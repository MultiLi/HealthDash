# -*- coding: utf-8 -*-
"""每日综合指数视图装配：完整日五项合成 / 晨间快照四项。

**铁律（2026-09-12 定，对应 09-11 的评分事故）**：每个分项都必须由该维度的
实测值经 rules.json 阈值推导，**禁止常数短路**。「有值就给固定分」等于该维度
不进指数、只是把均值虚高。

事故复盘（09-11）：评分给 HRV 43 打了 95 分，composite 88「良好」，与同日报告
🟡 黄直接矛盾。两个缺陷叠加：
  ① 键名错位——完整日路径传的是 canonical hrv（last_night_avg / baseline_low /
     baseline_high），评分函数却按视图形状读 value / low / high，实测值恒为 None；
  ② 常数短路——`if status == "BALANCED": return 95` 把「读不到值」掩盖成满分。
更根本的是口径错误：Garmin 的 BALANCED 由 **7 日滚动均值** 决定，单夜暴跌时照旧
显示 BALANCED（09-11 实测 43 跌破下限 51 的 15.7%，周均 67 仍在带内 → BALANCED）。
拿它当「昨夜的自主神经状态」用，等于用上周的平均回答昨晚的问题。

公式在本文件（展示层合成指标），**全部阈值取自 rules.json**——睡眠达标线、步数
目标、久坐警戒、HRV 基线带与分档参数、RHR 升高阈值、血氧警戒线均不在此硬编码。
"""
from homepage.recovery import hrv_view


# ------------------------------------------------------------------ HRV 分项
def _hrv_band(v, rules):
    """个人基线带 (low, high)；缺基线或基线异常时退回 rules 缺省带。"""
    lo, hi = v.get("low"), v.get("high")
    if lo is None or hi is None or hi <= lo:
        lo, hi = rules["hrv"]["fallback_band"]
    return lo, hi


def hrv_state(hrv, rules):
    """当晚实测值相对个人基线带的位置：'above' | 'in' | 'below'；无值 None。

    **只看实测值，不看设备 status**（理由见模块 docstring）。
    """
    v = hrv_view(hrv)
    if not v:
        return None
    lo, hi = _hrv_band(v, rules)
    if v["value"] > hi:
        return "above"
    return "in" if v["value"] >= lo else "below"


def hrv_score(hrv, rules):
    """HRV 分项：≥ 上限 → at_or_above_high；带内线性；低于下限按半带宽线性扣分。"""
    v = hrv_view(hrv)
    if not v:
        return None
    lo, hi = _hrv_band(v, rules)
    c = rules["hrv"]["score"]
    top, edge = c["at_or_above_high"], c["at_low"]
    if v["value"] >= hi:
        return top
    if v["value"] >= lo:
        return round(edge + (top - edge) * (v["value"] - lo) / (hi - lo))
    half = (hi - lo) / 2.0
    return round(max(c["below_floor"],
                     edge - c["below_slope_per_halfwidth"] * (lo - v["value"]) / half))


# ------------------------------------------------------------- 其余分项
def _sleep_score(sl, rules):
    """睡眠时长达成度（对 need_min，缺则对 rules 达标线）。"""
    if not sl or not sl.get("duration_s"):
        return None
    need = sl.get("need_min") or rules["sleep"]["min_healthy_duration_min"]
    return min(100, sl["duration_s"] / (need * 60) * 100)


def _steps_score(act, rules):
    if not act or act.get("steps") is None:
        return None
    return min(100, act["steps"] / rules["activity"]["steps_goal"] * 100)


def _rhr_score(rhr, rules):
    """静息心率：≤ 7 日均 → 满分；每高 1 bpm 扣分。"""
    if not rhr or rhr.get("value") is None or rhr.get("avg7") is None:
        return None
    c = rules["rhr"]["score"]
    delta = rhr["value"] - rhr["avg7"]
    return max(c["floor"], c["at_or_below_avg7"] - max(0, delta) * c["per_bpm_above"])


def _sedentary_score(act, rules):
    """久坐：警戒线内满分，超出部分按小时扣分。"""
    if not act or act.get("sedentary_min") is None:
        return None
    c = rules["activity"]["sedentary_score"]
    warn = rules["activity"]["sedentary_warn_hours"] * 60
    return max(c["floor"], 100 - max(0, act["sedentary_min"] - warn) / 60.0
               * c["per_hour_beyond_warn"])


def _spo2_score(spo2, rules):
    """睡眠期血氧均值：≥ 警戒线满分；每低 1 个百分点扣分。"""
    if not spo2 or spo2.get("avg") is None:
        return None
    c = rules["spo2"]["score"]
    warn = rules["spo2"]["sleep_avg_warn"]
    return max(c["floor"], c["at_or_above_warn"]
               - max(0, warn - spo2["avg"]) * c["per_point_below"])


def _sleep_hr_score(shr, rules):
    """睡眠心率均值：基准 = profile.rhr_7d_avg + allowance_bpm。"""
    if not shr or shr.get("avg") is None:
        return None
    c = rules["sleep_hr"]["score"]
    base = rules["profile"]["rhr_7d_avg"] + c["allowance_bpm"]
    return max(c["floor"], c["at_or_below"]
               - max(0, shr["avg"] - base) * c["per_bpm_above"])


# ------------------------------------------------------------------ 合成
def score_day(day_dict, canon, rules):
    """完整日五项等权合成（睡眠/步数/HRV/静息心率/久坐）；
    晨间快照日（当天尚无完整数据）四项合成（睡眠/HRV/睡眠心率/血氧）。

    维度缺实测值时不参与平均——不补默认分，也不拿 0 当「没做到」，
    避免用不存在的证据凑数或凭空扣分。
    """
    rec = day_dict.get("recovery") or {}
    parts, used = [], []

    def add(name, val):
        if val is not None:
            parts.append(val)
            used.append(name)

    if day_dict.get("morning"):
        add("睡眠", _sleep_score(rec.get("sleep"), rules))
        add("HRV", hrv_score(rec.get("hrv"), rules))
        add("睡眠心率", _sleep_hr_score(rec.get("sleep_hr"), rules))
        add("血氧", _spo2_score(rec.get("spo2"), rules))
        if not parts:
            return None, ""
        return round(sum(parts) / len(parts)), \
            "晨间快照 · 基于%s（活动量未计入）" % "/".join(used)

    if not canon:
        return None, ""
    act = canon.get("activity") or {}
    add("睡眠", _sleep_score(canon.get("sleep"), rules))
    add("步数", _steps_score(act, rules))
    add("HRV", hrv_score(canon.get("hrv"), rules))
    add("静息心率", _rhr_score(canon.get("rhr"), rules))
    add("久坐", _sedentary_score(act, rules))
    if not parts:
        return None, ""
    return round(sum(parts) / len(parts)), \
        "数据日 %s · %s等权合成" % (day_dict["date"], "/".join(used))
