#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""不可重建物的加密备份（2026-10-25 立，对应 docs/decisions.md D6）。

**为什么单独写这个脚本**：`.gitignore` 排除了 reports/、daily_log.jsonl、
notes/events.jsonl——这三样**无法用代码重建**（LLM 叙述与主观数据不可复现）。
在此之前它们只存在于这台机器上，没有任何副本：硬盘故障 = 32 份判读 +
38 天健康事件 + 全部主观记录一起消失。

判据（docs/operations.md §8）：删掉它能不能重建？不能的就必须有副本。

备份三样：
  reports/*.md  reports/*.json      判读产物（含 8 份 C2 改过的终版）
  daily_log.jsonl                  主观标量（体重等）
  notes/events.jsonl               健康事件原文

加密：openssl aes-256-cbc + PBKDF2。口令不落盘、不进命令行参数，
      从环境变量 HEALTH_BACKUP_PASS 读，或交互输入。
      没有口令的备份等于明文，不要那样做。

用法：
  python3 tools/backup_health.py                     # 默认输出到 ~/Documents/HealthBackups/
  python3 tools/backup_health.py --out /Volumes/Ext  # 外部盘（推荐，抗硬件故障）
  python3 tools/backup_health.py --list              # 只看会打包什么，不打包
  HEALTH_BACKUP_PASS=xxx python3 tools/backup_health.py --no-confirm

⚠️ 输出默认在 ~/Documents/HealthBackups/（与本项目同一块盘）。这防误删、
   不防硬件故障。要抗硬件故障必须 --out 到外部盘或另一台机器。
"""
import argparse
import datetime
import os
import subprocess
import sys
import tarfile
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))


def _find_scripts():
    """定位 body-health 脚本目录（与 tools/homepage/paths.py 同一套候选顺序）。
    搬迁到 skills/ 后第一个候选自动命中。"""
    env = os.environ.get("HEALTH_SCRIPTS_DIR")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    for rel in (("skills", "body-health", "scripts"),
                (".workbuddy", "skills", "body-health", "scripts")):
        cand = os.path.join(os.path.dirname(_HERE), *rel)
        if os.path.isfile(os.path.join(cand, "settings.py")):
            return cand
    raise RuntimeError("找不到 body-health scripts 目录（试过 skills/ 与 .workbuddy/skills/）")


sys.path.insert(0, _find_scripts())
from settings import (  # noqa: E402  路径唯一解析点
    DAILY_LOG, EVENTS_LOG, GOLDEN, REPORTS_DIR,
)

# 需要备份的几样：不存在就是缺失，脚本必须报出来而不是静默跳过。
# 2026-10-31：全部改走 settings 常量。此前 daily_log / events 是
# ROOT 下硬编码拼接，数据搬进 data/ 后它们会「静默地报缺失」——
# 而备份脚本静默失效，比不备份更危险。
TARGETS = [
    ("reports", REPORTS_DIR, "判读产物（md + json）"),
    ("daily_log", DAILY_LOG, "主观标量（体重等）"),
    ("events", EVENTS_LOG, "健康事件原文"),
    ("golden", GOLDEN, "金标用例（口径回归锚点）"),
]

DEFAULT_OUT = os.path.expanduser("~/Documents/HealthBackups")


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%.1f%s" % (n, unit)
        n /= 1024
    return "%.1fTB" % n


def inventory():
    """打包前先列清单：有什么、缺什么、多大。缺项必须显式报。"""
    rows = []
    for name, path, desc in TARGETS:
        if not os.path.exists(path):
            rows.append((name, path, desc, None, "缺失"))
            continue
        if os.path.isdir(path):
            files = []
            for dirpath, _dirs, names in os.walk(path):
                files += [os.path.join(dirpath, n) for n in names]
            size = sum(os.path.getsize(f) for f in files)
            rows.append((name, path, desc, (len(files), size), "OK"))
        else:
            rows.append((name, path, desc,
                         (1, os.path.getsize(path)), "OK"))
    return rows


def get_pass():
    """口令：环境变量优先，否则交互输入（不落盘、不进 ps）。"""
    p = os.environ.get("HEALTH_BACKUP_PASS")
    if p:
        return p
    if not sys.stdin.isatty():
        print("✗ 非交互环境且未设 HEALTH_BACKUP_PASS——拒绝生成明文备份", file=sys.stderr)
        sys.exit(2)
    import getpass
    a = getpass.getpass("备份口令（不落盘，输错无法恢复）：")
    b = getpass.getpass("再输一次确认：")
    if a != b:
        print("✗ 两次不一致", file=sys.stderr)
        sys.exit(2)
    if len(a) < 8:
        print("✗ 口令至少 8 位", file=sys.stderr)
        sys.exit(2)
    return a


def build(out_dir, passphrase, no_confirm=False):
    rows = inventory()
    missing = [r for r in rows if r[4] != "OK"]

    print("将打包：")
    total_files, total_size = 0, 0
    for name, path, desc, stat, state in rows:
        if stat:
            total_files += stat[0]
            total_size += stat[1]
            print("  ✓ %-12s %-58s %d 个文件 / %s" % (name, desc, stat[0], human(stat[1])))
        else:
            print("  ✗ %-12s %-58s %s" % (name, desc, "路径不存在：" + path))
    if missing:
        print("\n⚠️ 有 %d 项目标缺失，备份会不完整。" % len(missing))

    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.join(out_dir, "health_backup_%s" % stamp)
    tar_path = base + ".tar.gz"
    enc_path = base + ".tar.gz.enc"

    with tarfile.open(tar_path, "w:gz") as tar:
        for _name, path, _desc, _stat, state in rows:
            if state != "OK":
                continue
            tar.add(path, arcname=os.path.basename(path))

    # openssl：PBKDF2 + aes-256-cbc。-pbkdf2 必须有，否则默认 KDF 太弱
    cmd = ["openssl", "enc", "-aes-256-cbc", "-salt", "-pbkdf2", "-iter", "200000",
           "-in", tar_path, "-out", enc_path, "-pass", "env:HEALTH_BACKUP_PASS"]
    env = dict(os.environ, HEALTH_BACKUP_PASS=passphrase)
    r = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        print("✗ 加密失败：%s" % (r.stderr or "").strip(), file=sys.stderr)
        sys.exit(1)
    os.remove(tar_path)          # 明文压缩包立即删掉，别留在磁盘上

    size = os.path.getsize(enc_path)
    print("\n已生成：%s" % enc_path)
    print("  %s（源 %s）· %d 个文件" % (human(size), human(total_size), total_files))
    print("\n解密：")
    print("  HEALTH_BACKUP_PASS=<口令> openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \\")
    print("    -in %s -out restore.tar.gz" % os.path.basename(enc_path))

    # 同一目录下的旧备份列表，避免无限堆积没人知道
    olds = sorted(f for f in os.listdir(out_dir) if f.startswith("health_backup_"))
    if len(olds) > 1:
        print("\n该目录已有 %d 份备份，最早：%s" % (len(olds), olds[0]))
    return enc_path


def main():
    ap = argparse.ArgumentParser(description="不可重建物的加密备份")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help="输出目录（默认 ~/Documents/HealthBackups/；抗硬件故障请指向外部盘）")
    ap.add_argument("--list", action="store_true", help="只列清单，不打包")
    ap.add_argument("--no-confirm", action="store_true", help="跳过确认直接打包")
    a = ap.parse_args()

    rows = inventory()
    if a.list:
        for name, path, desc, stat, state in rows:
            print("%-12s %-6s %-58s %s" % (
                name, state, desc,
                ("%d 文件 / %s" % (stat[0], human(stat[1]))) if stat else "缺失"))
        return 0

    if not a.no_confirm and sys.stdin.isatty():
        ans = input("输出到 %s ，确认打包？[y/N] " % a.out)
        if ans.strip().lower() not in ("y", "yes"):
            print("已取消")
            return 0

    build(a.out, get_pass(), a.no_confirm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
