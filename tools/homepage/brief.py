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


def brief_head(payload, limit=86):
    """判读的「一句话结论」（2026-11-01 建）。

    用途：判读流水——把 60 多天的结论压成一行行，让人能看出
    「同一个判断连续出现了几天」，而不是一天一份地翻。

    取 `cross[0]` 的第一句而不是 summary：summary 是给当天看的完整交代
    （常带档位理由与截断声明），塞进列表会变成一坨；cross 首条才是
    「这天到底怎么了」的正面结论。

    **超长就截断并加省略号**——列表里宁可话不完整，也不要一行占三行。
    """
    cross = payload.get("cross") or []
    if not cross:
        return None
    level = cross[0].get("level") or "NEUTRAL"
    text = (cross[0].get("text") or "").strip()
    if not text:
        return None
    # 按中文句末断句，取第一句；没有句号就用整段再截断
    head = text
    for sep in ("。", "；", ";"):
        i = text.find(sep)
        if 0 < i < limit:
            head = text[:i]
            break
    if len(head) > limit:
        head = head[:limit].rstrip("，、,· ") + "…"
    return {"head": head, "lv": level}
