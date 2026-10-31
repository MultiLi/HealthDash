# -*- coding: utf-8 -*-
"""工作区路径的唯一解析点。

**为什么需要这个文件**（2026-10-25 H1）：
在此之前，11 个脚本各自写一遍
    _WORKSPACE = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
这意味着**代码放在哪就是运行时契约**——把 skills/ 从 .workbuddy 搬到项目根，
这 11 处路径会同时断，而且报错位置离根因很远。这才是「代码改不动」的真正原因，
不是代码写得差，是位置被写死在了代码里。

解析顺序（前者优先）：
  1. 环境变量 HEALTH_ROOT          —— 换机器、测试、多副本（如 fixture 目录）
  2. 从本文件向上找**锚点**          —— 布局变了也能自己找到，不用改代码
  3. 按旧布局兜底（scripts/ 上四层） —— 万一锚点都不在，保持历史行为

锚点用「同时存在 tools/ 与 docs/」判定，而不是固定层数：
  - 当前布局  .workbuddy/skills/body-health/scripts/  → 上 4 层
  - 目标布局  skills/body-health/scripts/             → 上 3 层
两者都能命中，搬迁不需要改任何一行解析代码。

⚠️ 不要在本文件里做任何 I/O 或 import 项目内模块——它被所有脚本最先导入，
   一旦有循环依赖，整条链都起不来。
"""
import os

#: 判据：这些路径同时存在才认为是工作区根。用「源码布局」而不是「产物」判定，
#: 因为产物（data/ reports/）在全新 clone 里可能不存在。
_ANCHORS = (("tools", "docs"), ("tools", "notes"), (".git", "tools"))

#: 向上搜索的最大层数——防止在 Home 目录是 git repo 的情况下爬到无关祖先
_MAX_UP = 8


def _looks_like_root(path):
    for group in _ANCHORS:
        if all(os.path.isdir(os.path.join(path, name)) for name in group):
            return True
    return False


def _resolve_root():
    env = os.environ.get("HEALTH_ROOT")
    if env:
        return os.path.abspath(os.path.expanduser(env))

    here = os.path.dirname(os.path.abspath(__file__))
    cur = here
    for _ in range(_MAX_UP):
        cur = os.path.dirname(cur)
        if not cur or cur == os.path.dirname(cur):
            break
        if _looks_like_root(cur):
            return cur

    # 兜底：旧布局（<root>/.workbuddy/skills/body-health/scripts/）
    return os.path.abspath(os.path.join(here, "..", "..", "..", ".."))


ROOT = _resolve_root()


def path(*parts):
    """拼工作区下的路径：path("data", "raw") -> /…/data/raw。"""
    return os.path.join(ROOT, *parts)


# —— 数据单根（2026-10-31 立）——
# 全部个人数据住在 data/ 下，代码目录只留代码。目的不是"整齐"：
# 数据曾经散在 reports/、daily_log.jsonl、notes/events.jsonl、site/.data/
# 六七个位置，防泄露只能靠 .gitignore 逐条列举，漏一条就是公开泄露。
# 现在 .gitignore 只需 `data/` 一行，**新增任何数据目录天然不入库**。
#
# site/ 下的构建产物（index.json / daily / .data）是唯一例外：那是发布契约，
# 服务端与前端从固定路径取，挪了要改部署参数。它们仍是派生数据，已 ignore。
DATA_ROOT = os.environ.get("HEALTH_DATA_DIR") or path("data")

RAW_DIR = os.environ.get("HEALTH_RAW_DIR") or os.path.join(DATA_ROOT, "raw")
INBODY_DIR = os.environ.get("HEALTH_INBODY_DIR") or os.path.join(DATA_ROOT, "inbody")
DB_DIR = os.environ.get("HEALTH_DB_DIR") or os.path.join(DATA_ROOT, "db")
DB_PATH = os.environ.get("HEALTH_DB") or os.path.join(DB_DIR, "health.db")
STATE_DIR = os.environ.get("HEALTH_STATE_DIR") or os.path.join(DATA_ROOT, "state")
REPORTS_DIR = os.environ.get("HEALTH_REPORTS_DIR") or os.path.join(DATA_ROOT, "reports")
LOGS_DIR = os.environ.get("HEALTH_LOGS_DIR") or os.path.join(DATA_ROOT, "logs")
DAILY_LOG = os.path.join(LOGS_DIR, "daily_log.jsonl")
EVENTS_LOG = os.path.join(LOGS_DIR, "events.jsonl")
PROFILE = os.path.join(DATA_ROOT, "profile.json")
GOLDEN = os.path.join(DATA_ROOT, "tests", "golden.json")
SITE_DIR = path("site")
TOOLS_DIR = path("tools")

#: 指标定义与阈值的唯一来源（跟着 skill 目录走，不跟着工作区走）
REFERENCES_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                              "..", "references"))


def garmin_scripts_dir():
    """采集通道（Garmin 网关）的脚本目录。

    2026-10-25 H3：目录从 `.workbuddy/skills/garmin-connect-cn-data__skillhub/`
    搬到 `skills/garmin-connect-cn/`（去掉平台私有痕迹）。`sync.py` 原来把名字
    写死在一处，改这里用候选顺序找，命中 `garmin_gc_api.py` 即认——以后再改名
    也不用动调用方。
    """
    env = os.environ.get("HEALTH_GARMIN_SCRIPTS")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    for rel in (("skills", "garmin-connect-cn", "scripts"),
                ("skills", "garmin-connect-cn-data__skillhub", "scripts"),
                (".workbuddy", "skills", "garmin-connect-cn-data__skillhub", "scripts")):
        cand = os.path.join(ROOT, *rel)
        if os.path.isfile(os.path.join(cand, "garmin_gc_api.py")):
            return cand
    raise RuntimeError("找不到 garmin 采集脚本目录（试过 skills/ 与 .workbuddy/skills/）")


def describe():
    """自检用：把解析结果打印出来，搬迁后第一件事就是看它。"""
    return {
        "root": ROOT,
        "data_root": DATA_ROOT,
        "references": REFERENCES_DIR,
        "raw": RAW_DIR,
        "db": DB_PATH,
        "reports": REPORTS_DIR,
        "logs": LOGS_DIR,
        "profile": PROFILE,
        "anchors_ok": _looks_like_root(ROOT),
        "from_env": bool(os.environ.get("HEALTH_ROOT")),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(describe(), ensure_ascii=False, indent=1))
