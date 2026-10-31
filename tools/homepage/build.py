# -*- coding: utf-8 -*-
"""主构建：装配 days[] + inbody，产出站点视图模型（index 层再切轻量索引）。

数据模型 v3（配合前端双 Tab：每日 / 汇总）：
- days[]：每个 raw 日一份完整视图
  - recovery：该日晨间快照（昨夜睡眠细分 / HRV / 血氧 / 睡眠心率 / Garmin 准备度）
  - load：该日消耗（身体电量 / 压力四档 / 能量账本 / 全天心率）——完整日才有
  - activity：该日活动量（步数 / 爬楼 / 24h 步数分布 / 正式活动记录）——完整日才有
  - brief：该日判读简报（交叉判读 + sections）——仅 23:30 分析日有
  - hero：该日综合指数
"""
from datetime import datetime

from homepage.paths import SITE, load_rules
from homepage.loaders import latest_brief, all_briefs, canon_days
from homepage.timeutil import weekday_cn
from homepage.recovery import build_recovery
from homepage.load import build_load
from homepage.activity import build_activity
from homepage.scoring import score_day
from homepage.brief import build_brief_payload
from homepage.inbody import build as build_inbody


def _in_progress(d_str, today, now_hour, coverage=None):
    """该日是否仍在进行中（数据不完整，不得计入汇总、不得出结论性展示）。

    2026-09-12 修：原判据 `d_str > brief_date` 在「当天报告已生成」时失效
    （brief_date 已等于今天），于是只有 1636 步的当天被当成完整日算进
    「近 11 个完整日」的达标率与日均，把口径整体拉偏。

    2026-10-31 二次修：中间版本用 `now_hour < 23` 判，**23 点以后就把当天当完整日**。
    实测 10-31 23:46 时当天 `totalSteps` 与 `wellnessEndTimeLocal` 全为 null（手表根本
    没同步），却被算进「近 N 个完整日」的达标率与日均——是 2026-09-12 那个 bug 的原样复现，
    只是触发条件从「报告已生成」换成了「时钟过了 23 点」。冒烟里 7 条卡内细分同时渲染为空，
    就是这个错误判定的下游症状。

    现在的判据按**数据覆盖度**，时钟只做兜底：
      · 当天一个字节都没落云（has_data=False）→ 一定进行中，与几点无关
      · 当天数据覆盖到 1440（次日 00:00）→ 数据其实已经完整，可以计入
      · 覆盖度拿不到（老数据 / 适配器缺键）→ 退回时钟，保持历史行为
    注意：历史截断日（如 10-30 只到 14:25）**不走这里**——它不是「进行中」，
    是「截断」，由 data_gate 与报告的截断口径处理。两者不能混为一谈。
    """
    if d_str != today:
        return False
    cov = coverage or {}
    if cov.get("has_data") is False:
        return True
    cutoff = cov.get("cutoff_min")
    if cutoff is not None:
        return cutoff < 1440
    return now_hour < 23


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
        morning = _in_progress(d_str, today, now.hour, canon.get("coverage"))
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

    # 汇总层（达标率 KPI / 均值 / 多日趋势行）已由 L2 units 承担（2026-10-25）：
    # 以前这里每次构建都白算一套 summary 塞进 data["summary"]，而 indexlayer
    # 只取 days/inbody，**从不读它**——算完就丢。前端要的达标率现在有唯一来源
    # （index.json.units 的 judge/judge_rate），没必要再维护第二套 KPI。
    latest = days[-1]
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": "garmin-cn",
        "brief_date": brief_date,
        "synced_at": (meta[-1][1] or "")[11:16],
        "default_day": latest["date"],
        "days": days,
        # 事件型数据源（InBody）：不属于 days[]——它没有逐日序列，
        # 混进日粒度会凭空造出「月日均」这种不存在的口径。
        "inbody": build_inbody(),
    }
