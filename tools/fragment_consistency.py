#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""片段层一致性报告：片段回推 vs 日汇总，逐指标给结论（2026-11-01 建，roadmap P2）。

**为什么必须有它**（`docs/p2-fragment-layer.md` 不变式 1）：
片段永远不能改日值。但「切出来的小时加上去，还等于原来的日值吗」这个事实
必须**每天被算一遍并留档**——否则哪天源换了桶的切法，日层数字不变而片段全错，
谁都发现不了。

三种结论，一个指标都不能没有：
    恒等      片段回推 == 日值（在容差内）→ 可以进 2c 白名单
    不恒等    差值口径一并打印（差多少、几天不等）→ 只作分布，日值仍由日汇总裁决
    无日级对照 该指标日级本来就没有可比口径（理由写在 fragments.json）

退出码：白名单指标出现不恒等 → 1（这是「本该恒等却不等」，属实缺陷）；
非白名单指标不恒等 → 0（这是已知口径差异，报告里留档即可）。

    python3 tools/fragment_consistency.py
    python3 tools/fragment_consistency.py --json      # 给 CI / 下游读
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skills", "body-health", "scripts"))

import db          # noqa: E402
import fragments   # noqa: E402


def _rows(con):
    """(metric, 归属日, 值)：片段按 conditions.night 归夜，其余按 t 的日期。"""
    day, hour = {}, {}
    for r in con.execute("SELECT metric, t, value, conditions FROM obs"):
        m = r["metric"]
        if m.startswith(fragments.PREFIX):
            key = r["t"][:10]
            try:
                cond = json.loads(r["conditions"] or "{}")
            except ValueError:
                cond = {}
            key = cond.get("night") or key
            hour.setdefault(m, {}).setdefault(key, []).append(r["value"])
        else:
            day.setdefault(m, {})[r["t"][:10]] = r["value"]
    return day, hour


def analyse(con=None):
    con = con or db.connect()
    day, hour = _rows(con)
    pairs = fragments.pairs()
    cfg = fragments.config()
    covered = set()
    out = []
    for pair in pairs:
        frags = fragments.fragments_of(pair)
        covered.update(frags)
        target = pair.get("day")
        div = float(pair.get("divisor") or 1)
        tol = abs(float(pair.get("tolerance") or 0))
        per_day = {}
        for m in frags:
            for d, vals in (hour.get(m) or {}).items():
                per_day.setdefault(d, []).extend(vals)
        diffs = []
        for d, vals in sorted(per_day.items()):
            got = fragments.aggregate(vals, pair.get("agg"))
            if got is None:
                continue
            got = got / div
            want = (day.get(target) or {}).get(d)
            if want is None:
                diffs.append((d, got, None, None))
                continue
            diffs.append((d, got, want, got - want))
        compared = [x for x in diffs if x[2] is not None]
        bad = [x for x in compared if abs(x[3]) > tol]
        known = {e.get("date"): e.get("why") for e in (pair.get("known_exceptions") or [])}
        unexpected = [x for x in bad if x[0] not in known]
        if not compared:
            verdict = "无数据"
        elif not bad:
            verdict = "恒等"
        elif not unexpected:
            verdict = "条件恒等"
        else:
            verdict = "不恒等"
        out.append({
            "fragment": frags, "day": target, "agg": pair.get("agg"),
            "divisor": div, "tolerance": tol, "whitelist": bool(pair.get("whitelist")),
            "days": len(compared), "unequal": len(bad),
            "unexpected": len(unexpected),
            "max_abs_diff": round(max((abs(x[3]) for x in compared), default=0), 4),
            "missing_day": sum(1 for x in diffs if x[2] is None),
            "verdict": verdict,
            "worst": sorted(unexpected or bad, key=lambda x: -abs(x[3]))[:3],
            "known": [{"date": d, "why": known[d]} for d, _g, _w, _v in bad
                      if d in known],
            "note": pair.get("note"),
        })
    for m in sorted(set(hour) - covered):
        out.append({"fragment": [m], "day": None, "agg": None, "divisor": 1,
                    "tolerance": None, "whitelist": False, "days": len(hour[m]),
                    "unequal": None, "max_abs_diff": None, "missing_day": None,
                    "verdict": "无日级对照",
                    "note": (cfg.get("no_counterpart") or {}).get(
                        m, "**未登记理由**：请在 references/fragments.json 的 no_counterpart 里写明为什么没有日级对照"),
                    "worst": []})
    return out


def main():
    ap = argparse.ArgumentParser(description="片段一致性：片段回推 vs 日汇总")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows = analyse()
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        print("%-26s %-16s %-8s %-4s %-5s %-9s %s"
              % ("片段", "日级对照", "结论", "天数", "不等", "最大差", "说明"))
        for r in rows:
            print("%-26s %-16s %-8s %-4s %-5s %-9s %s" % (
                ",".join(r["fragment"])[:26], r["day"] or "—", r["verdict"],
                r["days"], "—" if r["unequal"] is None else r["unequal"],
                "—" if r["max_abs_diff"] is None else r["max_abs_diff"],
                (r["note"] or "")[:60]))
            for d, got, want, dv in r["worst"]:
                print("      %s 片段 %.3f / 日值 %.3f（Δ %+.3f）"
                      % (d, got, want, dv))
            for k in r.get("known") or []:
                print("      %s 已登记例外：%s" % (k["date"], k["why"]))
        bad = [r for r in rows if r["whitelist"] and r["unexpected"]]
        unexplained = [r for r in rows if "未登记理由" in (r["note"] or "")]
        drift = [r for r in rows if not r["whitelist"] and r["verdict"] == "不恒等"]
        print("\n白名单（可切）%d 项；条件恒等 %d 项；未登记异常 %d 项；"
              "非白名单不恒等 %d 项（已知口径差异，日值仍由日汇总裁决）；未登记理由 %d 项"
              % (sum(1 for r in rows if r["whitelist"]),
                 sum(1 for r in rows if r["whitelist"] and r["verdict"] == "条件恒等"),
                 len(bad), len(drift), len(unexplained)))
        if bad:
            print("✗ 白名单指标出现**未登记**的不恒等——这是缺陷（2c 的前提被破坏），"
                  "要么修切法，要么在 references/fragments.json 里登记原因")
        if unexplained:
            print("✗ 有片段指标没写「为什么没有日级对照」——每个指标都必须有结论")
    bad = [r for r in rows if r["whitelist"] and r["unexpected"]]
    unexplained = [r for r in rows if "未登记理由" in (r["note"] or "")]
    return 1 if (bad or unexplained) else 0


if __name__ == "__main__":
    sys.exit(main())
