# -*- coding: utf-8 -*-
"""窗口结论 → 主页索引（`index.json.conclusions`）。

## 它补的是什么

指标卡回答「这个单元的值是多少」，判读区回答「这一天怎么样」。中间缺一句
「这一段是什么形状」：同样 26 天久坐超标，落在 4 段里（结构性）和散在 26 段里
（偶发）是两种生活，卡片和日历都分不出来。

数据来自 `conclusions.py`，**本模块只搬运与压缩**，不重算任何判定。

## 为什么不是每个锚点都算

滚动窗的 key 是「窗口末日」，有数据就有 60 多个 key。每个 key 21 条结论 ≈ 1.5KB，
三个滚动窗就是 200KB+，而 `index.json` 是首屏唯一载荷（现 350KB）。

所以：**周/月按单元全量算（9+3 个），滚动窗只算最新那一个**。翻到历史滚动窗时
前端会明说「结论只算最新窗口」——历史那一段看日历与判读流水，那本来也是
它们该回答的问题（点日历格会自动切回日尺度）。
"""
import datetime

from homepage.paths import SCRIPTS

import sys as _sys
if SCRIPTS not in _sys.path:
    _sys.path.insert(0, SCRIPTS)

import db          # noqa: E402
import units       # noqa: E402
import conclusions as conc  # noqa: E402

#: 断言维度 -> 页面上的信息维度分组（与 metricgrid.js 的 GROUPS 同一套轴）
DIM_GROUP = {
    "sleep": "恢复", "autonomic": "恢复", "stress_energy": "恢复", "capacity": "恢复",
    "activity": "活动", "load": "活动",
}


def _slim(r):
    """单条结论 -> 页面需要的字段。**判定字段一个不改，只压名字。**

    `s` 状态、`w` 措辞、`h/n` 命中/可判定、`e` 段数、`l` 最长段、`gr` 信息维度。
    `iv/iu` 累计超出量与单位、`t` 趋势一句话——只在有值时出现，缺字段前端不画那一格。
    """
    o = {"id": r["id"], "gr": DIM_GROUP.get(r.get("dimension"), "其他"),
         "op": r.get("operator"),
         "s": r["state"], "w": r["word"], "h": r["hit"], "n": r["decidable"],
         "e": r["episodes"], "l": r["longest_run"]}
    ig = r.get("integral") or {}
    if ig.get("total"):
        o["iv"] = ig["total"]
        o["iu"] = ig.get("unit")
    tr = r.get("trend") or {}
    if tr.get("state") == "可判" and tr.get("total") is not None:
        o["t"] = "%s %+.1f" % (tr.get("direction") or "", tr["total"])
    return o


def block(rules=None, roll_scale="latest"):
    """产出 `{scale: {key: {w, na, rows}}}`。

    `na` = 该单元里因样本不足没给措辞的条数。它必须单独说一句：
    「这一段有 4 项判不了」和「这一段只有 17 项结论」是两件事。
    """
    rules = rules or db.load_rules()
    try:
        con = db.connect()
        ctx = units.context(con, rules, db.load_metrics())
        if ctx is None:
            return {"_error": "obs 表为空"}
        latest = min(ctx["last"], ctx["today"])
        specs = units.unit_specs(ctx["first"], ctx["last"], ctx["today"], rules)
        out = {}
        for sp in specs:
            sc = sp["scale"]
            if sc == "day":
                # 日尺度不需要：那一天的判断已经在判读区（⑤），
                # 再算一遍只是把同一条断言换个措辞复述。
                continue
            if sc.startswith("roll") and roll_scale != "all" and sp["key"] != latest:
                continue
            # 周/月的 dates 是整周/整月，截到数据范围内——项目开始前与今天之后
            # 不是「漏采」，不该进分母（与 window_spec 同一条纪律）。
            dates = [d for d in sp["dates"] if ctx["first"] <= d <= latest]
            if not dates:
                continue
            spec = units.window_spec(dates[0], dates[-1], ctx, rules,
                                     dates=dates, key=sp["key"])
            rows = conc.evaluate_window(con, spec, rules=rules, ctx=ctx)
            na = sum(1 for r in rows if r["state"] == "不可判定")
            out.setdefault(sc, {})[sp["key"]] = {
                "w": {"s": dates[0], "e": dates[-1], "n": len(dates),
                      "r": spec.get("ruler")},
                "na": na,
                "rows": [_slim(r) for r in rows if r["state"] != "不可判定"],
            }
        con.close()
        # 最小样本门跟着块走：前端那句「够不上 N 天」的数字必须来自口径文件，
        # 不能在 js 里再写一个 5（改门槛时两处必漂）。
        out["_min_n"] = int((rules.get("query") or {}).get("min_n_selected") or 0)
        return out
    except Exception as e:                      # 派生层缺失不阻断主页构建
        return {"_error": "%s: %s" % (type(e).__name__, str(e)[:100])}


def size_report(blk):
    """构建日志用：每个尺度多少单元、多少条结论、多少字节。"""
    import json
    out = []
    for sc, keys in sorted(blk.items()):
        if sc.startswith("_"):
            continue
        rows = sum(len(v["rows"]) for v in keys.values())
        out.append("%s %d 单元/%d 条" % (sc, len(keys), rows))
    b = len(json.dumps(blk, ensure_ascii=False).encode())
    return "，".join(out) + "（%.1fKB）" % (b / 1024.0)
