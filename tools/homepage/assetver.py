# -*- coding: utf-8 -*-
"""发布产物的一组内容指纹：让「改了内容」同时等同于「改了 URL」。

为什么需要（2026-09-16 实证）：发布走腾讯 EdgeOne 边缘缓存。
改完前端后重新发布，**URL 不变的文件会长期回旧副本**——
`/css/page.css`、`/js/detail.js`、`/js/metricgrid.js` 连发四次发布、下线重上、
`Cache-Control: no-cache` 全部无效；`/` 裸链接更是连续 10/10 返回 9-12 的旧 HTML。
只有换了 URL（`?cb=<随机>`）才命中源站。

于是本模块负责三件事，全部幂等：

1. `stamp_data()`  —— 给 `data.js` / `main.js` 里取数据的 URL 打**数据指纹** `?b=<token>`。
   数据每晚重跑构建，token 随之变化，于是数据永远拿不到旧副本。
2. `stamp_imports()` —— 给 `js/*.js` 之间 `import ... from "./x.js"` 的说明符打 `?v=<ver>`。
   **这一步不能省**：只给入口 `main.js` 打指纹不够，模块图内部仍会命中旧副本，
   页面会变成「新入口 + 旧模块」的混血（2026-09-16 线上实证：总览渲染成旧版 markup）。
3. `stamp_index()`  —— 给 `index.html` 的 css/js 引用打同一个 `?v=<ver>`。

版本号对「已打过指纹」的源码是稳定的：算 hash 前先剥掉既有的 `?v=`，所以重复构建不会
无谓地改文件；而**数据 token 不参与剥离**，因此数据一变、全站指纹跟着变。

治不了的只有入口页自身：`/` 的 URL 加不了参数，边缘缓存过期前，裸链接只能等它自愈；
急着看新版用 `<域名>/?v=1`（新缓存键 → MISS → 回源）。
"""
import hashlib
import os
import re

from homepage.paths import SITE

_CSS = ["css/base.css", "css/calendar.css", "css/page.css", "css/detail.css"]
_JS_DIR = "js"
_ENTRY_JS = "js/main.js"

# index.html 里的资源引用
_REF = re.compile(r"((?:css/[a-z]+\.css|js/main\.js))(\?v=[0-9a-f]+)?")
# js 之间的相对导入说明符
_IMPORT = re.compile(r'(from\s+")(\./[A-Za-z0-9_.]+\.js)(\?v=[0-9a-f]+)?(")')
# 取数据的 URL（数据指纹，与资源指纹分离）。
# group(1) **不含收尾引号**，否则查询串会被甩到字符串外面（曾经踩过：main.js 变成
# `fetch("index.json?b=…, …)` —— 引号丢了，整份脚本语法错误）。
_DATA_INDEX = re.compile(r'("index\.json)(\?b=[0-9a-f]+)?')
_DATA_SHARD = re.compile(r'("daily/" \+ month \+ "\.json)(\?b=[0-9a-f]+)?')
# 坏形态修复：`"index.json"?b=…` → `"index.json?b=…"`（把查询串挪回引号内）
_REPAIR_INDEX = re.compile(r'("index\.json)"(\?b=[0-9a-f]+)')

_STRIP_V = re.compile(r"\?v=[0-9a-f]+")


def _read(path):
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _js_files(site_dir):
    d = os.path.join(site_dir, _JS_DIR)
    if not os.path.isdir(d):
        return []
    return [os.path.join(_JS_DIR, n) for n in sorted(os.listdir(d)) if n.endswith(".js")]


def data_token(site_dir=SITE):
    """数据指纹：index.json + 全部月度分片的内容摘要。数据一变就变。"""
    h = hashlib.sha256()
    h.update(_read(os.path.join(site_dir, "index.json")).encode("utf-8"))
    daily = os.path.join(site_dir, "daily")
    if os.path.isdir(daily):
        for n in sorted(os.listdir(daily)):
            if n.endswith(".json"):
                h.update(n.encode("utf-8"))
                h.update(_read(os.path.join(daily, n)).encode("utf-8"))
    return h.hexdigest()[:8]


def asset_version(site_dir=SITE):
    """资源指纹：全部 css/js 内容（剥离已打的 ?v=，保证幂等）。"""
    h = hashlib.sha256()
    for rel in _CSS + _js_files(site_dir):
        h.update(rel.encode("utf-8"))
        h.update(_STRIP_V.sub("", _read(os.path.join(site_dir, rel))).encode("utf-8"))
    return h.hexdigest()[:8]


def stamp_data(site_dir=SITE):
    """取数 URL 打数据指纹。返回 (token, 改动文件数)。"""
    tok = data_token(site_dir)
    n = 0
    for rel in (_JS_DIR + "/data.js", _ENTRY_JS):
        path = os.path.join(site_dir, rel)
        src = _read(path)
        if not src:
            continue
        out = _REPAIR_INDEX.sub(lambda m: '%s%s"' % (m.group(1), m.group(2)), src)
        out = _DATA_INDEX.sub(lambda m: "%s?b=%s" % (m.group(1), tok), out)
        out = _DATA_SHARD.sub(lambda m: "%s?b=%s" % (m.group(1), tok), out)
        if out != src:
            _write(path, out)
            n += 1
    return tok, n


def stamp_imports(site_dir=SITE, ver=None):
    """js 模块图内部导入打资源指纹。返回 (版本号, 改动文件数)。"""
    ver = ver or asset_version(site_dir)
    n = 0
    for rel in _js_files(site_dir):
        path = os.path.join(site_dir, rel)
        src = _read(path)
        out = _IMPORT.sub(lambda m: "%s%s?v=%s%s" % (m.group(1), m.group(2), ver, m.group(4)), src)
        if out != src:
            _write(path, out)
            n += 1
    return ver, n


def stamp_index(site_dir=SITE, ver=None):
    """index.html 的资源引用打资源指纹。返回 (版本号, 是否改动)。"""
    ver = ver or asset_version(site_dir)
    path = os.path.join(site_dir, "index.html")
    src = _read(path)
    out = _REF.sub(lambda m: "%s?v=%s" % (m.group(1), ver), src)
    if out == src:
        return ver, False
    _write(path, out)
    return ver, True


def stamp_all(site_dir=SITE):
    """一次跑完：数据指纹 → 资源指纹 → 模块图 → 入口页。返回摘要 dict。"""
    tok, n_data = stamp_data(site_dir)
    ver, n_imp = stamp_imports(site_dir)
    _, idx_changed = stamp_index(site_dir, ver)
    return {"data_token": tok, "assets_version": ver,
            "data_files": n_data, "import_files": n_imp, "index_changed": idx_changed}
