#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""片段层（hour / daypart）的唯一定义与聚合口径（2026-11-01 建，roadmap P2）。

**分工**（`docs/p2-fragment-layer.md`）：
    index.py      片段落盘进 obs（method=garmin_hour，指标名 hour_*）
    units.py      hour / daypart 两个尺度，**只出值、不出判定**
    fragment_consistency.py   片段回推 vs 日值，逐指标给结论
    本模块        日分区窗口、片段↔日级对照关系、小时归组数学

**为什么片段指标名带 `hour_` 前缀**：`units` 与 `assertions` 读 obs 时按
`substr(t,1,10)` 取日值、**后写覆盖先写**。若片段沿用日级指标名，小时 23 那一行
会静默盖掉日值——一次构建就把所有日层结论改掉，而且不报错。前缀让「未加过滤的
日读取」在任何情况下都取不到片段（结构性防御，不靠自觉加 WHERE）。

**三条不变式**（与设计文档一致，代码里兑现）：
1. 日值仍由日汇总裁决：片段只在 whitelist 且实测恒等时**标记 derived_from**，绝不改值。
2. 判定不下放：hour / daypart 单元 judge 与 delta_verdict 恒为 None（`FRAGMENT_SCALES`）。
3. 不为缺失造假：没有原生粒度的指标不建片段，覆盖不足按实测占比写 coverage。
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_HERE, "..", "references", "fragments.json")

#: 片段尺度。这两个尺度的单元只出值（judge / delta_verdict 恒 None）。
FRAGMENT_SCALES = ("hour", "daypart")
#: 片段指标名前缀——同时也是日级读取的排除条件（见模块 docstring）。
PREFIX = "hour_"

_CACHE = {"raw": None, "mtime": None}


def config(path=None):
    path = path or CONFIG_PATH
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {"version": 0, "dayparts": {}, "pairs": [], "no_counterpart": {}}
    if _CACHE["raw"] is None or _CACHE["mtime"] != mtime:
        with open(path, encoding="utf-8") as fh:
            _CACHE["raw"] = json.load(fh) or {}
        _CACHE["mtime"] = mtime
    return _CACHE["raw"]


def dayparts(path=None):
    """{名称: (起始小时, 结束小时)}，结束小时不含。"""
    out = {}
    for name, span in (config(path).get("dayparts") or {}).items():
        lo, hi = int(span[0]), int(span[1])
        out[name] = (lo, hi)
    return out


def daypart_of(hour):
    """小时序号 → 日分区名。未覆盖的小时返回 None（不猜）。"""
    h = int(hour) % 24
    for name, (lo, hi) in sorted(dayparts().items(), key=lambda kv: kv[1][0]):
        if lo <= h < hi:
            return name
    return None


def daypart_hours(name):
    lo, hi = dayparts()[name]
    return list(range(lo, hi))


def pairs(path=None):
    return list(config(path).get("pairs") or [])


def whitelist(path=None):
    """可由片段聚合、且实测恒等的指标（2c 白名单）。"""
    return [p for p in pairs(path) if p.get("whitelist")]


def fragments_of(pair):
    return list(pair.get("fragment") or [])


def pair_for_day(metric, path=None):
    for p in pairs(path):
        if p.get("day") == metric:
            return p
    return None


def aggregate(values, agg):
    """按 registry 的 agg 聚一个片段的若干小时值。空返回 None。"""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    if agg == "sum":
        return round(sum(vals), 3)
    if agg == "min":
        return round(min(vals), 3)
    if agg == "max":
        return round(max(vals), 3)
    return round(sum(vals) / len(vals), 3)


def hour_key(t):
    """obs 的 t（2026-10-31T14:00:00）→ 小时单元键（2026-10-31T14）。"""
    return t[:13]


def daypart_key(date, part):
    return "%s-%s" % (date, part)
