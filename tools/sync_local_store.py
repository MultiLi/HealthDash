#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把主观日志与判读产物镜像进 health.db。

**为什么要有这一步**（2026-10-31）：
`obs / units / assertions` 早就入库了，但另外三样一直留在文件里——
`daily_log.jsonl`（主观标量）、`events.jsonl`（健康事件）、`data/reports/*.json`
（判读产物）。单看每一天都没问题，一旦要**跨日查询**就立刻退化：
「近 90 天体重怎么走的」「历次体检都发现了什么」「红灯分布」——
只能写脚本遍历几十个文件，或者打开主页翻日历。

**写入仍走文件，查询走这里。** JSONL 是追加式原始日志（人可读、可 diff、
可手工改一行），报告 md 是给人读的正文。库只是它们的镜像，删掉重跑本脚本即可。

用法：
    python3 tools/sync_local_store.py           # 同步三张表
    python3 tools/sync_local_store.py --check   # 只报告差异，不写库

同步是幂等的（JSONL 按主键 upsert，events 按 (ts, content) 去重）。
"""
import argparse
import glob
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
SCRIPTS = os.path.join(ROOT, "skills", "body-health", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import db  # noqa: E402
from settings import DAILY_LOG, EVENTS_LOG, REPORTS_DIR  # noqa: E402

#: daily_log 的正式列；其余字段落 extra（含已废弃的 weight_extra_*）
DAILY_COLS = ("date", "recorded_at", "energy", "soreness", "sleep_quality",
              "weight_kg", "bp_sys", "bp_dia", "alcohol_cups", "caffeine_last",
              "phone_down_plan", "overtime", "note")

LEVEL_CHARS = "红黄绿不可判读⚪🔴🟡🟢"


def _jsonl(path):
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _level(summary, which):
    """从判读首段里取档位，**只返回汉字**（红/黄/绿/不可判读）。取不到返回 None。

    历史上有两种写法，都要认：
      新（10-27 起）：「规则档 🔴 红、判读档 🟡 黄（理由：…）」
      旧（10-26 及之前）：「黄（规则档与判读档一致，无降档：…）」——两档相同时
                        不分别写，所以 which 传什么都返回同一个值。

    刻意剥掉 emoji：同一个档位存成「黄」和「🟡黄」两种值，GROUP BY 就会裂成两组，
    「有几个黄日」这种问题都答不对。emoji 属于展示层，不该进库。
    """
    if not summary:
        return None
    m = re.search(which + r"档\s*[🔴🟡🟢⚪]?\s*(红|黄|绿|不可判读)", summary)
    if m:
        return m.group(1)
    m = re.match(r"\s*(?:总评\s*)?[🔴🟡🟢⚪]?\s*(红|黄|绿|不可判读)", summary)
    if m:
        return m.group(1)
    return None


def sync_daily_log(con, dry=False):
    rows = _jsonl(DAILY_LOG)
    payload = []
    for r in rows:
        rec = {c: r.get(c) for c in DAILY_COLS}
        rest = {k: v for k, v in r.items() if k not in DAILY_COLS}
        # 体重列的归一（2026-10-31）：早期行把晨起体重记在废弃字段
        # weight_extra_morning_kg 里，正式列 weight_kg 为空。两者语义相同
        #（都是晨起空腹），所以在库内合并到正式列，同时原字段保留在 extra 里可溯源。
        # 不这么做，「近 90 天体重」在 SQL 里永远是空集。
        if rec["weight_kg"] is None:
            rec["weight_kg"] = r.get("weight_extra_morning_kg")
        rec["extra"] = json.dumps(rest, ensure_ascii=False) if rest else None
        payload.append(rec)
    if not dry and payload:
        con.executemany(
            "INSERT OR REPLACE INTO daily_log (%s) VALUES (%s)"
            % (",".join(DAILY_COLS + ("extra",)),
               ",".join("?" * (len(DAILY_COLS) + 1))),
            [tuple(p[c] for c in DAILY_COLS + ("extra",)) for p in payload])
        con.commit()
    return len(payload)


def sync_events(con, dry=False):
    rows = _jsonl(EVENTS_LOG)
    payload = [(r.get("ts"), r.get("date"), r.get("type"), r.get("content"),
                r.get("source")) for r in rows if r.get("content")]
    if not dry and payload:
        con.executemany(
            "INSERT OR IGNORE INTO events (ts, date, type, content, source) "
            "VALUES (?,?,?,?,?)", payload)
        con.commit()
    return len(payload)


def sync_reports(con, dry=False):
    files = sorted(glob.glob(os.path.join(REPORTS_DIR, "body_brief_*.json")))
    payload = []
    for path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        date = d.get("date") or os.path.basename(path)[len("body_brief_"):-5]
        j = d.get("judgment") or {}
        summary = j.get("summary") or ""
        md = os.path.join(REPORTS_DIR, "body_brief_%s.md" % date)
        payload.append((
            date, "body_brief",
            _level(summary, "规则"), _level(summary, "判读"),
            summary or None,
            len(j.get("findings") or []), len(j.get("cross") or []),
            1 if j else 0,
            None, None, None,
            d.get("synced_at"),
            md if os.path.isfile(md) else None,
            path,
        ))
    if not dry and payload:
        con.executemany(
            "INSERT OR REPLACE INTO reports "
            "(date, kind, rule_level, judge_level, summary, n_findings, n_cross,"
            " has_judgment, data_hash, rules_hash, engine_ver, synced_at,"
            " md_path, json_path) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", payload)
        con.commit()
    return len(payload)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只比对，不写库")
    a = ap.parse_args()
    dry = a.check

    con = db.connect()
    if not dry:
        meta = db.get_meta(con)
        db.set_meta(con, "local_store_synced_at",
                    __import__("datetime").datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))

    n_daily = sync_daily_log(con, dry)
    n_events = sync_events(con, dry)
    n_reports = sync_reports(con, dry)

    print("%s daily_log  %d 行" % ("[check]" if dry else "synced", n_daily))
    print("%s events     %d 条" % ("[check]" if dry else "synced", n_events))
    print("%s reports    %d 份" % ("[check]" if dry else "synced", n_reports))

    if not dry:
        for table in ("daily_log", "events", "reports"):
            n = con.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
            print("  库内 %-10s %d 行" % (table, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
