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
SITE = os.path.join(ROOT, "site")

import sys  # noqa: E402


def _find_scripts():
    """定位 body-health 的脚本目录。

    2026-10-25 之前这里写死 `.workbuddy/skills/body-health/scripts`——引擎住在
    WorkBuddy 的私有目录里，而呈现层用一条字符串指过去。这是「呈现层对引擎的
    反向硬依赖」，也是 skills 搬到根目录前的第二个阻塞点。

    现在按候选顺序找，命中 `settings.py` 即认为是（它是引擎的唯一路径解析点）。
    搬迁到 `skills/body-health/` 后第一个候选自动命中，不需要改任何代码。
    """
    env = os.environ.get("HEALTH_SCRIPTS_DIR")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    for rel in ("skills", "body-health", "scripts"), \
                (".workbuddy", "skills", "body-health", "scripts"):
        cand = os.path.join(ROOT, *rel)
        if os.path.isfile(os.path.join(cand, "settings.py")):
            return cand
    raise RuntimeError(
        "找不到 body-health scripts 目录（试过 skills/ 与 .workbuddy/skills/）"
        "——可设 HEALTH_SCRIPTS_DIR 指定")


SCRIPTS = _find_scripts()
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

# —— 时间换算唯一来源：gtime.py（含回归用例，勿在别处手搓）——
from adapters.gtime import (  # noqa: E402,F401
    local_ms_to_hhmm as ms_to_hhmm,
    gmt_ms_to_local_hhmm as gmt_to_hhmm,
    iso_gmt_to_hhmm,
)

# —— 阈值唯一来源：rules.json（判读与展示阈值都改那里）——
# 走 settings.REFERENCES_DIR：口径跟着 skill 目录走，不跟着工作区走。
# 数据位置同理走 settings：2026-10-31 起全部数据在 data/ 单根下，
# 呈现层不再自己拼 ROOT/reports（那是旧布局，搬家即断）。
from settings import (  # noqa: E402,F401
    REFERENCES_DIR, RAW_DIR as RAW, REPORTS_DIR as REPORTS,
    DAILY_LOG, EVENTS_LOG,
)
RULES_PATH = os.path.join(REFERENCES_DIR, "rules.json")


def load_rules():
    """口径 = rules.json + data/profile.json（个人档案已从 rules.json 拆出）。

    复用引擎的 db.load_rules，避免呈现层自己拼一份合并逻辑——那正是
    「同一知识住两处」的典型形态，改一处漂一处。
    """
    from db import load_rules as _engine_load_rules
    return _engine_load_rules()
