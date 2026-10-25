#!/usr/bin/env python3
"""用 Edge 里解出的 JWT_WEB 直连 Garmin Connect CN，验证并落盘 token。

不经过 SSO 登录（也就不会触发 MFA）：JWT_WEB 是浏览器会话 Cookie，
直接当鉴权头用。验证通过后把 token dump 到 ~/.config/garmin-cn/tokens，
之后 garmin_cli.py 的 get_client() 走 tokenstore 复用，永久免验证码。
"""
import json
import os
import sys

from garminconnect import Garmin

TOKEN_DIR = os.path.expanduser("~/.config/garmin-cn/tokens")
JWT_PATH = "/tmp/garmin_jwt_web.txt"

jwt = open(JWT_PATH).read().strip()
print(f"JWT_WEB 长度: {len(jwt)}")

g = Garmin(None, None, is_cn=True)
c = g.client
c.jwt_web = jwt
c.csrf_token = ""  # 让 JWT_WEB 回退分支直接用 Cookie 头

# 1) 验证身份
try:
    prof = c.connectapi("/userprofile-service/socialProfile")
except Exception as exc:
    print(json.dumps({"status": "error", "stage": "verify", "detail": str(exc)[:400]},
                     ensure_ascii=False))
    sys.exit(1)

if not isinstance(prof, dict) or "displayName" not in prof:
    print(json.dumps({"status": "error", "stage": "verify", "detail": "无有效 profile"},
                     ensure_ascii=False))
    sys.exit(1)

print("登录用户:", prof.get("displayName"), "|", prof.get("fullName", ""))

g.display_name = prof.get("displayName")
g.full_name = prof.get("fullName", "")

# 2) 顺带拉一下用户设置，确认 API 全通
try:
    settings = c.connectapi(g.garmin_connect_user_settings_url)
    print("user-settings:", "OK" if isinstance(settings, dict) else type(settings))
except Exception as exc:
    print("user-settings 失败:", str(exc)[:200])

# 3) 落盘 token，供 garmin_cli.py 复用
os.makedirs(TOKEN_DIR, exist_ok=True)
try:
    c.dump(TOKEN_DIR)
    print("token 已落盘:", TOKEN_DIR, sorted(os.listdir(TOKEN_DIR)))
except Exception as exc:
    print("dump 失败:", str(exc)[:300])

print(json.dumps({
    "status": "success",
    "display_name": g.display_name,
    "message": "JWT_WEB 会话可用",
}, ensure_ascii=False))
