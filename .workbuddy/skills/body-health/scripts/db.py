#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""物化层数据库（2026-10-25 建，架构见 notes/architecture_v4.md）。

**这是派生层，不是真相。**
真相只有两个：`data/raw/*.json`（采集快照）与 `references/rules.json`（口径）。
本库随时可以删掉重建：`python3 index.py build --all`。

四张表对应架构三层 + 元信息：
  obs          L1 观测：指标 / 值 / 时间 / 来源 / 方法 / 条件 / 覆盖
  units        L2 单元聚合：日 / 周 / 月 / 滚动窗 × 指标
  assertions   L3 断言：命题 + 布尔 + 证据 + 置信档
  meta         构建指纹（rules_hash / data_hash / engine_ver / built_at）

为什么 SQLite 而不是继续用 JSON（2026-10-25 定）：
  · 现在是「每次 build 全量重解析 raw」，54 天 2.44s，随天数线性增长；
    物化后查询是毫秒级，且「过去 90 天红灯出现在哪些维度」从读 90 份报告变成一次 SQL。
  · 单文件、stdlib、无服务进程，符合「单用户 + 日频」的规模。
"""
import hashlib
import json
import os
import sqlite3

_HERE = os.path.dirname(os.path.abspath(__file__))
from settings import ROOT as _WORKSPACE  # noqa: E402  路径唯一解析点（settings.py）
DB_PATH = os.environ.get("HEALTH_DB") or os.path.join(
    _WORKSPACE, "data", "state", "health.db")
RULES_PATH = os.path.join(_HERE, "..", "references", "rules.json")
METRICS_PATH = os.path.join(_HERE, "..", "references", "metrics.json")

SCHEMA = """
CREATE TABLE IF NOT EXISTS obs (
  metric     TEXT NOT NULL,
  t          TEXT NOT NULL,              -- 区间起点（日观测为当日 00:00）
  t_end      TEXT,                       -- 区间终点（睡眠=醒来时刻）
  value      REAL NOT NULL,
  unit       TEXT,
  kind       TEXT,                       -- point / interval / derived
  source     TEXT NOT NULL,              -- garmin / inbody / daily_log
  method     TEXT NOT NULL,              -- garmin_hrv / bia_h40 / home_scale …
  device     TEXT,
  coverage   REAL,                       -- 该观测覆盖了多长时间（1.0=整日）
  conditions TEXT,                       -- JSON：fasted / same_scale / time_of_day …
  derived_from TEXT,                     -- JSON：派生观测的输入
  raw_ref    TEXT,
  PRIMARY KEY (metric, t, source, method)
);
CREATE INDEX IF NOT EXISTS obs_metric_t ON obs(metric, t);

CREATE TABLE IF NOT EXISTS units (
  scale      TEXT NOT NULL,              -- day / week / month / roll7 / roll28 / roll90
  key        TEXT NOT NULL,              -- 2026-10-24 / 2026-W43 / 2026-10 / 滚动窗末日
  metric     TEXT NOT NULL,
  value      REAL,                       -- 单元代表值（agg 决定 mean/median/sum/last）
  agg        TEXT,
  n          INTEGER,                    -- 有实测值的天数
  coverage   REAL,                       -- n / expected（expected 已按「进行中」截到当天）
  judge      TEXT,                       -- ok / no / na
  judge_rate REAL,                       -- 逐日判定的达标率（judge_mode=daily 时唯一有意义）
  delta_v    REAL,                       -- 与上一单元（滚动窗＝非重叠位移）
  delta_mdc  REAL,                       -- 该指标的门槛；NULL=未标定
  delta_verdict TEXT,                    -- changed / within-noise / insufficient / uncalibrated
  delta_vs   TEXT,                       -- 对比对象
  delta_note TEXT,
  start      TEXT, end TEXT, expected INTEGER,
  PRIMARY KEY (scale, key, metric)
);
CREATE INDEX IF NOT EXISTS units_sm ON units(scale, metric, key);

CREATE TABLE IF NOT EXISTS assertions (
  id         TEXT NOT NULL,              -- 稳定标识，可跨日统计
  scale      TEXT NOT NULL,
  key        TEXT NOT NULL,
  date       TEXT,                       -- 该单元的代表日（day=当日；week/month/roll=末日）
  dimension  TEXT,                       -- sleep / activity / autonomic / load /
                                         -- stress_energy / capacity / bodycomp / data / cross
  subject    TEXT,                       -- 涉事指标（逗号分隔）
  verdict    INTEGER NOT NULL,           -- 1 触发 / 0 未触发（未触发的也落库，便于回测 diff）
  level      TEXT,                       -- BAD / WARN / GOOD / NEUTRAL
  confidence TEXT,                       -- 由 confidence.tier 推导
  rule_ref   TEXT,                       -- 该断言依据的规则键
  evidence   TEXT,                       -- JSON：**只含已算好的数字**
  action_hint TEXT,
  PRIMARY KEY (id, scale, key)
);
CREATE INDEX IF NOT EXISTS assert_date ON assertions(date);
CREATE INDEX IF NOT EXISTS assert_id ON assertions(id);

-- 设备/信源自己的**分类判定**（不是数值）：准备度五档、HRV status、睡眠子项评级…
-- 与 obs 分开的原因：它们是字符串分档，塞进 REAL 列只能编码成魔数，且语义上
-- 「设备怎么判」与「我们怎么判」必须能并列展示、互不覆盖。
CREATE TABLE IF NOT EXISTS grades (
  metric     TEXT NOT NULL,              -- readiness_level / hrv_status / sleep_sub_remPercentage …
  t          TEXT NOT NULL,
  source     TEXT NOT NULL,
  method     TEXT NOT NULL,
  grade      TEXT NOT NULL,              -- 原始分档串（MODERATE / UNBALANCED / FAIR）
  rank       INTEGER,                    -- 有天然序的给序号（准备度 0..4），否则 NULL
  note       TEXT,
  PRIMARY KEY (metric, t, source, method)
);
CREATE INDEX IF NOT EXISTS grades_metric_t ON grades(metric, t);

CREATE TABLE IF NOT EXISTS obs_day_gate (
  date       TEXT PRIMARY KEY,
  level      TEXT,                       -- complete / partial / no_data / missing_raw
  cutoff_min INTEGER,                    -- 该日数据覆盖到第几分钟（1440 制）
  steps      INTEGER,
  note       TEXT
);

CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


# ------------------------------------------------------------------ 连接
def connect(path=None):
    p = path or DB_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    ensure_columns(con)
    return con


# ------------------------------------------------------------------ 指纹
def sha1_file(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest()[:12]
    except OSError:
        return None


def engine_ver():
    """引擎版本 = 本层四个脚本内容的联合哈希。改判读逻辑即改指纹。"""
    h = hashlib.sha1()
    for name in ("db.py", "units.py", "assertions.py", "index.py"):
        p = os.path.join(_HERE, name)
        if os.path.isfile(p):
            with open(p, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()[:12]


def rules_hash():
    return sha1_file(RULES_PATH)


def data_hash(con):
    """观测集的联合哈希：任何一条观测变化都会改变它。"""
    h = hashlib.sha1()
    for row in con.execute(
            "SELECT metric, t, value FROM obs ORDER BY metric, t"):
        h.update(("%s|%s|%s;" % (row["metric"], row["t"], row["value"])).encode())
    return h.hexdigest()[:12]


def set_meta(con, k, v):
    con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)", (k, str(v)))


def get_meta(con):
    return {r["k"]: r["v"] for r in con.execute("SELECT k, v FROM meta")}


# ------------------------------------------------------------------ 写入
OBS_COLS = ("metric", "t", "t_end", "value", "unit", "kind", "source", "method",
            "device", "coverage", "conditions", "derived_from", "raw_ref")
UNIT_COLS = ("scale", "key", "metric", "value", "agg", "n", "coverage", "judge", "judge_rate",
             "delta_v", "delta_mdc", "delta_verdict", "delta_vs", "delta_note",
             "start", "end", "expected")
ASSERT_COLS = ("id", "scale", "key", "date", "dimension", "subject", "verdict",
               "level", "confidence", "rule_ref", "evidence", "action_hint")


def upsert_obs(con, rows):
    con.executemany(
        "INSERT OR REPLACE INTO obs (%s) VALUES (%s)"
        % (",".join(OBS_COLS), ",".join("?" * len(OBS_COLS))),
        [tuple(r.get(c) for c in OBS_COLS) for r in rows])


def upsert_units(con, rows):
    con.executemany(
        "INSERT OR REPLACE INTO units (%s) VALUES (%s)"
        % (",".join(UNIT_COLS), ",".join("?" * len(UNIT_COLS))),
        [tuple(r.get(c) for c in UNIT_COLS) for r in rows])


def upsert_grades(con, rows):
    con.executemany(
        "INSERT OR REPLACE INTO grades(metric,t,source,method,grade,rank,note) "
        "VALUES (:metric,:t,:source,:method,:grade,:rank,:note)", rows)


def ensure_columns(con):
    """给已存在的库补列（库是派生层，但没必要每次全删重建）。"""
    want = {"units": {"judge_rate": "REAL"}}
    for table, cols in want.items():
        have = {r["name"] for r in con.execute("PRAGMA table_info(%s)" % table)}
        for col, decl in cols.items():
            if col not in have:
                con.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, decl))
    con.commit()


def upsert_assertions(con, rows):
    con.executemany(
        "INSERT OR REPLACE INTO assertions (%s) VALUES (%s)"
        % (",".join(ASSERT_COLS), ",".join("?" * len(ASSERT_COLS))),
        [tuple(r.get(c) for c in ASSERT_COLS) for r in rows])


# ------------------------------------------------------------------ 读
def load_rules():
    with open(RULES_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def load_metrics():
    with open(METRICS_PATH, encoding="utf-8") as fh:
        return (json.load(fh) or {}).get("metrics") or {}


def metric_conf(rules, metric):
    """指标的噪声门槛登记（rules.confidence.metrics）。未登记返回空 dict。"""
    return ((rules.get("confidence") or {}).get("metrics") or {}).get(metric) or {}
