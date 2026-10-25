# -*- coding: utf-8 -*-
"""Garmin 时间戳统一换算（body-health 技能唯一时间工具，禁止别处手搓）。

两条硬规则（2026-09-06 实测翻车后固化为代码，勿凭记忆重写）：
1. `*TimestampLocal` / `*TimestampGMT` 混用，语义完全不同：
   - `*TimestampGMT`：真正的 UTC epoch，本地钟面 = epoch + 8h。
   - `*TimestampLocal`：**「当地挂钟时间按 UTC 存」**——钟面值直接编码成
     UTC epoch。按 UTC 取钟面即可；用本机时区再转一次会 +8h
     （实测把入睡 02:27 显示成 10:27）。
2. 沙箱机器时区不可信：全模块禁止 datetime.fromtimestamp()，
   一律用 UTC 锚定法（utcfromtimestamp / epoch + timedelta）。

自验证：python3 gtime.py  —— 内置 2026-09-06 真实数据回归用例，改动必跑。
"""
import datetime

TZ = 28800  # 本地 UTC+8（北京）
_EPOCH = datetime.datetime(1970, 1, 1)


def local_ms_to_hhmm(ms):
    """`*TimestampLocal`（挂钟按 UTC 存）→ "HH:MM"。"""
    if not ms:
        return None
    return (_EPOCH + datetime.timedelta(seconds=ms / 1000)).strftime("%H:%M")


def gmt_ms_to_local_hhmm(ms):
    """`*TimestampGMT`（真 UTC epoch）→ 本地 "HH:MM"（+8h）。"""
    if not ms:
        return None
    return (_EPOCH + datetime.timedelta(seconds=ms / 1000 + TZ)).strftime("%H:%M")


def iso_gmt_to_hhmm(iso):
    """ISO GMT 时间串（如 sleepLevels 的 startGMT）→ 本地 "HH:MM"（+8h）。"""
    if not iso:
        return None
    try:
        base = datetime.datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
        return (_EPOCH + (base - _EPOCH) + datetime.timedelta(seconds=TZ)
                ).strftime("%H:%M")
    except Exception:
        return None


if __name__ == "__main__":
    # 回归用例：2026-09-06 raw 实测值（入睡 02:27 / 醒 10:00）
    START_LOCAL, END_LOCAL = 1788661620000, 1788688843000
    START_GMT, END_GMT = 1788632820000, 1788660043000
    assert local_ms_to_hhmm(START_LOCAL) == "02:27", local_ms_to_hhmm(START_LOCAL)
    assert local_ms_to_hhmm(END_LOCAL) == "10:00", local_ms_to_hhmm(END_LOCAL)
    # 两类字段指向同一时刻，钟面必须一致
    assert gmt_ms_to_local_hhmm(START_GMT) == local_ms_to_hhmm(START_LOCAL)
    assert gmt_ms_to_local_hhmm(END_GMT) == local_ms_to_hhmm(END_LOCAL)
    # ISO 串与 ms 两条路指向同一时刻，钟面必须一致
    assert iso_gmt_to_hhmm("2026-09-05T18:27:00.0") == "02:27", \
        iso_gmt_to_hhmm("2026-09-05T18:27:00.0")
    # 空值不抛异常
    assert local_ms_to_hhmm(None) is None and gmt_ms_to_local_hhmm(0) is None
    assert iso_gmt_to_hhmm(None) is None and iso_gmt_to_hhmm("垃圾") is None
    print("gtime.py: 8 项断言全部通过（含 9/6 真实数据回归用例）")
