#!/usr/bin/env python3
"""Garmin Connect CN — 两阶段 MFA 登录（后台常驻，等待验证码）。

背景
----
佳明账号开启二次验证后，每次用账号密码发起登录都会往邮箱/手机发一个新验证码，
并立即作废上一个。因此「跑一次脚本 → 拿码 → 再跑一次」必然失败：第二次运行
触发的新挑战会把手上那个码废掉。

本脚本把两个阶段放进**同一个进程**：
  阶段 1  发起登录 → 佳明发出验证码 → 进程挂起轮询
  阶段 2  检测到验证码文件 → 用同一个会话提交 → 落盘 token

用法
----
    # 后台启动，进程会写出状态文件并等待验证码
    python garmin_mfa_login.py <email> <password>

    # 用户收到验证码后，写入轮询文件，进程自动继续
    echo -n '123456' > /tmp/garmin_cn_mfa_code

产出
----
  ~/.config/garmin-cn/credentials.json  账号凭据（600）
  ~/.config/garmin-cn/tokens/           SSO token，供 garmin_cli.py 复用
  /tmp/garmin_cn_mfa_status.json         实时状态（供外部查看进度）
  /tmp/garmin_cn_mfa_result.json         最终结果
"""
import json
import os
import shutil
import sys
import time

from garminconnect import Garmin

CONFIG_DIR = os.path.expanduser("~/.config/garmin-cn")
CREDENTIALS_FILE = os.path.join(CONFIG_DIR, "credentials.json")
TOKEN_DIR = os.path.join(CONFIG_DIR, "tokens")

CODE_FILE = "/tmp/garmin_cn_mfa_code"
STATUS_FILE = "/tmp/garmin_cn_mfa_status.json"
RESULT_FILE = "/tmp/garmin_cn_mfa_result.json"

WAIT_SECONDS = 600  # 单轮最长等待 10 分钟
POLL_INTERVAL = 2
MAX_ROUNDS = 3      # 最多发起 3 轮挑战（每轮会发一枚新验证码）

# 网络层会间歇性出现 "CONNECT tunnel failed, response 502"（沙箱透明代理抖动），
# 这类错误重试即可成功；而验证码错误/会话过期重试无意义，必须立即放弃。
FATAL_MFA_MARKERS = (
    "MFA_CODE_INVALID",
    "codeEntryInvalid",
    "SESSION_EXPIRED",
    "sessionHasExpired",
    "INVALID_USERNAME_PASSWORD",
)


def is_fatal_mfa_error(exc_text):
    return any(m in exc_text for m in FATAL_MFA_MARKERS)


def retry_on_network_error(fn, what, attempts=8, base_delay=6):
    """网络抖动重试；认证类错误立即抛出。"""
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last = exc
            text = str(exc)
            if is_fatal_mfa_error(text):
                raise
            if i < attempts - 1:
                delay = base_delay * (i + 1)
                write_json(STATUS_FILE, {
                    "stage": f"{what}_retry",
                    "attempt": i + 1,
                    "of": attempts,
                    "error": text[:200],
                    "next_in_seconds": delay,
                })
                time.sleep(delay)
    raise last


def write_json(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def save_credentials(email, password):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CREDENTIALS_FILE, "w", encoding="utf-8") as f:
        json.dump({"email": email, "password": password}, f)
    os.chmod(CREDENTIALS_FILE, 0o600)


def finish_login(client, email, password):
    """MFA 提交成功后：加载 profile、落盘 token、写凭据。"""
    os.makedirs(TOKEN_DIR, exist_ok=True)
    with open(os.path.join(TOKEN_DIR, ".keep"), "w", encoding="utf-8") as f:
        f.write("")

    # 复刻 Garmin.login() 在 MFA 成功后的收尾动作
    # （return_on_mfa 模式下这些步骤被跳过了）
    try:
        with open(os.devnull, "w"):
            pass
        client.client.dump(TOKEN_DIR)
    except Exception as exc:
        write_json(STATUS_FILE, {"stage": "token_dump_failed", "detail": str(exc)})

    display_name = None
    try:
        prof = client.client.connectapi("/userprofile-service/socialProfile")
        if isinstance(prof, dict):
            display_name = prof.get("displayName")
            client.display_name = display_name
            client.full_name = prof.get("fullName", "")
    except Exception:
        pass

    try:
        client.client.connectapi(client.garmin_connect_user_settings_url)
    except Exception:
        pass

    save_credentials(email, password)
    return display_name


def main():
    if len(sys.argv) != 3:
        print("用法: garmin_mfa_login.py <email> <password>")
        return 2

    email, password = sys.argv[1], sys.argv[2]

    for p in (CODE_FILE, RESULT_FILE):
        if os.path.exists(p):
            os.remove(p)

    # 清掉旧 token，确保走一次完整登录
    shutil.rmtree(TOKEN_DIR, ignore_errors=True)

    write_json(STATUS_FILE, {"stage": "starting", "email": email})

    # ── 多轮挑战：一枚码不对就自动发起下一枚，直到成功或用尽轮次 ──
    # 佳明每次发起登录挑战都会作废上一枚验证码，所以「拿到新码 → 立刻提交」
    # 必须在同一个进程里完成；失败后重新挑战即可拿到一枚全新的码。
    last_err = None
    for round_no in range(1, MAX_ROUNDS + 1):
        outcome = run_one_round(email, password, round_no, MAX_ROUNDS)
        if outcome == "ok":
            return 0
        if outcome == "timeout":
            write_json(RESULT_FILE, {
                "status": "error",
                "message": f"第 {round_no} 轮等待验证码超时",
            })
            return 1
        # 认证失败（验证码无效/会话过期）→ 清掉旧码，发起下一轮新挑战
        last_err = outcome
        if os.path.exists(CODE_FILE):
            os.remove(CODE_FILE)
        write_json(STATUS_FILE, {
            "stage": "rechallenging",
            "failed_round": round_no,
            "detail": str(outcome)[:300],
            "message": "上一枚验证码无效，正在发起新一轮挑战，请等待新邮件",
        })

    write_json(STATUS_FILE, {"stage": "exhausted"})
    write_json(RESULT_FILE, {
        "status": "error",
        "message": f"{MAX_ROUNDS} 轮验证码均未通过: {last_err}",
    })
    return 1


def run_one_round(email, password, round_no, max_rounds):
    """发起一次挑战并等待提交验证码。返回 'ok' | 'timeout' | Exception。"""
    client = Garmin(email, password, is_cn=True, return_on_mfa=True)
    try:
        # 网络抖动（502/隧道失败）在这里重试是安全的：
        # 请求没到达佳明就不会发出验证码。
        status, _state = retry_on_network_error(
            client.login, "login", attempts=6, base_delay=5
        )
    except Exception as exc:
        return exc

    if status != "needs_mfa":
        # 未触发 MFA，直接登录成功
        display_name = finish_login(client, email, password)
        write_json(STATUS_FILE, {"stage": "done", "mfa": False})
        write_json(RESULT_FILE, {
            "status": "success", "mfa": False,
            "message": "已登录（未触发二次验证）",
            "display_name": display_name,
        })
        return "ok"

    # ── 阶段 1 完成：验证码已发出，等待用户回填 ──
    challenge_ts = time.time()
    mfa_method = getattr(client.client, "_mfa_method", None)
    mfa_flow = getattr(client.client, "_mfa_flow", None)
    write_json(STATUS_FILE, {
        "stage": "waiting_for_code",
        "round": round_no,
        "of_rounds": max_rounds,
        "email": email,
        "code_file": CODE_FILE,
        "mfa_method": mfa_method,
        "mfa_flow": mfa_flow,
        "challenge_issued_at": challenge_ts,
        "challenge_issued_local": time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(challenge_ts)
        ),
        "message": "新验证码已发出，请用【最新一封】邮件里的码",
    })
    print(json.dumps({
        "status": "needs_mfa",
        "round": round_no,
        "mfa_method": mfa_method,
        "message": f"第 {round_no}/{max_rounds} 轮验证码已发出，等待写入 {CODE_FILE}",
    }, ensure_ascii=False), flush=True)

    code = wait_for_code(WAIT_SECONDS)
    if not code:
        write_json(STATUS_FILE, {"stage": "timeout", "round": round_no})
        return "timeout"

    # ── 阶段 2：用同一会话提交验证码 ──
    # 会话仍活在本进程内，网络抖动时重试不会作废验证码。
    #
    # 佳明对 mfaMethod 的取值很挑：库里取的是 customerMfaInfo.mfaLastMethodUsed
    # （可能是 phone / email / sms / authenticator），一旦与实际投递渠道不符，
    # 即便验证码本身正确也会返回 MFA_CODE_INVALID。
    # 会话在同一进程内仍然有效，所以逐一试各种方法，不必重新发码。
    # 默认先按 email 渠道提交：佳明返回的 mfaLastMethodUsed 可能是 phone，
    # 但短信在国内经常收不到，用户实际拿到的是邮件里的码，渠道对不上就会
    # 一直返回 MFA_CODE_INVALID。可用环境变量 GARMIN_MFA_METHODS 覆盖顺序。
    env_methods = os.getenv("GARMIN_MFA_METHODS")
    if env_methods:
        methods = [m.strip() for m in env_methods.split(",") if m.strip()]
    else:
        methods = []
        for m in ("email", "phone", "sms"):
            if m and m not in methods:
                methods.append(m)
        if mfa_method and mfa_method not in methods:
            methods.append(mfa_method)

    errs = []
    for idx, method in enumerate(methods, start=1):
        write_json(STATUS_FILE, {
            "stage": "submitting_code",
            "round": round_no,
            "mfa_method": method,
            "attempt": idx,
            "of_methods": len(methods),
        })
        client.client._mfa_method = method
        try:
            retry_on_network_error(
                lambda: client.client.resume_login(None, code),
                "mfa_submit",
                attempts=4,
                base_delay=5,
            )
            break  # 成功
        except Exception as exc:
            errs.append(f"[{method}] {exc}")
            if is_fatal_mfa_error(str(exc)):
                continue  # 该方法不匹配，换下一个
            return exc     # 其他错误直接向上抛
    else:
        combined = Exception(" | ".join(e[:200] for e in errs))
        write_json(STATUS_FILE, {
            "stage": "mfa_failed",
            "round": round_no,
            "tried_methods": methods,
            "detail": str(combined)[:400],
        })
        return combined

    display_name = finish_login(client, email, password)
    write_json(STATUS_FILE, {"stage": "done", "mfa": True})
    write_json(RESULT_FILE, {
        "status": "success",
        "mfa": True,
        "message": f"已登录: {email} (Garmin Connect CN)",
        "display_name": display_name,
        "tokenstore": TOKEN_DIR,
    })
    return "ok"


def wait_for_code(timeout):
    """轮询等待验证码文件出现且非空，返回 code 或 None。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(CODE_FILE):
            try:
                raw = open(CODE_FILE, encoding="utf-8").read().strip()
            except Exception:
                raw = ""
            if raw:
                return raw
        time.sleep(POLL_INTERVAL)
    return None


if __name__ == "__main__":
    sys.exit(main())
