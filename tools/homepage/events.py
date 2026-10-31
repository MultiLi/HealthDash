# -*- coding: utf-8 -*-
"""日常事件流下发（2026-11-01 建）。

`data/logs/events.jsonl` 本来就存在，21 条，全是判读结论成立的前提：
加班口径的定义、血氧疑虑的排查过程、体重口径的确认。但前端**一条都不显示**——
于是报告说「同日加班叠加散步」，页面上只看得见散步，看不见「加班」这个判断从哪来。

**为什么全量下发而不分页**：20 多条、每条一两百字，全发约 6KB。为这点体量
再开一个按需端点，只会多一条可失败的链路。数量真涨到几百条时再谈分页。

**为什么不放进 days[]**：事件没有「一天一条」的约束，一天可以零条也可以多条，
塞进逐日索引会让行结构变得不确定。它是独立的旁路数据，按日期建索引即可。
"""
import json
import os

from homepage.paths import EVENTS_LOG

# 判读层面最能改变结论的类型排前面：diet/context 是背景，checkup 是排查过程
TYPE_ORDER = {"context": 0, "checkup": 1, "diet": 2, "note": 3}


def events_block(limit_per_day=6):
    """{date: [{t: type, c: content, ts}]}，按日期分组、组内按类型与时间排序。"""
    if not os.path.isfile(EVENTS_LOG):
        return {}
    out = {}
    with open(EVENTS_LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            d = e.get("date")
            content = (e.get("content") or "").strip()
            if not d or not content:
                continue
            out.setdefault(d, []).append({
                "t": e.get("type") or "note",
                "c": content,
                "ts": e.get("ts") or "",
            })
    for d, items in out.items():
        items.sort(key=lambda x: (TYPE_ORDER.get(x["t"], 9), x["ts"]))
        # 单日超过上限就截断并留一条计数，避免某天的长讨论把整个面板撑爆
        if len(items) > limit_per_day:
            out[d] = items[:limit_per_day] + [{
                "t": "more", "c": "另有 %d 条" % (len(items) - limit_per_day), "ts": ""}]
    return out
