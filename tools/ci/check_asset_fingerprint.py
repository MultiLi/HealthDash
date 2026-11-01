#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""资源/数据指纹一致性：**内容改了就必须重建，重建了就必须整体提交。**

2026-11-01 的真实事故：为了让 diff 干净，把 7 个「只有指纹差异」的 js 还原了，
结果 `index.html` 引用新指纹、`data.js` 内部 import 旧指纹。浏览器/smoke 按 html
的版本号加载模块，拿到的是**第二个模块实例**，store 不共享——CI 直接 6 条红。

所以判据不是「各处指纹彼此一致」这么弱，而是**每处指纹都必须等于按当前内容算出
的值**（`assetver.asset_version` / `data_token`）。这样连「内容改了但忘了重建」
一起抓住——那才是同一类错误的上游。

用法：
  python3 tools/ci/check_asset_fingerprint.py       # 0 = 一致，1 = 不一致（附修法）
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from homepage import assetver                      # noqa: E402
from homepage.paths import SITE                    # noqa: E402

V_REF = re.compile(r"\?v=([0-9a-f]+)")
B_REF = re.compile(r"\?b=([0-9a-f]+)")
# js 之间的相对导入（可选带指纹）
IMPORT = re.compile(r'from\s+"(\./[A-Za-z0-9_.]+\.js)(\?v=([0-9a-f]+))?"')
# index.html 的资源引用（可选带指纹）
HTML_REF = re.compile(r'((?:css/[a-z]+\.css|js/main\.js))(\?v=([0-9a-f]+))?')
# 取数 URL（可选带数据指纹）
DATA_REF = re.compile(r'"((?:index|daily/[^"]*)\.json)(\?b=([0-9a-f]+))?"')


def _read(rel):
    p = os.path.join(SITE, rel)
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return f.read()


def js_files():
    d = os.path.join(SITE, "js")
    if not os.path.isdir(d):
        return []
    return ["js/" + n for n in sorted(os.listdir(d)) if n.endswith(".js")]


def check_asset(problems):
    """所有 ?v= 必须等于按内容算出的版本；该带指纹的地方不许空着。"""
    ver = assetver.asset_version(SITE)
    seen = {}

    for rel in js_files():
        src = _read(rel) or ""
        for m in IMPORT.finditer(src):
            spec, got = m.group(1), m.group(3)
            if got != ver:
                problems.append("%s: import %s 的指纹是 %s，应为 %s"
                                % (rel, spec, got or "（缺失）", ver))
            seen[got] = seen.get(got, 0) + 1

    html = _read("index.html")
    if html is None:
        return ver, None
    for m in HTML_REF.finditer(html):
        ref, got = m.group(1), m.group(3)
        if got != ver:
            problems.append("index.html: %s 的指纹是 %s，应为 %s"
                            % (ref, got or "（缺失）", ver))
        seen[got] = seen.get(got, 0) + 1
    return ver, seen


def check_data(problems):
    """数据指纹只验「存在」，不验「等于多少」。

    为什么：`?b=` 绑定的是 `index.json` + 分片的内容，而那些**不入库**。仓库里存着的
    必然是某次历史数据的 token，与本地当前数据几乎永远不等——拿它做等值检查会 100%
    误报。真正的保证在别处：`assetver.stamp_data()` 每次构建都会重打，而 CI 里没有
    site/index.json 时也压根没法算。所以这里只拦住「忘了打」这一种形态。
    """
    for rel in ("js/data.js", "js/main.js"):
        src = _read(rel) or ""
        for m in DATA_REF.finditer(src):
            url, got = m.group(1), m.group(3)
            if not got:
                problems.append("%s: %s 没打数据指纹（构建没跑全）" % (rel, url))
    return None


def main():
    problems = []
    ver, seen = check_asset(problems)
    tok = check_data(problems)

    if problems:
        print("✗ 发布产物指纹不一致——提交前必须先重建并整体提交：")
        for p in problems[:12]:
            print("   ", p)
        if len(problems) > 12:
            print("    …共 %d 处" % len(problems))
        print("\n修法：python3 tools/build_homepage_data.py && git add site/")
        print("      （要么整体提交，要么整体还原；禁止挑部分还原——那会造出双模块实例）")
        return 1

    print("资源指纹 v=%s 全部匹配（%d 处引用）· 数据指纹已打"
          % (ver, sum(seen.values()) if seen else 0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
