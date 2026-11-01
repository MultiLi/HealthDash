#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任意窗口查询（roadmap P4）。**L2 的第二个消费端，不是第二套数学。**

## 它解决什么

以前只有两类问法：`units.py get --scale week --key 2026-W43`（六个写死的尺度）
和日报告（固定模板）。「过去 12 天」「10-01 到 10-20」「最近 30 天里采集正常的日子」
这类问题**没法问**——不是没数据，是没有表达方式。

## 两条硬约束（`docs/query-interface.md`）

1. **数学只有一份。** 聚合、覆盖门禁、达标、环比全部调 `units.py` 的
   `compute_cell` / `apply_delta`——与本文件之外的重建走同一条路。任何在这里
   重写一遍的想法，都会让主页、报告、对话在同一个问题上给出不同答案。
2. **判定永远在后端。** 返回的每行都带 `judge` / `delta_verdict`，由代码算好；
   调用方（页面、agent、报告）只读结论，不许自己比大小。

## 查询定义

```json
{"window": {"start": "2026-10-01", "end": "2026-10-20"},
 "metrics": ["hrv_ms", "rhr_bpm"],        // 省略 = 全部可聚合指标
 "delta": true}                            // 省略 = true
```

窗口也可以是已标定的单元：`{"window": {"scale": "week", "key": "2026-W43"}}`。
未知字段一律报错（防止把 `metric` 写成 `metrics` 这类静默失效）。

## 用法

    python3 query.py --start 2026-10-01 --end 2026-10-20
    python3 query.py --days 14 --metrics hrv_ms,rhr_bpm
    python3 query.py --scale week --key 2026-W43 --json
    python3 query.py --q '{"window":{"start":"2026-09-01","end":"2026-10-31"}}'
    python3 query.py --list-metrics
"""
import argparse
import datetime
import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import db                                                    # noqa: E402
import units                                                 # noqa: E402

from settings import EVENTS_LOG                              # noqa: E402

SCALE_NAMES = ("day", "week", "month", "roll7", "roll28", "roll90")
#: 查询定义允许的键。多一个都报错——静默忽略拼错的字段会让调用方以为查了。
Q_KEYS = {"window", "metrics", "delta"}
WIN_KEYS = {"start", "end", "scale", "key"}


class QueryError(ValueError):
    pass


# ------------------------------------------------------------------ 规范化
def metrics_pool(reg=None):
    """可查询的指标：有 agg 的日级指标（片段指标不在任意窗口口径内）。"""
    reg = reg if reg is not None else db.load_metrics()
    frag = units.frag_metrics(reg)
    return {k: v for k, v in reg.items() if v.get("agg") and k not in frag}


def normalize(qdef, reg=None):
    """校验并补全查询定义。**未知字段报错**，不静默忽略。"""
    qdef = dict(qdef or {})
    extra = set(qdef) - Q_KEYS
    if extra:
        raise QueryError("查询定义里有未知字段：%s（允许：%s）"
                         % ("、".join(sorted(extra)), "、".join(sorted(Q_KEYS))))
    win = dict(qdef.get("window") or {})
    if not win:
        raise QueryError("缺 window（要么 {start,end}，要么 {scale,key}）")
    wextra = set(win) - WIN_KEYS
    if wextra:
        raise QueryError("window 里有未知字段：%s" % "、".join(sorted(wextra)))
    if "scale" in win:
        if win["scale"] not in SCALE_NAMES:
            raise QueryError("未知尺度：%s（允许：%s）" % (win["scale"], "、".join(SCALE_NAMES)))
        if not win.get("key"):
            raise QueryError("给了 scale 就必须给 key")
        if "start" in win or "end" in win:
            raise QueryError("scale/key 与 start/end 只能二选一")
    else:
        for k in ("start", "end"):
            if not win.get(k):
                raise QueryError("window 必须同时有 start 与 end（或改用 scale/key）")
            try:
                datetime.date.fromisoformat(win[k])
            except ValueError:
                raise QueryError("%s 不是 YYYY-MM-DD：%s" % (k, win[k]))
        if win["start"] > win["end"]:
            raise QueryError("start(%s) 晚于 end(%s)" % (win["start"], win["end"]))
    pool = metrics_pool(reg)
    names = qdef.get("metrics")
    if names in (None, [], "all"):
        names = sorted(pool)
    else:
        if isinstance(names, str):
            names = [x.strip() for x in names.split(",") if x.strip()]
        unknown = [m for m in names if m not in pool]
        if unknown:
            raise QueryError("未知指标：%s（--list-metrics 看全部 %d 个）"
                             % ("、".join(unknown), len(pool)))
    return {"window": win, "metrics": list(names),
            "delta": bool(qdef.get("delta", True))}


# ------------------------------------------------------------------ 求值
def _provenance(con):
    meta = db.get_meta(con)
    ev = None
    if os.path.isfile(EVENTS_LOG):
        h = hashlib.sha1()
        with open(EVENTS_LOG, "rb") as fh:
            h.update(fh.read())
        ev = h.hexdigest()[:12]
    return {"rules_hash": meta.get("rules_hash"), "engine_ver": meta.get("engine_ver"),
            "data_hash": meta.get("data_hash"), "built_at": meta.get("built_at"),
            "span": meta.get("span"), "events_hash": ev}


def _specs_for(q, ctx, rules):
    """查询定义 → 目标 spec + （可选）上一窗 spec。尺度的日期数学全部复用 unit_specs。"""
    win = q["window"]
    if "scale" in win:
        idx = {(sp["scale"], sp["key"]): sp
               for sp in units.unit_specs(ctx["first"], ctx["last"], ctx["today"], rules)}
        sp = idx.get((win["scale"], win["key"]))
        if sp is None:
            raise QueryError("没有这个单元：%s %s" % (win["scale"], win["key"]))
        prev = idx.get((win["scale"], units.prev_key(
            win["scale"], win["key"],
            k=int(win["scale"][4:]) if win["scale"].startswith("roll") else None)))
        return sp, prev
    sp = units.window_spec(win["start"], win["end"], ctx, rules)
    if not sp["dates"]:
        raise QueryError("窗口内没有任何已采集的日期（数据范围 %s ~ %s）"
                         % (ctx["first"], ctx["last"]))
    # 任意窗口的「上一窗」＝整段前移一个窗长（非重叠，与滚动窗同一纪律）
    n = len(sp["dates"])
    d0 = datetime.date.fromisoformat(sp["dates"][0])
    d1 = datetime.date.fromisoformat(sp["dates"][-1])
    prev = units.window_spec((d0 - datetime.timedelta(days=n)).isoformat(),
                             (d1 - datetime.timedelta(days=n)).isoformat(), ctx, rules)
    return sp, prev


def run(qdef, con=None, rules=None, reg=None, ctx=None):
    """执行一次查询。返回 {query, ruler, window, provenance, rows, empty}。"""
    con = con or db.connect()
    rules = rules or db.load_rules()
    reg = reg if reg is not None else db.load_metrics()
    q = normalize(qdef, reg)
    metrics = {m: reg[m] for m in q["metrics"]}
    ctx = ctx or units.context(con, rules, metrics)
    if ctx is None:
        raise QueryError("obs 表为空：先跑 index.py build")
    sp, prev_sp = _specs_for(q, ctx, rules)
    rows = units.compute_cells(sp, metrics, ctx, rules)
    cells = {(r["scale"], r["key"], r["metric"]): r for r in rows}
    if q["delta"] and prev_sp is not None and prev_sp["dates"]:
        for r in units.compute_cells(prev_sp, metrics, ctx, rules):
            cells[(r["scale"], r["key"], r["metric"])] = r
        # 本查询的所有行同属一个 spec，上一个单元恒为 prev_sp（任意窗口＝前移一窗长）
        units.apply_delta(rows, cells, ctx, rules,
                          key_of_prev=lambda sc, key: prev_sp["key"])
    for r in rows:
        mdef = reg.get(r["metric"]) or {}
        r["label"] = mdef.get("label")
        r["unit"] = mdef.get("unit")
        r["dir"] = mdef.get("dir")
    rows.sort(key=lambda r: (r["metric"]))
    return {
        "query": q,
        "ruler": sp.get("ruler") or sp["scale"],
        "window": {"start": sp["start"], "end": sp["end"],
                   "days": len(sp["dates"]), "expected": sp["expected"],
                   "first": sp["dates"][0] if sp["dates"] else None,
                   "last": sp["dates"][-1] if sp["dates"] else None,
                   "prev": ({"start": prev_sp["start"], "end": prev_sp["end"],
                             "days": len(prev_sp["dates"])}
                            if prev_sp and prev_sp["dates"] else None)},
        "provenance": _provenance(con),
        "rows": rows,
        "empty": [m for m in q["metrics"] if m not in {r["metric"] for r in rows}],
    }


# ------------------------------------------------------------------ 展示
def _fmt(v, mdef, m):
    if v is None:
        return "—"
    if m == "sleep_h":
        return "%.2fh" % v
    if isinstance(v, float) and abs(v - round(v)) < 1e-9:
        return "%d" % round(v)
    return ("%.2f" % v).rstrip("0").rstrip(".")


def render(res):
    w = res["window"]
    out = []
    out.append("窗口 %s ~ %s（%d 天有效 / 分母 %d）· 尺子借用 %s"
               % (w["first"], w["last"], w["days"], w["expected"], res["ruler"]))
    if w["prev"]:
        out.append("对比窗 %s ~ %s（%d 天）" % (w["prev"]["start"], w["prev"]["end"],
                                            w["prev"]["days"]))
    p = res["provenance"]
    out.append("真值版本 rules %s · data %s · engine %s · built %s"
               % ((p["rules_hash"] or "-")[:8], (p["data_hash"] or "-")[:8],
                  (p["engine_ver"] or "-")[:8], p["built_at"] or "-"))
    out.append("")
    out.append("%-16s %10s %4s %6s %-4s %-14s %s"
               % ("指标", "值", "n", "覆盖", "达标", "变化", "说明"))
    for r in res["rows"]:
        rate = "(%d%%)" % round(r["judge_rate"] * 100) if r.get("judge_rate") is not None else ""
        out.append("%-16s %10s %4d %5.0f%% %-4s %-14s %s"
                   % (r["metric"], _fmt(r["value"], None, r["metric"]), r["n"],
                      (r["coverage"] or 0) * 100, (r["judge"] or "-") + rate,
                      r["delta_verdict"] or "-",
                      ("" if r["delta_v"] is None else "Δ%+.2f " % r["delta_v"])
                      + (r["delta_note"] or "")))
    if res["empty"]:
        out.append("\n无数据的指标 %d 个：%s（na ≠ 0，是没有观测，不是零）"
                   % (len(res["empty"]), "、".join(res["empty"])))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="任意窗口查询（复用 units 的唯一数学）")
    ap.add_argument("--q", default="", help="查询定义 JSON")
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--days", type=int, default=0, help="最近 N 天（截止最后一天数据）")
    ap.add_argument("--scale", default="", choices=SCALE_NAMES)
    ap.add_argument("--key", default="")
    ap.add_argument("--metrics", default="all", help="逗号分隔，或 all")
    ap.add_argument("--no-delta", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list-metrics", action="store_true")
    a = ap.parse_args()

    if a.list_metrics:
        pool = metrics_pool()
        print("%-18s %-8s %-6s %s" % ("指标", "方向", "聚合", "说明"))
        for m, d in sorted(pool.items()):
            print("%-18s %-8s %-6s %s" % (m, d.get("dir") or "-", d.get("agg") or "-",
                                          (d.get("label") or "")))
        print("\n共 %d 个（片段指标 hour_* 不在任意窗口口径内，见 docs/fragment-layer.md）"
              % len(pool))
        return 0

    try:
        if a.q:
            qdef = json.loads(a.q)
        elif a.scale:
            qdef = {"window": {"scale": a.scale, "key": a.key}}
        elif a.days:
            con = db.connect()
            ctx0 = units.context(con, db.load_rules(), {})
            if ctx0 is None:
                raise QueryError("obs 表为空：先跑 index.py build")
            end = min(ctx0["last"], ctx0["today"])
            start = (datetime.date.fromisoformat(end)
                     - datetime.timedelta(days=a.days - 1)).isoformat()
            qdef = {"window": {"start": start, "end": end}}
        else:
            qdef = {"window": {"start": a.start, "end": a.end}}
        if a.metrics and a.metrics != "all":
            qdef["metrics"] = a.metrics
        if a.no_delta:
            qdef["delta"] = False
        res = run(qdef)
    except QueryError as e:
        print("✗ %s" % e, file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
