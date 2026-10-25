---
name: body-health
agent_created: true
description: >
  生成每日身体健康简报：把当日的睡眠、HRV、静息心率、压力、身体电量、
  逐时心率、训练负荷、准备度、VO2 Max 等指标，按生理学/运动学规则加工成
  结论式分析（总评红黄绿 + 分维要点 + 当日时间线 + 趋势对比 + 数据缺口声明）。
  当用户问"今天身体怎么样"、"总结一下今天的健康状况"、"我的身体状态如何"、
  "恢复得怎么样"、"今天运动量够吗"等日常健康分析问题时使用本技能。
  数据源可插拔（适配器架构），当前实现：garmin-cn。
---

# body-health — 每日身体状态简报（数据源可插拔）

把原始身体指标加工成"人 10 秒能抓住重点"的健康简报。**与具体数据源解耦**：
换数据源/加数据源不改引擎，只写适配器。

## 架构（四层，采集与分析彻底解耦）

```
调度锚点层    scripts/slot.py                ← 排程日解析 + 续跑账本 → data/state/slots.json
                                             （定时任务的「目标日」唯一来源；已完成不再派发）
采集落盘层    scripts/sync.py                ← 幂等拉取 → <workspace>/data/raw/<date>.json
                                             （唯一允许网络请求与鉴权的层；历史日落盘后永不重拉）
              scripts/collect.py             ← 「拉数」与「出报告」解耦的采集编排
                                             （高频跑：拉目标日 + 自动回补截断日 + 推送主页）
完整闸门      scripts/data_gate.py           ← 「报告能不能开写」的唯一判据
                                             （field-level：该日覆盖到哪一分钟；只算不判）
数据源适配层  scripts/adapters/<source>.py   ← 离线读盘 → 规范化 schema（契约见 adapters/__init__.py）
                                             （解析、时区锚定、coverage 归一化全在这层，零网络）
事实包层      scripts/fact_pack.py           ← 只算不判：全部计算事实 → reports/facts_D.json
                                             （连续短睡/电量净值/Z2/运动窗/主观与 events 上下文）
              scripts/refresh_recent.py      ← 重算最近 N 天事实包并回并各自判读（趋势窗一致性）
判读层        晚间自动化 agent                ← 按本文件「判读框架」对事实包做定性判读，写报告
口径层        references/rules.json          ← 阈值标尺（agent 判读的统一尺子），改标准只改这里
```

**硬边界**：引擎与适配器禁止出现源特有字段名（如 dailySleepDTO）、禁止做 epoch 运算和任何网络请求；
sync 层失败时判读用已落盘数据继续并把缺失日记入 gaps，绝不回源重试登录。

**双层判读（2026-09-06 晚 Sir 定，取代脚本判读）**：定性分析不是脚本的活。
- 脚本只负责「算」：fact_pack.py 输出全部计算事实，**输出里没有一个判断句**；
  LLM 禁止对原始数据自行算数——所有数字必须取自事实包。
- agent 负责「判」：交叉归因、红黄绿、建议，按本文件判读框架执行；
  趋势以最近一天优先、反转必须指出；结论强度不得超出样本量。
- 结晶循环：agent 判读中反复出现、可精确复述为「触发条件+阈值+归因+行动」的结论，
  沉淀进 rules.json 四元组，供事实包做机械化预标记——agent 是判读者兼方法论生产者。
- daily_brief.py 的规则判读降级为 legacy 备用（不再进入晚间主流程）。

**报告产出纪律（2026-09-06 立，2026-10-18 修订）**：每日报告一天一份，只由晚间作业产出。
任何调试、验证、演示一律 `--out /tmp`，禁止覆盖 reports/；禁止在作业之外"顺手"生成报告。
2026-10-18 起，晚间作业从「23:30 一次」改为**多班次幂等重试**（见下节），
但「一天一份」与「只由作业产出」两条不变。

## 数据完整性与报告时点（2026-10-18 重构，最高优先）

**一句话：报告锚定「数据完整」，不锚定「钟点」。23:30 只是「最早可以出」，不是「必须出」。**

### 事故与根因

| 日期 | 报告写的截止 | 次日复核的真实数据 | 差额 |
|---|---|---|---|
| 10-17 | 22:05 | 完整日 24:00 | 步数 9948 → **10830**、久坐 835 → **943 min**、强度 3 → 4 |
| 10-18 | 19:34 | 当时尚未同步 | 缺 266 分钟（4.4h），报告已写完 |

根因不是「拉数不够勤」，而是**报告时点被固定在 23:30，而数据到没到是随机的**。
手表→手机→云端的同步时刻不可预知（10-17 是 22:05、10-18 是 19:34），
23:30 那一刀切下去，撞上什么就写什么。

### 关键认知：提高拉数频率**不能**催出数据

云端没有就是没有。拉一万次，截止时刻还是同一个。所以「提高频率」的真实价值只有两条：
① **数据一落云就进我们的盘**（撞上"已完整"的概率变高）；② **自动回补截断日**
（以前一天被截断就永远截断——raw 一旦落盘不再重拉，10-13~10-16 是人工 `--force` 才补全的）。
**真正的解法是「等 + 多班次重试」，不是「拉得更勤」。**

### 三层机制

| 层 | 文件 | 职责 |
|---|---|---|
| 采集解耦 | `collect.py` | 只拉数：目标日 + 自动回补 `partial` 的历史日 + 推主页。**不出报告、不碰账本** |
| 完整性闸门 | `data_gate.py` | 判 `complete / partial / no_data / missing_raw`，只读 canonical，零网络 |
| 账本硬约束 | `slot.py` | `mark --status done` 在数据 `partial` 时**直接拒绝关账**（exit 3） |

### 闸门判据（唯一，字段级）

依据 `canonical.coverage.cutoff_min`（适配器从 `wellnessEndTimeLocal` 归一化，
**引擎与前端禁止再出现这个源字段名**）：

| 等级 | 条件 | 处置 |
|---|---|---|
| `missing_raw` | 无 raw 文件 | 先跑 sync；不得出报告 |
| `no_data` | 有响应但 `totalSteps` 为 null | **不会再有数据** → 照常出「⚪ 不可判读」 |
| `partial` | 有数据，`cutoff_min` < 1430（23:50） | **禁止写报告** → `mark waiting_data`，等下一班 |
| `complete` | `cutoff_min` ≥ 1430 | 可以写报告 |

阈值全在 `rules.json` 的 `gate` 节（`complete_cutoff_min` / `publish_deadline_hour_next_day` /
`repull_days` / `hourly_grid_tolerance_pct` / `empty_day_awake_min`），禁止硬编码在脚本里。

### 多班次排程（幂等，靠账本去重）

| 班次 | 干什么 |
|---|---|
| 23:30 | 主班：数据齐就出；不齐记 `waiting_data`，**不写报告** |
| 02:30 | 抓夜间同步；仍不齐继续等 |
| 11:30 | 兜底：到 `publish_by`（D+1 11:00）仍不完整 → 出**「截断终版」**，显式标注截止与缺失区间，`--allow-truncated` 关账 |

- 每一班都先 `resolve`；`targets` 为空即 noop（已出过就绝不重出）。
- `02:30` 那一班的 `current_slot` 仍是**前一天**（边界恒为 23:30），所以不会漂到当天。
- **一天一份报告的铁律不变**：不完整时宁可晚出，也不出两份。

### 截断终版怎么写（唯一允许的不完整报告）

只在 `can_publish_truncated=true` 时使用，且必须同时满足：
1. 报告头部写明「数据截止 HH:MM · 截断终版」，并列出缺失区间（如「19:34–24:00 未进账」）；
2. 「数据缺口」节第一条就是这条，说明**不做睡前判读**；
3. 久坐给分钟数、**不给「占清醒期比例」**（分母少几小时，比例不可与前一日并列）；
4. `mark --status done --allow-truncated --data-cutoff HH:MM`。

### 事后自动纠偏（`stale_report`）

账本 `done` 但 `data_cutoff` 是截断值、而 raw 后来被回补成 `complete` 时，
`effective_status()` 返回 **`stale_report`**，下一班自动**重写同一份文件**（不是新增版本）。
窗口限定在补跑窗 3 天内——更早的历史报告按既有终版认账，不翻旧账。

**单一终版纪律（2026-09-09 00:50 Sir 定，与上一条同权重）**：报告文件永远只保留最新最全的一版结论，读到的就是终版。
- 禁止在报告内出现任何版本痕迹：v1/v2 标注、变更对照表、「版本修正」「草稿作废」「替代同日某版」、增量语言（"较上版 +X"）。
- 补数/修正后**直接改写同一份文件**为终版口径（md 与 judgment json 同步），不留旧版对照。
- 数据截止范围说明（如「23:28 后未计入」）允许保留——这是数据边界声明，不是版本历史。
- 历史报告同样适用：发现旧报告含版本痕迹，随手清成单一终版（2026-09-09 已全量清洗 09-02~09-08）。

**加工唯一性（2026-09-06 Sir 定，全体系生效）**：数据加工方式必须唯一。
canonical 工厂（adapters/）是唯一 raw 解析点，两个消费方禁止再长第二套解析：
- 浅加工（展示）：tools/homepage 只消费 canonical，只做布局/文案/抽样/达标着色，
  禁止解析 raw DTO、禁止硬编码阈值（阈值从 rules.json 读取，goals 随数据下发前端）；
- 深加工（判读）：daily_brief 引擎只消费 canonical + rules.json，是体系唯一判断点。
- 时间换算唯一入口 adapters/gtime.py；同一指标多来源并存时仲裁在判读层（证据降权），
  不在管道层去重。审查口诀：搜 raw 键名出现在 adapters 之外 = 违规；
  搜阈值数字硬编码在 rules.json 之外 = 违规。
- **指标定义唯一入口是 `references/metrics.json` + `scripts/metricdefs.py`**（2026-10-25 立）：
  「某指标的达标线是哪条规则、怎么换算、区间在哪」不许在消费方重写；阈值**数值**仍在 rules.json，
  注册表只声明**映射**。审查口诀：搜 `rules["activity"]["steps_good"]` 这类按指标名取阈值的写法
  出现在 metricdefs 之外 = 违规（应改成 `metricdefs.threshold("steps_daily","judge",rules)`）。
  解析失败**必须抛错**，禁止 `or 50` 这类静默退路——退路要写进注册表的 `band_fallback`。
  **存量豁免（S5 前不动）**：`daily_brief.py`（legacy 引擎）与 `homepage/{summary,scoring,activity}.py`
  仍在自查 rules 叶子。其中 `summary.py` 已是死代码（算完被 `build_index` 丢弃）、`scoring.py` 的阈值
  是评分参数而非常规达标线。**新增/修改代码一律走 metricdefs**，不要照抄这几处。

## 执行锚点与续跑账本（2026-09-12 建，定时任务的日期纪律）

**一句话：目标日由「排程时刻」决定，不由「运行时刻」决定。**

事故背景（2026-09-09 ~ 09-12 实测）：定时任务原先把目标日取自运行时的 `shell date`。
09-09 的 23:30 那次跑到「拉数」就被中断（会话超时），09-10 整晚没跑，两天报告直接消失；
09-11 的 23:30 任务实际在 09-12 01:0x 才执行，只能靠 agent 当场推断才锚回 09-11。
根因不是脚本 bug，而是**作业没有身份**——没有任何东西记录「哪一天的什么工作还没做完」。

### 概念

| 名词 | 定义 |
|---|---|
| **slot（排程日）** | 本作业最近一次应执行的排程时刻所在的自然日。23:30 的任务迟到到次日 01:08 执行，slot 仍是前一天；09:30 的任务在 00:00–09:30 之间被唤醒，slot 是**昨日**（今晨窗口还没到） |
| **目标日** | = slot 日，永远如此。运行时刻只用于算迟到量与窗口判定 |
| **账本** | `data/state/slots.json`，逐 slot 记录 status/attempts/数据截止/失败原因，是续跑的唯一依据 |

### 生命周期（`scripts/slot.py`，stdlib，无网络）

```
resolve  →  按需拉数/事实包/判读/合并（skills 原有流程）  →  mark  →  （全部 target 完成后）refresh_recent + 主页同步
```
1. `resolve --job evening|morning`：stdout 是 JSON。**targets 为空 = 本排程日已完成，直接结束，禁止重出报告**；
   `mode` = scheduled / late / catchup / noop；每个 target 自带可直接执行的 sync/fact/merge/mark 命令。
2. 逐日处理 targets（一天一份报告，禁止合并）；每天处理完 `mark --status done`；失败 `mark --status failed --error`，
   继续下一天，不中断其余 target。
3. 账本已完成的日期不再被派发；中断后重跑自动从没做完的那天接着走（这就是「续跑」）。

### 窗口与优先级

| 作业 | 排程 | 自动补跑窗 | 单次最多处理 | 说明 |
|---|---|---|---|---|
| evening | **23:30 主班 / 02:30 夜间重试 / 11:30 兜底（三班幂等）** | 排程日前 3 天内未完成的 slot | 3 天（当天最优先，其余从近到远） | 超过 14 天自动关账（`expired`）；边界恒为 23:30，02:30 归前一晚 |
| collect | 10:00 / 20:00 | 无 | — | 只拉数、只回补、只推主页；不出报告、不碰账本 |
| morning | 09:30 | 无（主观数据不可回溯） | 1 天 | 唤醒落在 00:00–09:30 直接判过期，不追补 |

- 排程提前量容差 5 分钟（调度器早触发时不至于退回上一班）。
- 积压（超补跑窗未超关账窗）只列在 `backlog_stale` 里，不每日提醒；需要补时
  `slot.py resolve --job evening --include-stale --max 8`。

### 产物自愈（双向）

账本 status 以**报告产物**为准，不对就自动纠正：
- 账本 `done` 但 md/judgment 不在盘上 → `artifacts_missing`，下次运行重出该日报告；
- 报告本体齐全但 `body_brief_<D>.json` 的 `judgment` 被后续 fact_pack 覆盖成空 → 自动补并契约（不重出报告）；
- 账本无记录但产物齐全 → 按产物认账为 `done`（账本丢失/回滚时不会重复出报告）。

### 禁令

- 定时任务里**禁止把 `shell date` 当目标日**，一律取 `slot.py resolve` 的 `targets[].date`。
- 禁止为了「对齐运行时刻」把迟到作业改期到运行日（迟到 ≠ 改期）。
- 禁止在 targets 为空时重出报告；禁止把多天合并成一份报告。

## 分层判据（改代码前先过这一条）

**一句话：换数据源时要改的文件都是源层，不用改的才是通用层。**

| 内容 | 归属 | 实例 |
|---|---|---|
| 鉴权、通道网关、端点、限流重试 | Garmin skill（garmin-connect-cn-data__skillhub） | 通道 A query 风格、garth token、**通道 B 实现（gca.garth_get）也只住网关** |
| 原始字段语义、时间戳编码、源报告口径 | **适配器**（adapters/garmin_cn.py，源 quirks 在此被翻译掉） | dailySleepDTO、TimestampLocal 挂钟坑（gtime.py 是唯一出口）、totalSteps=null=没戴表、**血氧跨夜归属（D 日 avgSleepSpO2 优先，缺则 D-1 日 avgTomorrowSleepSpO2）**、HR 采样间隔（hr_sample_interval_min 随 canonical 输出） |
| **指标定义**（语义 / 单位 / 聚合方式 / 达标方向 / 达标线是哪条规则 / 前端 key） | **`references/metrics.json` 唯一来源 + `scripts/metricdefs.py` 唯一解析器**（2026-10-25 收口） | 声明式表达式 `"goal": "activity.steps_goal"`；加指标只改这一处；断链在 `index.py verify` 报错 |
| canonical schema、生理学/运动学判读、交叉规则、阈值、主观日志、渲染 | **通用引擎**（daily_brief.py + rules.json） | 六维判读、cross_analysis、睡眠债/久坐/负荷规则 |

**引擎禁令**：不出现源字段名、源报告口径（"跨夜归明日"这类，一律在适配器归一化）、时间戳运算；不 import gtime。
新数据源接入 = 只写一个适配器 + sync 采集路径，引擎与 rules.json 零改动。若发现自己想改引擎来兼容某个源的怪癖，
说明归一化做晚了——退回适配器层解决。

## 判读框架与纪律：判定读断言，解释归 LLM（2026-10-25 重构，每份报告必读）

**一句话：`facts_D.json` 的两个新节点决定了报告长什么样——`assertions` 是判定，
`scales` 是多尺度事实。判读只做解释，不做判定。**

### 1. `facts.assertions` 是判定的唯一来源

里面三组：`l1`（单指标）、`cross`（交叉规则，代码算的布尔）、`device`（设备自己的判断，
INFO 透传），加一个 `rollup`（总评）与 `counts`。每条带 `id / level / confidence /
evidence / rule_ref / n_hist`（近 30 天第几次触发）。

五条硬约束：

1. **总评取 `assertions.rollup.label`，不得改档。** 你要降档可以，但必须写成
   「规则档 🔴 红、判读档 🟡 黄（理由：…）」两档并列，**不许只写黄**。
   历史 30 天里 8 天代码判红、报告全写黄，一篇红灯都没有——这就是「改档不留痕」的代价。
2. **不要自己拿 `computed` 里的数字去比阈值。** 判定已经在 L3 做完了，你再算一遍
   等于每晚重新执行一遍规则，且必然与主页、与断言表打架。
3. **每条触发的 `cross` 必须出现在报告「交叉判读」里**，正文引用其 `id` 与 evidence 数字。
4. **`device.*` 只是证据**，不能替代我们的 level；`hrv_status` 是设备按 7 日均算的，
   照旧不采信为当日结论。
5. **数字只能来自 facts**（含 `assertions.evidence` 与 `scales`）。facts 里没有的，
   如实进「数据缺口」，不要凭印象补。

### 2. `facts.scales` 是趋势的答案

`week / month / roll7 / roll28 / roll90` 各有 `latest`（当前单元）与 `series`（最近的
若干单元，滚动窗已按非重叠抽样）。单元里 `judge` 的 `na` = 覆盖不足，**此时不许拿
这个数下结论**（`cov` 就是覆盖率）。想问「近 90 天怎么样」，读这里，不要去翻旧报告。

### 3. `judgment_D.json` 必须带 `findings[]`

```json
{"summary": "…", "cross": [[级别, 文本], …], "sections": {…},
 "findings": [{"id": "<assertion id>", "level": "WARN", "dimension": "autonomic",
               "evidence": {…从 assertions 原样抄…}, "n_hist": 4,
               "note": "一句话：它意味着什么"}]}
```

`findings` 至少覆盖 `assertions.l1` 与 `cross` 里所有 BAD/WARN。`note` 是你唯一的
增量——把「38ms 意味着什么」写清楚，别复述数字。它的价值是让「过去 90 天红灯出现在
哪些维度」变成一次查询，而不是重读 90 份散文。

### 4. 出报告后必须自检

```bash
python3 <本技能目录>/scripts/verify_judgment.py --date D
```

它检查：总评是否与 `rollup` 一致、`findings` 是否漏掉 BAD/WARN、每条 `cross` 是否
出现在正文、正文数字是否都能在 facts 里找到出处、有没有「v2/修正版」这类字样。
**不通过就按提示改报告并重跑，最多一次**；仍不通过就在报告头部加一行
「自检未通过：<项>」，照常交付——**不许因为自检不出报告**。

### 5. LLM 不可用时的兜底

```bash
python3 <本技能目录>/scripts/compose.py --date D            # 打印骨架（数字全对、无归因）
python3 <本技能目录>/scripts/compose.py --date D --as-final  # 顶替正式报告（需 --force）
```

骨架路径**不调 LLM**：措辞生硬但每个数字都能溯源。默认 `--write` 只写
`.skeleton.md`，永远不碰正式报告。

### 6. 改口径前后必跑回测

```bash
python3 <本技能目录>/scripts/backtest.py snapshot   # 改之前
python3 <本技能目录>/scripts/index.py build
python3 <本技能目录>/scripts/backtest.py diff       # 改之后：哪几天改判了
python3 <本技能目录>/scripts/backtest.py golden     # 12 条金标用例
```

金标钉的是真实发生过的日子（10-22 同向偏软 / 10-24 单腿过门槛 / 10-08 截断 /
10-18 配速差一档…）。任何一条翻转都要能解释清楚，否则不该合并。

## 用法（按角色分步）

```bash
# 0. 锚点解析（定时任务的第一条命令；手动分析可跳过，直接用 --date）
python3 <本技能目录>/scripts/slot.py resolve --job evening|morning   # stdout 是 JSON
python3 <本技能目录>/scripts/slot.py mark --job evening --date D --status done|failed|waiting_data
python3 <本技能目录>/scripts/slot.py status [--days 14]              # 人工查账本

# 0b. 采集（2026-10-18 新增：拉数 + 自动回补截断日 + 可选推主页；不出报告）
uv run --python 3.11 --with curl_cffi --with garth python \
  <本技能目录>/scripts/collect.py [--days 3] [--no-push] [--json]

# 0c. 完整性闸门（写报告前必过；也可单独查）
python3 <本技能目录>/scripts/data_gate.py check  --date D [--text]
python3 <本技能目录>/scripts/data_gate.py matrix --days 7

# 0d. 体成分（InBody）闸门：本地零网络体检；--fetch 才联网刷一次
python3 <本技能目录>/scripts/check_inbody.py [--fetch] [--json] [--as-of D]
python3 <本技能目录>/scripts/check_inbody.py --mark-reported [DATETIMES]   # 并入报告后记账

# 2. 物化层（断言/单元的来源；collect.py 已自动调用，手动分析时先跑一次）
python3 <本技能目录>/scripts/index.py build     # raw → obs → units → assertions
python3 <本技能目录>/scripts/index.py verify    # 11 项一致性自检，exit 0 才算干净

# 2b. 事实包（只算不判：计算事实 + assertions 快照 + 多尺度 scales + canonical 契约）
uv run --python 3.11 python \
  <本技能目录>/scripts/fact_pack.py [--date YYYY-MM-DD] [--days 4]
#    产物：reports/facts_D.json（事实，agent 判读输入）
#          reports/body_brief_D.json（canonical 契约，主页消费；judgment 字段由判读方合并）
#    合并判读：fact_pack.py --date D --merge-judgment reports/judgment_D.json
#    趋势窗刷新（补跑历史日后必跑）：python3 <本技能目录>/scripts/refresh_recent.py --days 4

# 3. legacy：脚本规则判读（已退出晚间主流程，仅调试/回溯用；测试一律 --out /tmp）
uv run --python 3.11 python \
  <本技能目录>/scripts/daily_brief.py [--date YYYY-MM-DD] [--days 4] [--source garmin-cn] [--out /tmp]
```

- 默认分析今天，趋势窗口默认 4 天，默认源 garmin-cn。
- **`--force` 慎用**：目标日专属键（dailyhr / vo2_latest / activities）仅在「该日 == --date」时采集。
  2026-09-07 曾用 `--force` 全量重拉，把历史日曾作目标日留下的逐时心率整文件覆盖成空，
  Z2 窗口从 4 天塌成 1 天。已加保护：重拉时这些键没有新值就沿用旧值（sync.py sync() 末尾）。
  要补历史日的逐时心率用 `--backfill`，不要靠 `--force`。
- 报告产出纪律：每日报告一天一份，只由 23:30 定时任务产出；调试一律 --out /tmp。
- garmin-cn 适配器走本工作空间内 garmin-connect-cn-data__skillhub 技能的通道 A（gc-api 网关，query 风格全通）；
  仅当通道 A 401 时才回落通道 B——回落实现住在网关（garmin_gc_api.garth_get，只加载已存 token，
  绝不二次登录），sync.py 只写调用策略，零端点知识、零鉴权细节。活动量/久坐/强度分钟/身体电量充放电均为实测值。
  该技能的采集禁令（严禁二次登录等）见其 SKILL.md。

执行后：读 .md，把总评和交叉判读用对话呈现；"数据缺口"一节照实告知，禁止编造数值。

## 血氧维度（2026-09-05 起待出数）

- 端点：`wellness/daily/spo2/{date}`（通道 A 路径风格）。用户手表血氧开关 2026-09-05 才开启，此前全 null 属正常，勿归因为 CN 限制。
- 适配器 `day["spo2"]` 含 `sleep_avg / day_avg / lowest / hourly`。
- 判读纪律：**基线建立前（<7 晚有效采样）只记录不出结论，不做单点告警**；稳定后仅当睡眠均值持续 <92% 或最低 <88% 且呼吸紊乱升高时升级为就医提示。用途是睡眠呼吸暂停筛查，不是日常焦虑源。

## 信源参考量与设备分档（2026-10-25 立，判读必读）

**一句话：设备的动态参考是判定的「输入」，不是判定的「替代」；设备的分类判定是「证据」，不是「裁决」。**

设备逐日给的参考量（已进观测层，可作阈值输入）：

| 指标 | 含义 | 静态对照 | 覆盖 |
|---|---|---|---|
| `sleep_need_h` | 设备按 HRV/午睡/睡眠债/训练算的**今晚需求** | 7h | 33/36 |
| `bed_window_start/end_min` | 推荐入睡窗（00:00 起的分钟数） | 01:30 | **20/36** |
| `spo2_avg7` | 设备 7 日血氧均值 | 92% | 30/36 |
| `acwr` | 急慢性负荷比 | 0.8 门槛 | 36/36 |

四条纪律：

1. **取严不取松**。`sleep_h` 的判读线 = `max(静态 7h, 设备需求)`。设备说需要 7.83h 就按 7.83h 判；
   设备说 6.5h 时**不放松**到 6.5h——动态参考只能让标准更贴合，不能让它更低。
2. **相对量不当绝对标准**。设备推荐入睡窗是按**当前作息**算出来的，拿窗口本身判达标等于把现状正常化。
   只用偏移：判读线 = `窗止 + 45 分`，红线 = `窗止 + 120 分`，静态 01:30 只在窗缺失时兜底。
3. **尺子换了必须说**。参考量缺失时退回静态口径，同时落 `data.reference.missing` 断言，
   并在 `sleep.*` 的 evidence 里带 `ruler: device_window | device_need | static_fallback`。
   **报任何睡眠结论前先看 ruler**——否则「02:27 有时判晚有时不判」无法解释。
   （实测设备窗只有 20/36 天给，10-08 起连续不给。）
4. **设备分档原样透传，不映射成红黄绿**。`grades` 表存 readiness 五档、睡眠八项评级、评分反馈、
   HRV status、呼吸紊乱分级；断言层用 `device.*`（一律 `INFO`）。
   ⚠️ `hrv_status` 是**设备按 7 日均**算的，不是昨夜状态——照旧不采信为当日结论（09-11 事故）。

**阈值写法**：注册表里 `"judge": "max(sleep.min_healthy_duration_min / 60, ref:sleep_need_h)"`，
退路写 `"judge_fallback"`。**禁止**在代码里 `refs.get(x) or 7` 这类静默兜底。

**阈值逐日变 → 单元判定必须逐日做**：`judge_mode: "daily"` 时单元达标 = 达标天数/可判天数
（`units.daily_ok_rate`），不是「日均值 ÷ 日均阈值」。两个均值相除会失真。

## 多重观测框架（2026-10-19 建 → **2026-10-25 归档**）

> **已归档到 `scripts/legacy/observations.py`，不要再从它 import。**
> 它当年想解决的两件事已各有归属：
> · **观测结构化** → `obs` 表（`index.py` 是唯一 ETL：raw → obs 只在那里发生）
> · **可比条件与多源裁决** → `references/metrics.json` 的 `role` / `error_ref` / `agg`
>   ，以及 `units` 的 `agg` 与 coverage 门禁
>
> 保留文件是因为 `resolve()` 的「权重 × 条件」思路在新接数据源（体脂秤、血压计）时
> 还会用到。但它是**只读参考**：新增代码再从它取数，就等于又开了一条取值路径，
> 违反架构公理 4（一个指标只有一条取值路径）。
>
> 四条硬规则本身仍然有效，只是执行者换了：
>
> 1. **观测不合并**——`obs` 表按 (metric, t, source, method) 存，同一指标多源并存。
> 2. **可比性由注册表声明**——`metrics.json` 的 `role`（真值/proxy）与 `error_ref`；
>    体重同条件比 0.6kg、异条件 2.0kg 这类门槛在 `rules.confidence`。
> 3. **仲裁显式化**——单元层出 `delta_verdict`（changed / within-noise / insufficient /
>    uncalibrated / suppressed），**不给差值的时候要说明为什么**，不静默挑一个。
> 4. **派生观测留输入**——`obs` 有 `raw_ref`，`assertions.evidence` 只放已算好的数字。

**与 canonical 的分工**：canonical 仍是日渲染的快路径（画图、算指数）；
observations 是**多源问题的答案层**。报告里凡涉及「某指标现在是多少」「变没变」，
优先走 `resolve()`，不要自己从 facts 里挑一个数。

**红线**：本层不判断「哪个源更准」——那是判读层的证据裁决。它只负责把冲突摆到桌面上。

## 判读框架（引擎自动执行，回复按此口径解释）

0. **交叉协同判读是简报主体**（2026-09-06 Sir 明确要求）：不做数值复读机——仪表盘能看到的数只做简要汇总，
   增量价值来自指标间的交叉与协同分析，结合生理学机制给出单看任何指标都得不出的判断。
   引擎 `cross_analysis()` 实现规则集，阈值在 rules.json 的 `cross`：
   - 睡眠债 × 自主神经（连续短睡是否已推高交感张力，还是仍在代偿）
   - 入睡时间 × 睡眠结构（晚睡截断后半夜 → REM 主产段被截掉，机制归因而非现象罗列）
   - 久坐 × Body Battery 消耗 × 心率（坐一天却耗光电 = 脑力/应激消耗，不产生训练适应）
   - 短睡/HRV 低 × 训练风险（欠觉日只该练到 Z2；反之恢复条件好却没练 = 浪费恢复能力）
   - 负荷账本 × VO2 Max 分档（ACWR 长期低 + VO2 一般 = 没有上行支撑，方向性判断）
   - 主观日志 × 睡眠结构（酒精/晚咖啡因对深睡 REM 的归因，客观-主观交叉）
   - Body Battery 连续净负 × 睡眠债（同源判断：源头在入睡时间）
1. **总评**：全部维度取最差级别 → 红（必须正视）/ 黄（可控有短板）/ 绿。
2. **六维**：睡眠（时长、深睡/REM 占比、入睡时间）、**活动量与久坐（步数/距离/活动消耗/久坐时长/最长连续久坐块/强度分钟/夜间充电与日间消耗净值）**、自主神经（HRV vs 个人基线 + 环比骤降、RHR vs 7 日均）、训练与负荷（逐时心率反推运动窗、ACWR）、消耗与压力（BB 落点、压力-心率交叉验证）、准备度与有氧底子（readiness、恢复时间、VO2 Max 分档、**Z2 暴露：日分钟数 + 窗口累计 vs 周目标 180min，单次 ≥45min 计有效剂量——证据锚点 exercise_science.md §1-2；零 Z2 不惩罚只报进账**）。
3. **交叉验证规则**（防误读关键）：压力高+心率高 = 体力消耗；压力高+心率平 = 脑力/情绪。逐时心率 avg≥100 且 max≥150 的整块窗 = 真实运动（没按开始键也算）。
3b. **活动量以 usersummary 实测值为准，绝不用 activity 列表**：后者只含按了开始键的记录，实测误差可达一个数量级（9/4 activity 数百米 vs 实测 12187 步 / 9.77 km）。`totalSteps` 为 `null` = 当天没戴表/没同步，**记为数据缺口，不是零活动**。
3c. **变化表述必须先过噪声门槛（2026-10-19 建，硬约束）**：`rules.json` 的 `confidence` 节登记了每个观测项的
   `noise_sd / mdc_single / mdc_change`（本机 24–27 天实测，非文献）。规则：
   · `|Δ| < mdc_change` 时**禁止**用「上升/下降/改善/恶化/增加/减少/最高/最低」这类动词，只能说「在误差内」；
   · 单次读数偏离个人基线 < `mdc_single` 时不得做机制归因；
   · 谈趋势需 ≥7 天、方向一致、总幅度 > `mdc_change`；**单日步数比较无意义**（门槛 8000 步，只能看 7 日均值）。
   · 「距目标差多少」是**达标判断**，与「变化判断」两回事，不受此门槛约束——别把两者混成一句。
   · 不确定某项查 `notes/confidence_model.md`；新增观测项时先补 `confidence` 节再写判读。
3d. **体成分是事件型维度，按 `facts.inbody` 判，不按日期凑（2026-10-25 建）**：InBody 没有逐日序列，
   报告里只能出现「最近一次扫描 + 距今 N 天」，**当天没测就写「当日无体测」，不许把旧值写成当日状态**。
   · `facts.inbody.verdict` = `EMPTY`（截至该日无扫描）→ 报告**不得引用任何体成分数字**；
     `NEW`（有未进报告的扫描）→ 本次必须把新扫描写进报告并 `check_inbody.py --mark-reported`；
     `STALE`（超 `stale_days_warn`）→ 可作基线，须标「陈旧基线」；`ANOMALY` → 只报事实+可疑点，不做归因。
   · 变化只能引用 `deltas[].exceeds_mdc` 为 true 的项；`same_conditions=false` 时体重门槛按异条件 2.0kg，
     **且必须写明时段不同**（晨起空腹 vs 晚间差 1–2kg 是进食/水合，不是胖瘦）。
   · 头号口径纪律：**BIA 家用机 + 非空腹 + ±3–5%，两周内的涨跌一律不可判**。骨骼肌 ±1.5kg 内
     只能说「没有证据表明流失」，内脏脂肪 ±1 级是噪声，体脂率 ±3.0pt 起才谈变化。
4. **回复风格**：结论先行；**对话里以交叉判读为主体展开，指标明细一句带过（"明细在报告里"）**；
   趋势用对比说话（"连续三晚不足 7h"而非"睡眠不足"）；每条黄灯给行动建议。

## 主观补充数据（客观指标的校准锚）

客观指标无法自我校准——HRV 低但人感觉很好，和有气无力，是两回事。用户手工记录的主观数据
存放在 **`/Users/tian.li/Documents/Health/daily_log.jsonl`**（一行一天，JSON）。分析时先读它，
与 canonical 逐日数据按日期左连接；缺就声明缺口，不要编。

字段（`null` = 未填）：

| 字段 | 含义 | 采集时点 |
|---|---|---|
| `date` | `YYYY-MM-DD` | — |
| `energy` | 晨起精力 1–5 | 早 |
| `soreness` | 肌肉酸痛 0–3 | 早 |
| `sleep_quality` | 睡眠自评 1–5 | 早 |
| `weight_kg` / `bp_sys` / `bp_dia` | 体重、收缩压、舒张压 | 早（体重每周 2–3 次、血压每周 2–3 次即可） |
| `alcohol_cups` | 饮酒杯数 | 晚 |
| `caffeine_last` | 末次咖啡因 `HH:MM` | 晚 |
| `phone_down_plan` | 当晚计划放下手机 `HH:MM` | 晚 |
| `overtime` | 是否加班（bool） | 晚 |
| `note` | 自由备注 | 任意 |

字段名以本表为准（2026-09-12 校正：历史文档里的 `alcohol_drinks` / `screen_cutoff` 是早期草稿名，
实际落盘的是 `alcohol_cups` / `phone_down_plan`）。引擎不做字段映射，整行原样进事实包的
`subjective_today`，判读方直接读——所以**新增字段不需要改引擎**，只要同步这张表。

行为标记（饮酒/咖啡因/屏幕/加班）是把「睡前降档慢」从现象变成可归因原因的唯一数据层——
缺了它，相关建议只能退化成"早点睡"。

## 体成分源 InBody（事件型，2026-10-18 建）

**定位：不进六维日报，不做逐日适配器。** InBody 是「事件型」数据源——一次体测产出一条记录，
间隔以周/月计，没有逐日序列。因此**不写 `data/raw/<date>.json`、不写 `adapters/inbody.py`、
不改 daily_brief 引擎**，走独立落盘 + 按需引用。

| 项 | 值 |
|---|---|
| 采集 | `scripts/sync_inbody.py`（stdlib，无第三方依赖） |
| 凭据 | `~/.config/inbody-cn/credentials.json`（600，`login_id`=注册手机号纯数字） |
| 会话 | `~/.config/inbody-cn/session.json`，JWT 缓存 23h，过期自动重登 |
| 产物 | `data/inbody/raw/scan_<DATETIMES>.json`（含 BCA/MFA/IMP/LB/WC）、`profile.json`、`index.json`、`latest.json` |
| 区域 | 中国区 API 主机 `wechat.lookinbody.cn`（由 `GetCountryInfoV2` 按 `Code2=CN` 解析） |
| MCP | **不装**（见下方「MCP 为什么放弃」） |

**字段分布（2026-10-18 实测，摘要层踩过的坑）**：同一份扫描的指标散在四个段里，
**不能只看 BCA/MFA**：

| 段 | 关键字段 | 坑 |
|---|---|---|
| `BCA` | WT/BFM/TBW/FFM/SLM/PROTEIN/MINERAL/EQUIP_SERIAL | 蛋白质字段名是 `PROTEIN`（`PR` 为 null） |
| `MFA` | PBF/SMM/BMI/WHR/FFMI + 各 `*_MIN/_MAX` | 只有判定值，没有 BMR/VFL |
| `WC` | **VFL（内脏脂肪等级）、BMR、VFA、FSRANK** | 摘要层原先读 `MFA.VFL` → 永远 null，主页看不见内脏脂肪 |
| `LB` | **节段瘦体重**（LRA/LLA/LT/LRL/LLL）、**节段脂肪**（FRA/FLA/FT/FRL/FLL） | 比节段阻抗可读：阻抗只能看 >10% 判异常，瘦体重能直接看左右差 |
| `IMP` | 5/50/200 kHz 节段阻抗 | 左腿较右腿高约 6%（三频一致），在站姿/电极误差内，**只作复测对照** |

`summarize()` 已修：`visceral_fat_level` 取 `WC.VFL`、新增 `bmr_kcal`、`ffmi`、
`segmental_lean_kg` / `segmental_fat_kg`。**新增字段时先确认它在哪一段**，别假设都在 BCA。

### 闸门与三段式链路（2026-10-25 建，Sir 要求「拉取/检查/分析」全链支持）

```
sync_inbody.py   拉（网络层，只落盘）        ← collect.py 第 ⑤ 步调，一天两次（10:00 / 23:30）
check_inbody.py  查（闸门层，只体检不判读）  ← 本地零网络；collect 每次顺带跑，02:30 班带 --fetch
fact_pack.py     算（facts.inbody 节点）     ← 只认「报告日及以前」的扫描，门槛已算好
晚间报告 agent    判（见判读框架 3d）        ← 新扫描并入报告后 --mark-reported 记账
```

| 项 | 值 |
|---|---|
| 阈值唯一来源 | `rules.json` → `inbody`（`stale_days_warn`=14 / `stale_days_bad`=45 / `weight_mdc_diff_condition`=2.0 / `hard_anomaly`） |
| 产物 | `data/inbody/status.json`（`verdict` / `age_days` / `deltas` / `anomalies` / `unreported` / `last_reported_scan`） |
| verdict | `OK` 无新无异常 ｜ `NEW` 有新扫描未进报告 ｜ `STALE` 过旧 ｜ `ANOMALY` 可疑/不可比 ｜ `EMPTY` 无记录 ｜ `FAILED` 拉取失败 |
| 退出码 | 0 = OK/NEW ｜ 1 = STALE/ANOMALY ｜ 2 = EMPTY/FAILED |

`check_inbody.py` 查五件事：**可达性**（`--fetch` 顺带登录）、**陈旧度**、**完整性**
（关键字段缺失 / 体重 ≠ 去脂+脂肪 差>0.6kg / PBF 与脂肪量折算差>1.0pt）、
**可比性**（机型或序列号变更 → 跨机型比对整段作废）、**变化量**（逐项过 `confidence` 门槛）。

三条纪律：

1. **扫描是事件型，没有新扫描 ≠ 故障**。凭据/接口真挂了的特征是 `verdict=FAILED`，
   不是「数没变」——所以 `collect` 现在**成功也打一行**，失败打两行，不再静默。
2. **`--mark-reported` 是幂等账**，只在报告真的写进了那次扫描后调用。`unreported` 非空 = 报告层欠账。
3. **历史日重算用 `--as-of D`**（fact_pack 已自动传），否则 10-22 的报告会读到 10-25 的扫描。

### 呈现层（主页，2026-10-18 建）

`tools/homepage/inbody.py`（视图装配）→ `site/index.json` 顶层 `inbody` 块 →
`site/js/inbody.js` + `site/css/inbody.css` 渲染独立一节「体成分 InBody」。

四条版式纪律：

1. **不进日/周/月尺度，不吃 anchor/scale。** 它的时间语义只有「最近一次 + 距今 N 天」。
   塞进指标网格会立刻说错两件事：暗示每天都有值、让「月日均」这种口径凭空产生。
2. **不为 1 条记录造趋势图。** `scan_count < 2` 时页面明说「现在是截面，不是趋势」。
3. **体重必须展示构成**（去脂 + 脂肪的堆叠条），不能只给一个 90.2。
   同时把**晨起空腹体重并列**并写明「两个口径不可相减、也不要取平均」——
   不写这句，90.2 与 85.9 摆在一起必然被读成「胖了 4.3kg」。
4. **每个数都要给「相对参考区间站在哪」**（区间条 + 越界量），而不是只给颜色；
   参考区间取设备下发的 `*_MIN/_MAX`，前端不硬编码。
   口径声明（非空腹 / 家用机 / BIA ±3–5%）必须出现在页面上，不能塞脚注。

**目标路径由实测去脂体重现算**：`体重 = FFM ÷ (1 − 目标体脂率)`。
禁止把 85.9/80.8 这类结果写死——换一次扫描就该自动重算。

```bash
cd /Users/tian.li/Documents/Health
python3 skills/body-health/scripts/sync_inbody.py --check   # 只验凭据
python3 skills/body-health/scripts/sync_inbody.py           # 全量落盘
```

**MCP 为什么放弃（2026-10-18 实测，别再试一遍）**：上游 `inbody-api-mcp` 0.1.0 声明
`requires-python >=3.14`，而 `mcp` 依赖链里的 **pydantic-core 在 3.14 上没有预编译 wheel**，
uv 会现场装 Rust 工具链（`~/Library/Caches/puccinialin`）编译它——冷装 3 分钟以上仍不出结果，
换缓存目录、清代理变量都绕不过。为一个只读、四个工具的 server 承担这种依赖不划算。
**结论：交互式查询用 `latest.json` / `index.json`，能力等价，零依赖。**
（另注：uv/uvx 在系统代理失效时会挂死，`no_proxy='*'` 对它**无效**，正确写法是
`env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy uv ...`。）

**凭据与登录禁令（2026-10-18 实测）**

- `login_id` = **13716939072**（中国区账号已确认存在，美国区同号不存在）。
- ⚠️ **禁止盲试密码**。错误密码返回 `PW_FAIL_4` —— 末尾数字是**剩余尝试次数**，
  每错一次减一，减到 0 大概率锁号。`EmptyData` 才是「账号不存在」，两者不可混淆。
  **要登录必须先拿到真密码；绝不为了「测一下链路」而打登录端点。**
- 验证码登录（`POST /V2/Main/GetPhoneAuthNumber`）端点存在，但**字段名未破解**
  （19 个平铺名 + 嵌套 + `Data` 字符串封装共 40+ 组合均回落韩语「没有手机号」）。
  已知线索：该接口的 `Data` 在 ASP.NET 模型里是 **string**（传对象会 400）。
  要攻这条路，先反编译中国区 APK 拿字段名，别再盲猜。

**判读纪律（BIA 的固有误差，别当成实测真值）**1. **只在「最近一次扫描 ≤30 天」时把体成分写进结论**；超过 30 天只作历史参考并显式标注距今多少天。
   体成分是慢变量，但不能拿三个月前的数支持今天的判断。
2. **口径与晨起体重分开记账**：`latest.json` 的 `weight_kg` 是体测仪读数（含衣着、时段、进食影响），
   与 `daily_log.jsonl` 的晨起空腹体重是**两个口径，禁止互相替换或求平均**。
3. **跨设备/跨时段的两条扫描不可直接比较**。可比的前提：同型号设备 + 同一时段（推荐晨起空腹、
   排便后、未进食未训练）+ 相近水分状态。换健身房设备 = 新基线，不叠加趋势。
4. **生物电阻抗受水合状态影响大**：运动后、饮酒后、高钠餐后、女性经期前后的 PBF 可摆动 1–3 个百分点，
   与真实脂肪变化同量级 —— 单次涨跌不构成结论，至少两条同口径扫描才谈趋势。
5. **节段数据（IMP / 左右腿肌肉）用途有限**：可看左右对称性，**不能**用来判断「哪条腿更该减脂肪」——
   局部减脂不存在（见 `notes/leg_circumference_method_map.md`）。

**与减脂/围度议题的连接**：PBF + SMM 决定「B 通道（脂肪）是不是主战场」——
男性 PBF >20% 时脂肪是主项；SMM 偏低则任何热量缺口都必须配保肌抗阻。
内脏脂肪等级（VFL）与腰围/身高比互为印证，两者打架时以腰围实测为准。

## 如何接入新数据源（如 Apple Health / 华为 / 小米 / Whoop）

1. 新建 `scripts/adapters/<source>.py`，实现 `collect(date, days)`，按
   `adapters/__init__.py` 里的契约输出规范化 schema（缺什么指标省什么字段）。
2. 类上加 `@register("<source>")`。
3. 依赖变化时在 SKILL.md 的 RUNNER 命令里补 `--with <pkg>`。
4. 该源特有的坑（时区编码、封锁端点、鉴权禁令）写进适配器文件 docstring，不污染引擎。

## 外置能力注入约定（2026-09-06 Sir 定，永久接口协议）

引入外置的人体健康/运动学能力（专家、知识体系）时，遵守以下约定：

**总约束：引擎不认识专家。** 引擎只执行 rules.json 中已验证的规则；专家不直接改引擎代码，也不在运行时被引擎调用。专家产出方法论，引擎只执行方法论。

**知识底座：`references/exercise_science.md`**（2026-09-06 建）。所有生理学/运动学归因必须引用该知识库，不得凭常识拍结论；新规则进 rules.json 前须能在库中找到证据锚点并注明证据强度。引入新领域能力前，先扩充知识库再写规则。

**注入三通道：**

1. **规则化（主干）**：外部假设 → 用历史 raw 数据回测验证 → 翻译为四元组 `(触发条件 + 阈值 + 机制归因 + 行动建议)` → 阈值进 rules.json 对应节，机制归因与行动建议进引擎对应规则。**翻译不成四元组的建议留在对话层，不硬塞进引擎。**
2. **专家周期复盘**：周期复盘时由外置专家审视引擎输出，职责单一——提出新的交叉假设；验证通过走通道 1 沉淀。
3. **维度扩展**：新能力需要新数据时，走适配器架构 + 升字段门槛（"每天固定流程测 + 判读引擎消费"才进 daily_log），不为能力改引擎。

**优先级预判**（排期参考，非约定）：Z2 有氧底子建设（拉 VO2 Max）> 减重能量学（BB 消耗 × 体重趋势能量平衡）> 力量训练（手表心率证据弱，需手动记录或长期缺位）。

## 已知坑（跨源通用）

- 沙箱禁用 `datetime.fromtimestamp()`（时区错乱）。时间换算唯一入口是 `adapters/gtime.py`
  （`gmt_ms_to_local_hhmm` / `local_ms_to_hhmm`，内置 9/6 真实数据回归断言，改动后 `python3 gtime.py` 必跑）。
  关键坑：`*TimestampLocal` 是挂钟按 UTC 存，勿再 +8h；新数据源的坑写进适配器 docstring，不污染引擎。
- 任何采集异常记入 gaps 输出，简报中如实声明数据缺口。
- 用户生理档案（maxHR/RHR/年龄）变化时更新 rules.json 的 `profile`。
- **`activities` 是「最近 N 条」的滚动窗口，不是逐日端点（2026-09-12 建）**：逐日 raw 里存的是
  **该日 sync 时刻**的快照；快照早于当日运动、或 limit 被更早的活动占满时，这个键**存在却不含本日记录**
  —— 实证：09-04~09-09 的 raw 各存 10 条，只有 09-05/09-06 含本日，09-08/09-09 属本日 **0** 条，
  所以 09-08/09-09 报告当时「看不到」当天活动，不是判读疏忽。
  · 回补：`sync.py --backfill`（已支持 activities）。它是跨日端点，只拉一次再按 `startTimeLocal`
    分派；写入独立键 **`activities_own`**（仅本日记录），**不覆盖**原 `activities` 滚动窗口。
    **判据是「本日记录是否已齐」，不是「键是否存在」**——后者会让缺本日的日期被误判为已补。
  · 适配器优先消费 `activities_own`，回落 `activities`，两者都按 `startTimeLocal` 日期过滤。
  · `_fetch_target_extras` 的 limit=10 太浅且只给目标日；跨日回补用 `ACTIVITY_POOL_LIMIT=50`。
- **引用活动前必须看 `typeKey`，别用「走」/「跑」想当然概括（2026-09-12 实证）**：同一段夜间 Z2 可能是
  `walking`，也可能是 `indoor_cardio`，步频与负荷形态不同——09-09 的 49min / HR 均 121 / 3906 步
  被 09-10、09-11 报告称作「夜走」，实为 `indoor_cardio`（步频仅约 80 步/分）。已更正。
  **`locationName` 可做定位判读**：09-10 闵行、09-11 嘉定 → 在沪（出差）；09-05/06/08 海淀/朝阳 → 在京。
- **判读引用「相邻日」数字前，先确认该相邻日的 raw 是完整日（2026-09-16 实证，代价一次归因错向）**：
  判据是 `usersummary_daily.wellnessEndTimeLocal` 是否落在**次日 00:00**。09-12 盘上存的曾是半天账
  （步数 1636），补跑到完整日后变成 **16444 步**（窗口最高，含晚间两小时 8700 步）。
  当时写好的 09-13 报告据此把结论写成「09-12+09-13 两天塌陷」，实为「09-12 拉满 → 09-13 归零」（单日跌 92%）。
  **口诀：趋势窗里的相邻日，只要不是「目标日当天 sync 的完整日」，引用前先验 wellnessEndTimeLocal。**
- **`refresh_recent.py --days N` 只覆盖「窗口末日往前 N 天」，比窗口更早的补跑日不会被它刷新（2026-09-16 建）**：
  补跑日若落在 refresh 窗口之外，必须**手工对该日与相邻日各跑一次 fact_pack**，否则相邻日趋势表数字会互相打架。
  且该脚本不改写历史 md —— 若补跑确实改变了某日数字，**该日报告需按单一终版口径另行改写**（脚本会打印提醒）。
- **`tools/smoke_site.mjs`「进行中的当天」一组断言按 `ix.today` 定位（2026-09-16 修）**：原先按「索引最后一行
  ＝进行中的当天」推断，深夜构建（当天尚无数据行）时会误报 2 项失败；现改为找不到当日数据行时显式跳过，
  断言数由 48 变为 44（视当天有无数据行而定）。
- **活动总量以 usersummary 为准，绝不用 activity 列表**（原第 3b 条已在上方判读框架声明，此处仅强调）：
  09-14 三条活动合计 5938 步，而全天实测 14493 步——差额全在未记录的日常走动。
