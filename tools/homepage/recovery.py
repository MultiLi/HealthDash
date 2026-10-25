# -*- coding: utf-8 -*-
"""恢复层视图装配：任一日晨间快照（昨夜睡眠细分 / HRV / 血氧 / 睡眠心率 / 准备度）。

纪律：只消费 canonical（adapters 唯一产出）。这里只做：
文案映射（分档评语）、睡眠结构轴相对布局、曲线抽样。零源解析、零阈值。
"""
from homepage.timeutil import fmt_sleep, downsample

_Q_MAP = {"EXCELLENT": "极佳", "GOOD": "良好", "FAIR": "一般", "POOR": "较差"}
_K_MAP = {"totalDuration": "总时长", "deepPercentage": "深睡占比",
          "lightPercentage": "浅睡占比", "remPercentage": "REM 占比",
          "awakeCount": "清醒次数", "restlessness": "翻身 restless",
          "stress": "睡眠压力"}


def _readiness_fb(score):
    """按分数分档生成文案（单调一致）；不照搬 Garmin feedbackShort——
    其码值与分数不成单调关系（实测 92 分 WELL_RECOVERED、75 分反而
    EXCELLENT_RECOVERY，直译会出现"75 分恢复极佳"）。"""
    if score >= 90:
        return "恢复极佳"
    if score >= 80:
        return "恢复充分"
    if score >= 70:
        return "恢复良好"
    if score >= 50:
        return "恢复一般"
    return "恢复不足"


def _recovery_label(rt):
    if rt is None or rt <= 2:
        return "预计已充分恢复"
    if rt < 60:
        return "预计还需 %s 分钟恢复" % rt
    return "预计还需 %.1f 小时恢复" % (rt / 60.0)


def _sleep_view(sl):
    h, m = fmt_sleep(sl["duration_s"])
    stages = {k: round((sl.get(k) or 0) / 60)
              for k in ("deep_s", "light_s", "rem_s", "awake_s")}
    total_s = sum(sl.get(k) or 0 for k in ("deep_s", "light_s", "rem_s")) or 1
    # 睡眠结构轴：canonical levels [[start,end,lvl]] -> 相对入睡起点的分钟布局
    start_min = None
    hypno = []
    for st, en, lvl in sl.get("levels") or []:
        cur = int(st[:2]) * 60 + int(st[3:])
        end = int(en[:2]) * 60 + int(en[3:])
        if start_min is None:
            start_min = cur
        while cur < start_min:
            cur += 1440
        while end < cur:
            end += 1440
        hypno.append([cur - start_min, end - start_min, lvl])
    return {
        "h": h, "m": m, "seconds": sl["duration_s"],
        "score": sl.get("score"),
        "bed": sl.get("start_local"), "wake": sl.get("end_local"),
        "stages": {"deep_min": stages["deep_s"], "light_min": stages["light_s"],
                   "rem_min": stages["rem_s"], "awake_min": stages["awake_s"]},
        "stage_pct": {"deep": round((sl.get("deep_s") or 0) / total_s * 100),
                      "light": round((sl.get("light_s") or 0) / total_s * 100),
                      "rem": round((sl.get("rem_s") or 0) / total_s * 100)},
        "sub_scores": [{"k": _K_MAP.get(s["key"], s["key"]),
                        "q": _Q_MAP.get(s["qualifier"], s["qualifier"])}
                       for s in sl.get("sub_scores") or []
                       if s["key"] != "overall"],
        "awake_count": sl.get("awake_count"),
        "restless": sl.get("restless"),
        "resp": sl.get("resp"),
        "breathing": {"LOW": "低", "MEDIUM": "中", "HIGH": "高"}.get(
            sl.get("breathing"), sl.get("breathing")),
        "sleep_stress": sl.get("sleep_stress"),
        "need_h": round((sl.get("need_baseline_min") or 420) / 60),
        "nap_min": sl.get("nap_min") or 0,
        "hypno": hypno,
    }


def hrv_view(hrv):
    """canonical hrv（last_night_avg/baseline_*）或视图 hrv（value/low/high）
    → 统一视图形状 {value, weekly_avg, low, high, status}；无实测值返回 None。

    **HRV 字段归一的唯一入口**：展示（build_recovery）与评分（scoring）共用。
    2026-09-12 修：此前 scoring 直接读 canonical 却按视图键名取值，实测值恒为
    None，评分被设备 status 短路成 95 分——两套键名各认一套是事故根因。
    """
    if not hrv:
        return None
    v = hrv.get("value")
    if v is None:
        v = hrv.get("last_night_avg")
    if v is None:
        return None
    lo = hrv.get("low")
    if lo is None:
        lo = hrv.get("baseline_low")
    hi = hrv.get("high")
    if hi is None:
        hi = hrv.get("baseline_high")
    return {"value": v, "weekly_avg": hrv.get("weekly_avg"),
            "low": lo, "high": hi, "status": hrv.get("status")}


def _ctx_cn(ctx):
    return {"AFTER_WAKEUP_RESET": "晨起", "AFTER_POST_EXERCISE_RESET": "运动后",
            "UPDATE_REALTIME_VARIABLES": "实时"}.get(ctx, "重算")


def build_recovery(canon):
    """canonical 日 -> 晨间恢复视图。字段缺失容忍。"""
    out = {"date_label": "%s/%s" % (canon["date"][5:7], canon["date"][8:10])}

    rd = canon.get("readiness")
    if rd and rd.get("score") is not None:
        out["readiness"] = {"score": rd["score"], "level": rd.get("level"),
                            "feedback": _readiness_fb(rd["score"]),
                            "recovery": _recovery_label(rd.get("recovery_min"))}
        series = canon.get("readiness_series") or []
        if len(series) > 1:
            # 日内重算轨迹：多个快照各回答不同问题，只给单点会误导
            out["readiness"]["series"] = [
                [s["time_local"], s["score"], _ctx_cn(s.get("input_context"))]
                for s in series]

    sl = canon.get("sleep")
    if sl and sl.get("duration_s"):
        out["sleep"] = _sleep_view(sl)

    hv = hrv_view(canon.get("hrv"))
    if hv:
        hv["curve"] = downsample(
            (canon.get("hrv") or {}).get("readings") or [], 3)
        out["hrv"] = hv

    spo2 = canon.get("spo2")
    if spo2 and spo2.get("sleep_avg") is not None:
        # 展示口径与引擎判读一致：睡眠期均值（归属醒来那天），非日间均值
        out["spo2"] = {"avg": int(spo2["sleep_avg"]), "low": spo2.get("lowest"),
                       "avg7": spo2.get("avg7"),
                       "curve": sorted([["%02d:00" % h, v]
                                        for h, v in (spo2.get("hourly") or {}).items()])}

    shr = (sl or {}).get("sleep_hr") or {}
    if shr.get("curve"):
        vals = [p[1] for p in shr["curve"] if p[1] is not None]
        if vals:
            out["sleep_hr"] = {"avg": shr.get("avg"), "lowest": min(vals),
                               "curve": downsample(shr["curve"], 5)}
    return out
