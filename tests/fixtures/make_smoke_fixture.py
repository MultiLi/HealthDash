#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke 冒烟测试的合成数据 fixture 生成器（roadmap P0③，backlog C12）。

**为什么是它**：`smoke_site.mjs` 的渲染断言需要一份 index.json + 按月分片，
而两者都不入库（数据边界是硬纪律）。CI 里没有真实数据，冒烟只能 SKIP——
「没验」长期充当掩护。本脚本在 CI 运行时**现场合成**一份完全虚构的数据，
让冒烟在无 data/ 的环境真跑、真测到判读分支。

**设计原则（为什么复用后端代码而不是手写 index.json）**：
- 索引与分片由真实的 `homepage.indexlayer.build_index` 生成——day_row /
  collect_block / 四态判定的口径与线上一字不差，fixture 永不与后端漂移。
- 只有三个块是合成的：
    · units     —— 判定存于 health.db（CI 无库）。这里模拟**后端会下发什么**，
                   只验前端渲染契约；判定本身的正确性归 golden 回测管。
    · events    —— data/logs/events.jsonl 不入库，给两条合成事件占位。
    · nudge     —— daily_log.jsonl 同上。
- 所有数值由确定性公式生成（正弦扰动），**无任何真实个人数据**；
  生成产物写进调用方指定的临时目录，产物本身不入 git。

用法: python3 tests/fixtures/make_smoke_fixture.py <输出目录>
"""
import datetime
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from homepage.paths import load_rules                    # noqa: E402
import homepage.indexlayer as indexlayer                 # noqa: E402
from homepage.indexlayer import shard_days               # noqa: E402
from homepage.timeutil import weekday_cn                 # noqa: E402

# 关键防线（2026-11-01 CI 事故）：units_block 会 db.connect()，而 sqlite 在裸
# checkout 里会**凭空创建**空的 data/db/health.db——随后的 golden 检查靠「文件
# 存在」判定可跑，空库让它在 CI 里假性运行并失败。fixture 的 units 本来就是
# 合成的，直接把真实读库口短路掉，杜绝这个副作用。
indexlayer.units_block = lambda rules, day_rows=None: {"_fixture": "synthetic"}

SPAN_START = "2026-09-01"   # smoke 有写死 2026-09 系列锚点，跨度必须覆盖 9 月
HRV_BAND = {"low": 45, "high": 75}

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
LV = ["GOOD", "FAIR", "POOR"]


def wob(i, amp, base):
    """确定性「伪随机」：第 i 天的扰动值，任何机器任何时间跑都一样。"""
    return base + amp * math.sin(i * 1.7) + (amp / 2) * math.sin(i * 0.53)


def hhmm(minutes):
    return "%02d:%02d" % (minutes // 60 % 24, minutes % 60)


def hr_curve(seed):
    """48 个半小时点的心率曲线（load.hr.curve）。"""
    return [[hhmm(m * 30), int(58 + 8 * math.sin(m * 0.7 + seed) + (25 if 14 <= m / 2 <= 16 else 0))]
            for m in range(48)]


def synth_recovery(i, date_str, full=True):
    """晨间快照：昨夜睡眠 / HRV / 准备度。full=False 时整体缺（失败日）。"""
    if not full:
        return None
    secs = int(wob(i, 3600, 25200))          # 6~8h 区间摆动
    r_score = int(wob(i, 18, 68))
    return {
        "date_label": date_str[5:].replace("-", "/"),
        "hrv": {
            "value": int(wob(i, 9, 54)), "weekly_avg": 55,
            "low": HRV_BAND["low"], "high": HRV_BAND["high"],
            "status": "BALANCED" if i % 3 else "UNBALANCED",
            "curve": [["03:00", 45], ["04:00", 40], ["05:00", 38], ["06:00", 42], ["07:00", 44]],
        },
        "sleep": {
            "h": secs // 3600, "m": secs % 3600 // 60, "seconds": secs,
            "score": int(wob(i, 15, 70)), "bed": "23:%02d" % (i % 50), "wake": "07:%02d" % (i % 50),
            "stages": {"deep_min": 80 + i % 20, "light_min": 230 + i % 30,
                       "rem_min": 55 + i % 15, "awake_min": 60 + i % 40},
            "stage_pct": {"deep": 22, "light": 62, "rem": 16},
            "sub_scores": [{"k": "总时长", "q": "一般"}, {"k": "睡眠压力", "q": "良好"}],
            "awake_count": 2 + i % 4, "restless": 8 + i % 6,
            "resp": {"avg": 14.1, "low": 13.2, "high": 15.0},
            "breathing": "无异常", "sleep_stress": 21 + i % 8,
            "need_h": 7.5, "nap_min": 0,
        },
        "sleep_hr": {"avg": 53 + i % 4, "min": 50 + i % 3},
        "spo2": {"avg": 96, "low": 90, "avg7": 95.2,
                 "curve": [["00:00", 95], ["04:00", 94], ["08:00", 96]]},
        "readiness": {
            "score": r_score,
            "level": ["POOR", "LOW", "MODERATE", "HIGH", "PRIME"][min(r_score // 20, 4)],
            "feedback": "合成反馈", "recovery": "预计已充分恢复",
            # 多点轨迹（运动后重算）——smoke 用它挑 trailDay 验日内轨迹行
            "series": [["07:30", r_score, "晨起"], ["13:00", r_score + 3, "运动后"]],
        },
    }


def synth_load(i, full=True):
    if not full:
        return None
    return {
        "bb": {"low": 25, "high": 70, "night": {"start": 21, "end": 70, "delta": 25}},
        "stress": {"overall": int(wob(i, 8, 38)), "avg": int(wob(i, 8, 38)),
                   "max": 80 + i % 15,
                   "dur_min": {"rest": 200 + i % 60, "low": 640, "medium": 180, "high": 30}},
        "energy": {"bmr": 2100, "active": float(int(wob(i, 150, 280))),
                   "total": 2400 + i % 100},
        "hr": {"max": 100 + i % 30, "min": 52, "rhr": int(wob(i, 3, 53)),
               "curve": hr_curve(i)},
    }


def synth_activity(i, date_str, full=True):
    if not full:
        return None
    steps = int(wob(i, 3500, 7500))
    return {
        "date_label": date_str[5:].replace("-", "/"),
        "steps": steps, "steps_goal": 10000,
        "distance_km": round(steps / 1300.0, 2),
        "active_kcal": float(int(wob(i, 120, 260))),
        "intensity_min": 10 + i % 25,
        "sedentary_min": 480 + i % 200,
        "sedentary_max_block_min": 120 + i % 80,
        "floors": 3 + i % 8,
        "hourly_steps": [[h, max(0, int(300 * math.sin(h / 3.0 + i))) ] for h in range(24)],
        "sessions": [{"name": "步行", "type": "walking", "time": "13:05",
                      "duration_min": 30.0, "distance_km": 2.4}],
    }


def synth_brief(i):
    """合成判读简报（内容全部中性，无真实结论）。"""
    return {
        "cross": [{"level": "INFO", "text": "合成交叉判读：各指标均在合成基线内， fixture 用例。"}],
        "sections": [{"level": "INFO", "title": "睡眠",
                      "marks": [{"level": "GOOD", "text": "合成用例：睡眠时长在合成区间内。"}]}],
        "gaps": [],
    }


def build_days(today):
    """跨度内逐日合成完整 day 视图（build() 的输出形态）。"""
    start = datetime.date.fromisoformat(SPAN_START)
    end = datetime.date.fromisoformat(today)
    days = []
    for k in range((end - start).days + 1):
        d = start + datetime.timedelta(days=k)
        date_str = d.isoformat()
        wd = d.isoweekday()
        base = {
            "date": date_str, "label": date_str[5:].replace("-", "/"),
            "weekday": WEEKDAYS[wd - 1], "morning": date_str == today,
            "has_brief": True, "brief_head": None, "brief": None,
            "collect": {"raw": True, "errors": {}},
            "coverage": None if date_str == today else {"cutoff_min": 1440, "has_data": True},
        }
        if date_str == today:
            # 今天：进行中。晨间快照有（昨夜睡眠可判），load/activity 还没到点。
            base["recovery"] = synth_recovery(k, date_str)
            base["load"] = None
            base["activity"] = None
            base["hero"] = {"score": None, "band": "—", "note": "进行中"}
            days.append(base)
            continue
        # 固定剧本：10-07 采集失败、10-14~10-15 无原始数据（覆盖四态里的 failed/missing）
        if date_str == "2026-10-07":
            base["collect"] = {"raw": True, "errors": {"hrv_daily": "合成错误：fixture 采样失败"}}
            base["coverage"] = {"has_data": False}
        elif date_str in ("2026-10-14", "2026-10-15"):
            base["collect"] = {"raw": False, "errors": {}}
            base["coverage"] = {"has_data": False}
        failed = base["collect"]["errors"] or not base["collect"]["raw"]
        base["recovery"] = synth_recovery(k, date_str, full=not failed)
        base["load"] = synth_load(k, full=not failed)
        base["activity"] = synth_activity(k, date_str, full=not failed)
        base["has_brief"] = not failed
        base["hero"] = ({"score": int(wob(k, 12, 72)), "band": LV[k % 3],
                         "note": "合成数据日 · 等权合成"} if not failed
                        else {"score": None, "band": "—", "note": "无数据"})
        if not failed:
            base["brief"] = synth_brief(k)
            # brief_head 是 {head, lv} 对象（brief.brief_head() 的返回形态），
            # 不是字符串——build_index 原样透传，前端按 .head/.lv 读
            base["brief_head"] = {"head": "合成判读流水 %s：各指标在合成基线内，行为达标度中等" % date_str,
                                  "lv": "INFO"}
        days.append(base)
    return days


def synth_units(days_rows):
    """合成 L2 units 块（模拟后端下发）。

    字段契约：{scale: {unit_key: {frontend_key: {v,j,c,n,d,dv,vs,g?}}}}。
    剧本：W43（含 2026-10-22，smoke 的周锚点）的 HRV 判 within-noise、
    步数判 suppressed——正好覆盖「不给箭头/不给百分比」两个渲染分支。
    """
    keys = ["sleep_h", "hrv", "rhr", "steps", "sedentary_min",
            "stress", "active_kcal", "intensity_min"]

    def cell(v, j, c=1.0, n=1, dv=None, vs=None):
        out = {"v": v, "j": j, "c": c, "n": n}
        if dv:
            out["dv"] = dv
        if vs:
            out["vs"] = vs
        return out

    def week_key(ds):
        d = datetime.date.fromisoformat(ds)
        iso = d.isocalendar()
        return "%d-W%02d" % (iso[0], iso[1])

    out = {"day": {}, "week": {}, "month": {}}
    for r in days_rows:
        vals = {"sleep_h": r.get("sleep_h"), "hrv": r.get("hrv"), "rhr": r.get("rhr"),
                "steps": r.get("steps"), "sedentary_min": r.get("sedentary_min"),
                "stress": r.get("stress"), "active_kcal": r.get("active_kcal"),
                "intensity_min": r.get("intensity_min")}
        cells = {}
        for k, v in vals.items():
            if v is None:
                cells[k] = cell(None, "na", 0.0, 0)
            else:
                j = "ok" if k != "sedentary_min" or v <= 480 else "no"
                cells[k] = cell(v, j, 1.0, 1)
        out["day"][r["date"]] = cells

    weeks = sorted({week_key(r["date"]) for r in days_rows})
    for w in weeks:
        wcells = {}
        for k in keys:
            if w == "2026-W43":
                wcells["hrv"] = cell(52.0, "ok", 1.0, 7, dv="within-noise", vs="2026-W42")
                wcells["steps"] = cell(8000.0, "ok", 1.0, 7, dv="suppressed", vs="2026-W42")
            else:
                wcells[k] = cell(5000.0 + len(k), "ok", 1.0, 7, dv="insufficient", vs=None)
        out["week"][w] = wcells
    months = sorted({r["date"][:7] for r in days_rows})
    for m in months:
        out["month"][m] = {k: cell(5000.0 + len(k), "ok", 1.0, 20) for k in keys}
    return out


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, ".smoke-fixture")
    today = datetime.date.today().isoformat()
    rules = load_rules()
    days = build_days(today)
    data = {
        "generated_at": today + " 00:00",
        "source": "fixture",
        "brief_date": today,
        "synced_at": "00:00",
        "default_day": days[-1]["date"],
        "days": days,
        "inbody": None,
    }
    idx = indexlayer.build_index(data, rules)
    # CI 无库 / 无主观日志：这三个块由本脚本合成（见模块注释的分工说明）。
    # units 已在上面把读库口短路成 _fixture 标记，这里整体替换为合成判定。
    idx["units"] = synth_units(idx["days"])
    idx["events"] = {
        "2026-09-15": [{"t": "context", "c": "合成事件：当日为工作日、久坐为主（fixture 用例）",
                        "ts": "2026-09-15T09:00:00+08:00"}],
        "2026-10-20": [{"t": "note", "c": "合成事件：口径备注，验证事件区渲染（fixture 用例）",
                        "ts": "2026-10-20T21:00:00+08:00"}],
    }
    idx["nudge"] = {
        "date": today,
        "missing": [{"key": "energy", "label": "精力"}, {"key": "weight_kg", "label": "晨起空腹体重"}],
        "labels": "精力、晨起空腹体重",
        "last_logged": "2026-10-28",
        "gap_days": 3,
        "weight_stale_days": 4,
        "prompt": "%s 补数：精力 ?/5、酸痛 ?/5、睡眠自评 ?/5、晨起体重 ?kg" % today,
    }

    os.makedirs(os.path.join(out_dir, "daily"), exist_ok=True)
    with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)
    for month, mdays in sorted(shard_days(days).items()):
        with open(os.path.join(out_dir, "daily", month + ".json"), "w", encoding="utf-8") as f:
            json.dump({"month": month, "days": mdays}, f, ensure_ascii=False, indent=1)
    print("fixture written:", out_dir, "| days:", len(days), "| today:", today)


if __name__ == "__main__":
    main()
