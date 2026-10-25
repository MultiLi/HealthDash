# -*- coding: utf-8 -*-
"""InBody（LookinBody 中国区）体成分采集器。

定位：与 sync.py 同属「网络层」，只负责把 InBody 扫描记录落盘，
不做判读、不改写报告。判读归 daily_brief / fact_pack。

数据来源：反代自 InBody Android App 的移动端 REST API（第三方实现
inbody-api-mcp，MIT）。中国区区域主机 = https://wechat.lookinbody.cn
（由 appapicommon.lookinbody.com 的 GetCountryInfoV2 按 ISO Code2=CN 解析）。

凭据：~/.config/inbody-cn/credentials.json
    {"login_id": "手机号（纯数字，不含 +86）", "password": "...", "country": "CN"}
也可用环境变量 INBODY_LOGIN_ID / INBODY_LOGIN_PW / INBODY_COUNTRY_CODE 覆盖。
⚠️ LoginID 是注册手机号，不是邮箱；填邮箱会返回 EmptyData。

会话：JWT 缓存到 ~/.config/inbody-cn/session.json，24h 内复用，过期自动重登。

产物：
    data/inbody/raw/scan_<DATETIMES>.json   单次扫描原始记录（BCA/MFA/IMP）
    data/inbody/profile.json                账号档案（身高/年龄/性别）
    data/inbody/index.json                  扫描索引（摘要，新→旧）
    data/inbody/latest.json                 最新一次扫描 + age_days 陈旧度

用法：
    python3 sync_inbody.py --check          # 只核对凭据与可达性，不落盘
    python3 sync_inbody.py                  # 全量拉取并落盘
    python3 sync_inbody.py --days 0         # 只刷最新（扫描是事件型，非常态）
"""
import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request

# 系统代理指向常不在的 127.0.0.1:1087 —— urllib 会吃系统代理而失败，强制直连
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"

COMMON_URL = "https://appapicommon.lookinbody.com"
_FULL_SYNC = "1990-01-01 11:11:11"
_UA = "okhttp/4.12.0"
_UA_DALVIK = "Dalvik/2.1.0 (Linux; U; Android 14; Pixel 6 Pro Build/AP2A.240905.003.F1)"
_ENVELOPE = {
    "Language": "en",
    "LogCode": "",
    "AppType": "Android",
    "OSVersion": "14",
    "PhoneModel": "Google Pixel 6 Pro",
    "RegAppType": "InBody",
    "AppVersion": "2.8.31_914",
}

_HERE = os.path.dirname(os.path.abspath(__file__))            # scripts/
_SKILL_DIR = os.path.abspath(os.path.join(_HERE, ".."))       # body-health/
# scripts → body-health → skills → .workbuddy → 工作区根
from settings import ROOT as _WORKSPACE  # noqa: E402  路径唯一解析点（settings.py）
DATA_DIR = os.environ.get("HEALTH_INBODY_DIR") or os.path.join(
    _WORKSPACE, "data", "inbody")
CONF_DIR = os.path.expanduser("~/.config/inbody-cn")
CONF_FILE = os.path.join(CONF_DIR, "credentials.json")
SESSION_FILE = os.path.join(CONF_DIR, "session.json")


class InBodyError(Exception):
    pass


# ----------------------------------------------------------------------
# 凭据与会话
# ----------------------------------------------------------------------
def load_credentials():
    login_id = os.environ.get("INBODY_LOGIN_ID")
    password = os.environ.get("INBODY_LOGIN_PW")
    country = os.environ.get("INBODY_COUNTRY_CODE")
    if not (login_id and password):
        if not os.path.isfile(CONF_FILE):
            raise InBodyError(
                "缺少凭据：请创建 %s，内容形如 "
                '{"login_id":"手机号纯数字","password":"...","country":"CN"}'
                % CONF_FILE)
        with open(CONF_FILE) as fh:
            conf = json.load(fh)
        login_id = login_id or conf.get("login_id")
        password = password or conf.get("password")
        country = country or conf.get("country") or "CN"
    if not (login_id and password):
        raise InBodyError("凭据不完整（login_id / password 缺一）")
    return str(login_id).strip(), str(password), (country or "CN").strip().upper()


def _post(url, payload, headers=None, timeout=30):
    hdrs = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": _UA,
        "Accept-Encoding": "gzip",
    }
    hdrs.update(headers or {})
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise InBodyError("HTTP %s @ %s" % (e.code, url))
    except urllib.error.URLError as e:
        raise InBodyError("网络不可达 @ %s（%s）" % (url, e.reason))


def _envelope(country_code, **extra):
    return dict(_ENVELOPE, CountryCode=country_code, **extra)


# ----------------------------------------------------------------------
# 客户端
# ----------------------------------------------------------------------
class InBody:
    def __init__(self):
        self.login_id, self.password, self.country = load_credentials()
        self.api_base = None
        self.country_code = None
        self.token = None
        self.uid = None
        self._load_session()

    # --- session cache ---
    def _load_session(self):
        if not os.path.isfile(SESSION_FILE):
            return
        try:
            with open(SESSION_FILE) as fh:
                s = json.load(fh)
        except Exception:
            return
        if s.get("login_id") != self.login_id or s.get("country") != self.country:
            return
        if s.get("expires_at", 0) < time.time() + 300:
            return
        self.token = s.get("token")
        self.uid = s.get("uid")
        self.api_base = s.get("api_base")
        self.country_code = s.get("country_code")

    def _save_session(self):
        os.makedirs(CONF_DIR, exist_ok=True)
        tmp = SESSION_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({
                "login_id": self.login_id,
                "country": self.country,
                "token": self.token,
                "uid": self.uid,
                "api_base": self.api_base,
                "country_code": self.country_code,
                "expires_at": time.time() + 23 * 3600,
            }, fh)
        os.replace(tmp, SESSION_FILE)
        os.chmod(SESSION_FILE, 0o600)

    # --- host resolution ---
    def resolve_host(self):
        if self.api_base:
            return self.api_base
        data = _post(COMMON_URL + "/CommonAPI/GetCountryInfoV2",
                     _envelope("", SyncDatetime=_FULL_SYNC))
        if not data.get("IsSuccess"):
            raise InBodyError("GetCountryInfoV2 失败: %s" % data.get("ErrorMsg"))
        row = next((r for r in (data.get("Data") or [])
                    if r.get("Type") == "API"
                    and str(r.get("Code2", "")).upper() == self.country), None)
        if row is None:
            raise InBodyError("国家码 %r 无对应 API 主机（应填 ISO Code2，如 CN）"
                              % self.country)
        self.api_base = row["Domain"].rstrip("/")
        self.country_code = str(row.get("Number"))
        return self.api_base

    # --- auth ---
    def login(self):
        base = self.resolve_host()
        payload = _envelope(
            self.country_code,
            AuthProvider="", AuthProviderID="", AuthToken="", CustomKey="",
            DeviceType="Pixel 6 Pro 14",
            LoginID=self.login_id, LoginPW=self.password, Type="Login",
            SyncDatetime=_FULL_SYNC, SyncDatetimeBasalMedical=_FULL_SYNC,
            SyncDatetimeCardiac=_FULL_SYNC, SyncDatetimeExercise=_FULL_SYNC,
            SyncDatetimeInBody=_FULL_SYNC, SyncDatetimeNutrition=_FULL_SYNC,
            SyncDatetimeSleep=_FULL_SYNC,
            SyncType="Main;InBody;Exercise;Nutrition;Sleep;EasyTrainning;"
                     "BasalMedical;",
        )
        data = _post(base + "/V2/Main/GetLoginWithSyncDataPartV2", payload)
        if not data.get("IsSuccess") or not data.get("Token"):
            err = (data.get("ErrorMsg") or "").strip()
            hint = {
                "EmptyData": "账号不存在或未在此区域注册（LoginID 必须是注册手机号，"
                             "不是邮箱；中国区账号需 country=CN）",
            }.get(err, "请检查手机号/密码")
            raise InBodyError("登录失败 [%s] —— %s" % (err or "unknown", hint))
        self.token = data["Token"]
        self.uid = (data.get("Data") or {}).get("UID")
        if not self.uid:
            raise InBodyError("登录成功但未返回 UID")
        self._save_session()
        return data

    def _authed_post(self, path, payload, _retry=True):
        if not self.token:
            self.login()
        base = self.resolve_host()
        try:
            data = _post(base + path, payload, headers={
                "Authorization": "Bearer %s" % self.token,
                "User-Agent": _UA_DALVIK,
                "Accept": "application/json",
            })
        except InBodyError as e:
            if _retry and "HTTP 401" in str(e):
                self.token = None
                self.login()
                return self._authed_post(path, payload, _retry=False)
            raise
        if isinstance(data, dict) and not data.get("IsSuccess"):
            err = (data.get("ErrorMsg") or "")
            if _retry and any(k in err.lower()
                              for k in ("token", "auth", "expire")):
                self.token = None
                self.login()
                return self._authed_post(path, payload, _retry=False)
            raise InBodyError("InBody API 错误: %s" % (err or data))
        return data

    # --- data ---
    def profile(self):
        payload = _envelope(self.country_code, uid=self.uid,
                            syncDatetime=_FULL_SYNC, NumberPerData="100",
                            CurrentIndex="0", Language="en-US",
                            UseInBodyHomeDevice="false")
        data = self._authed_post("/V2/Main/GetUserInfo", payload)
        recs = data.get("Data") or []
        if not recs:
            raise InBodyError("GetUserInfo 无档案数据")
        return recs[0]

    def count(self):
        payload = _envelope(self.country_code, UID=self.uid,
                            SyncDatetimeInBody=_FULL_SYNC,
                            UseInBodyHomeDevice="false")
        data = self._authed_post("/V2/InBody/GetInBodyDataTotalCount", payload)
        return int((data.get("Data") or {}).get("InBodyDataCount", 0))

    def scans(self, number=100, index=0):
        payload = _envelope(self.country_code, uid=self.uid,
                            syncDatetime=_FULL_SYNC,
                            NumberPerData=str(number), CurrentIndex=str(index),
                            Language="en-US", UseInBodyHomeDevice="false")
        data = self._authed_post("/V2/InBody/GetInBodyData", payload)
        return data.get("Data") or []


# ----------------------------------------------------------------------
# 规范化
# ----------------------------------------------------------------------
def _num(v):
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


def _iso(raw):
    if not raw or len(str(raw)) < 14:
        return None
    s = str(raw)[:14]
    try:
        return datetime.datetime.strptime(s, "%Y%m%d%H%M%S").isoformat()
    except ValueError:
        return None


def summarize(rec):
    """把单次扫描压成摘要。字段名沿用 InBody 原生缩写，缺失即 None。

    2026-10-18 修：VFL（内脏脂肪等级）与 BMR 实际挂在 `WC` 段，
    不在 BCA/MFA —— 摘要层原先读 mfa.VFL，于是 index.json 里
    内脏脂肪一直是 null，主页永远显示不出这个最该看的数。
    `LB` 段是节段瘦体重/脂肪（左右臂、躯干、左右腿），
    比节段阻抗更可读：阻抗只有 5%/10% 这类判据，瘦体重能直接看左右差。
    """
    bca = rec.get("BCA") or {}
    mfa = rec.get("MFA") or {}
    wc = rec.get("WC") or {}
    lb = rec.get("LB") or {}
    wt = _num(bca.get("WT"))
    bfm = _num(bca.get("BFM"))

    def _lb(prefix):
        return {"right_arm": _num(lb.get(prefix + "RA")),
                "left_arm": _num(lb.get(prefix + "LA")),
                "trunk": _num(lb.get(prefix + "T")),
                "right_leg": _num(lb.get(prefix + "RL")),
                "left_leg": _num(lb.get(prefix + "LL"))}

    return {
        "datetime": _iso(rec.get("DATETIMES")),
        "raw_datetime": rec.get("DATETIMES"),
        "weight_kg": wt,
        "bmi": _num(mfa.get("BMI")),
        "percent_body_fat": _num(mfa.get("PBF")),
        "skeletal_muscle_mass_kg": _num(mfa.get("SMM")),
        "body_fat_mass_kg": bfm,
        "fat_free_mass_kg": round(wt - bfm, 1) if (wt and bfm) else None,
        "total_body_water_kg": _num(bca.get("TBW")),
        "protein_kg": _num(bca.get("PROTEIN") or bca.get("PR")),
        "mineral_kg": _num(bca.get("MINERAL") or bca.get("MN")),
        "visceral_fat_level": _num(wc.get("VFL") or wc.get("VFALevel")
                                   or mfa.get("VFL")),
        "bmr_kcal": _num(wc.get("BMR")),
        "whr": _num(mfa.get("WHR")),
        "ffmi": _num(bca.get("FFMI")),
        "segmental_lean_kg": _lb("L"),
        "segmental_fat_kg": _lb("F"),
        "equipment": bca.get("EQUIP") or rec.get("EQUIP"),
        "serial": bca.get("EQUIP_SERIAL"),
    }


def _age_days(iso_dt):
    if not iso_dt:
        return None
    d = datetime.datetime.fromisoformat(iso_dt).date()
    return (datetime.date.today() - d).days


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="InBody 体成分采集")
    ap.add_argument("--check", action="store_true", help="只登录并核对，不落盘")
    ap.add_argument("--days", type=int, default=0,
                    help="只保留最近 N 天的扫描写入 index（0=全部）")
    ap.add_argument("--number", type=int, default=0,
                    help="拉取条数上限（0=全部，自动分页）")
    args = ap.parse_args()

    try:
        client = InBody()
        client.resolve_host()
        client.login()
        prof = client.profile()
        total = client.count()
    except InBodyError as e:
        print("[inbody] ✗ %s" % e, file=sys.stderr)
        return 2

    print("[inbody] ✓ 已登录  uid=%s  host=%s  country_code=%s"
          % (client.uid, client.api_base, client.country_code))
    print("[inbody] 档案：%s  %s  %s 身高 %s cm  体重 %s kg"
          % (prof.get("Name"), prof.get("Gender"), prof.get("Age"),
             prof.get("Height"), prof.get("Weight")))
    print("[inbody] 扫描总数：%s" % total)

    if args.check:
        return 0

    if total == 0:
        print("[inbody] 账号下无任何扫描记录 —— 需要先在 InBody 体测仪上测一次"
              "（并把结果同步进 App/小程序）。")
        return 3

    recs = []
    page = 100
    idx = 0
    want = total if not args.number else min(args.number, total)
    while len(recs) < want:
        batch = client.scans(number=min(page, want - len(recs)), index=idx)
        if not batch:
            break
        recs.extend(batch)
        idx += len(batch)
        if len(batch) < page:
            break
    if not recs:
        print("[inbody] ✗ 扫描总数为 %s 但明细为空" % total, file=sys.stderr)
        return 2

    # 分页时可能重复返回同一条（CurrentIndex 语义随服务端实现），按 DATETIMES 去重
    _seen = set()
    _uniq = []
    for r in recs:
        k = r.get("DATETIMES")
        if k in _seen:
            continue
        _seen.add(k)
        _uniq.append(r)
    recs = _uniq

    os.makedirs(os.path.join(DATA_DIR, "raw"), exist_ok=True)
    for rec in recs:
        rd = rec.get("DATETIMES") or "unknown"
        with open(os.path.join(DATA_DIR, "raw", "scan_%s.json" % rd), "w") as fh:
            json.dump(rec, fh, ensure_ascii=False, indent=2)

    with open(os.path.join(DATA_DIR, "profile.json"), "w") as fh:
        json.dump({
            "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "uid": client.uid,
            "name": prof.get("Name"), "gender": prof.get("Gender"),
            "age": prof.get("Age"), "birthday": prof.get("Birthday"),
            "height_cm": _num(prof.get("Height")),
            "weight_kg": _num(prof.get("Weight")),
        }, fh, ensure_ascii=False, indent=2)

    scans = [summarize(r) for r in recs]
    scans.sort(key=lambda s: s.get("raw_datetime") or "", reverse=True)

    if args.days:
        cutoff = datetime.date.today() - datetime.timedelta(days=args.days)
        scans = [s for s in scans
                 if s.get("datetime") and
                 datetime.date.fromisoformat(s["datetime"][:10]) >= cutoff]

    with open(os.path.join(DATA_DIR, "index.json"), "w") as fh:
        json.dump({
            "updated": datetime.datetime.now().isoformat(timespec="seconds"),
            "scan_count_total": total,
            "scans": scans,
        }, fh, ensure_ascii=False, indent=2)

    latest = dict(scans[0])
    latest["age_days"] = _age_days(latest.get("datetime"))
    raw_file = os.path.join(DATA_DIR, "raw",
                            "scan_%s.json" % latest["raw_datetime"])
    if os.path.isfile(raw_file):
        with open(raw_file) as fh:
            full = json.load(fh)
        latest["BCA"] = full.get("BCA")
        latest["MFA"] = full.get("MFA")
    with open(os.path.join(DATA_DIR, "latest.json"), "w") as fh:
        json.dump(latest, fh, ensure_ascii=False, indent=2)

    print("\n%-20s %7s %7s %7s %7s %7s" %
          ("扫描时间", "体重kg", "骨骼肌", "体脂%", "体脂kg", "BMI"))
    for s in scans:
        def f(v, w=7, p=".1f"):
            return ("%" + str(w) + p) % v if v is not None else " " * (w - 1) + "-"
        print("%-20s %s %s %s %s %s"
              % (s["datetime"] or "?", f(s["weight_kg"]), f(s["skeletal_muscle_mass_kg"]),
                 f(s["percent_body_fat"]), f(s["body_fat_mass_kg"]), f(s["bmi"])))
    print("\n[inbody] 最新一次距今 %s 天；已落盘 %d 条 → %s"
          % (latest.get("age_days"), len(scans), DATA_DIR))
    return 0


if __name__ == "__main__":
    sys.exit(main())
