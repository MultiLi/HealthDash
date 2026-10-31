#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定时采集（collect）—— 把「拉数据」与「出报告」彻底解耦。

一次 collect 做六件事，**不出报告、不做判读、不碰账本**：
  ① 拉目标日（今天）：d == date 时 sync 恒重拉，历史日已落盘自动跳过
  ② 回补窗口内「被截断」的历史日（data_gate 判 partial 的才碰，完整日不动）
  ③ 打印完整性一览（哪几天完整、哪几天还差多少分钟）
  ④ 重建物化层（index.py build：obs → units → assertions，只读本地 raw，不联网）
  ⑤ 可选：刷新主页数据（--push，默认开）
  ⑥ InBody：刷一次扫描 + 过体成分闸门（check_inbody，本地零网络），**成功也打一行**

为什么值得高频跑（2026-10-18 定）：
**提高拉数频率并不能「催出」数据** —— 云端没有就是没有，手表不同步，
拉一万次也是同一个截止时刻。它的真实价值是两件事：
  · **数据一落云就进我们的盘**：手表→手机同步发生的时刻不可预知
    （10-17 是 22:05，10-18 是 19:34），拉得勤，23:30 那次报告
    撞上「已经完整」的概率就高；
  · **截断日自动回补**：以前一天被截断就永远截断（raw 一旦落盘不再重拉），
    10-13~10-16 是被人工 --force 才补完整的。现在由 ② 自动做。

用法：
    python3 collect.py                # 默认：近 3 天，拉完顺手推主页
    python3 collect.py --days 5 --no-push
    python3 collect.py --json
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from settings import ROOT as _WORKSPACE  # noqa: E402  路径唯一解析点（settings.py）

import sync                                            # noqa: E402
import data_gate                                       # noqa: E402


def run(days=3, push=False, quiet=False, with_inbody=True):
    today = datetime.date.today()
    log = (lambda *a: None) if quiet else print

    # ① 目标日必拉（sync 内部：d == date 恒重拉、历史日缺文件才拉）
    log("[collect] 拉取 %s（窗口 %d 天）…" % (today.isoformat(), days))
    try:
        s = sync.sync(today.isoformat(), days)
        fetched = s.get("fetched") or []
        log("[collect]   实拉 %s" % ("、".join(fetched) if fetched else "无（均已落盘）"))
        if s.get("errors"):
            log("[collect]   端点失败：%s" % json.dumps(s["errors"], ensure_ascii=False))
    except Exception as e:
        s = {"fetched": [], "errors": {"_": str(e)}}
        log("[collect]   ✗ 拉取失败：%s" % e)

    # ② 回补窗口内被截断的历史日（不含今天）
    rep = {"repaired": [], "unchanged": [], "errors": {}}
    if days > 1:
        try:
            rep = sync.repair_recent(days - 1, end=today - datetime.timedelta(days=1))
        except Exception as e:
            rep["errors"]["_"] = str(e)
    if rep.get("repaired"):
        for r in rep["repaired"]:
            log("[collect]   回补 %s：%s → %s（步数 %s → %s）"
                % (r["date"], r["before"], r["after"],
                   r["steps_before"], r["steps_after"]))

    # ③ 完整性一览
    rows = data_gate.matrix(days, end=today)
    if not quiet:
        log("\n%-12s %-7s %-6s %6s  %s" % ("日期", "状态", "截止", "步数", "说明"))
        for r in rows:
            mark = {"complete": "OK", "partial": "WAIT",
                    "no_data": "EMPTY", "missing_raw": "NO-RAW"}[r["level"]]
            log("%-12s %-7s %-6s %6s  %s"
                % (r["date"], mark, r["cutoff"] or "-",
                   r.get("steps") if r.get("steps") is not None else "-",
                   (r["blocking"][0] if r["blocking"] else "")))

    # ④ 物化层（2026-10-25 接入）：raw 一变就把 obs/units/assertions 跟上。
    #    放在主页推送之前——主页数据要读 units，顺序反了会推一版旧单元。
    #    **失败不影响采集**：派生层坏了可以随时 index.py build 重建，没必要连累拉数。
    indexed = None
    try:
        r = subprocess.run([sys.executable, os.path.join(_HERE, "index.py"), "build"],
                           capture_output=True, text=True, timeout=300)
        indexed = (r.returncode == 0)
        if not indexed:
            log("[collect] 物化层重建失败（rc=%s）：%s"
                % (r.returncode, ((r.stderr or r.stdout or "").strip()
                                  .splitlines() or ["?"])[-1]))
    except Exception as e:
        indexed = False
        log("[collect] 物化层重建失败：%s" % e)

    log("[collect] 物化层：%s" % ("已重建" if indexed else "未重建（见上）"))

    # ⑤ 主页热更新
    pushed = None
    if push:
        script = os.path.join(_WORKSPACE, "tools", "build_homepage_data.py")
        rc = subprocess.call([sys.executable, script, "--push"],
                             stdout=subprocess.DEVNULL if quiet else None)
        pushed = (rc == 0)
        log("[collect] 主页数据推送：%s" % ("成功" if pushed else "失败（不影响采集）"))

    # ⑥ InBody（事件型）：顺手确认有没有新扫描，并过一遍体成分闸门。
    #    拉取失败只记一行，不影响佳明数据；闸门（check_inbody）只读本地，零额外网络。
    inbody, inbody_check = None, None
    if with_inbody:
        try:
            r = subprocess.run(
                [sys.executable,
                 os.path.join(_HERE, "sync_inbody.py")],
                capture_output=True, text=True, timeout=120)
            inbody = "ok" if r.returncode == 0 else "failed(rc=%s)" % r.returncode
            if r.returncode != 0:
                log("[collect] InBody 未刷新：%s"
                    % ((r.stderr or r.stdout or "").strip().splitlines() or ["?"])[-1])
        except Exception as e:
            inbody = "failed"
            log("[collect] InBody 未刷新：%s" % e)
        try:
            import check_inbody
            inbody_check = check_inbody.evaluate()
            check_inbody.write_status(inbody_check)
            log(check_inbody.one_line(inbody_check))
            for a in (inbody_check.get("anomalies") or [])[:2]:
                log("[collect]   ⚠ %s" % a)
        except Exception as e:
            inbody_check = {"verdict": "FAILED", "ok": False, "error": str(e)}
            log("[collect] InBody 闸门不可用：%s" % e)

    return {
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "days": days,
        "sync": s,
        "repair": rep,
        "gate": rows,
        "indexed": indexed,
        "pushed": pushed,
        "inbody": inbody,
        "inbody_check": inbody_check,
        "complete": [r["date"] for r in rows if r["level"] == "complete"],
        "waiting": [r["date"] for r in rows if r["level"] == "partial"],
    }


def main():
    ap = argparse.ArgumentParser(description="定时采集（不出报告）")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--no-push", dest="push", action="store_false", default=True)
    ap.add_argument("--no-inbody", dest="with_inbody", action="store_false",
                    default=True, help="跳过 InBody 检查（默认会顺手刷一次）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    out = run(a.days, push=a.push, quiet=a.json, with_inbody=a.with_inbody)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
