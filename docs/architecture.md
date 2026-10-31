# 当前架构

> **本文件是架构的唯一权威描述。**
>
> 本文回答两个问题：**数据怎么流？代码住哪？** 判读怎么写见 `skills/body-health/SKILL.md`，运维怎么做见 `docs/operations.md`。

---

## 一、六条公理

1. **真相只有两个**：`data/raw/*`（不可变采集快照）与 `references/rules.json`（口径）。其余一切都是派生层，必须可丢弃、可重建。
2. **判定的归属按「可复现性」切**：能写成布尔表达式的一律进代码；只有需要先验与取舍的才进 LLM。
3. **LLM 不得做算术**。它的输入是断言（已判定 + 已算好的数字），不是原始指标。输入里没有数字，它就只能引用。
4. **一个指标只有一条取值路径**。查询必须经过观测层，不允许任何消费方再从 raw 或 facts 里自己挑一个数。
5. **一个指标只有一份定义**。语义、聚合方式、达标方向、达标线映射都只有一处；消费方读定义，不复制定义。
6. **每次产出自带版本指纹**（data / rules / engine 三重哈希），否则「同样的输入两次跑出不同结论」无法被发现。

---

## 二、数据分层

```
L0  raw            不可变采集快照（JSON 文件，冷存，溯源）           真相
L1  observations   指标 + 值 + 时间 + 来源 + 方法 + 条件 + 覆盖       派生·可重建
L2  units          日 / 周 / 月 / 滚动 7·28·90 × 指标 → 统计量         派生·可重建
L3  assertions     命题 + 布尔 + 证据 + 置信档（DAG，非平铺）          派生·可重建
L4  compose        模板骨架（必产）+ LLM 叙述（可选、可校验）          产物
L5  surface        主页 / 日报告 / 周期查询 / 对话                     展示
```

| 层 | 输入 | 输出 | 落盘位置 | 可复现 |
|---|---|---|---|---|
| L0 | Garmin / InBody API | 日快照、扫描文件 | `data/raw/` `data/inbody/raw/` | 逐字节 |
| L1 | L0 + 适配器 | `obs` 表 | `data/db/health.db` | 逐字节 |
| L2 | L1 + rules | `units` 表 | 同上 | 逐字节 |
| L3 | L2 + rules | `assertions` 表 | 同上 | 逐字节 |
| L4 | **只读 L3** | 报告 md / judgment json | `data/reports/` | 骨架逐字节，叙述不确定 |
| L5 | L2/L3/L4 | 页面、回答 | `site/`（发布产物） | — |

### 2.1 数据单根

**全部个人数据住在 `data/` 下，代码目录不含数据。** 这一条是硬约束，不是风格偏好：

```
data/
  raw/  inbody/  db/  reports/  logs/  state/  tests/  archive/  profile.json
```

**为什么用结构而不是规则**：数据分散时，防泄露只能靠 `.gitignore` 逐条列举，漏一条就是泄露（`docs/incidents.md` #24）。现在 `.gitignore` 只有一行 `data/*`，新增任何数据子目录天然不入库——边界由**目录结构**保证，不再依赖谁记得改规则。

`archive/` 存放已退役文档的原件（清理历史时归档），永不进仓库，只查不改。

**唯一例外**：`site/index.json`、`site/daily/`、`site/.data/` 留在 `site/` 下，因为它们是发布契约（`server.py` 与前端从固定路径取），挪了要改部署参数。它们仍是派生数据，已 ignore。

### 2.2 文件 vs 数据库的分工

| 形态 | 放什么 | 为什么 |
|---|---|---|
| **文件** | `raw/` 快照、报告 `md`、`logs/*.jsonl` | 原始证据与人类可读产物：可 diff、可手工改、可逐字节核对 |
| **SQLite** | `obs` `units` `assertions` + `daily_log` `events` `reports` 镜像 | 跨日查询：`GROUP BY` 一行解决的事，别写脚本遍历几十个文件 |

后三张表是**镜像**，不是写入真相。`data/logs/*.jsonl` 仍是追加式原始日志，
报告正文仍在 `data/reports/*.md`。写入走文件，查询走库，
由 `tools/sync_local_store.py` 单向同步（幂等，可随时重建）。

这条分工的判据是**「谁来读」**：人读的留在文件，聚合统计走 SQL。
把报告正文塞进 `BLOB` 会让 `git diff` 和编辑器排版全部失效，那是负优化。

**L3 是心脏**。它把「今天 HRV 低于基线带下沿、偏移超过 mdc_single，所以是正式破位」从散文变成一条可查询记录。没有它，「过去 90 天红灯出现在哪些维度」只能靠重读 90 份报告。

### 2.3 三个核心对象的规格

字段实现见 `scripts/db.py` 的建表语句与 `references/metrics.json`；这里只钉不可协商的语义。

**Observation（L1）** — 一次测量，永不合并、永不平均。

| 字段 | 职责 |
|---|---|
| `t` / `t_end` / `coverage` | **时间对齐的唯一出处**。睡眠属于醒来那天、负荷属于前一天，这个错位在 L1 钉死，消费方不许再猜 |
| `method` / `device` / `conditions` | 可比性判据。`comparable_on` 任一项不满足即判 `not-comparable` 且**不给差值** |
| `derived_from` | 派生观测的溯源链，口径变更时可级联重算 |

**Unit（L2）** — 一个尺度下的统计量。`scale ∈ {day, week, month, roll7, roll28, roll90}`。

| 铁律 | 内容 |
|---|---|
| 变化判定唯一在 L2 | `delta.verdict` 五档：`changed` / `within-noise` / `insufficient` / `uncalibrated` / `suppressed`。前端与报告只消费，不自己算 |
| 滚动窗禁环比 | 相邻两步重叠 6/7，变化一律用**非重叠位移差** `v_t − v_(t−k)` |
| 覆盖不足不给结论 | `coverage < 门槛` → `judge="na"`。门槛：周 4/7、月 15/当月天数、滚动 0.7×窗长 |

**单元级门槛必须按 `confidence.tier.change` 分级**——`mdc_change` 是单次读数门槛，直接套到周均会失真：

| tier.change | 指标 | 允许的结论形式 |
|---|---|---|
| `med` | hrv_ms / rhr_bpm / sleep_h / spo2_sleep_avg | 周环比 verdict + 多月斜率 |
| `low` | steps_daily / body_fat_pct / vo2max / 骨骼肌 / 去脂体重 / 内脏脂肪 | **不给周环比**，只给多月斜率或不给 |

**Assertion（L3）** — 命题 + 布尔 + 证据 + 档位。四条性质让它可规模化：`id` 稳定（可跨日统计）、`evidence` 只含已算好的数字（LLM 引用即可）、`level` 由代码定（不可改判）、可回归（`backtest diff`）。

断言是 **DAG 不是平铺**：L1 单指标断言 → L2 交叉断言（布尔组合）→ L3 总评与维度档位。**交叉规则是否触发由代码判，LLM 只解释。**

L1 断言 id 按维度命名：`sleep` / `activity` / `autonomic` / `load` / `stress_energy` / `capacity` / `bodycomp` / `data`。
`data` 维度是显式声明的一类断言——**数据缺口不是脚注，是结论的一部分**，参与总评（取 `NEUTRAL` 而非 `GOOD`）。

**MetricDef** — 一个指标只有一份定义：语义、聚合、方向、达标线映射。`references/metrics.json` 是唯一来源，`scripts/metricdefs.py` 是唯一解析器。

| 约束 | 内容 |
|---|---|
| 阈值三语义 | `goal`（展示达标）/ `judge`（判读线，未声明则退回 goal）/ `severe`（红线）。三者可以不同，但必须写在同一处并各自具名 |
| 表达式受限 | 只允许 数字 / 点号路径 / `+ - * /` / 括号，`ast` 白名单求值，不用 `eval` |
| 退路必须显式 | 个人带缺失时退回哪条规则写在 `band_fallback`；**禁止代码里藏 `or 50` 这类静默退路** |
| 解析失败必须响 | 缺路径直接抛错，`metricdefs.py validate` 与 `index.py verify` 列出每条断链（含「区间写反」） |
| 阈值可引用信源 | `ref:<指标名>` 让达标线吃设备逐日算的值（该睡多久、建议几点睡），与静态值 `max/min` 混写；取严不取松 |

---

## 三、代码 vs LLM 的边界

| 职责 | 归属 | 理由 |
|---|---|---|
| 采集、时区、单位、覆盖度 | **代码** | 不可协商 |
| 聚合（mean / median / rate / slope） | **代码** | 同上 |
| 门槛判定（mdc / 阈值 / 达标三态） | **代码** | 布尔，必须逐比特可复现 |
| 交叉规则**是否触发** | **代码**（断言 DAG） | 布尔组合，可回归测试 |
| 总评 / 红黄绿 / 维度档位 | **代码** | 取最差维度；两次跑不能不一样 |
| 数据缺口声明 | **代码** | 由 L1 coverage 直接生成 |
| 机制**选择与解释** | **LLM** | 多个自洽解释之间取舍，需要先验 |
| 与主观日志 / 自由文本的对齐 | **LLM** | 文本不可规则化 |
| 建议的措辞与个体化 | **LLM** | 同结论对"加班日"与"休息日"说法不同 |
| 提出新规则假设 | **LLM 提议 → 回测 → 人批准** | 不能直接进 rules.json |

**LLM 的输入契约**：`assertions[]` + `units[]` + `subjective_today` + `events_recent` + `gaps[]`。**禁止**给 raw、`hr_series`、`hrvReadings`、`facts.target_day` 这类原始结构——给了它就会算。

**输出校验**：`verify_judgment.py` 正则抽取报告里全部数值 → 必须命中 `assertions[].evidence` 或 `units[].value`（四位有效数字容差）→ cross 段必须携带真实触发的 `assertion_ids` → 不过则打回，连续两次回落骨架版。

---

## 四、代码组织：现状与目标

### 4.1 代码分布

| 位置 | 行数 | 内容 |
|---|---|---|
| `skills/body-health/scripts/` | 7,222 | 全部核心引擎：采集 / 物化 / 判读编排 / 调度 |
| `skills/garmin-connect-cn/scripts/` | 2,285 | 采集通道与鉴权 |
| `tools/` + `tools/homepage/` | 1,756 | 视图装配、构建、推送、冒烟 |
| `site/js` + `site/css` | 2,585 | 前端（原生 ES module，无构建） |
| `site/server.py` | 468 | 同源可写端点（标准库，单端口） |
| `skills/body-health/references/` | — | 口径与指标定义（rules / metrics / lexicon） |

`.workbuddy/skills/` 下只留指向 `skills/` 的符号链接（兼容旧命令路径与宿主 skill 发现）。**新代码一律写新路径。**

### 4.2 路径解析

**规则：代码放在哪，绝不能成为运行时契约。**

`skills/body-health/scripts/settings.py` 是唯一解析点，按三级顺序找根：

1. 环境变量 `HEALTH_ROOT`（换机器、测试、fixture 目录）
2. 从自身位置向上找**锚点**（同时存在 `tools/` 与 `docs/` 的祖先目录，最多 8 层）
3. 按旧布局兜底（`scripts/` 上四层）

关键是第 2 级用锚点而不是固定层数，所以目录搬迁是**零代码改动**：

| 布局 | 深度 | 结果 |
|---|---|---|
| `skills/body-health/scripts/`（当前） | 上 3 层 | 解析到工作区根 |
| `.workbuddy/skills/body-health/scripts/`（兼容符号链接） | 上 4 层 | 同样解析到工作区根 |

采集通道目录（`skills/garmin-connect-cn/`）由 `settings.garmin_scripts_dir()` 按候选顺序发现，改目录名时调用方无需跟着改。

**规则**：新代码一律 `from settings import ROOT / RAW_DIR / REPORTS_DIR ...`，不许再用 `__file__` 往上数。

### 4.3 仍未解除的一条

1. **判读规则与工程文档混装**：`skills/body-health/SKILL.md` 644 行里，判读 prompt 约 35%、架构约束 20%、事故复盘 30%、操作手册 15%。（班次 prompt 已于 2026-11-01 入库 `automations/`，宿主配置只剩薄加载器，见 §4.4。）
2. **判读规则与工程文档混装**：`skills/body-health/SKILL.md` 644 行里，判读 prompt 约 35%、架构约束 20%、事故复盘 30%、操作手册 15%。

### 4.4 目标形态（剩余部分）

原则一句话：**宿主降级为适配器，不是架构的一部分。** skills 放到项目根目录，任何 agent 框架用同一条命令行调用。

```
skills/                          ← 能力包放根目录，脱离任何宿主的私有约定
  body-health/
    SKILL.md                     判读说明 + 入口声明（薄）
    automations/*.md             5 个班次的 prompt 源码（入库！）
    references/                  rules.json / metrics.json / lexicon.json
    scripts/                     引擎实现（迁移期保留，最终下沉到 healthd/）
  garmin-connect-cn/             采集通道（去掉 _meta.json / _icon.png 等平台元数据）
  inbody-cn/                     体成分采集

healthd/                         ← 应用根（可选，未拍板，见 notes/backlog.md B8）
  settings.py                    唯一路径解析：HEALTH_ROOT 环境变量 → __file__ 兜底
  domain/     observations · units · assertions        纯函数
  ingest/     garmin · inbody · ports                  唯一联网鉴权
  pipeline/   materialize · facts · compose
  judge/      prompts/*.md · contract.py · verify.py
  present/    = 现 tools/homepage
  jobs/       collect · evening · morning · publish · query   ★ agent 边界

apps/web/    = 现 site/
docs/        项目文档（人读）
```

**三条规则**（新代码必须遵守，存量按迁移表改）：

1. `domain/` 不许 import `ingest/` 或 `present/`。
2. 任何脚本不许再用 `dirname(__file__)/../..` 反推根目录，一律读 `settings.ROOT`。
3. 宿主相关信息（appId、发布参数、调度配置）只允许出现在一个地方，不许散落在文档里。

---

## 五、多 agent 的做法：agent 边界 = job 边界

通信方式本来就已经是文件契约（`facts` → `judgment` → `index.json`），缺的只是把边界写死。

| Agent | 输入 | 输出 | 不许做 |
|---|---|---|---|
| 采集 | Garmin / InBody API | `data/raw/<date>.json` | 出报告、改账本 |
| 物化 | raw | `health.db` | 判读 |
| 判读 | `facts_D.json` + 判读 prompt | `judgment_D.json` | **算数、改档位** |
| 呈现 | judgment + facts | `site/index.json` | 判读 |
| 问答 | assertions / units | 回答（不落盘） | 从 raw 取数 |

四个可并行点：多日并行判读、判读与呈现解耦、口径变更并行验证（靠 `backtest diff`）、新数据源并行接入（写 `ingest/`，`domain/` 零改动）。

**唯一还缺的机制**：判读契约从散文变 schema。`findings[]` 的字段现在靠 SKILL.md 举例说明，换个模型就会漂——应该变成 `judge/contract.py` 里的强类型定义，verify 时逐字段校验。

---

## 六、剩余迁移步骤

按「先解除最硬的耦合，再动会改变行为的」排序。

| 步 | 动作 | 风险 | 验证 |
|---|---|---|---|
| **H4** | 拆 SKILL.md：prompt / 架构 / 事故 / 手册 四份 | 中，会改判读行为 | 连出 3 天与旧版逐条比 findings |
| **H5** | `data/reports/` 按机器件 / 人件分目录 | 中 | 跑补跑流程验单一终版 |
| **H6** | `make verify` + CI + 脱敏 fixture + 发布动作脚本化 | 低 | CI 绿 + 一次真实发布 |

> 班次 prompt 入库（原 H2）已完成：两班指令源在 `skills/body-health/automations/`，宿主自动化只剩「读该文件并执行」的薄加载器，改文件即生效，宿主配置不再需要人工同步。

---

## 七、一致性与可复现

| 机制 | 内容 |
|---|---|
| 三重指纹 | 每个 unit / assertion / 报告带 `data_hash`（观测集）、`rules_hash`、`engine_ver` |
| 结论过期 | 任一指纹变化 → 该产物标 `stale`，下次读到时重算并留日志 |
| 回测 | `backtest.py snapshot` 存基线，`diff` 输出新旧断言集差异，**改阈值前必跑** |
| 金标用例 | 12 个已定案日期（09-11 HRV 事故、10-22 同向偏软、10-24 单腿过门槛、10-08 久坐伪影…）写入 `data/tests/golden.json`，断言必须逐条复现 |
| 单一取值 | 取「某指标某单元的值」只走 L2；取「变没变」只走 `delta.verdict`；取「现在是多少」只走 observations |

金标用例是这套架构能不能长期活着的关键——没有它，口径改一次、历史结论就没人知道偏了多少。

---

## 八、明确不做（含否决项）

> 每一行都是「考虑过但不做什么」。**这是 git 查不到的信息**——`git log` 只会说"改成了 A"，不会说"考虑过 B 但为什么没做"。半年后有人重新提出同一方案，先来这里查。

**产品与口径**

- 不做季/年自然周期，不做周报/月报独立作业（滚动窗 7·28·90 已覆盖需求；自然周期有跨年边界问题）
- 不回填历史 findings（等于按新口径重写全部终版，违反「单一终版」）
- 不让 L4 拥有任何判定权；报告总评不允许静默降档——降档必须带 `downgrade_reason` 并并列两档
- 不统一步数两把尺子（judge=8000 判达标、goal=10000 只作目标，显式并列——统一成任一把都会把另一语义挤掉）
- 设备参考量不替代判定、只作输入且取严不取松（设备推荐按当前作息算，当达标线等于把现状正常化）
- 设备分档不映射成我们的红黄绿（「设备怎么判」与「我们怎么判」必须能同时呈现，单独存 `grades` 表透传）

**存储与数据**

- 不把 raw 迁进数据库（raw 是可读、可 diff、可人工核对的最后一道防线；派生层可随时重建）
- 不保留云服务副本（全项目无写入方的死配置，只会让「数据存在哪」含糊并暴露密钥）
- 定时任务不搬云服务、不在 server.py 自起 cron 线程（云服务无可执行环境；自起线程有冷启动回收、反代超时、无持久文件系统三个硬障碍。cron 不是瓶颈，判读才是）

**工程**

- 不引入消息队列 / 流处理——单用户、日频，SQLite + 增量函数足够（vision.md 的流批一体不需要真流式基建）
- 不引入 Web 框架（`server.py` 标准库够用）；不给前端加构建步骤（内容指纹已解决缓存问题）
- 不做页内对话框 / 页面内嵌会话：对话智能归宿主 agent，与侧边栏页面预览协作（深链衔接）；页内嵌会话需要开写端点与页面鉴权，破坏「公开站点纯只读」的防线（见 `vision.md` §五）
- 不抽判读规则让 LLM 读散文自己判（同一份数据三处三种说法且无法回归测试——判定进代码、叙述进 LLM）
- 不清理转 private 前的旧 commit 残留（filter-branch 强推后 GitHub 仍保留不可达对象，彻底消除只有删库或联系 Support；转 private 已使匿名 404。**再见到这个问题不要当新问题提**）
- 判读规则不放 `rules.json` 之外、个人档案不进口径文件（口径文件要能公开，个人基线不能公开）
