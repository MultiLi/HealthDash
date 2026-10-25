"""归档：汇总层（2026-10-25 移入 legacy）。

build.py 曾每次构建都调用 build_summary 并把结果塞进 data["summary"]，
但 indexlayer.build_index 从不读它——算完就丢。达标率 KPI 现在有唯一来源：
index.json.units（L2 单元的 judge / judge_rate / coverage）。

保留文件是为了留 2026-09-12 那段注释（HRV 达标必须走 scoring.hrv_state，
别用 status=="BALANCED"）——那是 09-11 事故的直接教训。
"""
# -*- coding: utf-8 -*-
"""汇总层视图装配：达标率 KPI / 均值 / 多日趋势行。

KPI 阈值全部取自 rules.json（睡眠 420min / 步数目标 / 久坐 8h），
并随 data.json 下发 goals 给前端，前端不再硬编码判好阈值。
HRV 达标判定与综合指数的 HRV 分项共用 scoring.hrv_state（2026-09-12 修：
原判定 `status == "BALANCED"` 会把 09-11 的实测 43（跌破下限）算成「在区间」，
KPI 虚高到 90%，且让主页与报告口径互相打架）。
"""
from homepage.paths import load_rules
from homepage.scoring import hrv_state


def build_summary(complete_days):
    """complete_days: 已构建的完整日 day dict 列表（升序）。"""
    rules = load_rules()
    sleep_goal_h = rules["sleep"]["min_healthy_duration_min"] / 60
    steps_goal = rules["activity"]["steps_goal"]
    sed_max = rules["activity"]["sedentary_warn_hours"] * 60

    rows = []
    for d in complete_days:
        rec = d.get("recovery") or {}
        load = d.get("load") or {}
        act = d.get("activity") or {}
        hrv = rec.get("hrv") or {}
        sh = rec.get("sleep") or {}
        en = load.get("energy") or {}
        st = load.get("stress") or {}
        rows.append({
            "date": d["date"], "label": d["label"],
            "sleep_h": round((sh.get("seconds") or 0) / 3600, 2) if sh else None,
            "hrv": hrv.get("value"),
            # 达标 = 当晚实测值未低于个人基线下限（高于上限＝恢复充分，同样达标）
            "hrv_in_range": hrv_state(hrv, rules) in ("in", "above"),
            "steps": act.get("steps"),
            "rhr": (load.get("hr") or {}).get("rhr"),
            "stress_avg": st.get("avg"),
            "sedentary_min": act.get("sedentary_min"),
            "active_kcal": en.get("active"),
            "score": (d.get("hero") or {}).get("score"),
        })

    def rate(key_ok):
        n = len(rows)
        if not n:
            return None, 0, 0
        ok = sum(1 for r in rows if key_ok(r))
        return round(ok / n * 100), ok, n

    sleep_rate, s_ok, n = rate(lambda r: (r["sleep_h"] or 0) >= sleep_goal_h)
    steps_rate, st_ok, _ = rate(lambda r: (r["steps"] or 0) >= steps_goal)
    hrv_rate, h_ok, _ = rate(lambda r: r["hrv_in_range"])
    sed_rate, sd_ok, _ = rate(lambda r: (r["sedentary_min"] is not None
                                         and r["sedentary_min"] <= sed_max))

    def avg_of(key, nd=0):
        vals = [r[key] for r in rows if r[key] is not None]
        return round(sum(vals) / len(vals), nd) if vals else None

    return {
        "n_days": len(rows),
        "range": [rows[0]["label"] + "–" + rows[-1]["label"]] if rows else "",
        "goals": {"sleep_h": sleep_goal_h, "steps": steps_goal,
                  "sedentary_min": sed_max},
        "kpis": {
            "sleep_rate": sleep_rate, "sleep_ok": s_ok, "sleep_n": n,
            "steps_rate": steps_rate, "steps_ok": st_ok,
            "hrv_rate": hrv_rate, "hrv_ok": h_ok,
            "sed_rate": sed_rate, "sed_ok": sd_ok,
        },
        "avgs": {
            "sleep_h": avg_of("sleep_h", 1), "steps": avg_of("steps"),
            "hrv": avg_of("hrv"), "sedentary_min": avg_of("sedentary_min"),
            "active_kcal": avg_of("active_kcal"), "stress_avg": avg_of("stress_avg"),
        },
        "rows": rows,
    }
