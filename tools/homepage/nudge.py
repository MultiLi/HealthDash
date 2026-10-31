# -*- coding: utf-8 -*-
"""主观补数提示（2026-11-01 建）。

**要解决的问题不是「忘记录」，是「开口的成本」。**
真实现状：能为设备自动采的一直在跑（跨度 60 天里 37 天有实测），
要靠人主动说一句的全部断了（`daily_log.jsonl` 只有 6 行，最后一条 09-16）。
这不是自律问题——补一句「精力 4 分」得先打开对话、找到 agent、说完整一段话，
摩擦远大于收益。所以把「还缺哪几项」摆到页面上，让开口变成一次复制粘贴。

**为什么只读不写**：主页是公开站点（`index.json` 本身就是全量健康数据），
在上面开一个写接口等于给任何人一个往主观日志里写字的洞。所以这里只做
「缺什么 + 一句话模板」，写入仍然走 agent。这是有意的取舍，不是没做完。
"""
import json
import os
from datetime import date

from homepage.paths import DAILY_LOG

# 每日该有的三项主观标量。**清单只放真正被下游判读消费的字段**：
# 每天问一堆没人回答的问题，只会让人连最该答的三项也一起跳过。
DAILY_FIELDS = (
    ("energy", "精力"),
    ("soreness", "酸痛"),
    ("sleep_quality", "睡眠自评"),
)
# 体重不是每日必填（同条件周均口径），但断太久就没法判趋势
WEIGHT_FIELD = ("weight_kg", "晨起空腹体重")
WEIGHT_STALE_DAYS = 7


def _rows():
    out = []
    if not os.path.isfile(DAILY_LOG):
        return out
    with open(DAILY_LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("date"):
                out.append(r)
    out.sort(key=lambda r: r["date"])
    return out


def _has(row, key):
    """该字段是否**真的答了**。

    null 与缺席一律算没答：`weight_kg: null` 的意思是「今天没称」，
    不是「体重是 0」。把 null 当有效值会让「断更」看起来像「有记录」。
    """
    return row is not None and row.get(key) is not None


def _days_between(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def nudge_block(today=None):
    """{date, missing, since, gap_days, weight_stale_days, prompt}。

    全为空（今天三项都答了、体重也不旧）时前端不显示任何东西——
    这个块是提醒，不是常驻面板。
    """
    today = today or date.today().isoformat()
    rows = _rows()
    by_date = {r["date"]: r for r in rows}
    trow = by_date.get(today)

    missing = [{"key": k, "label": lab} for k, lab in DAILY_FIELDS if not _has(trow, k)]

    last = rows[-1]["date"] if rows else None
    # 「断了多久」按**最后一次真正落库的日子**算，不按最后一次访问算。
    gap = _days_between(last, today) if last else None

    # 体重单独看：它不要求每天量，但太久没量就看不出趋势
    wdays = None
    for r in reversed(rows):
        if _has(r, "weight_kg"):
            wdays = _days_between(r["date"], today)
            break

    weight_stale = (wdays is None) or (wdays > WEIGHT_STALE_DAYS)
    if weight_stale and not any(m["key"] == WEIGHT_FIELD[0] for m in missing):
        missing.append({"key": WEIGHT_FIELD[0], "label": WEIGHT_FIELD[1]})

    if not missing:
        return None

    labels = "、".join(m["label"] for m in missing)
    prompt = ("%s 补数：精力 ?/5、酸痛 ?/5、睡眠自评 ?/5" % today)
    if weight_stale:
        prompt += "、晨起体重 ?kg"
    return {
        "date": today,
        "missing": missing,
        "labels": labels,
        "last_logged": last,
        "gap_days": gap,
        "weight_stale_days": wdays,
        "prompt": prompt,
    }
