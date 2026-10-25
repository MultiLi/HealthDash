# -*- coding: utf-8 -*-
"""消耗层视图装配：完整日（身体电量 / 压力四档 / 能量账本 / 全天心率）。

只消费 canonical：电量/压力/能量/心率数值与曲线全部来自 adapters，
这里只做展示整形（曲线抽样）。
"""
from homepage.timeutil import downsample


def build_load(canon):
    out = {}
    bb = canon.get("bb") or {}
    if bb.get("low") is not None:
        out["bb"] = {"low": bb.get("low"), "high": bb.get("high"),
                     "night": bb.get("night")}

    st = canon.get("stress") or {}
    if st.get("tiers_min"):
        out["stress"] = {"overall": st.get("overall"), "avg": st.get("avg"),
                         "max": st.get("max"), "dur_min": st["tiers_min"]}

    act = canon.get("activity") or {}
    if act.get("bmr_kcal") is not None:
        out["energy"] = {"bmr": act.get("bmr_kcal"),
                         "active": act.get("active_kcal"),
                         "total": act.get("total_kcal")}

    hr_range = canon.get("hr_range")
    series = canon.get("hr_series") or []
    if hr_range or series:
        out["hr"] = {"max": (hr_range or {}).get("max"),
                     "min": (hr_range or {}).get("min"),
                     "rhr": (canon.get("rhr") or {}).get("value"),
                     "curve": downsample(
                         [["%02d:%02d" % divmod(m, 60), v] for m, v in series],
                         15)}
    return out
