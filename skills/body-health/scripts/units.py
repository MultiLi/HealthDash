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
import fragments  # noqa: E402

#: 片段尺度由 fragments 模块定义（hour / daypart），只有出值权、没有判定权。
SCALES = ("day", "week", "month", "roll7", "roll28", "roll90") + fragments.FRAGMENT_SCALES


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


# ------------------------------------------------------------------ 任意窗口
def ruler_for(n_days, rules=None):
    """任意窗口**借哪把已标定的尺子**。

    覆盖门禁（`coverage_min`）是按尺度标定出来的：日 1.0、周 0.57、月 0.5、
    滚动 0.7。任意窗口没有标定过，现编一个门槛就等于「为了让结论过线而调门槛」。
    所以这里只**借用**长度最接近的已标定尺度，并把它记进结果（`ruler`），
    让「这个判定用的是哪把尺子」始终可回答——与 metricdefs 的 must_report_ruler 同一条纪律。
    """
    rules = rules or db.load_rules()
    cov = (rules.get("units") or {}).get("coverage_min") or {}
    n = int(n_days)
    if n <= 1:
        return "day"
    if n <= 7:
        return "week"
    if n <= 31:
        return "month"
    if n <= 60:
        return "roll28"
    return "roll90" if "roll90" in cov else "roll28"


def window_spec(start, end, ctx, rules=None, dates=None, key=None):
    """任意时间窗 → 与 unit_specs 同形状的 spec（`scale="window"`）。

    **只按有数据的日子算分母**：窗口覆盖到数据范围之外的部分不是「漏采」
    （项目开始前、今天之后的日子都不该计入 expected）。这条与 week/month 的
    截断规则一致——否则查「过去一年」会得到一个永远很低的覆盖率。
    `dates` 可显式给出（条件筛选后的日子），此时 expected 就是筛选后的天数。
    """
    first, last = ctx["first"], min(ctx["last"], ctx["today"])
    if dates is None:
        d0 = max(datetime.date.fromisoformat(start), datetime.date.fromisoformat(first))
        d1 = min(datetime.date.fromisoformat(end), datetime.date.fromisoformat(last))
        dates = [] if d1 < d0 else [(d0 + datetime.timedelta(days=i)).isoformat()
                                    for i in range((d1 - d0).days + 1)]
    else:
        dates = [d for d in dates if first <= d <= last]
    return {"scale": "window", "key": key or ("%s~%s" % (start, end)),
            "start": start, "end": end, "dates": dates,
            "expected": len(dates), "ruler": ruler_for(len(dates), rules)}


# ------------------------------------------------------------------ 取值
def _read_obs(con, metrics):
    """从 L1 读逐日值。L2 只读 L1，绝不回 raw——口径变更时只需重跑这一层。

    **片段观测（metric 前缀 hour_）在这里被排除**：本函数产出的是「日值」，
    而片段是按小时的多行；混进来会被「后写覆盖先写」静默取到 23 点那一条。
    片段由 `_frag_units()` 走 hour / daypart 两个尺度，两条路互不交叉。
    """
    day_vals, bands, cov = {}, {}, {}
    q = ("SELECT metric, substr(t,1,10) AS d, value, coverage FROM obs "
         "WHERE metric NOT LIKE '%s%%' ORDER BY t" % fragments.PREFIX)
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
    # 档位按**判读线**（judge），不是展示目标（goal）。2026-10-25 之前这里用 goal，
    # 是全仓唯一一处「展示口径覆盖判读口径」的地方：步数 goal=10000、judge=8000，
    # 于是 8982 步那天主页判「未达标」而报告根本不提——同一份数据两种说法。
    # 两者只对步数不同（其余指标的 judge 未单独声明，judge_of 会自动退回 goal）。
    g = metricdefs.judge_of(metric, rules)
    if g is None:
        return None
    return "ok" if ((value >= g) if direction == "above" else (value <= g)) else "no"


# ------------------------------------------------------------------ 片段单元
def frag_metrics(reg=None):
    """只在片段尺度出现的指标（registry 里 scales 全落在 hour/daypart）。"""
    reg = reg if reg is not None else db.load_metrics()
    out = {}
    for k, v in reg.items():
        sc = v.get("scales") or []
        if sc and set(sc) <= set(fragments.FRAGMENT_SCALES):
            out[k] = v
    return out


def _frag_units(con, reg):
    """hour / daypart 两个尺度。**只出值**：judge 与 delta_verdict 恒为 None。

    小时单元 = 一条片段观测（片段已是该小时的聚合，不再二次平均）；
    日分区单元 = 该窗口内小时的确定性聚合（sum 类求和、mean 类求均值）。
    覆盖不足时 coverage 照样给（n / 窗口小时数），不插值、不补零。
    """
    rows = []
    acc = {}
    q = ("SELECT metric, t, value, coverage FROM obs "
         "WHERE metric LIKE '%s%%' ORDER BY t" % fragments.PREFIX)
    for r in con.execute(q):
        m, t, v = r["metric"], r["t"], r["value"]
        if m not in reg:
            continue
        agg = reg[m].get("agg")
        hour = int(t[11:13])
        start = t
        end = (datetime.datetime.strptime(t, "%Y-%m-%dT%H:%M:%S")
               + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
        rows.append({"scale": "hour", "key": fragments.hour_key(t), "metric": m,
                     "value": v, "agg": agg, "n": 1, "coverage": r["coverage"],
                     "judge": None, "judge_rate": None, "delta_v": None,
                     "delta_mdc": None, "delta_verdict": None, "delta_vs": None,
                     "delta_note": None, "start": start, "end": end, "expected": 1})
        part = fragments.daypart_of(hour)
        if part:
            cell = acc.setdefault((t[:10], part, m), [])
            cell.append(v)
    for (date, part, m), vals in sorted(acc.items()):
        hours = fragments.daypart_hours(part)
        agg = (reg.get(m) or {}).get("agg")
        d0 = datetime.datetime.strptime(date, "%Y-%m-%d")
        rows.append({"scale": "daypart", "key": fragments.daypart_key(date, part),
                     "metric": m, "value": fragments.aggregate(vals, agg),
                     "agg": agg, "n": len(vals),
                     "coverage": round(len(vals) / float(len(hours)), 3),
                     "judge": None, "judge_rate": None, "delta_v": None,
                     "delta_mdc": None, "delta_verdict": None, "delta_vs": None,
                     "delta_note": None,
                     "start": (d0 + datetime.timedelta(hours=hours[0])).strftime(
                         "%Y-%m-%dT%H:%M:%S"),
                     "end": (d0 + datetime.timedelta(hours=hours[-1] + 1)).strftime(
                         "%Y-%m-%dT%H:%M:%S"),
                     "expected": len(hours)})
    return rows


# ------------------------------------------------------------------ 求值核心
# 这一节是**全仓唯一的「窗口 → 单元格」实现**：`rebuild()`（批量物化六个尺度）与
# `query.py`（任意窗口按需查询）都调这里的函数。抽出来的唯一理由就是 P4 的那条
# 硬约束——查询不许有自己的一套数学。任何在这里之外重写聚合/达标/环比的地方，
# 都会让两个消费端在同一个问题上给出不同答案（docs/query-interface.md §3）。
def context(con, rules, metrics):
    """一次性装载求值输入。同一份 ctx 喂给所有 spec，避免逐格读库。"""
    u = rules.get("units") or {}
    day_vals, bands, cov = _read_obs(con, metrics)
    if not day_vals:
        return None
    return {
        "day_vals": day_vals, "bands": bands, "cov": cov,
        "cov_min": u.get("coverage_min") or {},
        "delta_min_n": u.get("delta_min_n", 2),
        "suppress": set(u.get("tier_suppress_change") or ["low", "none"]),
        "day_cov_min": u.get("day_coverage_min", 0.95),
        "daily_ok_rate": u.get("daily_ok_rate", 0.6),
        "ref_names": {m: sorted({r for expr in (md.get("thresholds") or {}).values()
                                 if isinstance(expr, str)
                                 for r in metricdefs.refs_in(expr)})
                      for m, md in metrics.items()},
        "first": min(day_vals), "last": max(day_vals),
        "today": datetime.date.today().isoformat(),
    }


def compute_cell(spec, metric, mdef, ctx, rules):
    """一个 (窗口, 指标) → 单元格。**不含环比**（环比要看相邻单元格，见 apply_delta）。

    spec 里的 `ruler` 是「借哪把已标定的尺子」：日/周/月/滚动窗各用自己那把；
    任意窗口没有标定过门槛，借用长度最接近的尺度并把 ruler 写进结果，保证
    「这个判定用的是哪把尺子」可回答。
    """
    day_vals, cov, bands = ctx["day_vals"], ctx["cov"], ctx["bands"]
    sc = spec["scale"]
    ruler = spec.get("ruler") or sc
    vals = []
    for d in spec["dates"]:
        v = day_vals.get(d, {}).get(metric)
        if v is None:
            continue
        # 截断日不进**聚合**：coverage 0.11 的那天算「一天」会把周均整体拉偏
        # （2026-10-25 02:48 截止、1656 步，计入会把本周步数从 6,400 压到 5,729）。
        # 日尺度例外——值要留着给「今天走到现在多少步」用，由 coverage 判 na。
        c = cov.get((metric, d))
        if sc != "day" and c is not None and c < ctx["day_cov_min"]:
            continue
        vals.append(v)
    if not vals:
        return None
    value = _agg(vals, mdef.get("agg"))
    n = len(vals)
    if sc == "day":
        # 日尺度的 coverage ＝ 这一天被观测了多大比例（不是 n/1=1.0）。
        # 否则截断日会拿到 1.0 覆盖率并照常判达标——那是把半天的账当整天。
        cs = [cov.get((metric, d)) for d in spec["dates"]]
        cs = [c for c in cs if c is not None]
        coverage = round(min(cs), 3) if cs else None
    else:
        coverage = round(n / spec["expected"], 3) if spec["expected"] else None
    band = None
    if metric == "hrv_ms":
        for d in reversed(spec["dates"]):
            b = bands.get(d) or {}
            if b.get("hrv_band_low") is not None and b.get("hrv_band_high") is not None:
                band = (b["hrv_band_low"], b["hrv_band_high"])
                break
    judge, judge_rate = None, None
    if mdef.get("dir"):
        if coverage is None or coverage < (ctx["cov_min"].get(ruler, 0) or 0):
            judge = "na"
        elif mdef.get("judge_mode") == "daily":
            # 阈值逐日变（如「设备当晚算的需求」）→ 逐日判再求达标率。
            # 用「日均时长 ÷ 日均需求」会失真：两个均值相除不等于逐日达标率。
            ok_n = tot = 0
            for d in spec["dates"]:
                vd = day_vals.get(d, {})
                val_d = vd.get(metric)
                if val_d is None:
                    continue
                c_d = cov.get((metric, d))
                if c_d is not None and c_d < ctx["day_cov_min"] and sc != "day":
                    continue
                refs = {k: vd.get(k) for k in ctx["ref_names"].get(metric, [])}
                try:
                    th = metricdefs.judge_of(metric, rules, refs)
                except Exception:
                    th = metricdefs.judge_of(metric, rules)
                if th is None:
                    continue
                tot += 1
                ok_n += 1 if (val_d >= th if mdef["dir"] == "above"
                              else val_d <= th) else 0
            if tot:
                judge_rate = round(ok_n / tot, 3)
                judge = "ok" if judge_rate >= ctx["daily_ok_rate"] else "no"
            else:
                judge = "na"
        else:
            judge = _judge(metric, value, mdef, rules, band)
    return {"scale": sc, "key": spec["key"], "metric": metric, "value": value,
            "agg": mdef.get("agg"), "n": n, "coverage": coverage,
            "judge": judge, "judge_rate": judge_rate,
            "start": spec["start"], "end": spec["end"],
            "expected": spec["expected"], "ruler": spec.get("ruler")}


def compute_cells(spec, metrics, ctx, rules):
    """一个窗口 × 全部指标 → 单元格列表（跳过没有数据的指标）。"""
    out = []
    for m, mdef in metrics.items():
        cell = compute_cell(spec, m, mdef, ctx, rules)
        if cell is not None:
            out.append(cell)
    return out


def apply_delta(rows, cells, ctx, rules, key_of_prev=None):
    """环比 / 位移差。**变化判定只在这里**，消费方只读 delta_verdict。

    `key_of_prev(scale, key)` 允许查询层把「上一个单元」定义成别的（窗口 → 前移一个窗长），
    而日/周/月/滚动仍走 `prev_key`。五档语义不变。
    """
    def _default_prev(sc, key):
        if sc == "window":
            # 任意窗口没有「上一个单元」的天然定义（前移一个窗长由调用方决定），
            # 猜一个就会让两个不同的窗口被当成可比。宁可报错。
            raise ValueError("window 尺度的上一窗必须由调用方传入 key_of_prev")
        return prev_key(sc, key, k=int(sc[4:]) if sc.startswith("roll") else None)

    key_of_prev = key_of_prev or _default_prev
    cov_min, delta_min_n, suppress = ctx["cov_min"], ctx["delta_min_n"], ctx["suppress"]
    for row in rows:
        sc, key, m = row["scale"], row["key"], row["metric"]
        ruler = row.get("ruler") or sc
        conf = db.metric_conf(rules, m)
        mdc = conf.get("mdc_change")
        tier = (conf.get("tier") or {}).get("change")
        pkey = key_of_prev(sc, key)
        prev = cells.get((sc, pkey, m))
        row["delta_vs"] = pkey
        row["delta_mdc"] = mdc
        if prev is None or prev.get("value") is None:
            row["delta_verdict"], row["delta_note"] = "insufficient", "没有可比的上一个单元"
            continue
        dv = round(row["value"] - prev["value"], 3)
        row["delta_v"] = dv
        # 覆盖不足时**不给变化结论**（2026-10-25）。理由：近 28/90 天窗只有 50% 覆盖时，
        # 两个窗的均值差把「缺数据」算成了「变了」——这正是趋势分析最容易被污染的形态。
        # 优先级最高：它比样本量、比门槛都更根本（数字本身不可比）。
        cmin = float(cov_min.get(ruler, 0) or 0)
        cs = [x for x in (row.get("coverage"), prev.get("coverage")) if x is not None]
        if cmin and cs and min(cs) < cmin:
            row["delta_verdict"] = "insufficient"
            row["delta_note"] = "覆盖不足（%.0f%% / 门槛 %.0f%%）" % (min(cs) * 100, cmin * 100)
            continue
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
            row["delta_note"] = "|Δ|=%.2f > mdc %.2f" % (abs(dv), mdc)
        else:
            row["delta_verdict"] = "within-noise"
            row["delta_note"] = "|Δ|=%.2f ≤ mdc %.2f" % (abs(dv), mdc)
    return rows


# ------------------------------------------------------------------ 重算
def rebuild(con, rules=None, metrics=None, quiet=True):
    rules = rules or db.load_rules()
    reg = db.load_metrics()
    _frag_only = frag_metrics(reg)
    metrics = {k: v for k, v in (metrics or {k: v for k, v in reg.items()
                                             if v.get("agg")}).items()
               if k not in _frag_only}
    ctx = context(con, rules, metrics)
    if ctx is None:
        if not quiet:
            print("obs 表为空：先跑 index.py build")
        return 0
    specs = unit_specs(ctx["first"], ctx["last"], ctx["today"], rules)

    # ---- 第一遍：算出每个单元每个指标的值 ----
    cells, rows = {}, []
    for sp in specs:
        for row in compute_cells(sp, metrics, ctx, rules):
            cells[(row["scale"], row["key"], row["metric"])] = row
            rows.append(row)

    # ---- 第二遍：环比 / 位移差 ----
    apply_delta(rows, cells, ctx, rules)

    con.executemany("DELETE FROM units WHERE scale=? AND key=?",
                    [(sp["scale"], sp["key"]) for sp in specs])
    db.upsert_units(con, rows)
    frows = _frag_units(con, reg)
    if frows:
        con.execute("DELETE FROM units WHERE scale IN (%s)"
                    % ",".join("?" * len(fragments.FRAGMENT_SCALES)),
                    fragments.FRAGMENT_SCALES)
        db.upsert_units(con, frows)
        rows.extend(frows)
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
