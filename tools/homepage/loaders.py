# -*- coding: utf-8 -*-
"""数据读取：canonical 工厂（adapters，唯一源解析层）+ 最新判读简报。

本包从此不读 raw 的业务字段——raw 只允许读 date/synced_at 等落盘元信息。
"""
import glob
import json
import os

from homepage.paths import REPORTS, RAW, SCRIPTS


def all_briefs():
    """{date_str: brief_json} 按日期升序——全部已落盘的判读简报。

    2026-09-07 修复：原先只取最新一份（latest_brief），导致主页除最新分析日外
    所有日期的判读区都是空的。判读是「一天一份」，必须按日装配。
    """
    out = {}
    for p in sorted(glob.glob(os.path.join(REPORTS, "body_brief_*.json"))):
        with open(p, encoding="utf-8") as f:
            try:
                b = json.load(f)
            except Exception:
                continue
        d = b.get("date") or os.path.basename(p)[len("body_brief_"):-len(".json")]
        out[d] = b
    return out


def latest_brief():
    """最新一份判读简报（保留兼容：用于 brief_date / 元信息）。"""
    briefs = all_briefs()
    if not briefs:
        raise SystemExit("reports/ 下没有 body_brief_*.json")
    return briefs[max(briefs)]


def raw_meta():
    """[(date_str, synced_at)] 按日期升序——仅落盘元信息，非业务数据。"""
    out = []
    for p in sorted(glob.glob(os.path.join(RAW, "*.json"))):
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
        out.append((raw.get("date"), raw.get("synced_at") or ""))
    return out


def canon_days():
    """{date_str: canonical_day} —— 经唯一规范化工厂 adapters 获取全量日。

    与 daily_brief 引擎同一 canonical 来源，保证浅加工（展示）与
    深加工（判读）消费完全相同的数据加工结果。
    """
    meta = raw_meta()
    if not meta:
        raise SystemExit("data/raw/ 下没有原始日数据")
    import sys
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)
    from adapters import get_adapter
    latest, earliest = meta[-1][0], meta[0][0]
    import datetime
    span = (datetime.date.fromisoformat(latest)
            - datetime.date.fromisoformat(earliest)).days + 1
    bundle = get_adapter("garmin-cn").collect(latest, span)
    return {d["date"]: d for d in bundle["days"]}, meta
