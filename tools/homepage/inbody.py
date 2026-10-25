# -*- coding: utf-8 -*-
"""InBody 体成分视图层（事件型数据源，2026-10-18 建）。

**为什么不进日/周/月尺度**：InBody 是事件型——一次体测一条记录，间隔以周/月计，
没有逐日序列。把它塞进指标网格（那张网格的每一格都是「同一天的一个连续量」）
会立刻说错两件事：① 暗示每天都有值；② 让「月日均」这种口径凭空产生。

所以它独立成节，有自己的时间语义：**最近一次扫描 + 距今 N 天**。
只有一条记录时它就是一张截面卡；记录多了自然长成扫描时间线——
不为 1 条数据造一张假趋势图。

口径纪律（与 SKILL.md「体成分源 InBody」一节同源）：
  · 体测仪体重 ≠ 晨起空腹体重，**两个口径并列、禁止相减/平均**；
  · 跨机型不可比（H40 家用机 vs 健身房 770/970 差 1–3pt）；
  · BIA 绝对误差 ±3–5%，只谈区间与通道归属，不追两周变化；
  · 超过 30 天标记 stale，只作历史参考。
"""
import datetime
import json
import os

from homepage.paths import ROOT

INBODY = os.path.join(ROOT, "data", "inbody")
DAILY_LOG = os.path.join(ROOT, "daily_log.jsonl")

# 目标路径的体脂率档位（展示用，不是判读阈值）
_TARGET_PBF = [(20, "参考区间上限"), (18, "中间站"), (15, "有线条")]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _load(name):
    p = os.path.join(INBODY, name)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def morning_weight():
    """晨起空腹体重（daily_log 的唯一正式口径）。

    2026-10-18 实测：正式字段 weight_kg 目前全为 null，唯一有值的是 09-06 的
    废弃字段 weight_extra_morning_kg=85.9，故回落读取并如实标注来源日期。
    """
    if not os.path.isfile(DAILY_LOG):
        return None
    best = None
    with open(DAILY_LOG, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            kg = _num(row.get("weight_kg")) or _num(row.get("weight_extra_morning_kg"))
            if kg:
                best = {"kg": kg, "date": row.get("date"),
                        "source": "weight_kg" if row.get("weight_kg") else
                                  "weight_extra_morning_kg（历史字段）"}
    return best


def _reference(latest):
    """参考区间：直接取设备随记录下发的 MIN/MAX，不在前端硬编码。
    MFA 段是体成分判定表，BCA 段是原始量——SLM/蛋白质/矿物质只在 BCA 里。"""
    mfa = latest.get("MFA") or {}
    bca = latest.get("BCA") or {}

    def rng(a, b):
        lo, hi = _num(mfa.get(a)), _num(mfa.get(b))
        if lo is None or hi is None:
            lo, hi = _num(bca.get(a)), _num(bca.get(b))
        return [lo, hi] if lo is not None and hi is not None else None

    return {
        "weight_kg": rng("WT_MIN", "WT_MAX"),
        "skeletal_muscle_kg": rng("SMM_MIN", "SMM_MAX"),
        "percent_body_fat": rng("PBF_MIN", "PBF_MAX"),
        "body_fat_kg": rng("BFM_MIN", "BFM_MAX") or rng("PBFM_MIN", "PBFM_MAX"),
        "bmi": rng("BMI_MIN", "BMI_MAX"),
        "whr": rng("WHR_MIN", "WHR_MAX"),
        "lean_mass_kg": rng("SLM_MIN", "SLM_MAX"),
        "protein_kg": rng("PROTEIN_MIN", "PROTEIN_MAX"),
        "mineral_kg": rng("MINERAL_MIN", "MINERAL_MAX"),
        "visceral_fat_level": 9,        # InBody 内置：1–9 正常，10 以上偏高
        "_source": "设备随记录下发的 MFA/BCA 区间（H40 内置判定表）",
        "_bca": bool(bca),
    }


def _targets(latest, ref):
    """以「去脂体重不变」（减脂保肌）为前提的目标路径。

    weight @ pbf = FFM / (1 - pbf)。不硬编码 85.9/80.8——由实测 FFM 现算，
    这样任何一次新扫描都自动给出属于自己的目标，而不是抄上一次的数。
    """
    ffm = _num(latest.get("fat_free_mass_kg"))
    wt = _num(latest.get("weight_kg"))
    if not ffm or not wt:
        return []
    out = []
    for pbf, label in _TARGET_PBF:
        target_w = round(ffm / (1 - pbf / 100.0), 1)
        out.append({
            "pbf": pbf,
            "label": label,
            "weight_kg": target_w,
            "fat_to_lose_kg": round(wt - target_w, 1),
            "in_range": bool(ref.get("percent_body_fat")
                             and pbf <= ref["percent_body_fat"][1]),
        })
    return out


def build(morning=None):
    idx = _load("index.json")
    latest_raw = _load("latest.json")
    prof = _load("profile.json") or {}
    if not idx or not idx.get("scans"):
        return {"available": False,
                "reason": "InBody 账号下没有扫描记录"}

    scans = idx["scans"]
    latest = dict(scans[0])
    for k in ("visceral_fat_level", "bmr_kcal", "ffmi", "segmental_lean_kg",
              "segmental_fat_kg"):
        if latest.get(k) in (None, {}, 0) and latest_raw:
            latest[k] = latest_raw.get(k)
    if latest.get("fat_free_mass_kg") is None and latest_raw:
        latest["fat_free_mass_kg"] = latest_raw.get("fat_free_mass_kg")

    dt = latest.get("datetime")
    age_days = None
    if dt:
        age_days = (datetime.date.today()
                    - datetime.date.fromisoformat(dt[:10])).days

    ref = _reference(latest_raw or {})
    mw = morning if morning is not None else morning_weight()

    return {
        "available": True,
        "scan_count": idx.get("scan_count_total") or len(scans),
        "updated": idx.get("updated"),
        "latest": latest,
        "age_days": age_days,
        "stale": bool(age_days is not None and age_days > 30),
        "equipment": latest.get("equipment"),
        "serial": latest.get("serial") or (latest_raw or {}).get("serial"),
        "profile": {"height_cm": prof.get("height_cm"),
                    "age": prof.get("age"), "gender": prof.get("gender")},
        "reference": ref,
        "targets": _targets(latest, ref),
        "morning_weight": mw,
        # 扫描时间线：>=2 次才有趋势可言，1 次时前端不应渲染任何"趋势"
        "history": [{"datetime": s.get("datetime"),
                     "weight_kg": s.get("weight_kg"),
                     "percent_body_fat": s.get("percent_body_fat"),
                     "skeletal_muscle_mass_kg": s.get("skeletal_muscle_mass_kg")}
                    for s in sorted(scans, key=lambda s: s.get("datetime") or "")],
        "caveats": _caveats(latest, age_days),
    }


def _caveats(latest, age_days):
    """口径声明：这三条必须出现在页面上，不能塞脚注——
    数字一旦先被看见，判读就没机会纠正它。"""
    out = []
    t = (latest.get("datetime") or "")
    hh = t[11:16]
    if hh and hh > "08:30":
        out.append("测量时点 %s，非晨起空腹——受当日进食饮水影响，"
                   "体重通常比晨起高 1–2 kg" % hh)
    if (latest.get("equipment") or "").upper().startswith("H"):
        out.append("机身 %s 为家用站立式，跨机型不可比（专业机差 1–3 个百分点）"
                   % (latest.get("equipment") or ""))
    out.append("生物电阻抗固有误差 ±3–5%：只定区间与通道归属，不追两周内的涨跌")
    if age_days is not None and age_days > 30:
        out.append("距今 %d 天，仅作历史参考" % age_days)
    if (latest.get("percent_body_fat") is not None
            and not (3 <= float(latest["percent_body_fat"]) <= 65)):
        out.append("体脂率读数超出 BIA 可信范围，建议复测")
    return out
