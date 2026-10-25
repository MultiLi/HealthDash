#!/usr/bin/env python3
"""事实包构建：canonical + 全部计算事实 → reports/facts_<date>.json。

定位（2026-09-06 Sir 定，双层判读架构）：
  脚本层只负责"算"——所有数字在此算好，附上原始序列，**不做任何定性判断**；
  判读（交叉归因、红黄绿、建议）由晚间自动化的 agent 按SKILL.md 判读框架执行。
  本脚本同时写 body_brief_<date>.json（canonical 契约，供健康主页消费），
  判读产出经 --merge-judgment 合并进同一文件。

用法:
  uv run --python 3.11 python fact_pack.py --date D --days N
  uv run --python 3.11 python fact_pack.py --date D --merge-judgment reports/judgment_D.json
"""
import argparse
import datetime
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
_WS = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
sys.path.insert(0, _WS)  # 供 import tools 无关，仅为路径完整性
REPORTS = (os.environ.get("HEALTH_REPORTS_DIR")
           or os.path.join(_WS, "reports"))
EVENTS = os.path.join(_WS, "notes", "events.jsonl")

from daily_brief import (load_rules, load_subjective, compute_z2,
                         detect_exercise, build_trend, _get, SUBJ_LOG)
from adapters import get_adapter


def load_events(days=3):
    """events.jsonl 最近 N 天 → 列表（升序）。文件不存在返回空。"""
    try:
        rows = [json.loads(l) for l in open(EVENTS) if l.strip()]
    except FileNotFoundError:
        return []
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    return [r for r in rows if r.get("date", "") >= cutoff]


def build(date, days):
    rules = load_rules()
    bundle = get_adapter("garmin-cn").collect(date, days)
    all_days = bundle["days"]
    day = next((d for d in all_days if d["date"] == date), {}) or {}

    # —— 睡眠序列与连续短睡（night_rows 最新在前；从最近一夜往回数，只算不判）——
    nights = [d for d in all_days if d["date"] <= date][-rules["sleep"]["debt_lookback_days"] - 1:]
    min_sleep = rules["sleep"]["min_healthy_duration_min"]
    night_rows = [{"date": d["date"],
                   "duration_min": (_get(d, "sleep", "duration_s") or 0) // 60 or None,
                   "bed": _get(d, "sleep", "start_local"),
                   "score": _get(d, "sleep", "score"),
                   "short_of_min": (bool(d.get("sleep")) and
                                    (_get(d, "sleep", "duration_s") or 0) // 60 < min_sleep)}
                  for d in reversed(nights)]
    # 从最近一夜往回的连续短睡数
    recent_short_streak = 0
    for r in night_rows:
        if r["short_of_min"]:
            recent_short_streak += 1
        else:
            break
    # 最近一夜达标时，其前的连续短睡数
    prior_short = 0
    if night_rows and not night_rows[0]["short_of_min"]:
        for r in night_rows[1:]:
            if r["short_of_min"]:
                prior_short += 1
            else:
                break

    # —— 身体电量净值序列（升序）——
    bb_series = [{"date": d["date"],
                  "charged": _get(d, "activity", "bb_charged"),
                  "drained": _get(d, "activity", "bb_drained")}
                 for d in all_days]
    for b in bb_series:
        b["net"] = (b["charged"] - b["drained"]
                    if b["charged"] is not None and b["drained"] is not None else None)

    # —— Z2 / 运动窗 / 趋势 ——
    hr_hourly = day.get("hr_hourly") or {}
    windows = detect_exercise(hr_hourly, rules,
                              day.get("hr_sample_interval_min")) if hr_hourly else []
    z2_today = compute_z2(hr_hourly, rules, day.get("hr_sample_interval_min"),
                          day.get("hr_series")) if (hr_hourly or day.get("hr_series")) else None
    z2_win_min, z2_days_n = 0, 0
    for d in all_days:
        hh, ss = d.get("hr_hourly") or {}, d.get("hr_series") or []
        if hh or ss:
            z2_win_min += compute_z2(hh, rules, d.get("hr_sample_interval_min"),
                                     ss)["minutes"]
            z2_days_n += 1

    subj = load_subjective(SUBJ_LOG)

    # InBody 体成分（事件型，2026-10-25 接入）：只认「目标日及以前」的扫描，
    # 门槛判定由 check_inbody 用 rules.json 的门槛算出，判读方只读结论不重算。
    try:
        import check_inbody
        inbody = check_inbody.evaluate(as_of=date)
    except Exception as e:
        inbody = {"verdict": "FAILED", "ok": False,
                  "notes": ["InBody 闸门不可用：%s" % e]}

    # 完整性结论随事实包下发（2026-10-18 建）：报告里的「数据截止」一行
    # 一律引用这里，禁止判读方自己从 synced_at 推断——两套口径必然打架。
    try:
        import data_gate
        completeness = data_gate.inspect(date)
    except Exception as e:
        completeness = {"level": "unknown", "blocking": ["闸门不可用：%s" % e]}

    facts = {
        "date": date, "source": bundle["source"],
        "synced_at": bundle.get("synced_at") or "",
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "gaps": bundle.get("gaps", []),
        "completeness": completeness,
        "inbody": inbody,
        "thresholds_ref": os.path.join(_HERE, "..", "references", "rules.json"),
        "trend": build_trend(all_days, rules),
        "target_day": day,
        "computed": {
            "sleep": {
                "nights_asc": night_rows,
                "min_healthy_min": min_sleep,
                "recent_short_streak": recent_short_streak,
                "prior_short_if_last_ok": prior_short,
                "late_bedtime": rules["sleep"]["late_bedtime"],
                "last_night": night_rows[0] if night_rows else None,
            },
            "bb_net_asc": bb_series,
            "z2": {"today": z2_today, "window_days": z2_days_n,
                   "window_min": z2_win_min,
                   "weekly_target_min": rules["z2"]["weekly_target_min"],
                   "effective_session_min": rules["z2"]["effective_session_min"]},
            "exercise_windows": windows,
            "acwr": _get(day, "training", "acwr"),
            "acute_load": _get(day, "training", "acute_load"),
            "vo2max": day.get("vo2max"),
            "spo2": day.get("spo2"),
            "hrv": {"today": day.get("hrv"),
                    "prev_day": next((_get(d, "hrv", "last_night_avg")
                                      for d in reversed(all_days)
                                      if d["date"] < date and d.get("hrv")), None)},
            "rhr": day.get("rhr"),
            "subjective_today": subj.get(date),
            "subjective_recent": [subj[d] for d in sorted(subj)[-7:] if d],
        },
        "events_recent": load_events(3),
    }

    os.makedirs(REPORTS, exist_ok=True)
    facts_path = os.path.join(REPORTS, f"facts_{date}.json")
    json.dump(facts, open(facts_path, "w"), ensure_ascii=False, indent=1)
    # canonical 契约文件（健康主页消费；判读字段由 agent 经 --merge-judgment 写入）
    brief = {"date": date, "source": bundle["source"],
             "gaps": bundle.get("gaps", []), "synced_at": bundle.get("synced_at"),
             "canonical": all_days, "judgment": None}
    json.dump(brief, open(os.path.join(REPORTS, f"body_brief_{date}.json"), "w"),
              ensure_ascii=False, indent=1)
    print("facts:", facts_path)
    print("brief-contract:", os.path.join(REPORTS, f"body_brief_{date}.json"))
    return facts


def merge_judgment(date, judgment_path):
    """agent 判读产出（cross/sections/summary）合并进 body_brief 契约文件。"""
    p = os.path.join(REPORTS, f"body_brief_{date}.json")
    brief = json.load(open(p))
    brief["judgment"] = json.load(open(judgment_path))
    json.dump(brief, open(p, "w"), ensure_ascii=False, indent=1)
    print("judgment merged ->", p)


def main():
    ap = argparse.ArgumentParser(description="健康事实包（只算不判）")
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--days", type=int, default=4)
    ap.add_argument("--merge-judgment", dest="merge_judgment", default=None)
    a = ap.parse_args()
    if a.merge_judgment:
        merge_judgment(a.date, a.merge_judgment)
    else:
        build(a.date, a.days)


if __name__ == "__main__":
    main()
