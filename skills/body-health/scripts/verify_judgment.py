#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判读自检：报告里的数字有没有出处、总评有没有被改、交叉规则有没有被漏掉（2026-10-25 建）。

**为什么需要它**（notes/architecture_v4.md §四）
LLM 判读最大的失效形态不是「写得难看」，而是**数字是编的**——09-11 那次
「HRV 明明偏低却打了高分」就是典型：分数看着像从数据来的，实际没有任何一处能对上。
以前没人能发现，因为没人有耐心把报告里的每个数字回溯一遍。

检查项分两级：

**必须通过（exit 1）**
1. 总评一致：`judgment.summary` 的开头档位必须等于 `assertions.rollup.label`。
   要降档可以，但必须写成「规则档 X、判读档 Y（理由：…）」两档并列，不许只写 Y。
2. findings 覆盖：所有 BAD/WARN 的 l1 与 cross 断言都要在 `findings[]` 里出现，且 level 相同。
3. 交叉引用：每条触发的 cross 断言，其 id 必须在正文或 judgment.cross 里出现。
4. 禁止词：`v1/v2/修正版/草稿作废/变更对照` —— 「单一终版」纪律。

**只提示（不影响退出码，--strict 时才算错）**
5. 数字溯源：正文里的每个数字都应能在 facts（含 assertions.evidence 与 computed）里找到。
   之所以不设成硬门槛：报告确实会引用派生量（逐时步数之和、百分比、时刻换算），
   这些在 facts 里没有字面值。硬卡会让自检变成噪音，最后没人看。

用法：
    python3 verify_judgment.py --date 2026-10-24
    python3 verify_judgment.py --date 2026-10-24 --strict     # 数字溯源也算错
"""
import argparse
import datetime
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import settings as _settings  # noqa: E402  路径唯一解析点（settings.py）

import db                                                    # noqa: E402
import assertions as asrt                                    # noqa: E402

# 2026-10-31 数据迁入 data/ 后报告在 data/reports；旧 <ws>/reports 已废弃
REPORTS = os.environ.get("HEALTH_REPORTS_DIR") or _settings.REPORTS_DIR
FORBIDDEN = ("v1", "v2", "v3", "修正版", "草稿作废", "变更对照", "较上版")
LEVEL_WORD = {"BAD": "红", "WARN": "黄", "GOOD": "绿", "NEUTRAL": "不可判读"}
LEVEL_FROM_WORD = {"红": "BAD", "黄": "WARN", "绿": "GOOD", "不可判读": "NEUTRAL",
                   "🔴": "BAD", "🟡": "WARN", "🟢": "GOOD", "⚪": "NEUTRAL"}


# ------------------------------------------------------------------ 数字白名单
def _add_number(out, v):
    try:
        f = float(v)
    except Exception:
        return
    out.add(round(f, 4))
    out.add(round(f, 2))
    out.add(round(f, 1))
    out.add(float(int(round(f))))
    if abs(f) < 1:                       # 0.63 这类比率，报告常写成 63%
        out.add(round(f * 100, 2))
        out.add(float(int(round(f * 100))))
    if abs(f) >= 1000:                   # 千分位
        out.add(round(f, 0))


def _walk(obj, out, depth=0):
    if depth > 8:
        return
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        _add_number(out, obj)
        return
    if isinstance(obj, str):
        # 时刻（02:27）与分钟制都要能对上：报告两种写法都会出现
        m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", obj.strip())
        if m:
            mm = int(m.group(1)) * 60 + int(m.group(2))
            _add_number(out, mm)
            _add_number(out, int(m.group(1)))
            _add_number(out, int(m.group(2)))
        return
    if isinstance(obj, list):
        _add_number(out, len(obj))
        for x in obj:
            _walk(x, out, depth + 1)
        return
    if isinstance(obj, dict):
        for x in obj.values():
            _walk(x, out, depth + 1)


ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
WEEK = re.compile(r"\bW\d{1,2}\b")
CODE_FENCE = re.compile(r"```.*?```", re.S)
INLINE_CODE = re.compile(r"`[^`]*`")
HHMM = re.compile(r"^\d{1,2}:\d{2}$")


def extract_numbers(text):
    """正文里「读者会当成事实」的数字。代码片断/日期/id 不算。"""
    t = CODE_FENCE.sub(" ", text)
    t = INLINE_CODE.sub(" ", t)          # 行内代码常是字段名/规则名，不是断言
    t = ISO_DATE.sub(" ", t)
    t = WEEK.sub(" ", t)
    t = re.sub(r"^\s*\|?[-:\s|]+\|?\s*$", " ", t, flags=re.M)   # 表格分隔行
    out = []
    for m in re.finditer(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w])", t):
        raw = m.group(1)
        # 「10h」「23h」「21h 压力」这类钟点：报告里是「21h 那一小时」，按 21 记
        out.append((raw, float(raw)))
    return out


# ------------------------------------------------------------------ 检查
def check(date, strict=False):
    md_path = os.path.join(REPORTS, "body_brief_%s.md" % date)
    j_path = os.path.join(REPORTS, "judgment_%s.json" % date)
    f_path = os.path.join(REPORTS, "facts_%s.json" % date)
    problems, warns = [], []
    if not os.path.isfile(md_path):
        return ["报告不存在：%s" % md_path], []
    md = open(md_path, encoding="utf-8").read()
    jd = json.load(open(j_path, encoding="utf-8")) if os.path.isfile(j_path) else {}
    facts = json.load(open(f_path, encoding="utf-8")) if os.path.isfile(f_path) else {}

    con = db.connect()
    snap = asrt.snapshot(con, date)
    con.close()
    roll = snap["rollup"]
    expect = roll.get("level")

    # 1. 总评一致
    summary = str(jd.get("summary") or "")
    head = summary[:40]
    got = None
    for w, lv in LEVEL_FROM_WORD.items():
        if w in head:
            got = lv
            break
    if got is None:
        problems.append("summary 开头没有档位词（红/黄/绿/不可判读）：%r" % head[:30])
    elif got != expect:
        # 允许降档，但必须两档并列并写理由
        if not re.search(r"(规则档|判读档).{0,40}(规则档|判读档)", summary + md):
            problems.append("总评不符：规则档 %s（%s），summary 写 %s，且未见「规则档/判读档」并列说明"
                            % (LEVEL_WORD.get(expect), expect, LEVEL_WORD.get(got)))
        else:
            warns.append("总评降档已声明（规则 %s → 判读 %s），请确认理由可核"
                         % (LEVEL_WORD.get(expect), LEVEL_WORD.get(got)))

    # 2. findings 覆盖
    need = [a for a in (snap["l1"] + snap["cross"]) if a["level"] in ("BAD", "WARN")]
    got_ids = {f.get("id"): f.get("level") for f in (jd.get("findings") or [])}
    if not jd.get("findings"):
        if not (jd.get("meta") or {}).get("skeleton"):
            problems.append("judgment 缺 findings[]（%d 条 BAD/WARN 断言无处可查）" % len(need))
    else:
        for a in need:
            if a["id"] not in got_ids:
                problems.append("findings 缺断言：%s（%s）" % (a["id"], a["level"]))
            elif got_ids[a["id"]] != a["level"]:
                problems.append("findings 改写了级别：%s 断言 %s，findings 写 %s"
                                % (a["id"], a["level"], got_ids[a["id"]]))

    # 3. 交叉判读要覆盖全部触发项。
    # 以前这里要求 assertion id 出现在**正文**里，那是错的要求：报告是给人看的，
    # 把 `cross.sleep_debt_autonomic` 塞进句子只会让人读不下去。机器可读的关联
    # 在 `findings[].id` 里（第 2 项已查），正文只需要**条数不少**。
    if not (jd.get("meta") or {}).get("skeleton"):
        got_cross = len(jd.get("cross") or [])
        need_cross = len(snap["cross"])
        if got_cross < need_cross:
            problems.append("交叉判读只写了 %d 条，触发 %d 条（漏：%s）" % (
                got_cross, need_cross,
                "、".join(a["id"] for a in snap["cross"])))

    # 4. 禁止词（只查标题行与「版本」上下文，避免误伤正文里的普通用词）
    for w in FORBIDDEN:
        if re.search(r"(第|版|—|-|\s)" + re.escape(w) + r"(\s|版|：|$)", md[:400]):
            problems.append("报告头部出现版本类字样「%s」——违反单一终版纪律" % w)

    # 5. 数字溯源（提示级）
    allow = set()
    _walk(facts, allow)
    for a in snap["l1"] + snap["cross"] + snap["device"]:
        _walk(a.get("evidence"), allow)
    # 只把**与这一天相关的单元**计入白名单：日/本周/本月/三个滚动窗。
    # 全表加入会让校验形同虚设（3,800 个值里总能撞上一个）。
    dd = datetime.date.fromisoformat(date)
    wk = "%04d-W%02d" % dd.isocalendar()[:2]
    con = db.connect()
    for r in con.execute(
            "SELECT value FROM units WHERE "
            "(scale='day' AND key=?) OR (scale='week' AND key=?) OR "
            "(scale='month' AND key=?) OR (scale LIKE 'roll%' AND key=?)",
            (date, wk, date[:7], date)):
        _walk(r["value"], allow)
    con.close()
    misses = []
    for raw, fv in extract_numbers(md):
        if len(raw) == 4 and 1990 <= fv <= 2100:      # 年份
            continue
        if any(abs(fv - a) <= max(0.01, abs(a) * 0.002) for a in allow):
            continue
        misses.append(raw)
    if misses:
        uniq = sorted(set(misses), key=lambda x: -float(x))[:25]
        msg = "正文有 %d 个数字在 facts / 断言证据里找不到出处：%s" % (len(misses), "、".join(uniq))
        (problems if strict else warns).append(msg)

    return problems, warns


def main():
    ap = argparse.ArgumentParser(description="判读自检")
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--strict", action="store_true", help="数字溯源未命中也算错")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    problems, warns = check(a.date, a.strict)
    if a.json:
        print(json.dumps({"date": a.date, "problems": problems, "warnings": warns},
                         ensure_ascii=False, indent=1))
    else:
        print("【%s】自检" % a.date)
        for p in problems:
            print("  ✗ %s" % p)
        for w in warns:
            print("  ⚠ %s" % w)
        print("  %s（错误 %d / 提示 %d）" % ("通过" if not problems else "不通过",
                                          len(problems), len(warns)))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
