#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回测：改口径前后，历史结论翻转了哪几条（2026-10-25 建）。

**为什么必须有它**（notes/architecture_v4.md §七）
改一条阈值是五分钟的事，但它会让过去 36 天的结论悄悄变化——而「变了但没人知道」
比「变错」更危险。以前没有任何机制能回答「这次改口径，历史哪几天改判了」。

两种用法，都只读、都不改库：

**① 前后对比（改阈值时的正路）**
    python3 backtest.py snapshot                       # 改之前：存一份基线
    #  ...改 references/rules.json 或 metrics.json...
    python3 index.py build                             # 重算
    python3 backtest.py diff                           # 改之后：看翻转

**② 金标用例（每次改完必跑）**
    python3 backtest.py golden                         # tests/golden.json 逐条验
    python3 backtest.py golden --verbose

金标用例钉的是**真实发生过的日子**（10-22 同向偏软、10-24 单腿过门槛、10-08 截断、
10-18 配速差一档…），不是理想值。任何一条翻转都意味着这次改口径动了历史结论，
必须先解释清楚再合并。

`snapshot` 存到 `reports/backtest/<时间戳>.json`（已 gitignore 的派生目录）。
"""
import argparse
import datetime
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from settings import ROOT as _WORKSPACE  # noqa: E402  路径唯一解析点（settings.py）

import db                                                    # noqa: E402

OUT_DIR = os.environ.get("HEALTH_BACKTEST_DIR") or os.path.join(_WORKSPACE, "reports", "backtest")
GOLDEN = os.path.join(_WORKSPACE, "tests", "golden.json")
LATEST = os.path.join(OUT_DIR, "latest.json")


# ------------------------------------------------------------------ 快照
def dump(con):
    """全量断言 → {date: {id: {verdict, level}}}。只含 day 尺度（跨尺度另列）。"""
    rows = {}
    for r in con.execute(
            "SELECT date, id, verdict, level, evidence FROM assertions "
            "WHERE scale='day' ORDER BY date, id"):
        try:
            ev = json.loads(r["evidence"] or "{}")
        except Exception:
            ev = {}
        rows.setdefault(r["date"], {})[r["id"]] = {
            "verdict": r["verdict"], "level": r["level"], "evidence": ev}
    meta = db.get_meta(con)
    return {"at": datetime.datetime.now().isoformat(timespec="seconds"),
            "rules_hash": meta.get("rules_hash"), "engine_ver": meta.get("engine_ver"),
            "data_hash": meta.get("data_hash"), "days": rows}


def cmd_snapshot(a):
    con = db.connect()
    snap = dump(con)
    con.close()
    os.makedirs(OUT_DIR, exist_ok=True)
    path = a.out or os.path.join(
        OUT_DIR, "snap_%s.json" % datetime.datetime.now().strftime("%Y%m%dT%H%M%S"))
    json.dump(snap, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(snap, open(LATEST, "w", encoding="utf-8"), ensure_ascii=False)
    n = sum(len(v) for v in snap["days"].values())
    print("基线：%s（%d 天 / %d 条断言，rules %s）"
          % (path, len(snap["days"]), n, (snap.get("rules_hash") or "")[:12]))
    return 0


# ------------------------------------------------------------------ 对比
def cmd_diff(a):
    base_path = a.baseline or LATEST
    if not os.path.isfile(base_path):
        raise SystemExit("没有基线：先跑 `backtest.py snapshot`（找的是 %s）" % base_path)
    base = json.load(open(base_path, encoding="utf-8"))
    con = db.connect()
    cur = dump(con)
    con.close()

    print("基线：%s（rules %s）" % (base_path, (base.get("rules_hash") or "")[:12]))
    print("当前：rules %s%s" % ((cur.get("rules_hash") or "")[:12],
                               "（规则未变，预期零差异）"
                               if cur.get("rules_hash") == base.get("rules_hash") else ""))
    flips, added, removed = [], [], []
    dates = sorted(set(base["days"]) | set(cur["days"]))
    for d in dates:
        b, c = base["days"].get(d, {}), cur["days"].get(d, {})
        for i in sorted(set(b) | set(c)):
            if i not in b:
                added.append((d, i, c[i]["verdict"], c[i]["level"]))
            elif i not in c:
                removed.append((d, i, b[i]["verdict"], b[i]["level"]))
            elif b[i]["verdict"] != c[i]["verdict"] or b[i]["level"] != c[i]["level"]:
                flips.append((d, i, b[i], c[i]))
    if not (flips or added or removed):
        print("\n零翻转：历史结论完全一致。")
        return 0
    if flips:
        print("\n判定翻转 %d 条：" % len(flips))
        for d, i, b, c in flips:
            print("  %-12s %-38s %s/%s → %s/%s" % (
                d, i, "触发" if b["verdict"] else "未触发", b["level"] or "-",
                "触发" if c["verdict"] else "未触发", c["level"] or "-"))
    if added:
        print("\n新增断言 %d 条：%s…" % (len(added), "、".join("%s %s" % (d, i) for d, i, _, _ in added[:8])))
    if removed:
        print("\n消失断言 %d 条：%s…" % (len(removed), "、".join("%s %s" % (d, i) for d, i, _, _ in removed[:8])))
    print("\n每条翻转都要能回答：这是我有意改的，还是改别的带出来的？答不上来就不该合并。")
    return 1 if a.strict else 0


# ------------------------------------------------------------------ 金标
OPS = {"==": lambda x, y: abs(x - y) < 1e-9, "!=": lambda x, y: abs(x - y) >= 1e-9,
       ">": lambda x, y: x > y, ">=": lambda x, y: x >= y,
       "<": lambda x, y: x < y, "<=": lambda x, y: x <= y}


def cmd_golden(a):
    if not os.path.isfile(GOLDEN):
        raise SystemExit("缺 %s" % GOLDEN)
    g = json.load(open(GOLDEN, encoding="utf-8"))
    con = db.connect()
    bad = []
    print("%-4s %-12s %-38s %s" % ("", "日期", "断言", "说明"))
    for i, c in enumerate(g["cases"], 1):
        d, aid, want_v = c["date"], c["id"], c["verdict"]
        row = con.execute(
            "SELECT verdict, level, evidence, subject FROM assertions "
            "WHERE date=? AND id=? AND scale='day'", (d, aid)).fetchone()
        if row is None:
            bad.append((d, aid, "库里没有这条断言"))
            print("%-4s %-12s %-38s ✗ 不存在" % (i, d, aid))
            continue
        problems = []
        if row["verdict"] != want_v:
            problems.append("触发=%d，期望 %d" % (row["verdict"], want_v))
        want_lv = c.get("level")
        if (row["level"] or None) != (want_lv or None):
            problems.append("级别=%s，期望 %s" % (row["level"], want_lv))
        ev = json.loads(row["evidence"] or "{}")
        for ea in (c.get("evidence_assert") or []):
            v = ev.get(ea["k"])
            if v is None:
                problems.append("证据缺 %s" % ea["k"])
            elif not OPS[ea["op"]](float(v), float(ea["v"])):
                problems.append("证据 %s=%s，期望 %s %s" % (ea["k"], v, ea["op"], ea["v"]))
        if problems:
            bad.append((d, aid, "；".join(problems)))
            print("%-4s %-12s %-38s ✗ %s" % (i, d, aid, "；".join(problems)))
            if a.verbose:
                print("     why: %s" % c.get("why"))
        else:
            print("%-4s %-12s %-38s ✓" % (i, d, aid))
    con.close()
    print("\n%s（%d / %d 通过）" % ("金标全过" if not bad else "金标不通过",
                                 len(g["cases"]) - len(bad), len(g["cases"])))
    if bad:
        print("翻转用例的判读意义：")
        for d, aid, why in bad:
            case = next(x for x in g["cases"] if x["date"] == d and x["id"] == aid)
            print("  · %s %s — %s" % (d, aid, case.get("why", "")))
    return 0 if not bad else 1


def main():
    ap = argparse.ArgumentParser(description="改口径后的回测与金标校验")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("snapshot", help="存一份当前断言基线")
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_snapshot)
    p = sub.add_parser("diff", help="与基线对比，列出翻转")
    p.add_argument("--baseline", default=None)
    p.add_argument("--strict", action="store_true", help="有翻转时 exit 1")
    p.set_defaults(func=cmd_diff)
    p = sub.add_parser("golden", help="跑 tests/golden.json 金标用例")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_golden)
    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
