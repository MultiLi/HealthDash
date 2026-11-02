# -*- coding: utf-8 -*-
"""InBody 体成分视图层（事件型数据源）。

定位：**把它当成「低频指标的又一个来源」，不是「另一个板块」**。所以本文件
产出两样东西，两者用同一套口径：

  ① cards[]  —— 几张与日指标同构的卡片（体重 / 体脂率 / 去脂体重 / 骨骼肌 /
                内脏脂肪），每张带「较上次 ±x，过没过门槛」。前端把它们放进
                指标区的「体成分」分组，外观与恢复/行为/负荷三组一致。
  ② 详情块   —— 截面构成、参考区间、目标路径、节段分布（原有内容）。

**为什么不进日/周/月尺度**：`references/metrics.json` 已把 weight_kg /
body_fat_pct / ffm_kg 声明为「点观测、低频」，并写明「刻意不进 units 聚合——
一旦合并就会把两个口径平均成一个不存在的数」。所以卡片只有扫描日的点，
时间语义是「最近一次 + 上次 + 全部扫描」，不吃 anchor/scale。

**变化量不在这里算**：过没过门槛由 `check_inbody.py` 判定并落在
`data/inbody/status.json`，本文件只读。视图层重算一次门槛，就等于给同一个
事实留下第二条路径——报告写「过门槛」、页面写「在误差内」，两边都不算错。

口径纪律（与 metrics.json / SKILL.md 同源）：
  · 体测仪体重 ≠ 晨起空腹体重，两个口径并列、禁止相减/平均；
  · 跨机型不可比（H40 家用 vs 健身房 770/970 差 1–3pt）；
  · BIA ±3–5%，只谈区间与通道归属，不追两周变化；
  · 超过 30 天标记 stale，只作历史参考。
"""
import datetime
import json
import os

from homepage.paths import DAILY_LOG, ROOT, load_rules

INBODY = os.path.join(ROOT, "data", "inbody")

# 目标路径的体脂率档位（展示用，不是判读阈值）
_TARGET_PBF = [(20, "参考区间上限"), (18, "中间站"), (15, "有线条")]

# 卡片顺序 = 从「最该先看的」到「补充」：(注册表 key, 扫描对象 key, 参考区间 key)
_CARDS = [
    ("weight_kg", "weight_kg", "weight_kg"),
    ("body_fat_pct", "percent_body_fat", "percent_body_fat"),
    ("ffm_kg", "fat_free_mass_kg", "lean_mass_kg"),
    ("skeletal_muscle_kg", "skeletal_muscle_mass_kg", "skeletal_muscle_kg"),
    ("visceral_fat_level", "visceral_fat_level", "visceral_fat_level"),
]

# 同期对照：两次扫描之间，Garmin 侧的行为面。(行字段, 标签, 单位, 精度, 除数)
_CONTEXT = [
    ("steps", "日步数", "步", 0, 1),
    ("sleep_h", "睡眠", "h", 1, 1),
    ("intensity_min", "中高强度", "min", 0, 1),
    ("sedentary_min", "久坐", "h", 1, 60),
    ("active_kcal", "活动消耗", "kcal", 0, 1),
    ("rhr", "静息心率", "bpm", 0, 1),
    ("hrv", "HRV", "ms", 0, 1),
]

VERDICT_TEXT = {
    "changed": "过门槛",
    "within-noise": "在误差内",
    "no-baseline": "首次测量",
    "not-comparable": "跨条件不可比",
}


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
    """晨起空腹体重（daily_log 的唯一正式口径：weight_kg）。

    路径走 settings 的 DAILY_LOG——数据在 data/logs/ 下，自己拼 ROOT/daily_log.jsonl
    会永远读不到（曾如此，页面上的「双口径并列」因此从未渲染过）。
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
            kg = _num(row.get("weight_kg"))
            if kg:
                best = {"kg": kg, "date": row.get("date")}
    if not best:
        return None
    try:
        best["age_days"] = (datetime.date.today()
                            - datetime.date.fromisoformat(best["date"])).days
    except Exception:
        best["age_days"] = None
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


def _deltas():
    """闸门算好的变化量：{扫描字段名: delta 记录}。读不到就返回空。"""
    st = _load("status.json") or {}
    out = {}
    for d in st.get("deltas") or []:
        if d.get("metric"):
            out[d["metric"]] = d
    return out, st


def _defs():
    """指标定义（label/unit/precision）唯一来源：references/metrics.json。

    读 `registry()` 而不是 `resolve_all()`：后者只下发「进单元聚合」的指标，
    体成分被 metrics.json 明确声明为不进聚合，用 resolve_all 会一条都拿不到。
    """
    try:
        import metricdefs                      # scripts/ 在 sys.path（paths.py 注入）
        return metricdefs.registry()
    except Exception:
        return {}


def _cards(scans, ref, targets, deltas, checked=True):
    """稀疏指标卡：每张只有扫描日的点，变化来自闸门。

    `checked=False` = 闸门还没跑过最新那次扫描（status.json 的最新记录对不上）。
    这时**不给比较**：拿上一次的差冒充这一次的，正是本页最该避免的那种错。
    """
    defs = _defs()
    ordered = sorted(scans, key=lambda s: s.get("datetime") or "")
    prev = ordered[-2] if len(ordered) > 1 else None
    latest = ordered[-1] if ordered else {}
    tgt = {t["pbf"]: t for t in targets}

    out = []
    for key, field, ref_key in _CARDS:
        d = defs.get(key) or {}
        series = []
        for s in ordered:
            v = _num(s.get(field))
            if v is None:
                continue
            series.append({"d": (s.get("datetime") or "")[:10], "v": v})
        if not series:
            continue
        cur = series[-1]["v"]
        dl = deltas.get(field) or {}
        card = {
            "key": key,
            "label": d.get("label") or key,
            "unit": d.get("unit") or "",
            "precision": d.get("precision") if d.get("precision") is not None else 1,
            "value": cur,
            "ref": ref.get(ref_key),
            "series": series,
            "scans": len(series),
            "prev": dl.get("prev"),
            "delta": dl.get("delta"),
            "mdc": dl.get("mdc_change"),
            "exceeds": dl.get("exceeds_mdc"),
            "same_conditions": dl.get("same_conditions"),
            "note": "",
        }
        if prev:
            card["prev_date"] = (prev.get("datetime") or "")[:10]
        if not checked:
            card["verdict"] = "pending-check"
        elif not dl:
            card["verdict"] = "no-baseline"
        elif dl.get("hard_anomaly"):
            card["verdict"] = "not-comparable"
            card["note"] = "变化幅度超出可信范围，先怀疑测错"
        elif dl.get("same_conditions") is False:
            # 跨时段/跨机型：门槛已由闸门换成异条件值，但「是否可比」必须说出来
            card["verdict"] = "changed" if dl.get("exceeds_mdc") else "not-comparable"
        else:
            card["verdict"] = "changed" if dl.get("exceeds_mdc") else "within-noise"
        # 体重卡额外挂目标线：目标路径的第一站（参考区间上限），现算不写死
        if key == "weight_kg" and tgt:
            card["target"] = tgt.get(20, {}).get("weight_kg")
        out.append(card)
    return out


def _mean(rows, key, div):
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        return None, 0
    return round(sum(vals) / len(vals) / div, 1 if div == 1 else 1), len(vals)


def window_for(days, prev_dt, latest_dt):
    """两次扫描之间的 Garmin 行为面。

    **只并置，不判方向**：窗口对齐回答的是「体成分变化那段时间，行为面什么样」，
    不是「因为走了多少步所以掉了多少脂肪」。所以这里只给均值与有效天数，
    不给差值、不给箭头——那两个东西要过 rules.confidence 的门槛，而跨源因果
    没有门槛可过。
    """
    if not days or not prev_dt or not latest_dt:
        return None
    try:
        prev_d = datetime.date.fromisoformat(prev_dt[:10])
        last_d = datetime.date.fromisoformat(latest_dt[:10])
    except Exception:
        return None
    now = [r for r in days
           if prev_d < datetime.date.fromisoformat(r["date"]) <= last_d]
    if not now:
        return None
    n = len(now)
    prev_rows = [r for r in days
                 if prev_d - datetime.timedelta(days=n)
                 < datetime.date.fromisoformat(r["date"]) <= prev_d]
    metrics = []
    for key, label, unit, prec, div in _CONTEXT:
        now_v, now_n = _mean(now, key, div)
        if now_v is None:
            continue
        prev_v, prev_n = _mean(prev_rows, key, div)
        metrics.append({"key": key, "label": label, "unit": unit,
                        "precision": prec, "now": now_v, "now_n": now_n,
                        "prev": prev_v, "prev_n": prev_n})
    return {
        "from": now[0]["date"], "to": now[-1]["date"], "days": n,
        "metrics": metrics,
        "note": "两段等长窗口并置：本窗＝上次扫描次日到本次扫描，前窗＝紧邻的同长段。"
                "只供对照，不判方向——两个源之间没有可过的因果门槛。",
    }


def attach_window(ib, rows):
    """把「扫描区间内的 Garmin 行为面」挂到索引块上。

    单独一步，是因为它要的是**索引行**（扁平的 date/steps/sleep_h），
    那是 indexlayer 的产物；build 阶段手上只有完整 day（嵌套 recovery/activity）。
    视图层不重造一份扁平化逻辑——两套扁平化就是两套口径。
    """
    if not ib or not ib.get("available"):
        return ib
    hist = ib.get("history") or []
    if len(hist) < 2:
        return ib
    out = dict(ib)
    out["window"] = window_for(rows, hist[-2].get("datetime"), hist[-1].get("datetime"))
    return out


def build():
    """装配体成分块。**同期行为面不在这里**——它要索引行，见 attach_window()."""
    idx = _load("index.json")
    latest_raw = _load("latest.json")
    prof = _load("profile.json") or {}
    if not idx or not idx.get("scans"):
        return {"available": False,
                "reason": "InBody 账号下没有扫描记录"}

    scans = sorted(idx["scans"], key=lambda s: s.get("datetime") or "")
    latest = dict(scans[-1])
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
    targets = _targets(latest, ref)
    deltas, status = _deltas()
    # 闸门（check_inbody）每次采集都会重跑；万一最新扫描还没被它看过，
    # status.deltas 讲的是上一对扫描，拿它当这次的变化就是张冠李戴。
    checked = status.get("latest_datetime") == latest.get("datetime")

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
        "targets": targets,
        "morning_weight": morning_weight(),
        # 稀疏指标卡：与日指标同构，但只有扫描日的点（不进 units 聚合）
        "cards": _cards(scans, ref, targets, deltas, checked=checked),
        # 闸门结论：页面与报告共用同一份判定，不各算一次
        "status": {
            "verdict": status.get("verdict"),
            "checked_at": status.get("checked_at"),
            "conditions": (status.get("conditions") or {}).get("note"),
            "notes": (status.get("notes") or [])[-1:] if status.get("notes") else [],
        },
        # 扫描时间线：>=2 次才有趋势可言，1 次时前端不应渲染任何"趋势"
        "history": [{"datetime": s.get("datetime"),
                     "weight_kg": s.get("weight_kg"),
                     "percent_body_fat": s.get("percent_body_fat"),
                     "skeletal_muscle_mass_kg": s.get("skeletal_muscle_mass_kg")}
                    for s in scans],
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
