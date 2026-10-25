# -*- coding: utf-8 -*-
"""活动层视图装配：完整日活动量（步数 / 爬楼 / 小时分布 / 正式活动）。

只消费 canonical。历史遗留的双源回退（us.totalSteps or act.steps）已删——
"usersummary 为准"由 adapters 单点实现，视图层不得再有第二套取数逻辑。
"""
from homepage.paths import load_rules


def build_activity(canon):
    act = canon.get("activity") or {}
    if not act:
        return None
    rules = load_rules()
    a = {
        "date_label": "%s/%s" % (canon["date"][5:7], canon["date"][8:10]),
        "steps": act.get("steps"),
        "steps_goal": rules["activity"]["steps_goal"],
        "distance_km": (round(act["distance_km"], 1)
                        if act.get("distance_km") is not None else None),
        "active_kcal": act.get("active_kcal"),
        "intensity_min": act.get("intensity_min"),
        "sedentary_min": act.get("sedentary_min"),
        "sedentary_max_block_min": act.get("sedentary_max_block_min"),
        "floors": (round(act["floors_up"]) if act.get("floors_up") is not None else None),
        "hourly_steps": act.get("hourly_steps"),
        "sessions": [],
    }
    for s in canon.get("activities") or []:
        a["sessions"].append({
            "name": s.get("name"),
            "type": s.get("type"),
            "time": s.get("start_local"),
            "duration_min": s.get("minutes"),
            "distance_km": s.get("distance_km"),
        })
    return a
