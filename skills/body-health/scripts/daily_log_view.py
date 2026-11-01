#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""daily_log 的物化视图层（roadmap P1 / 1b）。

**定调**（`docs/vision.md` §三）：事件流是真相源，`daily_log.jsonl` 是**日层
物化视图**——一行一天、有则更新。它从不该是第二个真相：谁想改主观数据，
应该往事件流 append 一条新事件（旧事件 supersede），再让本模块把视图重推出来。

**过渡期的现实**：补数现在由 agent 直接改 `daily_log.jsonl`（班次指令这么写）。
一步翻转会把「唯一的写入口」和「唯一的读入口」同时换掉，出事无人兜。所以
本模块提供三段能力，按 1b → 1c 顺序使用：

    --sync      文件 → 事件：把 daily_log 里**事件流还没有或值不同**的字段补成
                measurement/diet/context 事件（值变了就 supersede 旧事件）。
                这是双写期的桥梁：写入口仍是文件，但真相开始向事件流沉淀。
    --verify    对账：由事件重推一份视图，与文件逐字段比，报差异。双写期每天
                跑它，差异必须为零；不为零说明有人绕过了桥。
    --rebuild   事件 → 文件：**翻转后的正式动作**。视图重推成功（先写临时文件
                再原子替换），此后文件可随时删掉重建。

字段口径只在这里定义一次：daily_log 字段 ↔ 事件 type/payload.metric 的映射
见 FIELDS。丢掉字段（如 `weight_extra_*`）不再映射——它们已迁成 measurement
事件，正式体重口径只有 `weight_kg`。
"""
import argparse
import json
import os
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from settings import DAILY_LOG, EVENTS_LOG  # noqa: E402
import eventbus                             # noqa: E402

# (daily_log 字段, 事件 type, 附加 payload, 中文名)
FIELDS = [
    ("energy",          "measurement", {"metric": "energy", "scale": "1-5"}, "精力"),
    ("soreness",        "measurement", {"metric": "soreness", "scale": "1-5"}, "酸痛"),
    ("sleep_quality",   "measurement", {"metric": "sleep_quality", "scale": "1-5"}, "睡眠自评"),
    ("weight_kg",       "measurement", {"metric": "weight_kg", "unit": "kg", "part": "morning"}, "晨起体重"),
    ("bp_sys",          "measurement", {"metric": "bp_sys", "unit": "mmHg"}, "收缩压"),
    ("bp_dia",          "measurement", {"metric": "bp_dia", "unit": "mmHg"}, "舒张压"),
    ("alcohol_cups",    "diet",        {"metric": "alcohol_cups", "unit": "杯"}, "饮酒"),
    ("caffeine_last",   "diet",        {"metric": "caffeine_last"}, "末次咖啡因"),
    ("overtime",        "context",     {"metric": "overtime"}, "加班"),
    ("phone_down_plan", "context",     {"metric": "phone_down_plan"}, "放下手机计划"),
    ("note",            "note",        {"metric": "note"}, "备注"),
]
BY_FIELD = {f[0]: f for f in FIELDS}
BY_METRIC = {f[2]["metric"]: f for f in FIELDS}


def read_rows(path=None):
    path = path or DAILY_LOG
    if not os.path.isfile(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def latest_by_metric(events):
    """{(date, metric): 事件}：只保留未被 supersede 的最新一条。"""
    dead = eventbus.superseded_ids(events)
    out = {}
    for e in events:
        if e["_id"] in dead:
            continue
        m = (e.get("payload") or {}).get("metric")
        if not m or m not in BY_METRIC:
            continue
        out[(e["date"], m)] = e
    return out


def _ev_for(date, field, value, ts, prev=None):
    fname, etype, extra, label = BY_FIELD[field]
    ts = ts or (date + "T22:00:00+08:00")
    ev = {
        "ts": ts, "date": date, "type": etype, "source": "conversation",
        "content": "主观补数：%s %s" % (label, value),
        "payload": dict(extra, value=value),
        "confidence": "high",
    }
    return ev


def cmd_sync(a):
    rows = read_rows(a.file or DAILY_LOG)
    events = eventbus.read(a.events or EVENTS_LOG)
    cur = latest_by_metric(events)
    appended = skipped = 0
    for r in rows:
        d = r.get("date")
        if not d:
            continue
        ts = r.get("recorded_at") or (d + "T22:00:00+08:00")
        for field, _t, _x, _l in FIELDS:
            if field not in r or r[field] is None:
                continue
            val = r[field]
            prev = cur.get((d, (BY_FIELD[field][2]["metric"])))
            if prev is not None:
                if (prev.get("payload") or {}).get("value") == val:
                    skipped += 1
                    continue
                ev = _ev_for(d, field, val, ts, prev)
                eventbus.supersede(prev["_id"], ev, path=a.events or EVENTS_LOG)
            else:
                eventbus.append(_ev_for(d, field, val, ts), path=a.events or EVENTS_LOG)
            appended += 1
    print("sync：新增/更新事件 %d 条，已一致跳过 %d 个字段" % (appended, skipped))
    return 0


def build_view(events):
    """由事件重推视图：{date: {field: value, recorded_at}}。"""
    dead = eventbus.superseded_ids(events)
    rows = {}
    for e in events:
        if e["_id"] in dead:
            continue
        p = e.get("payload") or {}
        m = p.get("metric")
        if not m or m not in BY_METRIC:
            continue
        field = BY_METRIC[m][0]
        row = rows.setdefault(e["date"], {"date": e["date"]})
        row[field] = p.get("value")
        row["recorded_at"] = max(row.get("recorded_at") or "", e.get("ts") or "")
    return rows


def cmd_verify(a):
    rows = {r["date"]: r for r in read_rows(a.file or DAILY_LOG)}
    view = build_view(eventbus.read(a.events or EVENTS_LOG))
    diffs = []
    for d in sorted(set(rows) | set(view)):
        f, v = rows.get(d, {}), view.get(d, {})
        for field, _t, _x, _l in FIELDS:
            a_v, b_v = f.get(field), v.get(field)
            if a_v != b_v:
                diffs.append((d, field, a_v, b_v))
    if not diffs:
        print("对账通过：文件与事件视图逐字段一致（%d 天 / %d 字段口径）"
              % (len(rows), len(FIELDS)))
        return 0
    print("对账不一致 %d 处（文件值 ← → 事件视图值）：" % len(diffs))
    for d, field, x, y in diffs:
        print("  %s %-16s %r ← → %r" % (d, field, x, y))
    print("\n修法：写入口只认一处——要改数据请 append 事件，然后 --rebuild；"
          "文件里手改出来的差异会在这里被抓住。")
    return 1


def cmd_rebuild(a):
    view = build_view(eventbus.read(a.events or EVENTS_LOG))
    path = a.file or DAILY_LOG
    rows = []
    for d in sorted(view):
        r = view[d]
        # 字段顺序固定：便于 diff 与人工读
        out = {"date": d}
        for field, _t, _x, _l in FIELDS:
            if r.get(field) is not None:
                out[field] = r[field]
        if r.get("recorded_at"):
            out["recorded_at"] = r["recorded_at"]
        rows.append(out)
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    print("rebuild：由事件重推 %d 天 → %s" % (len(rows), os.path.basename(path)))
    return 0


def main():
    ap = argparse.ArgumentParser(description="daily_log 物化视图（事件为真相）")
    ap.add_argument("--file", default="", help="视图文件（默认 data/logs/daily_log.jsonl）")
    ap.add_argument("--events", default="", help="事件文件（默认 data/logs/events.jsonl）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync", help="文件 → 事件（双写期桥梁，幂等）").set_defaults(func=cmd_sync)
    sub.add_parser("verify", help="对账：事件视图 vs 文件").set_defaults(func=cmd_verify)
    sub.add_parser("rebuild", help="事件 → 文件（翻转后的正式动作）").set_defaults(func=cmd_rebuild)
    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
