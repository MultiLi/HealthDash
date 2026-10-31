# 每日健康报告班（02:00）· 班次指令唯一源

> 本文件是 WorkBuddy 自动化「每日健康报告（02:00）」的**唯一指令源**：自动化 prompt 只是一个薄加载器（「读取本文件并执行」）。**改这里即生效**（下轮班次自动读到新指令），不要去改自动化配置本身。
>
> - automation id：`80d3624c-b820-491a-bc7f-b410657da46a`（rrule：每日 02:00）
> - 唯一允许的重复：收尾那行「线上主页」地址与 `tools/deploy.json` 各存一份（换域名两处都要改）。

---

每日健康报告班（02:00）—— 全体系唯一的报告班次，出前一天的报告。执行目录 /Users/tian.li/Documents/Health
（路径约定：**全部数据在 data/ 下**。报告与事实包是 data/reports/，库是 data/db/health.db，主观日志是 data/logs/。旧路径 reports/ 已在 2026-10-31 迁移，不存在了。）

【0 · 锚点解析（第一步，必做）】
python3 /Users/tian.li/Documents/Health/skills/body-health/scripts/slot.py resolve --job evening
· current_slot 恒为**前一晚 23:30**（边界不变），不是今天。目标日一律取 targets[].date，**严禁用 shell date / 运行日推导**。
· targets 为空 → 直接跳到第 5 步（本轮没报告要出）。
· 迟到不等于改期。收尾再 resolve 一次确认清零。

【1 · 拉数（报告的第一步，不是独立任务）】
照 target.collect_cmd 执行。它一条命令做完全部采集：佳明当日 + 回补近 3 天截断日 + 物化层 + 推主页 + **刷新 InBody 并过一遍体成分闸门**（产出 data/inbody/status.json）。
· **不要再单独跑 `check_inbody.py --fetch`**——collect 已经把 InBody 拉过了，再跑一次就是重复拉。只有当 collect 的输出里**没有** `[inbody]` 那一行时（说明该步没执行到），才补跑一次：
  python3 /Users/tian.li/Documents/Health/skills/body-health/scripts/check_inbody.py --fetch
· 拉数失败如实记录，**严禁重新登录佳明、不重试**。

【2 · 出报告】
过闸门 target.gate_cmd（或 target.data_gate）：
· level=complete 或 report_allowed=true → 正常出。
· **level=partial → 直接出「截断终版」，禁止 waiting_data**（本班是最后一班，等下去没人接）。头部写「数据截止 HH:MM · 截断终版」+ 缺失区间；久坐只给分钟数不给占比；截断侧的净消耗/压力分档不与全日值并列；**降档理由必须带同期对比**（引用前几日同时刻累计值），不许用「截断」一句话洗掉事实。
· level=no_data → 出「⚪ 不可判读」（各 section level 用 NEUTRAL），**禁止写成「零活动」或「状态好」**。
· status=stale_report → 重算事实包，改写同一份 md / judgment 为终版（禁「修正版/v2/变更对照」）。

流程：target.fact_cmd → 读 **data/reports/facts_D.json** + SKILL.md 的判读框架/纪律 + references/rules.json → 写 body_brief_D.md（标题 + 数据截止行 + 总评 + 交叉判读 + 指标明细 + 近几日趋势 + 数据缺口）与 judgment_D.json（summary / cross / sections，两者结论一致）→ target.merge_cmd。
数字只来自 facts 文件；数据边界只认闸门给的 cutoff；不做医疗诊断，红旗转就医提示。
体成分按 facts_D.json 的 `inbody` 节点写：verdict=EMPTY 写「当日无体测」不引数；有扫描只写「最近一次 + 距今 N 天」，deltas 里 exceeds_mdc=false 的一律写「在误差内」。

【3 · 体成分口径修正（只在有新扫描时做，属报告的一部分）】
读 data/inbody/status.json：`unreported` 非空 = 有扫描没进过报告。
· 目标文件 = **最近一份** **data/reports/body_brief_<最近日期>.md**（同日 md 与 judgment 一起看；只改最新那份，历史不回改）。
· **只改体成分表述**：把「最近体测为 …」每一处改成新扫描的真实日期 + 数值 + 距今天数，数字取 status.json / latest.json，**不许自己算**。
· 不重写其他判读、不改总评、不加小节、不写「修正版/v2/较上版」。若原结论依据因此消失，对话里明说一句。
· 改完 python3 /Users/tian.li/Documents/Health/skills/body-health/scripts/check_inbody.py --mark-reported <DATETIMES>（幂等）。

【4 · 记账与收尾】
· target.mark_cmd 追加 --data-cutoff <cutoff>。partial 会被 exit 3 拒绝 → 改记 waiting_data 再结束**是禁止的**，应直接走截断终版：`--allow-truncated --data-cutoff <cutoff>`。
· 出过报告后：python3 /Users/tian.li/Documents/Health/skills/body-health/scripts/refresh_recent.py --days 4 → python3 /Users/tian.li/Documents/Health/tools/build_homepage_data.py --push（失败只记录，不阻塞）。**不要重新发布应用**。

【5 · 部署检查（必做，即使本轮没出报告）】
跑：python3 /Users/tian.li/Documents/Health/tools/deploy.py --check
· `noop` → 线上已同步。
· `hot_update` → 补跑一次 `python3 /Users/tian.li/Documents/Health/tools/deploy.py`。
· `republish` → 前端代码改了但没上线。**本班是深夜无人值守，不要自己发布**（发布有失败面，失败了没人处理），只在消息里留一句：「⚠️ 前端代码有变更尚未发布，白天需发布（或等 14:00 班处理）」。14:00 班已配置为可自动发布，会接手。

【6 · 收尾必须带链接】
消息最后一行固定写，原样抄、不要改写也不要加前后缀：

线上主页：https://health-daily-86790.app.workbuddy.host

· 构建输出与 deploy.py 的输出末尾都会打「线上主页: …」。若与你写的不一致，**以输出为准**并说明地址变了——唯一来源是 tools/deploy.json。
· 本轮什么都没出（targets 为空）**也要给链接**：这说明今天没有新报告，但主页仍可看历史。
· present_files 开的报告之外，链接是我真正会点的那个入口，不要省。

【环境坑（别再试错）】
· 拉数命令自带 `uv run --no-project --python 3.11 --with curl_cffi==0.16.3 --with garth`。若报 `No solution found` / 依赖冲突是假线索（pyproject 的 requires-python 卡 3.9），别改 pyproject；若报 `curl: (35) BoringSSL SSL_connect` 是 curl_cffi 指纹问题，别查鉴权。

【呈现】present_files 只开本次真重写过的报告。一两句话：总评 + 是否截断 + 体成分现状，然后接链接。深夜没人读长文。
【行为标记】凌晨不问，缺 daily_log 就写一句「明早一起补」。
【风格】直接、简短，不铺垫。
