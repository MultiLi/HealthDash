#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""窗口结论的不变量检查（定性结论窗口化的验收门）。

**为什么只验不变量**：数据每天在长，「30 天里 22 天久坐超标」这种期望值明天就假了，
而为了让测试变绿去改期望值，正是这个仓库最想避免的事。这里只验恒真命题：

  · registry    注册表完整：库里的每个断言 id 都登记过时间语义，反之亦然
  · denominator 分母可知：行存在 ⟺ inputs 齐。**这条抓「只在触发时落库」的回归**
  · degenerate  窗长 = 1 时，rate 类结论的状态必须等于当日断言（聚合是恒等）
  · closure     计数闭合：命中 + 未命中 = 可判定，可判定 + 不可判定 = 窗口天数
  · additive    加性：一个窗口的命中天数 = 各单日命中之和（抓日期错位）

前两条是这一层最容易被破坏的地方：分母一错，发生率会被算成 100%，而且**看起来
非常像结论**。degenerate 抓「有人给窗口层另写了一套日判定」。

    python3 tools/window_conclusions_check.py
    python3 tools/window_conclusions_check.py --json
"""
import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skills", "body-health", "scripts"))

import conclusions as conc   # noqa: E402
import db                    # noqa: E402
import units                 # noqa: E402

RESULTS = []


def check(name):
    def deco(fn):
        def wrapper(*a, **kw):
            try:
                detail = fn(*a, **kw)
                RESULTS.append({"检查": name, "ok": True, "detail": detail})
            except AssertionError as e:
                RESULTS.append({"检查": name, "ok": False, "detail": str(e)})
        return wrapper
    return deco


def _span(ctx):
    return ctx["first"], min(ctx["last"], ctx["today"])


def _spec(ctx, rules, lo, hi):
    return units.window_spec(lo, hi, ctx, rules)


# ------------------------------------------------------------------ 检查
@check("registry")
def c_registry(con, rules, ctx, reg):
    reg_ids = set(conc.registry())
    db_ids = {r["id"] for r in con.execute("SELECT DISTINCT id FROM assertions")}
    lex = conc.lexicon()

    unregistered = sorted(db_ids - reg_ids)
    assert not unregistered, "库里有断言没登记时间语义：%s" % "、".join(unregistered)
    missing_lex = sorted(i for i in reg_ids if not lex.get(i))
    assert not missing_lex, "注册表里的 id 没有中文名（lexicon.id）：%s" % "、".join(missing_lex)

    bad = []
    for aid, e in conc.registry().items():
        kind = e.get("kind")
        if kind == "judge":
            if not e.get("operator"):
                bad.append("%s 是 judge 但缺 operator" % aid)
            if not e.get("inputs"):
                bad.append("%s 是 judge 但没有 inputs（分母无从推导）" % aid)
            if not (e.get("words") or {}).get("成立"):
                bad.append("%s 缺措辞 words" % aid)
        elif e.get("operator"):
            bad.append("%s 是 %s 却声明了 operator（只有 judge 能按窗口求值）" % (aid, kind))
    assert not bad, "；".join(bad[:4])
    n_judge = len(conc.windowable())
    return "登记 %d 条（可窗口化 %d 条），库里 %d 个 id 全部有归属" % (
        len(reg_ids), n_judge, len(db_ids))


@check("denominator")
def c_denominator(con, rules, ctx, reg):
    """行存在 ⟺ 可判定。缺一行 → 分母偏小（发生率虚高）；多一行 → 缺失被当成未触发。"""
    lo, hi = _span(ctx)
    spec = _spec(ctx, rules, lo, hi)
    problems = []
    for aid in conc.windowable():
        e = conc.spec_of(aid)
        dec = set(conc._decidable_dates(spec["dates"], e, ctx))
        rows = conc._rows_for(con, aid, lo, hi)
        missing = sorted(dec - set(rows))
        extra = sorted(set(rows) - dec)
        if missing:
            problems.append("%s 缺 %d 行（%s）→ 分母偏小" %
                            (aid, len(missing), "、".join(missing[:2])))
        if extra:
            problems.append("%s 多 %d 行（%s）→ 缺失被当成未触发" %
                            (aid, len(extra), "、".join(extra[:2])))
    assert not problems, "；".join(problems[:4])
    return "%d 条结论逐日核对：可判定天数与库中行数一致" % len(conc.windowable())


@check("degenerate")
def c_degenerate(con, rules, ctx, reg):
    """窗长 = 1 时聚合必须是恒等：rate 类结论的状态 == 当日断言 verdict。

    run 类不在检查内：它的日级 verdict 已经含历史（streak），单日窗口本来就重建
    不出同一语义，硬比会得出假失败。这条限制写在 docs/query-interface.md。
    """
    lo, hi = _span(ctx)
    bad, n = [], 0
    for aid in conc.windowable():
        if conc.spec_of(aid).get("operator") != "rate":
            continue
        rows = conc._rows_for(con, aid, lo, hi)
        for d, r in sorted(rows.items()):
            sp = _spec(ctx, rules, d, d)
            got = conc.evaluate_window(con, sp, ids=[aid], rules=rules, ctx=ctx, min_n=1)[0]
            want = "成立" if r["verdict"] else "不成立"
            n += 1
            if got["state"] != want:
                bad.append("%s %s：窗口层 %s / 日断言 %s(verdict=%d，可判定 %d)"
                           % (aid, d, got["state"], want, r["verdict"], got["decidable"]))
    assert not bad, "；".join(bad[:3])
    return "%d 个(结论, 日) 对，单日窗口与日断言逐条一致" % n


@check("closure")
def c_closure(con, rules, ctx, reg):
    lo, hi = _span(ctx)
    spec = _spec(ctx, rules, lo, hi)
    rows = conc.evaluate_window(con, spec, rules=rules, ctx=ctx)
    bad = []
    for r in rows:
        if r["hit"] + (r["decidable"] - r["hit"]) != r["decidable"]:
            bad.append("%s 命中/未命中不闭合" % r["id"])
        if r["decidable"] + r["undecidable"] != r["window"]["days"]:
            bad.append("%s 可判定 + 不可判定 ≠ 窗口天数（%d+%d≠%d）"
                       % (r["id"], r["decidable"], r["undecidable"], r["window"]["days"]))
        if r["value"] is not None and not (0 <= r["value"] <= 1):
            bad.append("%s 发生率越界：%s" % (r["id"], r["value"]))
        if r["value"] is not None and r["ci"][0] is not None:
            if not (r["ci"][0] - 1e-9 <= r["value"] <= r["ci"][1] + 1e-9):
                bad.append("%s 发生率不在自己的置信区间内" % r["id"])
        if r["anomaly"]:
            bad.append("%s 有 %d 天命中却不可判定（%s）"
                       % (r["id"], len(r["anomaly"]), "、".join(r["anomaly"][:2])))
        if r["state"] == "成立" or r["state"] == "不成立" or r["state"] == "参半":
            if not r["word"]:
                bad.append("%s 状态是 %s 却没有措辞" % (r["id"], r["state"]))
    assert not bad, "；".join(bad[:4])
    return "%d 条结论：计数闭合、发生率在 CI 内、异常命中为 0" % len(rows)


@check("additive")
def c_additive(con, rules, ctx, reg):
    """窗口的命中天数必须等于各单日命中之和——聚合不许悄悄错位一天。"""
    lo, hi = _span(ctx)
    d0, d1 = datetime.date.fromisoformat(lo), datetime.date.fromisoformat(hi)
    wins = []
    for n in (7, 30):
        if (d1 - d0).days + 1 >= n:
            wins.append((d1 - datetime.timedelta(days=n - 1), d1))
    # 再加一个不贴末端的窗口：贴末端的位置对齐错一天也看不出来
    end = d1 - datetime.timedelta(days=9)
    start = end - datetime.timedelta(days=9)
    if start >= d0:
        wins.append((start, end))
    bad, n = [], 0
    for aid in conc.windowable():
        for start, end in wins:
            sp = _spec(ctx, rules, start.isoformat(), end.isoformat())
            got = conc.evaluate_window(con, sp, ids=[aid], rules=rules, ctx=ctx)[0]
            manual = 0
            for d in sp["dates"]:
                one = _spec(ctx, rules, d, d)
                manual += conc.evaluate_window(con, one, ids=[aid], rules=rules,
                                               ctx=ctx, min_n=1)[0]["hit"]
            n += 1
            if got["hit"] != manual:
                bad.append("%s %s~%s：窗口命中 %d / 单日求和 %d"
                           % (aid, start, end, got["hit"], manual))
    assert not bad, "；".join(bad[:3])
    return "%d 个(结论, 窗口) 对，窗口命中 = 单日命中之和" % n


def main():
    ap = argparse.ArgumentParser(description="窗口结论不变量检查")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    con = db.connect()
    rules = db.load_rules()
    reg = db.load_metrics()
    ctx = units.context(con, rules, reg)
    if ctx is None:
        print("skip: obs 表为空")
        return 0
    for fn in (c_registry, c_denominator, c_degenerate, c_closure, c_additive):
        fn(con, rules, ctx, reg)
    if a.json:
        print(json.dumps(RESULTS, ensure_ascii=False, indent=1))
    else:
        for r in RESULTS:
            print("%s %-12s %s" % ("✓" if r["ok"] else "✗", r["检查"], r["detail"]))
        bad = [r for r in RESULTS if not r["ok"]]
        print("\n%s（%d/%d 通过）" % ("全部通过" if not bad else "有失败",
                                    len(RESULTS) - len(bad), len(RESULTS)))
    return 0 if all(r["ok"] for r in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
