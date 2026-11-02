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
import conclusions as conc                                   # noqa: E402

from settings import EVENTS_LOG                              # noqa: E402
from settings import VIEWS                                   # noqa: E402

SCALE_NAMES = ("day", "week", "month", "roll7", "roll28", "roll90")
#: 查询定义允许的键。多一个都报错——静默忽略拼错的字段会让调用方以为查了。
Q_KEYS = {"window", "metrics", "delta", "conditions", "logic", "conclusions"}
WIN_KEYS = {"start", "end", "scale", "key", "days"}
#: 条件对象的允许键（按 kind 细分，见 _check_cond）
COND_KEYS = {
    "metric": {"kind", "metric", "op", "value", "range", "negate"},
    "event": {"kind", "type", "metric", "contains", "op", "value", "shift", "negate"},
    "collect": {"kind", "level_in", "exclude_levels", "negate"},
}
OPS = {
    "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
}


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
    if "days" in win:
        if not isinstance(win["days"], int) or win["days"] <= 0:
            raise QueryError("days 必须是正整数")
        if set(win) - {"days"}:
            raise QueryError("days 是相对窗口（截止最后一天数据），不能与 start/end/scale 混用")
    elif "scale" in win:
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
    conds = qdef.get("conditions") or []
    if not isinstance(conds, list):
        raise QueryError("conditions 必须是数组")
    reg = reg if reg is not None else db.load_metrics()
    for c in conds:
        _check_cond(c, reg)
    logic = str(qdef.get("logic") or "and").lower()
    if logic not in ("and", "or"):
        raise QueryError("logic 只能是 and / or")
    cids = qdef.get("conclusions") or []
    if isinstance(cids, str):
        cids = [x.strip() for x in cids.split(",") if x.strip()]
    if not isinstance(cids, list):
        raise QueryError("conclusions 必须是数组（断言 id 列表）")
    if cids == ["all"]:
        cids = conc.windowable()
    for aid in cids:
        _check_conclusion(aid)
    return {"window": win, "metrics": list(names),
            "delta": bool(qdef.get("delta", True)),
            "conditions": conds, "logic": logic, "conclusions": list(cids)}


def _check_conclusion(aid):
    """结论 id 必须在注册表里，且 kind=judge。**不许把归因型结论当窗口结论查。**

    `cross.*` 在日级是「同一时点两件事同时成立」；搬到窗口上只剩共现次数，
    再往前一步就是「这 30 天 HRV 低是因为熬夜」——跨时间的伪因果。
    这里直接拒绝，比事后在报告里删句子可靠。
    """
    try:
        spec = conc.spec_of(aid)
    except conc.ContractError as e:
        raise QueryError(str(e))
    if spec.get("kind") != "judge" or not spec.get("operator"):
        raise QueryError(
            "结论 %s 的 kind=%s，不能按窗口求值（只有 kind=judge 且声明了 operator 的可以）。"
            "cross 类只看共现，data/device/point 类不窗口化——详见 "
            "skills/body-health/references/conclusions.json 的 kinds 说明。"
            % (aid, spec.get("kind")))


def _cond_metrics(reg):
    """条件可用的指标：全部已注册的日级指标（含非聚合的参考量，如 sleep_need_h）。

    片段指标（hour_*）不行——它们是小时级观测，拿来当「这一天的条件」就是把
    粒度偷偷换了（见 docs/fragment-layer.md）。
    """
    return {k for k in reg if not k.startswith(units.fragments.PREFIX)}


def _check_cond(c, reg):
    if not isinstance(c, dict):
        raise QueryError("条件必须是对象")
    kind = c.get("kind")
    if kind not in COND_KEYS:
        raise QueryError("未知条件类型：%r（允许：metric / event / collect）" % (kind,))
    extra = set(c) - COND_KEYS[kind]
    if extra:
        raise QueryError("%s 条件里有未知字段：%s（允许：%s）"
                         % (kind, "、".join(sorted(extra)),
                            "、".join(sorted(COND_KEYS[kind]))))
    if kind == "metric":
        if not c.get("metric"):
            raise QueryError("metric 条件必须给 metric")
        if c["metric"] not in _cond_metrics(reg):
            raise QueryError("条件指标未注册或属于片段：%s" % c["metric"])
        has_range = "range" in c
        has_op = "op" in c
        if has_range == has_op:
            raise QueryError("metric 条件要么给 op+value，要么给 range，二选一")
        if has_op:
            if c["op"] not in OPS:
                raise QueryError("未知比较符：%s（允许：%s）" % (c["op"], "、".join(OPS)))
            if "value" not in c:
                raise QueryError("给了 op 就必须给 value")
        else:
            rng = c["range"]
            if not (isinstance(rng, (list, tuple)) and len(rng) == 2):
                raise QueryError("range 必须是 [下界, 上界]")
    elif kind == "event":
        if not any(c.get(k) for k in ("type", "metric", "contains")):
            raise QueryError("event 条件至少要给 type / metric / contains 之一")
        if c.get("metric") and c.get("op") and "value" not in c:
            raise QueryError("event 条件给了 op 就必须给 value")
        if c.get("op") and c["op"] not in OPS:
            raise QueryError("未知比较符：%s" % c["op"])
    else:
        if c.get("level_in") is None and c.get("exclude_levels") is None:
            raise QueryError("collect 条件要给 level_in 或 exclude_levels")



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


def _trace(con, dates, metrics, limit=30, concl_ids=None):
    """这次查询的**断言溯源**：窗口内与所查指标相关的断言 id。

    agent 的回答必须能指回「哪条断言支持这个说法」（`verify_judgment` 的数值溯源
    延伸到对话）。这里只给引用，不给新结论——判定仍然只在 L2/L3。
    """
    if not con or not dates:
        return None
    lo, hi = dates[0], dates[-1]
    wheres, args = [], []
    if metrics:
        wheres.append("subject IN (%s)" % ",".join("?" * len(metrics)))
        args += list(metrics)
    if concl_ids:
        wheres.append("id IN (%s)" % ",".join("?" * len(concl_ids)))
        args += list(concl_ids)
    if not wheres:
        return None
    sql = ("SELECT date, id, verdict, level, subject FROM assertions "
           "WHERE scale='day' AND date>=? AND date<=? AND (%s) "
           "ORDER BY date DESC, id" % " OR ".join(wheres))
    try:
        rows = list(con.execute(sql, [lo, hi] + args))
    except Exception as e:                        # 库是派生层，读不到不该让查询失败
        return {"error": str(e)[:120]}
    return {"count": len(rows), "truncated": len(rows) > limit,
            "items": [{"date": r["date"], "id": r["id"], "level": r["level"],
                       "verdict": r["verdict"], "subject": r["subject"]}
                      for r in rows[:limit]],
            "note": "断言 id 可用于追问/回查；没列出的见 assertions 表"}


def _specs_for(q, ctx, rules):
    """查询定义 → 目标 spec + （可选）上一窗 spec。尺度的日期数学全部复用 unit_specs。"""
    win = q["window"]
    if "days" in win:
        # 相对窗口：截止最后一天数据往前数 N 天。保存视图要的是「最近 14 天」，
        # 而不是「10-19 到 11-01」——后者存下来第二天就成了历史切片。
        end = min(ctx["last"], ctx["today"])
        start = (datetime.date.fromisoformat(end)
                 - datetime.timedelta(days=int(win["days"]) - 1)).isoformat()
        sp = units.window_spec(start, end, ctx, rules)
        n = len(sp["dates"])
        prev = units.window_spec(
            (datetime.date.fromisoformat(start) - datetime.timedelta(days=n)).isoformat(),
            (datetime.date.fromisoformat(start) - datetime.timedelta(days=1)).isoformat(),
            ctx, rules) if n else None
        return sp, prev
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


def _load_events():
    """按归属日索引事件（**排除被 supersede 的**：作废的事件不该影响条件筛选）。"""
    if not os.path.isfile(EVENTS_LOG):
        return {}
    import eventbus
    events = eventbus.read(EVENTS_LOG)
    dead = eventbus.superseded_ids(events)
    idx = {}
    for e in events:
        if e["_id"] in dead:
            continue
        idx.setdefault(e["date"], []).append(e)
    return idx


def _load_gates(con):
    return {r["date"]: r["level"] for r in con.execute("SELECT date, level FROM obs_day_gate")}


def _cmp_ok(v, cond):
    if "range" in cond:
        lo, hi = cond["range"]
        return lo <= v <= hi
    return OPS[cond["op"]](v, cond["value"])


def _event_hit(e, cond):
    p = e.get("payload") or {}
    if cond.get("type") and e.get("type") != cond["type"]:
        return False
    if cond.get("contains") and cond["contains"] not in (e.get("content") or ""):
        return False
    if cond.get("metric"):
        if p.get("metric") != cond["metric"]:
            return False
        if cond.get("op"):
            try:
                return OPS[cond["op"]](float(p.get("value")), float(cond["value"]))
            except (TypeError, ValueError):
                return False
    return True


def _day_matches(cond, d, ctx, ev_idx, gates):
    """True / False / **None（不可判定）**。三者必须分开：

    「那天没测睡眠」不等于「那天睡够了」。把不可判定当成 False 会让筛选结果
    悄悄偏向有数据的日子，而不知道的人会把这个偏差读成结论。
    """
    kind = cond["kind"]
    if kind == "metric":
        v = ctx["day_vals"].get(d, {}).get(cond["metric"])
        if v is None:
            return None
        res = _cmp_ok(float(v), cond)
    elif kind == "event":
        shift = int(cond.get("shift") or 0)
        src = (datetime.date.fromisoformat(d) - datetime.timedelta(days=shift)).isoformat()
        res = any(_event_hit(e, cond) for e in (ev_idx.get(src) or []))
    else:
        # 闸门表只含有 raw 的日子；没有 raw 就没有行 —— 那正是 missing_raw，
        # 不是「不知道」。当成不可判定会让采集条件永远有一堆不明不白的空洞。
        lvl = gates.get(d, "missing_raw")
        if cond.get("level_in") is not None:
            res = lvl in cond["level_in"]
        else:
            res = lvl not in (cond.get("exclude_levels") or [])
    if cond.get("negate"):
        res = not res
    return res


def select_days(q, dates, ctx, ev_idx, gates):
    """按条件挑日子。返回 (命中的日期, 统计)。

    `and` 语义下「有一条 False 就出局，有一条 None 就算不可判定」——这是为了让
    「某天缺睡眠数据」不会因为另一条条件满足而被当成命中。
    """
    matched, undecidable = [], 0
    per = [{"条件": c, "命中": 0, "未命中": 0, "不可判定": 0} for c in q["conditions"]]
    for d in dates:
        vs = []
        for i, c in enumerate(q["conditions"]):
            r = _day_matches(c, d, ctx, ev_idx, gates)
            if r is None:
                per[i]["不可判定"] += 1
            elif r:
                per[i]["命中"] += 1
            else:
                per[i]["未命中"] += 1
            vs.append(r)
        if q["logic"] == "and":
            ok = False if any(v is False for v in vs) else (
                True if all(v is True for v in vs) else None)
        else:
            ok = True if any(v is True for v in vs) else (
                False if all(v is False for v in vs) else None)
        if ok is True:
            matched.append(d)
        elif ok is None:
            undecidable += 1
    return matched, {"candidates": len(dates), "matched": len(matched),
                     "excluded": len(dates) - len(matched) - undecidable,
                     "undecidable": undecidable, "by_condition": per,
                     "logic": q["logic"], "matched_dates": matched}


def apply_min_n(rows, matched_n, required):
    """条件筛选后的最小样本门。**判定权在这里，不在调用方。**

    条件把样本变小之后，均值差很容易「看起来显著」——3 个熬夜日的 RHR 略高
    不是发现。低于门槛一律 na / insufficient，并说明原因；不许把门槛调低来凑结论。
    """
    if not required:
        return rows
    for r in rows:
        if r["n"] < int(required):
            if r.get("judge") not in (None,):
                r["judge"] = "na"
            r["judge_rate"] = None
            r["delta_verdict"] = "insufficient"
            why = "筛选后仅 %d 个实测日（门槛 %d）" % (r["n"], int(required))
            r["delta_note"] = why if not r.get("delta_note") else \
                "%s；%s" % (why, r["delta_note"])
            r["min_n_note"] = why
    return rows


def run(qdef, con=None, rules=None, reg=None, ctx=None):
    """执行一次查询。返回 {query, ruler, window, selection, provenance, rows, empty}。"""
    con = con or db.connect()
    rules = rules or db.load_rules()
    reg = reg if reg is not None else db.load_metrics()
    q = normalize(qdef, reg)
    metrics = {m: reg[m] for m in q["metrics"]}
    # 结论要用到自己的 inputs（可能不在 metrics 里），喂给 ctx 的指标集是二者并集
    want = set(metrics)
    for aid in q["conclusions"]:
        want |= {m for m in conc.inputs_of(aid) if m in reg}
    ctx = ctx or units.context(con, rules, {m: reg[m] for m in want})
    if ctx is None:
        raise QueryError("obs 表为空：先跑 index.py build")
    sp, prev_sp = _specs_for(q, ctx, rules)
    selection = None
    if q["conditions"]:
        ev_idx, gates = _load_events(), _load_gates(con)
        dates, selection = select_days(q, sp["dates"], ctx, ev_idx, gates)
        sp = units.window_spec(sp["start"], sp["end"], ctx, rules, dates=dates,
                               key="%s#cond" % sp["key"])
        if prev_sp is not None and prev_sp["dates"]:
            pdates, _ = select_days(q, prev_sp["dates"], ctx, ev_idx, gates)
            prev_sp = units.window_spec(prev_sp["start"], prev_sp["end"], ctx, rules,
                                        dates=pdates, key="%s#cond" % prev_sp["key"])
        if not dates:
            raise QueryError("这个条件下窗口内没有任何命中的日子"
                             "（候选 %d 天，不可判定 %d 天）"
                             % (selection["candidates"], selection["undecidable"]))
    rows = units.compute_cells(sp, metrics, ctx, rules)
    cells = {(r["scale"], r["key"], r["metric"]): r for r in rows}
    if q["delta"] and prev_sp is not None and prev_sp["dates"]:
        for r in units.compute_cells(prev_sp, metrics, ctx, rules):
            cells[(r["scale"], r["key"], r["metric"])] = r
        # 本查询的所有行同属一个 spec，上一个单元恒为 prev_sp（任意窗口＝前移一窗长）
        units.apply_delta(rows, cells, ctx, rules,
                          key_of_prev=lambda sc, key: prev_sp["key"])
    else:
        # 没有可比的上一窗时也要把话说明白，不能留个空白让人以为是零变化
        for r in rows:
            r["delta_verdict"] = "insufficient"
            r["delta_note"] = ("没有可比的上一窗（同条件、整段前移）"
                               if selection is not None else "没有可比的上一窗")
    min_n = int((rules.get("query") or {}).get("min_n_selected") or 0)
    if selection is not None and min_n:
        apply_min_n(rows, selection["matched"], min_n)
    for r in rows:
        mdef = reg.get(r["metric"]) or {}
        r["label"] = mdef.get("label")
        r["unit"] = mdef.get("unit")
        r["dir"] = mdef.get("dir")
    rows.sort(key=lambda r: (r["metric"]))
    # 定性结论：与数值行同窗口、同条件。判定由 conclusions.py 算，这里只搬运。
    # 条件用到的指标要传下去：筛选「睡眠<6.5h 的日子」再算「睡眠不足发生率」是循环。
    cond_metrics = {c.get("metric") for c in q["conditions"] if c.get("metric")}
    concl = conc.evaluate_window(con, sp, ids=q["conclusions"], rules=rules, ctx=ctx,
                                 cond_metrics=cond_metrics) \
        if q["conclusions"] else []
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
        "selection": selection,
        "min_n": ({"required": min_n} if selection is not None and min_n else None),
        "provenance": _provenance(con),
        "trace": _trace(con, sp["dates"], q["metrics"], concl_ids=q["conclusions"]),
        "rows": rows,
        "conclusions": concl,
        "empty": [m for m in q["metrics"] if m not in {r["metric"] for r in rows}],
    }


# ------------------------------------------------------------------ 保存视图
def _read_views(path=None):
    path = path or VIEWS
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh) or {}
    except ValueError as e:
        raise QueryError("views.json 不是合法 JSON：%s" % e)
    items = data.get("views") if isinstance(data, dict) else data
    return [v for v in (items or []) if isinstance(v, dict) and v.get("id")]


def _write_views(views, path=None):
    path = path or VIEWS
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"views": views}, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


def save_view(vid, qdef, title=None, note=None, path=None):
    """存一条视图定义。**存的是查询定义，不是结果**——结果由构建时求值。

    校验在这里做一次：存一个跑不通的查询，构建期它会变成一条 error 视图，
    页面显示「算不出来」——那比存的时候就拒绝更晚、更难查。
    """
    if not vid or not all(c.isalnum() or c in "-_" for c in vid):
        raise QueryError("视图 id 只能用字母/数字/-/_：%r" % vid)
    normalize(qdef)                                    # 跑不通的定义不许存
    views = _read_views(path)
    item = {"id": vid, "title": title or vid, "note": note, "query": qdef}
    for i, v in enumerate(views):
        if v["id"] == vid:
            views[i] = item
            break
    else:
        views.append(item)
    _write_views(views, path)
    return item


def remove_view(vid, path=None):
    views = _read_views(path)
    left = [v for v in views if v["id"] != vid]
    if len(left) == len(views):
        return False
    _write_views(left, path)
    return True



def _fmt(v, mdef, m):
    if v is None:
        return "—"
    if m == "sleep_h":
        return "%.2fh" % v
    if isinstance(v, float) and abs(v - round(v)) < 1e-9:
        return "%d" % round(v)
    return ("%.2f" % v).rstrip("0").rstrip(".")


def _cond_label(c):
    k = c.get("kind")
    neg = "非" if c.get("negate") else ""
    if k == "metric":
        body = c.get("range") and "%s ∈ %s" % (c["metric"], c["range"]) or \
            "%s %s %s" % (c["metric"], c.get("op"), c.get("value"))
    elif k == "event":
        bits = []
        if c.get("type"):
            bits.append("type=%s" % c["type"])
        if c.get("metric"):
            bits.append("%s %s %s" % (c["metric"], c.get("op") or "存在", c.get("value") or ""))
        if c.get("contains"):
            bits.append("含「%s」" % c["contains"])
        if c.get("shift"):
            bits.append("前 %d 日" % c["shift"])
        body = "事件 " + "·".join(bits)
    else:
        body = "采集 %s" % (c.get("level_in") or ("不含 %s" % c.get("exclude_levels")))
    return neg + body


def render(res):
    w = res["window"]
    s = res.get("selection")
    out = []
    if s:
        out.append("查询范围 %s ~ %s（候选 %d 天）→ 命中 %d 天（%s ~ %s，分母 %d）· 尺子借用 %s"
                   % (w["start"], w["end"], s["candidates"], s["matched"],
                      w["first"], w["last"], w["expected"], res["ruler"]))
        out.append("条件（%s）%s" % (s["logic"].upper(),
                                  " + ".join(_cond_label(c) for c in res["query"]["conditions"])))
        out.append("筛选：命中 %d · 排除 %d · **不可判定 %d**（缺数据的日子既不算命中也不算排除）"
                   % (s["matched"], s["excluded"], s["undecidable"]))
        for c in s["by_condition"]:
            out.append("   · %-34s 命中 %d / 未 %d / 不可判 %d"
                       % (_cond_label(c["条件"])[:34], c["命中"], c["未命中"], c["不可判定"]))
        if res.get("min_n"):
            out.append("最小样本门：%d 个实测日（低于它一律 na，不许把门槛调低凑结论）"
                       % res["min_n"]["required"])
    else:
        out.append("窗口 %s ~ %s（%d 天有效 / 分母 %d）· 尺子借用 %s"
                   % (w["first"], w["last"], w["days"], w["expected"], res["ruler"]))
    if w["prev"]:
        out.append("对比窗 %s ~ %s（%d 天%s）"
                   % (w["prev"]["start"], w["prev"]["end"], w["prev"]["days"],
                      "，同条件" if s else ""))
    p = res["provenance"]
    out.append("真值版本 rules %s · data %s · engine %s · events %s · built %s"
               % ((p["rules_hash"] or "-")[:8], (p["data_hash"] or "-")[:8],
                  (p["engine_ver"] or "-")[:8], (p.get("events_hash") or "-")[:8],
                  p["built_at"] or "-"))
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
    if res.get("conclusions"):
        out.append("")
        out.append(conc.render(res["conclusions"]))
        out.append("（窗口级结论是描述统计：比例 + 段结构 + 精度区间。"
                   "「持续」「长期」这类词只能建立在 state=成立的结论上）")
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
    ap.add_argument("--conclusions", default="",
                    help="逗号分隔的断言 id（按窗口聚合成定性结论）；all = 全部可窗口化的")
    ap.add_argument("--list-conclusions", action="store_true", help="列出可查的结论 id")
    ap.add_argument("--no-delta", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list-metrics", action="store_true")
    ap.add_argument("--save", default="", help="把本次查询存成视图（需要 id）")
    ap.add_argument("--title", default="", help="视图标题（配合 --save）")
    ap.add_argument("--note", default="", help="视图备注（配合 --save）")
    ap.add_argument("--list-views", action="store_true")
    ap.add_argument("--rm-view", default="")
    ap.add_argument("--explain", action="store_true",
                    help="只打印规范化后的查询（不改任何东西），用于复述意图")
    a = ap.parse_args()

    if a.list_views:
        views = _read_views()
        if not views:
            print("还没有保存的视图（data/views.json 不存在或为空）")
            return 0
        for v in views:
            w = (v.get("query") or {}).get("window") or {}
            if "days" in w:
                span = "最近 %d 天（相对）" % w["days"]
            elif w.get("start"):
                span = "%s~%s" % (w["start"], w["end"])
            else:
                span = "%s %s" % (w.get("scale"), w.get("key"))
            conds = (v.get("query") or {}).get("conditions") or []
            print("%-18s %-28s %s%s" % (v["id"], (v.get("title") or "")[:28], span,
                                        " +%d 条件" % len(conds) if conds else ""))
        return 0
    if a.rm_view:
        print("已删除 %s" % a.rm_view if remove_view(a.rm_view)
              else "没有这个视图：%s" % a.rm_view)
        return 0

    if a.list_metrics:
        pool = metrics_pool()
        print("%-18s %-8s %-6s %s" % ("指标", "方向", "聚合", "说明"))
        for m, d in sorted(pool.items()):
            print("%-18s %-8s %-6s %s" % (m, d.get("dir") or "-", d.get("agg") or "-",
                                          (d.get("label") or "")))
        print("\n共 %d 个（片段指标 hour_* 不在任意窗口口径内，见 docs/fragment-layer.md）"
              % len(pool))
        return 0

    if a.list_conclusions:
        lex = conc.lexicon()
        print("%-34s %-8s %-16s %s" % ("id", "算子", "inputs", "中文名"))
        for aid in conc.windowable():
            s = conc.spec_of(aid)
            print("%-34s %-8s %-16s %s" % (aid, s.get("operator"),
                                           ",".join(s.get("inputs") or [])[:16],
                                           lex.get(aid) or "（缺词表）"))
        print("\n共 %d 条可窗口化结论（跨日归因型与采集状态型不在此列，见 "
              "references/conclusions.json）" % len(conc.windowable()))
        return 0

    try:
        if a.q:
            qdef = json.loads(a.q)
        elif a.scale:
            qdef = {"window": {"scale": a.scale, "key": a.key}}
        elif a.days:
            qdef = {"window": {"days": a.days}}
        else:
            qdef = {"window": {"start": a.start, "end": a.end}}
        if a.metrics and a.metrics != "all":
            qdef["metrics"] = a.metrics
        if a.conclusions:
            qdef["conclusions"] = ("all" if a.conclusions == "all"
                                   else [x.strip() for x in a.conclusions.split(",")
                                         if x.strip()])
        if a.no_delta:
            qdef["delta"] = False
        if a.save:
            item = save_view(a.save, qdef, title=a.title or a.save, note=a.note or None)
            print("已保存视图 %s（%s）" % (item["id"], item["title"]))
            print("物化下一版：python3 tools/build_homepage_data.py")
            print("深链：?view=%s" % item["id"])
            return 0
        res = run(qdef)
    except QueryError as e:
        print("✗ %s" % e, file=sys.stderr)
        return 2
    if a.explain:
        q = res["query"]
        w = q["window"]
        span = ("最近 %d 天" % w["days"]) if "days" in w else (
            "%s ~ %s" % (w["start"], w["end"]) if "start" in w else
            "%s %s" % (w["scale"], w["key"]))
        conds = "、".join(_cond_label(c) for c in q.get("conditions") or [])
        print("理解：查 %s 的 %s%s%s" % (
            span, "、".join(q["metrics"]),
            ("，只看 " + conds) if conds else "",
            ("，外加 %d 条定性结论" % len(q["conclusions"])) if q.get("conclusions") else ""))
        if q.get("conclusions"):
            print("结论：%s" % "、".join(q["conclusions"]))
        print("窗口 %s ~ %s · 尺子 %s · 分母 %d"
              % (res["window"]["first"], res["window"]["last"],
                 res["ruler"], res["window"]["expected"]))
        return 0
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
