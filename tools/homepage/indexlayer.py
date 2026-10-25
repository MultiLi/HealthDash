# -*- coding: utf-8 -*-
"""索引层：长周期数据的轻量索引 + 按月详情分片。

为什么分层（2026-09-12 定）：
原先把全部日详情塞进单个 site/data.json（11 天 187KB ≈ 17KB/天），
一年就是 6MB，首屏必然被拖死。而日历 / 多尺度聚合 / 趋势这类长周期视图，
每格实际只需要十来个字段（≈260B/天，一年约 95KB）。

于是拆成两份：
- site/index.json            轻量逐日索引——日历、聚合、趋势矩阵全由它渲染
- site/daily/YYYY-MM.json    完整日详情按月分片——点开某天才按需拉取

**铁律**：跨日结论（如 HRV 达标）必须由后端出，前端只计数不重判。
scoring.hrv_state 是 HRV 达标的唯一口径，前端拿 hrv_ok 布尔值用；
否则「展示口径」与「判读口径」迟早各说各话（2026-09-11 事故复盘）。

阈值一律经 rules.json 下发，前端不硬编码（goals / baselines 两段）。
"""
import datetime
import os

from homepage.paths import SCRIPTS
from homepage.scoring import hrv_state

import sys as _sys
if SCRIPTS not in _sys.path:
    _sys.path.insert(0, SCRIPTS)
import metricdefs  # noqa: E402  ← 指标定义的唯一权威解析器（scripts/metricdefs.py）


def _wd(date_str):
    """ISO 周内序号：1=周一 … 7=周日（日历网格按周一起始）。"""
    return datetime.date.fromisoformat(date_str).isoweekday()


def day_row(day, rules):
    """完整 day 视图 -> 轻量索引行（日历/聚合/趋势的原子单位）。"""
    rec = day.get("recovery") or {}
    load = day.get("load") or {}
    act = day.get("activity") or {}
    hero = day.get("hero") or {}
    hv = rec.get("hrv") or {}
    sl = rec.get("sleep") or {}
    en = load.get("energy") or {}
    st = load.get("stress") or {}
    rd = rec.get("readiness") or {}

    # HRV 达标判定走后端唯一口径，前端只消费布尔值
    ok = hrv_state(hv, rules) if hv else None
    secs = sl.get("seconds")
    return {
        "date": day["date"],
        "label": day["label"],
        "weekday": day["weekday"],
        "wd": _wd(day["date"]),
        "morning": bool(day.get("morning")),
        "has_brief": bool(day.get("has_brief")),
        "score": hero.get("score"),
        "band": hero.get("band"),
        # Garmin 准备度：与综合指数并排的第二项分数。
        # 取 canonical 的 readiness（适配器已锁定为晨起 AFTER_WAKEUP_RESET 快照，
        # 日间重算进 readiness_series）——字段归一的唯一入口在后端，前端不猜。
        # level 为设备自己的五档（POOR/LOW/MODERATE/HIGH/PRIME），前端直接挂档位，
        # 不用我们的 score 阈值去改写设备判断。
        "readiness": rd.get("score"),
        "readiness_level": rd.get("level"),
        "sleep_h": round(secs / 3600, 2) if secs else None,
        "hrv": hv.get("value"),
        "hrv_ok": None if ok is None else (ok in ("in", "above")),
        "steps": act.get("steps"),
        "rhr": (load.get("hr") or {}).get("rhr"),
        "stress": st.get("avg"),
        "sedentary_min": act.get("sedentary_min"),
        "active_kcal": en.get("active"),
        "intensity_min": act.get("intensity_min"),
        "distance_km": act.get("distance_km"),
    }


def hrv_band(days, rules):
    """个人 HRV 基线带：取最后一个有基线的日子，缺则退 rules 缺省带。"""
    for d in reversed(days):
        hv = (d.get("recovery") or {}).get("hrv") or {}
        lo, hi = hv.get("low"), hv.get("high")
        if lo is not None and hi is not None and hi > lo:
            return {"low": lo, "high": hi}
    lo, hi = rules["hrv"]["fallback_band"]
    return {"low": lo, "high": hi}


# 主页要下发的单元指标（== METRICS 的 8 张卡）。只发这些：
# units 表里有 30+ 指标 × 6 尺度 ≈ 3,800 行，全发会把首屏撑到 1MB+。
# 卡以外的东西（体成分、血氧…）走各自的节，不需要单元判定。
_UNIT_METRICS = ("sleep_h", "hrv_ms", "rhr_bpm", "steps_daily", "sedentary_min",
                 "stress_avg", "active_kcal", "intensity_min")


def units_block(rules):
    """L2 单元判定下发（2026-10-25 接入，S2）。

    **为什么必须由后端下发**：`delta` 到底是「明显变化」还是「在误差内」，
    要过 rules.confidence 的 mdc 门槛；`judge` 要不要因为覆盖率不足而给 `na`；
    达标率的分母怎么算——这三件以前都在浏览器里各自实现一遍，于是主页画着
    「↑ 7%」的报告写着「在误差内」。现在只有一份：units 表。

    字段压短（v/j/c/n/d/dv/r/vs）是为了首屏体积：七个字段带全名会把
    index.json 从 26KB 推到 200KB+，而这些键在 js 侧只被一个函数读。

    DB 不可用（没跑过 index.py build）时返回 None，前端按旧逻辑回落并标注。
    """
    try:
        import db as _db
        con = _db.connect()
        rows = list(con.execute(
            "SELECT scale, key, metric, value, judge, judge_rate, coverage, n, "
            "delta_v, delta_verdict, delta_vs FROM units WHERE metric IN (%s)"
            % ",".join("?" * len(_UNIT_METRICS)), _UNIT_METRICS))
        con.close()
    except Exception as e:                     # 派生层缺失不阻断主页构建
        return {"_error": str(e)[:120]}

    # 后端 metric -> 前端 key（同一份注册表，前端不再自己维护映射）
    fk = {}
    for m, d in metricdefs.resolve_all(rules).items():
        k = d.get("frontend_key")
        if k and m in _UNIT_METRICS:
            fk[m] = k
    out = {}
    for r in rows:
        k = fk.get(r["metric"])
        if not k:
            continue
        cell = {"v": r["value"], "j": r["judge"], "c": r["coverage"], "n": r["n"]}
        if r["judge_rate"] is not None:
            cell["r"] = r["judge_rate"]
        if r["delta_v"] is not None:
            cell["d"] = round(r["delta_v"], 2)
        if r["delta_verdict"]:
            cell["dv"] = r["delta_verdict"]
        if r["delta_vs"]:
            cell["vs"] = r["delta_vs"]
        out.setdefault(r["scale"], {}).setdefault(r["key"], {})[k] = cell
    return out


def _goals(rules):
    """兼容旧的 ix.goals 结构 —— 值全部经唯一解析器取得。"""
    md = metricdefs.resolve_all(rules)
    return {
        "sleep_h": md["sleep_h"]["goal"],
        "steps": md["steps_daily"]["goal"],
        "steps_good": metricdefs.judge_of("steps_daily", rules),
        "sedentary_min": md["sedentary_min"]["goal"],
        "intensity_min": md["intensity_min"]["goal"],
    }


def build_index(data, rules):
    """完整视图 -> 轻量索引（首屏唯一载荷）。"""
    days = data["days"]
    hp = rules.get("homepage") or {}
    return {
        "generated_at": data["generated_at"],
        "source": data["source"],
        "brief_date": data["brief_date"],
        "synced_at": data["synced_at"],
        "default_day": data["default_day"],
        "today": datetime.date.today().isoformat(),
        # goals 是**兼容投影**：数值一律由 metricdefs 解析，本文件不再重写任何
        # metric→规则 的映射（2026-10-25）。前端旧代码仍读 ix.goals，S2 后改读 metric_defs。
        "goals": _goals(rules),
        # 指标定义全量下发：前端 S2 起据此渲染卡片，删掉 constants.js 里的 METRICS 表副本。
        "metric_defs": metricdefs.resolve_all(rules),
        "baselines": {
            "hrv": hrv_band(days, rules),
            "rhr": rules["profile"]["rhr_7d_avg"],
            "rhr_ok_max": rules["profile"]["rhr_7d_avg"] + rules["rhr"]["elevated_delta_bpm"],
            "stress_avg_good": hp.get("stress_avg_good", 40),
            "score": {"good_min": rules["hero"]["good_min"],
                      "fair_min": rules["hero"]["fair_min"]},
        },
        # L2 单元判定（每个尺度 × 单元 × 指标：值 / 达标 / 覆盖率 / 环比判定）。
        # 前端的环比箭头与达标色从**这里**读，不再自己比大小（2026-10-25）。
        "units": units_block(rules),
        "trend_windows": hp.get("trend_windows", [7, 30, 90]),
        "span": {"first": days[0]["date"], "last": days[-1]["date"], "n": len(days)},
        # 事件型数据源随索引下发：它是独立节，不进 days[]、不吃尺度，
        # 体积小（一次扫描 ~1KB），没必要为它再开一个按需端点。
        "inbody": data.get("inbody"),
        "days": [day_row(d, rules) for d in days],
    }


def shard_days(days):
    """完整日详情按月分片：{'2026-09': {'2026-09-11': <完整 day>}}。"""
    out = {}
    for d in days:
        out.setdefault(d["date"][:7], {})[d["date"]] = d
    return out


def write_index_layer(site_dir, data, rules):
    """落盘 index.json + daily/YYYY-MM.json，并清掉已不存在的月份分片。"""
    import json

    idx = build_index(data, rules)
    idx_path = os.path.join(site_dir, "index.json")
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)

    daily_dir = os.path.join(site_dir, "daily")
    os.makedirs(daily_dir, exist_ok=True)
    shards = shard_days(data["days"])
    for month, mdays in shards.items():
        with open(os.path.join(daily_dir, month + ".json"), "w", encoding="utf-8") as f:
            json.dump({"month": month, "days": mdays}, f, ensure_ascii=False, indent=1)
    # 目标日被删除/回补后，旧月份分片必须一并清掉，否则前端会读到幽灵数据
    for fn in os.listdir(daily_dir):
        if fn.endswith(".json") and fn[:-5] not in shards:
            os.remove(os.path.join(daily_dir, fn))

    # 旧的全量文件已被索引 + 分片取代，留着只会让首屏误读
    legacy = os.path.join(site_dir, "data.json")
    if os.path.exists(legacy):
        os.remove(legacy)
    return idx, shards
