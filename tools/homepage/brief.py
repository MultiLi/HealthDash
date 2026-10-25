# -*- coding: utf-8 -*-
"""判读简报 payload：agent 判读（judgment）→ 前端视图模型。

双层判读架构（2026-09-06）：判读由晚间自动化 agent 产出，写入
body_brief_*.json 的 judgment 字段（cross/sections/summary）；
本函数只做视图整形（sections 按严重度排序）。兼容旧脚本判读结构。
"""

def build_brief_payload(brief):
    level_rank = {"GOOD": 0, "INFO": 1, "NEUTRAL": 1, "WARN": 2, "BAD": 3}
    j = brief.get("judgment") or {}
    raw_sections = j.get("sections") if isinstance(j.get("sections"), dict) \
        else brief.get("sections", {})
    raw_cross = j.get("cross") if j.get("cross") is not None else brief.get("cross", [])
    sections = []
    for title, sec in raw_sections.items():
        sections.append({"title": title, "level": sec.get("level", "NEUTRAL"),
                         "marks": [{"level": lv, "text": tx} for lv, tx in sec.get("marks", [])]})
    sections.sort(key=lambda s: level_rank.get(s["level"], 1))
    return {
        "cross": [{"level": lv, "text": tx} for lv, tx in raw_cross],
        "sections": sections,
        "gaps": brief.get("gaps", []),
    }
