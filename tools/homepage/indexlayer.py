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
from homepage.nudge import nudge_block
from homepage.events import events_block

import sys as _sys
if SCRIPTS not in _sys.path:
    _sys.path.insert(0, SCRIPTS)
import metricdefs  # noqa: E402  ← 指标定义的唯一权威解析器（scripts/metricdefs.py）


def _wd(date_str):
    """ISO 周内序号：1=周一 … 7=周日（日历网格按周一起始）。"""
    return datetime.date.fromisoformat(date_str).isoweekday()


def collect_state(day, today=None):
    """该日「数据是为什么没的」——四态唯一口径出口（2026-11-01 建）。

    前端以前只有一个「空心灰点」表示无数据，于是三种完全不同的情况长得一模一样：
      · `failed`  端点报错（raw 里 sync_errors 有内容）→ **系统侧故障**，该去修管线
      · `missing` 该日 raw 文件根本不存在            → 采集任务没跑到这一天
      · `absent`  有 raw、无报错、却无实测            → **设备侧**没推数据（多半没戴表）
      · `pending` 今天、还没到出数时刻                → 正常，等就行
    把 `failed` 显示成「没戴表」，是把系统的锅扣在人头上。归因错了，
    后面所有「我最近是不是懈怠了」的判断全部跟着错。所以这一层必须由后端定，
    不能让每块 UI 各自猜（并列自 `hrv_state` / `units` 的单一口径纪律）。

    判据顺序即优先级：报错 > 有数 > 无 raw > 今天未到 > 无佩戴。
    报错排第一是因为「部分维度丢了也和全好长得一样」——10-31 就是 steps 全空、
    hrv/准备度/曲线三个端点同时报错，若不看 sync_errors 只会判成「没戴表」。
    """
    c = day.get("collect") or {}
    errs = c.get("errors") or {}
    cov = day.get("coverage") or {}
    has = cov.get("has_data")
    if errs:
        return "failed"
    if has is True:
        return "ok"
    if not c.get("raw", True):
        return "missing"
    if today and day.get("date") == today:
        return "pending"
    return "absent"


def day_row(day, rules, today=None):
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
        # 采集状态四态（ok/failed/missing/absent/pending）：日历格、空态文案、
        # 顶部胶囊三处共用。**不要在前端重新推断**——它需要 sync_errors 与
        # coverage 两个只有后端才有的字段。
        "cs": collect_state(day, today),
        # 失败端点名（仅 cs=failed 时有值）：日历格 title 要能说出「哪个端点挂了」，
        # 只写「采集失败」等于让人自己去翻 raw。空列表不下发，省体积。
        "ce": (sorted(((day.get("collect") or {}).get("errors") or {}).keys())
               or None),

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


def units_block(rules, day_rows=None):
    """L2 单元判定下发（2026-10-25 接入，S2）。

    **为什么必须由后端下发**：`delta` 到底是「明显变化」还是「在误差内」，
    要过 rules.confidence 的 mdc 门槛；`judge` 要不要因为覆盖率不足而给 `na`；
    达标率的分母怎么算——这三件以前都在浏览器里各自实现一遍，于是主页画着
    「↑ 7%」的报告写着「在误差内」。现在只有一份：units 表。

    字段压短（v/j/c/n/d/dv/r/vs）是为了首屏体积：七个字段带全名会把
    index.json 从 26KB 推到 200KB+，而这些键在 js 侧只被一个函数读。

    `g`（2026-11-01 加）= 该单元内**采集侧没拿到的天数**（raw 缺失 / 端点报错）。
    它和 `c`（覆盖率）是两个问题：
      · `c` 低 = 样本少，均值不稳 → 影响「这个数可不可信」
      · `g` 高 = 系统没采到 → 影响「这个数为什么缺」
    以前只有一个覆盖率，于是「采集挂了 12 天」长得和「你自己 12 天没测」一模一样。
    注意 `g` 只在展示层解释成因，**不改变任何判定**：judge/rate/coverage 仍是
    L2 的原始口径，避免在这里偷偷重算一遍（那正是「两套口径」的老毛病）。

    DB 不可用（没跑过 index.py build）时返回 None，前端按旧逻辑回落并标注。
    """
    try:
        import db as _db
        con = _db.connect()
        rows = list(con.execute(
            "SELECT scale, key, metric, value, judge, judge_rate, coverage, n, "
            "delta_v, delta_verdict, delta_vs, start, end FROM units "
            "WHERE metric IN (%s)" % ",".join("?" * len(_UNIT_METRICS)), _UNIT_METRICS))
        con.close()
    except Exception as e:                     # 派生层缺失不阻断主页构建
        return {"_error": str(e)[:120]}

    # 逐日采集故障（failed / missing），按日期索引，供上面按单元区间统计
    bad_days = set()
    if day_rows:
        bad_days = {r["date"] for r in day_rows if r.get("cs") in ("failed", "missing")}

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
        # 采集缺口：区间内有多少天是「系统没取到」而不是「人没给」
        if bad_days and r["start"] and r["end"]:
            g = sum(1 for d in bad_days if r["start"] <= d <= r["end"])
            if g:
                cell["g"] = g
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


def collect_block(days, rows):
    """采集健康度汇总（2026-11-01 建）。给顶部胶囊与告警条用。

    **为什么单独成块而不是让前端数 days[].cs**：胶囊要在一句话里说清
    「多少天有实测 / 多少天是采集失败」，这是比率判断，不能让每块 UI 各自
    filter 一遍再四舍五入。汇总口径只有一份。
    """
    from collections import Counter
    c = Counter(r["cs"] for r in rows)
    errs = []
    for d in days:
        e = (d.get("collect") or {}).get("errors") or {}
        if e:
            errs.append({"date": d["date"], "keys": sorted(e.keys())})
    errs.sort(key=lambda x: x["date"], reverse=True)
    # 连续缺失区间：光说「18 天没原始数据」没用，得说清是哪几段。
    # 断档常常是连续的（一次同步坏了就是两周），按段报比按天列短得多。
    runs, cur = [], []
    for r in rows:
        if r["cs"] == "missing":
            cur.append(r["date"])
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    ranges = ["%s~%s" % (g[0][5:], g[-1][5:]) if len(g) > 1 else g[0][5:]
              for g in runs]
    return {
        "summary": {"n": len(rows), "ok": c["ok"], "failed": c["failed"],
                    "missing": c["missing"], "absent": c["absent"],
                    "pending": c["pending"]},
        # 只留最近 5 次：告警条是「现在要不要去看一眼」，不是错误归档。
        "recent_errors": errs[:5],
        "missing_ranges": ranges,
    }


def build_index(data, rules):
    """完整视图 -> 轻量索引（首屏唯一载荷）。"""
    days = data["days"]
    hp = rules.get("homepage") or {}
    today = datetime.date.today().isoformat()
    rows = [day_row(d, rules, today) for d in days]
    return {
        "generated_at": data["generated_at"],
        "source": data["source"],
        "brief_date": data["brief_date"],
        "synced_at": data["synced_at"],
        "default_day": data["default_day"],
        "today": today,
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
        # 传 rows 是为了给每格附上「采集缺口 g」——说明覆盖为什么不足，不改判定。
        "units": units_block(rules, rows),
        "trend_windows": hp.get("trend_windows", [7, 30, 90]),
        "span": {"first": days[0]["date"], "last": days[-1]["date"], "n": len(days)},
        # 采集健康度：哪里没数、为什么没数。**加这个块的原因很直接**——
        # 页面原本写着「Garmin 已同步 · 60 个数据日」，而那 60 天里有 22 天
        # 一个实测值都没有。用「数据日」计数，等于把空壳算成有数据。
        "collect": collect_block(days, rows),
        # 判读流水（2026-11-01）：{date: {head, lv}}，一天一句。
        # 62 天约 6KB，换来的是一眼能看出「睡眠不足」这个结论连了几天——
        # 单日判读再详细也做不到这件事。
        "briefs": {d["date"]: d["brief_head"] for d in days if d.get("brief_head")},
        # 事件流旁路（2026-11-01）：{date: [{t,c,ts}]}。判读的结论依赖这些背景，
        # 结论和依据必须能同屏出现，否则三个月后回看会觉得判断来得莫名其妙。
        "events": events_block(),
        # 主观补数缺口（2026-11-01）：只读提示，不提供写入。
        # 详见 homepage/nudge.py 的模块注释——公开站点上不开写接口是取舍，不是遗漏。
        "nudge": nudge_block(today),
        # 事件型数据源随索引下发：它是独立节，不进 days[]、不吃尺度，
        # 体积小（一次扫描 ~1KB），没必要为它再开一个按需端点。
        "inbody": data.get("inbody"),
        "days": rows,
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
