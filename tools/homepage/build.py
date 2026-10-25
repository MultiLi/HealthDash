# -*- coding: utf-8 -*-
"""主构建：装配 days[] + summary，产出 site/data.json 视图模型。

数据模型 v3（配合前端双 Tab：每日 / 汇总）：
- days[]：每个 raw 日一份完整视图
  - recovery：该日晨间快照（昨夜睡眠细分 / HRV / 血氧 / 睡眠心率 / Garmin 准备度）
  - load：该日消耗（身体电量 / 压力四档 / 能量账本 / 全天心率）——完整日才有
  - activity：该日活动量（步数 / 爬楼 / 24h 步数分布 / 正式活动记录）——完整日才有
  - brief：该日判读简报（交叉判读 + sections）——仅 23:30 分析日有
  - hero：该日综合指数
- summary：全量汇总层（达标率 KPI / 均值 / 多日趋势行）
"""
from datetime import datetime

from homepage.paths import SITE, load_rules
from homepage.loaders import latest_brief, all_briefs, canon_days
from homepage.timeutil import weekday_cn
from homepage.recovery import build_recovery
from homepage.load import build_load
from homepage.activity import build_activity
from homepage.scoring import score_day
from homepage.summary import build_summary
from homepage.brief import build_brief_payload
from homepage.inbody import build as build_inbody


def _in_progress(d_str, today, now_hour):
    """该日是否仍在进行中（数据不完整，不得计入汇总、不得出结论性展示）。

    2026-09-12 修：原判据 `d_str > brief_date` 在「当天报告已生成」时失效
    （brief_date 已等于今天），于是只有 1636 步的当天被当成完整日算进
    「近 11 个完整日」的达标率与日均，把口径整体拉偏。改为按本地时钟判定：
    当天 23:00 前即为进行中——与「报告 23:30 才出终版」的采集纪律同一口径。
    """
    return d_str == today and now_hour < 23


def build():
    rules = load_rules()
    brief = latest_brief()
    brief_date = brief["date"]
    canon_map, meta = canon_days()
    dates = sorted(canon_map)
    latest_date = dates[-1]

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # 判读是「一天一份」：按日装配，不是只给最新分析日（2026-09-07 修复）
    brief_payloads = {d: build_brief_payload(b) for d, b in all_briefs().items()}

    days = []
    for d_str in dates:
        canon = canon_map[d_str]
        morning = _in_progress(d_str, today, now.hour)
        day = {
            "date": d_str,
            "label": "%s/%s" % (d_str[5:7], d_str[8:10]),
            "weekday": weekday_cn(d_str),
            "morning": morning,
            "has_brief": d_str in brief_payloads,
            "recovery": build_recovery(canon),
            "load": None if morning else build_load(canon),
            "activity": None if morning else build_activity(canon),
        }
        score, note = score_day(day, canon, rules)
        hb = rules["hero"]
        band = ("良好" if (score or 0) >= hb["good_min"]
                else "一般" if (score or 0) >= hb["fair_min"] else "待注意")
        day["hero"] = {"score": score, "band": band, "note": note}
        if day["has_brief"]:
            payload = brief_payloads[d_str]
            gaps = [g for g in payload["gaps"]
                    if not ((day["recovery"] or {}).get("spo2") and "血氧" in g)]
            day["brief"] = {"cross": payload["cross"], "sections": payload["sections"],
                            "gaps": gaps}
        days.append(day)

    complete_days = [d for d in days if not d["morning"]]
    summary = build_summary(complete_days)

    latest = days[-1]
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": "garmin-cn",
        "brief_date": brief_date,
        "synced_at": (meta[-1][1] or "")[11:16],
        "default_day": latest["date"],
        "days": days,
        "summary": summary,
        # 事件型数据源（InBody）：不属于 days[]——它没有逐日序列，
        # 混进日粒度会凭空造出「月日均」这种不存在的口径。
        "inbody": build_inbody(),
    }
