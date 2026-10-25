#!/usr/bin/env python3
"""步态指标基线 —— 只读本地 data/raw/*.json，零联网、不触发佳明登录。

用途：拉取/复测步频、步长、步速、全天步数密度（含未记录活动的步行）。
来源字段：
  data/raw/D.json -> activities_own[]  : averageRunningCadenceInStepsPerMinute / avgStrideLength / distance / duration / steps / typeKey
  data/raw/D.json -> summary_chart.values[] : 96 格，每格 15 分钟，含 steps
  data/raw/D.json -> usersummary_daily.totalSteps

用法：
  python3 gait_metrics.py                 # 全部日期
  python3 gait_metrics.py --days 14       # 最近 14 个有数据的日期
  python3 gait_metrics.py --min-min 17    # 只统计时长 >= 17 分钟的活动（默认 17）
  python3 gait_metrics.py --md            # 输出 markdown 表，便于贴档
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import sys as _sys
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)
from settings import RAW_DIR  # noqa: E402  路径唯一解析点（settings.py）

WALK_TYPES = {"walking", "running", "hiking", "treadmill_running", "indoor_walking", "track_running"}


def type_key(a):
    """raw 里类型落在 activityType.typeKey（dict），兼容字符串写法。"""
    at = a.get("activityType")
    if isinstance(at, dict):
        return (at.get("typeKey") or "").lower()
    if isinstance(at, str):
        return at.lower()
    return (a.get("typeKey") or "").lower()


def load_raw():
    out = []
    for f in sorted(glob.glob(os.path.join(RAW_DIR, "*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if "date" in d:
            out.append(d)
    return out


def activity_rows(days, min_min, types):
    rows = []
    for d in days:
        for a in (d.get("activities_own") or []):
            tk = type_key(a)
            if types and tk not in types:
                continue
            dur = a.get("duration") or 0
            dist = a.get("distance") or 0
            minutes = dur / 60.0
            if minutes < min_min:
                continue
            rows.append({
                "date": d["date"],
                "name": a.get("activityName") or tk,
                "typeKey": tk,
                "loc": a.get("locationName"),
                "min": minutes,
                "km": dist / 1000.0 if dist else None,
                "steps": a.get("steps"),
                "cad": a.get("averageRunningCadenceInStepsPerMinute"),
                "stride_cm": a.get("avgStrideLength"),
                "kmh": (dist / dur * 3.6) if dur and dist else None,
            })
    rows.sort(key=lambda r: (r["date"], -r["min"]))
    return rows


def density_rows(days):
    rows = []
    for d in days:
        sc = (d.get("summary_chart") or {}).get("values") or []
        st = [(x.get("steps") or 0) for x in sc]
        if not st:
            continue
        best, bi = 0, 0
        for i in range(max(1, len(st) - 3)):
            s = sum(st[i:i + 4])
            if s > best:
                best, bi = s, i
        rows.append({
            "date": d["date"],
            "total": (d.get("usersummary_daily") or {}).get("totalSteps"),
            "peak15": max(st),
            "peak15_density": round(max(st) / 15.0, 1),
            "best60": best,
            "best60_density": round(best / 60.0, 1),
        })
    rows.sort(key=lambda r: r["date"])
    return rows


def fmt(v, nd=1):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{round(v, nd)}"
    return str(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0, help="只取最近 N 个有数据日期（0=全部）")
    ap.add_argument("--min-min", type=float, default=17.0, help="活动时长门槛（分钟）")
    ap.add_argument("--md", action="store_true", help="输出 markdown 表格")
    ap.add_argument("--all-types", action="store_true", help="不限活动类型")
    args = ap.parse_args()

    days = load_raw()
    if not days:
        print(f"未找到 raw 数据：{RAW_DIR}", file=sys.stderr)
        return 1
    if args.days:
        days = days[-args.days:]

    types = None if args.all_types else WALK_TYPES
    acts = activity_rows(days, args.min_min, types)
    dens = density_rows(days)

    print(f"# 步态指标  data/raw  {days[0]['date']} ~ {days[-1]['date']}  ({len(days)} 天有数据)\n")

    print(f"## 表1 已记录活动（时长 >= {args.min_min:g} 分钟）")
    if not acts:
        print("（无）\n")
    else:
        if args.md:
            print("| 日期 | 类型 | 地点 | 分钟 | km | 步数 | 步频 | 步长cm | km/h |")
            print("|---|---|---|---|---|---|---|---|---|")
            for r in acts:
                print(f"| {r['date']} | {r['typeKey']} | {r['loc'] or '-'} | {round(r['min'],1)} | {fmt(r['km'],2)} | "
                      f"{r['steps'] or '-'} | {fmt(r['cad'])} | {fmt(r['stride_cm'])} | {fmt(r['kmh'],2)} |")
        else:
            print(f"{'日期':12}{'类型':10}{'地点':10}{'分钟':>7}{'km':>7}{'步数':>7}{'步频':>7}{'步长cm':>8}{'km/h':>7}")
            for r in acts:
                print(f"{r['date']:12}{r['typeKey']:10}{(r['loc'] or '-'):10}{round(r['min'],1):>7}{fmt(r['km'],2):>7}"
                      f"{r['steps'] or '-':>7}{fmt(r['cad']):>7}{fmt(r['stride_cm']):>8}{fmt(r['kmh'],2):>7}")
        cads = [r["cad"] for r in acts if r["cad"]]
        if cads:
            print(f"\n步频：min {round(min(cads),1)} / max {round(max(cads),1)} / "
                  f"均值 {round(sum(cads)/len(cads),1)}  (n={len(cads)})")
        # 长时段样本（碎片走动+停顿会把均值拉低，不足以代表步行步频）
        long_cads = [r["cad"] for r in acts if r["cad"] and r["min"] >= 17]
        if long_cads:
            print(f"  其中 >=17min 样本：min {round(min(long_cads),1)} / max {round(max(long_cads),1)} / "
                  f"均值 {round(sum(long_cads)/len(long_cads),1)}  (n={len(long_cads)})  ← 这个才是\"步行步频\"口径")
        print()

    print("## 表2 全天步数密度（每格 15 分钟，含未记录活动的步行）")
    if args.md:
        print("| 日期 | 日总步 | 峰值15min | 峰值密度 | 连续60min | 60min密度 |")
        print("|---|---|---|---|---|---|")
        for r in dens:
            print(f"| {r['date']} | {r['total'] or '-'} | {r['peak15']} | {r['peak15_density']} | "
                  f"{r['best60']} | {r['best60_density']} |")
    else:
        print(f"{'日期':12}{'日总步':>8}{'峰值15min':>10}{'峰值密度':>9}{'连续60min':>11}{'60min密度':>10}")
        for r in dens:
            print(f"{r['date']:12}{r['total'] or '-':>8}{r['peak15']:>10}{r['peak15_density']:>9}"
                  f"{r['best60']:>11}{r['best60_density']:>10}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
