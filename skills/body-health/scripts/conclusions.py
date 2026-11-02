#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L3.5 窗口结论层：把日级断言按窗口聚合成**定性陈述**。

## 它补的是哪一截

三层各管一段：`units` 回答「这个窗口的数值是多少」，`assertions` 回答「昨天怎么样」，
中间缺的是「这一段的形状是什么」——「久坐是常态还是偶发」「这是持续性欠觉还是
几晚没睡好」。这一层不重算任何日判定，只做两件事：

1. **数**：命中天数 ÷ 可判定天数，外加段结构（几段、最长几段）。
2. **说**：按 `references/conclusions.json` 里的措辞给出定性陈述。

## 三条硬约束

1. **日判定只有一份。** 读 `assertions` 表，不重跑规则；`windows` 层出现第二个
   `if sleep < 7` 就是第二条路径。
2. **分母从 inputs 推导，不从行数推。** 「可判定」＝该断言声明的全部指标当天都有值；
   直接拿断言行数当分母，会把「没测到」算成「没发生」（`assertions` 里曾有 3 处这样）。
3. **门槛不新造。** 发生率分界读 `rules.units.daily_ok_rate`，样本门读
   `rules.query.min_n_selected`，趋势门槛读 `confidence.<metric>.mdc_change` 并按
   √n 收缩——日级门槛搬到窗口级不收缩，真实趋势会被判成「在误差内」。

## 算子

| 算子 | 语义 | 用在哪 |
|---|---|---|
| `rate` | 命中 ÷ 可判定 + 段结构 | 阈值型断言（21 条） |
| `run` | 段数与最长段 | 「连续 N 晚」型（2 条） |
| `integral` | Σ 越过判读线的部分 | 欠账/超出量（挂在结论上） |
| `trend` | 斜率 + mdc/√n | 缓慢漂移（挂在结论上） |

`integral` / `trend` 不单独成 id：它们描述的是同一条结论的另一个时间面。

## 用法

    python3 conclusions.py --list
    python3 conclusions.py --days 30
    python3 conclusions.py --start 2026-10-04 --end 2026-11-02 --ids sleep.duration.short
    python3 conclusions.py --days 30 --json
"""
import argparse
import datetime
import json
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import db          # noqa: E402
import units       # noqa: E402
import metricdefs  # noqa: E402

CONC_PATH = os.path.join(_HERE, "..", "references", "conclusions.json")
LEXICON_PATH = os.path.join(_HERE, "..", "references", "lexicon.json")

STATES = ("成立", "参半", "不成立", "不可判定")


class ContractError(RuntimeError):
    """注册表与代码不一致：未登记的断言 id、缺 operator、缺措辞。"""


# ------------------------------------------------------------------ 注册表
_REG = {}
_METRICS = {}


def _metrics():
    """指标注册表只解析一次：逐日求值时它会被读上千遍，重读文件是纯浪费。"""
    if not _METRICS:
        _METRICS.update(db.load_metrics())
    return _METRICS


def registry(path=None):
    """结论注册表（按路径缓存）。未登记的 id 不是「跳过」，是契约破坏。"""
    path = path or CONC_PATH
    if path not in _REG:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh) or {}
        _REG[path] = data.get("conclusions") or {}
    return _REG[path]


_LEX = {}
_DIM_ORDER = {}


def lexicon(path=None):
    path = path or LEXICON_PATH
    if path not in _LEX:
        with open(path, encoding="utf-8") as fh:
            _LEX[path] = (json.load(fh) or {}).get("id") or {}
    return _LEX[path]


def dimension_order(path=None):
    """词表里维度的顺序就是结论的排列顺序（呈现顺序是数据，不是代码）。"""
    path = path or LEXICON_PATH
    if path not in _DIM_ORDER:
        with open(path, encoding="utf-8") as fh:
            dims = (json.load(fh) or {}).get("dimension") or {}
        _DIM_ORDER[path] = {k: i for i, k in enumerate(dims)}
    return _DIM_ORDER[path]


def spec_of(aid, path=None):
    """一条结论的声明。缺登记 → 报错并指出怎么补。"""
    reg = registry(path)
    if aid not in reg:
        raise ContractError(
            "断言 %s 没有在 references/conclusions.json 登记：它的时间语义没人定义过。"
            "补一条 {kind, operator, inputs, words} 再重建。" % aid)
    return reg[aid]


def kind_of(aid, path=None):
    return spec_of(aid, path).get("kind") or "judge"


def windowable(path=None):
    """可按窗口聚合的 id（kind=judge 且声明了 operator）。"""
    return sorted(k for k, v in registry(path).items()
                  if v.get("kind") == "judge" and v.get("operator"))


def inputs_of(aid, path=None):
    return list(spec_of(aid, path).get("inputs") or [])


# ------------------------------------------------------------------ 小工具
def wilson(k, n, z=1.96):
    """二项比例的 Wilson 区间。**用来声明精度，不用来否决陈述**。

    区间宽在小样本上必然大（7 天窗口的 95% CI 宽约 ±35pp），拿「CI 下界必须
    高过判定线」当门禁，会让「7 天里 6 天没睡够」这种明显事实判成「不确定」。
    """
    if n <= 0:
        return [None, None]
    p = float(k) / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 3), round(min(1.0, c + h), 3)]


def _dates_between(lo, hi):
    d0, d1 = datetime.date.fromisoformat(lo), datetime.date.fromisoformat(hi)
    return [(d0 + datetime.timedelta(days=i)).isoformat()
            for i in range((d1 - d0).days + 1)]


def _episodes(hits):
    """命中日按日历相邻切段，返回 (段数, 最长段长)。"""
    eps, cur = [], []
    for d in sorted(hits):
        dd = datetime.date.fromisoformat(d)
        if cur and (dd - datetime.date.fromisoformat(cur[-1])).days > 1:
            eps.append(cur)
            cur = []
        cur.append(d)
    if cur:
        eps.append(cur)
    return len(eps), (max(len(e) for e in eps) if eps else 0)


def _ref(name):
    """把 'cross.sleep_debt_nights' 解析成 rules 里的数字。缺失即报错。"""
    return name


def _rule_num(rules, dotted):
    cur = rules
    for part in str(dotted).split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise ContractError("rules 里没有 %s（注册表的 min_run_ref 指向了不存在的键）"
                                % dotted)
        cur = cur[part]
    return cur


# ------------------------------------------------------------------ 求值
def _decidable_dates(dates, spec, ctx):
    """可判定＝全部 inputs 当天都有值。

    `prev=true`（与上一读数比较的断言）要求**存在更早的读数**，而不是「昨天有值」：
    日层跨缺口时比的是「上一次有值的那个读数」（`assertions.rebuild` 的 prev_hrv
    就是这么传的）。要求严格相邻会造出一批「有命中但不可判定」的假异常。
    """
    inputs = spec.get("inputs") or []
    if not inputs:
        return []
    dv = ctx["day_vals"]
    first_ok = {}
    for m in inputs:
        ds = [d for d in dv if (dv[d] or {}).get(m) is not None]
        first_ok[m] = min(ds) if ds else None
    need_prev = bool(spec.get("prev"))
    out = []
    for d in dates:
        v = dv.get(d) or {}
        if any(v.get(m) is None for m in inputs):
            continue
        if need_prev and any(first_ok.get(m) is None or d <= first_ok[m] for m in inputs):
            continue
        out.append(d)
    return out


def _rows_for(con, aid, lo, hi):
    return {r["date"]: r for r in con.execute(
        "SELECT date, verdict, level, dimension FROM assertions "
        "WHERE id=? AND scale='day' AND date>=? AND date<=?", (aid, lo, hi))}


def _integral(entry, dates, ctx, rules):
    """Σ 越过判读线的部分。**用逐日判读线**（睡眠需求逐日变），不用窗口均值线。

    截断日不参与（与 units 同一条纪律：0.11 覆盖的那天算一整天会把账算歪）。
    """
    cfg = (entry or {}).get("integral")
    if not cfg:
        return None
    m = cfg.get("metric")
    mdef = _metrics().get(m) or {}
    dv, cov = ctx["day_vals"], ctx["cov"]
    total, n, skipped = 0.0, 0, 0
    for d in dates:
        v = (dv.get(d) or {}).get(m)
        if v is None:
            continue
        c = cov.get((m, d))
        if c is not None and c < ctx["day_cov_min"]:
            skipped += 1
            continue
        refs = {k: (dv.get(d) or {}).get(k) for k in ctx["ref_names"].get(m, [])}
        try:
            th = metricdefs.judge_of(m, rules, refs)
        except Exception:
            th = metricdefs.judge_of(m, rules)
        if th is None:
            continue
        if mdef.get("dir") == "below":
            total += max(0.0, v - th)       # 超过上限的部分（久坐）
        elif mdef.get("dir") == "above":
            total += max(0.0, th - v)       # 不足下限的部分（睡眠）
        n += 1
    return {"metric": m, "label": cfg.get("label") or mdef.get("label"),
            "total": round(total, 2), "unit": mdef.get("unit"),
            "n": n, "skipped_truncated": skipped}


def _trend(entry, dates, ctx, rules, gappy=False):
    """斜率 + 噪声门槛。窗口级门槛＝mdc_change ÷ √n。

    为什么必须收缩：mdc_change 是**两次单次读数**之间的门槛（≈2×日间 SD）。
    n 天均值之间的门槛按 √n 收缩。HRV 的 20ms 搬到 19 天均值上仍是 20ms 的话，
    一个 6.7ms 的真实下行会被判成「在误差内」——门槛把结论吃掉了。
    """
    cfg = (entry or {}).get("trend")
    if not cfg:
        return None
    m = cfg.get("metric")
    dv, cov = ctx["day_vals"], ctx["cov"]
    pts = []
    for d in dates:
        v = (dv.get(d) or {}).get(m)
        if v is None:
            continue
        c = cov.get((m, d))
        if c is not None and c < ctx["day_cov_min"]:
            continue
        pts.append(float(v))
    conf = db.metric_conf(rules, m)
    mdc = conf.get("mdc_change")
    tier = (conf.get("tier") or {}).get("change")
    u = rules.get("units") or {}
    min_points = int(u.get("slope_min_points", 6))
    rule = ((rules.get("confidence") or {}).get("rule") or {})
    min_days = int(rule.get("trend_min_days", 7))
    base = {"metric": m, "label": cfg.get("label") or m, "n": len(pts),
            "mdc_change": mdc, "slope": None, "total": None,
            "threshold": None, "direction": None, "state": None, "reason": None,
            "basis": "selected_days" if gappy else "calendar_days"}
    if tier in (u.get("tier_suppress_change") or []):
        base["state"] = "未标定"
        base["reason"] = "该指标 tier.change=%s：累计量类的环比/趋势门槛无效，只看多月斜率" % tier
        return base
    if mdc is None:
        base["state"] = "未标定"
        base["reason"] = "rules.confidence 没有登记 %s 的 mdc_change" % m
        return base
    if len(pts) < max(min_points, min_days):
        base["state"] = "样本不足"
        base["reason"] = "实测 %d 天，需要 ≥ %d 天" % (len(pts), max(min_points, min_days))
        return base
    s = units.slope(pts, rules=rules)
    if s is None:
        base["state"] = "样本不足"
        base["reason"] = "斜率为空（点不够或全等）"
        return base
    thr = mdc / math.sqrt(len(pts))
    total = s * (len(pts) - 1)
    # 方向一致性：最近 trend_min_days 天的斜率符号要与整体同向
    tail = units.slope(pts[-min_days:], rules=rules)
    agree = tail is not None and (tail == 0 or s == 0 or (tail > 0) == (s > 0))
    base.update({"slope": round(s, 3), "total": round(total, 1),
                 "threshold": round(thr, 2),
                 "direction": "上行" if total > 0 else "下行"})
    if abs(total) <= thr:
        base["state"] = "噪声内"
        base["reason"] = "|%.1f| ≤ 门槛 %.1f（mdc %s ÷ √%d）" % (total, thr, mdc, len(pts))
    elif not agree:
        base["state"] = "方向不一致"
        base["reason"] = "近 %d 天斜率与整体不同向，趋势未确认" % min_days
    else:
        base["state"] = "可判"
    return base


def evaluate_window(con, spec, ids=None, rules=None, ctx=None, min_n=None,
                    cond_metrics=None):
    """一个窗口 × 若干结论 → 结论行。**不做日判定**，只读 assertions 与 obs。"""
    rules = rules or db.load_rules()
    dates = list(spec["dates"])
    if not dates:
        return []
    ctx = ctx or units.context(con, rules, _metrics())
    if ctx is None:
        raise ContractError("obs 表为空：先跑 index.py build")
    u = rules.get("units") or {}
    ok_rate = float(u.get("daily_ok_rate", 0.6))
    min_n = int(min_n if min_n is not None else
                (rules.get("query") or {}).get("min_n_selected") or 0)
    lex = lexicon()
    lo, hi = dates[0], dates[-1]
    date_set = set(dates)
    # 一周里有洞 = 条件是筛选过的（或窗口边缘截断），斜率按「窗口内实际比到的日子」计
    gappy = len(dates) != (datetime.date.fromisoformat(hi)
                           - datetime.date.fromisoformat(lo)).days + 1
    out = []
    for aid in (ids or windowable()):
        entry = spec_of(aid)
        op = entry.get("operator")
        if entry.get("kind") != "judge" or not op:
            raise ContractError("%s 的 kind=%s / operator=%s：只有 kind=judge 且声明了 "
                                "operator 的结论能进窗口层" % (aid, entry.get("kind"), op))
        dec = _decidable_dates(dates, entry, ctx)
        dec_set = set(dec)
        rows = _rows_for(con, aid, lo, hi)
        # 窗口＝给定的日期集合。条件筛选后它是一类日子（有洞），不是一段连续时间；
        # 只认 dates 里的行，否则窗口外的命中会被误报成「命中却不可判定」。
        rows = {d: r for d, r in rows.items() if d in date_set}
        hits = [d for d in dec if (rows.get(d) or {})["verdict"]]
        anomaly = sorted(d for d, r in rows.items() if r["verdict"] and d not in dec_set)
        n_ep, longest = _episodes(hits)
        value = round(len(hits) / float(len(dec)), 3) if dec else None
        row = {
            "id": aid, "label": lex.get(aid), "operator": op,
            "dimension": next((r["dimension"] for r in rows.values() if r["dimension"]), None),
            "window": {"start": spec.get("start", lo), "end": spec.get("end", hi),
                       "days": len(dates)},
            "hit": len(hits), "decidable": len(dec), "undecidable": len(dates) - len(dec),
            "value": value, "ci": wilson(len(hits), len(dec)),
            "episodes": n_ep, "longest_run": longest,
            "ruler": spec.get("ruler") or spec.get("scale"),
            "min_n": min_n, "anomaly": anomaly,
            "state": None, "word": None, "reason": None,
            "integral": None, "trend": None,
        }
        # ── 状态：先过样本门，再按算子给结论。措辞只在状态确定后取 ──
        words = entry.get("words") or {}
        overlap = sorted(set(cond_metrics or ()) & set(entry.get("inputs") or []))
        if overlap:
            # 用结论的输入去筛日子，再在这批日子上算结论，是循环：筛「睡眠<6.5h 的日子」
            # 之后算「睡眠不足发生率」，必然 100%。数字没错，但它什么都没说。
            row.update(state="不可判定", word=None,
                       reason="筛选条件用到 %s，这条结论被条件决定（循环），不给措辞"
                              % "、".join(overlap))
        elif not dec:
            row.update(state="不可判定", reason="窗口内没有可判定的日子（inputs 全缺）")
        elif len(dec) < min_n:
            row.update(state="不可判定",
                       reason="可判定 %d 天 < 门槛 %d；比例仍给出，但不给措辞" % (len(dec), min_n))
        elif op == "run":
            need = int(_rule_num(rules, entry.get("min_run_ref")))
            row["state"] = "成立" if longest >= need else "不成立"
            row["reason"] = "最长连续 %d 天（门槛 %d 天）" % (longest, need)
        else:
            if value >= ok_rate:
                row["state"] = "成立"
            elif value <= 1 - ok_rate:
                row["state"] = "不成立"
            else:
                row["state"] = "参半"
            row["reason"] = "发生率 %.0f%%（分界 %.0f%% / %.0f%%，n=%d）" % (
                value * 100, ok_rate * 100, (1 - ok_rate) * 100, len(dec))
        row["word"] = None if overlap else words.get(row["state"])
        row["integral"] = _integral(entry, dates, ctx, rules)
        row["trend"] = _trend(entry, dates, ctx, rules, gappy=gappy)
        out.append(row)
    order = dimension_order()
    out.sort(key=lambda r: (order.get(r["dimension"], 99), r["id"]))
    return out


# ------------------------------------------------------------------ 展示
def _fmt_unit(v, unit):
    if v is None:
        return "—"
    if unit == "min":
        return "%.1f h" % (v / 60.0)
    if unit == "h":
        return "%.1f h" % v
    return ("%g" % v)


def render(rows, spec=None):
    if not rows:
        return "没有可展示的结论"
    w = rows[0]["window"]
    head = "窗口 %s ~ %s（%d 天）· 尺子 %s · 最小可判定 %d 天" % (
        w["start"], w["end"], w["days"], rows[0]["ruler"] or "-", rows[0]["min_n"])
    out = [head, "",
           "%-30s %-6s %9s %6s %6s %s" % ("结论", "状态", "命中/可判", "段数", "最长", "陈述")]
    for r in rows:
        out.append("%-30s %-6s %9s %6d %6d %s" % (
            (r["label"] or r["id"])[:30], r["state"],
            "%d/%d" % (r["hit"], r["decidable"]),
            r["episodes"], r["longest_run"], r["word"] or "（不给措辞）"))
        if r["reason"]:
            out.append("%-30s %s" % ("", "· " + r["reason"]))
        if r["integral"] and r["integral"]["total"]:
            out.append("%-30s %s" % ("", "· %s 累计 %s（%d 天）" % (
                r["integral"]["label"], _fmt_unit(r["integral"]["total"],
                                                  r["integral"]["unit"]),
                r["integral"]["n"])))
        t = r["trend"]
        if t and t.get("slope") is not None:
            out.append("%-30s %s" % ("", "· %s %s %.2f/天，累计 %+.1f，%s（%s）" % (
                t["label"], t["direction"], t["slope"], t["total"], t["state"],
                t["reason"] or "超过门槛")))
        elif t:
            out.append("%-30s %s" % ("", "· %s：%s（%s）" % (t["label"], t["state"],
                                                          t["reason"])))
        if r["anomaly"]:
            out.append("%-30s %s" % ("", "· ⚠ %d 天有命中但不可判定：%s"
                                     % (len(r["anomaly"]), "、".join(r["anomaly"][:3]))))
    return "\n".join(out)


def _spec_from_args(a, con, rules):
    ctx = units.context(con, rules, _metrics())
    if a.days:
        end = min(ctx["last"], ctx["today"])
        start = (datetime.date.fromisoformat(end)
                 - datetime.timedelta(days=a.days - 1)).isoformat()
    else:
        start, end = a.start, a.end
    if not start or not end:
        raise SystemExit("要给 --days，或 --start 与 --end")
    return units.window_spec(start, end, ctx, rules), ctx


def main():
    ap = argparse.ArgumentParser(description="窗口结论：把日级断言聚合成定性陈述")
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--days", type=int, default=0)
    ap.add_argument("--ids", default="", help="逗号分隔；省略＝全部可窗口化结论")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list", action="store_true", help="列出注册表")
    a = ap.parse_args()

    if a.list:
        lex = lexicon()
        print("%-34s %-12s %-9s %-22s %s" % ("id", "kind", "operator", "inputs", "中文名"))
        for aid, e in sorted(registry().items()):
            print("%-34s %-12s %-9s %-22s %s" % (
                aid, e.get("kind"), e.get("operator") or "-",
                ",".join(e.get("inputs") or []) or "-", lex.get(aid) or "（缺词表）"))
        print("\n可窗口化 %d 条 / 全部 %d 条" % (len(windowable()), len(registry())))
        return 0

    con = db.connect()
    rules = db.load_rules()
    spec, ctx = _spec_from_args(a, con, rules)
    ids = [x.strip() for x in a.ids.split(",") if x.strip()] or None
    rows = evaluate_window(con, spec, ids=ids, rules=rules, ctx=ctx)
    if a.json:
        print(json.dumps({"window": {k: spec.get(k) for k in ("start", "end", "key")},
                          "rows": rows}, ensure_ascii=False, indent=1))
    else:
        print(render(rows, spec))
    return 0


if __name__ == "__main__":
    sys.exit(main())
