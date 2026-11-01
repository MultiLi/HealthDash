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

加密：openssl aes-256-cbc + PBKDF2。口令不落盘、不进命令行参数。
      取口令的顺序：环境变量 HEALTH_BACKUP_PASS → **macOS 钥匙串** → 交互输入。
      钥匙串条目：service=health-backup，account=当前用户（2026-11-01 起，
      解决「没法挂定时任务」的缺口——口令不再需要人记也不再进 shell 历史）。

      ⚠️ 钥匙串解决的是「自动化」，不是「灾难恢复」：这台机器连同钥匙串一起
      没了，加密备份也解不开。所以首次 `--store-pass` 之后**务必把口令离线记
      一份**（`--show-pass` 可随时打印）。

用法：
  python3 tools/backup_health.py --store-pass          # 生成并存入钥匙串（只做一次）
  python3 tools/backup_health.py --show-pass           # 打印口令（离线抄一份）
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


KC_SERVICE = "health-backup"


def _kc_account():
    return os.environ.get("USER") or os.environ.get("LOGNAME") or "healthdash"


def kc_get():
    """从 macOS 钥匙串读口令；读不到返回 None（非 macOS / 没存过）。"""
    try:
        r = subprocess.run(["security", "find-generic-password",
                            "-s", KC_SERVICE, "-a", _kc_account(), "-w"],
                           capture_output=True, text=True)
    except OSError:
        return None
    if r.returncode != 0:
        return None
    return (r.stdout or "").strip() or None


def kc_store(passphrase):
    """写进钥匙串（-U 覆盖同名条目）。返回 (ok, 说明)。"""
    try:
        r = subprocess.run(["security", "add-generic-password", "-U",
                            "-s", KC_SERVICE, "-a", _kc_account(),
                            "-w", passphrase, "-T", "/usr/bin/security"],
                           capture_output=True, text=True)
    except OSError as e:
        return False, "security 命令不可用：%s" % e
    if r.returncode != 0:
        return False, (r.stderr or "").strip()[:200]
    return True, "已写入钥匙串（service=%s account=%s）" % (KC_SERVICE, _kc_account())


def gen_pass():
    """高熵口令：24 字节 url-safe base64，约 32 字符。"""
    import base64
    import secrets
    return base64.urlsafe_b64encode(secrets.token_bytes(24)).decode().rstrip("=")


def get_pass():
    """口令：环境变量 → 钥匙串 → 交互输入（不落盘、不进 ps）。"""
    p = os.environ.get("HEALTH_BACKUP_PASS")
    if p:
        return p
    p = kc_get()
    if p:
        return p
    if not sys.stdin.isatty():
        print("✗ 非交互环境且未设 HEALTH_BACKUP_PASS / 钥匙串无条目——拒绝生成明文备份",
              file=sys.stderr)
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
    ap.add_argument("--store-pass", action="store_true",
                    help="生成口令并存入 macOS 钥匙串（已有则覆盖；需先 --show-pass 抄一份离线）")
    ap.add_argument("--show-pass", action="store_true",
                    help="打印当前生效的口令（离线抄一份，这是唯一灾难恢复凭据）")
    ap.add_argument("--pass-status", action="store_true", help="只报告口令来源")
    a = ap.parse_args()

    if a.show_pass:
        p = get_pass()
        print(p)
        print("\n⚠️ 离线抄一份并放进可信的地方：这台机器与钥匙串一起丢失时，"
              "加密备份只有这一串能解开。", file=sys.stderr)
        return 0

    if a.pass_status:
        src = ("环境变量 HEALTH_BACKUP_PASS" if os.environ.get("HEALTH_BACKUP_PASS")
               else "macOS 钥匙串" if kc_get() else "未配置（会退回交互输入）")
        print("口令来源：%s" % src)
        return 0

    if a.store_pass:
        existing = kc_get()
        if existing and not a.no_confirm:
            print("⚠️ 钥匙串里已有口令，覆盖会让**已有备份全部解不开**。")
            print("   若确要轮换：先 --show-pass 记下旧口令，再重跑 --store-pass --no-confirm。")
            if not sys.stdin.isatty():
                return 2
            if input("确认覆盖？[y/N] ").strip().lower() not in ("y", "yes"):
                print("已取消")
                return 0
        p = gen_pass()
        ok, msg = kc_store(p)
        print(("✓ " if ok else "✗ ") + msg)
        if not ok:
            print("  退路：把下面这串存进你自己的密码管理器，并每次用 "
                  "HEALTH_BACKUP_PASS=… 调用。", file=sys.stderr)
            print(p)
            return 1
        print("\n现在把下面的口令离线抄一份（之后可用 --show-pass 再取）：")
        print("  %s" % p)
        print("\n⚠️ 覆盖/丢失钥匙串 = 已有备份永久解不开。轮换前先解密验证旧备份。")
        return 0

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
