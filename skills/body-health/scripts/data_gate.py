#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据完整性闸门（data gate）—— 「这一天的报告能不能开写」的唯一判据。

【为什么存在（2026-10-18 实测事故）】
10-17 的 23:30 报告基于 22:05 截止的数据写成，报告里写着「非终版」。
次日复核时对同一天重新拉取，拿回来的是**完整日**：
    步数 9948 → 10830（晚上那段走路此前完全没进账）
    久坐 835 → 943 min
    强度分钟 3 → 4
即：数据不全不是因为拿不到，而是因为**没等**。同理 10-18 的 23:30 报告
截止在 19:34，缺 4.5 小时。根因是「报告锚定钟点（23:30）而不是数据完整」。

【本层的定位】
排在 sync 之后、fact_pack / 判读之前的一道**机器闸门**，只回答一个问题：
这一天在云端还有没有没拿到的数据。
判读方（agent）**无权**自行声明「数据完整」——完整性由这里的字段级判据给出。
闸门只算不判，零网络，零主观语言。

【判据（全部来自 canonical.coverage，适配器已归一化）】
    cutoff_min = 该日数据终点距当日 00:00 的分钟数
                 1440 = 整日；1174 = 19:34 截止；0 = 当天没有任何覆盖
    has_data   = totalSteps 是否为 null（null = 没戴表/没同步）

四级：
    missing_raw  没有 raw 文件            → 先跑 sync；不应出报告
    no_data      有响应但无佩戴数据        → **不会再有数据**，照常出「不可判读」
    partial      有数据，截止 < 23:50     → **云端大概率还有**，等 / 回补，不写报告
    complete     截止 ≥ 23:50             → 可以写报告

非阻断的交叉校验（只产出 caveats，用来给报告写「口径声明」，不挡报告）：
    grid        逐时步数网格合计 vs totalSteps，偏差 >15% → 逐时层不全，降权
    awake_den   久坐+活动+高强度 合计 ≥ 1400 min 且当日无睡眠 → 久坐分母伪影
    layers      dailyhr / activities 缺失 → 活动与 Z2 判读降权

用法：
    python3 data_gate.py check  --date 2026-10-17            # JSON
    python3 data_gate.py check  --date 2026-10-17 --text
    python3 data_gate.py matrix --days 5                     # 近 N 天一览
    python3 data_gate.py matrix --days 5 --json
"""
import argparse
import datetime
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from settings import ROOT as _WORKSPACE  # noqa: E402  路径唯一解析点（settings.py）
RAW_DIR = os.environ.get("HEALTH_RAW_DIR") or os.path.join(_WORKSPACE, "data", "raw")
RULES = os.path.join(_HERE, "..", "references", "rules.json")

LEVELS = ("missing_raw", "no_data", "partial", "complete")


def load_gate_rules():
    try:
        with open(RULES, encoding="utf-8") as fh:
            return json.load(fh).get("gate") or {}
    except Exception:
        return {}


def _cutoff_label(minutes):
    if minutes is None:
        return "未知"
    if minutes >= 1440:
        return "24:00"
    return "%02d:%02d" % (minutes // 60, minutes % 60)


def inspect(date, rules=None, now=None):
    """单日完整性判定。只读 canonical，不解析 raw、不联网。"""
    rules = rules if rules is not None else load_gate_rules()
    now = now or datetime.datetime.now()
    complete_min = int(rules.get("complete_cutoff_min", 1430))
    tol_pct = float(rules.get("hourly_grid_tolerance_pct", 15))
    empty_awake = int(rules.get("empty_day_awake_min", 1400))
    deadline_h = int(rules.get("publish_deadline_hour_next_day", 9))
    repull_days = int(rules.get("repull_days", 3))

    out = {
        "date": date,
        "level": None,
        "cutoff_min": None,
        "cutoff": None,
        "has_data": None,
        "blocking": [],
        "caveats": [],
        "checks": {},
        "repullable": False,
        "publish_by": None,
        "can_publish_truncated": False,
    }

    d = datetime.date.fromisoformat(date)
    publish_by = datetime.datetime.combine(
        d + datetime.timedelta(days=1), datetime.time(deadline_h, 0))
    out["publish_by"] = publish_by.isoformat(timespec="minutes")
    out["can_publish_truncated"] = bool(now >= publish_by)

    raw = os.path.join(RAW_DIR, "%s.json" % date)
    if not os.path.isfile(raw):
        out["level"] = "missing_raw"
        out["blocking"].append("原始数据未落盘（sync 未运行或失败）")
        out["repullable"] = True
        return out

    from adapters import get_adapter
    try:
        bundle = get_adapter("garmin-cn").collect(date, 1)
        day = next((x for x in bundle["days"] if x["date"] == date), {})
    except Exception as e:                       # 适配器异常不该让整条流水线停摆
        out["level"] = "partial"
        out["blocking"].append("适配器解析失败：%s" % e)
        out["repullable"] = True
        return out

    cov = day.get("coverage") or {}
    cutoff = cov.get("cutoff_min")
    out["cutoff_min"] = cutoff
    out["cutoff"] = _cutoff_label(cutoff)
    out["has_data"] = cov.get("has_data")

    if not cov.get("has_data"):
        out["level"] = "no_data"
        out["blocking"].append("当日无佩戴/同步数据（空占位响应），不会再有新数据")
        return out

    if cutoff is None:
        out["level"] = "partial"
        out["blocking"].append("覆盖终点未知（wellnessEndTimeLocal 缺失）")
        out["repullable"] = (now.date() - d).days <= repull_days
        return out

    # ---------------- 交叉校验（只进 caveats） ----------------
    act = day.get("activity") or {}
    steps = act.get("steps")
    out["steps"] = steps
    out["sedentary_min"] = act.get("sedentary_min")
    grid = act.get("hourly_steps")
    if steps and grid:
        gsum = sum(n for _, n in grid)
        dev = abs(gsum - steps) / float(steps) * 100.0
        out["checks"]["grid"] = {"sum": gsum, "total": steps, "dev_pct": round(dev, 1)}
        if dev > tol_pct:
            out["caveats"].append(
                "逐时步数网格合计 %d ≠ 实测 %d（差 %.0f%%，阈值 %.0f%%）——"
                "逐时层不完整，逐时分布/分档判读降权，活动总量以 usersummary 为准"
                % (gsum, steps, dev, tol_pct))
    sed, a, h = (act.get("sedentary_min"), act.get("active_min"),
                 act.get("highly_active_min"))
    if None not in (sed, a, h):
        tot = sed + a + h
        out["checks"]["awake_den"] = {"sum_min": tot, "sedentary": sed,
                                      "active": a, "highly": h}
        if tot >= empty_awake:
            out["caveats"].append(
                "久坐+活动+高强度合计 %d min ≈ 整日（≥%d），当日无睡眠记录 → "
                "久坐含「整日计入清醒」的分母伪影，**久坐绝对值与占比均不可引用**"
                % (tot, empty_awake))
    if not day.get("hr_hourly") and not day.get("hr_series"):
        out["caveats"].append("逐时心率缺失 → 运动窗/Z2 当日不可判读")
    if not day.get("activities"):
        out["caveats"].append("无正式活动记录（不等于没活动，活动量以 usersummary 为准）")

    # ---------------- 判定 ----------------
    if cutoff >= complete_min:
        out["level"] = "complete"
    else:
        out["level"] = "partial"
        out["blocking"].append(
            "数据截止 %s，缺 %d 分钟（最后一段 %s–24:00 未进账）"
            % (out["cutoff"], 1440 - cutoff, out["cutoff"]))
        out["repullable"] = (now.date() - d).days <= repull_days
    return out


def matrix(days=5, end=None, rules=None):
    """近 N 天一览（升序）。"""
    end = end or datetime.date.today()
    rules = rules if rules is not None else load_gate_rules()
    rows = []
    for i in range(days - 1, -1, -1):
        d = (end - datetime.timedelta(days=i)).isoformat()
        rows.append(inspect(d, rules))
    return rows


# ---------------------------------------------------------------- CLI
def _fmt_row(r):
    mark = {"complete": "OK  ", "partial": "WAIT", "no_data": "EMPTY",
            "missing_raw": "NO-RAW"}[r["level"]]
    steps = (r["checks"].get("grid") or {}).get("total")
    return "%-12s %-7s %-6s %6s  %s" % (
        r["date"], mark, r["cutoff"] or "-", steps if steps is not None else "-",
        (r["blocking"][0] if r["blocking"] else ""))


def cmd_check(a):
    r = inspect(a.date)
    if a.text:
        print(_fmt_row(r))
        for c in r["caveats"]:
            print("  ⚠ " + c)
    else:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0


def cmd_matrix(a):
    rows = matrix(a.days, rules=load_gate_rules())
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    print("%-12s %-7s %-6s %6s  %s" % ("日期", "状态", "截止", "步数", "说明"))
    for r in rows:
        print(_fmt_row(r))
    bad = [r for r in rows if r["level"] in ("partial", "missing_raw")]
    if bad:
        print("\n%d 天未完整，可回补：%s"
              % (len(bad), "、".join(r["date"] for r in bad)))
    return 0


def main():
    ap = argparse.ArgumentParser(description="数据完整性闸门")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check")
    p.add_argument("--date", default=datetime.date.today().isoformat())
    p.add_argument("--text", action="store_true")
    p.set_defaults(func=cmd_check)
    p = sub.add_parser("matrix")
    p.add_argument("--days", type=int, default=5)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_matrix)
    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
