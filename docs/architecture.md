# 当前架构

> **本文件是架构的唯一权威描述。** `notes/architecture_v4.md` 是 2026-10-25 的推导过程与 S1/S2 落地记录，作为历史保留，**不再更新**——它的结论已吸收进本文。
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

| 层 | 输入 | 输出 | 谁写 | 可复现 |
|---|---|---|---|---|
| L0 | Garmin / InBody API | 日快照、扫描文件 | 采集脚本 | 逐字节 |
| L1 | L0 + 适配器 | `obs` 表 | `adapters` + `index.py` | 逐字节 |
| L2 | L1 + rules | `units` 表 | `units.py` | 逐字节 |
| L3 | L2 + rules | `assertions` 表 | `assertions.py` | 逐字节 |
| L4 | **只读 L3** | 报告 md / judgment json | `compose.py` + LLM | 骨架逐字节，叙述不确定 |
| L5 | L2/L3/L4 | 页面、回答 | 前端 / agent | — |

**L3 是心脏**。它把「今天 HRV 38，基线 57–80，差 −19 > mdc_single 15，所以是正式破位」从散文变成一条可查询记录。没有它，「过去 90 天红灯出现在哪些维度」只能靠重读 90 份报告。

三个核心对象的字段定义与设计取舍见 `notes/architecture_v4.md` §3。

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

### 4.1 现状（2026-10-25）

| 位置 | 行数 | 内容 |
|---|---|---|
| `.workbuddy/skills/body-health/scripts/` | 7,222 | 全部核心引擎：采集 / 物化 / 判读编排 / 调度 |
| `.workbuddy/skills/garmin-connect-cn-data__skillhub/scripts/` | 2,285 | 采集通道与鉴权 |
| `tools/` + `tools/homepage/` | 1,756 | 视图装配、构建、推送、冒烟 |
| `site/js` + `site/css` | 2,585 | 前端（原生 ES module，无构建） |
| `site/server.py` | 468 | 同源可写端点（标准库，单端口） |
| `references/` | — | 口径与指标定义 |

**83% 的后端代码住在 `.workbuddy/skills/`** —— 一个名字就宣告「我是某 Agent 平台插件」的目录。这不是设计选择，是历史结果，代价在下一节。

### 4.2 路径解析（H1 已于 2026-10-25 解除）

**曾经的病灶**：13 个脚本各写一遍 `dirname(__file__)/../../../..` 反推工作区根，外加 `tools/homepage/paths.py` 用一条硬编码字符串指回引擎目录。**代码放在哪就是运行时契约**——搬一次目录，13 处同时断，且报错位置离根因很远。这才是「代码改不动」的技术根因。

**现在**：`scripts/settings.py` 是唯一解析点，按三级顺序找根：

1. 环境变量 `HEALTH_ROOT`（换机器、测试、fixture 目录）
2. 从自身位置向上找**锚点**（同时存在 `tools/` 与 `docs/` 的祖先目录，最多 8 层）
3. 按旧布局兜底（`scripts/` 上四层）

关键是第 2 级用锚点而不是固定层数，所以两种布局都能命中：

| 布局 | 深度 | 结果 |
|---|---|---|
| `.workbuddy/skills/body-health/scripts/`（当前） | 上 4 层 | 解析到工作区根 |
| `skills/body-health/scripts/`（H3 目标） | 上 3 层 | 同样解析到工作区根，**零代码改动** |

`_SKILLS`（`_HERE/../..`）这类相对层级在搬迁后仍成立，无需改——两个 skill 会一起搬。

**规则**：新代码一律 `from settings import ROOT / RAW_DIR / REPORTS_DIR ...`，不许再用 `__file__` 往上数。`tools/homepage/paths.py` 同样改为按候选顺序定位脚本目录（先 `skills/`，后 `.workbuddy/skills/`），不再写死路径。

### 4.3 其余两条约束（仍未解除）

1. **编排在宿主里**：5 个定时班次的 prompt 存在 WorkBuddy 自动化配置，不在仓库。仓库里只有执行记忆。
2. **判读规则与工程文档混装**：`SKILL.md` 644 行里，判读 prompt 约 35%、架构约束 20%、事故复盘 30%、操作手册 15%。

### 4.3 目标形态

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

healthd/                         ← 应用根（可选，见 decisions.md D3）
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

## 六、工程迁移路径

按「先解除最硬的耦合，再动会改变行为的」排序。**H1 是其余全部的前置条件。**

| 步 | 动作 | 风险 | 验证 |
|---|---|---|---|
| **H1** | ✅ **2026-10-25 完成**：`scripts/settings.py` 集中路径解析（`HEALTH_ROOT` 环境变量优先，其次向上找锚点）；14 处反推改为导入；`tools/homepage/paths.py` 改为按候选顺序定位；新增 `pyproject.toml`（依赖与工具配置的唯一声明，引擎零第三方依赖） | 低，零行为变化 | 22 个模块全部导入成功、根解析一致；`index.py verify` 11 项 / `backtest golden` 12/12 / `smoke_site` 113 项 / `probe_interactions` 8/8 全过；另做**搬迁布局模拟**（把 `settings.py` 放到 `skills/body-health/scripts/` 的三层结构下）确认解析不变 |
| **H2** | 5 个班次 prompt 落进 `skills/*/automations/`，宿主配置只留「读该文件」 | 低 | 手动触发一次 10:00 班比对行为 |
| **H3** | `skills/` 搬到根目录；`references/` 随行；清掉平台元数据 | 中，需独占窗口 | 同上三套 + probe |
| **H4** | 拆 SKILL.md：prompt / 架构 / 事故 / 手册 四份 | 中，会改判读行为 | 连出 3 天与旧版逐条比 findings |
| **H5** | `reports/` 按机器件 / 人件分目录 | 中 | 跑补跑流程验单一终版 |
| **H6** | `make verify` + CI + 脱敏 fixture + 发布动作脚本化 | 低 | CI 绿 + 一次真实发布 |

H1+H2 不碰任何线上行为。H3 之后所有事都变简单，但它是最大的一次机械改动。

---

## 七、一致性与可复现

| 机制 | 内容 |
|---|---|
| 三重指纹 | 每个 unit / assertion / 报告带 `data_hash`（观测集）、`rules_hash`、`engine_ver` |
| 结论过期 | 任一指纹变化 → 该产物标 `stale`，下次读到时重算并留日志 |
| 回测 | `backtest.py snapshot` 存基线，`diff` 输出新旧断言集差异，**改阈值前必跑** |
| 金标用例 | 12 个已定案日期（09-11 HRV 事故、10-22 同向偏软、10-24 单腿过门槛、10-08 久坐伪影…）写入 `tests/golden.json`，断言必须逐条复现 |
| 单一取值 | 取「某指标某单元的值」只走 L2；取「变没变」只走 `delta.verdict`；取「现在是多少」只走 observations |

金标用例是这套架构能不能长期活着的关键——没有它，口径改一次、历史结论就没人知道偏了多少。

---

## 八、明确不做

- 不做季/年自然周期（滚动窗覆盖需求）；不做周报/月报独立作业
- 不回填历史 findings
- 不引入消息队列 / 流处理——单用户、日频，SQLite + 增量函数足够
- 不让 L4 拥有任何判定权
- 不把 raw 迁进数据库——raw 是可读、可 git 化、可人工核对的最后一道防线
- 不引入 Web 框架（`server.py` 468 行标准库够用）
- 不给前端加构建步骤（内容指纹已解决缓存问题）
