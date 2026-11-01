#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合入 → 清理 → 部署：CD 的唯一入口（2026-11-01 建）。

## 为什么需要一条命令

`docs/workflow.md` §五 早就写了 0→8 步，但那是**给人看的 bash 片段**。2026-11-01
一天之内就出现过三种走岔：先部署后合并、合并后没重建成产物、把新数据推给还不认识
它的旧服务端。这些都不是「不懂」，是「顺序靠记性」。记性会输，命令不会。

## 固定顺序（每一步都有理由，不许调换）

    ① 起点校验   必须在 main、与 origin/main 一致、除 site/ 产物外工作区干净
                 → 否则你部署的东西不属于 main，线上会和 git 分家
    ② 构建       在 main 上构建（不是分支上）→ 产物与代码同版本
    ③ 判动作     deploy --check → noop / hot_update / republish
    ④ 代码先上线  republish 时必须先发布应用，**再**推数据
                 → 旧服务端不认识新数据键，先推数据会被整包拒绝（连当天 index 都丢）
    ⑤ 推数据     热更新 + 回读核验（不信返回值，只信再取一次）
    ⑥ 登记游标   --mark-app-deployed 只在真实发布成功后写
    ⑦ 清理       删已合并的本地分支、清构建残留、把工作区拉回干净
                 → 产物指纹是构建副产物，不该以「未提交改动」的样子跨会话存在
    ⑧ 线上核验   真去 curl 关键端点，打印链接

## 用法

    python3 tools/release.py              # 只体检 + 报告该做什么，不改任何东西
    python3 tools/release.py --run        # 真跑：构建 → 热更新（如需）→ 核验 → 清理
    python3 tools/release.py --run --keep-artifacts   # 不清理 site/ 产物差异

退出码：0 已同步/已完成 · 2 需要人工发布应用 · 3 推送失败 · 4 前置条件不满足
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: 允许「未提交」的路径：构建产物（内容指纹随数据变），不是源码改动
ARTIFACT_PREFIX = ("site/js/", "site/css/", "site/index.html", "site/views/",
                   "site/index.json", "site/daily/", "site/.data/")


def sh(cmd, cwd=None, timeout=300):
    p = subprocess.run(cmd, cwd=cwd or ROOT, capture_output=True, text=True,
                       timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def step(n, title):
    print("\n—— %s %s" % (n, title))


class Abort(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


# ------------------------------------------------------------------ ① 起点
def check_start(allow_branch=False):
    step("①", "起点校验")
    rc, out = sh(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    branch = out.strip()
    if rc != 0:
        raise Abort(4, "读不到当前分支：%s" % out.strip()[:200])
    if branch != "main" and not allow_branch:
        raise Abort(4, "当前在 %s，不在 main。部署必须发生在 main 上——"
                       "分支上的代码还没合入，先开 PR 合并。" % branch)
    sh(["git", "fetch", "--quiet", "origin"])
    rc, out = sh(["git", "rev-list", "--left-right", "--count", "origin/main...HEAD"])
    if rc == 0 and out.strip():
        behind, ahead = (int(x) for x in out.split())
        if behind:
            raise Abort(4, "落后 origin/main %d 个提交。先 `git pull --ff-only`——"
                           "在旧基线上构建等于把线上拉回过去。" % behind)
        if ahead:
            raise Abort(4, "有 %d 个提交没进 main。先走 PR 合入再部署。" % ahead)
    rc, out = sh(["git", "status", "--porcelain"])
    dirty = [ln for ln in out.splitlines() if ln.strip()]
    src = [ln for ln in dirty
           if not any(ln[3:].startswith(p.rstrip("/")) or ln[3:] == p.rstrip("/")
                      for p in ARTIFACT_PREFIX)]
    if src:
        raise Abort(4, "工作区有未提交的源码改动（%d 个），先提交或还原：\n    %s"
                    % (len(src), "\n    ".join(src[:5])))
    arts = len(dirty) - len(src)
    print("  分支 main · 与 origin 一致 · 源码干净%s"
          % ("（另有 %d 个构建产物差异，属正常）" % arts if arts else ""))
    return {"branch": branch, "artifact_changes": arts}


# ------------------------------------------------------------------ ② 构建
def build():
    step("②", "在 main 上构建产物")
    rc, out = sh([sys.executable, "tools/build_homepage_data.py"], timeout=600)
    for line in out.splitlines():
        if any(k in line for k in ("written:", "views:", "fingerprint:", "线上主页:", "✓", "✗")):
            print("  " + line.strip())
    if rc != 0:
        raise Abort(4, "构建失败：\n%s" % out[-600:])


# ------------------------------------------------------------------ ③ 判动作
def plan():
    step("③", "判断该做什么")
    rc, out = sh([sys.executable, "tools/deploy.py", "--check"], timeout=180)
    action = ""
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("需要动作"):
            action = line.split("：", 1)[-1].strip()
            print("  计划：" + action)
        elif line.startswith("→") or line.startswith("代码 ") or "无需动作" in line:
            print("  " + line)
    # 以退出码为准（deploy.py 的定义：2 = 需要重新发布），文本只用来显示。
    if rc == 2 or "republish" in action:
        return "republish"
    if rc != 0:
        raise Abort(4, "deploy.py --check 失败 rc=%s：\n%s" % (rc, out[-400:]))
    if "hot_update" in action:
        return "hot_update"
    return "noop"


# ------------------------------------------------------------------ ⑤ 推数据
def push():
    step("⑤", "热更新数据 + 回读核验")
    rc, out = sh([sys.executable, "tools/deploy.py"], timeout=300)
    for line in out.splitlines():
        if any(k in line for k in ("已推送", "回读核验", "数据热更新", "!")):
            print("  " + line.strip())
    if rc == 2:
        raise Abort(2, "推送前发现代码又变了：需要重新发布应用")
    if rc == 3:
        raise Abort(3, "热更新推送失败（见上）")
    if rc != 0:
        raise Abort(3, "推送异常 rc=%s" % rc)


def mark_deployed():
    rc, out = sh([sys.executable, "tools/deploy.py", "--mark-app-deployed"], timeout=120)
    print("  " + (out.strip().splitlines() or [""])[0])


# ------------------------------------------------------------------ ⑦ 清理
def cleanup(keep_artifacts=False):
    step("⑦", "清理")
    rc, out = sh(["git", "branch", "--merged", "main", "--format=%(refname:short)"])
    gone = [b for b in out.split() if b and b != "main" and b != "*"]
    for b in gone:
        sh(["git", "branch", "-D", b])
    print("  已合并的本地分支：%s" % ("、".join(gone) + "（已删）" if gone else "无"))
    rc, out = sh(["git", "worktree", "list", "--porcelain"])
    wts = [ln.split(" ", 1)[1] for ln in out.splitlines()
           if ln.startswith("worktree ") and ".worktrees/" in ln]
    print("  并行 worktree：%s" % ("、".join(os.path.basename(w) for w in wts) or "无"))
    if not keep_artifacts:
        rc, out = sh(["git", "status", "--porcelain"])
        arts = [ln[3:] for ln in out.splitlines()
                if ln.strip() and any(ln[3:].startswith(p.rstrip("/")) for p in ARTIFACT_PREFIX)]
        if arts:
            # 产物指纹是构建副产物：线上那份已经上传，本地留一堆未提交改动只会
            # 让下一个会话误以为「有人改了前端」。拉回 HEAD，下次构建再生成。
            sh(["git", "checkout", "--", "site/"])
            print("  回到干净的 site/（%d 个产物指纹差异已还原）" % len(arts))
        else:
            print("  site/ 已与 HEAD 一致")


# ------------------------------------------------------------------ ⑧ 核验
def verify_live():
    step("⑧", "线上核验")
    rc, out = sh([sys.executable, "tools/push_data.py", "--check"], timeout=180)
    ok = rc == 0
    for line in out.splitlines():
        if line.startswith("--check") or line.startswith("分片"):
            print("  " + line.strip())
    if not ok:
        print("  ! 线上与本地不一致——上面的推送没有真正生效")
        return False
    return True


def main():
    ap = argparse.ArgumentParser(description="合入 → 清理 → 部署（CD 唯一入口）")
    ap.add_argument("--run", action="store_true", help="真的执行（默认只体检）")
    ap.add_argument("--keep-artifacts", action="store_true", help="不还原 site/ 产物差异")
    ap.add_argument("--allow-branch", action="store_true", help="允许非 main（仅调试用）")
    a = ap.parse_args()

    print("HealthDash · CD（%s）" % ("执行" if a.run else "体检，不改任何东西"))
    try:
        st = check_start(a.allow_branch)
        if not a.run:
            build_hint = "（--run 时会重建）"
            act = plan()
            print("\n结论：%s%s" % (
                {"noop": "线上已同步，无需动作",
                 "hot_update": "需要热更新数据",
                 "republish": "需要重新发布应用（热更新改不了 js/css）"}[act], build_hint))
            if act == "republish":
                print("\n发布顺序（不许调换）：")
                print("  1) 在 WorkBuddy 里发布 site/ 应用（appId 见 tools/deploy.json）")
                print("  2) python3 tools/deploy.py --mark-app-deployed")
                print("  3) python3 tools/release.py --run   # 这次才会推数据")
                return 2
            return 0

        build()
        act = plan()
        if act == "republish":
            print("\n✗ 卡在这里：代码变了，必须先发布应用。")
            print("  顺序：发布 → deploy.py --mark-app-deployed → release.py --run")
            return 2
        if act == "hot_update":
            push()
        else:
            print("  线上已同步，跳过推送")
        if not verify_live():
            return 3
        cleanup(a.keep_artifacts)
        print("\n完成：代码与数据都与线上一致")
        return 0
    except Abort as e:
        print("\n✗ %s" % e)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
