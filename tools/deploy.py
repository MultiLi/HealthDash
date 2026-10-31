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

`data/state/deploy.lock.json` —— 记录「上一次**确认**上线时」的代码与数据指纹。
它不是真相，是一个游标；删掉它会退回「首次运行」状态（只建基线、不动作）。

## 游标会说谎，所以要有 `code_verified`（2026-11-01 修）

上面那条 `init` 分支曾经直接把「本地当前指纹」写成「已上线」。只要当时工作区里
躺着一批还没发布的改动，基线从第一秒起就是错的——之后 `plan()` 永远算「没变」，
永远 noop。**2026-11-01 实证**：`--check` 报「代码与数据都与上次上线一致」，
而线上 `main.js?v=f094d208`、本地 `a3b1db98`，js 根本没推上去。

所以游标里多了一个 `code_verified`：
  · `init` 写 **false**（只记录本地状态，不声称线上一致）
  · `--mark-app-deployed` 写 **true**（发布成功是唯一可信证据）
  · `plan()` 见到 false，会真的去拉线上 `index.html` 比对指纹；
    一致就自愈成 true，不一致就返回 `republish`。

判据从「本地两次指纹之差」升级成「本地 vs 线上」。前者只能发现「我改了」，
发现不了「我改了但没上线」——那正是这个文件存在的全部理由。
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request

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


#: 线上地址的唯一来源。读不到就不做核验（离线也能用，只是不声称「已一致」）。
_DEPLOY_JSON = os.path.join(HERE, "deploy.json")

#: index.html 里真正决定浏览器请求哪个资源的那个键：?v=（资源）/ ?b=（数据）。
_ASSET_REF = re.compile(r"main\.js\?v=([0-9a-f]+)")


def site_origin():
    try:
        with open(_DEPLOY_JSON, encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("origin") or ""
    except (OSError, ValueError):
        return ""


def local_asset_version():
    """本地 index.html 引用的资源版本——浏览器真正会去请求的那个键。"""
    m = _ASSET_REF.search(_read(os.path.join(SITE, "index.html")))
    return m.group(1) if m else ""


def live_asset_version(timeout=8):
    """线上 index.html 引用的资源版本。取不到返回 ""，调用方必须当作「无法核验」。"""
    origin = site_origin()
    if not origin:
        return ""
    # 明确绕过系统代理：这域名是自己家的，走代理反而常被拦（且代理进程时常不在）
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        origin.rstrip("/") + "/?_=" + str(int(__import__("time").time())),
        headers={"Cache-Control": "no-cache", "User-Agent": "healthd-deploy/1"},
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "ignore")
    except Exception:
        return ""
    m = _ASSET_REF.search(body)
    return m.group(1) if m else ""


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


def plan(heal=False, force_verify=False):
    """返回 (动作, 说明)。

    动作 ∈ init / noop / hot_update / republish / republish+hot_update

    核心变化（2026-11-01）：只要游标没被「一次真实发布」确认过，就**去问线上**，
    而不是拿本地前后两次指纹相比。后者只能发现「我改了」，发现不了
    「我改了、但从来没上线」。

    force_verify=True 时无视 `code_verified`，每次都拉线上比对（排查用；
    会多一次 HTTP 请求，日常不需要）。
    """
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
            "data_pushed_at": lock.get("data_pushed_at"),
            "code_verified": bool(lock.get("code_verified"))}

    # ── 游标未验证（或被要求强制核验）：以线上为准 ──────────────────────
    if force_verify or not lock.get("code_verified"):
        info["local_asset"] = local_asset_version()
        info["live_asset"] = live_asset_version()
        if info["live_asset"] and info["live_asset"] == info["local_asset"]:
            # 线上与本地资源版本一致 = 线上确实是最新的，游标可以自愈
            info["code_verified"] = True
            if heal:
                lock["code_verified"] = True
                write_lock(lock)
        elif info["live_asset"]:
            # 线上能读、但版本对不上 = 铁证：代码没上线
            return ("republish" + ("+hot_update" if data_changed else "")), info
        else:
            # 线上取不到（离线/域名不通）：不能声称一致，也不因此卡住定时任务。
            # 保留旧判据，但把「未核验」这件事带出去。
            info["live_unreachable"] = True

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
    ap.add_argument("--verify-live", action="store_true",
                    help="强制拉线上 index.html 比对（无视游标的已验证标记）")
    a = ap.parse_args()

    # —— 登记发布完成 ——
    if a.mark_app_deployed:
        lock = read_lock()
        lock["code"] = code_fingerprint()
        lock["data"] = data_token()
        # 一次真实发布的成功回执，是唯一能让游标「可信」的东西
        lock["code_verified"] = True
        lock["app_deployed_at"] = __import__("datetime").datetime.now().strftime(
            "%Y-%m-%dT%H:%M:%S")
        lock.pop("note", None)   # 「首次建立基线」这类临时说明，登记真实发布后就不再成立
        write_lock(lock)
        print("已登记：代码 %s · 数据 %s" % (lock["code"], lock["data"]))
        print("（本命令只更新游标，不校验线上；发布是否真的成功要靠打开链接确认）")
        return 0

    action, info = plan(heal=not a.check, force_verify=a.verify_live)

    if action == "init":
        lock = {"code": info["code"], "data": info["data"],
                "app_deployed_at": None, "data_pushed_at": None,
                # 关键：这是「本地现在什么样」，不是「线上现在什么样」。
                # 不写 False 的话，只要本地躺着未发布的改动，游标就从第一秒起是错的。
                "code_verified": False,
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
        # 线上核验结果必须显式打出来：这是唯一能证明「真的上线了」的东西
        if info.get("live_asset"):
            same = info["live_asset"] == info.get("local_asset")
            print("线上资源 v=%s   本地 v=%s   %s" % (
                info["live_asset"], info.get("local_asset") or "（读不到）",
                "一致" if same else "★ 不一致 —— 代码没上线"))
        if info.get("live_unreachable"):
            print("⚠️ 取不到线上（离线或域名不通）：只按本地游标判断，未核验线上")
        if not info.get("code_verified"):
            print("⚠️ 游标未经发布确认（code_verified=false）：以上结论不保证线上一致")

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
