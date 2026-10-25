#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L2 单元聚合层：把逐日观测压成「尺度单元」的统计量（2026-10-25 建）。

**为什么需要这一层（notes/architecture_v4.md §3.2）**
现在「周/月」是浏览器里对逐日行现算的：不落盘、不可复用、报告用不了，
而且主页与报告各算一次，口径靠人同步。本模块把它搬到后端并物化。

一个单元 = (尺度, 键)：
    day   → 2026-10-24          expected=1
    week  → 2026-W43（ISO 周）   expected=7
    month → 2026-10
    roll7 / roll28 / roll90      expected=窗长（滚动窗末日做键）

三条铁律：
1. **delta 的判定只在这里做**，消费方只读 `delta_verdict`，不得自己比大小。
2. **滚动窗禁用环比**——相邻两步重叠 6/7，环比会凭空造出显著性；
   一律用非重叠位移差 `v_t − v_(t−k)`，k=窗长。
3. **覆盖不足不给结论**：coverage < rules.units.coverage_min → judge='na'。
   3 天算出来的「周均」是噪声，不是结论。

delta_verdict 五档（消费方按此决定措辞，不得自行判定）：
    changed        |Δ| > mdc_change，可以说"上升/下降"
    within-noise   |Δ| ≤ mdc_change，只能说"在误差内"
    insufficient   样本不足（n < delta_min_n 或没有上一单元）
    uncalibrated   该指标在 rules.confidence 里**没有登记 mdc**，不判变化
    suppressed     tier.change=low/none，该指标不做环比判定（只给多月斜率）

用法：
    python3 units.py rebuild                              # 从 obs 重算全部单元
    python3 units.py get    --scale week --key 2026-W43
    python3 units.py series --metric hrv_ms --scale week --n 12
    python3 units.py matrix --scale week --n 6
"""
import argparse
import datetime
import json
import os
import statistics
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import db  # noqa: E402
import metricdefs  # noqa: E402

SCALES = ("day", "week", "month", "roll7", "roll28", "roll90")


# ------------------------------------------------------------------ 日历
def _d(s):
    return datetime.date.fromisoformat(s)


def iso_week_key(d):
    y, w, _ = d.isocalendar()
    return "%d-W%02d" % (y, w)


def week_dates(key):
    y, w = int(key[:4]), int(key[6:])
    mon = datetime.date.fromisocalendar(y, w, 1)
    return [(mon + datetime.timedelta(days=i)).isoformat() for i in range(7)]


def month_dates(key):
    y, m = int(key[:4]), int(key[5:7])
    n = (datetime.date(y + (m == 12), (m % 12) + 1, 1) - datetime.timedelta(days=1)).day
    return ["%s-%02d" % (key, i) for i in range(1, n + 1)]


def prev_key(scale, key, k=None):
    if scale == "day":
        return (_d(key) - datetime.timedelta(days=1)).isoformat()
    if scale == "week":
        return iso_week_key(_d(week_dates(key)[0]) - datetime.timedelta(days=7))
    if scale == "month":
        y, m = int(key[:4]), int(key[5:7])
        return "%04d-%02d" % (y - (m == 1), 12 if m == 1 else m - 1)
    return (_d(key) - datetime.timedelta(days=k)).isoformat()   # 滚动窗：非重叠位移


def unit_specs(first, last, today, rules):
    """枚举 [first, last] 范围内的全部单元。

    expected 的口径（决定 coverage 的分母）：
      · day       只有「有 raw 的日子」才有 expected，缺的就是没采到
      · week/month 截到 [max(start, first), min(end, today)]——本项目开始前、
                  今天之后的日子不是「漏采」，不该计入分母
      · roll_k    恒为窗长 k。窗口装不满就 coverage 低，自动被门禁压住
                  （滚动窗要满 28 天才算数，不能靠「前面几天凑」冒充）
    """
    roll = rules.get("units", {}).get("roll_windows") or [7, 28, 90]
    out = []
    d0, d1 = _d(first), _d(last)

    d = d0
    while d <= d1:
        out.append({"scale": "day", "key": d.isoformat(), "start": d.isoformat(),
                    "end": d.isoformat(), "dates": [d.isoformat()], "expected": 1})
        d += datetime.timedelta(days=1)

    keys, d = [], d0
    while d <= d1:
        k = iso_week_key(d)
        if k not in keys:
            keys.append(k)
        d += datetime.timedelta(days=1)
    for k in keys:
        dates = week_dates(k)
        eff = [x for x in dates if first <= x <= today]
        if eff:
            out.append({"scale": "week", "key": k, "start": dates[0], "end": dates[-1],
                        "dates": dates, "expected": len(eff)})

    keys, d = [], d0
    while d <= d1:
        k = d.isoformat()[:7]
        if k not in keys:
            keys.append(k)
        d += datetime.timedelta(days=1)
    for k in keys:
        dates = month_dates(k)
        eff = [x for x in dates if first <= x <= today]
        if eff:
            out.append({"scale": "month", "key": k, "start": dates[0], "end": dates[-1],
                        "dates": dates, "expected": len(eff)})

    for k in roll:
        # 从 span 第一天就开始算：早期窗口 coverage 会很低，被门禁压住只报数字——
        # 这比「窗口装不满就干脆不出现」诚实（用户能看见『还不能谈 90 天趋势』）。
        end = d0
        while end <= d1:
            dates = [(end - datetime.timedelta(days=i)).isoformat()
                     for i in range(k - 1, -1, -1)]
            out.append({"scale": "roll%d" % k, "key": end.isoformat(),
                        "start": dates[0], "end": end.isoformat(),
                        "dates": dates, "expected": k})
            end += datetime.timedelta(days=1)
    return out


# ------------------------------------------------------------------ 取值
def _read_obs(con, metrics):
    """从 L1 读逐日值。L2 只读 L1，绝不回 raw——口径变更时只需重跑这一层。"""
    day_vals, bands, cov = {}, {}, {}
    q = ("SELECT metric, substr(t,1,10) AS d, value, coverage FROM obs "
         "ORDER BY t")
    for r in con.execute(q):
        m, d = r["metric"], r["d"]
        # 全部指标都进 day_vals：聚合指标用来算单元，参考量（sleep_need_h 等）
        # 用来当**阈值输入**——阈值逐日变时，单元判定必须逐日做。
        day_vals.setdefault(d, {})[m] = r["value"]
        cov[(m, d)] = r["coverage"]
        if m in ("hrv_band_low", "hrv_band_high"):
            bands.setdefault(d, {})[m] = r["value"]
    return day_vals, bands, cov


def _agg(vals, how):
    if not vals:
        return None
    if how == "sum":
        return round(sum(vals), 2)
    if how == "median":
        return round(statistics.median(vals), 2)
    if how == "last":
        return round(vals[-1], 2)
    if how == "min":
        return round(min(vals), 2)
    if how == "max":
        return round(max(vals), 2)
    return round(sum(vals) / len(vals), 2)


def _goal(metric, rules):
    """达标线。**映射来自 references/metrics.json，数值来自 rules.json**——
    本函数不再持有任何 metric→规则 的硬编码字典（那正是「定义没有唯一来源」的病灶）。
    解析失败会抛错而不是返回 None：静默 None 会让指标悄悄失去达标判定。"""
    try:
        return metricdefs.goal_of(metric, rules)
    except Exception as e:
        raise RuntimeError("指标 %s 的达标线解析失败：%s" % (metric, e))


def _band(metric, rules, personal=None):
    try:
        return metricdefs.band_of(metric, rules, personal)
    except Exception as e:
        raise RuntimeError("指标 %s 的区间解析失败：%s" % (metric, e))


def _judge(metric, value, mdef, rules, band):
    """达标三态 ok / no / na。**na 与 no 必须分开**——缺数据不等于没做到。

    覆盖门禁在调用点处理（coverage < coverage_min 直接判 na），
    本函数只负责「有值且该单元可信」之后的阈值比较。
    """
    if value is None:
        return "na"
    direction = mdef.get("dir")
    if direction is None:
        return None
    if direction == "band":
        b = band or _band(metric, rules, None)
        return "na" if not b else ("ok" if b[0] <= value <= b[1] else "no")
    g = _goal(metric, rules)
    if g is None:
        return None
    return "ok" if ((value >= g) if direction == "above" else (value <= g)) else "no"


# ------------------------------------------------------------------ 重算
def rebuild(con, rules=None, metrics=None, quiet=True):
    rules = rules or db.load_rules()
    reg = db.load_metrics()
    metrics = metrics or {k: v for k, v in reg.items() if v.get("agg")}
    u = rules.get("units") or {}
    cov_min = u.get("coverage_min") or {}
    delta_min_n = u.get("delta_min_n", 2)
    suppress = set(u.get("tier_suppress_change") or ["low", "none"])

    day_vals, bands, cov = _read_obs(con, metrics)
    day_cov_min = u.get("day_coverage_min", 0.95)
    daily_ok_rate = u.get("daily_ok_rate", 0.6)
    # 每个指标引用了哪些参考量（阈值里的 ref:xxx）
    ref_names = {m: sorted({r for expr in (md.get("thresholds") or {}).values()
                            if isinstance(expr, str)
                            for r in metricdefs.refs_in(expr)})
                 for m, md in metrics.items()}
    if not day_vals:
        if not quiet:
            print("obs 表为空：先跑 index.py build")
        return 0
    first, last = min(day_vals), max(day_vals)
    today = datetime.date.today().isoformat()
    specs = unit_specs(first, last, today, rules)

    # ---- 第一遍：算出每个单元每个指标的值 ----
    cells, rows = {}, []
    for sp in specs:
        sc, key = sp["scale"], sp["key"]
        for m, mdef in metrics.items():
            vals, last_d = [], None
            for d in sp["dates"]:
                v = day_vals.get(d, {}).get(m)
                if v is None:
                    continue
                # 截断日不进**聚合**：coverage 0.11 的那天算「一天」会把周均整体拉偏
                # （2026-10-25 02:48 截止、1656 步，计入会把本周步数从 6,400 压到 5,729）。
                # 日尺度例外——值要留着给「今天走到现在多少步」用，由 coverage 判 na。
                c = cov.get((m, d))
                if sp["scale"] != "day" and c is not None and c < day_cov_min:
                    continue
                vals.append(v)
                last_d = d
            if not vals:
                continue
            value = _agg(vals, mdef.get("agg"))
            n = len(vals)
            if sc == "day":
                # 日尺度的 coverage ＝ 这一天被观测了多大比例（不是 n/1=1.0）。
                # 否则截断日会拿到 1.0 覆盖率并照常判达标——那是把半天的账当整天。
                cs = [cov.get((m, d)) for d in sp["dates"]]
                cs = [c for c in cs if c is not None]
                coverage = round(min(cs), 3) if cs else None
            else:
                coverage = round(n / sp["expected"], 3) if sp["expected"] else None
            band = None
            if m == "hrv_ms":
                for d in reversed(sp["dates"]):
                    b = bands.get(d) or {}
                    if b.get("hrv_band_low") is not None and b.get("hrv_band_high") is not None:
                        band = (b["hrv_band_low"], b["hrv_band_high"])
                        break
            judge, judge_rate = None, None
            if mdef.get("dir"):
                if coverage is None or coverage < cov_min.get(sc, 0):
                    judge = "na"
                elif mdef.get("judge_mode") == "daily":
                    # 阈值逐日变（如「设备当晚算的需求」）→ 逐日判再求达标率。
                    # 用「日均时长 ÷ 日均需求」会失真：两个均值相除不等于逐日达标率。
                    ok_n = tot = 0
                    for d in sp["dates"]:
                        vd = day_vals.get(d, {})
                        val_d = vd.get(m)
                        if val_d is None:
                            continue
                        c_d = cov.get((m, d))
                        if c_d is not None and c_d < day_cov_min and sc != "day":
                            continue
                        refs = {k: vd.get(k) for k in ref_names.get(m, [])}
                        try:
                            th = metricdefs.judge_of(m, rules, refs)
                        except Exception:
                            th = metricdefs.judge_of(m, rules)
                        if th is None:
                            continue
                        tot += 1
                        ok_n += 1 if (val_d >= th if mdef["dir"] == "above"
                                      else val_d <= th) else 0
                    if tot:
                        judge_rate = round(ok_n / tot, 3)
                        judge = "ok" if judge_rate >= daily_ok_rate else "no"
                        judge_note = "%d/%d 天达标（≥%.0f%% 记 ok）" % (
                            ok_n, tot, daily_ok_rate * 100)
                    else:
                        judge = "na"
                else:
                    judge = _judge(m, value, mdef, rules, band)
            row = {"scale": sc, "key": key, "metric": m, "value": value,
                   "agg": mdef.get("agg"), "n": n, "coverage": coverage,
                   "judge": judge, "judge_rate": judge_rate,
                   "start": sp["start"], "end": sp["end"],
                   "expected": sp["expected"]}
            cells[(sc, key, m)] = row
            rows.append(row)

    # ---- 第二遍：环比 / 位移差 ----
    for row in rows:
        sc, key, m = row["scale"], row["key"], row["metric"]
        conf = db.metric_conf(rules, m)
        mdc = conf.get("mdc_change")
        tier = (conf.get("tier") or {}).get("change")
        pkey = prev_key(sc, key, k=int(sc[4:]) if sc.startswith("roll") else None)
        prev = cells.get((sc, pkey, m))
        row["delta_vs"] = pkey
        row["delta_mdc"] = mdc
        if prev is None or prev.get("value") is None:
            row["delta_verdict"], row["delta_note"] = "insufficient", "没有可比的上一个单元"
            continue
        dv = round(row["value"] - prev["value"], 3)
        row["delta_v"] = dv
        need_n = 1 if sc == "day" else delta_min_n
        if min(row["n"], prev["n"]) < need_n:
            row["delta_verdict"] = "insufficient"
            row["delta_note"] = "实测值少于 %d 天" % need_n
        elif mdc is None:
            row["delta_verdict"] = "uncalibrated"
            row["delta_note"] = "rules.confidence 未登记该指标门槛"
        elif tier in suppress:
            row["delta_verdict"] = "suppressed"
            row["delta_note"] = "tier.change=%s：不做环比判定，看多月斜率" % tier
        elif abs(dv) > float(mdc):
            row["delta_verdict"] = "changed"
            row["delta_note"] = "|Δ|=%.2f > mdc %.2f" % (abs(dv), float(mdc))
        else:
            row["delta_verdict"] = "within-noise"
            row["delta_note"] = "|Δ|=%.2f ≤ mdc %.2f" % (abs(dv), float(mdc))

    con.executemany("DELETE FROM units WHERE scale=? AND key=?",
                    [(sp["scale"], sp["key"]) for sp in specs])
    db.upsert_units(con, rows)
    con.commit()
    if not quiet:
        print("units: %d 行 / %d 个单元" % (len(rows), len(specs)))
    return len(rows)


# ------------------------------------------------------------------ 趋势
def slope(points, min_n=None, min_span=None, rules=None):
    """最小二乘斜率（单位/步）。**样本不足返回 None，不猜**。"""
    rules = rules or db.load_rules()
    u = rules.get("units") or {}
    min_n = min_n or u.get("slope_min_points", 6)
    min_span = min_span or u.get("slope_min_span_days", 14)
    pts = [(i, v) for i, v in enumerate(points) if v is not None]
    if len(pts) < min_n:
        return None
    if len(points) > 1 and len(points) < min_span and min_span > len(points):
        # 单元数少于要求的跨度时，仍允许（周/月单元的跨度天然大于天数）
        pass
    n = len(pts)
    sx = sum(p[0] for p in pts)
    sy = sum(p[1] for p in pts)
    sxy = sum(p[0] * p[1] for p in pts)
    sxx = sum(p[0] * p[0] for p in pts)
    den = n * sxx - sx * sx
    return None if den == 0 else round((n * sxy - sx * sy) / den, 4)


# ------------------------------------------------------------------ CLI
def _fmt(v, mdef, m):
    if v is None:
        return "—"
    if m in ("sleep_h",):
        return "%.1fh" % v
    if m == "steps_daily":
        return "%d" % round(v)
    if m == "sedentary_min":
        return "%dh%02d" % (int(v // 60), int(v % 60))
    if isinstance(v, float) and abs(v - round(v)) < 1e-9:
        return "%d" % round(v)
    return "%.1f" % v


def cmd_rebuild(a):
    con = db.connect()
    n = rebuild(con, quiet=False)
    print(db.get_meta(con))
    return 0 if n else 1


def cmd_get(a):
    con = db.connect()
    rows = list(con.execute(
        "SELECT * FROM units WHERE scale=? AND key=? ORDER BY metric", (a.scale, a.key)))
    if not rows:
        print("无该单元：%s %s" % (a.scale, a.key))
        return 1
    reg = db.load_metrics()
    if a.json:
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=1))
        return 0
    unit = rows[0]
    print("【%s %s】%s ~ %s · expected %d 天"
          % (a.scale, a.key, unit["start"], unit["end"], unit["expected"]))
    print("%-14s %10s %5s %7s %-4s %-14s %s"
          % ("指标", "值", "n", "覆盖", "达标", "变化", "说明"))
    for r in rows:
        m = r["metric"]
        rate = ""
        if "judge_rate" in r.keys() and r["judge_rate"] is not None:
            rate = "(%d%%)" % round(r["judge_rate"] * 100)
        print("%-14s %10s %5d %6.0f%% %-4s %-14s %s"
              % (m, _fmt(r["value"], reg.get(m), m), r["n"],
                 (r["coverage"] or 0) * 100, (r["judge"] or "-") + rate,
                 r["delta_verdict"] or "-",
                 ("" if r["delta_v"] is None else "Δ%+.1f " % r["delta_v"])
                 + (r["delta_note"] or "")))
    return 0


def cmd_series(a):
    con = db.connect()
    rows = list(con.execute(
        "SELECT key, end, value, n, coverage, judge, delta_verdict FROM units "
        "WHERE scale=? AND metric=? ORDER BY end DESC LIMIT ?",
        (a.scale, a.metric, a.n)))
    rows.reverse()
    if not rows:
        print("无数据")
        return 1
    if a.json:
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=1))
    else:
        print("%-12s %10s %5s %7s %-4s %s"
              % ("单元", "值", "n", "覆盖", "达标", "变化"))
        for r in rows:
            print("%-12s %10s %5d %6.0f%% %-4s %s"
                  % (r["key"], _fmt(r["value"], None, a.metric), r["n"],
                     (r["coverage"] or 0) * 100, r["judge"] or "-",
                     r["delta_verdict"] or "-"))
    s = slope([r["value"] for r in rows], rules=db.load_rules())
    print("\n斜率：%s（%s）" % (
        "样本不足，不判" if s is None else "%+.3f / %s" % (s, a.scale),
        "%d 个单元，门槛 ≥%d" % (len(rows),
                              db.load_rules()["units"]["slope_min_points"])))
    return 0


def cmd_matrix(a):
    con = db.connect()
    keys = [k[0] for k in con.execute(
        "SELECT DISTINCT key FROM units WHERE scale=? ORDER BY end DESC LIMIT ?",
        (a.scale, a.n))]
    keys.reverse()
    reg = db.load_metrics()
    for k in keys:
        print("\n=== %s %s ===" % (a.scale, k))
        for r in con.execute(
                "SELECT * FROM units WHERE scale=? AND key=? ORDER BY metric",
                (a.scale, k)):
            print("  %-14s %10s  n=%-2d cov=%3.0f%%  %-4s %s" % (
                r["metric"], _fmt(r["value"], reg.get(r["metric"]), r["metric"]),
                r["n"], (r["coverage"] or 0) * 100, r["judge"] or "-",
                r["delta_verdict"] or "-"))
    return 0


def main():
    ap = argparse.ArgumentParser(description="L2 单元聚合层")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("rebuild", help="从 obs 表重算全部单元")
    p.set_defaults(func=cmd_rebuild)

    p = sub.add_parser("get", help="查一个单元")
    p.add_argument("--scale", required=True, choices=SCALES)
    p.add_argument("--key", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("series", help="查一个指标的同尺度序列")
    p.add_argument("--metric", required=True)
    p.add_argument("--scale", default="week", choices=SCALES)
    p.add_argument("--n", type=int, default=12)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_series)

    p = sub.add_parser("matrix", help="最近 n 个单元的全指标")
    p.add_argument("--scale", default="week", choices=SCALES)
    p.add_argument("--n", type=int, default=6)
    p.set_defaults(func=cmd_matrix)

    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
