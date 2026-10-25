#!/usr/bin/env python3
"""Garmin CN 采集编排与落盘：幂等拉取原始数据 → <workspace>/data/raw/<date>.json。

定位：E 的编排 + 落盘（land），不是完整 E 层——传输/鉴权/端点知识
全部在 garmin-connect-cn-data__skillhub 网关（garmin_gc_api：通道 A、
通道 B、ENDPOINTS）。本层只决定「拉哪些端点组合、何时重拉、落到哪」，
这些与 adapter 的 canonical 契约共演化，故归属判读工作空间。
判读引擎与其适配器全部离线，只读本脚本落盘的文件。

幂等规则：
  - 历史日期：raw 文件已存在即跳过（已有数据不可变，永不覆盖）；
  - 目标日（--date）：每次都重拉（当日数据仍在累积）；
  - --force：无视上述规则全量重拉；
  - --backfill：补漏模式——对历史日 raw 中**缺失的键**补拉并合并写入，
    已有键一律不碰（补漏是修复落盘遗漏，不是改写历史；这是唯一
    允许写入已存在历史日文件的路径，且只做增补）。

产物（单日一个 JSON，键与 pull() 的输出形状一致）：
  sleep_daily / hrv_daily / stress_daily / usersummary_daily /
  summary_chart / spo2_daily / readiness_daily / ts_daily   → 单日响应
  bb_range / stress_range                                    → 该日条目（自区间端点切出）
  dailyhr / vo2_latest / activities                          → 仅目标日写入
    （其中 dailyhr 与 activities 可用 --backfill 回补到历史日；
      activities 是跨日端点，回补时按 startTimeLocal 日期分派）
  synced_at / sync_errors                                    → 元信息与失败端点

用法：
  uv run --python 3.11 --with curl_cffi --with garth python sync.py \
      [--date YYYY-MM-DD] [--days N] [--force]
"""
import argparse
import datetime
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))          # body-health/scripts
_SKILLS = os.path.abspath(os.path.join(_HERE, "..", ".."))  # .workbuddy/skills
_WORKSPACE = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
GARMIN_CN_SCRIPTS = os.path.join(
    _SKILLS, "garmin-connect-cn-data__skillhub", "scripts")
RAW_DIR = os.environ.get("HEALTH_RAW_DIR") or os.path.join(_WORKSPACE, "data", "raw")

# 逐日端点：(raw 键名, ENDPOINTS 键名)
PER_DAY = [
    ("sleep_daily", "sleep_day"),
    ("hrv_daily", "hrv"),
    ("stress_daily", "stress"),
    ("usersummary_daily", "usersummary_daily"),
    ("summary_chart", "summary_chart"),
    ("spo2_daily", "spo2"),
    ("readiness_daily", "readiness"),
    ("ts_daily", "ts_daily"),
]
RANGE_KEYS = ("bb_range", "stress_range")


def raw_path(date):
    return os.path.join(RAW_DIR, f"{date}.json")


def _fetch_day(gca, eps, date):
    """拉某日全部逐日端点，响应形状与 pull() 保持一致。"""
    day = {"date": date}
    for key, ep in PER_DAY:
        code, body = gca.get(eps[ep].format(date=date))
        if code == 200 and body:
            if isinstance(body, dict):
                body.setdefault("calendarDate", date)
            else:
                body = {"calendarDate": date, "values": body}
            day[key] = body
        else:
            day.setdefault("sync_errors", {})[key] = f"HTTP {code}"
    return day


def _fetch_ranges(gca, eps, dates):
    """区间端点一次拉全（≤28 天分块），按 calendarDate 切成单日条目。"""
    out = {k: {} for k in RANGE_KEYS}
    days = sorted(dates)
    i = 0
    while i < len(days):
        chunk = days[i:i + 28]
        for key in RANGE_KEYS:
            code, body = gca.get(
                eps[key].format(start=chunk[0], end=chunk[-1]))
            if code == 200 and isinstance(body, list):
                for item in body:
                    cd = item.get("calendarDate")
                    if cd in out[key] or cd not in set(days):
                        continue
                    out[key][cd] = item
        i += 28
    return out


def _fetch_dailyhr(gca, eps, date):
    """拉某日逐时心率：通道 A 优先，通道 B（路径风格）回落。"""
    code, hr = gca.get(eps["dailyhr"].format(date=date))
    if code == 200 and isinstance(hr, dict):
        return hr
    code2, hr2 = gca.garth_get(eps["dailyhr_path"], params={"date": date})
    if code2 == 200 and isinstance(hr2, dict):
        return hr2
    return None


def _fetch_target_extras(gca, eps, day, date):
    """仅目标日需要：逐时心率（含通道 B 回落）、VO2 Max、正式活动列表。
    通道 B 实现在网关（gca.garth_get），端点全部取自 gca.ENDPOINTS——
    本函数只有调用策略，零端点知识、零鉴权细节。"""
    hr = _fetch_dailyhr(gca, eps, date)
    if hr is not None:
        day["dailyhr"] = hr
    else:
        day.setdefault("sync_errors", {})["dailyhr"] = "通道A与B均失败"

    code, vo2 = gca.get(eps["vo2max_latest"].format(date=date))
    if code == 200 and isinstance(vo2, dict):
        day["vo2_latest"] = vo2

    code, acts = gca.get(eps["activities"].format(limit=10))
    if code == 200 and isinstance(acts, list):
        day["activities"] = acts


def sync(date, days, force=False):
    window = [(datetime.date.fromisoformat(date)
               - datetime.timedelta(days=days - 1 - i)).isoformat()
              for i in range(days)]
    # 目标日永远重拉；历史日已有落盘即跳过
    need = [d for d in window
            if d == date or force or not os.path.isfile(raw_path(d))]
    summary = {"date": date, "window": window, "fetched": [], "skipped": [],
               "errors": {}}
    for d in window:
        if d not in need:
            summary["skipped"].append(d)

    if need:
        sys.path.insert(0, GARMIN_CN_SCRIPTS)
        import garmin_gc_api as gca
        eps = gca.ENDPOINTS
        os.makedirs(RAW_DIR, exist_ok=True)
        fetched = {d: _fetch_day(gca, eps, d) for d in need}
        ranges = _fetch_ranges(gca, eps, need)
        for d in need:
            fetched[d]["bb_range"] = ranges["bb_range"].get(d)
            fetched[d]["stress_range"] = ranges["stress_range"].get(d)
        if date in fetched:
            _fetch_target_extras(gca, eps, fetched[date], date)
        for d in need:
            # 目标日专属键（dailyhr/vo2_latest/activities）只有 d == date 时才采集。
            # --force 重拉历史日时，这些键拿不到新值，若直接整文件覆盖会把历史日
            # 曾作为目标日留下的逐时心率抹掉（2026-09-07 事故：--force 后 09-04~06
            # dailyhr 全部变空，导致 Z2 窗口只剩 1 天）。故缺新值时沿用旧值。
            prev_path = raw_path(d)
            if os.path.isfile(prev_path):
                try:
                    prev = json.load(open(prev_path))
                except Exception:
                    prev = {}
                for k in ("dailyhr", "vo2_latest", "activities"):
                    if not fetched[d].get(k) and prev.get(k):
                        fetched[d][k] = prev[k]
            fetched[d]["synced_at"] = datetime.datetime.now().isoformat(
                timespec="seconds")
            json.dump(fetched[d], open(raw_path(d), "w"),
                      ensure_ascii=False, indent=1)
            summary["fetched"].append(d)
            if fetched[d].get("sync_errors"):
                summary["errors"][d] = fetched[d]["sync_errors"]
    return summary


def repair_recent(days=3, end=None):
    """回补最近 N 天内「有数据但被截断」的日子（2026-10-18 建）。

    与 --force 的区别：`--force` 是无差别全量重拉，会把**完整日**也覆写一遍
    （历史事故：09-07 一次 --force 把历史日的 dailyhr 全抹空，Z2 窗口塌成 1 天）。
    repair 只碰 `data_gate` 判为 `partial` 的日 —— 即「云端还有、我们没拿到」，
    完整日一个字节都不动。这是「补漏不属篡改」的自动执行路径。

    实证（2026-10-18）：10-17 当日 23:30 拉到的是 22:05 截止，次日 repair
    重拉得到完整日，步数 9948 → 10830、久坐 835 → 943 min。
    """
    import data_gate                                   # 同目录，懒加载
    end = end or datetime.date.today()
    out = {"window": [], "repaired": [], "unchanged": [],
           "skipped": {}, "errors": {}}
    for i in range(days):
        d = (end - datetime.timedelta(days=i)).isoformat()
        out["window"].append(d)
        try:
            g0 = data_gate.inspect(d)
        except Exception as e:
            out["errors"][d] = "gate: %s" % e
            continue
        if g0["level"] != "partial":
            out["skipped"][d] = g0["level"]
            continue
        try:
            sync(d, 1)              # d == date → 必重拉，且带目标日专属键
            g1 = data_gate.inspect(d)
        except Exception as e:
            out["errors"][d] = str(e)
            continue
        rec = {"date": d, "before": g0["cutoff"], "after": g1["cutoff"],
               "level_after": g1["level"],
               "steps_before": g0.get("steps"), "steps_after": g1.get("steps"),
               "sedentary_before": g0.get("sedentary_min"),
               "sedentary_after": g1.get("sedentary_min")}
        if g1["cutoff"] != g0["cutoff"]:
            out["repaired"].append(rec)
        else:
            out["unchanged"].append(rec)
    return out


BACKFILL_KEYS = ("dailyhr", "activities")
# activities 是跨日端点（返回最近 N 条，倒序），不是单日端点；补历史日时
# 只拉一次，再按 startTimeLocal 的日期分派。50 条足够覆盖回补窗口。
ACTIVITY_POOL_LIMIT = 50


def _fetch_activities(gca, eps, limit=ACTIVITY_POOL_LIMIT):
    """正式活动列表（跨日，最近 limit 条）。"""
    code, acts = gca.get(eps["activities"].format(limit=limit))
    if code == 200 and isinstance(acts, list):
        return acts
    return None


def backfill(dates, keys=BACKFILL_KEYS):
    """补漏模式：仅对已落盘历史日缺失的键补拉合并，已有键不覆盖。

    逐日端点（dailyhr）按日拉；跨日端点（activities）只拉一次，
    再按 startTimeLocal 的日期分派到各历史日——否则每天都写入同一份
    全量列表，那不是「补漏」而是污染。分派天然只命中目标日，
    与适配器按 `startTimeLocal.startswith(date)` 的过滤口径一致。
    """
    sys.path.insert(0, GARMIN_CN_SCRIPTS)
    import garmin_gc_api as gca
    eps = gca.ENDPOINTS
    summary = {"backfilled": [], "already_present": [], "fetch_failed": {},
               "no_raw": []}
    fetchers = {"dailyhr": lambda d: _fetch_dailyhr(gca, eps, d)}

    pool_by_date = {}
    if "activities" in keys:
        for a in _fetch_activities(gca, eps) or []:
            dd = (a.get("startTimeLocal") or "")[:10]
            if dd:
                pool_by_date.setdefault(dd, []).append(a)

    for d in dates:
        p = raw_path(d)
        if not os.path.isfile(p):
            summary["no_raw"].append(d)
            continue
        day = json.load(open(p))
        touched = False
        for key in keys:
            if key == "activities":
                # activities 是「最近 N 条」的滚动窗口，逐日 raw 里存的是**该日
                # sync 时刻**的快照——快照早于当日运动、或 limit 被更早的活动占满时，
                # 这个键存在、内容却不含本日记录（2026-09-12 实证：09-04~09-09 的 raw
                # 各存 10 条，只有 09-05/09-06 含本日；09-08/09-09 属本日 0 条）。
                # 故判据不是「键是否存在」，而是「本日记录是否已齐」，且不覆盖原
                # activities（保留滚动窗口原貌），另写 activities_own = 仅本日记录，
                # canonical 优先消费它。
                own_new = pool_by_date.get(d) or []
                own_cur = [x for x in (day.get("activities_own") or [])
                           if (x.get("startTimeLocal") or "").startswith(d)]
                if not own_new:
                    summary["fetch_failed"].setdefault(d, []).append(key)
                elif len(own_new) > len(own_cur):
                    day["activities_own"] = own_new
                    day["backfilled_at"] = {
                        **(day.get("backfilled_at") or {}),
                        key: datetime.datetime.now().isoformat(timespec="seconds")}
                    touched = True
                else:
                    summary["already_present"].append(f"{d}:{key}")
                continue
            if day.get(key):
                summary["already_present"].append(f"{d}:{key}")
                continue
            body = fetchers[key](d)
            if body:
                day[key] = body
                day["backfilled_at"] = {**(day.get("backfilled_at") or {}),
                                        key: datetime.datetime.now().isoformat(
                                            timespec="seconds")}
                touched = True
            else:
                summary["fetch_failed"].setdefault(d, []).append(key)
        if touched:
            json.dump(day, open(p, "w"), ensure_ascii=False, indent=1)
            summary["backfilled"].append(d)
    return summary


def main():
    ap = argparse.ArgumentParser(description="Garmin CN 幂等采集落盘")
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--days", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--backfill", action="store_true",
                    help="补漏模式：只补历史日缺失的键，不覆盖已有数据")
    ap.add_argument("--repair", type=int, default=0, metavar="N",
                    help="回补最近 N 天内被截断（partial）的日子；完整日不动")
    a = ap.parse_args()
    if a.repair:
        print(json.dumps(repair_recent(a.repair), ensure_ascii=False, indent=1))
        return
    if a.backfill:
        window = [(datetime.date.fromisoformat(a.date)
                   - datetime.timedelta(days=a.days - 1 - i)).isoformat()
                  for i in range(a.days)]
        print(json.dumps(backfill(window), ensure_ascii=False, indent=1))
        return
    s = sync(a.date, a.days, a.force)
    print(json.dumps(s, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
