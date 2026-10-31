#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部署编排：判断「这次该做什么」，并执行能自动做的那部分。

## 为什么需要它（2026-11-01）

在这之前，「上线」不是一个动作，而是三个各自为政的后果：
`collect.py` 顺手 `build_homepage_data.py --push`（数据热更新）、
改完前端靠人记得「要重新发布」、`tools/deploy.json` 里只是躺着一个地址。
结果是 **改了前端代码之后线上一直是旧的，而且没人发现**——
2026-10-31 的重构（删 cloud.js、改 main.js/store.js）就是这么积压的：
本地构建了、冒烟过了、git 推了，线上打开还是昨晚那版。

## 判据只有一条：这次变了什么？

| 变的东西 | 后果 | 代价 |
|---|---|---|
| 只有数据（`data/`） | **热更新**：一个 POST，秒级，用户几乎无感 | 无 |
| 前端代码（`site/js` `site/css` `index.html`） | **必须重新发布应用** | 有失败面、有缓存预热 |
| 都没有 | 什么都不做 | — |

所以脚本的第一职责是**把「变了什么」算准**。这里有一个坑必须绕开：

`assetver.asset_version()` 只剥离 `?v=`，**不剥 `?b=`**——而 `data.js` 里嵌着
`index.json?b=<token>`。数据每晚重算，token 就变，`data.js` 内容跟着变，
那个指纹**每天都在变**。拿它判断「前端代码改了没」会天天误报「该重新发布」。
所以本脚本自己算一份 `code_fingerprint()`：两种指纹都剥掉，只剩真正的代码内容。

## 用法

    python3 tools/deploy.py             # 检查 + 需要热更新就自动推（日常/定时任务用这个）
    python3 tools/deploy.py --check     # 只报告，不动任何东西
    python3 tools/deploy.py --push      # 强制推一次数据（即使指纹没变）
    python3 tools/deploy.py --mark-app-deployed
                                        # 应用发布成功后调用，登记当前代码版本

## 退出码（给定时任务和 CI 读）

    0  无需动作 / 已完成
    2  **需要重新发布应用**（脚本做不了，要 agent 调发布工具）
    3  需要热更新但推送失败

## 状态文件

`data/state/deploy.lock.json` —— 记录「上一次成功上线时」的代码与数据指纹。
它不是真相，是一个游标；删掉它会退回「首次运行」状态（只建基线、不动作）。
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SITE = os.path.join(ROOT, "site")

if HERE not in sys.path:
    sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "skills", "body-health", "scripts"))

from homepage.assetver import data_token  # noqa: E402  数据指纹复用，别再写一份
from settings import STATE_DIR  # noqa: E402  路径唯一解析点

LOCK_PATH = os.path.join(STATE_DIR, "deploy.lock.json")

#: assetver 注入的两种指纹：?v=（资源）/ ?b=（数据）。判断代码变更时**两个都要剥**。
_STAMP = re.compile(r"\?(?:v|b)=[0-9a-f]+")

_CSS_DIR = "css"
_JS_DIR = "js"


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def code_fingerprint(site_dir=SITE):
    """前端**纯代码**指纹：css/js/index.html 内容，剥掉两种构建注入的指纹。

    与 assetver.asset_version() 的区别只有一个：多剥了 `?b=`。
    少了这一步，数据一变指纹就变，CD 会天天喊「要重新发布」。
    """
    h = hashlib.sha256()
    files = ["index.html"]
    for sub, ext in ((_CSS_DIR, ".css"), (_JS_DIR, ".js")):
        d = os.path.join(site_dir, sub)
        if os.path.isdir(d):
            files += [os.path.join(sub, n) for n in sorted(os.listdir(d)) if n.endswith(ext)]
    for rel in files:
        src = _read(os.path.join(site_dir, rel))
        if not src:
            continue
        h.update(rel.encode("utf-8"))
        h.update(_STAMP.sub("", src).encode("utf-8"))
    return h.hexdigest()[:12]


def read_lock():
    try:
        with open(LOCK_PATH, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def write_lock(lock):
    os.makedirs(STATE_DIR, exist_ok=True)
    lock["updated_at"] = __import__("datetime").datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with open(LOCK_PATH, "w", encoding="utf-8") as fh:
        json.dump(lock, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def plan():
    """返回 (动作, 说明)。动作 ∈ init / noop / hot_update / republish / republish+hot_update。"""
    code, data = code_fingerprint(), data_token()
    lock = read_lock()
    if not lock.get("code"):
        return "init", {"code": code, "data": data,
                        "why": "还没有上线基线（deploy.lock.json 不存在或为空）"}

    code_changed = lock.get("code") != code
    data_changed = lock.get("data") != data
    info = {"code": code, "data": data,
            "prev_code": lock.get("code"), "prev_data": lock.get("data"),
            "app_deployed_at": lock.get("app_deployed_at"),
            "data_pushed_at": lock.get("data_pushed_at")}

    if code_changed and data_changed:
        return "republish+hot_update", info
    if code_changed:
        return "republish", info
    if data_changed:
        return "hot_update", info
    return "noop", info


def push_data():
    """热更新：把本地构建产物 POST 到线上端点。返回 True/False。"""
    rc = subprocess.call([sys.executable, os.path.join(HERE, "push_data.py")])
    return rc == 0


def main():
    ap = argparse.ArgumentParser(description="部署编排：判断该热更新还是该重新发布")
    ap.add_argument("--check", action="store_true", help="只报告，不动任何东西")
    ap.add_argument("--push", action="store_true", help="强制推一次数据")
    ap.add_argument("--mark-app-deployed", action="store_true",
                    help="应用发布成功后调用，登记当前代码版本")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    a = ap.parse_args()

    # —— 登记发布完成 ——
    if a.mark_app_deployed:
        lock = read_lock()
        lock["code"] = code_fingerprint()
        lock["data"] = data_token()
        lock["app_deployed_at"] = __import__("datetime").datetime.now().strftime(
            "%Y-%m-%dT%H:%M:%S")
        lock.pop("note", None)   # 「首次建立基线」这类临时说明，登记真实发布后就不再成立
        write_lock(lock)
        print("已登记：代码 %s · 数据 %s" % (lock["code"], lock["data"]))
        print("（本命令只更新游标，不校验线上；发布是否真的成功要靠打开链接确认）")
        return 0

    action, info = plan()

    if action == "init":
        lock = {"code": info["code"], "data": info["data"],
                "app_deployed_at": None, "data_pushed_at": None,
                "note": "首次建立基线；真正的上线时间未知，需要人工确认一次"}
        if not a.check:
            write_lock(lock)
        print("首次运行：建立上线基线（code=%s data=%s）" % (info["code"], info["data"]))
        print("⚠️ 基线只记录「现在本地是什么」，不保证线上一致。")
        print("   若刚改过前端，请发布一次：workbuddy_sites_deploy，然后 --mark-app-deployed。")
        return 0 if a.check else 2

    if a.json:
        print(json.dumps({"action": action, **info}, ensure_ascii=False, indent=1))
    else:
        print("代码 %s%s   数据 %s%s" % (
            info["code"], "" if info["prev_code"] in (None, info["code"])
            else "（上次 %s）" % info["prev_code"],
            info["data"], "" if info["prev_data"] in (None, info["data"])
            else "（上次 %s）" % info["prev_data"]))

    # `--push` 是「强制推一次」，所以 noop 时也要放行——否则指纹没变就永远推不了，
    # 而「指纹没变但线上 .data/ 空了」正是重新发布后的常见状态（新沙箱里数据目录是空的）。
    if action == "noop" and not a.push:
        print("无需动作：代码与数据都与上次上线一致")
        return 0

    if a.check:
        print("需要动作：%s" % action)
        if "republish" in action:
            print("  → 代码变了，必须重新发布应用（热更新改不了 js/css）")
        if "hot_update" in action:
            print("  → 数据变了，可以热更新（一个 POST，不重新上线）")
        return 2 if "republish" in action else 0

    # —— 执行 ——
    need_republish = "republish" in action
    pushed = None
    if a.push or "hot_update" in action:
        print("\n-- 热更新（推送数据）--")
        pushed = push_data()
        if pushed:
            lock = read_lock()
            lock["data"] = info["data"]
            lock["data_pushed_at"] = __import__("datetime").datetime.now().strftime(
                "%Y-%m-%dT%H:%M:%S")
            write_lock(lock)
            print("数据热更新完成，游标已更新")
        else:
            print("! 热更新失败（不影响本地构建产物：静态副本仍是兜底）", file=sys.stderr)

    if need_republish:
        print("\n⚠️  前端代码有变更 —— 需要重新发布应用，脚本做不了这一步。")
        print("   发布参数：appId=wbapp_3G1QX7kZjmy7RNbU9gBdT5, language=python,")
        print("            startCmd=\"python3 server.py\", installCmd=\"\", updateExistingApp=true")
        print("   发布成功后跑：python3 tools/deploy.py --mark-app-deployed")
        return 2

    if pushed is False:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
