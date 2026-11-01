#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""条件筛选的不变量检查（roadmap P4b 的验收）。

**为什么不写死期望值**：数据每天在长，`命中 16 天` 这种断言明天就假了，
而为了让测试变绿去改期望值，正是这份仓库最想避免的事。所以这里只验**不变量**——
「凡是命中的日子，一定满足条件」「negate 前后两条集合互补」这类**恒真命题**。
它们不会随数据增长失效，只会在逻辑坏掉时变红。

覆盖这三类条件各自的语义，以及两个最容易出错的地方：
  · 不可判定（缺数据）不许被当成「未命中」——那会让筛选偏向有数据的日子
  · 最小样本门：n 不够时判定必须是 na，不是「凑出来的 ok」

    python3 tools/query_checks.py
    python3 tools/query_checks.py --json
"""
import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skills", "body-health", "scripts"))

import db       # noqa: E402
import query    # noqa: E402
import units    # noqa: E402

WIN = None          # 由 main 按数据范围填
RESULTS = []


def check(name):
    def deco(fn):
        def wrapper(*a, **kw):
            try:
                detail = fn(*a, **kw)
                RESULTS.append({"检查": name, "ok": True, "detail": detail})
            except AssertionError as e:
                RESULTS.append({"检查": name, "ok": False, "detail": str(e)})
        return wrapper
    return deco


def _candidates(ctx):
    """窗口内的候选日期（与 query.window_spec 同一裁剪规则）。"""
    lo = max(WIN["start"], ctx["first"])
    hi = min(WIN["end"], min(ctx["last"], ctx["today"]))
    d0 = datetime.date.fromisoformat(lo)
    d1 = datetime.date.fromisoformat(hi)
    return [(d0 + datetime.timedelta(days=i)).isoformat()
            for i in range((d1 - d0).days + 1)] if d1 >= d0 else []


def _ctx(reg, con, rules):
    return units.context(con, rules, query.metrics_pool(reg))


def c_metric(con, rules, reg, ctx, ev, gates):
    cond = {"kind": "metric", "metric": "sleep_h", "op": "<", "value": 6.5}
    res = query.run({"window": WIN, "metrics": ["sleep_h"], "conditions": [cond]},
                    con=con, rules=rules, reg=reg, ctx=ctx)
    dates = set(res["selection"]["matched_dates"])
    # 正向：命中的每一天都真的满足条件
    for d in dates:
        v = ctx["day_vals"].get(d, {}).get("sleep_h")
        assert v is not None and v < 6.5, "%s 命中但 sleep_h=%r" % (d, v)
    # 反向：可判定且不满足条件的日子一个都不能漏在命中集外/内
    for d in _candidates(ctx):
        v = ctx["day_vals"].get(d, {}).get("sleep_h")
        if v is None:
            continue
        assert (d in dates) == (v < 6.5), "%s 命中判定错误（sleep_h=%.2f）" % (d, v)
    return "命中 %d 天；正反两向逐日复核 sleep_h<6.5 全部成立" % len(dates)


def c_negate(con, rules, reg, ctx, ev, gates):
    base = {"kind": "metric", "metric": "sleep_h", "op": "<", "value": 6.5}
    r1 = query.run({"window": WIN, "metrics": ["sleep_h"], "conditions": [base]},
                   con=con, rules=rules, reg=reg, ctx=ctx)
    r2 = query.run({"window": WIN, "metrics": ["sleep_h"],
                    "conditions": [dict(base, negate=True)]},
                   con=con, rules=rules, reg=reg, ctx=ctx)
    a = set(r1["selection"]["matched_dates"])
    b = set(r2["selection"]["matched_dates"])
    assert not (a & b), "negate 前后命中集相交：%s" % sorted(a & b)[:3]
    decidable = {d for d in _candidates(ctx)
                 if ctx["day_vals"].get(d, {}).get("sleep_h") is not None}
    assert a | b == decidable, ("negate 前后未构成可判定日的划分（缺 %d 天）"
                                % len(decidable - (a | b)))
    return "命中/非命中互补，正好覆盖 %d 个可判定日" % len(decidable)


def c_event_shift(con, rules, reg, ctx, ev, gates):
    cond = {"kind": "event", "contains": "凌晨", "shift": 1}
    res = query.run({"window": WIN, "metrics": ["rhr_bpm"], "conditions": [cond]},
                    con=con, rules=rules, reg=reg, ctx=ctx)
    dates = set(res["selection"]["matched_dates"])
    for d in dates:
        src = (datetime.date.fromisoformat(d) - datetime.timedelta(days=1)).isoformat()
        assert [e for e in (ev.get(src) or []) if "凌晨" in (e.get("content") or "")], \
            "%s 命中但前一日(%s)没有含「凌晨」的事件" % (d, src)
    # 反向：前一日有该事件的日子，必须命中
    for d in _candidates(ctx):
        src = (datetime.date.fromisoformat(d) - datetime.timedelta(days=1)).isoformat()
        has = bool([e for e in (ev.get(src) or []) if "凌晨" in (e.get("content") or "")])
        assert (d in dates) == has, "%s 的 shift 判定错误（源日 %s）" % (d, src)
    return "命中 %d 天；正反两向逐日复核「前一日有含凌晨事件」" % len(dates)


def c_collect(con, rules, reg, ctx, ev, gates):
    cond = {"kind": "collect", "level_in": ["complete"]}
    res = query.run({"window": WIN, "metrics": ["steps_daily"], "conditions": [cond]},
                    con=con, rules=rules, reg=reg, ctx=ctx)
    dates = res["selection"]["matched_dates"]
    for d in dates:
        lvl = gates.get(d, "missing_raw")
        assert lvl == "complete", "%s 命中但闸门=%s" % (d, lvl)
    assert res["selection"]["undecidable"] == 0, "采集条件不该有不可判定日"
    return "命中 %d 天，闸门级别全为 complete" % len(dates)


def c_min_n(con, rules, reg, ctx, ev, gates):
    """构造一个必然样本不足的查询：窗口只留 3 天。"""
    last = min(ctx["last"], ctx["today"])
    start = (datetime.date.fromisoformat(last) - datetime.timedelta(days=2)).isoformat()
    res = query.run({"window": {"start": start, "end": last},
                     "metrics": ["rhr_bpm", "sleep_h"],
                     "conditions": [{"kind": "metric", "metric": "sleep_h",
                                     "op": "<", "value": 99}]},
                    con=con, rules=rules, reg=reg, ctx=ctx)
    req = (rules.get("query") or {}).get("min_n_selected")
    assert req, "rules.query.min_n_selected 没配"
    bad = [r for r in res["rows"] if r["n"] < req
           and (r["judge"] not in (None, "na") or r["delta_verdict"] != "insufficient")]
    assert not bad, "样本不足却给了结论：%s" % bad[:2]
    return "样本不足（< %d）时判定一律 na、变化一律 insufficient" % req


def c_partition(con, rules, reg, ctx, ev, gates):
    """命中 + 排除 + 不可判定 = 候选天数（三个桶必须互斥且穷尽）。"""
    for conds, logic in (([{"kind": "metric", "metric": "sleep_h", "op": "<", "value": 7}], "and"),
                         ([{"kind": "metric", "metric": "sleep_h", "op": "<", "value": 7},
                           {"kind": "collect", "level_in": ["complete"]}], "and"),
                         ([{"kind": "metric", "metric": "sleep_h", "op": "<", "value": 7},
                           {"kind": "collect", "level_in": ["complete"]}], "or")):
        res = query.run({"window": WIN, "metrics": ["sleep_h"], "conditions": conds,
                         "logic": logic}, con=con, rules=rules, reg=reg, ctx=ctx)
        s = res["selection"]
        assert s["matched"] + s["excluded"] + s["undecidable"] == s["candidates"], \
            "三个桶不闭合：%s" % s
        assert len(s["matched_dates"]) == s["matched"], "matched_dates 与计数不符"
    return "and / 双条件 and / or 三种组合下三桶均闭合"


def main():
    global WIN
    ap = argparse.ArgumentParser(description="条件筛选不变量检查")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    con = db.connect()
    rules = db.load_rules()
    reg = db.load_metrics()
    ctx = _ctx(reg, con, rules)
    if ctx is None:
        print("skip: obs 表为空")
        return 0
    WIN = {"start": ctx["first"], "end": min(ctx["last"], ctx["today"])}
    ev, gates = query._load_events(), query._load_gates(con)
    for fn in (c_metric, c_negate, c_event_shift, c_collect, c_min_n, c_partition):
        check(fn.__name__[2:])(fn)(con, rules, reg, ctx, ev, gates)
    if a.json:
        print(json.dumps(RESULTS, ensure_ascii=False, indent=1))
    else:
        for r in RESULTS:
            print("%s %-14s %s" % ("✓" if r["ok"] else "✗", r["检查"], r["detail"]))
        bad = [r for r in RESULTS if not r["ok"]]
        print("\n%s（%d/%d 通过）" % ("全部通过" if not bad else "有失败",
                                    len(RESULTS) - len(bad), len(RESULTS)))
    return 0 if all(r["ok"] for r in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
