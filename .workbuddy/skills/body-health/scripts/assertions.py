#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L3 断言层：把规则判定固化成可查询、可回归的记录（2026-10-25 建）。

**为什么需要这一层（notes/architecture_v4.md §3.3）**
现在的判读是「LLM 读散文规则 + 读一堆原始数字，每晚重新执行一遍」。
后果有三：① 同一份数据两次可能判出不同结论；② 改一条阈值，没人知道历史结论偏了多少；
③ 想问「过去 90 天红灯出现在哪些维度」只能重读 90 份报告。

断言把「判定」从叙述里剥出来：

    { id: "autonomic.hrv.change_beyond_mdc", scope: (day, 2026-10-24),
      verdict: true, level: "WARN", confidence: "med",
      evidence: {value:38, prev:64, delta:-26, mdc:20}, rule_ref: "confidence.hrv_ms" }

四条性质：**id 稳定**（可跨日统计）、**evidence 只含算好的数字**（LLM 只能引用）、
**level 由代码定**（红黄绿不可被 LLM 改判）、**可回归**（改阈值后跑 diff）。

断言是 DAG：L1 单指标（本文件 `_l1_*`）→ L2 交叉（`_cross_*`，只吃 L1 的布尔）→
总评（取最差维度，见 `rollup`）。**交叉规则必须由代码判**——`daily_brief.cross_analysis()`
早就写成了布尔组合，却因为新流程不用它，改由 LLM 每晚读散文重新执行。

未触发的断言也落库（verdict=0）：回测要能看见「某条规则从哪天开始触发」，
而不是只有触发时的快照。

用法：
    python3 assertions.py rebuild
    python3 assertions.py list    --date 2026-10-24 [--level WARN] [--json]
    python3 assertions.py trend   --id autonomic.hrv.below_band --days 90
    python3 assertions.py summary --days 90
    python3 assertions.py rollup  --date 2026-10-24
"""
import argparse
import datetime
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import db  # noqa: E402
import metricdefs  # noqa: E402

BAD, WARN, GOOD, INFO = "BAD", "WARN", "GOOD", "NEUTRAL"
SEVERITY = {BAD: 0, WARN: 1, GOOD: 2, INFO: 3}


# ------------------------------------------------------------------ 读取
def _day_values(con):
    """date -> {metric: value}。断言只读 obs，不读 raw、不读 facts。"""
    out = {}
    for r in con.execute("SELECT metric, substr(t,1,10) AS d, value FROM obs"):
        out.setdefault(r["d"], {})[r["metric"]] = r["value"]
    return out


def _streak(dates_desc, fn):
    """从最近一天往回数连续满足 fn 的天数（遇 None 即断）。"""
    n = 0
    for d in dates_desc:
        if fn(d):
            n += 1
        else:
            break
    return n


# ------------------------------------------------------------------ 构造
def A(aid, date, dimension, subject, rule_ref, verdict, level=None,
      evidence=None, confidence=None, hint=None, scale="day", key=None):
    return {"id": aid, "scale": scale, "key": key or date, "date": date,
            "dimension": dimension, "subject": subject,
            "verdict": 1 if verdict else 0,
            "level": level if verdict else None,
            "confidence": confidence, "rule_ref": rule_ref,
            "evidence": json.dumps(evidence or {}, ensure_ascii=False,
                                   default=str),
            "action_hint": hint if verdict else None}


# ------------------------------------------------------------------ L1
def build_day(date, v, rules, ctx):
    """一天的 L1 断言。v = 当日 {metric: value}；ctx = 跨日上下文（streak 等）。"""
    out = []
    hc = db.metric_conf(rules, "hrv_ms")

    def conf(metric):
        return (db.metric_conf(rules, metric).get("tier") or {}).get("level")

    # ---- data：缺口是结论的一部分，不是脚注 ----
    cov = ctx.get("cutoff_min")
    if ctx.get("level") == "partial":
        out.append(A("data.day.partial", date, "data", None, "gate.complete_cutoff_min",
                     True, WARN, {"cutoff_min": cov,
                                  "complete_cutoff_min": rules["gate"]["complete_cutoff_min"]},
                     "high", "该日数据未收全，结论按截止时刻限定"))
    if ctx.get("level") == "no_data":
        out.append(A("data.day.no_data", date, "data", None, "gate.empty_day_awake_min",
                     True, INFO, {"level": "no_data"}, "high",
                     "没有输入就不给健康结论"))
    elif not v:
        out.append(A("data.day.no_observations", date, "data", None, "obs.empty",
                     True, INFO, {}, "high"))

    # ---- sleep ----
    sh = v.get("sleep_h")
    if sh is not None:
        refs = {"sleep_need_h": v.get("sleep_need_h")}
        floor_h = _th("sleep_h", "judge", rules, refs)    # max(静态 7h, 设备当晚需求)
        severe_h = _th("sleep_h", "severe", rules)        # 6.0（rules.cross.short_sleep_min / 60）
        short = floor_h is not None and sh < floor_h
        out.append(A("sleep.duration.short", date, "sleep", "sleep_h",
                     "sleep.min_healthy_duration_min|ref:sleep_need_h", short,
                     BAD if (severe_h is not None and sh < severe_h) else WARN,
                     {"hours": round(sh, 2), "floor_h": floor_h,
                      "device_need_h": v.get("sleep_need_h"),
                      "ruler": metricdefs.ruler_of("sleep_h",
                                                   {"sleep_need_h": v.get("sleep_need_h")}),
                      "severe_h": severe_h},
                     conf("sleep_h"), "补觉优先于训练"))
        out.append(A("sleep.duration.very_short", date, "sleep", "sleep_h",
                     "cross.short_sleep_min",
                     severe_h is not None and sh < severe_h, BAD,
                     {"hours": round(sh, 2), "threshold_h": severe_h},
                     conf("sleep_h"), "欠觉日训练只到 Z2"))
    rem = v.get("sleep_rem_pct")
    if rem is not None:
        lo, hi = metricdefs.band_of("sleep_rem_pct", rules)
        out.append(A("sleep.rem.low", date, "sleep", "sleep_rem_pct",
                     "sleep.rem_pct_optimal", rem < lo, WARN,
                     {"rem_pct": rem, "optimal": [lo, hi]}, None,
                     "REM 由起床时刻与睡眠连续性决定，先修作息再谈补剂"))
    deep = v.get("sleep_deep_pct")
    if deep is not None:
        lo, hi = metricdefs.band_of("sleep_deep_pct", rules)
        out.append(A("sleep.deep.out_of_band", date, "sleep", "sleep_deep_pct",
                     "sleep.deep_pct_optimal", not (lo <= deep <= hi), INFO,
                     {"deep_pct": deep, "optimal": [lo, hi]}, None))
    bed = v.get("sleep_bed_min")
    bw_end = v.get("bed_window_end_min")
    late_min = metricdefs.judge_time_of("sleep_bed_min", rules,
                                        {"bed_window_end_min": bw_end})
    severe_bed = None
    try:
        severe_bed = metricdefs.severe_of("sleep_bed_min", rules,
                                          {"bed_window_end_min": bw_end})
    except Exception:
        severe_bed = None
    if bed is not None:
        out.append(A("sleep.bedtime.late", date, "sleep", "sleep_bed_min",
                     "ref:bed_window_end_min + sleep.bed_late_tolerance_min",
                     late_min is not None and bed >= late_min, WARN,
                     {"bed_min": bed, "bed_threshold_min": late_min,
                      "device_window_end": bw_end,
                      "ruler": metricdefs.ruler_of("sleep_bed_min",
                                                   {"bed_window_end_min": bw_end}),
                      "offset_from_window_min": (bed - bw_end) if bw_end else None},
                     None, "入睡提前比任何补剂都有效"))
        if severe_bed is not None and bed >= severe_bed:
            out.append(A("sleep.bedtime.very_late", date, "sleep", "sleep_bed_min",
                         "ref:bed_window_end_min + sleep.bed_severe_offset_min",
                         True, BAD,
                         {"bed_min": bed, "bed_severe_min": severe_bed,
                          "ruler": metricdefs.ruler_of("sleep_bed_min",
                                                       {"bed_window_end_min": bw_end}),
                          "offset_from_window_min": bed - bw_end if bw_end else None},
                         None, "超过 2 小时就不是『晚一点』，是作息整体后移"))
    out.append(A("sleep.debt.streak_ge_2", date, "sleep", "sleep_h",
                 "cross.sleep_debt_nights", ctx.get("short_streak", 0) >= 2, WARN,
                 {"streak": ctx.get("short_streak", 0),
                  "min_nights": rules["cross"]["sleep_debt_nights"]}, conf("sleep_h"),
                 "睡眠债按周计，一晚还不清"))

    # ---- activity ----
    st = v.get("steps_daily")
    if st is not None:
        # 两条互斥：severe(<5000) 判红；low(5000–8000) 只判黄。
        # 不能两条都判红——那会让「严重不足」这一件事在总评里被数两次。
        judge_lo = _th("steps_daily", "judge", rules)      # 8000（判读线）
        severe_lo = _th("steps_daily", "severe", rules)    # 5000（红线）
        out.append(A("activity.steps.severe", date, "activity", "steps_daily",
                     "activity.steps_low",
                     severe_lo is not None and st < severe_lo, BAD,
                     {"steps": st, "threshold": severe_lo}, conf("steps_daily")))
        out.append(A("activity.steps.low", date, "activity", "steps_daily",
                     "activity.steps_good",
                     (severe_lo is not None and judge_lo is not None
                      and severe_lo <= st < judge_lo), WARN,
                     {"steps": st, "good": judge_lo, "low": severe_lo},
                     conf("steps_daily")))
    sed = v.get("sedentary_min")
    if sed is not None:
        sed_max = _th("sedentary_min", "judge", rules)
        out.append(A("activity.sedentary.over", date, "activity", "sedentary_min",
                     "activity.sedentary_warn_hours",
                     sed_max is not None and sed >= sed_max, WARN,
                     {"sedentary_min": sed, "limit_min": sed_max},
                     None, "每 60 分钟起身一次，比事后补运动有效"))
    im = v.get("intensity_min")
    if im is not None:
        im_goal = _th("intensity_min", "judge", rules)
        out.append(A("activity.intensity.below_goal", date, "activity", "intensity_min",
                     "activity.intensity_daily_goal",
                     im_goal is not None and im < im_goal, WARN,
                     {"intensity_min": im, "goal": im_goal},
                     conf("steps_daily"), "日常走动换不来中高强度，要主动起一段"))

    # ---- autonomic ----
    hrv = v.get("hrv_ms")
    lo = v.get("hrv_band_low")
    hi = v.get("hrv_band_high")
    if hrv is not None and lo is not None:
        below = hrv < lo
        dev = round(lo - hrv, 1) if below else 0
        out.append(A("autonomic.hrv.below_band", date, "autonomic", "hrv_ms",
                     "hrv.fallback_band", below, WARN,
                     {"value": hrv, "band": [lo, hi], "below_by": dev,
                      "mdc_single": hc.get("mdc_single"),
                      "beyond_mdc_single": dev >= (hc.get("mdc_single") or 1e9)},
                     conf("hrv_ms")))
    prev = ctx.get("hrv_prev")
    if hrv is not None and prev is not None:
        dv = round(hrv - prev, 1)
        mdc = hc.get("mdc_change")
        out.append(A("autonomic.hrv.change_beyond_mdc", date, "autonomic", "hrv_ms",
                     "confidence.hrv_ms.mdc_change",
                     mdc is not None and abs(dv) > mdc, WARN,
                     {"value": hrv, "prev": prev, "delta": dv, "mdc": mdc},
                     conf("hrv_ms"), "方向确认后再说『下降』"))
    rhr = v.get("rhr_bpm")
    avg7 = v.get("rhr_avg7")
    if rhr is not None and avg7 is not None:
        d = rhr - avg7
        d_max = metricdefs.delta_threshold("rhr_bpm", rules)
        out.append(A("autonomic.rhr.elevated", date, "autonomic", "rhr_bpm",
                     "rhr.elevated_delta_bpm", d_max is not None and d >= d_max,
                     WARN, {"value": rhr, "avg7": avg7, "delta": d, "threshold": d_max},
                     conf("rhr_bpm")))

    # ---- load ----
    win = v.get("exercise_window_n")
    z2 = v.get("z2_min")
    if win is not None:
        out.append(A("load.exercise.window_detected", date, "load", "exercise_window_n",
                     "exercise.window_avg_hr_min", win >= 1, GOOD,
                     {"windows": win}, None, "证据是逐时心率，不是活动记录"))
    if z2 is not None:
        out.append(A("load.z2.none", date, "load", "z2_min", "z2.pct_of_max", z2 <= 0,
                     INFO, {"z2_min": z2, "band_target": rules["z2"]["weekly_target_min"]},
                     None, "零 Z2 不惩罚，只报进账"))
        out.append(A("load.z2.effective_session", date, "load", "z2_min",
                     "z2.effective_session_min",
                     z2 >= rules["z2"]["effective_session_min"], GOOD,
                     {"z2_min": z2, "effective_min": rules["z2"]["effective_session_min"]},
                     None, "有效剂量靠单次 ≥45min 累积"))

    # ---- stress / energy ----
    bbl = v.get("bb_low")
    if bbl is not None:
        out.append(A("stress_energy.bb.low", date, "stress_energy", "bb_low",
                     "bb.critical_low", bbl <= rules["bb"]["critical_low"], WARN,
                     {"bb_low": bbl, "critical": rules["bb"]["critical_low"]}, None))
    out.append(A("stress_energy.bb.net_negative_streak", date, "stress_energy", "bb_net",
                 "cross.bb_net_negative_days", ctx.get("bb_neg_streak", 0) >= 2, WARN,
                 {"streak": ctx.get("bb_neg_streak", 0),
                  "min_days": rules["cross"]["bb_net_negative_days"]}, None,
                 "收支倒挂的源头通常在入睡时间"))
    sa = v.get("stress_avg")
    good = _th("stress_avg", "judge", rules)
    if sa is not None and good is not None:
        out.append(A("stress_energy.avg.high", date, "stress_energy", "stress_avg",
                     "homepage.stress_avg_good", sa > good, WARN,
                     {"stress_avg": sa, "good_max": good}, None))

    # ---- capacity ----
    vo2 = v.get("vo2max")
    if vo2 is not None:
        band = next((b["name"] for b in rules["vo2max_male_bands"]
                     if b["min"] <= vo2 < b["max"]), "?")
        out.append(A("capacity.vo2.band_low", date, "capacity", "vo2max",
                     "vo2max_male_bands", band in ("差", "一般"), INFO,
                     {"vo2max": vo2, "band": band}, conf("vo2max")))
    rd = v.get("readiness_score")
    rd_line = _th("readiness_score", "judge", rules)
    if rd is not None and rd_line is not None:
        out.append(A("capacity.readiness.low", date, "capacity", "readiness_score",
                     "readiness.low_score", rd < rd_line, WARN,
                     {"readiness": rd, "line": rd_line}, None))

    # ---- data：动态参考量缺失 = 尺子换了，必须显式说 ----
    for ref_name, m in (("sleep_need_h", "sleep_need_h"),
                        ("bed_window_end_min", "bed_window_end_min")):
        if v.get(ref_name) is None:
            out.append(A("data.reference.missing", date, "data", m,
                         "metricdefs.thresholds.*_fallback", True, INFO,
                         {"ref": ref_name,
                          "affects": ("sleep_h" if ref_name == "sleep_need_h"
                                      else "sleep_bed_min"),
                          "fallback": ("静态 7h" if ref_name == "sleep_need_h"
                                      else "静态 01:30")}, "high",
                         "该日信源没给这个参考量，判定已退回静态口径——报告须写明用的哪把尺子"))

    # ---- device：把信源自己的判断与参考量摆上桌（INFO 透传，不映射成我们的红黄绿）----
    need = v.get("sleep_need_h")
    need_base = v.get("sleep_need_baseline_h")
    if need is not None and need_base is not None:
        out.append(A("device.sleep_need.elevated", date, "device", "sleep_need_h",
                     "ref:sleep_need_baseline_h", need > need_base + 0.25, INFO,
                     {"need_h": need, "baseline_h": need_base,
                      "delta_h": round(need - need_base, 2), "ruler": "device"}, None,
                     "设备把需求抬高了，通常来自睡眠债/训练负荷/HRV"))
    if bw_end is not None and bed is not None:
        out.append(A("device.bedtime.offset_from_window", date, "device", "sleep_bed_min",
                     "ref:bed_window_end_min", bed > bw_end, INFO,
                     {"bed_min": bed, "window": [v.get("bed_window_start_min"), bw_end],
                      "offset_min": bed - bw_end, "ruler": "device"}, None,
                     "设备推荐窗是按当前作息算的，只比偏移量、不拿窗当标准"))
    rd = v.get("readiness_score")
    if rd is not None:
        out.append(A("device.readiness.grade", date, "device", "readiness_score",
                     "grades.readiness_level", True, INFO,
                     {"score": rd}, None,
                     "设备五档原样透传；与综合指数并排看背离，不互相改写"))
    if v.get("acwr") is not None:
        out.append(A("device.acwr.level", date, "device", "acwr", "acwr.under",
                     v["acwr"] < rules["acwr"]["under"], INFO,
                     {"acwr": v["acwr"], "under": rules["acwr"]["under"]}, None))

    # ---- cross（只吃 L1 的布尔，不自己算数）----
    trg = {x["id"]: bool(x["verdict"]) for x in out}
    hrv_hit = trg.get("autonomic.hrv.below_band") or \
        trg.get("autonomic.hrv.change_beyond_mdc")
    no_load = (win in (0, None)) and (z2 in (0, None))
    if hrv_hit and no_load:
        out.append(A("cross.recovery_break_no_load", date, "cross",
                     "hrv_ms,z2_min,exercise_window_n", "cross.recovery_break_no_load",
                     True, WARN,
                     {"hrv": hrv, "prev_hrv": prev, "z2_min": z2, "windows": win},
                     conf("hrv_ms"),
                     "无负荷可挂 → 负荷只能落在睡眠破碎或晚间应激，先查这两个"))
    if trg.get("sleep.debt.streak_ge_2") and \
            (trg.get("autonomic.hrv.below_band") or trg.get("autonomic.rhr.elevated")):
        out.append(A("cross.sleep_debt_autonomic", date, "cross", "sleep_h,hrv_ms,rhr_bpm",
                     "cross.sleep_debt_nights", True, BAD,
                     {"short_streak": ctx.get("short_streak", 0), "hrv": hrv, "rhr": rhr},
                     conf("hrv_ms"),
                     "累积恢复不足，交感张力在升高——此状态不安排高强度"))
    if trg.get("activity.sedentary.over") and v.get("bb_net") is not None \
            and v["bb_net"] <= -30:
        out.append(A("cross.sedentary_high_energy", date, "cross",
                     "sedentary_min,bb_net", "cross.sedentary_high_energy", True, WARN,
                     {"sedentary_min": sed, "bb_net": v["bb_net"], "stress_avg": sa}, None,
                     "久坐日的疲劳是真实的，但不产生训练适应"))
    if trg.get("load.exercise.window_detected") and trg.get("sleep.duration.very_short"):
        out.append(A("cross.load_after_short_sleep", date, "cross",
                     "exercise_window_n,sleep_h", "cross.short_sleep_min", True, BAD,
                     {"windows": win, "sleep_h": sh}, conf("sleep_h"),
                     "睡眠剥夺下做高强度：皮质醇放大、损伤风险上升"))
    steps_judge = _th("steps_daily", "judge", rules)
    im_judge = _th("intensity_min", "judge", rules)
    if (st is not None and steps_judge is not None and st >= steps_judge
            and im is not None and im_judge is not None and im < im_judge
            and not trg.get("load.exercise.window_detected")
            and sh is not None and _th("sleep_h", "judge", rules)
            and sh >= _th("sleep_h", "judge", rules)):
        out.append(A("cross.idle_recovery_unused", date, "cross",
                     "steps_daily,intensity_min,sleep_h", "activity.intensity_daily_goal",
                     True, INFO, {"steps": st, "intensity_min": im, "sleep_h": sh},
                     conf("sleep_h"), "恢复能力是给训练用的，攒着不花就是浪费"))
    return out


def _th(metric, field, rules, refs=None):
    """阈值一律经 metricdefs 取——本文件不持有任何 metric→规则 的硬编码映射。

    refs 传了就用**信源给的动态值**（设备当晚算的睡眠需求、推荐入睡窗…），
    取不到时 metricdefs 会自动退回注册表里声明的 `<field>_fallback`。
    """
    return metricdefs.threshold(metric, field, rules, refs)


def _hhmm_to_min(s):
    try:
        h, m = s.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 0


# ------------------------------------------------------------------ 单元级
def build_unit_scope(con, rules):
    """跨尺度断言：单元覆盖率不足时**不给结论**，这条本身也要落库。

    只对**有达标方向的指标**（dir 非空）报覆盖不足——coverage 影响的是
    「能不能判达标」，不影响 vo2max 这种稀疏点值的存在性。
    """
    u = rules.get("units") or {}
    cov_min = u.get("coverage_min") or {}
    judged = {k for k, v in db.load_metrics().items() if v.get("dir")}
    out = []
    for scale in ("week", "month", "roll7", "roll28", "roll90"):
        rows = list(con.execute(
            "SELECT key, end, metric, coverage FROM units WHERE scale=? "
            "AND coverage IS NOT NULL", (scale,)))
        rows = [r for r in rows if r["metric"] in judged]
        agg = {}
        for r in rows:
            if r["coverage"] < cov_min.get(scale, 0):
                agg.setdefault(r["key"], []).append(r["metric"])
        for key, ms in agg.items():
            end = next(r["end"] for r in rows if r["key"] == key)
            out.append(A("data.unit.coverage_low", end, "data", ",".join(sorted(ms)[:6]),
                         "units.coverage_min", True, INFO,
                         {"scale": scale, "key": key, "below": sorted(ms),
                          "coverage_min": cov_min.get(scale)},
                         "high", "覆盖不足的单元只报数字，不给结论",
                         scale=scale, key=key))
    return out


# ------------------------------------------------------------------ 重算
def rebuild(con, rules=None):
    rules = rules or db.load_rules()
    vals = _day_values(con)
    if not vals:
        print("obs 表为空：先跑 index.py build")
        return 0
    dates = sorted(vals)
    gates = {}
    for r in con.execute(
            "SELECT date, level, cutoff_min FROM obs_day_gate"):
        gates[r["date"]] = {"level": r["level"], "cutoff_min": r["cutoff_min"]}

    rows = []
    prev_hrv = None
    for i, d in enumerate(dates):
        v = vals[d]
        desc = dates[:i + 1][::-1]                    # 当日在前
        short_streak = _streak(desc, lambda x: (vals[x].get("sleep_h") or 99) * 60
                               < rules["sleep"]["min_healthy_duration_min"])
        bb_streak = _streak(desc, lambda x: (vals[x].get("bb_net") or 0) < 0)
        ctx = {"cutoff_min": gates.get(d, {}).get("cutoff_min"),
               "level": gates.get(d, {}).get("level"),
               "short_streak": short_streak, "bb_neg_streak": bb_streak,
               "hrv_prev": prev_hrv}
        rows.extend(build_day(d, v, rules, ctx))
        if v.get("hrv_ms") is not None:
            prev_hrv = v["hrv_ms"]

    rows.extend(build_unit_scope(con, rules))

    con.execute("DELETE FROM assertions")
    db.upsert_assertions(con, rows)
    con.commit()
    fired = sum(1 for r in rows if r["verdict"])
    print("assertions: %d 条（触发 %d / 未触发 %d，含 %d 个单元级）"
          % (len(rows), fired, len(rows) - fired,
             sum(1 for r in rows if r["scale"] != "day")))
    return len(rows)


# ------------------------------------------------------------------ 汇总
def rollup(con, date):
    """总评 = 最差维度（代码定，不由 LLM 改判）。

    `data` 维度不参与最差档——它回答的是「今天能不能判」，不是「身体好不好」；
    但它能一票否决：no_data 时总评必须是「不可判读」，不许给红黄绿。
    """
    rows = list(con.execute(
        "SELECT dimension, level, id FROM assertions WHERE date=? AND scale='day' "
        "AND verdict=1", (date,)))
    if any(r["id"] == "data.day.no_data" for r in rows):
        return {"date": date, "level": INFO, "label": "不可判读",
                "reason": "当日无输入（空占位响应）", "dimensions": {}}
    dims = {}
    for r in rows:
        if r["dimension"] == "data":
            continue
        dims.setdefault(r["dimension"], []).append((r["level"], r["id"]))
    if not dims:
        return {"date": date, "level": INFO, "label": "不可判读", "dimensions": {}}
    worst = INFO
    for dim, items in dims.items():
        for lv, _ in items:
            if SEVERITY.get(lv, 9) < SEVERITY[worst]:
                worst = lv
    return {"date": date, "level": worst,
            "label": {BAD: "红灯", WARN: "黄灯", GOOD: "绿灯", INFO: "不可判读"}[worst],
            "dimensions": {k: [lv for lv, _ in v] for k, v in dims.items()}}


# ------------------------------------------------------------------ 对外快照
def snapshot(con, date, history_days=30):
    """某日断言的**唯一对外形态**（fact_pack / compose / verify 都读它）。

    为什么要有这一层：判读方以前直接读 facts.computed 里的散数字，于是
    「今天哪几条规则触发了、证据是什么、过去一个月是常态还是新事」只能靠 LLM 重推。
    这里一次给全，每条带 `n_hist`（近 N 天同 id 触发次数）——
    「10-24 的 HRV 破位是 30 天里第 4 次」这种上下文，散文版永远拿不到。
    """
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM assertions WHERE date=? AND scale='day' ORDER BY dimension, id",
        (date,))]
    since = (datetime.date.fromisoformat(date)
             - datetime.timedelta(days=history_days)).isoformat()
    hist = {}
    for r in con.execute(
            "SELECT id, COUNT(*) c FROM assertions WHERE scale='day' AND verdict=1 "
            "AND date>=? AND date<=? GROUP BY id", (since, date)):
        hist[r["id"]] = r["c"]

    out = []
    for r in rows:
        if not r["verdict"]:
            continue
        out.append({
            "id": r["id"], "dimension": r["dimension"], "level": r["level"],
            "subject": r["subject"], "confidence": r["confidence"],
            "rule_ref": r["rule_ref"],
            "evidence": json.loads(r["evidence"] or "{}"),
            "action_hint": r["action_hint"],
            "n_hist": hist.get(r["id"], 0),      # 近 history_days 天触发次数（含今天）
        })
    order = {BAD: 0, WARN: 1, GOOD: 2, INFO: 3}
    out.sort(key=lambda x: (order.get(x["level"], 9), x["dimension"], x["id"]))

    unit_scope = [dict(r) for r in con.execute(
        "SELECT id, scale, key, level, evidence FROM assertions "
        "WHERE date=? AND scale!='day' AND verdict=1 ORDER BY scale, key", (date,))]
    for u in unit_scope:
        u["evidence"] = json.loads(u["evidence"] or "{}")

    counts = {}
    for a in out:
        counts[a["level"]] = counts.get(a["level"], 0) + 1

    meta = db.get_meta(con)
    eng_now, rules_now = db.engine_ver(), db.rules_hash()
    return {
        "date": date,
        "rollup": rollup(con, date),
        "counts": counts,
        "l1": [a for a in out if a["dimension"] not in ("cross", "device")],
        "cross": [a for a in out if a["dimension"] == "cross"],
        "device": [a for a in out if a["dimension"] == "device"],
        "unit_scope": unit_scope,     # 周/月/滚动窗覆盖不足，明示「不给结论」
        "history_days": history_days,
        "fingerprint": {"rules_hash": rules_now, "engine_ver": eng_now,
                        "data_hash": meta.get("data_hash"),
                        "built_at": meta.get("built_at")},
        # 库的指纹与当前代码/规则不一致 = 物料过期。报告必须据实声明，不能装作没事。
        "stale": bool((meta.get("rules_hash") and meta["rules_hash"] != rules_now)
                      or (meta.get("engine_ver") and meta["engine_ver"] != eng_now)),
    }


# ------------------------------------------------------------------ CLI
def cmd_rebuild(a):
    con = db.connect()
    rebuild(con)
    return 0


def cmd_list(a):
    con = db.connect()
    q = "SELECT * FROM assertions WHERE date=?"
    args = [a.date]
    if a.level:
        q += " AND level=?" + " AND verdict=1"
        args.append(a.level)
    q += " ORDER BY dimension, id"
    rows = list(con.execute(q, args))
    if a.json:
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=1))
        return 0
    print("【%s】共 %d 条" % (a.date, len(rows)))
    for r in rows:
        if not r["verdict"] and not a.all:
            continue
        mark = {"BAD": "[红]", "WARN": "[黄]", "GOOD": "[绿]"}.get(r["level"], "[·]")
        ev = json.loads(r["evidence"] or "{}")
        print("%s %-34s %-22s %s" % (mark, r["id"], r["subject"] or "",
                                     json.dumps(ev, ensure_ascii=False)[:90]))
    return 0


def cmd_trend(a):
    con = db.connect()
    since = (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()
    rows = list(con.execute(
        "SELECT date, verdict, level, evidence FROM assertions "
        "WHERE id=? AND scale='day' AND date>=? ORDER BY date", (a.id, since)))
    hit = [r for r in rows if r["verdict"]]
    print("【%s】近 %d 天：%d 天有记录，%d 天触发"
          % (a.id, a.days, len(rows), len(hit)))
    for r in hit:
        print("  %s %-4s %s" % (r["date"], r["level"] or "-",
                                (r["evidence"] or "")[:100]))
    return 0


def cmd_summary(a):
    con = db.connect()
    since = (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()
    print("近 %d 天（自 %s）触发统计\n" % (a.days, since))
    print("%-10s %-36s %4s %4s %4s %4s" % ("维度", "断言", "红", "黄", "绿", "提示"))
    rows = list(con.execute(
        "SELECT dimension, id, level, COUNT(*) c FROM assertions "
        "WHERE scale='day' AND verdict=1 AND date>=? "
        "GROUP BY dimension, id, level", (since,)))
    agg = {}
    for r in rows:
        agg.setdefault((r["dimension"], r["id"]), {})[r["level"]] = r["c"]
    for (dim, aid), d in sorted(agg.items(), key=lambda kv: (kv[0][0], -sum(kv[1].values()))):
        print("%-10s %-36s %4s %4s %4s %4s" % (
            dim, aid, d.get(BAD, ""), d.get(WARN, ""), d.get(GOOD, ""), d.get(INFO, "")))
    lv = list(con.execute(
        "SELECT date, level, COUNT(*) c FROM assertions WHERE scale='day' "
        "AND verdict=1 AND date>=? GROUP BY date ORDER BY date", (since,)))
    print("\n逐日最差档：")
    for r in lv:
        mark = {"BAD": "红", "WARN": "黄", "GOOD": "绿"}.get(r["level"], "-")
        print("  %s %s" % (r["date"], mark))
    return 0


def cmd_rollup(a):
    con = db.connect()
    print(json.dumps(rollup(con, a.date), ensure_ascii=False, indent=1))
    return 0


def main():
    ap = argparse.ArgumentParser(description="L3 断言层")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("rebuild")
    p.set_defaults(func=cmd_rebuild)

    p = sub.add_parser("list")
    p.add_argument("--date", required=True)
    p.add_argument("--level", default=None, choices=[BAD, WARN, GOOD, INFO])
    p.add_argument("--all", action="store_true", help="含未触发的")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("trend")
    p.add_argument("--id", required=True)
    p.add_argument("--days", type=int, default=90)
    p.set_defaults(func=cmd_trend)

    p = sub.add_parser("summary")
    p.add_argument("--days", type=int, default=90)
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("rollup")
    p.add_argument("--date", required=True)
    p.set_defaults(func=cmd_rollup)

    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
