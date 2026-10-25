#!/usr/bin/env python3
"""Garmin CN 官方 /gc-api 直连客户端（2026-09-04 验证通过）。

链路：Edge cookie 解密(取 CASMFA) → CASMFA 免短信签票 → 消费 ticket 拿
JWT_WEB+session → /modern 取 csrf → 全套浏览器头(Sec-Fetch-*)打 /gc-api。
关键点：缺 Sec-Fetch-* 头会被 Cloudflare 403（空 body）；只有带上才能过。

用法：
  uv run --python 3.11 --with curl_cffi python garmin_gc_api.py get <gc-api路径>
  uv run --python 3.11 --with curl_cffi python garmin_gc_api.py pull \
      --start 2026-08-05 --end 2026-09-04 --out /tmp/out.json
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

from curl_cffi import requests as creq

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0")
# 会话缓存放用户配置目录（/tmp 重启即失，会导致每次开机首跑都走完整登录链路）
SESS_FILE = os.path.expanduser("~/.config/garmin-cn/gc_session.json")
COOKIE_DB = os.path.expanduser(
    "~/Library/Application Support/Microsoft Edge/Default/Cookies")

FULL_HEADERS = {
    "User-Agent": UA, "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://connect.garmin.cn/modern/",
    "Origin": "https://connect.garmin.cn",
    "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Sec-CH-UA": '"Microsoft Edge";v="130", "Chromium";v="130", "Not?A_Brand";v="24"',
    "Sec-CH-UA-Mobile": "?0", "Sec-CH-UA-Platform": '"macOS"',
}

# CN 端点地图（从 5.28.0.26 前端 chunk 挖出，backend=gcapi → /gc-api 网关）
ENDPOINTS = {
    "sleep_day": "sleep-service/sleep/dailySleepData?date={date}",
    "hrv": "hrv-service/hrv/{date}",
    "bb_events": "wellness-service/wellness/bodyBattery/events/{date}",
    "bb_reports": "wellness-service/wellness/bodyBattery/reports/daily?startDate={start}&endDate={end}",  # 含 charged/drained + 全天曲线
    "bb_range": "usersummary-service/stats/bodybattery/daily/{start}/{end}",  # ≤28天
    "sleep_range": "wellness-service/stats/sleep/daily/{start}/{end}",  # ≤28天
    "activities": "activitylist-service/activities/search/activities?limit={limit}&start=1",
    "respiration": "wellness-service/wellness/daily/respiration/{date}",
    "spo2_acclimation": "wellness-service/wellness/daily/spo2acclimation/{date}",  # 高原适应，非血氧读数
    "spo2": "wellness-service/wellness/daily/spo2/{date}",  # 真血氧读数（需手表开启血氧检测才有值）
    "stress": "wellness-service/wellness/dailyStress/{date}",
    "stress_range": "usersummary-service/stats/stress/daily/{start}/{end}",
    "dailyhr": "wellness-service/wellness/dailyHeartRate?date={date}",  # 必须 query 风格；路径风格 403
    "dailyhr_path": "/wellness-service/wellness/dailyHeartRate",  # 通道 B（garth 直连）用路径风格，与上一条等价异形
    # ↓ 2026-09-05 逆向报告页新增（query 风格全通，双通道均 200）
    "usersummary_daily": "usersummary-service/usersummary/daily?calendarDate={date}",
    "summary_chart": "wellness-service/wellness/dailySummaryChart?date={date}",
    "weight": "userprofile-service/userprofile/personal-information/weight",
    "personal_info": "userprofile-service/userprofile/personal-information",
    "hydration": "usersummary-service/usersummary/hydration/daily/{date}",  # 路径风格有效
    "readiness": "metrics-service/metrics/trainingreadiness/{date}",
    "readiness_range": "metrics-service/metrics/trainingreadiness/{start}/{end}",
    "ts_daily": "metrics-service/metrics/trainingstatus/daily/{date}",
    "ts_aggregated": "metrics-service/metrics/trainingstatus/aggregated/{date}",
    "vo2max_latest": "metrics-service/metrics/maxmet/latest/{date}",
    "vo2max_range": "metrics-service/metrics/maxmet/daily/{start}/{end}",
    "capable": "userprofile-service/userprofile/capableEnable/{name}",
    "settings": "userprofile-service/userprofile/settings",
}

_gcache = {}


class _NR(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _ufetch(url, data=None, headers=None, follow=True):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    op = (urllib.request.build_opener() if follow
          else urllib.request.build_opener(_NR()))
    try:
        with op.open(req, timeout=30) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def edge_sso_cookies():
    """解密 Edge cookie，返回 sso.garmin.cn 的 cookie dict（重点 CASMFA）。"""
    if "sso" in _gcache:
        return _gcache["sso"]
    import sqlite3, tempfile, shutil
    tmp = tempfile.mktemp(suffix=".db")
    shutil.copy(COOKIE_DB, tmp)
    pw = subprocess.run(
        ["security", "find-generic-password", "-w",
         "-s", "Microsoft Edge Safe Storage"],
        capture_output=True, text=True).stdout.strip()
    key = hashlib.pbkdf2_hmac("sha1", pw.encode(), b"saltysalt", 1003, 16)
    iv = (b" " * 16).hex()
    out = {}
    con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    for host, name, val, enc in con.execute(
            "SELECT host_key,name,value,encrypted_value FROM cookies "
            "WHERE host_key LIKE '%garmin%'"):
        v = val
        if not v and enc[:3] in (b"v10", b"v11"):
            blob = enc[3:]
            r = subprocess.run(["openssl", "enc", "-d", "-aes-128-cbc",
                                "-K", key.hex(), "-iv", iv],
                               input=blob, capture_output=True)
            v = r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""
        if not v:
            continue
        out.setdefault(host, {})[name] = v
    con.close()
    os.unlink(tmp)
    sso = {k: v for h, kv in out.items()
           for k, v in kv.items() if "sso.garmin.cn" in h}
    _gcache["sso"] = sso
    return sso


def build_session():
    """CASMFA 签票 → 消费 → csrf。返回 {"cookies": dict, "csrf": str}"""
    sso = edge_sso_cookies()
    ck = "; ".join(f"{k}={v}" for k, v in sso.items())
    svc = "https://connect.garmin.cn/app"
    code, hdrs, body = _ufetch(
        "https://sso.garmin.cn/mobile/api/login?clientId=GarminConnect&locale=en-US&service="
        + urllib.parse.quote(svc, safe=""),
        data=json.dumps({"username": _cred()["email"], "password": _cred()["password"],
                         "rememberMe": True, "captchaToken": ""}).encode(),
        headers={"Cookie": ck, "Accept": "application/json",
                 "Content-Type": "application/json",
                 "Origin": "https://sso.garmin.cn",
                 "Referer": "https://sso.garmin.cn/"})
    j = json.loads(body)
    assert j.get("responseStatus", {}).get("type") == "SUCCESSFUL", body[:200]
    jar = {}
    cur = f"{svc}?ticket={urllib.parse.quote(j['serviceTicketId'])}"
    for _ in range(8):
        code, hdrs, _ = _ufetch(
            cur, headers={"Cookie": "; ".join(f"{k}={v}" for k, v in jar.items())},
            follow=False)
        for k, v in hdrs.items():
            if k.lower() == "set-cookie":
                m = re.match(r"\s*([^=;]+)=([^;]*)", v)
                if m:
                    if m.group(2):
                        jar[m.group(1)] = m.group(2)
                    else:
                        jar.pop(m.group(1), None)
        loc = hdrs.get("Location")
        if code in (301, 302, 303, 307) and loc:
            cur = urllib.parse.urljoin(cur, loc)
        else:
            break
    s = creq.Session(impersonate="chrome")
    r = s.get("https://connect.garmin.cn/modern/",
              headers={"Cookie": "; ".join(f"{k}={v}" for k, v in jar.items())}, timeout=30)
    m = re.search(r'csrf-token" content="([^"]+)"', r.text)
    merged = dict(jar)
    merged.update(s.cookies.get_dict())
    sess = {"cookies": merged, "csrf": m.group(1) if m else ""}
    json.dump(sess, open(SESS_FILE, "w"), indent=2)
    return sess


def _cred():
    if "cred" not in _gcache:
        p = os.path.expanduser("~/.config/garmin-cn/credentials.json")
        _gcache["cred"] = json.load(open(p))
    return _gcache["cred"]


def load_session():
    if os.path.exists(SESS_FILE):
        return json.load(open(SESS_FILE))
    return build_session()


def get(path, sess=None):
    """GET /gc-api/<path>，自动 401 重建会话。返回 (status, json|str)。"""
    sess = sess or load_session()
    s = creq.Session(impersonate="chrome")
    H = dict(FULL_HEADERS)
    H["Cookie"] = "; ".join(f"{k}={v}" for k, v in sess["cookies"].items())
    H["Connect-Csrf-Token"] = sess["csrf"]
    r = s.get("https://connect.garmin.cn/gc-api/" + path, headers=H, timeout=30)
    if r.status_code == 401:
        sess = build_session()
        return get(path, sess)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text[:300]


GARTH_TOKEN_DIR = os.path.expanduser("~/.config/garmin-cn/garth")


def garth_get(path, params=None):
    """通道 B：garth 已存 token 直连（token 在 ~/.config/garmin-cn/garth）。
    只加载已有 token，绝不二次登录。返回 (status, json|None)。
    通道 B 的实现只住这里——采集层只做调用策略，不碰鉴权细节。"""
    try:
        from garth import Client
        c = Client(domain="garmin.cn")
        c.load(GARTH_TOKEN_DIR)
        c.sess.trust_env = False
        data = c.connectapi(path, params=params)
        return 200, data
    except Exception as exc:
        return 0, {"error": str(exc)[:200]}


def pull(start, end):
    """区间+按天全量拉取（≤31 天）。"""
    import datetime
    d0, d1 = datetime.date.fromisoformat(start), datetime.date.fromisoformat(end)
    days = [(d0 + datetime.timedelta(days=i)).isoformat()
            for i in range((d1 - d0).days + 1)]
    out = {"range": [start, end]}
    # 只拉下游真正消费的端点（respiration/vo2max_range/bb_reports 拉了没人用，已删）
    for name, ep in [("sleep_daily", ENDPOINTS["sleep_day"]),
                     ("hrv_daily", ENDPOINTS["hrv"]),
                     ("stress_daily", ENDPOINTS["stress"]),
                     ("usersummary_daily", ENDPOINTS["usersummary_daily"]),
                     ("summary_chart", ENDPOINTS["summary_chart"]),
                     ("spo2_daily", ENDPOINTS["spo2"]),
                     ("readiness_daily", ENDPOINTS["readiness"]),
                     ("ts_daily", ENDPOINTS["ts_daily"])]:
        arr = []
        for dt in days:
            c, b = get(ep.format(date=dt))
            if c == 200 and b:
                if isinstance(b, dict):
                    b.setdefault("calendarDate", dt)
                else:                      # 数组型响应（如 summary_chart）
                    b = {"calendarDate": dt, "values": b}
                arr.append(b)
        out[name] = arr
    for name, ep in [("bb_range", ENDPOINTS["bb_range"]),
                     ("stress_range", ENDPOINTS["stress_range"])]:
        # 28 天上限，分块
        data = []
        i = 0
        while i < len(days):
            chunk = days[i:i + 28]
            c, b = get(ep.format(start=chunk[0], end=chunk[-1]))
            if c == 200 and isinstance(b, list):
                data += b
            i += 28
        out[name] = data
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["get", "pull", "refresh"])
    ap.add_argument("path", nargs="?")
    ap.add_argument("--start"); ap.add_argument("--end"); ap.add_argument("--out")
    a = ap.parse_args()
    if a.cmd == "refresh":
        build_session(); print("session refreshed ->", SESS_FILE); return
    if a.cmd == "get":
        assert a.path, "需要 gc-api 路径"
        c, b = get(a.path); print(json.dumps({"status": c, "data": b}, ensure_ascii=False)[:2000]); return
    data = pull(a.start, a.end)
    if a.out:
        json.dump(data, open(a.out, "w"), ensure_ascii=False, indent=1)
        print("saved", a.out)
    else:
        print(json.dumps(data, ensure_ascii=False)[:2000])


if __name__ == "__main__":
    main()
