#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HealthDash 统一检查入口：**本地和 CI 跑的是同一份**。

**为什么需要它**（2026-11-01）：
在此之前，「验证」是散在 AGENT.md 第 7 节的一串命令，靠人（或 agent）记得跑，
而且**本地能跑的东西 CI 跑不了**——因为 data/ 与 site/index.json 都不入库。
结果就是两边各说各话：本地全绿、CI 一片红，或者反过来，谁也不知道该信谁。

这个文件把两边收成一份：
  同一个命令、同一套判据、同一份输出格式。
差异只有一个——某些检查需要数据，CI 里没有，于是显示 SKIP 并**在结尾声明缺口**。
「没跑」和「跑了没通过」必须长得不一样，否则 SKIP 会变成长期掩护。

用法：
    python3 tools/ci/check.py              # 人读
    python3 tools/ci/check.py --json       # 机器读（CI 写 STEP_SUMMARY 用）
    python3 tools/ci/check.py --strict     # SKIP 也算失败（发布前用）
    python3 tools/ci/check.py --only guard,structure   # 只跑指定项

退出码：
    0 = 全部通过（允许 SKIP）
    1 = 有检查失败
    2 = 环境问题（不在仓库内、缺 python3/node）

**纪律**：新增检查项必须同时满足 —— ① 本地无数据也能跑；② 失败时能说出「修法」。
只报错不说修法的检查，agent 读到只会重试同一个错误。
"""
import argparse
import ast
import json
import os
import re
import subprocess
import sys

# ── 定位仓库根 ────────────────────────────────────────────────────────────
# 不用 settings.py：如果它自己坏了，本文件仍然要能跑起来并报出这件事。
# 判据与 settings.py 保持一致（tools/ + docs/ 同时存在），两处若分歧以本文件为准
# 的只有一处——检查项 path:anchor 会直接拿 settings.py 的解析结果跟这里比对。


def _find_root(start):
    cur = os.path.abspath(start)
    for _ in range(8):
        cur = os.path.dirname(cur)
        if not cur or cur == os.path.dirname(cur):
            break
        if os.path.isdir(os.path.join(cur, "tools")) and os.path.isdir(os.path.join(cur, "docs")):
            return cur
    return None


ROOT = _find_root(os.path.dirname(os.path.abspath(__file__)))
IN_CI = os.environ.get("CI") == "true"

#: 检查时要跳过的目录（worktree 是另一个副本，重复扫描既慢又会误报）
SKIP_DIRS = {".git", ".worktrees", "__pycache__", "node_modules", ".venv", "venv", ".data"}


def sh(cmd, cwd=None, timeout=180, extra_env=None):
    """跑一条命令，返回 (rc, out)。rc=None 表示超时。"""
    try:
        env = dict(os.environ, NO_PROXY="*", no_proxy="*")
        if extra_env:
            env.update({k: v for k, v in extra_env.items() if v})
        p = subprocess.run(
            cmd, cwd=cwd or ROOT, capture_output=True, text=True, timeout=timeout, env=env,
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return None, "超时（%ds）" % timeout
    except FileNotFoundError:
        return 127, "命令不存在"


def walk(suffixes):
    """遍历仓库内指定后缀的文件，跳过 SKIP_DIRS。返回相对 ROOT 的路径列表。"""
    out = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if name.endswith(suffixes):
                out.append(os.path.relpath(os.path.join(base, name), ROOT))
    return out


# ── 检查项 ────────────────────────────────────────────────────────────────
# 每项返回 (status, detail)；status ∈ {"pass", "fail", "skip"}


def check_guard():
    """数据守卫：已跟踪文件里不许出现个人身体数值。"""
    rc, out = sh([sys.executable, "tools/guard_no_data.py"])
    if rc == 0:
        m = re.search(r"(\d+)\s*个文件", out)
        return "pass", "%s 个已跟踪文件无身体数值" % (m.group(1) if m else "?")
    if rc == 127:
        return "fail", "跑不起来：找不到 guard_no_data.py"
    lines = [ln for ln in out.splitlines() if ln.strip()]
    head = "；".join(lines[1:4]) if len(lines) > 1 else "见上方输出"
    return "fail", "%s\n    修法：L1 → 数据挪回 data/ 并 git rm --cached；L2 → 数值换成中性描述；L3 → 凭据只留本机" % head


def check_py_compile():
    """Python 语法编译 + 声明的 3.9 下限。只在内存里 compile()，不产生 __pycache__。

    为什么要查版本下限：`pyproject.toml` 写着 requires-python >=3.9，但开发机
    是 3.13。只用本机解释器编译，「声明 3.9、实际只有 3.13 能跑」这种不一致
    永远看不见。`ast.parse(feature_version=(3, 9))` 让这件事在本机就能验。
    """
    files = walk((".py",))
    bad, compat = [], []
    for rel in files:
        try:
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                src = fh.read()
        except OSError as e:
            bad.append("%s  %s" % (rel, e))
            continue
        try:
            compile(src, rel, "exec")
        except SyntaxError as e:
            bad.append("%s:%s  %s" % (rel, e.lineno, e.msg))
            continue
        try:
            ast.parse(src, rel, feature_version=(3, 9))
        except SyntaxError as e:
            compat.append("%s:%s  %s" % (rel, e.lineno, e.msg))
    if bad:
        return "fail", "%d 个文件语法错误：\n    %s\n    修法：先改语法再看别的，后续检查都建立在能解析之上" % (
            len(bad), "\n    ".join(bad[:5]))
    if compat:
        return "fail", "%d 处用了 3.9 还不支持的语法：\n    %s\n    修法：要么改用兼容写法，要么把 pyproject.toml 的 requires-python 提高（声明要和现实一致）" % (
            len(compat), "\n    ".join(compat[:5]))
    return "pass", "%d 个 Python 文件语法通过，且不超出 3.9 下限" % len(files)


def check_js_syntax():
    """JS/ESM 语法检查。"""
    files = walk((".js", ".mjs"))
    if not files:
        return "skip", "仓库里没有 js 文件"
    bad = []
    for rel in files:
        rc, out = sh(["node", "--check", rel])
        if rc == 127:
            return "skip", "环境没有 node，跳过 JS 语法"
        if rc != 0:
            first = next((ln for ln in out.splitlines() if ln.strip() and rel not in ln), "解析失败")
            bad.append("%s  %s" % (rel, first.strip()[:100]))
    if bad:
        return "fail", "%d 个文件语法错误：\n    %s\n    修法：node --check <文件> 本地复现，错的通常是括号或多余逗号" % (
            len(bad), "\n    ".join(bad[:5]))
    return "pass", "%d 个 JS/ESM 文件语法通过" % len(files)


def check_path_anchor():
    """路径解析契约：settings.py 必须能在这个 checkout 里自己找到根。

    这是**全新 clone 的第一道门**：CI 里没有 data/、没有产物，如果 settings.py
    靠产物判定根，它会在干净的 checkout 里解析到一个不存在的目录，然后所有
    脚本在离根因很远的地方报错。
    """
    entry = os.path.join(ROOT, "skills", "body-health", "scripts", "settings.py")
    if not os.path.isfile(entry):
        return "skip", "找不到 settings.py（布局变了？）"
    rc, out = sh([sys.executable, entry])
    if rc != 0:
        return "fail", "settings.py 退出码 %s：%s\n    修法：它必须是纯解析、零 I/O、零项目内 import" % (rc, out.strip()[:200])
    try:
        got = json.loads(out)
    except ValueError:
        return "fail", "settings.py 输出不是 JSON：%s\n    修法：保持 `python3 settings.py` 直接打印解析结果" % out.strip()[:160]

    if got.get("root") != ROOT:
        return "fail", "解析到 %s，实际根是 %s\n    修法：锚点判据（tools/+docs/ 同时存在）必须优先于兜底层数" % (
            got.get("root"), ROOT)
    if not got.get("anchors_ok"):
        return "fail", "anchors_ok=false：根是靠「上四层」兜底猜出来的，换布局就会断\n    修法：确认仓库根同时存在 tools/ 与 docs/"
    return "pass", "自解析到仓库根，锚点命中"


def check_structure():
    """结构契约：数据边界的规则还在，而且没被绕过。"""
    problems = []

    gi = os.path.join(ROOT, ".gitignore")
    if not os.path.isfile(gi):
        problems.append(".gitignore 不见了")
    else:
        with open(gi, encoding="utf-8") as fh:
            body = fh.read()
        for rule in ("data/*", "!data/README.md", "site/index.json", ".worktrees"):
            if rule not in body:
                problems.append(".gitignore 缺规则 `%s`" % rule)

    rc, out = sh(["git", "ls-files", "data/"])
    if rc == 0:
        tracked = [ln for ln in out.splitlines() if ln.strip() and not ln.endswith("data/README.md")]
        if tracked:
            problems.append("data/ 下有 %d 个文件被跟踪：%s" % (len(tracked), ", ".join(tracked[:3])))

    # 误入库的大文件（二进制/产物，往往是手滑 git add -A）
    big = []
    for rel in walk(("",)):
        try:
            size = os.path.getsize(os.path.join(ROOT, rel))
        except OSError:
            continue
        if size > 2 * 1024 * 1024:
            big.append("%s (%.1f MB)" % (rel, size / 1048576))
    if big:
        problems.append("跟踪了 >2MB 的文件：%s" % ", ".join(big[:3]))

    if problems:
        return "fail", "%s\n    修法：数据边界是本仓最贵的一条教训（见 docs/incidents.md），不要绕过" % "；".join(problems)
    return "pass", "数据边界规则完整，data/ 无跟踪文件"


def check_smoke():
    """前端冒烟。优先真实数据（site/index.json）；没有就现场合成脱敏 fixture
    （tests/fixtures/make_smoke_fixture.py，roadmap P0③）——CI 里从 SKIP 变成真跑。
    两条路径跑的是同一份断言，差别只在数据来源。"""
    index = os.path.join(ROOT, "site", "index.json")
    import tempfile
    fixture_dir = None
    if not os.path.isfile(index):
        gen = os.path.join(ROOT, "tests", "fixtures", "make_smoke_fixture.py")
        if not os.path.isfile(gen):
            return "skip", "缺 site/index.json 且无 fixture 生成器（数据不入库，CI 里必然没有）"
        fixture_dir = tempfile.mkdtemp(prefix="smoke-fixture-")
        rc, out = sh([sys.executable, gen, fixture_dir], timeout=120)
        if rc != 0:
            return "fail", "fixture 生成失败：\n    %s\n    修法：本地跑 python3 tests/fixtures/make_smoke_fixture.py /tmp/x 复现" % out.strip()[:200]
    if not os.path.isfile(os.path.join(ROOT, "tools", "smoke_site.mjs")):
        return "skip", "找不到 tools/smoke_site.mjs"
    rc, out = sh(["node", "tools/smoke_site.mjs"], timeout=120,
                 extra_env={"SMOKE_DATA_DIR": fixture_dir} if fixture_dir else None)
    if rc == 127:
        return "skip", "环境没有 node"
    if rc == 0:
        m = re.search(r"全部通过[:：]\s*(\d+)", out)
        return "pass", "%s 项断言通过" % (m.group(1) if m else "全部")
    # 只认行首的 ✗。用「失败」做关键词会误抓通过的断言名
    # （比如「✓ 端点失败原因被记下」本身是绿的一条），报出来的就是假线索。
    bad = [ln.strip()[1:].strip() for ln in out.splitlines() if ln.strip().startswith("✗")]
    if not bad:
        # 进程直接崩了（ENOENT 之类），没有逐条断言结果，抓 Error 行
        err = next((ln.strip() for ln in out.splitlines() if ln.strip().startswith("Error:")), "")
        bad = [err[:150]] if err else ["进程异常退出，没有任何断言输出"]
    m = re.search(r"(\d+)\s*通过\s*/\s*(\d+)\s*失败", out)
    tally = "（%s 通过 / %s 失败）" % (m.group(1), m.group(2)) if m else ""
    src = "fixture" if fixture_dir else "真实数据"
    return "fail", "[%s] %s\n    %s\n    修法：先分清是真错还是锚点失效（docs/operations.md「冒烟测试的锚点纪律」）" % (
        src, "；".join(bad[:3]), tally.strip())


def check_golden():
    """判读回归：需要 data/db/health.db。守卫不只看文件存在——sqlite 连接会
    凭空创建空库（2026-11-01 CI 实证：fixture 生成器的 db.connect() 在裸
    checkout 里造出空 health.db，金标假性运行 0/11）。必须真的有 assertions
    表才算数。"""
    db_path = os.path.join(ROOT, "data", "db", "health.db")
    if not os.path.isfile(db_path):
        return "skip", "缺少 data/db/health.db（数据不入库，CI 里必然没有）"
    import sqlite3
    try:
        con = sqlite3.connect(db_path)
        n = con.execute("SELECT count(*) FROM sqlite_master WHERE name='assertions'").fetchone()[0]
        con.close()
    except sqlite3.Error as e:
        return "skip", "health.db 打不开（%s），判读回归无法执行" % e
    if not n:
        return "skip", "health.db 是空库（无 assertions 表）——判读回归需要真实数据"
    script = os.path.join(ROOT, "skills", "body-health", "scripts", "backtest.py")
    if not os.path.isfile(script):
        return "skip", "找不到 backtest.py"
    rc, out = sh([sys.executable, script, "golden"], timeout=180)
    if rc == 0:
        m = re.search(r"(\d+)\s*/\s*(\d+)", out)
        return "pass", "金标 %s" % (m.group(0) if m else "全部复现")
    return "fail", "金标未全过\n    修法：改了 rules.json 就会漂，先 backtest.py diff 看改判清单"


def check_events_schema():
    """事件总线 schema 校验（roadmap P0②）。

    CI 里没有真实 events.jsonl（数据不入库），但解析器必须被真的执行——
    跑 tests/fixtures/events_fixture.jsonl（合成脱敏事件，覆盖 id/supersede/
    legacy 各分支）。本机有真实数据时额外验一遍：仓库里的 schema 与口径
    漂了立刻红，而不是等 P1 迁移时才发现。
    """
    validator = os.path.join(ROOT, "tools", "validate_events.py")
    fixture = os.path.join(ROOT, "tests", "fixtures", "events_fixture.jsonl")
    if not os.path.isfile(validator) or not os.path.isfile(fixture):
        return "skip", "缺 validate_events.py 或 fixture"
    rc, out = sh([sys.executable, validator, "--file", fixture, "--quiet"], timeout=60)
    if rc != 0:
        return "fail", "fixture 校验失败（解析器或 schema 坏了）：\n    %s\n    修法：本地跑 python3 tools/validate_events.py --file tests/fixtures/events_fixture.jsonl 看逐行错误" % out.strip()[:200]
    real = os.path.join(ROOT, "data", "logs", "events.jsonl")
    if not os.path.isfile(real):
        return "pass", "fixture 9 条通过（CI 无真实数据，本机才验 events.jsonl）"
    rc, out = sh([sys.executable, validator, "--quiet"], timeout=60)
    if rc != 0:
        return "fail", "真实 events.jsonl 违反 schema：\n    %s\n    修法：python3 tools/validate_events.py 看逐行错误；修正只能 append supersede，禁止改旧行" % out.strip()[:200]
    m = re.search(r"(\d+) 条", out)
    return "pass", "fixture + 真实数据 %s 均通过" % (m.group(1) if m else "")


CHECKS = [
    ("guard", check_guard),
    ("py:compile", check_py_compile),
    ("js:syntax", check_js_syntax),
    ("path:anchor", check_path_anchor),
    ("structure", check_structure),
    ("events:schema", check_events_schema),
    ("smoke", check_smoke),
    ("golden", check_golden),
]

MARKS = {"pass": "[PASS]", "fail": "[FAIL]", "skip": "[SKIP]"}
ICON = {"pass": "✅", "fail": "❌", "skip": "⏭️"}


def render_summary(results, n_pass, n_fail, n_skip):
    """GitHub Actions 的 STEP_SUMMARY。写进 Actions 页面，也是 agent 读结论的地方。"""
    out = ["## HealthDash 检查", ""]
    out.append("| | 检查项 | 结果 |")
    out.append("|---|---|---|")
    for r in results:
        detail = r["detail"].split("\n")[0]
        out.append("| %s | `%s` | %s |" % (ICON[r["status"]], r["name"], detail.replace("|", "\\|")))
    out.append("")
    out.append("**%d 通过 / %d 失败 / %d 跳过**" % (n_pass, n_fail, n_skip))
    fails = [r for r in results if r["status"] == "fail"]
    if fails:
        out.append("")
        out.append("### 失败详情")
        for r in fails:
            out.append("")
            out.append("**`%s`**" % r["name"])
            out.append("")
            out.append("```")
            out.append(r["detail"])
            out.append("```")
    skips = [r for r in results if r["status"] == "skip"]
    if skips:
        out.append("")
        out.append("### 本次未覆盖（跳过 ≠ 通过）")
        for r in skips:
            out.append("- `%s`：%s" % (r["name"], r["detail"]))
        out.append("")
        out.append("> 依赖 `data/` 的检查在 CI 里永远跑不了——数据不入库是本仓的硬边界。"
                   "这部分只能在本机 `python3 tools/ci/check.py` 覆盖。")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="HealthDash 统一检查入口")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--summary", action="store_true", help="只输出 GitHub STEP_SUMMARY 的 markdown")
    ap.add_argument("--summary-file", default="", help="把 markdown 写进该文件，stdout 仍是人读输出")
    ap.add_argument("--strict", action="store_true", help="SKIP 也算失败")
    ap.add_argument("--only", default="", help="逗号分隔的检查项名")
    a = ap.parse_args()

    if ROOT is None:
        print("不在 HealthDash 仓库内（找不到同时含 tools/ 与 docs/ 的祖先目录）", file=sys.stderr)
        return 2

    wanted = {s.strip() for s in a.only.split(",") if s.strip()}
    runs = [(n, f) for n, f in CHECKS if not wanted or n in wanted]
    if not runs:
        print("没有匹配的检查项，可选：%s" % ", ".join(n for n, _ in CHECKS), file=sys.stderr)
        return 2

    results = []
    for name, fn in runs:
        try:
            status, detail = fn()
        except Exception as e:  # 检查项自己崩了，不能让整轮静默变成绿
            status, detail = "fail", "检查项自身异常：%s: %s" % (type(e).__name__, e)
        results.append({"name": name, "status": status, "detail": detail})

    n_pass = sum(r["status"] == "pass" for r in results)
    n_fail = sum(r["status"] == "fail" for r in results)
    n_skip = sum(r["status"] == "skip" for r in results)

    if a.summary_file:
        # CI 用：一次运行同时喂给人看（stdout）和 Actions 页面（文件）
        try:
            with open(a.summary_file, "a", encoding="utf-8") as fh:
                fh.write(render_summary(results, n_pass, n_fail, n_skip) + "\n")
        except OSError as e:
            print("写 summary 失败：%s" % e, file=sys.stderr)

    if a.summary:
        print(render_summary(results, n_pass, n_fail, n_skip))
    elif a.json:
        print(json.dumps({
            "ok": n_fail == 0 and not (a.strict and n_skip),
            "root": ROOT, "in_ci": IN_CI,
            "pass": n_pass, "fail": n_fail, "skip": n_skip,
            "results": results,
        }, ensure_ascii=False, indent=2))
    else:
        print("HealthDash 检查 · %s" % ("CI" if IN_CI else "本地"))
        print("─" * 62)
        for r in results:
            print("%s %-13s %s" % (MARKS[r["status"]], r["name"], r["detail"]))
        print("─" * 62)
        print("%d 通过 / %d 失败 / %d 跳过" % (n_pass, n_fail, n_skip))
        if n_skip:
            # SKIP 不是「通过」。缺哪一块能力必须写出来，否则它会长期充当掩护。
            print("\n本次跳过（未覆盖 ≠ 通过）：")
            for r in results:
                if r["status"] == "skip":
                    print("  · %-12s %s" % (r["name"], r["detail"]))
            print("  需要数据的检查只能在有 data/ 的机器上跑；CI 覆盖不了这部分。")
        if n_fail:
            print("\n有 %d 项失败，先修再提交。" % n_fail)

    if n_fail:
        return 1
    if a.strict and n_skip:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
