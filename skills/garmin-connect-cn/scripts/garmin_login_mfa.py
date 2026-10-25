#!/usr/bin/env python3
"""Garmin Connect CN — 支持 MFA 的一次性登录。

用法:
    garmin_login_mfa.py <email> <password> <mfa_code>

登录成功后:
  1. 凭据写入 ~/.config/garmin-cn/credentials.json (600)
  2. garth token 落盘到 ~/.garth，供后续 garmin_cli.py 免 MFA 复用
"""
import json
import os
import shutil
import sys

from garminconnect import Garmin

CONFIG_DIR = os.path.expanduser("~/.config/garmin-cn")
CREDENTIALS_FILE = os.path.join(CONFIG_DIR, "credentials.json")
TOKEN_DIR = os.path.expanduser("~/.garth")


def main():
    if len(sys.argv) != 4:
        print(json.dumps({"status": "error", "message": "用法: garmin_login_mfa.py <email> <password> <mfa_code>"}))
        return 1

    email, password, mfa_code = sys.argv[1], sys.argv[2], sys.argv[3]

    # 清掉旧 SSO 缓存，避免过期 token 干扰
    shutil.rmtree(TOKEN_DIR, ignore_errors=True)

    client = Garmin(email, password, is_cn=True, prompt_mfa=lambda: mfa_code)
    try:
        client.login(tokenstore=TOKEN_DIR)
    except Exception as exc:
        print(json.dumps({"status": "error", "message": f"登录失败: {exc}"}, ensure_ascii=False))
        return 1

    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CREDENTIALS_FILE, "w", encoding="utf-8") as f:
        json.dump({"email": email, "password": password}, f)
    os.chmod(CREDENTIALS_FILE, 0o600)

    print(json.dumps({
        "status": "success",
        "message": f"已登录: {email} (Garmin Connect CN)",
        "display_name": getattr(client, "display_name", None),
        "tokenstore": TOKEN_DIR,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
