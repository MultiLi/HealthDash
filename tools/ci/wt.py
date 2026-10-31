#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Worktree 管理：一个 feature 一个分支一个目录。

**为什么需要它**（2026-11-01）：
2026-11-01 凌晨发生过一次现场：前端功能改到一半（20 个文件 859 行未提交），
另一个 agent 如果要动同一个仓库，只能在「等他提交」和「在同一工作区改、
互相覆盖」之间选。git worktree 解决这个问题——同一个仓库、多个检出、
互不干扰。但裸用 `git worktree add` 会掉进一个坑：

    **worktree 是干净的 checkout，而 data/ 与 site/index.json 都不入库。**
    于是新 worktree 里没有数据，任何读数的脚本都会在一个离根因很远的地方
    报错（或者更糟：静默产生空结果）。

所以本工具的 `new` 不只是建目录，还会把主仓库的数据通过**符号链接**接进来：
    data/            整目录（唯一数据源，多 worktree 共享同一份）
    site/index.json  前端数据层（冒烟测试需要）
    site/daily/      按月的详情分片
    tools/deploy.json 发布参数（含 appId，本机保存）

**这里有个必须理解的取舍**：代码隔离、数据共享。
两个 worktree 各改各的代码互不影响；但**数据只有一份**，A 跑了 collect，
B 读到的就是 A 拉的新数据。这是有意的——AGENT.md §8 说数据单根，
多份数据比共享数据更危险。相应地，`slot.py mark`、`push_data.py`、
发布动作仍然是**独占操作**，worktree 隔离不了它们。

用法：
    python3 tools/ci/wt.py new feat/nudge-v2   # 建分支 + worktree + 链数据
    python3 tools/ci/wt.py new fix/calendar --from origin/main
    python3 tools/ci/wt.py list                # 看所有 worktree 与脏状态
    python3 tools/ci/wt.py link                # 给已存在的 worktree 补链数据
    python3 tools/ci/wt.py rm feat/nudge-v2    # 删除（要求干净）
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

#: 允许的分支前缀。不在这里的会补默认前缀，而不是报错——agent 常常只给一个词。
PREFIXES = ("feat", "fix", "ci", "docs", "chore", "refactor", "test", "perf")

#: 链进 worktree 的路径：**唯一数据源**与**本机凭据**，多副本只会导致不一致。
LINK_PATHS = ("tools/deploy.json",)

#: 复制进 worktree 的路径：**派生产物**。必须各存一份，不能链。
#:
#: 原因是 2026-11-01 实测踩到的：site/index.json 若链接到主仓库，
#: worktree 里跑 `build_homepage_data.py` 会把主仓库的产物覆盖掉；
#: 而如果不重建、直接拿主仓库最新的 index.json 去跑冒烟，测的就是
#: 「旧分支代码 + 新数据」的组合——冒烟会红，但红的原因不在分支上。
#: 一个假红灯比没有灯更糟：agent 会去修一个不存在的问题。
COPY_PATHS = ("site/index.json", "site/daily")

#: 不接进 worktree 的路径：推送/发布专用，属于独占操作，一律回主仓库做。
SKIP_PATHS = ("site/.data", "site/.push_token")

#: data/ 是特殊情况，见 link_data() 里的说明。
DATA_MAIN = "data"


def git(*args, cwd=None):
    p = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main_root(cwd=None):
    """主仓库根。在 worktree 里调用也返回主仓，不是当前 worktree。"""
    rc, out = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=cwd)
    if rc != 0:
        return None
    return os.path.dirname(out.strip())


def _link_one(src, dst):
    """把 src 链到 dst。返回 (状态, 说明)，状态 ∈ {"linked", "skip"}。"""
    if not os.path.lexists(src):
        return "skip", "主仓库没有"
    if os.path.islink(dst):
        if os.path.realpath(dst) == os.path.realpath(src):
            return "skip", "已链"
        os.unlink(dst)
    elif os.path.exists(dst):
        return "skip", "已存在且不是链接，不动"
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    try:
        os.symlink(src, dst, target_is_directory=os.path.isdir(src))
    except OSError as e:
        return "skip", str(e)
    return "linked", ""


def _copy_derived(mr, wt_path):
    """复制派生产物（index.json / daily 分片）。返回 (已复制, 说明)。"""
    copied, notes = [], []
    for rel in COPY_PATHS:
        src = os.path.join(mr, rel)
        dst = os.path.join(wt_path, rel)
        if not os.path.exists(src):
            continue
        if os.path.lexists(dst):
            notes.append("%s（worktree 已有，不覆盖）" % rel)
            continue
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        try:
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            copied.append(rel)
        except (OSError, shutil.Error) as e:
            notes.append("%s 复制失败（%s）——冒烟测试会因此报错" % (rel, e))
    return copied, notes


def _unlink_all(wt_path, mr):
    """断开 worktree 里所有指向主仓库的链接。

    必须在 `git worktree remove` 之前调用：删除目录时如果还留着指向
    data/ 的链接，某些实现会顺着链接递归删除——那就把唯一一份数据删了。
    """
    for name in sorted(os.listdir(os.path.join(mr, DATA_MAIN))) if \
            os.path.isdir(os.path.join(mr, DATA_MAIN)) else []:
        dst = os.path.join(wt_path, DATA_MAIN, name)
        if os.path.islink(dst):
            os.unlink(dst)
    for rel in LINK_PATHS:
        dst = os.path.join(wt_path, rel)
        if os.path.islink(dst):
            os.unlink(dst)


def link_data(wt_path, mr):
    """把主仓库的数据接进 worktree。返回 (已链, 已复制, 说明)。

    **data/ 必须逐子项链接，不能整个目录链。**
    因为 `data/README.md` 是被跟踪的，新 worktree checkout 完就会有一个
    「存在但只有 README」的 data/ 目录。如果按目录级判断，链接会被跳过，
    于是脚本拿到一个真实的空数据目录——不报错，只是全部读空。
    「静默读空」比「明确报错」危险得多，所以这里绕开目录级链接。
    """
    linked, skipped = [], []

    src_data = os.path.join(mr, DATA_MAIN)
    dst_data = os.path.join(wt_path, DATA_MAIN)
    if os.path.isdir(src_data):
        os.makedirs(dst_data, exist_ok=True)
        for name in sorted(os.listdir(src_data)):
            if name == "README.md":
                continue  # 唯一被跟踪的文件，保留 worktree 自己那份
            state, why = _link_one(os.path.join(src_data, name),
                                   os.path.join(dst_data, name))
            if state == "linked":
                linked.append("data/" + name)
            elif why != "已链":
                skipped.append("data/%s（%s）" % (name, why))
    else:
        skipped.append("data/（主仓库没有）")

    for rel in LINK_PATHS:
        state, why = _link_one(os.path.join(mr, rel), os.path.join(wt_path, rel))
        if state == "linked":
            linked.append(rel)
        elif why != "已链":
            skipped.append("%s（%s）" % (rel, why))

    copied, notes = _copy_derived(mr, wt_path)
    return linked, copied, skipped + notes


def main_root(cwd=None):
    """主仓库根（worktree 里调用时返回主仓，不是当前 worktree）。"""
    rc, out = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=cwd)
    if rc != 0:
        return None
    return os.path.dirname(out.strip())


def normalize_branch(name):
    """`nudge-v2` → `feat/nudge-v2`；`feat/nudge-v2` 原样返回。"""
    name = name.strip()
    if "/" in name:
        head, rest = name.split("/", 1)
        if head in PREFIXES:
            return "%s/%s" % (head, rest)
    return "feat/%s" % name


def safe_slug(branch):
    """分支名 → 目录名。`feat/nudge-v2` → `feat-nudge-v2`。"""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip("-.")
    return slug or "worktree"


def cmd_new(a):
    mr = main_root()
    if mr is None:
        print("不在 git 仓库内", file=sys.stderr)
        return 2
    branch = normalize_branch(a.name)
    slug = safe_slug(branch)
    wt_path = os.path.join(mr, ".worktrees", slug)

    if os.path.exists(os.path.join(mr, ".worktrees", slug)):
        print("目录已存在：%s\n用 `list` 看现有 worktree，或换一个名字。" % wt_path, file=sys.stderr)
        return 2
    rc, out = git("rev-parse", "--verify", branch, cwd=mr)
    if rc == 0:
        print("分支 %s 已存在。要从它继续开发用 `link`，要新开请换名字。" % branch, file=sys.stderr)
        return 2

    base = a.base
    rc, _ = git("rev-parse", "--verify", base, cwd=mr)
    if rc != 0:
        # 离线或还没推过：退到本地 main，别让建分支失败在一个与意图无关的原因上
        fallback = "main"
        print("基线 %s 不可用，改用 %s" % (base, fallback), file=sys.stderr)
        base = fallback

    os.makedirs(os.path.join(mr, ".worktrees"), exist_ok=True)
    rc, out = git("worktree", "add", "-b", branch, wt_path, base, cwd=mr)
    if rc != 0:
        print("建 worktree 失败：\n%s" % out.strip(), file=sys.stderr)
        return 1

    linked, copied, skipped = link_data(wt_path, mr)
    print("已建 %s" % branch)
    print("  目录  %s" % os.path.relpath(wt_path, mr))
    print("  基线  %s" % base)
    if linked:
        print("  已链  %s   ← 与主仓库同一份，改动彼此可见" % "、".join(linked))
    if copied:
        print("  已复制 %s   ← 派生产物，本分支独立" % "、".join(copied))
    if skipped:
        print("  跳过  %s" % "、".join(skipped))
    print("")
    print("下一步：")
    print("  cd %s" % os.path.relpath(wt_path, mr))
    print("  python3 tools/ci/check.py          # 动手前先确认基线是绿的")
    print("  # …改代码…")
    print("  git add -A && git commit -m '...' && git push -u origin %s" % branch)
    print("  gh pr create --fill                # 或网页开 PR")
    print("")
    print("注意两点：")
    print("  1. data/ 是链到主仓库的同一份，跑了 collect 别的工作区立刻可见。")
    print("  2. 改了 site/ 之后要先重建产物再冒烟，否则测的是「旧产物 + 新代码」：")
    print("       python3 tools/build_homepage_data.py && node tools/smoke_site.mjs")
    return 0


def cmd_list(a):
    mr = main_root()
    if mr is None:
        print("不在 git 仓库内", file=sys.stderr)
        return 2
    rc, out = git("worktree", "list", "--porcelain", cwd=mr)
    if rc != 0:
        print(out, file=sys.stderr)
        return 1
    entries, cur = [], {}
    for line in out.splitlines():
        if line.startswith("worktree "):
            if cur:
                entries.append(cur)
            cur = {"path": line.split(" ", 1)[1]}
        elif line.startswith("branch "):
            cur["branch"] = line.split(" ", 1)[1].replace("refs/heads/", "")
        elif line.startswith("HEAD "):
            cur["head"] = line.split(" ", 1)[1][:8]
        elif line.strip() == "detached":
            cur["branch"] = "(detached)"
    if cur:
        entries.append(cur)

    print("%-28s %-10s %s" % ("分支", "HEAD", "目录 / 状态"))
    print("─" * 78)
    for e in entries:
        path = e["path"]
        sub = "" if os.path.realpath(path) == os.path.realpath(mr) else os.path.relpath(path, mr)
        rc2, st = git("status", "--porcelain", cwd=path)
        dirty = "%d 处未提交" % len([ln for ln in st.splitlines() if ln.strip()]) \
            if rc2 == 0 and st.strip() else "干净"
        # data/ 目录本身总是存在（README.md 被跟踪），所以要看实质内容
        has_data = os.path.lexists(os.path.join(path, "data", "raw"))
        mark = "" if sub == "" else ("  数据:%s" % ("已链" if has_data else "未链 ⚠"))
        print("%-28s %-10s %s%s" % (e.get("branch", "?"), e.get("head", ""), sub or ".", mark))
        print("%-28s %-10s %s" % ("", "", dirty))
    print("")
    if any(os.path.realpath(e["path"]) != os.path.realpath(mr) and
           not os.path.lexists(os.path.join(e["path"], "data", "raw")) for e in entries):
        print("有 worktree 未链数据：跑 `python3 tools/ci/wt.py link` 补上，否则读数脚本会读空。")
    return 0


def cmd_link(a):
    mr = main_root()
    if mr is None:
        print("不在 git 仓库内", file=sys.stderr)
        return 2
    base = os.path.join(mr, ".worktrees")
    if not os.path.isdir(base):
        print("没有 .worktrees 目录", file=sys.stderr)
        return 0
    for name in sorted(os.listdir(base)):
        wt = os.path.join(base, name)
        if not os.path.isdir(wt):
            continue
        linked, copied, skipped = link_data(wt, mr)
        if linked or copied:
            print("%-30s 已链 %s%s" % (name, "、".join(linked),
                                       "；已复制 " + "、".join(copied) if copied else ""))
        elif skipped:
            print("%-30s %s" % (name, "、".join(skipped)))
    return 0


def cmd_rm(a):
    mr = main_root()
    if mr is None:
        print("不在 git 仓库内", file=sys.stderr)
        return 2
    branch = normalize_branch(a.name)
    wt_path = os.path.join(mr, ".worktrees", safe_slug(branch))
    if not os.path.isdir(wt_path):
        print("没有这个 worktree：%s" % wt_path, file=sys.stderr)
        return 2

    # 链接先断，避免 git worktree remove 顺着链接去删主仓库的数据
    _unlink_all(wt_path, mr)

    args = ["worktree", "remove", wt_path]
    if a.force:
        args.append("--force")
    rc, out = git(*args, cwd=mr)
    if rc != 0:
        print("删除失败（通常是还有未提交改动）：\n%s\n确认不要了就加 --force" % out.strip(), file=sys.stderr)
        return 1

    if not a.keep_branch:
        git("branch", "-D", branch, cwd=mr)
        print("已删 worktree 与分支 %s" % branch)
    else:
        print("已删 worktree，保留分支 %s" % branch)
    return 0


def main():
    ap = argparse.ArgumentParser(description="HealthDash worktree 管理")
    sub = ap.add_subparsers(dest="cmd")

    p_new = sub.add_parser("new", help="建分支 + worktree + 链数据")
    p_new.add_argument("name", help="分支名，如 feat/nudge-v2 或 nudge-v2")
    p_new.add_argument("--from", dest="base", default="origin/main", help="基线（默认 origin/main）")
    p_new.set_defaults(func=cmd_new)

    sub.add_parser("list", help="列出所有 worktree").set_defaults(func=cmd_list)
    sub.add_parser("link", help="给所有 worktree 补链数据").set_defaults(func=cmd_link)

    p_rm = sub.add_parser("rm", help="删除 worktree")
    p_rm.add_argument("name")
    p_rm.add_argument("--force", action="store_true", help="丢掉未提交改动")
    p_rm.add_argument("--keep-branch", action="store_true", help="保留分支")
    p_rm.set_defaults(func=cmd_rm)

    a = ap.parse_args()
    if not getattr(a, "func", None):
        ap.print_help()
        return 0
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
