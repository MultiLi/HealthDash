#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""指标定义的唯一权威解析入口（2026-10-25 建，v3）。

## 为什么要有这个模块

`references/rules.json` 一直是**阈值数值**的唯一来源，但「某个指标的达标线是哪条规则、
怎么换算单位、落在哪个区间算好」这套**定义**从来没有唯一来源：它被写成了 8 份平行实现——

    units.py::_goal()          metric → rules 路径的硬编码字典
    assertions.py             直接读 activity.steps_good / sleep.min_healthy_duration_min…
    daily_brief.py            又读一遍
    homepage/indexlayer.py    又读一遍（下发给前端的 goals 块）
    homepage/summary.py       又读一遍
    homepage/scoring.py       又读一遍
    site/js/constants.js      METRICS[] 表：自带 label/unit/dir/agg/goal()/band()/fmt()
    site/js/aggregate.js      CHECKS 达标判定再写一遍

数值因为都读同一个 rules.json 所以一致，但**映射**不一致：`steps_daily` 的达标线在
units 里是 `steps_goal`(10000)，在 assertions 里是 `steps_good`(8000)；前端 key 叫 `steps`，
后端叫 `steps_daily`。改一次阈值要改 8 处、漏一处就是静默漂移。

## 本模块做的事

把「定义」收进 `references/metrics.json`，用**声明式表达式**引用 rules.json，
本模块是唯一的解析器：

    "thresholds": {
      "goal":   "activity.steps_goal",          # 单元达标（展示语义）
      "judge":  "activity.steps_good",          # 判读线（报告判黄）
      "severe": "activity.steps_low"            # 红线
    }
    "band": "sleep.deep_pct_optimal"            # 区间型；"@personal" = 用设备给的个人基线带
    "delta": ...                                # 差值型判据（如静息心率抬升）

表达式只允许 数字 / 点号路径 / + - * / 与括号，用 ast 白名单求值，不 eval 任意代码。

**解析失败必须响**：以前 `_goal()` 取不到键就返回 None，指标静默失去达标判定；
现在缺路径直接抛异常，`index.py verify` 会把每条断掉的引用列出来。

## 使用

    from metricdefs import goal_of, band_of, judge_of, resolve_all, validate
    goal_of("sleep_h", rules)        # 7.0
    band_of("hrv_ms", rules, personal=(57, 80))   # (57, 80)
    validate(rules)                  # [(metric, field, problem), ...] 空 = 全通
"""
import ast
import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
METRICS_PATH = os.path.join(_HERE, "..", "references", "metrics.json")

_CACHE = {"raw": None, "mtime": None}


# ------------------------------------------------------------------ 载入
def registry(path=None):
    p = path or METRICS_PATH
    mtime = os.path.getmtime(p)
    if _CACHE["raw"] is None or _CACHE["mtime"] != mtime or path:
        with open(p, encoding="utf-8") as fh:
            _CACHE["raw"] = json.load(fh).get("metrics") or {}
        _CACHE["mtime"] = mtime
    return _CACHE["raw"]


def meta(metric):
    m = registry().get(metric)
    if m is None:
        raise KeyError("未登记的指标：%s（先写进 references/metrics.json）" % metric)
    return m


# ------------------------------------------------------------------ 表达式
_ALLOWED = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
            ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b}
_FUNCS = {"max": max, "min": min}

# `ref:sleep_need_h` 不是合法 Python 标识符，先换成 __ref_sleep_need_h__ 再解析。
# 这是「阈值可以来自信源给的动态参考量」的唯一语法入口。
_REF = re.compile(r"ref:([A-Za-z_][A-Za-z0-9_]*)")


def _dotted(node):
    """ast 节点 → 点号路径（a.b.c）。不是纯名字链就返回 None。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return (base + "." + node.attr) if base else None
    return None


def path(rules, dotted):
    """rules 里的点号路径取值。**取不到就抛**——静默 None 是漂移的温床。"""
    node = rules
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError("rules.json 缺少路径：%s（断在 %s）" % (dotted, part))
        node = node[part]
    return node


def _eval(node, rules, refs=None):
    d = _dotted(node)
    if d is not None:
        if d.startswith("__ref_") and d.endswith("__"):
            name = d[6:-2]
            if not refs or refs.get(name) is None:
                raise KeyError("参考量缺失：ref:%s（该日/该单元没有这个信源值）" % name)
            return refs[name]
        return path(rules, d)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Call):
        fn = _dotted(node.func)
        if fn not in _FUNCS or node.keywords:
            raise ValueError("只允许 max/min 两个函数：%s" % fn)
        return _FUNCS[fn](*[_eval(a, rules, refs) for a in node.args])
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED:
        return _ALLOWED[type(node.op)](_eval(node.left, rules, refs),
                                       _eval(node.right, rules, refs))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval(node.operand, rules, refs)
    raise ValueError("不支持的表达式节点：%s" % type(node).__name__)


def refs_in(expr):
    """表达式里引用了哪些 ref（静态扫描，供校验与文档用）。"""
    return _REF.findall(str(expr or ""))


def evaluate(expr, rules, refs=None):
    """求值一个声明式表达式（字符串）或直接返回数值。

    refs = {"sleep_need_h": 7.5, ...}：信源给的动态参考量。缺失会抛 KeyError，
    由调用方决定是退回 fallback 还是报错——**不允许静默用 0 或默认值**。
    """
    if expr is None:
        return None
    if isinstance(expr, (int, float)):
        return expr
    text = _REF.sub(lambda m: "__ref_%s__" % m.group(1), str(expr))
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as e:
        raise ValueError("表达式语法错误：%r（%s）" % (expr, e))
    return _eval(tree.body, rules, refs)


# ------------------------------------------------------------------ 取值
def threshold(metric, field, rules, refs=None):
    """metric 的某条阈值（goal / judge / severe / delta）。未声明返回 None。

    `refs` 给了就优先用信源的动态值；表达式里 ref 取不到时，退回同名的
    `<field>_fallback`（若声明）——**退路必须写在注册表里**，不在代码里。
    """
    t = meta(metric).get("thresholds") or {}
    expr = t.get(field)
    fb = t.get(field + "_fallback")
    if expr is None:
        return evaluate(fb, rules, None) if fb else None
    try:
        return evaluate(expr, rules, refs)
    except Exception:
        if fb:
            return evaluate(fb, rules, None)
        raise


def goal_of(metric, rules, refs=None):
    return threshold(metric, "goal", rules, refs)


def judge_of(metric, rules, refs=None):
    """判读线；未单独声明时退回 goal（简单指标两者同值）。"""
    v = threshold(metric, "judge", rules, refs)
    return v if v is not None else goal_of(metric, rules, refs)


def severe_of(metric, rules, refs=None):
    return threshold(metric, "severe", rules, refs)


def delta_threshold(metric, rules, refs=None):
    return threshold(metric, "delta", rules, refs)


def judge_time_of(metric, rules, refs=None):
    """时刻型判读线 → 分钟数。

    优先用可计算的 judge（可含 ref，如「设备推荐窗止 + 45 分」）；
    ref 缺失或未声明时退回 judge_time 的 HH:MM（静态 01:30）。
    """
    try:
        v = threshold(metric, "judge", rules, refs)
        if v is not None:
            return float(v)
    except Exception:
        pass
    return threshold_time(metric, rules)


def threshold_time(metric, rules, field="judge_time"):
    """时刻型判据（HH:MM 字符串）→ 分钟数。用于「入睡不得晚于 01:30」这类定义。"""
    t = meta(metric).get("thresholds") or {}
    spec = t.get(field)
    if spec is None:
        return None
    raw = evaluate(spec, rules)
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        h, m = str(raw).split(":")
        return int(h) * 60 + int(m)
    except Exception:
        raise ValueError("时刻格式应为 HH:MM：%r" % (raw,))


def band_of(metric, rules, personal=None):
    """区间型指标的 [lo, hi]。

    `@personal` = 用调用方给的设备个人带（如 HRV）。个人带缺失时退到声明的
    `band_fallback`（也要写进注册表）——退路必须显式，不许在代码里藏一个默认值。
    """
    md = meta(metric)
    spec = md.get("band")
    if spec is None:
        return None
    if spec == "@personal":
        if personal:
            return tuple(personal)
        fb = md.get("band_fallback")
        if not fb:
            return None
        v = path(rules, fb)
        return tuple(v) if isinstance(v, (list, tuple)) and len(v) == 2 else None
    v = path(rules, spec)
    return tuple(v) if isinstance(v, (list, tuple)) and len(v) == 2 else None


def judged(metric):
    """该指标是否有达标方向（dir 非空）。"""
    return meta(metric).get("dir")


# ------------------------------------------------------------------ 校验
def validate(rules):
    """全量校验：表达式能否解析、路径是否存在、区间是否有序。

    返回 [(metric, field, problem)]；空列表 = 全通。
    这是 `index.py verify` 的一部分——**改 rules.json 键名会立刻在这里报错**。
    """
    bad = []
    for m, d in registry().items():
        for field in ("goal", "judge", "severe", "delta", "judge_time"):
            expr = (d.get("thresholds") or {}).get(field)
            if expr is None:
                continue
            try:
                rs = refs_in(expr)
                for rname in rs:
                    if rname not in registry():
                        bad.append((m, field, "引用了未登记的参考量 ref:%s" % rname))
                if field == "judge_time":
                    threshold_time(m, rules)      # 时刻型：只要解析得动就算通
                elif rs:
                    # 含动态参考量：此刻无 refs 必然算不出来，只验语法
                    evaluate(expr, rules, {k: 1.0 for k in rs})
                else:
                    v = evaluate(expr, rules)
                    if not isinstance(v, (int, float)):
                        bad.append((m, field, "结果不是数值：%r" % (v,)))
            except Exception as e:
                bad.append((m, field, str(e)))
        for fb_key in ("goal_fallback", "judge_fallback", "severe_fallback"):
            fb = (d.get("thresholds") or {}).get(fb_key)
            if fb is None:
                continue
            try:
                v = evaluate(fb, rules)
                if not isinstance(v, (int, float)):
                    bad.append((m, fb_key, "退路不是数值：%r" % (v,)))
            except Exception as e:
                bad.append((m, fb_key, "退路解析失败：%s" % e))
        if d.get("dir") in ("above", "below") and not (
                (d.get("thresholds") or {}).get("goal")
                or (d.get("thresholds") or {}).get("judge")):
            bad.append((m, "goal", "有达标方向却没有声明任何阈值"))
        if d.get("dir") == "band" and not d.get("band"):
            bad.append((m, "band", "dir=band 但没有声明区间来源"))
        if d.get("band") and d["band"] != "@personal":
            try:
                b = path(rules, d["band"])
                if not (isinstance(b, list) and len(b) == 2 and b[0] <= b[1]):
                    bad.append((m, "band", "区间不是 [lo, hi] 或 lo>hi：%r" % (b,)))
            except Exception as e:
                bad.append((m, "band", str(e)))
    return bad


# ------------------------------------------------------------------ 下发
def resolve_all(rules):
    """解析后的完整定义——供主页下发（前端不再自带一份 METRICS 表）。"""
    out = {}
    for m, d in registry().items():
        if not d.get("agg"):
            continue                      # 不进单元的参考量不下发
        item = {
            "key": m, "label": d.get("label"), "unit": d.get("unit"),
            "dir": d.get("dir"), "agg": d.get("agg"),
            "precision": d.get("precision"),
            "check": bool(d.get("check")),
            "frontend_key": d.get("frontend_key"),
            "goal": None, "band": None,
        }
        try:
            item["goal"] = goal_of(m, rules) if d.get("dir") in ("above", "below") else None
            if d.get("dir") == "band" and d.get("band"):
                # "@personal" 原样下发：个人基线带随数据变，前端从 baselines 取，
                # 不能在这里写死成某个具体区间（那会把上周的带当成永久标准）。
                item["band"] = ("@personal" if d["band"] == "@personal"
                                else list(band_of(m, rules)))
        except Exception as e:
            item["error"] = str(e)
        out[m] = item
    return out


def snapshot(rules):
    """可读快照：一条指标一行，用于人工 review 与文档。"""
    rows = []
    for m, d in sorted(registry().items()):
        th = d.get("thresholds") or {}
        rows.append({
            "metric": m, "dir": d.get("dir"), "agg": d.get("agg"),
            "goal": _safe(goal_of, m, rules), "judge": _safe(judge_of, m, rules),
            "severe": _safe(lambda x, r: threshold(x, "severe", r), m, rules),
            "band": d.get("band"),
            "resolved_band": _safe(band_of, m, rules),
            "check": bool(d.get("check")), "frontend_key": d.get("frontend_key"),
            "declared": th,
        })
    return rows


def _safe(fn, *a):
    try:
        v = fn(*a)
        return round(v, 4) if isinstance(v, float) else v
    except Exception as e:
        return "ERR:%s" % e


if __name__ == "__main__":
    import argparse
    import sys
    sys.path.insert(0, _HERE)
    import db

    ap = argparse.ArgumentParser(description="指标定义：唯一权威解析入口")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list", help="列出全部已登记指标与解析后的阈值")
    p.add_argument("--json", action="store_true")
    p.set_defaults(cmd="list")
    p = sub.add_parser("validate", help="校验全部声明（改完 rules.json 必跑）")
    p.set_defaults(cmd="validate")
    p = sub.add_parser("resolve", help="解析单个指标")
    p.add_argument("metric")
    p.set_defaults(cmd="resolve")
    a = ap.parse_args()
    rules = db.load_rules()

    if a.cmd == "validate":
        bad = validate(rules)
        for m, f, p_ in bad:
            print("✗ %-22s %-8s %s" % (m, f, p_))
        print("%s（%d 条问题）" % ("全部通过" if not bad else "有断链", len(bad)))
        sys.exit(0 if not bad else 1)

    if a.cmd == "resolve":
        d = meta(a.metric)
        print(json.dumps({
            "metric": a.metric, "label": d.get("label"), "unit": d.get("unit"),
            "dir": d.get("dir"), "agg": d.get("agg"),
            "goal": _safe(goal_of, a.metric, rules),
            "judge": _safe(judge_of, a.metric, rules),
            "band": _safe(band_of, a.metric, rules),
            "declared": d.get("thresholds")}, ensure_ascii=False, indent=1))
        sys.exit(0)

    rows = snapshot(rules)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        print("%-18s %-6s %-7s %8s %8s %8s  %-22s %s"
              % ("指标", "dir", "agg", "goal", "judge", "severe", "band", "前端 key"))
        for r in rows:
            print("%-18s %-6s %-7s %8s %8s %8s  %-22s %s"
                  % (r["metric"], r["dir"] or "-", r["agg"] or "-",
                     r["goal"], r["judge"], r["severe"], r["band"] or "-",
                     r["frontend_key"] or ""))
