#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""物化索引：raw → L1 观测 → L2 单元 → L3 断言（2026-10-25 建）。

一条命令把三层重建完，`data/state/health.db` 是**派生层，可随时删掉重建**。
真相只有 `data/raw/*.json` 与 `references/rules.json`。

    python3 index.py build            # 全量（首次/口径变更后）
    python3 index.py build --since 2026-10-20   # 指定起始日（单元与断言受影响的自动重算）
    python3 index.py stats            # 表规模 + 指纹
    python3 index.py verify           # 一致性自检（11 项：覆盖率/孤行/断言引用/定义可解析/ruler 标注）

**为什么要有 ETL 并且只此一处**
架构公理 4：一个指标只有一条取值路径。raw → obs 的解析**只在本文件**
（`_day_obs`）。下游（units / assert / report / 主页）全部只读 DB，
所以口径变更只需重跑本文件，不用碰 raw，也不会出现「两个消费方各解析一遍」。

时区/挂钟换算一律走 adapters（gtime.py 是唯一出口），本文件零 epoch 运算。
"""
import argparse
import datetime
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from settings import ROOT as _WORKSPACE  # noqa: E402  路径唯一解析点（settings.py）
sys.path.insert(0, _WORKSPACE)

import db                                                  # noqa: E402
import units as units_mod                                  # noqa: E402
import metricdefs                                          # noqa: E402
import assertions as assert_mod                                # noqa: E402
import fragments                                           # noqa: E402
import eventbus                                            # noqa: E402
from settings import EVENTS_LOG                            # noqa: E402
from adapters import get_adapter                           # noqa: E402
from daily_brief import compute_z2, detect_exercise        # noqa: E402

RAW_DIR = os.path.join(_WORKSPACE, "data", "raw")
INBODY_INDEX = os.path.join(_WORKSPACE, "data", "inbody", "index.json")
DEVICE = "garmin-venu4"

#: 片段观测的 method。与日级的 garmin_usersummary / garmin_stress 等区分，
#: 让「这条数是源给的日汇总还是我们切出来的小时」在库里有据可查。
FRAG_METHOD = "garmin_hour"
#: 睡眠分级（Garmin sleepLevels.activityLevel）→ 片段指标名
SLEEP_STAGE_METRIC = {0: "hour_sleep_deep_min", 1: "hour_sleep_light_min",
                      2: "hour_sleep_rem_min", 3: "hour_sleep_awake_min"}


# ------------------------------------------------------------------ 小工具
def _hhmm_to_min(s):
    try:
        h, m = s.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return None


def _obs(metric, value, t, **kw):
    return {"metric": metric, "t": t, "value": value, **kw}


def _raw_ref(date):
    return os.path.join("data", "raw", "%s.json" % date)


# ------------------------------------------------------------------ ETL（唯一解析点）
def _day_obs(day, rules):
    """一个 canonical 日 → 观测列表。字段名只在这里出现一次。"""
    d = day["date"]
    t = d + "T00:00:00"
    cov = day.get("coverage") or {}
    cutoff = cov.get("cutoff_min")
    frac = None if cutoff is None else min(1.0, cutoff / 1440.0)
    common = dict(source="garmin", device=DEVICE, raw_ref=_raw_ref(d))
    out = []

    sl = day.get("sleep") or {}
    if sl.get("duration_s"):
        out.append(_obs("sleep_h", round(sl["duration_s"] / 3600.0, 3), t,
                        method="garmin_sleep", kind="interval",
                        t_end=sl.get("end_local"), coverage=1.0, **common))
        if sl.get("deep_s") is not None:
            out.append(_obs("sleep_deep_pct", round(sl["deep_s"] / sl["duration_s"] * 100, 1),
                            t, method="garmin_sleep", kind="derived", coverage=1.0,
                            derived_from=json.dumps({"sleep_h": sl["duration_s"]}), **common))
        if sl.get("rem_s") is not None:
            out.append(_obs("sleep_rem_pct", round(sl["rem_s"] / sl["duration_s"] * 100, 1),
                            t, method="garmin_sleep", kind="derived", coverage=1.0,
                            derived_from=json.dumps({"sleep_h": sl["duration_s"]}), **common))
        if sl.get("start_local"):
            bm = _hhmm_to_min(sl["start_local"])
            if bm is not None:
                out.append(_obs("sleep_bed_min", bm, t, method="garmin_sleep",
                                kind="point", coverage=1.0, **common))
    # —— 设备给的动态参考量（不是我们的判定，是判定的输入）——
    if sl.get("need_min") is not None:
        out.append(_obs("sleep_need_h", round(sl["need_min"] / 60.0, 3), t,
                        method="garmin_sleep_need", kind="point", coverage=1.0, **common))
    if sl.get("need_baseline_min") is not None:
        out.append(_obs("sleep_need_baseline_h", round(sl["need_baseline_min"] / 60.0, 3),
                        t, method="garmin_sleep_need", kind="point", coverage=1.0, **common))
    bw = sl.get("bed_window") or {}
    if bw.get("start") is not None:
        out.append(_obs("bed_window_start_min", bw["start"], t, method="garmin_sleep_need",
                        kind="point", coverage=1.0, **common))
    if bw.get("end") is not None:
        out.append(_obs("bed_window_end_min", bw["end"], t, method="garmin_sleep_need",
                        kind="point", coverage=1.0, **common))

    shr = (sl.get("sleep_hr") or {}).get("avg")
    if shr is not None:
        out.append(_obs("sleep_hr_avg", shr, t, method="garmin_sleep",
                        kind="interval", t_end=sl.get("end_local"), coverage=1.0, **common))

    hv = day.get("hrv") or {}
    if hv.get("last_night_avg") is not None:
        out.append(_obs("hrv_ms", hv["last_night_avg"], t, method="garmin_hrv",
                        kind="interval", t_end=sl.get("end_local"), coverage=1.0, **common))
    for k, mk in (("baseline_low", "hrv_band_low"), ("baseline_high", "hrv_band_high")):
        if hv.get(k) is not None:
            out.append(_obs(mk, hv[k], t, method="garmin_hrv", kind="point",
                            coverage=1.0, **common))

    rh = day.get("rhr") or {}
    if rh.get("value") is not None:
        out.append(_obs("rhr_bpm", rh["value"], t, method="garmin_rhr",
                        kind="point", coverage=1.0, **common))
    if rh.get("avg7") is not None:
        out.append(_obs("rhr_avg7", rh["avg7"], t, method="garmin_rhr",
                        kind="point", coverage=1.0, **common))

    act = day.get("activity") or {}
    for key, mk in (("steps", "steps_daily"), ("distance_km", "distance_km"),
                    ("active_kcal", "active_kcal"), ("sedentary_min", "sedentary_min"),
                    ("intensity_min", "intensity_min")):
        if act.get(key) is not None:
            out.append(_obs(mk, act[key], t, method="garmin_usersummary",
                            kind="interval", t_end=cov.get("end_local"),
                            coverage=frac, **common))
    if act.get("bb_charged") is not None and act.get("bb_drained") is not None:
        out.append(_obs("bb_net", act["bb_charged"] - act["bb_drained"], t,
                        method="garmin_usersummary", kind="derived",
                        t_end=cov.get("end_local"), coverage=frac,
                        derived_from=json.dumps({"charged": act["bb_charged"],
                                                 "drained": act["bb_drained"]}),
                        **common))
    bb = day.get("bb") or {}
    if bb.get("low") is not None:
        out.append(_obs("bb_low", bb["low"], t, method="garmin_bb", kind="point",
                        coverage=frac, **common))

    st = day.get("stress") or {}
    if st.get("avg") is not None:
        out.append(_obs("stress_avg", st["avg"], t, method="garmin_stress",
                        kind="interval", t_end=cov.get("end_local"),
                        coverage=frac, **common))

    sp = day.get("spo2") or {}
    if sp.get("avg7") is not None:
        out.append(_obs("spo2_avg7", round(sp["avg7"], 2), t, method="garmin_spo2",
                        kind="point", coverage=1.0, **common))
    if sp.get("sleep_avg") is not None:
        out.append(_obs("spo2_sleep_avg", sp["sleep_avg"], t, method="garmin_spo2",
                        kind="interval", t_end=sl.get("end_local"), coverage=1.0, **common))

    rd = day.get("readiness") or {}
    if rd.get("score") is not None:
        out.append(_obs("readiness_score", rd["score"], t, method="garmin_readiness",
                        kind="point", coverage=1.0, **common))

    tr = day.get("training") or {}
    if tr.get("acwr") is not None:
        out.append(_obs("acwr", tr["acwr"], t, method="garmin_training",
                        kind="point", coverage=frac, **common))

    vv = day.get("vo2max") or {}
    if vv.get("value") is not None:
        out.append(_obs("vo2max", vv["value"], (vv.get("updated") or d) + "T00:00:00",
                        method="garmin_estimate", kind="point", coverage=None, **common))

    # Z2 / 运动窗：由采样层算，不在这里另写一套（复用引擎唯一实现）
    hh, ss = day.get("hr_hourly") or {}, day.get("hr_series") or []
    if hh or ss:
        z2 = compute_z2(hh, rules, day.get("hr_sample_interval_min"), ss)
        out.append(_obs("z2_min", z2["minutes"], t, method="garmin_hr_series",
                        kind="derived", coverage=frac,
                        derived_from=json.dumps({"runs": z2.get("runs"),
                                                 "resolution": z2.get("resolution")},
                                                ensure_ascii=False),
                        **common))
        w = detect_exercise(hh, rules, day.get("hr_sample_interval_min")) if hh else []
        out.append(_obs("exercise_window_n", len(w), t, method="garmin_hr_hourly",
                        kind="derived", coverage=frac, **common))
    return [o for o in out if o.get("value") is not None]


READY_RANK = {"POOR": 0, "LOW": 1, "MODERATE": 2, "HIGH": 3, "PRIME": 4}
BREATH_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
QUAL_RANK = {"POOR": 0, "FAIR": 1, "GOOD": 2, "EXCELLENT": 3}


def _day_grades(day):
    """设备自己下的**分类判定**，进 grades 表，与我们的断言并列而非互相覆盖。

    为什么单独存：这些是字符串分档（MODERATE / UNBALANCED / FAIR），编码成数字会丢语义；
    而且「设备怎么判」和「我们怎么判」必须能同时呈现——主页准备度用设备五档、
    报告用我们的阈值，两者背离时正是有价值的信息（09-07 综合指数 92 / 准备度 41 LOW）。
    """
    d = day["date"]
    t = d + "T00:00:00"
    base = dict(source="garmin", t=t)
    out = []
    rd = day.get("readiness") or {}
    if rd.get("level"):
        out.append({**base, "metric": "readiness_level", "method": "garmin_readiness",
                    "grade": rd["level"], "rank": READY_RANK.get(rd["level"]),
                    "note": "score=%s" % rd.get("score")})
    hv = day.get("hrv") or {}
    if hv.get("status"):
        out.append({**base, "metric": "hrv_status", "method": "garmin_hrv",
                    "grade": hv["status"], "rank": None,
                    "note": "设备按 7 日均判定，不等于昨夜状态"})
    sl = day.get("sleep") or {}
    if sl.get("breathing"):
        out.append({**base, "metric": "breathing_disruption", "method": "garmin_sleep",
                    "grade": sl["breathing"], "rank": BREATH_RANK.get(sl["breathing"]),
                    "note": None})
    if sl.get("score_feedback"):
        out.append({**base, "metric": "sleep_score_feedback", "method": "garmin_sleep",
                    "grade": sl["score_feedback"], "rank": None, "note": None})
    for sub in (sl.get("sub_scores") or []):
        if sub.get("key") and sub.get("qualifier"):
            out.append({**base, "metric": "sleep_sub_%s" % sub["key"],
                        "method": "garmin_sleep", "grade": sub["qualifier"],
                        "rank": QUAL_RANK.get(sub["qualifier"]), "note": None})
    return out


#: 主观事件 → obs 指标口径。field = events.payload.metric。
#: 这里是 L1 读主观数据的**唯一入口**：真相是事件流，不是 daily_log 视图
#: （视图是事件重推出来的派生物，见 daily_log_view.py）。
SUBJECTIVE = {
    "weight_kg":     {"method": "home_scale", "conditions": {"fasted": True,
                                                             "same_scale": True}},
    "sleep_quality": {"method": "self_report", "conditions": None},
    "energy":        {"method": "self_report", "conditions": {"scale": "1-5"}},
    "soreness":      {"method": "self_report", "conditions": {"scale": "1-5"}},
    "bp_sys":        {"method": "cuff", "conditions": None},
    "bp_dia":        {"method": "cuff", "conditions": None},
}


def _subjective_obs():
    """主观事件 → 观测。method 不同即不可比，故 method 单独记。

    **按事件归属日对齐 t**（`events.jsonl` 的 date 是归属日，ts 只是录入时刻）：
    跨零点补记的事件归属前一天，若拿 ts 当 t，`substr(t,1,10)` 会把它算到录入那天。
    体重只认 `part` 缺失或 `morning`——晚间体重的正式口径从来只有晨起一次。
    """
    out = []
    if not os.path.isfile(EVENTS_LOG):
        return out
    events = eventbus.read(EVENTS_LOG)
    dead = eventbus.superseded_ids(events)
    for e in events:
        if e["_id"] in dead:
            continue
        p = e.get("payload") or {}
        metric = p.get("metric")
        if metric not in SUBJECTIVE or p.get("value") is None:
            continue
        if metric == "weight_kg" and p.get("part") not in (None, "morning"):
            continue
        try:
            value = float(p["value"])
        except (TypeError, ValueError):
            continue
        spec = SUBJECTIVE[metric]
        conds = dict(spec["conditions"] or {})
        conds["event_id"] = e["_id"]
        if e.get("ts"):
            conds["recorded_at"] = e["ts"]
        out.append(_obs(metric, value, e["date"] + "T00:00:00", source="events",
                        method=spec["method"], kind="point", coverage=1.0,
                        conditions=json.dumps(conds, ensure_ascii=False),
                        raw_ref="events.jsonl"))
    return out


# ------------------------------------------------------------------ 片段（P2）
def _bucket_points(points, interval_min):
    """[(分钟序号, 值)] → {小时: ([值], 覆盖)]}。覆盖 = 实测点数 × 采样间隔 / 60。"""
    buckets = {}
    for m, v in points or []:
        buckets.setdefault(int(m) // 60, []).append(v)
    out = {}
    for h, vals in buckets.items():
        out[h] = (vals, min(1.0, len(vals) * float(interval_min) / 60.0))
    return out


def _split_by_hour(segments):
    """睡眠分级段 → {(本地小时起点 datetime): {stage: 分钟}} 与覆盖分钟。

    **按绝对本地时间切**，不按钟面猜：睡眠 23:00–07:00 跨零点，只有分钟序号
    无法判断落在一年的哪一天。段长通常 10–15 分钟，短段只落在单个小时里；
    长段（设备偶尔给整段）按小时边界劈开，保证逐小时合计等于原段时长。
    """
    alloc, covered = {}, {}
    for seg in segments or []:
        try:
            s = datetime.datetime.strptime(seg[0], "%Y-%m-%dT%H:%M:%S")
            e = datetime.datetime.strptime(seg[1], "%Y-%m-%dT%H:%M:%S")
        except (ValueError, IndexError, TypeError):
            continue
        stage = int(seg[2])
        while s < e:
            hour = s.replace(minute=0, second=0)
            stop = min(e, hour + datetime.timedelta(hours=1))
            mins = (stop - s).total_seconds() / 60.0
            alloc.setdefault(hour, {})
            alloc[hour][stage] = alloc[hour].get(stage, 0.0) + mins
            covered[hour] = covered.get(hour, 0.0) + mins
            s = stop
    return alloc, covered


def _hour_obs(day):
    """一个 canonical 日 → 片段观测（小时粒度，method=garmin_hour）。

    **只做归组与覆盖率，不做判定**：片段层没有任何 judge/档位概念
    （`docs/p2-fragment-layer.md` 不变式 2）。日值仍由日汇总那一行裁决，
    本函数产出的一切都不回写日值。
    """
    d = day["date"]
    sub = day.get("subday") or {}
    common = dict(source="garmin", device=DEVICE, raw_ref=_raw_ref(d))
    out = []

    def add(metric, start, value, coverage, conditions=None):
        t = start.strftime("%Y-%m-%dT%H:%M:%S")
        t_end = (start + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
        out.append(_obs(metric, value, t, method=FRAG_METHOD, kind="interval",
                        t_end=t_end, coverage=round(coverage, 3),
                        conditions=json.dumps(conditions, ensure_ascii=False)
                        if conditions else None, **common))

    def hstart(hour):
        return datetime.datetime.strptime(d, "%Y-%m-%d") + datetime.timedelta(hours=hour)

    # —— 心率（2 分钟采样）：均 / 最低 / 最高 ——
    hr = _bucket_points((sub.get("hr") or {}).get("points"),
                        (sub.get("hr") or {}).get("interval_min") or 2)
    for h, (vals, cov) in sorted(hr.items()):
        add("hour_hr_bpm", hstart(h), round(sum(vals) / len(vals), 1), cov)
        add("hour_hr_min", hstart(h), min(vals), cov)
        add("hour_hr_max", hstart(h), max(vals), cov)

    # —— 压力 / 身体电量（3 分钟采样）——
    for key, metric in (("stress", "hour_stress"), ("bb", "hour_bb")):
        ser = sub.get(key) or {}
        for h, (vals, cov) in sorted(_bucket_points(
                ser.get("points"), ser.get("interval_min") or 3).items()):
            add(metric, hstart(h), round(sum(vals) / len(vals), 1), cov)

    # —— 15 分钟活动桶：步数（可切）与久坐分钟（只作相对分布）——
    act = sub.get("activity") or {}
    per_hour = {}
    for m, steps, level in act.get("buckets") or []:
        h = int(m) // 60
        cell = per_hour.setdefault(h, {"steps": 0, "sed": 0, "n": 0})
        cell["steps"] += steps or 0
        cell["n"] += 1
        if level == "sedentary":
            cell["sed"] += 15
    for h, cell in sorted(per_hour.items()):
        cov = min(1.0, cell["n"] * float(act.get("interval_min") or 15) / 60.0)
        add("hour_steps", hstart(h), cell["steps"], cov)
        add("hour_sedentary_min", hstart(h), cell["sed"], cov)

    # —— 睡眠分级（按起床日归属，conditions 里写明是哪一夜）——
    alloc, covered = _split_by_hour((sub.get("sleep") or {}).get("segments"))
    for hour, stages in sorted(alloc.items()):
        cov = min(1.0, covered.get(hour, 0.0) / 60.0)
        for stage, metric in SLEEP_STAGE_METRIC.items():
            add(metric, hour, round(stages.get(stage, 0.0), 1), cov,
                conditions={"night": d})
    return out


def _annotate_fragments(day_obs, hour_obs):
    """2c：白名单指标标记「日值可由片段聚合得出」，**绝不用片段改日值**。

    只有实测恒等的指标进白名单（`references/fragments.json` 的 whitelist）。
    恒等才标 `derived_from`；不等就什么都不做——一致性报告会把差值摆出来，
    而不是让构建悄悄按片段覆盖日汇总。
    """
    sums = {}
    for o in hour_obs:
        sums.setdefault(o["metric"], []).append(o["value"])
    by_metric = {o["metric"]: o for o in day_obs}
    for pair in fragments.whitelist():
        target = by_metric.get(pair.get("day"))
        if target is None:
            continue
        vals = []
        for m in fragments.fragments_of(pair):
            vals.extend(sums.get(m) or [])
        got = fragments.aggregate(vals, pair.get("agg"))
        if got is None:
            continue
        div = float(pair.get("divisor") or 1)
        got = round(got / div, 3)
        tol = abs(float(pair.get("tolerance") or 0))
        if abs(got - float(target["value"])) <= tol + 1e-9:
            target["derived_from"] = json.dumps(
                {"fragments": fragments.fragments_of(pair), "agg": pair.get("agg"),
                 "method": FRAG_METHOD}, ensure_ascii=False)
    return day_obs


def _inbody_obs():
    """InBody 事件型：一次扫描 = 一组点观测。跨机型不可比，method 记为机型。"""
    out = []
    if not os.path.isfile(INBODY_INDEX):
        return out
    try:
        scans = (json.load(open(INBODY_INDEX, encoding="utf-8")) or {}).get("scans") or []
    except Exception:
        return out
    mapping = (("weight_kg", "weight_kg"), ("percent_body_fat", "body_fat_pct"),
               ("fat_free_mass_kg", "ffm_kg"),
               ("skeletal_muscle_mass_kg", "skeletal_muscle_kg"),
               ("visceral_fat_level", "visceral_fat_level"))
    for s in scans:
        dt = s.get("datetime")
        if not dt:
            continue
        hh = int(dt[11:13]) if len(dt) > 13 else 12
        for src, mk in mapping:
            if s.get(src) is None:
                continue
            out.append(_obs(mk, s[src], dt, source="inbody",
                            method="bia_%s" % (s.get("equipment") or "?").lower(),
                            device=s.get("equipment"), kind="point", coverage=1.0,
                            conditions=json.dumps({"fasted": False,
                                                   "time_of_day": "morning" if hh < 10
                                                   else "evening" if hh >= 18 else "afternoon"}),
                            raw_ref=os.path.join("data", "inbody", "raw",
                                                 "scan_%s.json" % s.get("raw_datetime"))))
    return out


# ------------------------------------------------------------------ 闸门
def _gates(dates):
    """data_gate 是该日完整性的唯一判据，原样落到 obs_day_gate 供断言读取。"""
    rows = []
    try:
        import data_gate
        for d in dates:
            g = data_gate.inspect(d)
            rows.append({"date": d, "level": g.get("level"),
                         "cutoff_min": g.get("cutoff_min"),
                         "steps": g.get("steps"), "note": None})
    except Exception as e:                     # 闸门不可用不阻断物化
        for d in dates:
            rows.append({"date": d, "level": "unknown", "cutoff_min": None,
                         "steps": None, "note": str(e)})
    return rows


# ------------------------------------------------------------------ 主流程
def build(since=None, quiet=False):
    rules = db.load_rules()
    raw = sorted(f[:-5] for f in os.listdir(RAW_DIR) if f.endswith(".json"))
    if not raw:
        raise SystemExit("data/raw 下没有原始数据")
    first, last = raw[0], raw[-1]
    span = (datetime.date.fromisoformat(last)
            - datetime.date.fromisoformat(first)).days + 1
    if not quiet:
        print("canon: %s ~ %s（%d 天跨度，%d 个 raw）" % (first, last, span, len(raw)))

    bundle = get_adapter("garmin-cn").collect(last, span)
    days = bundle.get("days") or []

    obs, frags = [], []
    for day in days:
        day_obs = _day_obs(day, rules)
        hour_obs = _hour_obs(day)
        frags.extend(hour_obs)
        obs.extend(_annotate_fragments(day_obs, hour_obs))
        obs.extend(hour_obs)
    obs.extend(_subjective_obs())
    obs.extend(_inbody_obs())
    grades = []
    for day in days:
        grades.extend(_day_grades(day))

    con = db.connect()
    con.execute("DELETE FROM obs")
    db.upsert_obs(con, obs)
    con.execute("DELETE FROM grades")
    db.upsert_grades(con, grades)
    con.executemany(
        "INSERT OR REPLACE INTO obs_day_gate(date,level,cutoff_min,steps,note) "
        "VALUES (:date,:level,:cutoff_min,:steps,:note)", _gates(raw))
    con.commit()
    if not quiet:
        print("obs: %d 条（%d 个指标，其中片段 %d 条）· grades: %d 条（%d 种设备分档）"
              % (len(obs), len({o["metric"] for o in obs}), len(frags), len(grades),
                 len({g["metric"] for g in grades})))

    n_units = units_mod.rebuild(con, rules, quiet=True)
    if not quiet:
        print("units: %d 行" % n_units)
    n_assert = assert_mod.rebuild(con, rules)

    db.set_meta(con, "rules_hash", db.rules_hash())
    db.set_meta(con, "engine_ver", db.engine_ver())
    db.set_meta(con, "data_hash", db.data_hash(con))
    db.set_meta(con, "built_at", datetime.datetime.now().isoformat(timespec="seconds"))
    db.set_meta(con, "span", "%s~%s" % (first, last))
    con.commit()

    stats = {"obs": len(obs), "units": n_units, "assertions": n_assert,
             **{k: v for k, v in db.get_meta(con).items()}}
    if not quiet:
        print("指纹:", json.dumps({k: stats[k] for k in
                                  ("rules_hash", "data_hash", "engine_ver")}))
    return stats


def cmd_build(a):
    st = build(a.since, quiet=False)
    print(json.dumps(st, ensure_ascii=False, indent=1))
    return 0


def cmd_stats(a):
    con = db.connect()
    print("%-12s %s" % ("表", "行数"))
    for t in ("obs", "units", "assertions", "obs_day_gate"):
        n = con.execute("SELECT COUNT(*) c FROM %s" % t).fetchone()["c"]
        print("%-12s %d" % (t, n))
    print()
    for k, v in db.get_meta(con).items():
        print("%-12s %s" % (k, v))
    print("\n按尺度：")
    for r in con.execute("SELECT scale, COUNT(*) c FROM units GROUP BY scale"):
        print("  %-8s %d" % (r["scale"], r["c"]))
    return 0


def cmd_verify(a):
    con = db.connect()
    bad = 0

    def chk(name, sql, expect_zero=True):
        nonlocal bad
        n = con.execute(sql).fetchone()["c"]
        flag = "OK " if (n == 0) == expect_zero else "✗ "
        if (n == 0) != expect_zero:
            bad += 1
        print("%s%-46s %d" % (flag, name, n))

    chk("观测值不得为 NULL",
        "SELECT COUNT(*) c FROM obs WHERE value IS NULL")
    chk("单元 n 不得超过 expected",
        "SELECT COUNT(*) c FROM units WHERE n > expected")
    chk("单元 coverage 必须在 (0,1]",
        "SELECT COUNT(*) c FROM units WHERE coverage<=0 OR coverage>1.0001")
    chk("delta_verdict 必须是五档之一",
        "SELECT COUNT(*) c FROM units WHERE delta_verdict IS NOT NULL AND "
        "delta_verdict NOT IN ('changed','within-noise','insufficient',"
        "'uncalibrated','suppressed')")
    chk("触发断言必须带 level 与 evidence",
        "SELECT COUNT(*) c FROM assertions WHERE verdict=1 AND "
        "(level IS NULL OR evidence IS NULL OR evidence='{}')")
    chk("未触发断言不得带 level",
        "SELECT COUNT(*) c FROM assertions WHERE verdict=0 AND level IS NOT NULL")
    chk("断言引用不存在的单元（跨尺度断言除外）",
        "SELECT COUNT(*) c FROM assertions a WHERE a.scale!='day' AND NOT EXISTS "
        "(SELECT 1 FROM units u WHERE u.scale=a.scale AND u.key=a.key)")
    # 策略强制：reference_policy.must_report_ruler=true —— 凡 rule_ref 含 "ref:" 的断言，
    # evidence 必须带 ruler，否则「今天用的是哪把尺子」无法回答。
    _pol = (json.load(open(metricdefs.METRICS_PATH, encoding="utf-8")) or {}).get(
        "reference_policy") or {}
    if _pol.get("must_report_ruler"):
        rows = [r for r in con.execute(
            "SELECT id, date, evidence FROM assertions WHERE rule_ref LIKE '%ref:%'")]
        missing = [r for r in rows if "ruler" not in (json.loads(r["evidence"] or "{}"))]
        print("%s%-46s %d" % ("OK " if not missing else "✗ ",
                              "动态阈值断言必须标注 ruler", len(missing)))
        if missing:
            bad += 1
            for r in missing[:5]:
                print("     ✗ %s %s" % (r["date"], r["id"]))

    chk("units 里出现未登记的指标",
        "SELECT COUNT(*) c FROM (SELECT DISTINCT metric FROM units) u WHERE u.metric "
        "NOT IN (%s)" % ",".join("'%s'" % m for m in metricdefs.registry()))

    # —— 片段层的三条结构性约束（P2）——
    # 判定不下放不是靠自觉：hour / daypart 单元一旦带上 judge 或 delta_verdict，
    # 就等于把「红了还是绿了」下放到小时级——那是 vision §三 明确禁止的。
    _frag_in = ",".join("'%s'" % s for s in fragments.FRAGMENT_SCALES)
    chk("片段单元不得有判定（judge 必须为空）",
        "SELECT COUNT(*) c FROM units WHERE scale IN (%s) AND judge IS NOT NULL" % _frag_in)
    chk("片段单元不得有变化结论（delta_verdict 必须为空）",
        "SELECT COUNT(*) c FROM units WHERE scale IN (%s) AND delta_verdict IS NOT NULL"
        % _frag_in)
    chk("片段观测 coverage 必须在 (0,1]",
        "SELECT COUNT(*) c FROM obs WHERE metric LIKE 'hour\\_%%' ESCAPE '\\' "
        "AND (coverage IS NULL OR coverage<=0 OR coverage>1.0001)")
    chk("片段指标不得出现在日及以上尺度",
        "SELECT COUNT(*) c FROM units WHERE metric LIKE 'hour\\_%%' ESCAPE '\\' "
        "AND scale NOT IN (%s)" % _frag_in)

    # 指标定义完整性：改 rules.json 键名、删键、改区间形态，都会在这里报出来。
    # 这是「定义唯一来源」真正兑现的地方——以前这类错误会静默变成「该指标没有达标判定」。
    rules = db.load_rules()
    broken = metricdefs.validate(rules)
    print("%s%-46s %d" % ("OK " if not broken else "✗ ", "指标定义表达式全部可解析", len(broken)))
    for m, f, why in broken:
        print("     ✗ %-22s %-10s %s" % (m, f, why))
    if broken:
        bad += 1
    fk = {}
    for m, d in metricdefs.registry().items():
        k = d.get("frontend_key")
        if k:
            fk.setdefault(k, []).append(m)
    dup = {k: v for k, v in fk.items() if len(v) > 1}
    print("%s%-46s %d" % ("OK " if not dup else "✗ ", "前端 key 无重复", len(dup)))
    if dup:
        bad += 1
        for k, v in dup.items():
            print("     ✗ %s ← %s" % (k, ",".join(v)))
    print("\n%s（%d 项不通过）" % ("全部通过" if bad == 0 else "有异常", bad))
    return 0 if bad == 0 else 1


def main():
    ap = argparse.ArgumentParser(description="物化索引：raw → obs → units → assertions")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build")
    p.add_argument("--since", default=None, help="起始日（当前实现仍全量重算，参数留作增量接口）")
    p.set_defaults(func=cmd_build)
    sub.add_parser("stats").set_defaults(func=cmd_stats)
    sub.add_parser("verify").set_defaults(func=cmd_verify)
    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
