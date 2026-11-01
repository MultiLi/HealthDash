#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""事件总线写入层（roadmap P1 / 1a）。

**它解决什么**：`events.jsonl` 一直是「谁想写就自己拼一行 JSON」——没有
统一入口，于是字段口径、id、supersede 语义全靠调用方自觉。P1 要求事件流
成为主/客观同构的真相源，写入必须收口到一个地方：**所有新事件经本模块
append，禁止直接往文件里 append 字符串。**

**id 策略（2026-11-01 定）**：新事件显式带 `id`（调用方生成 ULID/UUID 均可）；
存量 21 条 legacy 事件**不改写文件**，id 由内容派生：
    evt_ + sha1("ts|type|content")[:20]
派生是确定性的：同一条 legacy 事件在任何机器、任何时间算出同一个 id，
supercede 可以正常引用它。**不选「改写历史行补 id」是因为那正好违反我们自己
立的 append-only 铁律**——修复历史不应该靠修改历史。

**校验**：字段口径的唯一来源是 `references/event_schema.json`；本模块在写入前
用同一份 schema 校验（与 `tools/validate_events.py` 同源），写坏的事件进不了总线。
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from settings import EVENTS_LOG, REFERENCES_DIR  # noqa: E402

SCHEMA_PATH = os.path.join(REFERENCES_DIR, "event_schema.json")


def schema():
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def derive_id(ev):
    """legacy 事件的确定性 id：evt_ + sha1("ts|type|content")[:20]。"""
    raw = "%s|%s|%s" % (ev.get("ts", ""), ev.get("type", ""), ev.get("content", ""))
    return "evt_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def effective_id(ev):
    """事件的有效 id：显式 id 优先，否则内容派生（legacy 兼容）。"""
    return ev.get("id") or derive_id(ev)


def read(path=None):
    """读全部事件，返回 [{..., "_id": 有效 id}]（升序，与文件顺序一致）。"""
    path = path or EVENTS_LOG
    out = []
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                raise ValueError("第 %d 行不是合法 JSON，总线已损坏" % lineno)
            ev["_id"] = effective_id(ev)
            out.append(ev)
    return out


def superseded_ids(events):
    """已被作废的事件 id 集合。"""
    return {e["supersedes"] for e in events if e.get("supersedes")}


def validate_event(ev, events, schema_doc=None):
    """写入前校验。返回错误列表（空 = 通过）。

    只查「写入侧可控」的东西：字段口径、枚举、ts/date 关系、supersede 目标。
    不在这里判内容是否合理——那是判读层的事。
    """
    s = schema_doc or schema()
    errs = []
    for f in s["required_fields"]:
        if f not in ev:
            errs.append("缺必填字段 %s" % f)
    extra = set(ev) - set(s["required_fields"]) - set(s["optional_fields"]) - {"_id"}
    if extra:
        errs.append("未声明字段 %s（先入 event_schema.json）" % ",".join(sorted(extra)))
    for f in ("type", "source"):
        spec = s["required_fields"].get(f, {})
        if f in ev and spec.get("enum") and ev[f] not in spec["enum"]:
            errs.append("%s=%r 不在枚举内" % (f, ev[f]))
    try:
        dt = datetime.fromisoformat(ev.get("ts", ""))
        if dt.tzinfo is None:
            errs.append("ts 必须带时区偏移")
        elif ev.get("date") and ev["date"] > dt.date().isoformat():
            errs.append("date(%s) 晚于 ts 本地日(%s)" % (ev["date"], dt.date().isoformat()))
    except (ValueError, TypeError):
        errs.append("ts 不是带时区的 ISO8601")
    if not isinstance(ev.get("content"), str) or not (ev.get("content") or "").strip():
        errs.append("content 必须是非空字符串")

    known = {e["_id"] for e in events} if events else set()
    used = superseded_ids(events or [])
    if "supersedes" in ev:
        tgt = ev["supersedes"]
        if not ev.get("id"):
            errs.append("带 supersedes 的事件必须自带 id")
        if known and tgt not in known:
            errs.append("supersedes 目标不存在：%s" % tgt)
        if tgt in used:
            errs.append("目标已被作废过一次：%s" % tgt)
        if ev.get("id") and tgt == ev["id"]:
            errs.append("事件不能作废自己")
    if ev.get("id") and ev["id"] in known:
        errs.append("id 重复：%s" % ev["id"])
    return errs


def append(ev, path=None):
    """校验后追加一条事件，返回其有效 id。**这是写事件的唯一入口。**"""
    path = path or EVENTS_LOG
    events = read(path)
    errs = validate_event(ev, events)
    if errs:
        raise ValueError("拒绝写入：" + "；".join(errs))
    if not ev.get("id"):
        ev["id"] = derive_id(ev)      # 新事件不带 id 时也派生，保证可引用
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return ev["id"]


def supersede(old_id, ev, path=None):
    """新增一条事件并作废 old_id（旧行永不改动）。

    id 在这里先定：校验要求「带 supersedes 的事件必须自带 id」，而 append 是
    校验之后才派生 id 的——调用方因此必须自己生成，纯属多余的心智负担。
    派生是确定性的（sha1(ts|type|content)），先派生与后派生等价。
    """
    ev = dict(ev)
    ev["supersedes"] = old_id
    if not ev.get("id"):
        ev["id"] = derive_id(ev)
    return append(ev, path)


def main():
    ap = argparse.ArgumentParser(description="事件总线写入层 / 只读体检")
    ap.add_argument("--file", default="", help="事件文件（默认 data/logs/events.jsonl）")
    ap.add_argument("--ids", action="store_true", help="打印每条事件的有效 id（含 legacy 派生）")
    ap.add_argument("--check", action="store_true", help="逐条跑写入侧校验")
    a = ap.parse_args()

    path = a.file or EVENTS_LOG
    events = read(path)
    if a.ids:
        for e in events:
            legacy = "" if e.get("id") else "  (legacy 派生)"
            print("%s  %-9s %s%s" % (e["_id"], e.get("type", "?"), e.get("date", "?"), legacy))
        print("\n共 %d 条，其中 legacy 派生 %d 条" % (
            len(events), sum(1 for e in events if not e.get("id"))))
        return 0
    bad = 0
    s = schema()
    for i, e in enumerate(events, 1):
        errs = validate_event(e, events[:i - 1], s)
        if errs:
            bad += 1
            print("L%d %s：%s" % (i, e["_id"], "；".join(errs)))
    print("校验 %d 条，%d 条有问题" % (len(events), bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
