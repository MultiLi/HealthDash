#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重算最近 N 天的事实包与 canonical 契约，并回并各自的判读。

【为什么需要】
补跑历史日时 sync.py 会**重拉该日 raw**（目标日恒重拉）。该日 raw 一变，
其它日 brief 里的「近几日趋势」数字就和它不一致了——这正是 memory 里
「补跑历史日后必须重跑相邻日 fact_pack」这条纪律的由来，过去靠手工记得。

本脚本把这条纪律机械化：
  · 不做任何判读、**不改任何 md 报告**（报告文字是终版，不自动重写）；
  · 只重建 reports/facts_D.json + reports/body_brief_D.json（主页消费的契约）；
  · 重建会把 judgment 字段清空，故随后把已存在的 reports/judgment_D.json 回并回去，
    保证主页的「判读卡」不因重算而消失。

用法：
  python3 refresh_recent.py [--days 4] [--end YYYY-MM-DD] [--dry-run]
"""
import argparse
import datetime
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import fact_pack                       # noqa: E402
from sync import raw_path              # noqa: E402

REPORTS = fact_pack.REPORTS


def main():
    ap = argparse.ArgumentParser(description="重算最近 N 天事实包 + 回并判读")
    ap.add_argument("--days", type=int, default=4)
    ap.add_argument("--end", default=None, help="窗口末日（YYYY-MM-DD），默认今天")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    end = (datetime.date.fromisoformat(a.end) if a.end
           else datetime.date.today())
    rows = []
    for i in range(a.days):
        d = (end - datetime.timedelta(days=a.days - 1 - i)).isoformat()
        if not os.path.isfile(raw_path(d)):
            rows.append((d, "skip", "无 raw，不动"))
            continue
        if a.dry_run:
            rows.append((d, "dry-run", "将重建 facts/brief 并回并判读"))
            continue
        facts = fact_pack.build(d, a.days)
        jp = os.path.join(REPORTS, f"judgment_{d}.json")
        if os.path.isfile(jp):
            fact_pack.merge_judgment(d, jp)
            note = "已回并 judgment"
        else:
            note = "无 judgment（该日无判读，正常）"
        day = facts.get("target_day") or {}
        act = day.get("activity") or {}
        sl = day.get("sleep") or {}
        suffix = (f"步数 {act.get('steps')} / 睡眠 "
                  f"{(sl.get('duration_s') or 0) // 60}min / RHR "
                  f"{(day.get('rhr') or {}).get('value')}")
        rows.append((d, "rebuilt", f"{note}｜{suffix}"))

    w = max(len(r[0]) for r in rows)
    print(f"refresh_recent — 窗口末日 {end.isoformat()}，共 {a.days} 天")
    for d, st, note in rows:
        print(f"  {d:<{w}}  {st:<8} {note}")
    print("注意：本脚本不改写历史 md 报告；若补跑后某日数字确实变化，"
          "该日报告需按单一终版口径另行改写。")


if __name__ == "__main__":
    main()
