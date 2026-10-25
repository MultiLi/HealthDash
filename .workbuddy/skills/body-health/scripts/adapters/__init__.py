# -*- coding: utf-8 -*-
"""body-health 数据源适配器注册表与规范化契约。

========================= 适配器契约 =========================
一个数据源 = 一个实现 `collect(date, days) -> dict` 的模块。
引擎层（daily_brief.py）只认下面的规范化 schema，禁止出现任何
源特有字段名（如 dailySleepDTO / stressValuesArray）——源字段
的解析、时区锚定、单位换算全部由适配器在 collect 内完成。

collect(date: str, days: int) -> {
  "date":   "YYYY-MM-DD",     # 目标分析日
  "source": "<适配器名>",
  "gaps":   ["该源缺失指标的说明，如实告知用户，禁止编造"],
  "days": [ <逐日规范化数据，按日期升序，含目标日> ]
}

逐日 dict（字段全部可选：缺什么省什么，引擎按存在与否判读）：
  sleep:      {start_local, end_local, duration_s, deep_s, light_s, rem_s,
               awake_s, score, need_min, need_baseline_min, awake_count,
               restless, resp{avg,low,high}, breathing, sleep_stress,
               nap_min, sub_scores[{key,qualifier}], levels[[start,end,lvl]],
               sleep_hr{avg,lowest,curve}}
  hrv:        {last_night_avg, baseline_low, baseline_high, status,
               weekly_avg, readings[[hhmm,v]]}
  rhr:        {value, avg7}
  stress:     {hourly: {本地小时int: 压力int}, high_min, avg, max, overall,
               tiers_min{rest,low,medium,high}}
  bb:         {low, high, night{start,end,delta}}
  hr_hourly:  {本地小时int: {avg, max, n}}
  hr_series:  [[minute_of_day, bpm], ...]    # 分钟级采样序列，供区间
               分钟统计（Z2 等）；hr_sample_interval_min 声明采样节奏
  hr_range:   {min, max}
  readiness:  {level, score, recovery_min}
  training:   {acwr, acute_load}
  activity:   {steps, distance_km, active_kcal, bmr_kcal, total_kcal,
               sedentary_min, sedentary_max_block_min, intensity_min,
               vigorous_min, floors_up, bb_charged, bb_drained,
               hourly_steps[[h,n]]}
  activities: [{start_local, name, type, minutes, distance_km}]
  vo2max:     {value, updated}

时间口径（硬性）：hourly 的键 = 本地小时 0-23；*_local = 本地
"HH:MM"。适配器必须自行解决时区换算与时间戳锚定，引擎层不做
任何 epoch 运算。

注册：模块底部 `@register("源名")`；引擎用 --source 选择。
"""
import importlib
import os

REGISTRY = {}
_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def register(name):
    def deco(cls):
        REGISTRY[name] = cls
        return cls
    return deco


def get_adapter(name):
    if name not in REGISTRY:
        # 模块文件名用下划线，注册键可用连字符（garmin-cn → garmin_cn.py）
        mod = name.replace("-", "_")
        path = os.path.join(_PACKAGE_DIR, f"{mod}.py")
        if not os.path.isfile(path):
            raise SystemExit(f"未知数据源: {name}；已注册: {', '.join(REGISTRY) or '无'}")
        importlib.import_module(f"adapters.{mod}")
    if name not in REGISTRY:
        raise SystemExit(f"适配器 {name} 未注册")
    return REGISTRY[name]()
