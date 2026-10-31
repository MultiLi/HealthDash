#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""执行锚点解析 + 续跑账本（slot ledger）—— 定时任务的「日期锚定」唯一入口。

【要解决的问题（2026-09-09 ~ 09-12 实测事故）】
定时任务若把「目标日」取自运行时刻（shell date），首次执行一旦失败/迟到/被中断，
补跑就会把作业强行对应到**实际执行时段**：
  · 09-09 23:33 那次跑到「拉数」就被中断（会话超时），之后无人记账 → 该日报告永久消失；
  · 09-10 整晚未跑 → 同样消失；
  · 09-11 的 23:30 任务实际在 09-12 01:0x 才跑，只能靠 agent 当场推断才锚回 09-11。
两天报告的丢失，根因是「作业没有身份」——没有东西记录「哪一天的什么工作还没做完」。

【原则】
1. **排程日（slot）= 本作业最近一次应执行的排程时刻所在的自然日**，与运行时刻无关：
     23:30 的任务迟到到次日 01:08 执行 → slot 仍是前一晚那天（例如 09-11）；
     09:30 的任务在 00:00–09:30 之间被唤醒 → slot 是**昨日**（今晨的窗口还没到）。
   目标日 = slot 日，永远如此。运行时刻只用于算迟到量与窗口判定。
2. **账本是唯一事实来源**：已完成即幂等跳过，未完成才补跑。
   迟到/中断后重跑，只会处理「初次执行时段」的作业，不会漂到别的日期上。
3. 账本还兼职看门：如果账本写着 done，但报告文件不在（被中断/被删），
   自动降级为待处理（`artifacts_missing`）→ 下一次运行自愈重出。

【命令】
  init     设置作业的记账起点（track_from），避免把史前日期当成待办
  resolve  解析本次要处理的日期列表（targets）；无待办时 targets 为空
  mark     执行完（或失败/跳过）后逐日记账，必须调用
  status   人工查账本

用法：
  python3 slot.py init    --job evening --track-from 2026-09-09
  python3 slot.py resolve --job evening [--max 3] [--include-stale] [--now ISO]
  python3 slot.py mark    --job evening --date 2026-09-11 --status done \
                          [--data-cutoff 23:58] [--note "..."] [--error "..."]
  python3 slot.py status  [--job evening] [--days 14]

环境变量：HEALTH_STATE_DIR 覆盖账本目录（默认 <workspace>/data/state，测试用）。
"""
import argparse
import datetime
import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))              # body-health/scripts
from settings import ROOT as _WORKSPACE  # noqa: E402  路径唯一解析点（settings.py）
REPORTS = os.environ.get("HEALTH_REPORTS_DIR") or os.path.join(_WORKSPACE, "reports")
STATE_DIR = (os.environ.get("HEALTH_STATE_DIR")
             or os.path.join(_WORKSPACE, "data", "state"))
LEDGER = os.path.join(STATE_DIR, "slots.json")
SUBJ_LOG = os.path.join(_WORKSPACE, "daily_log.jsonl")

SYNC = os.path.join(_HERE, "sync.py")
FACT = os.path.join(_HERE, "fact_pack.py")
REFRESH = os.path.join(_HERE, "refresh_recent.py")
COLLECT = os.path.join(_HERE, "collect.py")
GATE = os.path.join(_HERE, "data_gate.py")
SLOT = os.path.abspath(__file__)
# 2026-10-31 修：原前缀缺 --no-project（被 pyproject 的 requires-python>=3.9 卡死，
# 报错全指向 garth 依赖，是假线索）+ 未 pin curl_cffi（0.17.0 的 chrome 指纹被佳明
# CN 直接 RST）。这两条每天让首班命令白挂一次，已写进命令本身，不再靠 agent 现场试错。
UV_NET = ("no_proxy='*' NO_PROXY='*' uv run --no-project --python 3.11 "
          "--with curl_cffi==0.16.3 --with garth python")
UV_OFF = "uv run --no-project --python 3.11 python"

# 作业定义：改排程时间只改这里
JOBS = {
    "evening": {
        "desc": "每日健康报告（单班 02:00，2026-10-31 由「23:30/02:30/11:30 三班」精简）",
        # 边界恒为 23:30：02:00 那一班属于**前一晚**的 slot，不能算当天。切勿改这两个字段。
        # 多班次已撤销，此处只留边界；账本仍靠「出完即 targets 空」幂等。
        "hour": 23, "minute": 30,
        "runs": ["02:00"],
        "catchup_days": 3,    # 排程日前 N 天内的未完成 slot 自动补跑
        "backlog_days": 14,   # 超过补跑窗但未超此值 → 记为积压（列出，不自动跑）
        "max_targets": 3,     # 单次运行最多处理几天（当天最优先，其余从近到远）
        "same_day_only": False,
        "verify_artifacts": True,   # 以报告产物为准做双向自愈
    },
    "morning": {
        "desc": "晨起主观补数（排程 09:30）· **已撤销 2026-10-31**：无自动化调用方，"
                "账本里残留 pending 属正常孤儿，不要为它补报告",
        "hour": 9, "minute": 30,
        "catchup_days": 0,    # 晨起主观数据不可回溯 → 无补跑
        "backlog_days": 0,
        "max_targets": 1,
        "same_day_only": True,
        "verify_artifacts": False,
    },
}

TERMINAL = ("done", "expired", "skipped")
RETRYABLE = ("pending", "failed", "in_progress", "artifacts_missing",
             "waiting_data", "stale_report")

DISCIPLINE = [
    "目标日一律取 targets[].date；禁止用 shell date / 运行日推导目标日。",
    "slot 未完成时补跑仍锚定原排程日（迟到不等于改期）。",
    "targets 为空 = 本排程日已完成，不要重复生成报告。",
    "每天一份报告：多个 target 逐日各出一份，禁止合并。",
    "【2026-10-31 起】只剩 02:00 一班：data_gate 判 partial 时**直接出「截断终版」**"
    "（--allow-truncated --data-cutoff），禁止记 waiting_data —— 已无兜底班接力。"
    "旧规则「等到 publish_by（D+1 11:00）」随 11:30 兜底班一并作废；"
    "publish_by 字段现在只是信息展示，代码不校验时间。",
    "mark done 会被闸门拦截：数据不完整时账本拒绝关账（除非显式 --allow-truncated）。",
]


# ---------------------------------------------------------------- 账本读写
def load_ledger():
    try:
        led = json.load(open(LEDGER))
        if isinstance(led, dict):
            led.setdefault("version", 1)
            led.setdefault("jobs", {})
            return led
    except FileNotFoundError:
        pass
    except Exception as e:
        raise SystemExit(f"账本损坏，请人工检查 {LEDGER}: {e}")
    return {"version": 1, "jobs": {}}


def save_ledger(led):
    os.makedirs(STATE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR, prefix=".slots-", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(led, f, ensure_ascii=False, indent=1)
    os.replace(tmp, LEDGER)


def _job(led, job):
    return led.setdefault("jobs", {}).setdefault(job, {})


def _slots(led, job):
    j = _job(led, job)
    return j.setdefault("slots", {})


# ---------------------------------------------------------------- 状态判定
def artifacts(date):
    """报告产物三件套的在盘情况（evening）。

      md        报告本体          reports/body_brief_<D>.md
      judgment  判读 json         reports/judgment_<D>.json
      merged    契约已合并判读    reports/body_brief_<D>.json 的 judgment 字段非空
    """
    md = os.path.join(REPORTS, f"body_brief_{date}.md")
    jd = os.path.join(REPORTS, f"judgment_{date}.json")
    br = os.path.join(REPORTS, f"body_brief_{date}.json")
    merged = False
    if os.path.isfile(br):
        try:
            merged = bool(json.load(open(br)).get("judgment"))
        except Exception:
            merged = False
    return {"md": os.path.isfile(md), "judgment": os.path.isfile(jd),
            "merged": merged}


def _repair_contract(date):
    """报告本体齐全、只差契约合并（同一天被二次 fact_pack 覆盖过 judgment）→ 补并。
    机械修复，不产生也不改写任何判读内容；被调用方的 print 全部改道 stderr，
    保证本脚本 stdout 始终只有一份可解析的 JSON。"""
    import contextlib
    import fact_pack                                   # 懒加载，保持常态零依赖
    with contextlib.redirect_stdout(sys.stderr):
        fact_pack.merge_judgment(date, os.path.join(REPORTS, f"judgment_{date}.json"))


def effective_status(led, job, date, repair=True):
    """账本状态 × 产物校验（双向自愈，产物为准）。

      · 账本 done 但报告本体不在        → artifacts_missing（重出报告）
      · 报告本体齐全但契约没并判读      → 自动补并契约后视为 done（不重出报告）
      · 账本没记录但产物齐全            → done（账本丢失/回滚时按产物认账）
      · 报告已出但数据被后来的回补超越  → stale_report（重写同一文件，仍是单一终版）
    morning 的主观数据来自人的回答，可能合法缺席，故不做产物校验。
    """
    rec = (_slots(led, job).get(date) or {})
    st = rec.get("status") or "pending"
    if not JOBS[job].get("verify_artifacts"):
        return st
    a = artifacts(date)
    complete = a["md"] and a["judgment"] and a["merged"]
    if complete:
        # 已发布的报告若当时是截断值，而现在 raw 已补全 → 必须按单一终版重写。
        # 否则「报告基于不完整数据」只是从「写的时候」挪到了「写完之后」。
        # 只在补跑窗内做（超过 3 天的历史报告按既有终版认账，不再翻旧账）。
        if st == "done" and _truncated_cutoff(rec.get("data_cutoff")) \
                and _recent(date, JOBS[job].get("catchup_days", 3)):
            g = _gate(date)
            if g.get("level") == "complete":
                return "stale_report"
        return "done"
    if a["md"] and a["judgment"] and not a["merged"]:
        if repair:
            _repair_contract(date)
            return "done"
        return "artifacts_missing"
    if st == "done":
        return "artifacts_missing"
    return st


def _truncated_cutoff(s):
    """账本里的 data_cutoff 是否为截断值（"19:34" 是，"24:00"/"无数据" 不是）。"""
    if not s or not isinstance(s, str):
        return False
    try:
        hh, mm = s.strip().split(":")
        return int(hh) * 60 + int(mm) < 1430
    except (ValueError, AttributeError):
        return False


def _recent(date, days):
    """该日是否仍在「值得回头改」的窗口内。"""
    try:
        return (datetime.date.today()
                - datetime.date.fromisoformat(date)).days <= int(days)
    except (ValueError, TypeError):
        return False


def _gate(date):
    """完整性闸门（只读 canonical，不联网）。失败时降级为 unknown，绝不阻断流程。"""
    try:
        import data_gate
        return data_gate.inspect(date)
    except Exception as e:
        return {"level": "unknown", "blocking": ["闸门不可用：%s" % e],
                "cutoff": None, "can_publish_truncated": False}


# ---------------------------------------------------------------- 排程算术
def _boundary(d, J):
    return datetime.datetime.combine(d, datetime.time(J["hour"], J["minute"]))


TOL_MIN = 5   # 排程提前量容差：调度器早于排程时刻几秒/几分钟触发时，
              # 应归入「今天这一班」而不是退回上一班（否则当天报告会被漏掉一班）


def current_slot(now, J):
    """本作业最近一次应执行的排程时刻所在的自然日。"""
    d = now.date()
    tol = datetime.timedelta(minutes=TOL_MIN)
    return d if now >= _boundary(d, J) - tol else d - datetime.timedelta(days=1)


def _tracked(job_rec, d):
    tf = job_rec.get("track_from")
    if tf:
        return d.isoformat() >= tf
    return d.isoformat() in (job_rec.get("slots") or {})


def _target(job, d, kind, now, J, status):
    iso = d.isoformat()
    late = round((now - _boundary(d, J)).total_seconds() / 3600.0, 2)
    g = _gate(iso) if J.get("verify_artifacts") else {}
    return {
        "date": iso,
        "kind": kind,                       # current | catchup
        "lateness_h": late,                 # 距原排程时刻的迟到量（小时）
        "status": status,                   # 账本里的当前状态
        # 采集：拉目标日 + 自动回补窗口内在截断的历史日（完整日不动）
        "collect_cmd": f"{UV_NET} {COLLECT} --days 4 --no-push",
        "sync_cmd": f"{UV_NET} {SYNC} --date {iso} --days 4",
        "fact_cmd": f"{UV_OFF} {FACT} --date {iso} --days 4",
        "merge_cmd": (f"{UV_OFF} {FACT} --date {iso} "
                      f"--merge-judgment {REPORTS}/judgment_{iso}.json"),
        "mark_cmd": f"python3 {SLOT} mark --job {job} --date {iso} --status done",
        "wait_cmd": f"python3 {SLOT} mark --job {job} --date {iso} --status waiting_data",
        # 完整性闸门：report_allowed=false 时**禁止写报告**，直接 wait_cmd 收尾
        "gate_cmd": f"python3 {GATE} check --date {iso}",
        "data_gate": {
            "level": g.get("level"),
            "cutoff": g.get("cutoff"),
            "blocking": g.get("blocking") or [],
            "publish_by": g.get("publish_by"),
            "can_publish_truncated": bool(g.get("can_publish_truncated")),
        },
        # 闸门故障时 fail-open：宁可出报告也不让流水线静默停摆（but 记 unknown）
        "report_allowed": g.get("level") in ("complete", "no_data", "unknown") or
                          bool(g.get("can_publish_truncated")),
        "brief_md": os.path.join(REPORTS, f"body_brief_{iso}.md"),
        "judgment_json": os.path.join(REPORTS, f"judgment_{iso}.json"),
    }


# ---------------------------------------------------------------- 命令
def cmd_init(a):
    led = load_ledger()
    j = _job(led, a.job)
    j["track_from"] = a.track_from
    j.setdefault("desc", JOBS[a.job]["desc"])
    j["inited_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    save_ledger(led)
    print(json.dumps({"ok": True, "job": a.job,
                      "track_from": a.track_from}, ensure_ascii=False))


def cmd_resolve(a):
    J = JOBS[a.job]
    now = datetime.datetime.fromisoformat(a.now) if a.now else datetime.datetime.now()
    led = load_ledger()
    job_rec = _job(led, a.job)
    slots = job_rec.setdefault("slots", {})
    cur = current_slot(now, J)
    max_t = a.max if a.max else J["max_targets"]
    cap = cur - datetime.timedelta(days=J["catchup_days"])
    stale_edge = cur - datetime.timedelta(days=J["backlog_days"])
    scan_from = cur - datetime.timedelta(days=a.scan_days)

    targets, expired_now, backlog = [], [], []
    changed = False

    # 1) 当前排程日最优先（当天那份永远先出）
    if _tracked(job_rec, cur):
        actionable = (not J.get("same_day_only")) or cur == now.date()
        st = effective_status(led, a.job, cur.isoformat())
        if actionable and st not in TERMINAL:
            targets.append(_target(a.job, cur, "current", now, J, st))

    # 2) 过去的 slot：从近到远
    d = cur - datetime.timedelta(days=1)
    while d >= scan_from:
        iso = d.isoformat()
        if _tracked(job_rec, d):
            st = effective_status(led, a.job, iso)
            if st not in TERMINAL:
                if d >= cap:
                    if len(targets) < max_t:
                        targets.append(_target(a.job, d, "catchup", now, J, st))
                elif d >= stale_edge:
                    backlog.append({"date": iso, "status": st,
                                    "age_days": (cur - d).days})
                else:
                    # 超出补跑窗 → 关账，不再每日提醒（仍可 --include-stale 手工拉回）
                    r = slots.setdefault(iso, {})
                    r["status"] = "expired"
                    r["updated_at"] = now.isoformat(timespec="seconds")
                    r["note"] = (f"超出自动补跑窗（>{J['catchup_days']} 天）未执行，"
                                 f"于 {now.date().isoformat()} 关账")
                    expired_now.append(iso)
                    changed = True
        d -= datetime.timedelta(days=1)

    # 3) --include-stale：把积压/已关账的也纳入本次
    if a.include_stale:
        extra = []
        for iso in [b["date"] for b in backlog] + expired_now:
            if len(targets) >= max_t:
                break
            if all(t["date"] != iso for t in targets):
                extra.append(_target(a.job, datetime.date.fromisoformat(iso),
                                     "catchup", now, J,
                                     effective_status(led, a.job, iso)))
        targets.extend(extra)
        expired_now = [e for e in expired_now
                       if all(t["date"] != e for t in targets)]

    cur_st = effective_status(led, a.job, cur.isoformat())
    if not targets:
        mode = "noop"
    elif any(t["kind"] == "catchup" for t in targets):
        mode = "catchup"
    elif targets[0]["lateness_h"] >= 0.5:
        mode = "late"
    else:
        mode = "scheduled"

    out = {
        "job": a.job,
        "desc": J["desc"],
        "workspace": _WORKSPACE,
        "now": now.isoformat(timespec="seconds"),
        "now_date": now.date().isoformat(),
        "slot_date": cur.isoformat(),
        "slot_time": _boundary(cur, J).isoformat(timespec="minutes"),
        "mode": mode,
        "action": "run" if targets else "nothing-to-do",
        "current_slot_status": cur_st,
        "targets": targets,
        "expired_now": expired_now,
        "backlog_stale": backlog,
        "ledger": LEDGER,
        "refresh_cmd": f"python3 {REFRESH} --days 4",
        "discipline": DISCIPLINE,
    }
    if changed:
        save_ledger(led)
    print(json.dumps(out, ensure_ascii=False, indent=1))


def cmd_mark(a):
    if a.status not in ("done", "failed", "skipped", "expired", "in_progress",
                        "waiting_data"):
        raise SystemExit(f"非法状态：{a.status}")

    # ---- 闸门：数据不完整时账本拒绝关账（2026-10-18 建）----
    # 这是「报告不基于不完整数据」的硬约束。以前靠判读者自觉，
    # 10-17/10-18 两次都在 22:05 / 19:34 截断数据上写完了报告。
    gate = None
    if a.status == "done" and not a.allow_truncated:
        gate = _gate(a.date)
        if gate.get("level") in ("partial", "missing_raw"):
            print(json.dumps({
                "ok": False,
                "error": "数据不完整，拒绝关账（报告不得基于截面数据）",
                "date": a.date, "level": gate.get("level"),
                "cutoff": gate.get("cutoff"),
                "blocking": gate.get("blocking"),
                "hint": ("先跑 collect 回补/等下一班；若已到 publish_by 时限仍需出报告，"
                         "改写为「截断终版」并加 --allow-truncated 显式声明。"),
                "mark_cmd_if_final": f"python3 {SLOT} mark --job {a.job} "
                                     f"--date {a.date} --status done "
                                     f"--allow-truncated --data-cutoff "
                                     f"{gate.get('cutoff')}",
            }, ensure_ascii=False, indent=1))
            raise SystemExit(3)

    led = load_ledger()
    slots = _slots(led, a.job)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    r = slots.setdefault(a.date, {})
    r.setdefault("first_seen_at", now)
    r["status"] = a.status
    r["updated_at"] = now
    r["attempts"] = int(r.get("attempts") or 0) + 1
    if a.status == "done":
        r["finished_at"] = now
        r.pop("last_error", None)
        if a.allow_truncated:
            r["truncated"] = True
    if a.error:
        r["last_error"] = a.error
    if a.note:
        r["note"] = a.note
    if a.data_cutoff:
        r["data_cutoff"] = a.data_cutoff
    elif gate and gate.get("cutoff"):
        r["data_cutoff"] = gate["cutoff"]
    if a.source:
        r["source"] = a.source
    save_ledger(led)
    print(json.dumps({"ok": True, "job": a.job, "date": a.date,
                      "status": a.status, "attempts": r["attempts"],
                      "gate": (gate or {}).get("level"),
                      "ledger": LEDGER}, ensure_ascii=False))


def cmd_status(a):
    led = load_ledger()
    jobs = [a.job] if a.job else list(JOBS)
    today = datetime.date.today()
    for job in jobs:
        j = _job(led, job)
        slots = j.get("slots") or {}
        print(f"## {job} — {JOBS[job]['desc']}")
        print(f"   track_from={j.get('track_from') or '(未设置=只认已记账日期)'}  "
              f"ledger={LEDGER}")
        rows = []
        for i in range(a.days):
            d = (today - datetime.timedelta(days=a.days - 1 - i)).isoformat()
            rec = slots.get(d) or {}
            st = effective_status(led, job, d)
            mk = "ok" if st == "done" else ("—" if st == "pending" else st)
            if job == "evening":
                prod = ",".join(k for k, v in (
                    ("md", os.path.isfile(os.path.join(REPORTS, f"body_brief_{d}.md"))),
                    ("jud", os.path.isfile(os.path.join(REPORTS, f"judgment_{d}.json"))),
                    ("raw", os.path.isfile(os.path.join(
                        os.path.dirname(REPORTS), "data", "raw", f"{d}.json"))),
                ) if v) or "-"
            else:
                prod = rec.get("note") or ""
            rows.append((d, st, prod, rec.get("updated_at") or ""))
        print(f"   {'日期':<12}{'状态':<18}{'产物':<16}更新时间")
        for d, st, prod, up in rows:
            print(f"   {d:<12}{st:<18}{prod:<16}{up}")
        print()
    print(f"账本文件：{LEDGER}")


def main():
    ap = argparse.ArgumentParser(description="执行锚点解析 + 续跑账本")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.add_argument("--job", required=True, choices=list(JOBS))
    p.add_argument("--track-from", dest="track_from", required=True)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("resolve")
    p.add_argument("--job", required=True, choices=list(JOBS))
    p.add_argument("--max", type=int, default=0, help="本次最多处理几天")
    p.add_argument("--include-stale", dest="include_stale", action="store_true")
    p.add_argument("--scan-days", dest="scan_days", type=int, default=60)
    p.add_argument("--now", default=None, help="覆盖当前时间（测试用 ISO）")
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("mark")
    p.add_argument("--job", required=True, choices=list(JOBS))
    p.add_argument("--date", required=True)
    p.add_argument("--status", required=True,
                   choices=["done", "failed", "skipped", "expired", "in_progress",
                            "waiting_data"])
    p.add_argument("--error", default=None)
    p.add_argument("--note", default=None)
    p.add_argument("--data-cutoff", dest="data_cutoff", default=None)
    p.add_argument("--source", default=None)
    p.add_argument("--allow-truncated", dest="allow_truncated",
                   action="store_true",
                   help="数据不完整仍要出报告时显式声明（仅 publish_by 之后）")
    p.set_defaults(func=cmd_mark)

    p = sub.add_parser("status")
    p.add_argument("--job", default=None, choices=list(JOBS))
    p.add_argument("--days", type=int, default=14)
    p.set_defaults(func=cmd_status)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
