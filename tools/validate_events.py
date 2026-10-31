#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""事件总线校验器：events.jsonl 的解析器与守门命令。

为什么需要它（roadmap P0②）：事件流是『流先记账』的地基，地基的纪律
（append-only、supersede 语义、字段口径）不能靠自觉——必须有命令能一行行
验，且本地与 CI 跑同一份。schema 唯一来源是
skills/body-health/references/event_schema.json，本文件只实现校验逻辑，
不复制字段清单——schema 改了枚举，这里自动跟上。

用法：
    python3 tools/validate_events.py                 # 校验 data/logs/events.jsonl（存在才校验）
    python3 tools/validate_events.py --file <path>   # 校验指定文件
    python3 tools/validate_events.py --quiet         # 只出结果行

退出码：0 = 通过（warning 允许）；1 = 有错误；2 = 用法/环境错误。
"""
import argparse
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = os.path.join(ROOT, "skills", "body-health", "references", "event_schema.json")
DEFAULT_LOG = os.path.join(ROOT, "data", "logs", "events.jsonl")


def load_schema():
    try:
        with open(SCHEMA, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        print("FATAL schema 读取失败 %s：%s" % (SCHEMA, e), file=sys.stderr)
        sys.exit(2)


def parse_ts(ts):
    """ISO8601 且带时区。返回 datetime 或 None。"""
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else None
    except (ValueError, TypeError):
        return None


def validate(path, schema, quiet):
    errors, warnings = [], []
    seen_ids, superseded = set(), set()
    n = 0
    with open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line:
                continue
            n += 1
            try:
                ev = json.loads(line)
            except ValueError as e:
                errors.append("L%d 不是合法 JSON：%s" % (lineno, e))
                continue
            if not isinstance(ev, dict):
                errors.append("L%d 事件必须是对象" % lineno)
                continue

            # 字段口径：缺必填、多未声明，都挡下
            for f in schema["required_fields"]:
                if f not in ev:
                    errors.append("L%d 缺必填字段 %s" % (lineno, f))
            extra = set(ev) - set(schema["required_fields"]) - set(schema["optional_fields"])
            if extra:
                errors.append("L%d 未声明字段 %s（先入 event_schema.json 再用）" % (lineno, ",".join(sorted(extra))))

            # ts / date 一致性
            dt = parse_ts(ev.get("ts")) if ev.get("ts") else None
            if ev.get("ts") and dt is None:
                errors.append("L%d ts 不是带时区的 ISO8601：%r" % (lineno, ev.get("ts")))
            if dt is not None and ev.get("date"):
                # date 是归属日、ts 是录入时刻：跨零点补记时 date 合法地早于 ts 本地日，
                # 只有「归属未来」才是错。这条判据已在存量数据上实证（3 条真补记）。
                if ev["date"] > dt.date().isoformat():
                    errors.append("L%d date(%s) 晚于 ts 本地日(%s)：归属未来必是写错" % (
                        lineno, ev["date"], dt.date().isoformat()))

            # 枚举
            for f in ("type", "source"):
                spec = schema["required_fields"].get(f, {})
                if f in ev and spec.get("enum") and ev[f] not in spec["enum"]:
                    errors.append("L%d type/source 越界：%r 不在 %s" % (lineno, ev[f], spec["enum"]))
            conf = schema["optional_fields"]["confidence"]
            if "confidence" in ev and ev["confidence"] not in conf["enum"]:
                errors.append("L%d confidence 越界：%r" % (lineno, ev["confidence"]))

            # content
            if "content" in ev and (not isinstance(ev["content"], str) or not ev["content"].strip()):
                errors.append("L%d content 必须是非空字符串" % lineno)

            # id / supersede 语义
            eid = ev.get("id")
            if eid:
                if eid in seen_ids:
                    errors.append("L%d 事件 id 重复：%s" % (lineno, eid))
                seen_ids.add(eid)
            elif "supersedes" in ev:
                errors.append("L%d 带 supersedes 的事件必须自带 id" % lineno)
            if "supersedes" in ev:
                tgt = ev["supersedes"]
                if tgt not in seen_ids:
                    errors.append("L%d supersedes 指向的 id 不存在或在其后：%s" % (lineno, tgt))
                elif tgt in superseded:
                    errors.append("L%d 旧事件被重复作废：%s" % (lineno, tgt))
                elif tgt == eid:
                    errors.append("L%d 事件不能作废自己" % lineno)
                else:
                    superseded.add(tgt)
            if eid is None and n > 0:
                warnings.append("L%d legacy 事件无 id（P1 事件化时补发）" % lineno)

    if not quiet:
        print("事件校验 %s：%d 条" % (os.path.relpath(path, ROOT), n))
        for w in warnings:
            print("  ⚠ %s" % w)
        for e in errors:
            print("  ✗ %s" % e)
    if errors:
        if not quiet:
            print("FAIL：%d 个错误 / %d 个警告" % (len(errors), len(warnings)))
        return 1
    if not quiet:
        print("PASS（%d 个警告）" % len(warnings))
    return 0


def main():
    ap = argparse.ArgumentParser(description="事件总线校验器")
    ap.add_argument("--file", default="", help="要校验的 jsonl 路径（默认 data/logs/events.jsonl，存在才校验）")
    ap.add_argument("--quiet", action="store_true", help="只出结果行")
    a = ap.parse_args()

    schema = load_schema()
    path = a.file or DEFAULT_LOG
    if not os.path.isfile(path):
        # 数据不入库：CI / 干净 checkout 里没有事件文件，属于正常缺省，不算失败
        if not a.quiet:
            print("SKIP：%s 不存在（数据不入库，CI 里必然没有；用 --file 指定 fixture 可真跑）" % path)
        return 0
    return validate(path, schema, a.quiet)


if __name__ == "__main__":
    sys.exit(main())
