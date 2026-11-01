#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把仓库里的钩子接上：`core.hooksPath -> tools/hooks`，跑一次永久生效。

为什么不用「拷到 .git/hooks」：副本会和仓库里的版本漂移，而漂移没有任何症状——
你改的是仓库里那份，运行的是 .git 里那份。指向目录就不会有第二个真相。

worktree 共用同一份 `.git/config`，所以设一次，所有 worktree 都生效。

用法：
  python3 tools/ci/setup_hooks.py          # 设置（幂等）
  python3 tools/ci/setup_hooks.py --check  # 只看状态，不改（exit 1 = 没装）
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WANT = "tools/hooks"


def current():
    r = subprocess.run(["git", "config", "--get", "core.hooksPath"],
                       cwd=ROOT, capture_output=True, text=True)
    return r.stdout.strip() or None


def main():
    check = "--check" in sys.argv
    cur = current()
    if cur == WANT:
        print("✓ core.hooksPath = %s（已生效）" % WANT)
        return 0
    if check:
        print("✗ core.hooksPath = %s，应为 %s——钩子没生效，提交门禁形同不存在"
              % (cur or "（未设置）", WANT))
        print("  修法：python3 tools/ci/setup_hooks.py")
        return 1
    subprocess.run(["git", "config", "core.hooksPath", WANT], cwd=ROOT, check=True)
    # 确保可执行位在（git 记录了 mode，但换机器 clone 后仍可能丢）
    for n in os.listdir(os.path.join(ROOT, WANT)):
        p = os.path.join(ROOT, WANT, n)
        if os.path.isfile(p):
            os.chmod(p, 0o755)
    print("✓ core.hooksPath 已指向 %s；钩子：" % WANT)
    for n in sorted(os.listdir(os.path.join(ROOT, WANT))):
        print("   ", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
