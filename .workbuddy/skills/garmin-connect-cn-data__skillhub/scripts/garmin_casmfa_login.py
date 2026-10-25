#!/usr/bin/env python3
"""免验证码登录（CASMFA）→ 消费 ticket 拿 JWT_WEB → 验证官方 API。"""
import json
import os
import urllib.request
import urllib.error

import requests
from garminconnect import Garmin

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0")
TOKEN_DIR = "/Users/tian.li/.config/garmin-cn/tokens"

d = json.load(open("/tmp/garmin_edge_cookies.json"))
sso = {}
for host, kv in d.items():
    if host in ("sso.garmin.cn", ".sso.garmin.cn"):
        sso.update(kv)
sso_cookie = "; ".join(f"{k}={v}" for k, v in sso.items())
creds = {"email": "litian9308@gmail.com", "password": "Lidaye11"}


def sso_login():
    url = ("https://sso.garmin.cn/mobile/api/login"
           "?clientId=GCM_IOS_DARK&locale=en-US"
           "&service=https%3A%2F%2Fconnect.garmin.cn%2Fapp")
    req = urllib.request.Request(url, data=json.dumps({
        "username": creds["email"], "password": creds["password"],
        "rememberMe": True, "captchaToken": "",
    }).encode(), method="POST", headers={
        "Cookie": sso_cookie, "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://sso.garmin.cn", "Referer": "https://sso.garmin.cn/",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"登录失败 HTTP {e.code}: {e.read()[:200]}")


resp = sso_login()
rt = resp.get("responseStatus", {}).get("type")
ticket = resp.get("serviceTicketId")
print("登录状态:", rt, "| ticket:", (ticket or "")[:24])
if rt != "SUCCESSFUL" or not ticket:
    raise SystemExit("未拿到票据")

# 消费 ticket（库的 fallback 路径有 bug，这里手动做）
s = requests.Session()
s.headers["User-Agent"] = UA
r = s.get("https://connect.garmin.cn/app", params={"ticket": ticket},
          allow_redirects=True, timeout=30)
print("消费 ticket:", r.status_code, "| 最终 URL:", r.url[:80])
jwt = None
for ck in s.cookies:
    if ck.name == "JWT_WEB":
        jwt = ck.value
if not jwt:
    jwt = s.cookies.get("JWT_WEB", domain=".connect.garmin.cn")
print("JWT_WEB:", "获取成功，长度 %d" % len(jwt) if jwt else "未获取")
if not jwt:
    raise SystemExit("没拿到 JWT_WEB")

g = Garmin(None, None, is_cn=True)
c = g.client
c.jwt_web = jwt
c.csrf_token = ""

prof = c.connectapi("/userprofile-service/socialProfile")
print("✅ API 可用，用户:", prof.get("displayName") if isinstance(prof, dict) else prof)

from datetime import date
today = date.today().isoformat()
s2 = c.connectapi(f"/usersummary-service/usersummary/daily/{today}")
keys = sorted([k for k in (s2 or {}) if any(
    x in k.lower() for x in ("battery", "hrv", "vo2", "stress", "sleep", "spo2",
                             "respiration", "training", "intensity"))])
print("官方 daily summary 关键字段(%d):" % len(keys))
for k in keys:
    print("   ", k)

os.makedirs(TOKEN_DIR, exist_ok=True)
try:
    c.dump(TOKEN_DIR)
    print("token 落盘:", sorted(os.listdir(TOKEN_DIR)))
except Exception as exc:
    print("dump 失败:", str(exc)[:150])
json.dump({"jwt_web": jwt}, open("/tmp/garmin_jwt_fresh.json", "w"))
