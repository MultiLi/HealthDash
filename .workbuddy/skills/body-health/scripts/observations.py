#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多重观测框架（observation framework）—— 让「同一指标的多个观测」并存，而不是提前合并。

【为什么需要（2026-10-19 建）】
现有数据有三套互不相通的存法：Garmin 的 canonical 日字典、InBody 的事件文件、
daily_log 的主观表。而且 canonical 里**一个指标只留一个值**（`activities_own`
覆盖 `activities`、InBody 体重与晨起体重完全不通）。所以项目原则里那条
「多观测并存，仲裁在判读层」一直只是文档里的一句话，不是数据结构。

本模块把它变成代码。四条硬规则：

1. **观测是一等公民，不是一个字段。** 最小单位 = (指标, 值, 时间, 来源, 方法, 条件)。
   同一指标可以同时存在任意多条，`query()` **永不合并、永不取平均、永不择一**。
2. **可比性由注册表声明，不由调用方猜。** 每个指标在 `references/metrics.json` 里
   有 `comparable_on`（例如体重必须同 method/device/空腹状态）。两次观测只要有一项
   不满足，`resolve()` 直接判 `not-comparable`，**不给差值**。
3. **仲裁显式化。** `resolve()` 返回「选了哪条、为什么、有什么冲突」，而不是静默挑一个。
4. **派生观测留输入。** FFM、BMI 这类必须带 `derived_from`，否则无法级联重算与溯源。

【不做的事】
· 不做 ETL 落盘：provider 在**读时**从各自的原生存储取数，不复制数据。
  新增一个源 = 写一个 provider（十几行）+ 在 metrics.json 登记，判读引擎零改动。
· 不判断「哪个源更准」：那是判读层的证据裁决，本层只暴露冲突。
· **不要求补新观测**：`waist_cm` / `calf_cm` 在注册表里空着也有意义——
  数据哪天来了，自动进入体系，不用改代码。

用法：
    python3 observations.py sources
    python3 observations.py query   --metric weight_kg --days 120
    python3 observations.py resolve --metric weight_kg  --question change
    python3 observations.py resolve --metric body_fat_pct --question level
    python3 observations.py matrix  --days 60          # 全部指标的当前裁决
"""
import argparse
import datetime
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
_WS = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
RAW = os.environ.get("HEALTH_RAW_DIR") or os.path.join(_WS, "data", "raw")
INBODY = os.environ.get("HEALTH_INBODY_DIR") or os.path.join(_WS, "data", "inbody")
DAILY_LOG = os.path.join(_WS, "daily_log.jsonl")
REGISTRY = os.path.join(_HERE, "..", "references", "metrics.json")
RULES = os.path.join(_HERE, "..", "references", "rules.json")

GARMIN_DEVICE = "garmin-venu4"
_BUNDLES = {}          # (end, days) -> canonical bundle：matrix 会按指标反复查询，必须缓存


# ---------------------------------------------------------------- 注册表
def registry():
    with open(REGISTRY, encoding="utf-8") as fh:
        return json.load(fh)


def metric_def(metric):
    reg = registry()
    m = (reg.get("metrics") or {}).get(metric)
    if m is None:
        raise SystemExit("未登记的指标：%s（先写进 references/metrics.json）" % metric)
    return m


def _conf(metric):
    try:
        with open(RULES, encoding="utf-8") as fh:
            return (json.load(fh).get("confidence") or {}).get("metrics", {}).get(metric, {})
    except Exception:
        return {}


# ---------------------------------------------------------------- 观测构造
def obs(metric, value, *, t, source, method, device=None, kind=None, t_end=None,
        coverage=None, conditions=None, derived_from=None, raw_ref=None, note=None):
    m = metric_def(metric)
    return {
        "metric": metric,
        "value": value,
        "unit": m["unit"],
        "kind": kind or m["kind"],
        "t": t,
        "t_end": t_end,
        "coverage": coverage,
        "source": source,
        "method": method,
        "device": device,
        "conditions": conditions or {},
        "derived_from": derived_from,
        "raw_ref": raw_ref,
        "note": note,
    }


# ---------------------------------------------------------------- providers
def _provider_garmin(metric, start, end, days):
    """Garmin 日序列：一个指标一天一条区间观observation。
    覆盖不到整日的，coverage 如实写——这是「这个数代表多少时间」的唯一出处。"""
    if metric not in ("hrv_ms", "rhr_bpm", "sleep_h", "steps_daily",
                      "sedentary_min", "intensity_min", "spo2_sleep_avg", "vo2max"):
        return []
    key = (end, days)
    if key not in _BUNDLES:
        from adapters import get_adapter
        _BUNDLES[key] = get_adapter("garmin-cn").collect(end, days)
    bundle = _BUNDLES[key]
    out = []
    for d in bundle.get("days", []):
        date = d["date"]
        if date < start:
            continue
        cov = d.get("coverage") or {}
        cutoff = cov.get("cutoff_min")
        frac = None if cutoff is None else min(1.0, cutoff / 1440.0)
        t = date + "T00:00:00"
        t_end = cov.get("end_local")
        act = d.get("activity") or {}
        sl = d.get("sleep") or {}

        if metric == "hrv_ms" and (d.get("hrv") or {}).get("last_night_avg") is not None:
            out.append(obs(metric, d["hrv"]["last_night_avg"], t=t, t_end=sl.get("end_local"),
                           source="garmin", method="garmin_hrv", device=GARMIN_DEVICE,
                           coverage=1.0, note="夜间均值"))
        elif metric == "rhr_bpm" and (d.get("rhr") or {}).get("value") is not None:
            out.append(obs(metric, d["rhr"]["value"], t=t, source="garmin",
                           method="garmin_rhr", device=GARMIN_DEVICE, coverage=1.0))
        elif metric == "sleep_h" and sl.get("duration_s"):
            out.append(obs(metric, round(sl["duration_s"] / 3600.0, 2), t=t,
                           t_end=sl.get("end_local"), source="garmin",
                           method="garmin_sleep", device=GARMIN_DEVICE, coverage=1.0))
        elif metric == "steps_daily" and act.get("steps") is not None:
            out.append(obs(metric, act["steps"], t=t, t_end=t_end, source="garmin",
                           method="garmin_usersummary", device=GARMIN_DEVICE,
                           coverage=frac))
        elif metric == "sedentary_min" and act.get("sedentary_min") is not None:
            out.append(obs(metric, act["sedentary_min"], t=t, t_end=t_end, source="garmin",
                           method="garmin_usersummary", device=GARMIN_DEVICE,
                           coverage=frac))
        elif metric == "intensity_min" and act.get("intensity_min") is not None:
            out.append(obs(metric, act["intensity_min"], t=t, t_end=t_end, source="garmin",
                           method="garmin_usersummary", device=GARMIN_DEVICE,
                           coverage=frac))
        elif metric == "spo2_sleep_avg" and (d.get("spo2") or {}).get("sleep_avg") is not None:
            out.append(obs(metric, d["spo2"]["sleep_avg"], t=t, t_end=sl.get("end_local"),
                           source="garmin", method="garmin_spo2", device=GARMIN_DEVICE,
                           coverage=1.0, note="睡眠期均值"))
        elif metric == "vo2max" and (d.get("vo2max") or {}).get("value") is not None:
            v = d["vo2max"]
            out.append(obs(metric, v["value"], t=(v.get("updated") or date) + "T00:00:00",
                           source="garmin", method="garmin_estimate", device=GARMIN_DEVICE,
                           coverage=None, note="设备估算"))
    return out


def _provider_inbody(metric, start, end, days):
    """InBody 事件型：一次扫描产出一组点观测，method 记为机型，跨机型不可比。"""
    mapping = {
        "weight_kg": ("weight_kg", "body"),
        "body_fat_pct": ("percent_body_fat", "pbf"),
        "ffm_kg": ("fat_free_mass_kg", "ffm"),
        "skeletal_muscle_kg": ("skeletal_muscle_mass_kg", "smm"),
        "visceral_fat_level": ("visceral_fat_level", "vfl"),
        "whr": ("whr", "whr"),
    }
    if metric not in mapping:
        return []
    p = os.path.join(INBODY, "index.json")
    if not os.path.isfile(p):
        return []
    with open(p, encoding="utf-8") as fh:
        scans = (json.load(fh) or {}).get("scans") or []
    out = []
    for s in scans:
        dt = s.get("datetime")
        if not dt or dt[:10] < start or dt[:10] > end:
            continue
        key = mapping[metric][0]
        v = s.get(key)
        if v is None:
            continue
        hh = int(dt[11:13]) if len(dt) > 13 else 12
        out.append(obs(metric, v, t=dt, source="inbody",
                       method="bia_%s" % (s.get("equipment") or "?").lower(),
                       device=s.get("equipment"),
                       conditions={"fasted": False,
                                   "time_of_day": "morning" if hh < 10 else
                                                  "evening" if hh >= 18 else "afternoon"},
                       raw_ref=os.path.join("data", "inbody", "raw",
                                            "scan_%s.json" % s.get("raw_datetime"))))
    return out


def _provider_subjective(metric, start, end, days):
    """daily_log：晨起自报。weight_kg 是唯一正式体重口径；历史行落在废弃字段里也照读并标注。"""
    if metric not in ("weight_kg", "sleep_quality"):
        return []
    if not os.path.isfile(DAILY_LOG):
        return []
    out = []
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
            if not d or d < start or d > end:
                continue
            if metric == "weight_kg":
                v = row.get("weight_kg")
                legacy = False
                if v is None:
                    v, legacy = row.get("weight_extra_morning_kg"), True
                if v is None:
                    continue
                out.append(obs(metric, float(v), t=d + "T00:00:00", source="daily_log",
                               method="home_scale", device=None,
                               conditions={"fasted": True, "same_scale": True},
                               note="历史字段 weight_extra_morning_kg" if legacy else None))
            else:
                v = row.get("sleep_quality")
                if v is None:
                    continue
                out.append(obs(metric, float(v), t=d + "T00:00:00", source="daily_log",
                               method="self_report", conditions={}))
    return out


PROVIDERS = {"garmin": _provider_garmin, "inbody": _provider_inbody,
             "subjective": _provider_subjective}


# ---------------------------------------------------------------- 查询
def query(metric, days=90, end=None, sources=None):
    """返回该指标的全部观测（升序），**不合并、不去重、不择一**。"""
    metric_def(metric)                       # 未登记直接报错，防止拼错指标名
    end_d = datetime.date.fromisoformat(end) if end else datetime.date.today()
    start_d = end_d - datetime.timedelta(days=days - 1)
    out = []
    for name, fn in PROVIDERS.items():
        if sources and name not in sources:
            continue
        try:
            out.extend(fn(metric, start_d.isoformat(), end_d.isoformat(), days))
        except Exception as e:               # 单个 provider 失败不拖垮整体
            out.append({"metric": metric, "error": "%s: %s" % (name, e)})
    out = [o for o in out if "error" not in o]
    out.sort(key=lambda o: o["t"])
    return out


def _group_key(o, comparable_on):
    """按可比性字段分组：同组内才允许比较。"""
    vals = []
    for path in comparable_on:
        node = o
        for part in path.split("."):
            node = (node or {}).get(part) if isinstance(node, dict) else None
        vals.append(node)
    return tuple(vals)


def _age_days(t):
    try:
        return (datetime.date.today() - datetime.date.fromisoformat(t[:10])).days
    except Exception:
        return None


def resolve(metric, question="level", days=365, end=None):
    """把「多条观测」收敛为一个**可执行的裁决**（含冲突说明），不产出任何生理结论。

    question="level"  当前值是多少（可合并吗？新鲜吗？）
    question="change" 变了没有（同组两点的差是否超过噪声门槛）
    """
    mdef = metric_def(metric)
    conf = _conf(metric)
    mdc = float(conf.get("mdc_change") or 0)
    comparable_on = mdef.get("comparable_on") or ["method", "device"]
    obs_list = query(metric, days=days, end=end)

    base = {"metric": metric, "label": mdef.get("label"), "unit": mdef.get("unit"),
            "question": question, "n": len(obs_list),
            "mdc_change": mdc, "independent_sources": conf.get("independent_sources"),
            "tier": conf.get("tier")}
    if not obs_list:
        return dict(base, verdict="no-data",
                    message="区间内没有任何观测。缺数据不等于零值。")

    groups = {}
    for o in obs_list:
        groups.setdefault(_group_key(o, comparable_on), []).append(o)
    reps = [g[-1] for g in groups.values()]
    spread = (max(r["value"] for r in reps) - min(r["value"] for r in reps)
              if len(reps) > 1 else 0.0)

    if question == "change":
        # 只看最新一组（同法同机），跨组一律不比较
        newest = max(groups.values(), key=lambda g: g[-1]["t"])
        if len(newest) < 2:
            return dict(base, verdict="insufficient",
                        candidates=len(newest),
                        message="同方法同条件的观测不足两条，无法判断变化。"
                                "（不同方法之间的两次读数不可相减）")
        a, b = newest[-2], newest[-1]
        delta = round(b["value"] - a["value"], 3)
        changed = abs(delta) > mdc
        return dict(base, **{
            "verdict": "changed" if changed else "within-noise",
            "from": {"t": a["t"], "value": a["value"]},
            "to": {"t": b["t"], "value": b["value"]},
            "delta": delta,
            "within": [a["t"][:10], b["t"][:10]],
            "message": ("变化 %.2f %s > 门槛 %.2f，可作为变化表述。"
                        % (delta, mdef["unit"], mdc) if changed else
                        "差 %.2f %s ≤ 门槛 %.2f，在误差内——禁用「上升/下降/改善/恶化」。"
                        % (abs(delta), mdef["unit"], mdc)),
            "conflict_groups": len(groups),
        })

    # question == level
    if len(reps) > 1 and spread > mdc:
        # 多方法并存且分歧超过门槛 → 不择一，给区间。这正是「两个口径不可相减」的机器版本。
        return dict(base, **{
            "verdict": "not-comparable",
            "range": [min(r["value"] for r in reps), max(r["value"] for r in reps)],
            "by_method": [{"method": r["method"], "device": r["device"],
                           "value": r["value"], "t": r["t"],
                           "conditions": r["conditions"]} for r in reps],
            "message": ("存在 %d 种观测方法，分歧 %.2f %s 超过门槛 %.2f。"
                        "**不可合并、不可平均**；应并列呈现或各自成序列。"
                        % (len(reps), spread, mdef["unit"], mdc)),
        })

    chosen = max(obs_list, key=lambda o: o["t"])
    age = _age_days(chosen["t"])
    cad = {"daily": 7, "weekly": 21, "monthly": 45, "quarterly": 100}.get(
        mdef.get("cadence"), 45)
    stale = age is not None and age > cad
    return dict(base, **{
        "verdict": "stale" if stale else "ok",
        "chosen": {"t": chosen["t"], "value": chosen["value"],
                   "method": chosen["method"], "device": chosen["device"],
                   "conditions": chosen["conditions"], "age_days": age},
        "message": ("最新观测距今 %d 天，超过该指标常规节奏（%d 天）——按历史参考处理。"
                    % (age, cad)) if stale else "单一方法内无冲突。",
        "conflict_groups": len(reps),
    })


# ---------------------------------------------------------------- CLI
def cmd_sources(a):
    print("已注册 provider：")
    for name, fn in PROVIDERS.items():
        print("  %-10s %s" % (name, (fn.__doc__ or "").strip().splitlines()[0]))
    print("\n已登记指标：%d 项（references/metrics.json）" % len(registry()["metrics"]))


def cmd_query(a):
    rows = query(a.metric, days=a.days, end=a.end)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    print("%-20s %10s %-8s %-14s %-12s %s"
          % ("时间", "值", "方法", "来源/设备", "覆盖", "条件"))
    for o in rows:
        print("%-20s %10s %-8s %-14s %-12s %s"
              % (o["t"][:19], o["value"], o["method"],
                 "%s/%s" % (o["source"], o["device"] or "-"),
                 "-" if o["coverage"] is None else "%.0f%%" % (o["coverage"] * 100),
                 json.dumps(o["conditions"], ensure_ascii=False)))


def cmd_resolve(a):
    r = resolve(a.metric, question=a.question, days=a.days, end=a.end)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return
    print("【%s · %s】%s" % (r.get("label"), r["question"], r["verdict"]))
    print("  " + r.get("message", ""))
    for k in ("delta", "range", "chosen", "by_method", "mdc_change"):
        if k in r and r[k] not in (None, [], {}):
            print("  %-11s %s" % (k, json.dumps(r[k], ensure_ascii=False)))
    if r.get("tier"):
        print("  %-11s %s" % ("tier", json.dumps(r["tier"], ensure_ascii=False)))


def cmd_matrix(a):
    reg = registry()["metrics"]
    print("%-22s %-16s %-6s %s" % ("指标", "裁决", "n", "说明"))
    for m in reg:
        r = resolve(m, question="level", days=a.days)
        print("%-22s %-16s %-6s %s"
              % (m, r["verdict"], r["n"], (r.get("message") or "")[:46]))


def main():
    ap = argparse.ArgumentParser(description="多重观测框架")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sources").set_defaults(func=cmd_sources)
    p = sub.add_parser("query")
    p.add_argument("--metric", required=True)
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--end", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_query)
    p = sub.add_parser("resolve")
    p.add_argument("--metric", required=True)
    p.add_argument("--question", choices=["level", "change"], default="level")
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--end", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_resolve)
    p = sub.add_parser("matrix")
    p.add_argument("--days", type=int, default=60)
    p.set_defaults(func=cmd_matrix)
    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
