#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据泄露守卫：已跟踪的文件里只要出现个人身体数值，就报出来。

**为什么需要它**（2026-11-01）：
`.gitignore` 只能拦「你想到的位置」。真正出事的那次，是 `notes/events.jsonl`
被提交后又被 ignore——文件后来排除掉了，但历史里的 21 行健康记录已经推到
公开仓库上。规则写对不等于边界守住了：**已跟踪的文件不会因为 ignore 规则而
自动退出**，而文档里随手引用的真实案例数值更不会被任何路径规则拦住。

所以守卫盯的是「内容」而不是「路径」：不管这个文件叫什么、住在哪，
只要里面出现「体重 85.9kg」「体脂率 23.8%」「HRV 38」这类实测值，就报错。

用法：
    python3 tools/guard_no_data.py            # 扫描已跟踪文件，exit 1 = 发现泄露
    python3 tools/guard_no_data.py --verbose  # 逐条打印命中位置
    python3 tools/guard_no_data.py --staged   # 只看暂存区（可挂到 pre-commit）

退出码：0 = 干净；1 = 发现疑似个人数据；2 = 不在 git 仓库内。

判据分级：
    L1 路径   data/ 下的文件被 git 跟踪 —— 结构已被破坏，最严重
    L2 内容   命中实测值模式 —— 具体数字泄露
    L3 凭据   跟踪了 deploy.json / .push_token —— 可被冒用
模板与示例文件（*example*、fixtures）里允许出现示例值，走白名单。
"""
import argparse
import os
import re
import subprocess
import sys

#: L2 内容规则：(正则, 说明)。宁可多报也不能漏——每条都是「这个数只有我知道」。
PATTERNS = [
    (r"\b[5-9]\d(?:\.\d)?\s*kg\b", "体重/重量实测值"),
    (r"\b1[0-9]\d\s*kg\b", "体重实测值"),
    (r"体脂(?:率)?\s*[:：]?\s*\d{2}(?:\.\d)?\s*%", "体脂率"),
    (r"骨骼肌\s*[:：]?\s*\d{2}(?:\.\d)?", "骨骼肌量"),
    (r"\bHRV\s*[:：]?\s*\d{2,3}\b", "HRV 实测"),
    (r"静息心率\s*[:：]?\s*\d{2,3}", "静息心率"),
    (r"\bRHR\s*[:：]?\s*\d{2,3}\b", "静息心率"),
    (r'"max_hr"\s*:\s*\d{2,3}', "最大心率"),
    (r'"rhr(?:_7d_avg)?"\s*:\s*\d{2,3}', "静息心率"),
    (r'"age"\s*:\s*\d{2}\b', "年龄"),
    (r"VO2\s*[Mm]ax\s*[:：]?\s*\d{2}", "VO2Max"),
    (r"\bBMI\s*[:：]?\s*\d{2}\.\d", "BMI"),
    (r"身高\s*[:：]?\s*\d{3}\s*cm", "身高"),
    (r'"weight_kg"\s*:\s*\d{2}(?:\.\d)?', "体重"),
    (r"体重\s*(?:晨起|晚间|空腹)?\s*[:：]?\s*\d{2}(?:\.\d)?", "体重"),
]

#: 允许出现示例值的文件（模板/夹具）。真实值永远不该走这条路。
#: data/README.md 是唯一允许入库的 data/ 文件：它只描述目录结构，不含实测值。
ALLOW = (
    "profile.example.json",
    "guard_no_data.py",     # 本文件自带正则字面量
    "tests/fixtures/",
    "data/README.md",
)

#: 一旦被跟踪就说明结构被破坏的路径前缀
FORBIDDEN_PREFIX = ("data/", "reports/", "notes/frontend-audit")

#: 被跟踪即可被冒用的凭据
CREDENTIAL = ("tools/deploy.json", "site/.push_token", "site/_push_token")

TEXT_EXT = (".md", ".json", ".jsonl", ".py", ".js", ".mjs", ".css", ".html",
            ".txt", ".yaml", ".yml", ".toml", ".sh")


def git(*args):
    p = subprocess.run(["git"] + list(args), capture_output=True, text=True)
    if p.returncode != 0:
        return None
    return p.stdout


def tracked(staged=False):
    out = git("diff", "--cached", "--name-only", "--diff-filter=ACMR") if staged \
        else git("ls-files")
    if out is None:
        return None
    return [line for line in out.splitlines() if line.strip()]


def allowed(path):
    return any(a in path for a in ALLOW)


def scan(paths, verbose=False):
    findings = []
    for path in paths:
        if allowed(path):
            continue
        if path.startswith(FORBIDDEN_PREFIX):
            findings.append(("L1", path, 0, "数据目录下的文件被 git 跟踪"))
            continue
        if path in CREDENTIAL:
            findings.append(("L3", path, 0, "凭据/发布参数被 git 跟踪"))
            continue
        if not path.endswith(TEXT_EXT):
            continue
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                lines = fh.readlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, 1):
            for pat, desc in PATTERNS:
                if re.search(pat, line):
                    findings.append(("L2", path, lineno, desc))
                    break
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--staged", action="store_true", help="只查暂存区（pre-commit 用）")
    a = ap.parse_args()

    if git("rev-parse", "--show-toplevel") is None:
        print("不在 git 仓库内", file=sys.stderr)
        return 2

    paths = tracked(a.staged)
    if paths is None:
        print("无法读取 git 索引", file=sys.stderr)
        return 2

    findings = scan(paths, a.verbose)
    if not findings:
        print("OK  已跟踪的 %d 个文件里没有个人身体数值" % len(paths))
        return 0

    print("发现 %d 处疑似个人数据：" % len(findings))
    for level, path, lineno, desc in findings:
        loc = "%s:%d" % (path, lineno) if lineno else path
        print("  [%s] %s  —  %s" % (level, loc, desc))
    print()
    print("修法：")
    print("  L1 → 数据不该在代码库里；确认它在 data/ 下，然后 git rm --cached")
    print("  L2 → 把具体数值换成中性描述（「HRV 明显低于带下沿」而不是「HRV 38」）")
    print("  L3 → git rm --cached，凭据只留在本机")
    print("  历史里若已有：见 docs/incidents.md「公开仓库泄露事件」的处理流程")
    return 1


if __name__ == "__main__":
    sys.exit(main())
