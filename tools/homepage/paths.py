# -*- coding: utf-8 -*-
"""项目路径常量 + body-health 技能唯一依赖点（gtime 时间工具 / rules.json 阈值）。

分层铁律（2026-09-06 定）：
- 本包（视图装配层）禁止解析 Garmin raw DTO——源解析唯一入口是
  adapters/garmin_cn.py（canonical 工厂），本包只消费 canonical。
- 时间换算唯一入口 adapters/gtime.py，阈值唯一入口 rules.json，
  均经本文件导出，禁止别处手搓。
"""
import json
import os

# tools/homepage/paths.py -> tools -> 项目根
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORTS = os.path.join(ROOT, "reports")
RAW = os.path.join(ROOT, "data", "raw")
SITE = os.path.join(ROOT, "site")

import sys  # noqa: E402
SCRIPTS = os.path.join(ROOT, ".workbuddy", "skills", "body-health", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

# —— 时间换算唯一来源：gtime.py（含回归用例，勿在别处手搓）——
from adapters.gtime import (  # noqa: E402,F401
    local_ms_to_hhmm as ms_to_hhmm,
    gmt_ms_to_local_hhmm as gmt_to_hhmm,
    iso_gmt_to_hhmm,
)

# —— 阈值唯一来源：rules.json（判读与展示阈值都改那里）——
RULES_PATH = os.path.join(SCRIPTS, "..", "references", "rules.json")


def load_rules():
    with open(RULES_PATH, encoding="utf-8") as f:
        return json.load(f)
