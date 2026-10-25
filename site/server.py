#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""健康主页的**同源小后端**（纯标准库，零依赖）。

为什么存在（2026-09-16）
    static 托管下，数据（index.json / daily/*.json）是**发布产物的一部分**：每晚重跑
    构建都要把整站重新发布一次，再跟 EdgeOne 边缘缓存缠斗（URL 不变就长期回旧副本）。
    本服务把「数据」从「发布产物」里摘出来，放到服务端本地可写目录，于是：

      取数  GET  /api/data/index              热更新的数据（无扩展名，绕开按扩展名的缓存规则）
      取数  GET  /api/data/daily/2026-09      月度分片
      兜底  GET  /index.json  /daily/*.json   随站点打包的静态副本（端点坏了页面也不会白）
      推送  POST /api/push                    构建完推一次即生效，**不需要重新上线**
      自检  GET  /api/health                  版本 / 天数 / 落盘时间 / 推送是否可用
      回滚  POST /api/rollback                退回上一次推送前的快照（.data/.prev/）

铁律
    - 只做「静态托管 + 4 个端点」，不引入任何依赖。
    - **可被 HTTP 取走的只有扩展名白名单内的文件**：server.py 自身、.push_token、
      .data/ 目录一律 404——推送令牌不能靠「别人不知道路径」来保护。
    - 推送必须带 X-Push-Token（env PUSH_TOKEN > .push_token > _push_token），
      找不到令牌就**关闭推送端点**并让 /api/health 明确报 push_enabled=false。
    - 先校验全部载荷、再落盘；落盘一律 .tmp → os.replace（读侧永远看不到半截文件）。
    - html / json 一律 Cache-Control: no-store（缓存正确性归服务端管，不靠 URL 指纹）。

启动: PORT=<port> python3 server.py      （部署时由平台注入 PORT，必须 bind 0.0.0.0）
"""
import json
import os
import re
import secrets
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

SITE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SITE_DIR, ".data")
PREV_DIR = os.path.join(DATA_DIR, ".prev")
BUNDLED_INDEX = os.path.join(SITE_DIR, "index.json")
BUNDLED_DAILY = os.path.join(SITE_DIR, "daily")

MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
MAX_BODY = 32 * 1024 * 1024

# 可被 HTTP 取走的扩展名白名单。不在名单里的一律 404（含 .py / .md / 无扩展名文件）。
SERVABLE = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".map": "application/json; charset=utf-8",
}

_lock = threading.Lock()


# ---------------------------------------------------------------- 令牌 / 路径

def load_token():
    """推送令牌：环境变量优先，其次隐藏文件。返回 (token, 来源描述)。"""
    t = (os.environ.get("PUSH_TOKEN") or "").strip()
    if t:
        return t, "env:PUSH_TOKEN"
    for name in (".push_token", "_push_token"):
        p = os.path.join(SITE_DIR, name)
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    t = f.read().strip()
            except OSError:
                continue
            if t:
                return t, name
    return "", ""


def state_index_path():
    return os.path.join(DATA_DIR, "index.json")


def state_shard_path(month):
    return os.path.join(DATA_DIR, "daily", month + ".json")


# ---------------------------------------------------------------- 读写小工具

def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError) as e:
        return None, "%s: %s" % (type(e).__name__, e)


def _write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return os.path.getsize(path)


def _snapshot_prev():
    """把当前状态目录整份拷进 .data/.prev/（回滚用）。没有状态时静默跳过。"""
    if not os.path.isdir(DATA_DIR):
        return 0
    if os.path.isdir(PREV_DIR):
        shutil.rmtree(PREV_DIR, ignore_errors=True)
    n = 0
    os.makedirs(PREV_DIR, exist_ok=True)
    for rel in ["index.json"]:
        src = os.path.join(DATA_DIR, rel)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(PREV_DIR, rel))
            n += 1
    src_daily = os.path.join(DATA_DIR, "daily")
    if os.path.isdir(src_daily):
        os.makedirs(os.path.join(PREV_DIR, "daily"), exist_ok=True)
        for name in sorted(os.listdir(src_daily)):
            if name.endswith(".json"):
                shutil.copy2(os.path.join(src_daily, name),
                             os.path.join(PREV_DIR, "daily", name))
                n += 1
    return n


# ---------------------------------------------------------------- 启动自愈

def seed_state_from_bundle():
    """启动时自愈：状态缺失、或状态比打包副本更旧，就用打包副本播种状态。

    为什么需要：`.data/` 是服务端运行时状态，但它和站点在同一个目录里，
    部署产物里可能带着一份**旧的**状态；若照单全收，页面会显示比上一次发布还旧的数据
    （「发布 ≠ 生效」的反向版本）。判据用 generated_at 字符串（'YYYY-MM-DD HH:MM' 可直接比较）。
    状态比打包副本新时**不动**——那才是热更新比发布新的正常情形。
    """
    bundled, err = _read_json(BUNDLED_INDEX)
    if bundled is None:
        return "skip(bundled 缺失: %s)" % err
    state, _ = _read_json(state_index_path())
    if state is not None and str(state.get("generated_at") or "") >= str(bundled.get("generated_at") or ""):
        return "keep(state generated_at=%s)" % state.get("generated_at")
    try:
        _write_json(state_index_path(), bundled)
        sd = os.path.join(DATA_DIR, "daily")
        if os.path.isdir(BUNDLED_DAILY):
            for name in sorted(os.listdir(BUNDLED_DAILY)):
                if name.endswith(".json"):
                    obj, err = _read_json(os.path.join(BUNDLED_DAILY, name))
                    if obj is not None:
                        _write_json(os.path.join(sd, name), obj)
    except OSError as e:
        return "fail(%s)" % e
    return "seeded(from bundled generated_at=%s)" % bundled.get("generated_at")


# ---------------------------------------------------------------- 载荷校验

def validate_index(obj):
    if not isinstance(obj, dict):
        return "index 不是 JSON 对象"
    days = obj.get("days")
    if not isinstance(days, list) or not days:
        return "index.days 不是非空数组"
    for i, r in enumerate(days):
        if not isinstance(r, dict) or not isinstance(r.get("date"), str) or not r["date"]:
            return "index.days[%d] 缺 date 字段" % i
    return None


def validate_shard(month, obj):
    if not MONTH_RE.match(month or ""):
        return "分片月份不合法: %r" % (month,)
    if not isinstance(obj, dict):
        return "分片 %s 不是 JSON 对象" % month
    if not isinstance(obj.get("days"), dict):
        return "分片 %s 缺 days 对象" % month
    return None


def normalize_payload(payload):
    """把两种载荷形态归一成 {files: {'index.json': obj, 'daily/MM.json': obj}}。"""
    if not isinstance(payload, dict):
        return None, "请求体不是 JSON 对象"
    files = {}
    if isinstance(payload.get("files"), dict):
        files.update(payload["files"])
    if payload.get("index") is not None:
        files["index.json"] = payload["index"]
    if isinstance(payload.get("daily"), dict):
        for month, obj in payload["daily"].items():
            files["daily/%s.json" % month] = obj
    if not files:
        return None, "载荷里没有 index / daily / files 任何内容"
    return files, None


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "health-home/1.0"
    protocol_version = "HTTP/1.1"

    # ---- 输出 ----
    def _send(self, code, body, ctype, cache, extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj, extra=None):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8",
                   "no-store", extra)

    def _err(self, code, msg, extra=None):
        self._json(code, {"ok": False, "error": msg}, extra)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None, "Content-Length 不合法"
        if n <= 0:
            return None, "请求体为空"
        if n > MAX_BODY:
            return None, "请求体过大（>%d 字节）" % MAX_BODY
        return self.rfile.read(n), None

    # ---- 路由 ----
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            return self.api_health()
        if path.startswith("/api/data/"):
            return self.api_data(path[len("/api/data/"):])
        if path.startswith("/api/"):
            return self._err(404, "未知端点")
        return self.static(path)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/push":
            return self.api_push()
        if path == "/api/rollback":
            return self.api_rollback()
        self._err(404, "未知端点")

    # ---- /api/health ----
    def api_health(self):
        idx, err = _read_json(state_index_path())
        src, days, gen = "bundled", 0, None
        if idx is not None:
            src, days, gen = "state", len(idx.get("days") or []), idx.get("generated_at")
        else:
            b, _ = _read_json(BUNDLED_INDEX)
            if b is not None:
                days, gen = len(b.get("days") or []), b.get("generated_at")
        shards = []
        sd = os.path.join(DATA_DIR, "daily")
        if os.path.isdir(sd):
            shards = sorted(n[:-5] for n in os.listdir(sd) if n.endswith(".json"))
        self._json(200, {
            "ok": True,
            "service": "health-home-data",
            "push_enabled": bool(TOKEN),
            "token_source": TOKEN_SRC if TOKEN else None,
            "data_source": src,
            "generated_at": gen,
            "days": days,
            "shards": shards,
            "state_files": (1 if idx is not None else 0) + len(shards),
        })

    # ---- /api/data/* ----
    def api_data(self, rest):
        rest = rest.strip("/")
        if rest.endswith(".json"):
            rest = rest[:-5]
        if rest == "index":
            obj, err = _read_json(state_index_path())
            origin = "state"
            if obj is None:
                obj, err = _read_json(BUNDLED_INDEX)
                origin = "bundled"
            if obj is None:
                return self._err(404, "数据不可用：%s" % err)
            return self._json(200, obj, {"X-Data-Origin": origin,
                                         "Access-Control-Allow-Origin": "*"})
        m = re.match(r"^daily/(.+)$", rest)
        if m:
            month = m.group(1)
            if not MONTH_RE.match(month):
                return self._err(400, "月份不合法")
            obj, err = _read_json(state_shard_path(month))
            origin = "state"
            if obj is None:
                obj, err = _read_json(os.path.join(BUNDLED_DAILY, month + ".json"))
                origin = "bundled"
            if obj is None:
                # 没有这个月的数据**不是错误**，是「该月没有记录」。
                # 返回 200 + 空分片，前端才能把「这天没数据」与「加载失败」分开显示
                # —— 前者给明确空态，后者才报错。返回 404 会让所有空月份都变成「加载失败」。
                return self._json(200, {"month": month, "days": {}},
                                  {"X-Data-Origin": "empty",
                                   "Access-Control-Allow-Origin": "*"})
            return self._json(200, obj, {"X-Data-Origin": origin,
                                         "Access-Control-Allow-Origin": "*"})
        self._err(404, "未知数据路径")

    # ---- /api/push ----
    def _auth(self):
        if not TOKEN:
            self._err(503, "服务端未配置推送令牌（PUSH_TOKEN / .push_token / _push_token 均缺失），"
                           "推送端点已关闭")
            return False
        got = self.headers.get("X-Push-Token") or ""
        if not secrets.compare_digest(got, TOKEN):
            self._err(401, "推送令牌不匹配")
            return False
        return True

    def api_push(self):
        if not self._auth():
            return
        raw, err = self._body()
        if err:
            return self._err(400, err)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            return self._err(400, "请求体不是合法 JSON：%s" % e)

        files, err = normalize_payload(payload)
        if err:
            return self._err(400, err)

        # 先全量校验，再落盘——半个坏载荷不应该覆盖掉上一份好数据
        for rel, obj in files.items():
            if rel == "index.json":
                err = validate_index(obj)
            elif rel.startswith("daily/"):
                err = validate_shard(rel[len("daily/"):-5], obj)
            else:
                err = "不认识的条目：%s" % rel
            if err:
                return self._err(400, "载荷校验失败（未写入任何文件）：%s" % err)

        written = {}
        with _lock:
            prev = _snapshot_prev()
            for rel, obj in files.items():
                if rel == "index.json":
                    path = state_index_path()
                else:
                    path = state_shard_path(rel[len("daily/"):-5])
                written[rel] = _write_json(path, obj)

        idx, _ = _read_json(state_index_path())
        self._json(200, {
            "ok": True,
            "written": written,
            "bytes": sum(written.values()),
            "prev_snapshot": prev,
            "generated_at": (idx or {}).get("generated_at"),
            "days": len((idx or {}).get("days") or []),
        })

    # ---- /api/rollback ----
    def api_rollback(self):
        if not self._auth():
            return
        if not os.path.isdir(PREV_DIR):
            return self._err(404, "没有可回滚的快照")
        with _lock:
            restored = []
            for root, _dirs, names in os.walk(PREV_DIR):
                for name in sorted(names):
                    src = os.path.join(root, name)
                    rel = os.path.relpath(src, PREV_DIR)
                    dst = os.path.join(DATA_DIR, rel)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    restored.append(rel)
        self._json(200, {"ok": True, "restored": restored})

    # ---- 静态 ----
    def static(self, url_path):
        rel = unquote(url_path).lstrip("/")
        if rel == "":
            rel = "index.html"
        if rel.endswith("/"):
            rel += "index.html"
        if ".." in rel.split("/") or any(p.startswith(".") for p in rel.split("/")):
            return self._err(404, "不存在")
        full = os.path.realpath(os.path.join(SITE_DIR, rel))
        if not full.startswith(os.path.realpath(SITE_DIR) + os.sep):
            return self._err(404, "不存在")
        ext = os.path.splitext(full)[1].lower()
        if ext not in SERVABLE or not os.path.isfile(full):
            return self._err(404, "不存在")
        # 指纹化过的资源（?v= / ?b=）可以长缓存；html 与 json 一律不缓存
        if ext in (".html", ".json"):
            cache = "no-store"
        elif "?v=" in self.path or "?b=" in self.path:
            cache = "public, max-age=31536000, immutable"
        else:
            cache = "public, max-age=600"
        try:
            with open(full, "rb") as f:
                body = f.read()
        except OSError as e:
            return self._err(500, "读取失败：%s" % e)
        self._send(200, body, SERVABLE[ext], cache)

    def log_message(self, fmt, *args):
        if urlparse(self.path).path.startswith("/api/push") or \
           urlparse(self.path).path == "/api/health":
            pass  # 由调用方自己汇报，不在这里刷屏


TOKEN, TOKEN_SRC = load_token()
SEED_RESULT = ""


def main():
    global SEED_RESULT
    port = int(os.environ.get("PORT") or 8790)
    SEED_RESULT = seed_state_from_bundle()
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    idx, _ = _read_json(state_index_path())
    print("[health-home] listening on 0.0.0.0:%d | site=%s" % (port, SITE_DIR))
    print("[health-home] seed=%s" % SEED_RESULT)
    print("[health-home] data=no-store API + bundled fallback | state=%s | push=%s"
          % ("yes" if idx is not None else "empty(用打包副本兜底)",
             ("on via " + TOKEN_SRC) if TOKEN else "OFF(无令牌)"))
    srv.serve_forever()


if __name__ == "__main__":
    main()
