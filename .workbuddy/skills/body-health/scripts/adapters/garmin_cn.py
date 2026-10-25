# -*- coding: utf-8 -*-
"""Garmin Connect CN 适配器：把落盘原始数据规范化为引擎 schema。

本适配器**完全离线**：只读 sync.py 落盘的 <workspace>/data/raw/<date>.json，
不做任何网络请求与鉴权（那都是 sync 层的职责）。缺某日 raw 文件时
如实记入 gaps，不编造、不回源。

时区与时间戳坑全部收敛到 gtime.py（`*TimestampLocal` 是挂钟按 UTC 存、
CN 端 stress 数组本地时间编码等），本文件禁止手搓 epoch 运算，
沙箱禁用 datetime.fromtimestamp()。
"""
import calendar
import datetime
import json
import os

from . import register
from .gtime import TZ, gmt_ms_to_local_hhmm, iso_gmt_to_hhmm

_HERE = os.path.dirname(os.path.abspath(__file__))            # adapters/
_SKILLS = os.path.abspath(os.path.join(_HERE, "..", ".."))    # skills/
# adapters 比 scripts 深一级，回工作区根要多一层 ..
_WORKSPACE = os.path.abspath(os.path.join(_HERE, "..", "..", "..", "..", ".."))
RAW_DIR = os.environ.get("HEALTH_RAW_DIR") or os.path.join(_WORKSPACE, "data", "raw")
TZ = 28800  # 本地 UTC+8
EPOCH = datetime.datetime(1970, 1, 1)

# 逐日 raw 键 → data 聚合键（与旧 pull() 输出形状一致，_canonical_day 无感）
_DAY_KEYS = ("sleep_daily", "hrv_daily", "stress_daily", "usersummary_daily",
             "summary_chart", "spo2_daily", "readiness_daily", "ts_daily",
             "bb_range", "stress_range")


def _day_anchor(stress_day):
    """当日本地 00:00 的 epoch 秒。CN 的 stress 数组是「本地时间当 UTC」编码：
    startTimestampGMT 字段串本身就是本地 00:00。
    无数据日（未佩戴/未同步）Garmin 仍返回一个占位 stress 表，startTimestampGMT 为
    null、stressValuesArray 为空 —— 此时返回 None，调用方按「该日无骨架」处理，
    不能让它把整个窗口炸掉（2026-10-08 实证：10-04~10-06 为占位日）。"""
    st = (stress_day or {}).get("startTimestampGMT")
    if not st:
        return None
    st = st.rstrip("Z0.")
    return calendar.timegm(
        datetime.datetime.strptime(st[:16], "%Y-%m-%dT%H:%M").utctimetuple())


def _prev_iso(iso):
    d = datetime.date.fromisoformat(iso) - datetime.timedelta(days=1)
    return d.isoformat()


def _coverage(us, iso):
    """该日**实际被覆盖到哪一分钟**（2026-10-18 建，完整性闸门的唯一数据源）。

    源口径：`wellnessEndTimeLocal` = 手表最后一次把数据推到云端的时刻，
    也就是这一天的数据终点。整日 = 次日 00:00 → cutoff_min = 1440；
    截止在当天 19:34 → cutoff_min = 1174，缺的 266 分钟一目了然。

    为什么放在适配器：这是**源特有字段及其时间口径**，按分层判据属于
    「原始字段语义」层。引擎、闸门、主页一律消费 canonical.coverage，
    任何地方再出现 `wellnessEndTimeLocal` 这个键名即为违规。
    """
    out = {"end_local": None, "cutoff_min": None,
           "last_sync_local": None, "has_data": us.get("totalSteps") is not None}
    wend = us.get("wellnessEndTimeLocal") or ""
    if len(wend) >= 16:
        try:
            dt = datetime.datetime.strptime(wend[:16], "%Y-%m-%dT%H:%M")
            out["end_local"] = dt.strftime("%Y-%m-%dT%H:%M")
            out["cutoff_min"] = ((dt.date() - datetime.date.fromisoformat(iso)).days
                                 * 1440 + dt.hour * 60 + dt.minute)
        except ValueError:
            pass
    # lastSyncTimestampGMT 是 GMT 串，仅同日拉取时才有值（历史回拉恒为 null）
    ls = us.get("lastSyncTimestampGMT")
    if isinstance(ls, str) and len(ls) >= 16:
        try:
            dt = (datetime.datetime.strptime(ls[:16], "%Y-%m-%dT%H:%M")
                  + datetime.timedelta(hours=8))
            out["last_sync_local"] = dt.strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            pass
    return out


@register("garmin-cn")
class GarminCN:
    def collect(self, date, days):
        start = (datetime.date.fromisoformat(date)
                 - datetime.timedelta(days=days - 1)).isoformat()
        window = []
        d = datetime.date.fromisoformat(start)
        while d.isoformat() <= date:
            window.append(d.isoformat())
            d += datetime.timedelta(days=1)

        files = {}
        missing = []
        for iso in window:
            p = os.path.join(RAW_DIR, f"{iso}.json")
            if os.path.isfile(p):
                with open(p) as fh:
                    files[iso] = json.load(fh)
            else:
                files[iso] = None
                missing.append(iso)

        gaps = []
        if missing:
            gaps.append("原始数据未落盘（sync 未运行或失败），以下日期各维度缺失："
                        + "、".join(missing))

        # 聚合回 pull() 时代的整体形状，_canonical_day 逻辑零改动
        data = {k: [] for k in _DAY_KEYS}
        for iso in window:
            f = files[iso]
            if not f:
                continue
            for k in _DAY_KEYS:
                v = f.get(k)
                if v:
                    data[k].append(v)

        tgt = files[date] or {}
        vo2_latest = (tgt.get("vo2_latest") or {}).get("generic")
        # 活动列表：优先 activities_own（sync --backfill 按日分派的「仅本日」记录，
        # 内容完整、不受快照时点影响）；回落 activities（目标日当场拉取的滚动窗口，
        # 历史日常有「键存在但不含本日」的情形）。两者都在 _canonical_day 里
        # 按 startTimeLocal 的日期过滤，口径一致。
        acts = tgt.get("activities_own")
        if not isinstance(acts, list) or not acts:
            acts = tgt.get("activities")
        data["activities"] = acts if isinstance(acts, list) else []
        # 每日各自的心率档案（历史日经 sync --backfill 补漏后同样具备）
        dailyhr_by_date = {iso: ((files[iso] or {}).get("dailyhr") or {})
                           for iso in window}

        # 血氧：按目标日 raw 单点判断（不再让历史空日拉低整窗口）。
        # 端点：wellness-service/wellness/daily/spo2/{date}
        # 源报告口径（在此归一化，canonical 不暴露「跨夜」概念）：
        #   Garmin 把一夜睡眠拆在两个日表里——醒来的那个日表有 avgSleepSpO2，
        #   前一晚的日表把同一晚标为 avgTomorrowSleepSpO2。canonical 约定：
        #   一夜睡眠归属「醒来的那天」（与 sleep_daily 的 bucket 一致）。
        #   因此 D 日的 sleep_avg = D 日 avgSleepSpO2，缺则回退 D-1 日
        #   avgTomorrowSleepSpO2（同一晚）。
        # 字段：averageSpO2（全窗口均值）/ lowestSpO2（瞬时最低）/
        #       spO2HourlyAverages（[[ms, value], ...]）。
        def _spo2_rec(iso):
            for x in data.get("spo2_daily", []):
                if x.get("calendarDate") == iso:
                    return x
            return {}
        _rec_t, _rec_y = _spo2_rec(date), _spo2_rec(_prev_iso(date))
        _tgt_spo2 = _rec_t
        _has_spo2_today = any(
            _tgt_spo2.get(k) is not None
            for k in ("avgSleepSpO2", "averageSpO2", "lowestSpO2",
                      "avgTomorrowSleepSpO2")) or \
            _rec_y.get("avgTomorrowSleepSpO2") is not None
        if not _has_spo2_today:
            gaps.append("血氧：当日无采样（手表血氧开关未开启，或未跨过一晚睡眠）")

        # 活动量缺口：usersummary 为 null = 当天没戴表/没同步，不是零活动
        miss = [x.get("calendarDate") for x in data.get("usersummary_daily", [])
                if x.get("totalSteps") is None]
        if miss:
            gaps.append("活动量数据缺失（未佩戴或未同步），不可当作零活动："
                        + "、".join(miss))

        out_days = []
        # 逐日规范化：以 stress_range（含 highStressDuration）为逐日骨架
        seen = set()
        for item in data["stress_range"]:
            cd = item.get("calendarDate")
            if cd and cd in window and cd not in seen:
                seen.add(cd)
                out_days.append(self._canonical_day(
                    cd, data, dailyhr_by_date.get(cd) or {}, vo2_latest))
        for iso in window:                       # stress_range 缺日兜底
            if iso not in seen:
                out_days.append(self._canonical_day(
                    iso, data, dailyhr_by_date.get(iso) or {}, vo2_latest))
        return {"date": date, "source": "garmin-cn", "gaps": gaps,
                "days": sorted(out_days, key=lambda x: x["date"]),
                "synced_at": (files[date] or {}).get("synced_at") or ""}

    # ------------------------------------------------ 规范化 ----
    def _canonical_day(self, d, data, dailyhr, vo2_latest):
        day = {"date": d}
        sleep_parent = next((x for x in data["sleep_daily"]
                             if (x.get("dailySleepDTO") or {}).get("calendarDate") == d), {})
        sleep = sleep_parent.get("dailySleepDTO") or {}
        if sleep:
            stages = {"deep_s": sleep.get("deepSleepSeconds"),
                      "light_s": sleep.get("lightSleepSeconds"),
                      "rem_s": sleep.get("remSleepSeconds"),
                      "awake_s": sleep.get("awakeSleepSeconds")}
            # 睡眠结构时间轴：activityLevel 0=深睡 1=浅睡 2=REM 3=清醒
            # 注意：sleepLevels / sleepHeartRate / sleepBodyBattery /
            # restlessMomentsCount 挂在 sleep_daily 顶层，不在 dailySleepDTO 里
            levels = [[iso_gmt_to_hhmm(sg.get("startGMT")),
                       iso_gmt_to_hhmm(sg.get("endGMT")),
                       int(sg.get("activityLevel") or 0)]
                      for sg in (sleep_parent.get("sleepLevels") or [])]
            levels = [x for x in levels if x[0] and x[1]]
            sh_vals = [p["value"] for p in (sleep_parent.get("sleepHeartRate") or [])
                       if p.get("value") is not None]
            sh_curve = [[gmt_ms_to_local_hhmm(p.get("startGMT")),
                         p.get("value")]
                        for p in (sleep_parent.get("sleepHeartRate") or [])]
            day["sleep"] = {
                "start_local": gmt_ms_to_local_hhmm(sleep.get("sleepStartTimestampGMT")),
                "end_local": gmt_ms_to_local_hhmm(sleep.get("sleepEndTimestampGMT")),
                "duration_s": sleep.get("sleepTimeSeconds"),
                "score": (sleep.get("sleepScores") or {}).get("overall", {}).get("value"),
                "need_min": ((sleep.get("nextSleepNeed") or {}).get("actual")),
                "need_baseline_min": ((sleep.get("sleepNeed") or {}).get("baseline")),
                # 设备自己算的**推荐入睡窗**（分钟制）。它是随作息漂移的相对量，
                # 判读只用「实际入睡 − 窗止」的偏移，不拿窗口本身当达标线。
                "bed_window": {"start": (sleep.get("nextSleepNeed") or {}).get(
                                   "recommendedBedtimeStartMins"),
                               "end": (sleep.get("nextSleepNeed") or {}).get(
                                   "recommendedBedtimeEndMins")},
                # 设备对昨晚的总评（SLEEP_SCORE 的 feedback，如 POSITIVE_DEEP /
                # NEGATIVE_LATE），与 sub_scores 的八项分档一起进 grades 表。
                "score_feedback": sleep.get("sleepScoreFeedback"),
                "awake_count": sleep.get("awakeCount"),
                "restless": sleep_parent.get("restlessMomentsCount"),
                "resp": {"avg": sleep.get("averageRespirationValue"),
                         "low": sleep.get("lowestRespirationValue"),
                         "high": sleep.get("highestRespirationValue")},
                "breathing": sleep.get("breathingDisruptionSeverity"),
                "sleep_stress": sleep.get("avgSleepStress"),
                "nap_min": round((sleep.get("napTimeSeconds") or 0) / 60),
                "sub_scores": [{"key": k, "qualifier": (v or {}).get("qualifierKey")}
                               for k, v in (sleep.get("sleepScores") or {}).items()
                               if isinstance(v, dict) and v.get("qualifierKey")],
                "levels": levels,
                "sleep_hr": {"avg": sleep.get("avgHeartRate"),
                             "lowest": min(sh_vals) if sh_vals else None,
                             "curve": sh_curve},
            }
            day["sleep"].update({k: v for k, v in stages.items() if v is not None})
        hrv = next((x["hrvSummary"] for x in data["hrv_daily"]
                    if x["hrvSummary"]["calendarDate"] == d), {})
        if hrv and hrv.get("lastNightAvg") is not None:
            base = hrv.get("baseline") or {}
            # hrvReadings 挂在 hrv_daily 顶层（hrvSummary 的兄弟键）
            hrv_parent = next((x for x in data["hrv_daily"]
                               if (x.get("hrvSummary") or {}).get("calendarDate") == d), {})
            readings = [[iso_gmt_to_hhmm(r.get("readingTimeGMT")),
                         r.get("hrvValue")]
                        for r in (hrv_parent.get("hrvReadings") or [])]
            day["hrv"] = {"last_night_avg": hrv["lastNightAvg"],
                          "baseline_low": base.get("balancedLow"),
                          "baseline_high": base.get("balancedUpper"),
                          "status": hrv.get("status"),
                          "weekly_avg": hrv.get("weeklyAvg"),
                          "readings": [p for p in readings if p[0] and p[1] is not None]}
        rhr = dailyhr.get("restingHeartRate")
        if rhr is not None:
            day["rhr"] = {"value": rhr,
                          "avg7": dailyhr.get("lastSevenDaysAvgRestingHeartRate")}
        stress_day = next((x for x in data["stress_daily"]
                           if x.get("calendarDate") == d), {})
        _stress_anchor = _day_anchor(stress_day)
        if stress_day and _stress_anchor is not None:
            anchor = _stress_anchor
            hourly = {}
            for t, v in stress_day.get("stressValuesArray") or []:
                if v >= 0:
                    hourly.setdefault((t // 1000 - anchor) // 3600, []).append(v)
            import statistics
            # highStressDuration（秒）在 stress_range 的 values 里，不在 stress_daily
            sr = next((x for x in data["stress_range"]
                       if x.get("calendarDate") == d), {})
            high_sec = ((sr.get("values") or {})
                        .get("highStressDuration")) or 0
            _sv = sr.get("values") or {}
            day["stress"] = {
                "hourly": {h: round(statistics.mean(v)) for h, v in hourly.items()},
                "high_min": high_sec // 60,
                "avg": stress_day.get("avgStressLevel"),
                "max": stress_day.get("maxStressLevel"),
                "overall": _sv.get("overallStressLevel"),
                "tiers_min": {k: round((_sv.get(k + "StressDuration") or 0) / 60)
                              for k in ("rest", "low", "medium", "high")}}
        # 血氧（手表端开关未开时全部 null，属正常；开了才有非 null）。
        # spO2HourlyAverages 是 [[ms_timestamp, value], ...] 而非 dict；
        # ms 时间戳锚 UTC，本地 UTC+8，按 ((ms/1000)+TZ)//3600 % 24 取小时。
        # 睡眠期均值按「归属醒来那天」归一化：D 日 avgSleepSpO2 优先，
        # 缺则用 D-1 日 avgTomorrowSleepSpO2（同一晚的另一侧口径）。
        spo2 = next((x for x in data.get("spo2_daily", [])
                     if x.get("calendarDate") == d), {})
        spo2_prev = next((x for x in data.get("spo2_daily", [])
                          if x.get("calendarDate") == _prev_iso(d)), {})
        if spo2 or spo2_prev:
            avg_s = spo2.get("avgSleepSpO2")
            if avg_s is None:
                avg_s = spo2_prev.get("avgTomorrowSleepSpO2")
            avg_d = spo2.get("averageSpO2")
            lowest = spo2.get("lowestSpO2")
            if any(v is not None for v in (avg_s, avg_d, lowest)):
                raw_hr = spo2.get("spO2HourlyAverages") or []
                hourly = {}
                for pair in raw_hr:
                    if not (isinstance(pair, (list, tuple)) and len(pair) >= 2):
                        continue
                    ts, val = pair[0], pair[1]
                    if ts is None or val is None:
                        continue
                    hour = ((ts // 1000) + TZ) // 3600 % 24
                    hourly.setdefault(hour, val)   # 同小时取首个有效读数
                day["spo2"] = {"sleep_avg": avg_s, "day_avg": avg_d,
                               "lowest": lowest,
                               "avg7": spo2.get("lastSevenDaysAvgSpO2"),
                               "hourly": hourly}
        # 活动量与久坐（实测值，来源 usersummary_daily + summary_chart）
        us = next((x for x in data.get("usersummary_daily", [])
                   if x.get("calendarDate") == d), None)
        if us:
            # 覆盖范围永远输出（哪怕 totalSteps 为 null）——闸门靠它区分
            # 「没戴表」与「还没同步完」，两者处置完全不同。
            day["coverage"] = _coverage(us, d)
        if us and us.get("totalSteps") is not None:
            mod = us.get("moderateIntensityMinutes") or 0
            vig = us.get("vigorousIntensityMinutes") or 0
            day["activity"] = {
                "steps": us.get("totalSteps"),
                "distance_km": (round(us["totalDistanceMeters"] / 1000, 2)
                                if us.get("totalDistanceMeters") is not None else None),
                "active_kcal": us.get("activeKilocalories"),
                "sedentary_min": (us["sedentarySeconds"] // 60
                                  if us.get("sedentarySeconds") is not None else None),
                # 三档时长用于「久坐分母」校验（四档合计 ≈ 清醒时长才算可信）
                "active_min": (us["activeSeconds"] // 60
                               if us.get("activeSeconds") is not None else None),
                "highly_active_min": (us["highlyActiveSeconds"] // 60
                                      if us.get("highlyActiveSeconds") is not None
                                      else None),
                "intensity_min": mod + vig,
                "vigorous_min": vig,
                "floors_up": us.get("floorsAscended"),
                "bb_charged": us.get("bodyBatteryChargedValue"),
                "bb_drained": us.get("bodyBatteryDrainedValue"),
            }
            sc = next((x for x in data.get("summary_chart", [])
                       if x.get("calendarDate") == d), {})
            grid = sc.get("values") or []
            hourly_steps = {}
            for g in grid:
                hhmm = iso_gmt_to_hhmm(g.get("startGMT"))
                if hhmm:
                    h = int(hhmm[:2])
                    hourly_steps[h] = hourly_steps.get(h, 0) + (g.get("steps") or 0)
            if us.get("bmrKilocalories") is not None:
                day["activity"]["bmr_kcal"] = round(us["bmrKilocalories"])
                day["activity"]["total_kcal"] = round(
                    us.get("totalKilocalories") or 0)
            if hourly_steps:
                day["activity"]["hourly_steps"] = [[h, hourly_steps.get(h, 0)]
                                                   for h in range(24)]
            if grid:
                best = cur = 0
                for g in grid:                       # 15 分钟一格
                    if g.get("primaryActivityLevel") == "sedentary":
                        cur += 15
                        best = max(best, cur)
                    else:
                        cur = 0
                day["activity"]["sedentary_max_block_min"] = best
        bb = next((x for x in data["bb_range"] if x["calendarDate"] == d), {})
        if bb:
            sbb = (sleep_parent.get("sleepBodyBattery") or [])
            bv = [p["value"] for p in sbb if p.get("value") is not None]
            day["bb"] = {"low": (bb.get("values") or {}).get("lowBodyBattery"),
                         "high": (bb.get("values") or {}).get("highBodyBattery"),
                         "night": ({"start": bv[0], "end": bv[-1],
                                    "delta": bv[-1] - bv[0]} if bv else None)}
        if dailyhr.get("heartRateValues"):
            anchor = _day_anchor(stress_day) if stress_day else None
            day["hr_hourly"] = self._hourly_hr(dailyhr, anchor)
            if anchor:
                # 分钟级心率序列 [minute_of_day, bpm]：Z2 等区间分钟统计
                # 需要样本分辨率（逐时均值的平滑会抹掉区间内进出的时段）
                day["hr_series"] = [
                    [(t // 1000 - anchor) // 60, v]
                    for t, v in dailyhr["heartRateValues"]
                    if v is not None]
            day["hr_range"] = {"min": dailyhr.get("minHeartRate"),
                               "max": dailyhr.get("maxHeartRate")}
            # 采样节奏是设备/源属性，随 canonical 声明，引擎不硬编码
            day["hr_sample_interval_min"] = 2
        # readiness_daily = {"calendarDate": D, "values": [rec, ...]}——一天多条
        # 快照（晨起 AFTER_WAKEUP_RESET / 运动后 AFTER_POST_EXERCISE_RESET /
        # 实时 UPDATE_*），各回答不同问题：多观测并存、不互相覆盖。
        recs = [x for x in data["readiness_daily"]
                if isinstance(x, dict) and x.get("calendarDate") == d
                and x.get("values")]
        series = [{"score": r.get("score"), "level": r.get("level"),
                   "recovery_min": r.get("recoveryTime"),
                   "input_context": r.get("inputContext"),
                   "time_local": (r.get("timestampLocal") or "")[11:16] or None}
                  for x in recs for r in x["values"] if r.get("score") is not None]
        series.sort(key=lambda s: s["time_local"] or "")
        if series:
            # 主快照 = 晨起恢复（判读恢复语义）；日间重算进 readiness_series
            wake = next((s for s in series
                         if s["input_context"] == "AFTER_WAKEUP_RESET"), series[0])
            day["readiness"] = {"level": wake["level"], "score": wake["score"],
                                "recovery_min": wake["recovery_min"]}
            if len(series) > 1:
                day["readiness_series"] = series
        ts = next((x for x in data["ts_daily"] if x.get("calendarDate") == d), {})
        ts_day = next(iter(ts.get("latestTrainingStatusData", {}).values()), {})
        acute = ts_day.get("acuteTrainingLoadDTO") or {}
        if acute:
            day["training"] = {"acwr": acute.get("dailyAcuteChronicWorkloadRatio"),
                               "acute_load": acute.get("dailyTrainingLoad")}
        day_acts = [x for x in data.get("activities", [])
                    if (x.get("startTimeLocal") or "").startswith(d)]
        if day_acts:
            day["activities"] = [
                {"start_local": (a.get("startTimeLocal") or "")[11:16],
                 "name": a.get("activityName"),
                 "type": a.get("activityType", {}).get("typeKey"),
                 "location": a.get("locationName"),
                 "minutes": (a.get("duration") or 0) // 60,
                 "distance_km": (round(a["distance"] / 1000, 2)
                                 if a.get("distance") is not None else None),
                 "avg_hr": a.get("averageHR"),
                 "max_hr": a.get("maxHR"),
                 "steps": a.get("steps"),
                 "training_effect": a.get("aerobicTrainingEffect")}
                for a in day_acts]
        if vo2_latest and vo2_latest.get("calendarDate") == d:
            day["vo2max"] = {"value": vo2_latest.get("vo2MaxValue"),
                             "updated": vo2_latest.get("calendarDate")}
        return day

    @staticmethod
    def _hourly_hr(dailyhr, anchor):
        import statistics
        if anchor is None:
            return {}
        hours = {}
        for t, v in dailyhr.get("heartRateValues") or []:
            if v is None:
                continue
            hours.setdefault((t // 1000 - anchor) // 3600, []).append(v)
        return {h: {"avg": round(statistics.mean(v)), "max": max(v), "n": len(v)}
                for h, v in hours.items()}
