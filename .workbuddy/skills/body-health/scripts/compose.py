#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L4 骨架编排：把断言直渲成一份**数字全对、措辞生硬**的报告（2026-10-25 建）。

**这个文件存在的唯一理由：报告不能依赖 LLM 可用性。**

以前「LLM 挂了 = 没有报告」。现在两条路都通：
  · `--write`：不调 LLM，直接出终版。措辞平铺直叙、没有机制归因，但每个数字都对，
    且与主页、与断言表完全一致（因为三者读同一份 assertions）。
  · 默认（不带 --write）：把骨架打到 stdout，作为 LLM 的**输入骨架**。LLM 的活是
    在这一骨架上补「为什么」，而不是从散数字重新推一遍判定。

三条硬约束：
1. **判定不由本文件产生**。level 一律取 assertion.level，本文件只排版。
   想改判定就去改 rules.json / metrics.json，改完重跑 index.py。
2. **数字只来自 assertions.evidence 与 units.value**。骨架里出现的任何数值都能溯源，
   所以 `verify_judgment.py` 能把 LLM 版报告按同一套规则校验。
3. **缺词表就报错**。lexicon.json 里没登记的断言 id / evidence 键 → 直接抛错而不是
   打个拼音凑合。措辞漂移（同一件事三十份报告三十种说法）是跨报告统计的头号杀手。

用法：
    python3 compose.py --date 2026-10-24                  # 打印骨架（喂 LLM / 人看）
    python3 compose.py --date 2026-10-24 --write          # 写 .skeleton.md（旁路文件，不覆盖任何东西）
    python3 compose.py --date 2026-10-24 --as-final       # 顶替正式报告（LLM 不可用时的兜底）

**写路径刻意分两级**（2026-10-25 事故后加）：`--write` 只写
`reports/body_brief_<date>.skeleton.md`，永远不碰正式报告；顶替正式报告要显式
`--as-final`，且已存在时还要 `--force`。原因是当天我用一条命令把 10-24 的 LLM 报告
覆盖成了骨架版，只能从会话转录里捞回来——「兜底脚本」不该有随手覆盖终版权限。
"""
import argparse
import datetime
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
_WORKSPACE = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))

import db                                                    # noqa: E402
import assertions as asrt                                    # noqa: E402
import metricdefs                                            # noqa: E402

REPORTS = os.environ.get("HEALTH_REPORTS_DIR") or os.path.join(_WORKSPACE, "reports")
LEXICON = os.path.join(_HERE, "..", "references", "lexicon.json")
WEEKDAY = "一二三四五六日"

# 「时刻」型键（分钟制）——渲染成 HH:MM。这些键名里带 bed_/window/cutoff，
# 语义上就是钟点；时长型的分钟（久坐 480、阈值 480）走 limit_min，不在此列。
# 2026-10-25 曾因 threshold_min 一个键两种语义（睡眠=钟点、久坐=时长）把
# 「久坐阈值 480 min」渲染成「08:00」，故把语义写进键名。
_MINUTE_KEYS = ("bed_min", "bed_threshold_min", "bed_severe_min",
                "device_window_end", "cutoff_min", "complete_cutoff_min")
# 单位由键名自带的（与指标无关）：h 结尾的是小时，offset 是分钟
_KEY_UNIT = {"hours": "h", "floor_h": "h", "severe_h": "h", "threshold_h": "h",
             "device_need_h": "h", "need_h": "h", "baseline_h": "h",
             "delta_h": "h", "sleep_h": "h", "limit_min": "min",
             "offset_from_window_min": "min", "offset_min": "min",
             "coverage_min": "%", "mdc": "ms",
             # 计「天/晚/个」的键：单位与 subject 无关，别跟着指标变成 h 或 bpm
             "streak": "天", "short_streak": "晚", "min_nights": "晚",
             "min_days": "天", "windows": "个",
             # 分档/评分类：数字本身已是语义单位，不挂后缀
             "score": "", "readiness": "", "line": "", "level": "",
             "readiness_level": ""}
# 渲染顺序：结论侧（实测/阈值）在前，辅助（门槛/尺子/窗口）在后
_KEY_ORDER = ("value", "hours", "sleep_h", "steps", "sedentary_min", "intensity_min",
              "z2_min", "rhr", "hrv", "avg7", "score", "readiness", "stress_avg",
              "acwr", "deep_pct", "rem_pct", "bb_low", "bb_net", "streak",
              "threshold", "threshold_h", "threshold_min", "floor_h", "severe_h",
              "goal", "good", "good_max", "low", "line", "critical", "under",
              "optimal", "band", "band_target", "mdef_min",
              "delta", "delta_h", "below", "below_by", "beyond_mdc_single",
              "mdc", "mdc_single", "prev", "prev_hrv",
              "bed_min", "device_window_end", "offset_from_window_min", "offset_min",
              "window", "device_need_h", "need_h", "need", "baseline_h",
              "short_streak", "windows", "effective_min",
              "ruler", "ref", "affects", "fallback", "scale", "key", "coverage_min")


def load_lexicon():
    d = json.load(open(LEXICON, encoding="utf-8"))
    for sec in ("level", "level_plain", "dimension", "id", "field"):
        if sec not in d:
            raise SystemExit("lexicon.json 缺 %s 节" % sec)
    return d


def _fmt_min(m):
    try:
        m = int(round(float(m)))
    except Exception:
        return str(m)
    # 时刻型允许 >1440（次日），按 24h 取模显示并标注次日
    if m >= 1440:
        return "%02d:%02d(+1)" % ((m % 1440) // 60, m % 60)
    return "%02d:%02d" % (m // 60, m % 60)


def _num(v):
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if abs(v - round(v)) < 1e-9:
            return str(int(round(v)))
        return ("%.2f" % v).rstrip("0").rstrip(".")
    return str(v)


def fmt_evidence(ev, lex, subject=None, drop=("scale", "key")):
    """evidence → 一行中文。键缺词表**直接抛错**（措辞漂移的源头就在这里）。

    `subject` 是断言的指标名：value / delta / threshold / band 这类键的单位
    **随指标变**（HRV 是 ms、RHR 是 bpm、步数是步），所以单位从注册表取，
    不在这张表里写死——否则改一次指标单位就要改两处。
    """
    munit = (metricdefs.registry().get(subject) or {}).get("unit") if subject else None
    parts = []
    seen = set()
    keys = [k for k in _KEY_ORDER if k in ev] + \
           [k for k in ev if k not in _KEY_ORDER]
    for k in keys:
        if k in drop or k in seen:
            continue
        seen.add(k)
        v = ev[k]
        if v is None:
            continue
        label = lex["field"].get(k)
        if label is None:
            raise KeyError("lexicon.json 的 field 节没有 '{}'（断言证据键必须登记中文名）".format(k))
        if isinstance(v, list):
            if not v:
                continue
            if len(v) > 4:                     # 长列表只报个数，避免把报告撑爆
                parts.append("%s %d 项" % (label, len(v)))
            else:
                if k == "window" and all(isinstance(x, (int, float)) for x in v):
                    parts.append("%s %s" % (label, "–".join(_fmt_min(x) for x in v)))
                else:
                    parts.append("%s %s" % (label, "/".join(_num(x) for x in v)))
            continue
        if k in _MINUTE_KEYS:
            parts.append("%s %s" % (label, _fmt_min(v)))
            continue
        if k == "ruler":
            parts.append("尺子 %s" % {"device": "设备值", "static_fallback": "静态兜底",
                                     "partial_ref": "部分设备值"}.get(str(v), v))
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            u = _KEY_UNIT.get(k)
            if u is None:
                u = munit or ""
            if k == "coverage_min" and u == "%":
                v = round(v * 100)
            if u == "%":                       # 百分号贴数字，不空格
                parts.append("%s %s%%" % (label, _num(v)))
            elif u == "":
                parts.append("%s %s" % (label, _num(v)))
            else:
                parts.append("%s %s %s" % (label, _num(v), u))
        else:
            parts.append("%s %s" % (label, _num(v) if not isinstance(v, str) else v))
    return "、".join(parts)


def _title(a, lex):
    t = lex["id"].get(a["id"])
    if t is None:
        raise KeyError("lexicon.json 的 id 节没有 '{}'（新增断言必须登记中文标题）".format(a["id"]))
    return t


def _sections(snap):
    """l1 按维度分组（保持 lexicon 的维度顺序）。"""
    order = list(load_lexicon()["dimension"].keys())
    out = {}
    for a in snap["l1"]:
        out.setdefault(a["dimension"], []).append(a)
    return {k: out[k] for k in order if k in out}


def _units_brief(con, date, lex):
    """尺度摘要：本日 / 本周 / 本月 / 近 7·28·90 天，取 headline 指标。"""
    rules = db.load_rules()
    d = datetime.date.fromisoformat(date)
    wk = "%04d-W%02d" % d.isocalendar()[:2]
    keys = [("day", date), ("week", wk), ("month", date[:7]),
            ("roll7", date), ("roll28", date), ("roll90", date)]
    metrics = lex.get("headline_metrics") or []
    out = []
    for sc, key in keys:
        rows = {}
        for r in con.execute(
                "SELECT metric, value, n, coverage, judge, judge_rate, "
                "delta_v, delta_mdc, delta_verdict, delta_vs "
                "FROM units WHERE scale=? AND key=?", (sc, key)):
            if r["metric"] in metrics:
                rows[r["metric"]] = dict(r)
        if rows:
            out.append({"scale": sc, "key": key, "metrics": rows})
    return out, rules


def _dates_for_scale(sc, key):
    """尺度 → 起止（只用于标题，不参与计算）。"""
    if sc == "day":
        return key, key
    if sc == "week":
        y, w = key.split("-W")
        monday = datetime.date.fromisocalendar(int(y), int(w), 1)
        return monday.isoformat(), (monday + datetime.timedelta(days=6)).isoformat()
    if sc == "month":
        first = key + "-01"
        dd = datetime.date.fromisoformat(first)
        nxt = (dd.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        return first, (nxt - datetime.timedelta(days=1)).isoformat()
    if sc.startswith("roll"):
        n = int(sc[4:])
        return (datetime.date.fromisoformat(key)
                - datetime.timedelta(days=n - 1)).isoformat(), key
    return key, key


SCALE_LABEL = {"day": "当日", "week": "本周", "month": "本月",
               "roll7": "近 7 天", "roll28": "近 28 天", "roll90": "近 90 天"}


def _summary_line(snap, lex):
    """总评句：只陈述计数与最差维度，**不做归因**（归因是 LLM 的活）。"""
    r = snap["rollup"]
    counts = snap.get("counts") or {}
    bits = []
    for lv in ("BAD", "WARN", "NEUTRAL"):
        if counts.get(lv):
            bits.append("%s %d 条" % (lex["level_plain"].get(lv, lv), counts[lv]))
    dims = r.get("dimensions") or {}
    worst_line = ""
    if dims:
        sev = {lv: asrt.SEVERITY.get(lv, 9) for lv in ("BAD", "WARN", "GOOD")}
        dim_worst = sorted(
            ((lex["dimension"].get(k, k), min(sev.get(lv, 9) for lv in v)) for k, v in dims.items()),
            key=lambda x: x[1])
        top = [n for n, s in dim_worst if s == dim_worst[0][1]]
        if top:
            worst_line = "；最差维度：%s" % "、".join(top)
    cross_n = len(snap.get("cross") or [])
    tail = "；交叉规则触发 %d 条" % cross_n if cross_n else ""
    return "总评 **%s**。触发 %s%s%s。" % (
        lex["level"].get(r.get("level"), r.get("label")),
        "、".join(bits) if bits else "无判定项", worst_line, tail)


def render(date, facts, snap, units, lex, rules):
    """骨架 md。每一行数字都能溯源到 facts / snap / units。"""
    d = datetime.date.fromisoformat(date)
    comp = (facts or {}).get("completeness") or {}
    roll = snap["rollup"]
    L = []
    A = L.append

    A("# %s 身体简报（周%s）" % (date, WEEKDAY[d.weekday()]))
    A("")
    # —— 数据边界：只认闸门 ——
    cut = comp.get("cutoff")
    lvl = comp.get("level")
    if lvl == "complete":
        A("> **数据截止 %s（完整日）**：`wellnessEndTimeLocal` 已到次日 00:00，闸门判 `complete`、`gaps` 空。" % (cut or "24:00"))
    elif lvl == "no_data":
        A("> **数据截止：无数据**。当日无佩戴/同步记录（空占位响应），本份不给红黄绿。")
    else:
        A("> **数据截止 %s · %s**：缺口 %s。%s" % (
            cut or "-", "截断" if lvl == "partial" else lvl,
            "、".join(comp.get("blocking") or []) or "-",
            "睡前判读不成立。" if lvl == "partial" else ""))
    fp = snap.get("fingerprint") or {}
    A("> **口径**：断言 %s 条（l1 %d / 交叉 %d / 设备 %d）；rules `%s` · engine `%s`%s" % (
        sum((snap.get("counts") or {}).values()), len(snap["l1"]), len(snap["cross"]),
        len(snap["device"]), (fp.get("rules_hash") or "")[:12],
        (fp.get("engine_ver") or "")[:12],
        " · ⚠ **物化层过期，本份未复算**" if snap.get("stale") else ""))
    A("")

    # —— 总评 ——
    A("## 总评：%s" % lex["level"].get(roll.get("level"), roll.get("label")))
    A("")
    A(_summary_line(snap, lex))
    A("")
    if (snap.get("counts") or {}).get("BAD"):
        A("> 红灯项（须逐条解释或按流程声明降档理由）：" +
          "、".join(_title(a, lex) for a in snap["l1"] if a["level"] == "BAD") +
          ("、" + "、".join(_title(a, lex) for a in snap["cross"] if a["level"] == "BAD")
           if any(a["level"] == "BAD" for a in snap["cross"]) else "") + "。")
        A("")

    # —— 交叉判读 ——
    A("## 交叉判读")
    A("")
    if not snap["cross"]:
        A("（当日无交叉规则触发。）")
    for i, a in enumerate(snap["cross"], 1):
        A("**%d.【%s】%s**" % (i, a["level"], _title(a, lex)))
        A("- 证据：%s" % fmt_evidence(a["evidence"], lex, a.get("subject")))
        A("- 规则：`%s`；近 %d 天第 %d 次触发" % (
            a["id"], snap.get("history_days", 30), a.get("n_hist") or 0))
        if a.get("action_hint"):
            A("- 提示：%s" % a["action_hint"])
        A("- 归因：`<待判读补写：机制在这条证据链的哪一环，用什么交叉验证，边界在哪>`")
        A("")

    # —— 指标明细（按维度）——
    A("## 指标明细")
    A("")
    A("| 维度 | 判定 | 级别 | 证据 | 置信 |")
    A("|---|---|---|---|---|")
    for dim, items in _sections(snap).items():
        for a in items:
            A("| %s | %s | %s | %s | %s |" % (
                lex["dimension"].get(dim, dim), _title(a, lex), a["level"],
                fmt_evidence(a["evidence"], lex, a.get("subject")),
                lex.get("confidence", {}).get(a.get("confidence") or "", "-")))
    A("")

    # —— 尺度摘要 ——
    A("## 尺度摘要")
    A("")
    A("| 尺度 | 区间 | " + " | ".join(_metric_label(m, lex) for m in
                                      (lex.get("headline_metrics") or [])) + " |")
    A("|---|---|" + "---|" * len(lex.get("headline_metrics") or []))
    for u in units:
        cells = []
        for m in (lex.get("headline_metrics") or []):
            r = u["metrics"].get(m)
            if not r or r.get("value") is None:
                cells.append("—")
                continue
            s = _fmt_value(m, r, lex)
            if r.get("judge") in ("ok", "no", "na"):
                s += {"ok": " ✓", "no": " ✗", "na": " (不可判)"}[r["judge"]]
            if r.get("judge_rate") is not None and u["scale"] != "day":
                s += " %d%%" % round(r["judge_rate"] * 100)
            dv = r.get("delta_verdict")
            if dv:
                mark = {"changed": "Δ过门槛", "within-noise": "Δ噪声内",
                        "insufficient": "Δ样本不足", "uncalibrated": "Δ无门槛",
                        "suppressed": "Δ不判"}.get(dv, "Δ?")
                cells.append("%s %s" % (s, mark))
            else:
                cells.append(s)
        a, b = _dates_for_scale(u["scale"], u["key"])
        A("| %s | %s~%s | %s |" % (SCALE_LABEL.get(u["scale"], u["scale"]),
                                   a[5:], b[5:], " | ".join(cells)))
    A("")

    # —— 数据缺口 ——
    A("## 数据缺口")
    A("")
    gaps = (facts or {}).get("gaps") or []
    data_items = [a for a in snap["l1"] if a["dimension"] == "data"]
    if not gaps and not data_items and not snap.get("unit_scope"):
        A("- 无。")
    for g in gaps:
        A("- %s" % g)
    comp2 = (facts or {}).get("computed") or {}
    if "subjective_today" in comp2 and comp2.get("subjective_today") is None:
        A("- **主观日志**：当日 `daily_log.jsonl` 无记录（判读不含主观补数）。")
    for a in data_items:
        A("- **%s**：%s" % (_title(a, lex), fmt_evidence(a["evidence"], lex, a.get("subject"))))
    scope = {}
    for u in snap.get("unit_scope") or []:
        scope.setdefault(u["scale"], []).append(u)
    for sc, items in scope.items():
        ev = items[0]["evidence"]
        A("- **%s 尺度覆盖不足**：%s 等 %d 个指标的覆盖低于 %s，该尺度只报数字、不给结论。" % (
            SCALE_LABEL.get(sc, sc),
            "、".join(metricdefs.registry().get(m, {}).get("label", m)
                     for m in (ev.get("below") or [])[:6]) or "-",
            len(ev.get("below") or []), _num(ev.get("coverage_min"))))
    A("")
    A("---")
    A("*本份由 `compose.py` 从断言直渲：数字全部来自 L3 断言证据与 L2 单元，"
      "未做机制归因。判读版须在此基础上补归因，且不得改变任何 level。*")
    return "\n".join(L)


def _metric_label(m, lex):
    md = metricdefs.registry().get(m) or {}
    return "%s(%s)" % (md.get("label", m), md.get("unit", ""))


def _fmt_value(m, r, lex):
    md = metricdefs.registry().get(m) or {}
    v = r["value"]
    prec = md.get("precision")
    if prec is not None:
        s = ("%." + str(int(prec)) + "f") % v
    else:
        s = _num(v)
    u = md.get("unit") or ""
    if m == "sedentary_min":
        return "%s min (%s)" % (int(round(v)), _fmt_min(v))
    # 表头已经带单位了，格内不再重复
    return s


def build_judgment(date, snap, lex):
    """judgment 骨架：findings[] 是新增的可统计产物，note 留空给判读填。"""
    cross = []
    for a in snap["cross"]:
        cross.append([a["level"], "%s：%s（id `%s`）" % (
            _title(a, lex), fmt_evidence(a["evidence"], lex, a.get("subject")), a["id"])])
    sections = {}
    for dim, items in _sections(snap).items():
        lv = "GOOD"
        for a in items:
            if asrt.SEVERITY.get(a["level"], 9) < asrt.SEVERITY.get(lv, 9):
                lv = a["level"]
        sections[lex["dimension"].get(dim, dim)] = {
            "level": lv,
            "marks": [[a["level"], "%s：%s" % (_title(a, lex),
                                               fmt_evidence(a["evidence"], lex, a.get("subject")))]
                      for a in items],
        }
    findings = []
    for a in snap["l1"] + snap["cross"]:
        if a["level"] not in ("BAD", "WARN"):
            continue
        findings.append({
            "id": a["id"], "level": a["level"], "dimension": a["dimension"],
            "evidence": a["evidence"], "n_hist": a.get("n_hist"),
            "note": None,       # ← 判读要填：一句话说明它意味着什么
        })
    return {
        "summary": "%s：%s" % (lex["level_plain"].get(snap["rollup"].get("level"), "不可判读"),
                               _summary_line(snap, lex)),
        "cross": cross,
        "sections": sections,
        "findings": findings,
        "meta": {"generated_by": "compose.py", "skeleton": True,
                 "rollup": snap["rollup"], "counts": snap.get("counts"),
                 "fingerprint": snap.get("fingerprint"), "stale": snap.get("stale")},
    }


def main():
    ap = argparse.ArgumentParser(description="从断言直渲报告骨架")
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--facts", default=None, help="facts 文件（默认 reports/facts_<date>.json）")
    ap.add_argument("--write", action="store_true",
                    help="写 .skeleton.md/.skeleton.json 旁路文件（不覆盖正式报告）")
    ap.add_argument("--as-final", dest="as_final", action="store_true",
                    help="顶替正式报告（LLM 不可用时的兜底；已存在需 --force）")
    ap.add_argument("--force", action="store_true", help="配 --as-final 时允许覆盖已有报告")
    ap.add_argument("--json", action="store_true", help="只输出 judgment 骨架")
    a = ap.parse_args()

    lex = load_lexicon()
    fpath = a.facts or os.path.join(REPORTS, "facts_%s.json" % a.date)
    facts = json.load(open(fpath, encoding="utf-8")) if os.path.isfile(fpath) else {}
    snap = facts.get("assertions")
    # facts 里的断言是**快照**，会过期（口径改了、重跑 index 了）。
    # 指纹不一致就以库为准并提示——否则会拿一份旧 evidence 渲染出旧口径的报告。
    con0 = db.connect()
    fresh = asrt.snapshot(con0, a.date)
    con0.close()
    if not snap or snap.get("error"):
        snap = fresh
        if not facts:
            print("⚠ %s 不存在，仅用断言渲染（无数据边界与 gaps）" % fpath, file=sys.stderr)
    elif (snap.get("fingerprint") or {}).get("engine_ver") != \
            (fresh.get("fingerprint") or {}).get("engine_ver"):
        print("⚠ facts 里的断言快照已过期（engine 指纹不同），改用当前库", file=sys.stderr)
        snap = fresh
    con = db.connect()
    units, rules = _units_brief(con, a.date, lex)
    con.close()

    jd = build_judgment(a.date, snap, lex)
    if a.json:
        print(json.dumps(jd, ensure_ascii=False, indent=1))
        return 0

    md = render(a.date, facts, snap, units, lex, rules)
    if not (a.write or a.as_final):
        print(md)
        return 0

    if a.as_final:
        md_path = os.path.join(REPORTS, "body_brief_%s.md" % a.date)
        j_path = os.path.join(REPORTS, "judgment_%s.json" % a.date)
        if os.path.isfile(md_path) and not a.force:
            print("✗ %s 已存在（单一终版纪律）。确认要顶替请加 --as-final --force"
                  % md_path, file=sys.stderr)
            return 2
    else:
        md_path = os.path.join(REPORTS, "body_brief_%s.skeleton.md" % a.date)
        j_path = os.path.join(REPORTS, "judgment_%s.skeleton.json" % a.date)
    os.makedirs(REPORTS, exist_ok=True)
    open(md_path, "w", encoding="utf-8").write(md + "\n")
    json.dump(jd, open(j_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("md:", md_path)
    print("judgment(骨架):", j_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
