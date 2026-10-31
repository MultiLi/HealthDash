#!/usr/bin/env python3
"""body-health 每日健康简报引擎：规范化指标 → 依 rules.json 判读 → 产出 brief。

引擎与数据源彻底解耦：
  1. 数据先由 sync.py 幂等采集落盘 <workspace>/data/raw/<date>.json
     （网络请求与鉴权只发生在 sync 层）；适配器 adapters/<source>.py
     离线读盘，输出规范化 schema（契约见 adapters/__init__.py docstring）。
     引擎不 import 任何源 SDK，不做 epoch 运算，不认识源字段名，零网络。
  2. 判读阈值/用户生理档案全部在 references/rules.json。

RUNNER：
  # 第一步：采集落盘（幂等，历史日已落盘自动跳过；失败时引擎会用缓存并记缺口）
  uv run --python 3.11 --with curl_cffi --with garth python <SKILL_DIR>/scripts/sync.py \
      [--date YYYY-MM-DD] [--days N]
  # 第二步：离线出报告
  uv run --python 3.11 python <SKILL_DIR>/scripts/daily_brief.py \
      [--date YYYY-MM-DD] [--days N] [--source garmin-cn] [--out DIR]
产物：<out>/body_brief_<date>.md（人读）+ .json（机读含 canonical）。
"""
import argparse
import datetime
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adapters import get_adapter  # noqa: E402

import settings as _settings  # noqa: E402  路径唯一解析点（settings.py）

RULES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "references", "rules.json")
# 主观日志 / 报告目录一律走 settings.py。
# 2026-10-31 数据迁入 data/ 后，旧的「向上 4 级 + WS_ROOT/daily_log.jsonl」算法已失效
# （skills/body-health/scripts 向上 4 级落到 Documents，读不到任何主观数据）。
SUBJ_LOG = _settings.DAILY_LOG
OUT_DIR = _settings.REPORTS_DIR

SEVERITY = {"BAD": 0, "WARN": 1, "NEUTRAL": 2, "GOOD": 3}
ICON = {"BAD": "[红]", "WARN": "[黄]", "GOOD": "[绿]", "INFO": "[·]"}


def load_rules():
    """通用口径 rules.json + 个人档案 data/profile.json。

    个人值（max_hr / rhr_7d_avg）**不在 rules.json 里**——那份要能公开。
    档案缺失时退回 profile.example.json 并打警告，让「忘了建档案」变成可见的错，
    而不是静默用错 max_hr 去算 Z2 区间。
    """
    with open(RULES, encoding="utf-8") as fh:
        rules = json.load(fh)
    for path, is_example in ((_settings.PROFILE, False),
                             (getattr(_settings, "PROFILE_EXAMPLE", None), True)):
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                prof = json.load(fh)
        except Exception:
            continue
        if is_example:
            print(f"[warn] 未找到个人档案，退回模板 {path}；max_hr/rhr 可能不准",
                  file=sys.stderr)
        rules["profile"] = prof
        break
    return rules


def hhmm(seconds):
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m" if seconds else None


def contiguous_runs(hours):
    runs = []
    for h in sorted(hours):
        if runs and h - runs[-1][1] <= 1:
            runs[-1][1] = h
        else:
            runs.append([h, h])
    return runs


def fmt_runs(runs):
    return "、".join(f"{s:02d}–{e + 1:02d} 点" for s, e in runs)


# ---------------------------------------------------------------- 解析 ----

def detect_exercise(hr_hourly, rules, sample_interval_min=None):
    """核心窗：avg≥100 且 max≥150 的小时；强度按核心窗定级；
    前后各并入 1 小时（avg≥90）作为热身/冷身，只扩展时间跨度。
    采样间隔由适配器随 canonical 声明（源属性），rules 仅作兜底。"""
    ex = rules["exercise"]
    max_hr = rules["profile"]["max_hr"]
    keys = sorted(h for h, x in hr_hourly.items()
                  if x["avg"] >= ex["window_avg_hr_min"]
                  and x["max"] >= ex["window_max_hr_min"])
    windows = []
    for h in keys:
        if windows and h - windows[-1][1] <= ex["zone_gap_min"]:
            windows[-1][1] = h
        else:
            windows.append([h, h])
    out = []
    for w in windows:
        core = list(w)
        if w[0] - 1 in hr_hourly and hr_hourly[w[0] - 1]["avg"] >= 90:
            w[0] -= 1                      # 热身
        if w[1] + 1 in hr_hourly and hr_hourly[w[1] + 1]["avg"] >= 90:
            w[1] += 1                      # 冷身
        samples = [hr_hourly[h] for h in range(core[0], core[1] + 1)
                   if h in hr_hourly]
        dur = sum(x["n"] for x in samples) * (sample_interval_min
                                              or ex.get("sample_interval_min", 2))
        avg = round(statistics.mean([x["avg"] for x in samples]))
        mx = max(x["max"] for x in samples)
        pct = avg / max_hr * 100
        zone = next((z["name"] for z in ex["zones"]
                     if z["pct_of_max"][0] <= pct < z["pct_of_max"][1]), "?")
        out.append({"start": f"{w[0]:02d}:00", "end": f"{w[1]+1:02d}:00",
                    "avg_hr": avg, "max_hr": mx, "zone": zone,
                    "minutes": dur, "pct_max": round(pct)})
    return [w for w in out if w["minutes"] >= ex["min_window_minutes"]]


# ---------------------------------------------------------------- 判读 ----

def judge_sleep(s, rules):
    r, marks = rules["sleep"], []
    dur = s.get("duration_s") or 0
    if dur:
        dur_min = dur // 60
        if dur_min < r["min_healthy_duration_min"] - 60:
            marks.append(("BAD", f"睡眠仅 {hhmm(dur)}，比健康下限少 "
                                 f"{r['min_healthy_duration_min'] - dur_min} 分钟"))
        elif dur_min < r["min_healthy_duration_min"]:
            marks.append(("WARN", f"睡眠 {hhmm(dur)}，略低于 7h 下限"))
        else:
            marks.append(("GOOD", f"睡眠 {hhmm(dur)}，达标"))
    pct = lambda k: round(s[k] / dur * 100) if dur and s.get(k) else None
    deep, rem = pct("deep_s"), pct("rem_s")
    if deep is not None:
        lo, hi = r["deep_pct_optimal"]
        marks.append(("GOOD" if lo <= deep <= hi else "WARN",
                      f"深睡 {deep}%（合理区间 {lo}-{hi}%）"))
    if rem is not None:
        lo, hi = r["rem_pct_optimal"]
        if rem < lo:
            marks.append(("WARN", f"REM {rem}% 偏低（合理 {lo}-{hi}%），"
                                  "多与入睡过晚/前夜酒精相关"))
    bed = s.get("start_local")
    if bed:
        late = bed >= r["late_bedtime"] or bed <= "04:00"
        marks.append(("WARN" if late else "GOOD",
                      f"入睡 {bed}（阈值 {r['late_bedtime']} 后视为晚睡）"))
    return marks


def judge_hrv(h, prev_avg, rules):
    avg = h.get("last_night_avg")
    if avg is None:
        return []
    lo = h.get("baseline_low") or rules["hrv"].get("fallback_balanced_low", 51)
    hi = h.get("baseline_high") or rules["hrv"].get("fallback_balanced_high", 71)
    marks = []
    if avg < lo:
        marks.append(("WARN", f"HRV {avg} 低于个人平衡下沿 {lo}——恢复不足/负担重"))
    elif avg > hi:
        marks.append(("INFO", f"HRV {avg} 高于平衡区间 {lo}-{hi}——恢复超常"))
    else:
        marks.append(("GOOD", f"HRV {avg}，处于个人平衡区间 {lo}-{hi}"))
    if prev_avg:
        drop = (prev_avg - avg) / prev_avg * 100
        if drop >= rules["hrv"]["big_drop_pct"]:
            marks.append(("WARN", f"较前夜下降 {round(drop)}%——警惕累积疲劳或疾病前兆"))
    return marks


def judge_rhr(rhr, rules):
    if not rhr:
        return []
    v, avg7 = rhr["value"], (rhr.get("avg7")
                             or rules["profile"]["rhr_7d_avg"])
    delta = v - avg7
    if delta >= rules["rhr"]["elevated_delta_bpm"]:
        return [("WARN", f"静息心率 {v}（7日均 {avg7}，+{delta}）——前日负荷大或身体在对抗什么")]
    return [("GOOD", f"静息心率 {v}（7日均 {avg7}）正常")]


def compute_z2(hr_hourly, rules, sample_interval_min=None, hr_series=None):
    """Z2（有氧底子）暴露量。
    证据锚点：references/exercise_science.md §1-2（60-70% HRmax；单次≥45min
    才达线粒体有效刺激剂量）。
    优先样本级计算（hr_series，分钟分辨率，可给出真实进/出区时段）；
    无样本序列时回退逐时均值法（分钟数误差 ±30min，只可作趋势参考）。"""
    z = rules["z2"]
    max_hr = rules["profile"]["max_hr"]
    lo = z["pct_of_max"][0] / 100 * max_hr
    hi = z["pct_of_max"][1] / 100 * max_hr
    interval = sample_interval_min or rules["exercise"].get("sample_interval_min", 2)
    band = [round(lo), round(hi)]

    if hr_series:
        pts = sorted((m, b) for m, b in hr_series if b is not None)
        in_zone = [m for m, b in pts if lo <= b <= hi]
        minutes = len(in_zone) * interval
        # 连续段：样本间隔 ≤ 3×采样节奏 视为同一段（容忍偶发缺样）
        runs = []
        gap_tol = 3 * interval
        for m in in_zone:
            if runs and m - runs[-1]["_end"] <= gap_tol:
                runs[-1]["_end"] = m
                runs[-1]["minutes"] += interval
            else:
                runs.append({"_start": m, "_end": m, "minutes": interval})
        for r in runs:
            r["start"] = f"{r['_start'] // 60:02d}:{r['_start'] % 60:02d}"
            r["end"] = f"{r['_end'] // 60:02d}:{r['_end'] % 60:02d}"
            del r["_start"], r["_end"]
        return {"minutes": minutes, "runs": runs, "band": band,
                "resolution": "sample"}

    hours = sorted(h for h, x in hr_hourly.items()
                   if x.get("avg") is not None and lo <= x["avg"] < hi)
    def _min(h):
        n = hr_hourly[h].get("n") or max(1, round(60 / interval))
        return n * interval
    minutes = sum(_min(h) for h in hours)
    runs = [{"start": f"{s:02d}:00", "end": f"{e + 1:02d}:00",
             "minutes": sum(_min(h) for h in range(s, e + 1))}
            for s, e in contiguous_runs(hours)]
    return {"minutes": minutes, "runs": runs, "band": band,
            "resolution": "hourly"}


def judge_z2(z2, rules):
    """Z2 判读：不惩罚零（久坐基线下日日红灯是噪声），只报进账与有效剂量。"""
    if not z2:
        return []
    r = rules["z2"]
    band = f"{z2['band'][0]}–{z2['band'][1]} bpm"
    if z2["minutes"] <= 0:
        return [("INFO", f"全天心率未进入 Z2 带（{band}）——"
                         "有氧底子今天没有进账（快走即可达成，无需跑步）")]
    eff = [x for x in z2["runs"] if x["minutes"] >= r["effective_session_min"]]
    if eff:
        seg = "、".join(f"{x['start']}–{x['end']} {x['minutes']}min" for x in eff)
        return [("GOOD", f"Z2 有效刺激 {z2['minutes']} 分钟（{seg}）"
                         f"——单次 ≥{r['effective_session_min']}min 达有效剂量，"
                         "线粒体适应由这种日子累积")]
    seg = "、".join(f"{x['start']}–{x['end']} {x['minutes']}min" for x in z2["runs"])
    return [("INFO", f"Z2 暴露 {z2['minutes']} 分钟（{seg}）"
                     f"——单次满 {r['effective_session_min']}min 才达有效剂量，"
                     "今天算垫量不算主课")]


def judge_exercise(windows, training, activities, rules):
    marks = []
    if windows:
        for w in windows:
            marks.append(("INFO", f"检测到未记录运动 {w['start']}–{w['end']}，"
                                  f"avg {w['avg_hr']}/max {w['max_hr']} bpm，"
                                  f"{w['zone']}（{w['pct_max']}% HRmax）——下次记得按开始键"))
    elif activities:
        marks.append(("GOOD", "有正式活动记录"))
    else:
        marks.append(("INFO", "全天无中高强度运动窗（按心率判读）"))
    acwr = (training or {}).get("acwr")
    if acwr is not None:
        a = rules["acwr"]
        if acwr < a["under"]:
            marks.append(("WARN", f"ACWR {acwr:.2f}——明显去训练化，负荷账本接近空白"))
        elif acwr > a["over"]:
            marks.append(("WARN", f"ACWR {acwr:.2f}——过量训练风险"))
        else:
            marks.append(("GOOD", f"ACWR {acwr:.2f}，负荷节奏健康"))
    return marks


def judge_activity(a, rules):
    """活动量与久坐：用实测值（usersummary），不用 activity 列表——后者严重低估。"""
    if not a:
        return [("INFO", "无活动量数据（当天未佩戴或未同步），活动维度无法判读")]
    r, marks = rules["activity"], []
    steps = a.get("steps")
    if steps is not None:
        if steps >= r["steps_good"]:
            lv, tail = "GOOD", "，活动量达标"
        elif steps >= r["steps_low"]:
            lv, tail = "WARN", "，偏低"
        else:
            lv, tail = "BAD", "，严重不足"
        line = f"步数 {steps}"
        if a.get("distance_km") is not None:
            line += f" / {a['distance_km']} km"
        if a.get("active_kcal") is not None:
            line += f" / 活动消耗 {int(a['active_kcal'])} kcal"
        marks.append((lv, line + tail))
    sed = a.get("sedentary_min")
    if sed is not None and sed >= r["sedentary_warn_hours"] * 60:
        blk = a.get("sedentary_max_block_min")
        extra = f"，最长连续久坐 {blk} 分钟" if blk else ""
        marks.append(("WARN", f"久坐 {sed // 60}h{sed % 60:02d}m"
                              f"（阈值 {r['sedentary_warn_hours']}h）{extra}"))
    im = a.get("intensity_min")
    if im is not None:
        goal = r["intensity_daily_goal"]
        marks.append(("GOOD" if im >= goal else "WARN",
                      f"强度分钟 {im}（日均目标 {goal}，周 150）"
                      + ("，达标" if im >= goal else "，未达标")))
    ch, dr = a.get("bb_charged"), a.get("bb_drained")
    if ch is not None and dr is not None:
        net = ch - dr
        marks.append(("GOOD" if net >= 0 else "WARN",
                      f"身体电量：夜间充 {ch} / 日间耗 {dr}，"
                      f"净 {'+' if net >= 0 else ''}{net}"
                      + ("" if net >= 0 else "——入不敷出")))
    return marks


def judge_bb(bb, rules):
    lo = (bb or {}).get("low")
    if lo is None:
        return []
    if lo <= rules["bb"]["critical_low"]:
        return [("WARN", f"Body Battery 耗到 {lo}——当日消耗见底")]
    return [("GOOD", f"Body Battery 落点 {lo}，尚有余量")]


def judge_stress_cross(hr_hourly, stress_hourly, rules):
    """压力高 + 心率高 = 体力；压力高 + 心率平 = 脑力/情绪（恢复期升高不算）。"""
    thr = rules["stress"]["cross_check_hr_avg"]
    both, calm = [], []
    for h, sv in stress_hourly.items():
        hh = hr_hourly.get(h)
        if not hh:
            continue
        if sv >= 60 and hh["avg"] >= thr:
            both.append(h)
        elif sv >= 70 and hh["avg"] < 80 and h >= 9:  # 9 点前是睡眠/赖床
            calm.append(h)
    marks = []
    if both:
        marks.append(("INFO", f"{fmt_runs(contiguous_runs(both))}压力与心率同升——体力消耗主导"))
    if calm:
        marks.append(("INFO", f"{fmt_runs(contiguous_runs(calm))}高压但心率平——脑力/情绪主导"))
    return marks


def judge_readiness(rd, rules):
    if not rd:
        return []
    marks = [(("GOOD" if (rd.get("score") or 0) >= 75 else "WARN"),
              f"训练准备度 {rd.get('level')}（{rd.get('score')} 分）")]
    if rd.get("recovery_min"):
        marks.append(("INFO", f"恢复时间 {rd['recovery_min'] // 60}h"
                              f"{rd['recovery_min'] % 60:02d}m"))
    return marks


def judge_vo2(vo2, rules):
    if not vo2 or vo2.get("value") is None:
        return []
    v = vo2["value"]
    band = next((b["name"] for b in rules["vo2max_male_bands"]
                 if b["min"] <= v < b["max"]), "?")
    return [("INFO", f"VO2 Max {v}（{band}，{vo2.get('updated')} 更新）")]


def judge_spo2(spo2, rules, prev_sleep_avg=None):
    """血氧判读：单点不诊断；睡眠均值持续 < sleep_avg_warn 或最低 < lowest_warn
    提示关注。基线建立期（baseline_lookback_days 天）只报事实。"""
    if not spo2:
        return []
    r = (rules or {}).get("spo2", {})
    warn_avg = r.get("sleep_avg_warn", 92)
    warn_low = r.get("lowest_warn", 88)
    marks = []
    sleep_avg = spo2.get("sleep_avg")   # 已由适配器归一化（夜归属醒来那天）
    day_avg = spo2.get("day_avg")
    lowest = spo2.get("lowest")
    parts = []
    if sleep_avg is not None:
        parts.append(f"睡眠期均值 {sleep_avg:.0f}%")
    if day_avg is not None:
        parts.append(f"全窗口均值 {day_avg:.0f}%")
    if lowest is not None:
        parts.append(f"瞬时最低 {lowest:.0f}%")
    if parts:
        marks.append(("INFO", "血氧 " + "，".join(parts)))
    # 关注线：均值低于阈值或瞬时最低低于阈值 → 黄灯
    flags = []
    if sleep_avg is not None and sleep_avg < warn_avg:
        flags.append(f"睡眠均值 {sleep_avg:.0f}% < {warn_avg}%")
    if lowest is not None and lowest < warn_low:
        flags.append(f"瞬时最低 {lowest:.0f}% < {warn_low}%")
    if flags:
        marks.append(("WARN", "血氧需关注：" + "，".join(flags)
                      + "（单点不诊断；连续多日反复出现再就医咨询）"))
    return marks


def level_rank(marks):
    order = {"BAD": 0, "WARN": 1, "GOOD": 2, "INFO": 3}
    worst = min((order[m[0]] for m in marks), default=3)
    return {0: "BAD", 1: "WARN", 2: "GOOD", 3: "NEUTRAL"}[worst]


# ------------------------------------------------------- 交叉协同判读 ----

def load_subjective(path=None):
    """主观日志 daily_log.jsonl → {date: dict}。文件不存在返回空表，不报错。"""
    p = path or SUBJ_LOG
    out = {}
    try:
        for line in open(p):
            line = line.strip()
            if line:
                rec = json.loads(line)
                out[rec.get("date")] = rec
    except FileNotFoundError:
        pass
    return out


def _get(d, *keys):
    """逐层取嵌套字段，缺则 None。"""
    for k in keys:
        d = (d or {}).get(k)
        if d is None:
            return None
    return d


def cross_analysis(day, days, windows, subj_today, subj_hist, rules):
    """交叉协同判读：单指标明细之上的增量。每条规则回答一个
    「指标 A 和指标 B 放在一起，说明了单看任何一个都看不出的什么」。"""
    r = rules["cross"]
    sleep_r = rules["sleep"]
    out = []
    s = day.get("sleep") or {}
    act = day.get("activity") or {}
    hrv = day.get("hrv") or {}
    rhr = day.get("rhr") or {}
    stress = day.get("stress") or {}
    bb = day.get("bb") or {}

    # 1. 睡眠债 × 自主神经：从最近一夜往回数连续短睡（streak 被_recent 夜打断就得承认）
    nights = [d for d in days if d["date"] <= day["date"]][-sleep_r["debt_lookback_days"] - 1:]
    def _short(d):
        dur = _get(d, "sleep", "duration_s") or 0
        return d.get("sleep") and dur // 60 < sleep_r["min_healthy_duration_min"]
    # 最新一夜在前
    flags = [_short(d) for d in reversed(nights)]
    streak = 0
    for f in flags:
        if f:
            streak += 1
        else:
            break
    hrv_low = (hrv.get("last_night_avg") is not None
               and hrv.get("baseline_low") is not None
               and hrv["last_night_avg"] < hrv["baseline_low"])
    rhr_up = rhr.get("value") and rhr.get("avg7") \
        and rhr["value"] - rhr["avg7"] >= rules["rhr"]["elevated_delta_bpm"]
    if streak >= r["sleep_debt_nights"] and (hrv_low or rhr_up):
        out.append(("BAD", f"连续 {streak} 晚睡眠不足 7h，"
                           f"{'HRV 跌破个人下沿' if hrv_low else '静息心率抬升'}——"
                           "这不是某晚没睡好，是累积恢复不足，交感张力在升高。"
                           "优先补觉，此状态下不安排高强度训练"))
    elif streak >= r["sleep_debt_nights"]:
        out.append(("WARN", f"连续 {streak} 晚睡眠不足，但 HRV/RHR 仍稳——"
                            "身体暂时扛住了。这种代偿通常有期限，别把它当无限额度"))
    elif flags and not flags[0]:
        # 最近一夜达标：查它之前攒了几晚短睡，如实报回补而不是继续念旧账
        prior = 0
        for f in flags[1:]:
            if f:
                prior += 1
            else:
                break
        if prior >= r["sleep_debt_nights"]:
            dur = _get(day, "sleep", "duration_s") or 0
            out.append(("INFO", f"此前连续 {prior} 晚睡眠不足，昨夜已回补 "
                                f"{dur // 3600}h{dur % 3600 // 60:02d}m"
                                f"{'，HRV 回升至 ' + str(hrv['last_night_avg']) if hrv.get('last_night_avg') else ''}"
                                "——方向对了。睡眠债按周计，一晚还不清，保住现在的入睡节奏"))

    # 2. 入睡时间 × 睡眠结构：晚睡截断后半夜 → REM 被压缩（REM 在后半夜富集）
    bed = s.get("start_local")
    rem_pct = round(s["rem_s"] / s["duration_s"] * 100) \
        if s.get("rem_s") and s.get("duration_s") else None
    if bed and bed >= sleep_r["late_bedtime"] and rem_pct is not None \
            and rem_pct < sleep_r["rem_pct_optimal"][0]:
        out.append(("WARN", f"入睡 {bed} 且 REM 仅 {rem_pct}%——机制上互为因果："
                            "REM 富集在后半夜，晚睡等于直接截掉 REM 主产段。"
                            "想修 REM，把入睡时间提前比任何补剂都有效"))
    deep_pct = round(s["deep_s"] / s["duration_s"] * 100) \
        if s.get("deep_s") and s.get("duration_s") else None
    subj = subj_today or {}
    if subj.get("alcohol_drinks") and subj["alcohol_drinks"] >= r["alcohol_rem_flag"]:
        note = "（前夜饮酒 " + str(subj["alcohol_drinks"]) + " 杯）" \
            if subj.get("alcohol_drinks") else ""
        out.append(("WARN", f"前夜饮酒→深睡受抑、后半夜交感兴奋、REM 回跳受损，"
                            f"对应今晚睡眠结构：深睡 {deep_pct}% / REM {rem_pct}%。"
                            "酒精的『助眠』是麻醉，不是恢复" + note))
    if subj.get("caffeine_last"):
        late_caffeine = subj["caffeine_last"] >= "15:00"
        if late_caffeine and bed and bed >= sleep_r["late_bedtime"]:
            out.append(("INFO", f"末次咖啡因 {subj['caffeine_last']} + 入睡 {bed}——"
                                "咖啡因半衰期 5-6h，这个时间点的摄入到睡前仍有约半量在体内，"
                                "晚睡有它一份贡献"))

    # 3. 久坐 × 消耗结构：坐了一整天，电却耗光了——耗在哪
    sed_h = (act.get("sedentary_min") or 0) / 60
    high_min = stress.get("high_min")
    drained = act.get("bb_drained")
    if sed_h >= rules["activity"]["sedentary_warn_hours"] \
            and drained is not None and drained >= 50:
        mode = "体力" if high_min is not None and high_min >= r["stress_heavy_min"] else "脑力/应激"
        out.append(("WARN", f"久坐 {sed_h:.0f}h 但身体电量耗掉 {drained}——"
                            f"{'心率佐证为体力消耗' if mode == '体力' else '心率平稳，属脑力/应激消耗'}"
                            "为主。久坐天的疲劳感是真实的（应激激素），"
                            "但它不产生任何训练适应，还挤占了恢复"))

    # 4. 短睡/HRV 低 × 训练风险：今天该不该练、练多狠
    last_night = s.get("duration_s")
    ready_ok = not ((last_night and last_night < r["short_sleep_min"] * 60) or hrv_low)
    if windows and not ready_ok:
        out.append(("BAD", "昨夜睡眠不足/HRV 偏低时出现了中高强度运动窗——"
                           "睡眠剥夺下做高强度，皮质醇反应放大、损伤风险上升，"
                           "VO2 Max 也不会因此受益。欠觉日训练只该到 Z2"))
    if not windows and ready_ok and (act.get("steps") or 0) >= rules["activity"]["steps_good"] \
            and (act.get("intensity_min") or 0) < rules["activity"]["intensity_daily_goal"]:
        out.append(("INFO", "今天恢复条件良好（睡眠、HRV 都在线）但没练——"
                            "恢复能力是给训练用的，攒着不花就是浪费"))

    # 5. 负荷账本 × 有氧底子：方向性判断
    acwr = _get(day, "training", "acwr")
    vo2 = _get(day, "vo2max", "value")
    if acwr is not None and acwr < rules["acwr"]["under"] and vo2 is not None:
        band = next((b["name"] for b in rules["vo2max_male_bands"]
                     if b["min"] <= vo2 < b["max"]), "?")
        if band in ("差", "一般"):
            out.append(("WARN", f"ACWR {acwr:.2f} + VO2 Max {vo2}（{band}）："
                                "负荷账本长期空白，意味着这个 VO2 Max 没有上行支撑，"
                                "只会随时间缓慢下探。步行维持日常消耗，"
                                "但提不上有氧底子——需要每周 1-2 次让心率进 Z3 的刺激"))

    # 6. Body Battery 净值：从最新一天往回数连续净负（streak 判断，不是窗口内凑数）
    def _net(d):
        c, dr = _get(d, "activity", "bb_charged"), _get(d, "activity", "bb_drained")
        return (c - dr) if (c is not None and dr is not None) else None
    nets = [(d["date"], _net(d)) for d in reversed(days)]   # 最新在前
    neg_streak = 0
    for _, v in nets:
        if v is not None and v < 0:
            neg_streak += 1
        else:
            break
    fmt = lambda dt: dt[5:7] + "/" + dt[8:10]
    if neg_streak >= r["bb_net_negative_days"]:
        seq = "、".join(f"{fmt(dt)} {v:+d}" for dt, v in nets[:neg_streak])
        tail = "，倒挂已成持续模式" if neg_streak >= 5 else ""
        out.append(("WARN", f"身体电量连续 {neg_streak} 天净消耗（{seq}）{tail}——"
                            "收入不抵支出。结合睡眠看，入睡时间偏晚是这条链的源头"))
    elif neg_streak == 0 and sum(1 for _, v in nets if v is not None and v < 0) \
            >= r["bb_net_negative_days"]:
        pos = "、".join(f"{fmt(dt)} {v:+d}" for dt, v in nets if v is not None and v >= 0)
        out.append(("INFO", f"此前几天电量净消耗，近段已转正（{pos}）——"
                            "收支回稳，保持当前节奏"))
    return out


# ---------------------------------------------------------------- 渲染 ----

def render(date, source, cross, sections, timeline, trend, gaps, synced_at=""):
    import datetime as _dt
    lines = [f"# 每日健康简报 — {date}（数据源：{source}）", ""]
    meta = [f"数据截止：{synced_at[11:16] if synced_at else '未知'}"]
    is_draft = bool(synced_at) and synced_at[11:13] < "23" \
        and synced_at[:10] == date
    meta.append("日内草稿版：当日数据未完整，红黄绿结论以 23:30 终版为准"
                if is_draft else "终版")
    lines += ["*" + " · ".join(meta) + "*", ""]
    worst = min((s["level"] for s in sections.values()),
                key=lambda l: SEVERITY.get(l, 2))
    headline = {"BAD": "红灯：有需要正视的问题",
                "WARN": "黄灯：整体可控，有明确短板",
                "GOOD": "绿灯：状态良好",
                "NEUTRAL": "数据有限，维持观察"}[worst]
    lines += [f"**总评：{headline}**", ""]
    # 交叉判读是主体，放最前
    if cross:
        lines += ["## 交叉判读", ""]
        for lv, msg in cross:
            lines.append(f"- {ICON.get(lv, ICON['INFO'])} {msg}")
        lines.append("")
    # 单指标明细压缩为附录
    lines += ["## 指标明细", ""]
    for name, sec in sections.items():
        for lv, msg in sec["marks"]:
            lines.append(f"- {ICON.get(lv, ICON['INFO'])} **{name}**：{msg}")
    lines.append("")
    if timeline:
        lines += ["## 当日时间线", "", "| 时段 | 状态 |", "|---|---|"]
        lines += [f"| {t[0]} | {t[1]} |" for t in timeline]
        lines.append("")
    if trend:
        lines += ["## 近几日趋势", "",
                  "| 日期 | 睡眠 | 入睡 | HRV | 步数 | Z2min | BB落点 | 高压min | 准备度 |",
                  "|---|---|---|---|---|---|---|---|---|"]
        lines += [f"| {t['date']} | {t['sleep']} | {t['bed']} | {t['hrv']} | "
                  f"{t['steps']} | {t['z2']} | {t['bb']} | {t['stress_high']} | {t['readiness']} |"
                  for t in trend]
        lines.append("")
    if gaps:
        lines += ["## 数据缺口", ""] + [f"- {g}" for g in gaps] + [""]
    return "\n".join(lines)


def build_trend(days, rules):
    out = []
    for day in days:
        s = day.get("sleep") or {}
        hh = day.get("hr_hourly") or {}
        z2m = compute_z2(hh, rules, day.get("hr_sample_interval_min"),
                         day.get("hr_series"))["minutes"] if (hh or day.get("hr_series")) else None
        out.append({
            "date": day["date"],
            "sleep": hhmm(s.get("duration_s")) or "—",
            "bed": s.get("start_local") or "—",
            "hrv": (day.get("hrv") or {}).get("last_night_avg", "—"),
            "bb": (day.get("bb") or {}).get("low", "—"),
            "stress_high": (day.get("stress") or {}).get("high_min", "—"),
            "steps": (day.get("activity") or {}).get("steps", "—"),
            "z2": z2m if z2m is not None else "—",
            "readiness": (f"{day['readiness']['level']}({day['readiness']['score']})"
                          if day.get("readiness") else "—")})
    return out


# ---------------------------------------------------------------- 主流程 ----

def main():
    ap = argparse.ArgumentParser(description="每日身体状态简报（数据源可插拔）")
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--days", type=int, default=4)
    ap.add_argument("--source", default="garmin-cn")
    ap.add_argument("--out", default=OUT_DIR, help="报告输出目录（默认工作空间 reports/）")
    ap.add_argument("--log", default=None, help="主观日志 jsonl 路径（默认工作空间根）")
    a = ap.parse_args()

    rules = load_rules()
    bundle = get_adapter(a.source).collect(a.date, a.days)
    days = bundle["days"]
    day = next((d for d in days if d["date"] == a.date), {}) or {}

    hr_hourly = day.get("hr_hourly") or {}
    hr_series = day.get("hr_series") or []
    windows = detect_exercise(hr_hourly, rules,
                              day.get("hr_sample_interval_min")) if hr_hourly else []
    z2_day = compute_z2(hr_hourly, rules,
                        day.get("hr_sample_interval_min"),
                        hr_series) if (hr_hourly or hr_series) else None
    # Z2 窗口累计（周进度视角）：逐日独立计算再求和
    z2_window_min, z2_days = 0, 0
    for d in days:
        hh = d.get("hr_hourly") or {}
        ss = d.get("hr_series") or []
        if hh or ss:
            z2_window_min += compute_z2(hh, rules,
                                        d.get("hr_sample_interval_min"),
                                        ss)["minutes"]
            z2_days += 1
    prev_hrv = None
    for d in days:
        if d["date"] < a.date and d.get("hrv"):
            prev_hrv = d["hrv"]["last_night_avg"]

    sections = {
        "睡眠": {"marks": judge_sleep(day.get("sleep") or {}, rules)},
        "活动量与久坐": {"marks": judge_activity(day.get("activity"), rules)},
        "自主神经（HRV/静息心率）": {"marks": judge_hrv(day.get("hrv") or {}, prev_hrv, rules)
                                       + judge_rhr(day.get("rhr"), rules)},
        "训练与负荷": {"marks": judge_exercise(windows, day.get("training"),
                                             day.get("activities"), rules)},
        "消耗与压力": {"marks": judge_bb(day.get("bb"), rules)
                             + judge_stress_cross(hr_hourly,
                                                  (day.get("stress") or {}).get("hourly", {}),
                                                  rules)},
        "准备度与有氧底子": {"marks": judge_readiness(day.get("readiness"), rules)
                                     + judge_vo2(day.get("vo2max"), rules)
                                     + judge_spo2(day.get("spo2"), rules)
                                     + judge_z2(z2_day, rules)},
    }
    if z2_days:
        pro = rules["z2"]["weekly_target_min"] * z2_days / 7
        pct = round(z2_window_min / pro * 100) if pro else 0
        sections["准备度与有氧底子"]["marks"].append(
            ("GOOD" if z2_window_min >= pro else "INFO",
             f"近 {z2_days} 天累计 Z2 {z2_window_min} 分钟"
             f"（周目标 {rules['z2']['weekly_target_min']}min 的 {pct}% 进度）"))
    for k in sections:
        sections[k]["level"] = level_rank(sections[k]["marks"])

    # 交叉协同判读：主观日志按日期取当日 + 历史窗口
    subj = load_subjective(a.log)
    subj_today = subj.get(a.date)
    subj_hist = {k: v for k, v in subj.items() if k != a.date}
    cross = cross_analysis(day, days, windows, subj_today, subj_hist, rules)

    s = day.get("sleep") or {}
    timeline = []
    if s.get("start_local"):
        timeline.append((f"{s['start_local']}–{s.get('end_local', '?')}",
                         f"睡眠 {hhmm(s.get('duration_s'))}，评分 {s.get('score', '—')}"))
    for w in windows:
        timeline.append((f"{w['start']}–{w['end']}",
                         f"运动窗：avg {w['avg_hr']} / max {w['max_hr']} bpm（{w['zone']}）"))
    for act in day.get("activities") or []:
        timeline.append((act.get("start_local", "?"),
                         f"正式活动：{act.get('type')} {act.get('minutes')}min"))

    trend = build_trend(days, rules)
    md = render(a.date, a.source, cross, sections, timeline, trend,
                bundle.get("gaps"), bundle.get("synced_at") or "")
    md_path = os.path.join(a.out, f"body_brief_{a.date}.md")
    json_path = os.path.join(a.out, f"body_brief_{a.date}.json")
    open(md_path, "w").write(md)
    json.dump({"date": a.date, "source": a.source, "cross": cross,
               "sections": sections,
               "timeline": timeline, "trend": trend,
               "gaps": bundle.get("gaps"), "canonical": days},
              open(json_path, "w"), ensure_ascii=False, indent=1)
    print("saved", md_path)
    print("saved", json_path)


if __name__ == "__main__":
    main()
