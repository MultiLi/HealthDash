# 健康数据采集班（14:00）· 班次指令唯一源

> 本文件是 WorkBuddy 自动化「健康数据采集（14:00）」的**唯一指令源**：自动化 prompt 只是一个薄加载器（「读取本文件并执行」）。**改这里即生效**（下轮班次自动读到新指令），不要去改自动化配置本身。
>
> - automation id：`e6d4de05-2bff-43bb-b0d8-192d758ff07b`（rrule：每日 14:00）
> - 唯一允许的重复：收尾那行「线上主页」地址与 `tools/deploy.json` 各存一份（换域名两处都要改）。

---

健康数据采集班（14:00）。**只拉数，不出报告、不做判读、不写账本。** 执行目录 /Users/tian.li/Documents/Health

【唯一任务】
1. 执行这一条（**照抄，不要换写法**）：
   no_proxy='*' NO_PROXY='*' uv run --no-project --python 3.11 --with curl_cffi==0.16.3 --with garth python /Users/tian.li/Documents/Health/skills/body-health/scripts/collect.py --days 3
   collect.py 一条命令做完全部采集：佳明当日 + 回补近 3 天截断日 + 物化层 + 推主页 + 刷新 InBody。
2. 转述成 3~5 行：今天截止几点、近 3 天哪几天不完整、回补成没成。InBody 只是采集结果里的一行，顺带带上即可（最新扫描日期 / 距今几天 / verdict），**不要单独展开、不要当独立议题分析**。

【部署检查（必做，本班是白天的兜底）】
跑：python3 /Users/tian.li/Documents/Health/tools/deploy.py --check
按输出处理，**不要跳过**：
· `noop` → 线上已同步，正常收尾。
· `hot_update` → 数据没推上去（正常不该发生，collect 已推）。补跑一次 `python3 /Users/tian.li/Documents/Health/tools/deploy.py`。
· `republish` → **前端代码改了但没上线**。本班是白天、有 12 小时处理窗口，**允许直接发布**：
  a. 用发布能力发布 /Users/tian.li/Documents/Health/site，参数 language=python、startCmd="python3 server.py"、installCmd=""、appId=wbapp_3G1QX7kZjmy7RNbU9gBdT5（传同一 appId 即覆盖同一应用，链接不变）
  b. 成功后登记：python3 /Users/tian.li/Documents/Health/tools/deploy.py --mark-app-deployed
  c. 消息里加一句「前端代码有变更，已重新发布」
· 发布失败就如实说一句，**不重试第二次**，把这句留在消息里让我白天处理。发布失败不影响数据采集的结论。

【收尾必须带链接 —— 这条和采集同等重要】
消息最后一行固定写，原样抄、不要改写也不要加前后缀：

线上主页：https://health-daily-86790.app.workbuddy.host

· collect.py 与 deploy.py 的输出末尾都会打「线上主页: …」。若与你写的不一致，**以输出为准**并说明地址变了——唯一来源是 tools/deploy.json。
· **采集失败也要给链接。** 主页照样能打开，只是数据没更新，这本身就是我要知道的信息；只在成功时给链接等于把失败藏起来。

【硬约束】
· **禁止写报告、禁止写 judgment json、禁止碰 data/state/slots.json、禁止调用 slot.py。** 报告只在 02:00 班产出。
· 拉数失败（网络/鉴权）只如实说一句，**严禁重新登录佳明、不重试超过一次**。手表没同步不是故障。
  `No solution found` / 依赖冲突是假线索（pyproject 的 requires-python 卡 3.9），别改 pyproject；`curl: (35) BoringSSL SSL_connect` 是 curl_cffi 指纹问题，别查 token、别重新登录。
· 发布只在 deploy.py 明确报 `republish` 时才做；其余情况一律不要发布。

【判读】
· 若「连续 2 天以上都截断在同一时段」，一句话提示手表可能在固定时段后不再同步。其余不展开——这是采集任务，不是分析任务。

【风格】直接、简短。没有异常就一句话收尾，然后接那行链接。
