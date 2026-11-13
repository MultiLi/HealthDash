#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把构建好的数据推到健康主页的**热更新端点**——不需要重新上线。

背景（2026-09-16）
    以前数据是发布产物的一部分：每晚 build 完必须重新发布整站，还要跟 EdgeOne 边缘缓存
    缠斗（URL 不变就长期回旧副本）。现在站的 server.py 提供同源可写端点，数据落在服务端
    本地目录，于是「数据更新」退化成一个 POST，与「代码上线」彻底分开。

用法
    python3 tools/push_data.py                # 推送 site/index.json + site/daily/*.json
    python3 tools/push_data.py --check        # 只读：比对本地与线上，不改任何东西
    python3 tools/push_data.py --dry-run      # 打印将要推送的内容摘要，不发请求
    python3 tools/push_data.py --base URL     # 临时覆盖服务地址（本地自测用）

地址来源优先级：--base > $HOMEPAGE_ORIGIN > tools/deploy.json 的 origin
令牌来源优先级：$PUSH_TOKEN > site/.push_token

退出码：0 = 成功且已核验；1 = 失败。
"""
import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")
DEPLOY_CFG = os.path.join(ROOT, "tools", "deploy.json")
TOKEN_FILES = (os.path.join(SITE, ".push_token"), os.path.join(SITE, "_push_token"))


def die(msg):
    print("推送失败：" + msg, file=sys.stderr)
    sys.exit(1)


def canonic(obj):
    """语义哈希：与 JSON 排版无关，只比内容。"""
    h = hashlib.sha256()
    h.update(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()[:12]


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def resolve_base(cli_base):
    if cli_base:
        return cli_base.rstrip("/")
    env = (os.environ.get("HOMEPAGE_ORIGIN") or "").strip()
    if env:
        return env.rstrip("/")
    if os.path.isfile(DEPLOY_CFG):
        cfg = read_json(DEPLOY_CFG)
        if cfg.get("origin"):
            return str(cfg["origin"]).rstrip("/")
    die("没有服务地址：请传 --base、设 HOMEPAGE_ORIGIN，或写 tools/deploy.json 的 origin")


def resolve_share_code(cli_base=""):
    """平台分享码。新版发布链接要求 `?sharecode=`，缺了网关直接回 403「链接不完整」。

    2026-11-13 实证：整站（含 /、/api/health）连续两天 403，采集与热更新全废，
    根因只是链接少了这个参数——不是代码、不是鉴权、不是网络。

    来源优先级：$HOMEPAGE_SHARECODE > tools/deploy.json 的 shareCode
    > 从 --base / origin / homeUrl 自带的 query 里捞。
    """
    env = (os.environ.get("HOMEPAGE_SHARECODE") or "").strip()
    if env:
        return env
    cfg = {}
    if os.path.isfile(DEPLOY_CFG):
        try:
            cfg = read_json(DEPLOY_CFG) or {}
        except ValueError:
            cfg = {}
    if cfg.get("shareCode"):
        return str(cfg["shareCode"]).strip()
    for raw in (cli_base, cfg.get("origin"), cfg.get("homeUrl")):
        if not raw:
            continue
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(str(raw)).query)
        if qs.get("sharecode"):
            return qs["sharecode"][0]
    return ""


def strip_query(url):
    """地址只保留 scheme://host/path，query 交给 sharecode 单独拼。"""
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, "", "")).rstrip("/")


def resolve_token():
    env = (os.environ.get("PUSH_TOKEN") or "").strip()
    if env:
        return env
    for p in TOKEN_FILES:
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                t = f.read().strip()
            if t:
                return t
    die("没有推送令牌：site/.push_token 缺失（服务端不认推送）")


def http(method, url, token=None, payload=None, timeout=30):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-Push-Token"] = token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    # macOS 系统代理（scutil）常指向一个并不在跑的 127.0.0.1:1087，urllib 会因此 Connection refused。
    # 这里显式禁用代理，不依赖调用方记得加 no_proxy。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, {"error": body[:300]}
    except urllib.error.URLError as e:
        die("连不上 %s（%s）" % (url, e.reason))


def collect():
    """本地待推送载荷：index + 全部分片。"""
    idx_path = os.path.join(SITE, "index.json")
    if not os.path.isfile(idx_path):
        die("site/index.json 不存在——先跑 tools/build_homepage_data.py")
    files = {"index.json": read_json(idx_path)}
    # 保存视图的求值产物：与月度分片同性质（数据，不是代码），必须跟着热更新走，
    # 否则线上视图会一直停在发布当天的数字。
    views_dir = os.path.join(SITE, "views")
    if os.path.isdir(views_dir):
        for name in sorted(os.listdir(views_dir)):
            if name.endswith(".json") and name != "index.json":
                files["views/" + name] = read_json(os.path.join(views_dir, name))
    daily_dir = os.path.join(SITE, "daily")
    if os.path.isdir(daily_dir):
        for name in sorted(os.listdir(daily_dir)):
            if name.endswith(".json"):
                files["daily/" + name] = read_json(os.path.join(daily_dir, name))
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", help="覆盖服务地址，如 http://127.0.0.1:8790")
    ap.add_argument("--check", action="store_true", help="只比对，不推送")
    ap.add_argument("--dry-run", action="store_true", help="只打印摘要，不发请求")
    args = ap.parse_args()

    base = strip_query(resolve_base(args.base))
    # 本地自测（127.0.0.1/localhost）不带分享码：那是网关侧的要求，本地 server 不认
    host = urllib.parse.urlsplit(base).hostname or ""
    share = "" if host in ("127.0.0.1", "localhost") else resolve_share_code(args.base)
    qs = ("?sharecode=" + share) if share else ""

    def url(path):
        return base + path + qs

    files = collect()
    local_digest = {rel: canonic(obj) for rel, obj in files.items()}

    print("本地：%d 个文件 · index %d 天 · generated_at %s"
          % (len(files), len(files["index.json"].get("days") or []),
             files["index.json"].get("generated_at")))
    for rel in sorted(files):
        n = len(files[rel].get("days") or [])
        print("  %-22s %-12s %s" % (rel, local_digest[rel], "%d 天" % n))

    if args.dry_run:
        print("\n[dry-run] 未发出任何请求。")
        return

    token = resolve_token()

    code, health = http("GET", url("/api/health"))
    if code != 200 or not health.get("ok"):
        die("GET /api/health 返回 %s %s\n  线上可能还是旧（纯静态）版本："
            "需要先把这个版本发布一次，之后数据才走热更新。"
            "（403「链接不完整」= 缺 sharecode，见 tools/deploy.json）"
            % (code, health.get("error") or ""))
    print("\n线上：服务 %s · 推送 %s(%s) · 数据源 %s · generated_at %s · %s 天 · 分片 %s"
          % (health.get("service"), "开" if health.get("push_enabled") else "关",
             health.get("token_source") or "-", health.get("data_source"),
             health.get("generated_at"), health.get("days"),
             ",".join(health.get("shards") or []) or "-"))
    if not health.get("push_enabled"):
        die("线上推送端点未启用（服务端没找到令牌文件）。检查 site/.push_token 是否随发布上传。")

    code, remote = http("GET", url("/api/data/index"))
    if code != 200:
        die("GET /api/data/index 返回 %s %s" % (code, remote.get("error") or ""))
    same_index = canonic(remote) == local_digest["index.json"]

    if args.check:
        print("\n--check：线上 index.json %s本地（%s / %s）"
              % ("等于" if same_index else "不等于", canonic(remote), local_digest["index.json"]))
        stale = []
        for rel in sorted(files):
            if rel == "index.json":
                continue
            code, obj = http("GET", url("/api/data/" + rel[:-5]))
            if code != 200 or canonic(obj) != local_digest[rel]:
                stale.append(rel)
        print("分片：%s" % ("全部一致" if not stale else "不一致 → " + ", ".join(stale)))
        sys.exit(0 if (same_index and not stale) else 1)

    code, res = http("POST", url("/api/push"), token=token, payload={"files": files})
    if code != 200 or not res.get("ok"):
        # **线上还是旧服务端时，views/ 是它不认识的条目**——整包会被拒，连 index 都推不上去，
        # 于是「加了一个新数据类型」直接变成「今天的数据没更新」。
        # 这里降级为「只推它认识的部分」，并把降级说得足够响：
        # 视图内容留在随包副本里，发布新版本后自然生效。
        if any(k.startswith("views/") for k in files):
            fallback = {k: v for k, v in files.items() if not k.startswith("views/")}
            print("! 推送被拒（%s）。线上服务端可能还不认识 views/，"
                  "本次只推 index 与分片；视图需重新发布后才会热更新。"
                  % (res.get("error") or code), file=sys.stderr)
            code, res = http("POST", url("/api/push"), token=token,
                             payload={"files": fallback})
            if code == 200 and res.get("ok"):
                files = fallback
        if code != 200 or not res.get("ok"):
            die("POST /api/push 返回 %s %s" % (code, res.get("error") or res))
    print("\n已推送：%d 个文件 / %d 字节（快照回滚点 %s）"
          % (len(res.get("written") or {}), res.get("bytes") or 0, res.get("prev_snapshot")))

    # 回读核验：不信返回值，只信再取一次拿到的东西
    code, back = http("GET", url("/api/data/index"))
    if code != 200 or canonic(back) != local_digest["index.json"]:
        die("回读核验不通过：线上 index.json 与本地不一致（端点是否被缓存？）")
    bad = []
    for rel in sorted(files):
        if rel == "index.json":
            continue
        code, obj = http("GET", url("/api/data/" + rel[:-5]))
        if code != 200 or canonic(obj) != local_digest[rel]:
            bad.append(rel)
    if bad:
        die("回读核验不通过的分片：" + ", ".join(bad))
    print("回读核验通过：%d 个文件与本地逐一一致 · 线上 generated_at %s · %s 天"
          % (len(files), back.get("generated_at"), len(back.get("days") or [])))


if __name__ == "__main__":
    main()
