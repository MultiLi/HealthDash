#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""保存视图的物化（roadmap P4c）。

## 为什么必须在这里算，而不是在浏览器里算

页面要能「URL 即视图」。但查询的判定（达标/变化/覆盖门禁）**只能在后端算**——
让浏览器自己聚合、自己比大小，就回到了 2026-10-25 之前八份平行实现的老路
（同一份数据三种说法，且无法回归测试）。所以：

    保存的查询（data/views.json）→ 构建时用 query.run() 求值 → site/views/<id>.json
    页面只做一件事：把这份 JSON 渲染出来，**一个字都不算**

代价是「深链只对已保存的视图生效」。这不是妥协，是判据顺序：先有后端算好的答案，
才有可分享的 URL。临时问题由 agent 直接回答，不生成 URL。

## 失败姿势

**一个视图坏了不许拖垮整次构建**：写一条 `{"error": ...}` 的视图，
页面把它原样显示成「这个视图暂时算不出来」，而不是画一张没有数字的空表
（空表和「查询失败」在页面上长得一样，那是最坏的一种沉默）。
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from homepage.paths import SITE          # noqa: E402
from settings import VIEWS               # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(_HERE)),
                                "skills", "body-health", "scripts"))

VIEW_FIELDS = ("id", "title", "note", "query")


def load_saved(path=None):
    """读保存的视图定义。文件不存在 → 空列表（不是错误）。"""
    path = path or VIEWS
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh) or {}
    except ValueError as e:
        raise SystemExit("views.json 不是合法 JSON：%s" % e)
    views = data.get("views") if isinstance(data, dict) else data
    out = []
    for v in views or []:
        if not isinstance(v, dict) or not v.get("id"):
            continue
        if not v.get("query"):
            continue
        out.append(v)
    return out


def build_views(site_dir=None, path=None, quiet=False):
    """把每个保存视图求值并写进 site/views/。返回 [(id, ok, 说明)]。"""
    import query  # 延迟导入：CI 无库时也不该在 import 期炸

    site_dir = site_dir or SITE
    out_dir = os.path.join(site_dir, "views")
    views = load_saved(path)
    os.makedirs(out_dir, exist_ok=True)
    results = []
    idx = []
    for v in views:
        vid = str(v["id"])
        payload = {"id": vid, "title": v.get("title") or vid,
                   "note": v.get("note"), "query": v.get("query")}
        try:
            res = query.run(v["query"])
            payload.update({k: res.get(k) for k in
                            ("ruler", "window", "selection", "min_n",
                             "provenance", "rows", "empty")})
            payload["error"] = None
            ok = True
        except Exception as e:                       # 一个视图坏了不拖垮构建
            payload.update({"error": "%s: %s" % (type(e).__name__, e), "rows": []})
            ok = False
        with open(os.path.join(out_dir, "%s.json" % vid), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        idx.append({"id": vid, "title": payload["title"], "note": payload.get("note"),
                    "ok": ok})
        results.append((vid, ok, payload.get("error") or
                        "%d 个指标" % len(payload.get("rows") or [])))
    # 清理已经不在配置里的旧视图产物（否则旧 URL 会永远返回一个过期答案）
    keep = {"%s.json" % r[0] for r in results} | {"index.json"}
    for name in os.listdir(out_dir):
        if name.endswith(".json") and name not in keep:
            os.remove(os.path.join(out_dir, name))
    with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8") as fh:
        json.dump({"built": len(idx), "views": idx}, fh, ensure_ascii=False)
    if not quiet:
        if not idx:
            print("views: 没有保存的视图（data/views.json 不存在或为空）")
        else:
            print("views: %d 个（%d 成功 / %d 失败）"
                  % (len(idx), sum(1 for r in results if r[1]),
                     sum(1 for r in results if not r[1])))
            for vid, ok, msg in results:
                print("  %s %-18s %s" % ("✓" if ok else "✗", vid, msg))
    return results
