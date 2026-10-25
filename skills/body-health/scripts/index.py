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
from adapters import get_adapter                           # noqa: E402
from daily_brief import compute_z2, detect_exercise        # noqa: E402

RAW_DIR = os.path.join(_WORKSPACE, "data", "raw")
INBODY_INDEX = os.path.join(_WORKSPACE, "data", "inbody", "index.json")
DAILY_LOG = os.path.join(_WORKSPACE, "daily_log.jsonl")
DEVICE = "garmin-venu4"


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


def _subjective_obs():
    """daily_log：晨起空腹体重与睡眠自评。方法不同即不可比，故 method 单独记。"""
    out = []
    if not os.path.isfile(DAILY_LOG):
        return out
    with open(DAILY_LOG, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            d = row.get("date")
            if not d:
                continue
            t = d + "T00:00:00"
            w = row.get("weight_kg")
            if w is None:
                w = row.get("weight_extra_morning_kg")
            if w is not None:
                out.append(_obs("weight_kg", float(w), t, source="daily_log",
                                method="home_scale", kind="point", coverage=1.0,
                                conditions=json.dumps({"fasted": True, "same_scale": True}),
                                raw_ref="daily_log.jsonl"))
            if row.get("sleep_quality") is not None:
                out.append(_obs("sleep_quality", float(row["sleep_quality"]), t,
                                source="daily_log", method="self_report",
                                kind="point", coverage=1.0, raw_ref="daily_log.jsonl"))
    return out


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

    obs = []
    for day in days:
        obs.extend(_day_obs(day, rules))
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
        print("obs: %d 条（%d 个指标）· grades: %d 条（%d 种设备分档）"
              % (len(obs), len({o["metric"] for o in obs}), len(grades),
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
