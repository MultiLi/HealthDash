# -*- coding: utf-8 -*-
"""InBody 体成分闸门（check）—— 拉取之后、判读之前的「体检」层。

定位（2026-10-25 立，Sir 要求「拉取 / 检查 / 分析」三段式）：
    sync_inbody.py  只拉不判（网络层）
    check_inbody.py 只查不判（闸门层）← 本文件
    fact_pack.py    只算不判（事实层）
    晚间报告 agent  判读（判读层）

本脚本做五件事，**不是**判读：
  ① 可达性：凭据/会话能不能用（--fetch 时顺带刷一次）
  ② 陈旧度：最新一次扫描距今几天（阈值见 rules.json → inbody.stale_days_*）
  ③ 完整性：关键字段是否齐全、体重是否等于脂肪量+去脂体重、PBF 与脂肪量是否自洽
  ④ 可比性：机型/序列号是否变了、时点是否与上次同段（跨机型整段作废）
  ⑤ 变化量：与上一条扫描逐项做门槛比对（门槛取 rules.json → confidence.metrics）

产物：data/inbody/status.json
    {checked_at, verdict, latest_datetime, age_days, scan_count,
     deltas[], anomalies[], notes[], conditions{}, unreported[], last_reported_scan}

verdict：OK（无新、数据健康）｜NEW（有新扫描且通过体检）｜
         STALE（最新扫描过旧）｜ANOMALY（数据可疑/不可比）｜EMPTY｜FAILED

用法：
    python3 check_inbody.py                 # 只做本地评估（零网络）
    python3 check_inbody.py --fetch         # 先刷 InBody 再评估（02:30 班用）
    python3 check_inbody.py --json
    python3 check_inbody.py --mark-reported # 把当前最新一条记为「已进报告」
    python3 check_inbody.py --mark-reported 20261021123730

退出码：0 = OK/NEW ｜ 1 = STALE/ANOMALY ｜ 2 = EMPTY/FAILED
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
_WS = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
DATA_DIR = os.environ.get("HEALTH_INBODY_DIR") or os.path.join(_WS, "data", "inbody")
RULES = os.path.join(_HERE, "..", "references", "rules.json")
STATUS = os.path.join(DATA_DIR, "status.json")

# 关键字段：summary 层必须有值，缺一个就说明这次扫描落盘不完整
REQUIRED = ["weight_kg", "percent_body_fat", "skeletal_muscle_mass_kg",
            "fat_free_mass_kg", "body_fat_mass_kg", "visceral_fat_level", "bmi"]

# 比对项：(summary 键, rules confidence 键, 显示名, 单位)
COMPARE = [
    ("weight_kg", "weight_kg", "体重", "kg"),
    ("percent_body_fat", "body_fat_pct", "体脂率", "pt"),
    ("skeletal_muscle_mass_kg", "skeletal_muscle_kg", "骨骼肌", "kg"),
    ("fat_free_mass_kg", "ffm_kg", "去脂体重", "kg"),
    ("visceral_fat_level", "visceral_fat_level", "内脏脂肪", "级"),
]

# 硬异常阈值：越过就不是「变化」，是先怀疑测错（换机 / 没赤足 / 数据串号）
HARD = {"weight_kg": 5.0, "percent_body_fat": 6.0,
        "skeletal_muscle_mass_kg": 4.0, "visceral_fat_level": 3.0}

DEFAULT_RULES = {"stale_days_warn": 14, "stale_days_bad": 45,
                 "weight_mdc_diff_condition": 2.0}


def _load(p, default=None):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _inbody_rules():
    r = _load(RULES, {}) or {}
    out = dict(DEFAULT_RULES)
    out.update({k: v for k, v in (r.get("inbody") or {}).items()
                if not k.startswith("_")})
    out["hard_anomaly"] = (r.get("inbody") or {}).get("hard_anomaly") or HARD
    out["_metrics"] = ((r.get("confidence") or {}).get("metrics") or {})
    return out


def _tod(dt):
    """时点分档：同段才可直接比（晨起 vs 晚间差 1–2kg 是进食/水合，不是胖瘦）。"""
    if not dt or len(dt) < 13:
        return "unknown"
    try:
        hh = int(dt[11:13])
    except Exception:
        return "unknown"
    return "morning" if hh < 10 else ("evening" if hh >= 18 else "afternoon")


def _days_between(iso_dt, today):
    if not iso_dt:
        return None
    try:
        return (today - datetime.date.fromisoformat(iso_dt[:10])).days
    except Exception:
        return None


def evaluate(today=None, as_of=None):
    """纯本地评估：只读 index.json / latest.json，零网络。

    as_of：报告日期（YYYY-MM-DD 或 date）。给了就**只认该日及以前**的扫描——
    历史报告重算时必须这样，否则 10-22 的报告会读到 10-25 的扫描。
    """
    today = today or datetime.date.today()
    as_of = as_of or today.isoformat()
    as_of = as_of[:10] if isinstance(as_of, str) else as_of.isoformat()
    rules = _inbody_rules()
    idx = _load(os.path.join(DATA_DIR, "index.json"), {}) or {}
    scans_all = idx.get("scans") or []
    scans = [s for s in scans_all if (s.get("datetime") or "")[:10] <= as_of]
    status_prev = _load(STATUS, {}) or {}
    last_reported = status_prev.get("last_reported_scan")

    res = {
        "checked_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "as_of": as_of,
        "verdict": "EMPTY", "ok": False,
        "latest_datetime": None, "age_days": None,
        "scan_count": idx.get("scan_count_total"),
        "index_updated": idx.get("updated"),
        "conditions": {}, "deltas": [], "anomalies": [], "notes": [],
        "unreported": [], "last_reported_scan": last_reported,
    }

    if not scans:
        if scans_all:
            res["notes"].append("截至 %s 无扫描记录（账号下最早一条在 %s）——"
                                "该日报告不得引用体成分，只能标「当日无体测」。"
                                % (as_of, (scans_all[-1].get("datetime") or "?")[:10]))
        else:
            res["notes"].append("尚无任何 InBody 扫描记录（data/inbody 为空）——"
                                "需要先在体测仪上测一次并同步进 App。")
        return res

    latest = scans[0]
    res["ok"] = True
    res["latest_datetime"] = latest.get("datetime")
    # 陈旧度一律相对「报告日 as_of」算（历史日重算时不能拿今天的年龄去判当日）
    _as_of_d = datetime.date.fromisoformat(as_of)
    res["age_days"] = _days_between(latest.get("datetime"), _as_of_d)
    if _as_of_d != today:
        res["age_days_now"] = _days_between(latest.get("datetime"), today)
    res["conditions"] = {
        "equipment": latest.get("equipment"),
        "serial": latest.get("serial"),
        "time_of_day": _tod(latest.get("datetime")),
        "fasted": False,
        "note": "BIA 家用机型（%s）+ 非晨起空腹口径：绝对值只能定区间，不可追两周变化。"
                % (latest.get("equipment") or "?"),
    }

    # ② 陈旧度
    age = res["age_days"]
    if age is not None:
        if age >= rules["stale_days_bad"]:
            res["verdict"] = "STALE"
            res["notes"].append("最新一次体成分距今 %d 天（≥%d）——数据已无时效，"
                                "报告与主页都必须标注为陈旧基线。"
                                % (age, rules["stale_days_bad"]))
        elif age >= rules["stale_days_warn"]:
            res["verdict"] = "STALE"
            res["notes"].append("最新一次体成分距今 %d 天（≥%d）——"
                                "可作基线引用，不可当作「当前状态」。"
                                % (age, rules["stale_days_warn"]))
        else:
            res["verdict"] = "OK"

    # ③ 完整性
    missing = [k for k in REQUIRED if latest.get(k) is None]
    if missing:
        res["anomalies"].append("字段缺失：%s（这次扫描落盘不完整）" % "、".join(missing))
    wt, ffm, bfm = (latest.get("weight_kg"), latest.get("fat_free_mass_kg"),
                    latest.get("body_fat_mass_kg"))
    if None not in (wt, ffm, bfm) and abs(wt - (ffm + bfm)) > 0.6:
        res["anomalies"].append(
            "内部不自洽：体重 %.1f ≠ 去脂 %.1f + 脂肪 %.1f（差 %.1fkg）"
            % (wt, ffm, bfm, wt - (ffm + bfm)))
    pbf = latest.get("percent_body_fat")
    if None not in (pbf, bfm, wt) and wt:
        calc = bfm / wt * 100
        if abs(calc - pbf) > 1.0:
            res["anomalies"].append(
                "体脂率不自洽：记录 %.1f%% vs 脂肪量折算 %.1f%%" % (pbf, calc))

    # ⑤ 与上一条扫描比对（scans 为新→旧）
    prev = scans[1] if len(scans) > 1 else None
    if prev:
        same_machine = (prev.get("equipment") == latest.get("equipment")
                        and prev.get("serial") == latest.get("serial"))
        same_tod = _tod(prev.get("datetime")) == _tod(latest.get("datetime"))
        if not same_machine:
            res["anomalies"].append(
                "机型变更：%s/%s → %s/%s —— 跨机型绝对值不可比，本次比对整段作废"
                % (prev.get("equipment"), prev.get("serial"),
                   latest.get("equipment"), latest.get("serial")))
        for key, mkey, label, unit in COMPARE:
            cur_v, prev_v = latest.get(key), prev.get(key)
            if cur_v is None or prev_v is None:
                continue
            delta = cur_v - prev_v
            m = rules["_metrics"].get(mkey) or {}
            mdc = m.get("mdc_change")
            if key == "weight_kg" and not same_tod:
                # 异条件体重：note 明确要求按 2.0 用，mdc_change=0.6 只在同条件成立
                mdc = rules["weight_mdc_diff_condition"]
            if mdc is None:
                continue
            hard = (rules["hard_anomaly"] or {}).get(key)
            res["deltas"].append({
                "metric": key, "label": label, "unit": unit,
                "prev": prev_v, "now": cur_v, "delta": round(delta, 2),
                "mdc_change": mdc, "exceeds_mdc": abs(delta) > mdc,
                "hard_anomaly": bool(hard and abs(delta) > hard),
                "same_conditions": bool(same_machine and same_tod),
            })
            if hard and abs(delta) > hard:
                res["anomalies"].append(
                    "%s 突变 %.1f%s（>%s%s）——先怀疑测错或换机，不当作真实变化"
                    % (label, delta, unit, hard, unit))
        res["prev_scan"] = {k: prev.get(k) for k in
                            ("datetime", "weight_kg", "percent_body_fat",
                             "skeletal_muscle_mass_kg", "visceral_fat_level",
                             "equipment", "serial")}
        if not same_tod:
            res["notes"].append("与上一条不同时段（%s → %s）：体重门槛按异条件 %s kg 用。"
                                % (_tod(prev.get("datetime")),
                                   _tod(latest.get("datetime")),
                                   rules["weight_mdc_diff_condition"]))
        over = [d for d in res["deltas"] if d["exceeds_mdc"]]
        res["notes"].append(
            "与上一条（%s）比对：%s"
            % (prev.get("datetime"),
               "、".join("%s %+.1f%s" % (d["label"], d["delta"], d["unit"])
                         for d in over) + " 过门槛" if over
               else "各项均在误差带内（不得用上升/下降描述）"))
    else:
        res["notes"].append("账号下仅一条扫描——无历史、无趋势，只能作为起点截面。")

    # 未进报告的扫描（报告层视角）
    if last_reported:
        res["unreported"] = [s.get("raw_datetime") for s in scans
                             if (s.get("raw_datetime") or "") > last_reported]
    else:
        res["unreported"] = [s.get("raw_datetime") for s in scans]

    if res["anomalies"] and res["verdict"] in ("OK", "NEW"):
        res["verdict"] = "ANOMALY"
    if res["verdict"] == "OK" and last_reported and res["unreported"]:
        res["verdict"] = "NEW"
    elif res["verdict"] == "OK" and not last_reported:
        res["verdict"] = "NEW"
    return res


def fetch():
    """跑一次 sync_inbody（网络层），返回 (ok, 最后一行输出)。"""
    try:
        r = subprocess.run([sys.executable, os.path.join(_HERE, "sync_inbody.py")],
                           capture_output=True, text=True, timeout=180)
        tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        return r.returncode == 0, (tail[-1] if tail else "?")
    except Exception as e:
        return False, str(e)


def write_status(res):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        prev = _load(STATUS, {}) or {}
        if prev.get("last_reported_scan"):
            res["last_reported_scan"] = prev["last_reported_scan"]
        json.dump(res, open(STATUS, "w"), ensure_ascii=False, indent=1)
    except Exception as e:
        print("[inbody-check] status.json 写入失败：%s" % e, file=sys.stderr)


def mark_reported(raw_datetime=None):
    st = _load(STATUS, {}) or {}
    if not st:
        st = evaluate()
    if not raw_datetime:
        raw_datetime = st.get("latest_datetime", "")[:19].replace("-", "").replace(":", "").replace("T", "")
    st["last_reported_scan"] = raw_datetime
    st["reported_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    json.dump(st, open(STATUS, "w"), ensure_ascii=False, indent=1)
    print("[inbody-check] 已记账：last_reported_scan=%s" % raw_datetime)
    return 0


def fmt(res):
    if res["verdict"] in ("EMPTY",):
        return "[inbody] ⚪ 无扫描记录" + (
            "（截至 %s）" % res["as_of"] if res.get("as_of") else "") + \
            ("\n[inbody]   · " + "\n[inbody]   · ".join(res.get("notes") or [])
             if res.get("notes") else "")
    L = []
    L.append("[inbody] %s  最新 %s（%s 天前）%s"
             % ({"OK": "✓", "NEW": "★", "STALE": "⚠", "ANOMALY": "✗"}.get(
                 res["verdict"], "·"),
                (res["latest_datetime"] or "?")[:16].replace("T", " "),
                res["age_days"], res["verdict"]))
    c = res.get("conditions") or {}
    L.append("[inbody]   机型 %s/%s  %s  扫描总数 %s"
             % (c.get("equipment"), c.get("serial"), c.get("time_of_day"),
                res.get("scan_count")))
    if res.get("deltas"):
        L.append("[inbody]   与上一条比对：" + "；".join(
            "%s %+.1f%s%s" % (d["label"], d["delta"], d["unit"],
                              "" if d["exceeds_mdc"] else "(误差内)")
            for d in res["deltas"]))
    for a in res.get("anomalies") or []:
        L.append("[inbody]   ✗ %s" % a)
    for n in res.get("notes") or []:
        L.append("[inbody]   · %s" % n)
    if res.get("unreported"):
        L.append("[inbody]   未进报告：%s" % "、".join(res["unreported"]))
    return "\n".join(L)


def one_line(res):
    """给 collect.py 这类「只拉数」的任务用的一行摘要。"""
    if res.get("verdict") == "FAILED":
        return "[inbody] ✗ 拉取失败：%s" % (res.get("error") or "?")
    if res.get("verdict") == "EMPTY":
        return "[inbody] ⚪ 账号下无扫描记录"
    s = "[inbody] %s 最新 %s（%s 天前）%s" % (
        {"OK": "✓", "NEW": "★", "STALE": "⚠", "ANOMALY": "✗"}.get(
            res["verdict"], "·"),
        (res.get("latest_datetime") or "?")[:16].replace("T", " "),
        res.get("age_days"), res["verdict"])
    if res.get("unreported"):
        s += "  未进报告 %d 条" % len(res["unreported"])
    if res.get("anomalies"):
        s += "  异常：%s" % res["anomalies"][0]
    return s


def main():
    ap = argparse.ArgumentParser(description="InBody 体成分闸门（只查不判）")
    ap.add_argument("--fetch", action="store_true", help="先刷一次 InBody 再评估")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--mark-reported", nargs="?", const="", default=None,
                    help="把当前（或指定）扫描记为已并入报告")
    ap.add_argument("--today", default=None, help="测试用：覆盖今天")
    ap.add_argument("--as-of", dest="as_of", default=None,
                    help="报告日（默认今天）：只看该日及以前的扫描")
    ap.add_argument("--no-write", action="store_true", help="不写 status.json")
    a = ap.parse_args()

    if a.mark_reported is not None:
        return mark_reported(a.mark_reported or None)

    if a.fetch:
        ok, tail = fetch()
        if not ok:
            res = {"checked_at": datetime.datetime.now().isoformat(timespec="seconds"),
                   "verdict": "FAILED", "ok": False, "error": tail,
                   "notes": ["拉取失败：%s" % tail],
                   "unreported": []}
            if not a.no_write:
                write_status(res)
            print(json.dumps(res, ensure_ascii=False, indent=1) if a.json
                  else "[inbody] ✗ 拉取失败：%s" % tail, file=sys.stderr if not a.json else sys.stdout)
            return 2

    today = (datetime.date.fromisoformat(a.today) if a.today
             else datetime.date.today())
    res = evaluate(today=today, as_of=a.as_of)
    if not a.no_write:
        write_status(res)
    print(json.dumps(res, ensure_ascii=False, indent=1) if a.json else fmt(res))
    return 0 if res["verdict"] in ("OK", "NEW") else (2 if res["verdict"] in ("EMPTY",) else 1)


if __name__ == "__main__":
    sys.exit(main())
