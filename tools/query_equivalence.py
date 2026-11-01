#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查询层口径等价性检查（roadmap P4 的第一道硬门）。

## 它证明什么

`query.py` 允许任意窗口查询。**只要它自己算一遍，就出现了第二套口径**——
主页、报告、对话会在同一个问题上给出不同答案，而 golden 只在 11 个历史日子里
钉着，抓不到这种漂移。所以这里做一件很土但很硬的事：

对**库里每一个单元**（day / week / month / roll7 / roll28 / roll90 × 全部指标，
约 4300 格），用当前代码重新求值，与 `units` 表逐字段比对 value / n / coverage /
judge / judge_rate / delta_v / delta_verdict。一处不同 = 有第二条路径，停下来修。

它同时抓另一件事：**物化过期**。改了 `compute_cell` 却忘了重跑 `index.py build`，
库里还是旧数——这个检查会立刻发现「库与当前代码不一致」。

## 两段

1. **bulk**：一次装载上下文，逐 spec 求值，与库逐格比对（覆盖全部单元）。
2. **spot**：挑几个单元走 `query.run()` 的公开入口（含 start/end → spec 的解析），
   确认壳层没有把值搞错。

    python3 tools/query_equivalence.py            # 全量比对
    python3 tools/query_equivalence.py --json
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skills", "body-health", "scripts"))

import db        # noqa: E402
import units     # noqa: E402
import query     # noqa: E402

FIELDS = ("value", "n", "coverage", "judge", "judge_rate",
          "delta_v", "delta_verdict", "delta_vs", "start", "end", "expected")
SCALES = ("day", "week", "month", "roll7", "roll28", "roll90")


def _same(a, b):
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            return a == b
        return abs(float(a) - float(b)) < 1e-9
    return a == b


def bulk(con, rules, reg):
    metrics = query.metrics_pool(reg)
    ctx = units.context(con, rules, metrics)
    if ctx is None:
        return None, [{"error": "obs 表为空"}]
    specs = units.unit_specs(ctx["first"], ctx["last"], ctx["today"], rules)
    cells, rows = {}, []
    for sp in specs:
        for r in units.compute_cells(sp, metrics, ctx, rules):
            cells[(r["scale"], r["key"], r["metric"])] = r
            rows.append(r)
    units.apply_delta(rows, cells, ctx, rules)
    fresh = {(r["scale"], r["key"], r["metric"]): r for r in rows}
    stored = {(r["scale"], r["key"], r["metric"]): dict(r)
              for r in con.execute("SELECT * FROM units WHERE scale IN (%s)"
                                   % ",".join("?" * len(SCALES)), SCALES)}
    diffs = []
    for k in sorted(set(fresh) | set(stored)):
        f, s = fresh.get(k), stored.get(k)
        if f is None or s is None:
            diffs.append({"cell": k, "field": "-",
                          "why": "只存在于%s" % ("库" if f is None else "重算"),
                          "got": None, "want": None})
            continue
        for fld in FIELDS:
            if not _same(f.get(fld), s.get(fld)):
                diffs.append({"cell": k, "field": fld, "got": f.get(fld),
                              "want": s.get(fld)})
    return {"cells": len(fresh), "stored": len(stored),
            "compared": len(set(fresh) & set(stored))}, diffs


def spot(con, rules, reg, n=8):
    """走公开入口 query.run()，确认壳层（spec 解析 + delta 取上一窗）没错。"""
    stored = list(con.execute(
        "SELECT scale, key, metric, value, judge, delta_verdict FROM units "
        "WHERE scale='week' ORDER BY key DESC LIMIT ?", (n,)))
    bad = []
    checked = 0
    for r in stored:
        try:
            res = query.run({"window": {"scale": r["scale"], "key": r["key"]},
                             "metrics": [r["metric"]]}, con=con, rules=rules, reg=reg)
        except query.QueryError as e:
            bad.append({"cell": (r["scale"], r["key"], r["metric"]), "why": str(e)})
            continue
        row = next((x for x in res["rows"] if x["metric"] == r["metric"]), None)
        if row is None:
            bad.append({"cell": (r["scale"], r["key"], r["metric"]), "why": "公开入口没返回该指标"})
            continue
        checked += 1
        for fld, want in (("value", r["value"]), ("judge", r["judge"]),
                          ("delta_verdict", r["delta_verdict"])):
            if not _same(row.get(fld), want):
                bad.append({"cell": (r["scale"], r["key"], r["metric"]), "field": fld,
                            "got": row.get(fld), "want": want})
    return {"checked": checked}, bad


def main():
    ap = argparse.ArgumentParser(description="查询层与 units 表的口径等价性")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    con = db.connect()
    rules = db.load_rules()
    reg = db.load_metrics()
    stat, diffs = bulk(con, rules, reg)
    if stat is None:
        print("✗ obs 表为空：先跑 index.py build")
        return 2
    sstat, sbad = spot(con, rules, reg)
    if a.json:
        print(json.dumps({"bulk": stat, "bulk_diffs": diffs,
                          "spot": sstat, "spot_diffs": sbad}, ensure_ascii=False, indent=1))
    else:
        print("bulk：重算 %d 格 / 库 %d 格 / 可比 %d 格"
              % (stat["cells"], stat["stored"], stat["compared"]))
        if diffs:
            print("✗ 与库不一致 %d 处（前 8）：" % len(diffs))
            for d in diffs[:8]:
                print("   %-28s %-14s 重算=%r 库=%r"
                      % ("/".join(d["cell"]) if isinstance(d["cell"], tuple)
                         else d["cell"], d.get("field"), d.get("got"), d.get("want")))
        else:
            print("✓ 逐字段一致：查询层与 units 表同源")
        print("spot：公开入口核对 %d 个单元" % sstat["checked"])
        for d in sbad[:5]:
            print("   ✗ %s %s" % (d.get("cell"), d.get("why") or d.get("got")))
        if not sbad:
            print("✓ 公开入口（query.run）取值、判定、变化结论与库一致")
    bad = bool(diffs or sbad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
