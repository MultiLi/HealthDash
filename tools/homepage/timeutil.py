# -*- coding: utf-8 -*-
"""视图装配层的展示工具：星期、时长格式化、曲线抽样。

纪律：只有"展示整形"住这里（下采样/格式化/文案映射）。
任何源字段解析住 adapters/，任何阈值住 rules.json。
"""
from datetime import date as _date

WEEKDAY = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}


def weekday_cn(date_str):
    y, m, d = map(int, date_str.split("-"))
    return WEEKDAY[_date(y, m, d).weekday()]


def fmt_sleep(seconds):
    h, rem = divmod(int(seconds), 3600)
    return h, rem // 60


def downsample(pairs, step):
    """[[label, v], ...] 每隔 step 取一个（纯展示减密，不改值）。"""
    return [p for i, p in enumerate(pairs or []) if i % step == 0]
