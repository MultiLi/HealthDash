# 文档地图与划分规范

> 本文回答一个具体问题：**我想写点东西，应该写在哪？**
>
> 写错地方是这套系统里最贵的错误——同一条知识散在三处，改一处就漂两处，而且没有任何测试能发现漂移。

---

## 一、三类文档，三种读者，三种写法

划分依据不是「内容主题」，而是**谁读、会不会被机器执行**。同一个主题（比如"HRV 低于基线带怎么判"）在三个地方出现是合理的，但**写法必须不同**。

| 类 | 读者 | 回答什么问题 | 写法 | 位置 |
|---|---|---|---|---|
| **A 项目文档** | 人（维护者、协作者、三个月后的你） | 为什么这么设计？怎么维护？踩过什么坑？ | 叙事、给背景、承认取舍 | `docs/` |
| **B 接入契约** | Agent（任意框架，第一次加载） | 我是谁？我能调什么？我禁止做什么？怎么证明我做对了？ | 祈使句、无历史、每条断言都必须有命令或路径支撑 | `AGENT.md` + `skills/*/` |
| **C 领域知识** | 人（判读时查）+ 判读 agent（当依据） | 生理学上这个数字意味着什么？个人基线是多少？ | 事实与判据、附证据来源、可被引用 | `skills/*/references/` + `notes/` 的基线文档 |

**没有第四类。** 「今天改了什么、当时为什么」不写进任何文件——它由 `git log` 承载，见下节。

---

## 二、历史归 git（2026-11-01 立）

> 仓库接入 git 之后，「变更记录」就成了第二本账。第二本账必然漂：它靠人记得更新，而 git 靠 `commit` 这个动作强制记录。

### 三条铁律

**1. 文件只写当前真相。**
任何一句描述"曾经怎样"，只要"现在怎样"已经在文中，就删掉"曾经"。

禁用词（出现在文档正文里即为缺陷）：`已修复` `已废弃` `已迁移` `v2/v3/v4` `升级日志` `变更记录` `变更对照` `此前是错的` `原来的方案是` `第一版做错`。

**2. 查历史只查 `git log`。**
配套要求：**commit message 必须写 why**，不能是「更新文档」「修 bug」。
判据：把 `git log --oneline` 从头看到尾，能否还原出"为什么变成现在这样"。不能，就是这个 commit 没写清。

**3. 会变的东西一律换引用。**
阈值 → 路径（`rules.activity.steps_good`）；实现 → 文件路径；实测值 → `data/` 下的产物。文档里不许出现"当时测得 X"。

### 两类例外：git 查不到，必须留在文件里

git 记录「改了什么」，**不记录「考虑过但不做什么」**。所以只有两类历史有资格留下：

| 类 | 位置 | 留什么 | 删什么 |
|---|---|---|---|
| **决策的否决理由** | `docs/decisions.md` | 一行：选了什么 + 否决了什么 + 一句理由 | 讨论过程、谁在何时说了什么、定了几次 |
| **事故的根因与防线** | `docs/incidents.md` | 现象 + 根因 + **现在的防线** | 复盘时间线、修了几轮、当时的情绪 |

**唯一的判据：这条历史能不能变成今天的一个检查项或一条禁令？**
能 → 留下，但形态必须是规则（`incidents` 落成防线、`decisions` 落成约定、能写成布尔表达式的落成断言或测试）。
不能 → 删掉，它只是过程。

⚠️ **「待定」不算例外**：待定项是**未来**，不是历史，`decisions.md` 第二部分照常维护。拍板后从待定移到已定，不留两份。

### 反例（全部真实发生过）

| 现象 | 病根 | 处置 |
|---|---|---|
| `MEMORY.md` 47 行「升级日志」，占全文 43% | 每次升级追加一段，内容全是"当时改了什么" | 整节删除，`git log` 有 |
| `notes/backlog.md` 里 80% 是 ✅ 已完成项 | 待办清单被当成了履历 | 完成即删，只留未办 |
| `README.md`「当前状态」列着 H1/H3 待办 | 那两条当天就完成了 | 整节删除 |
| `docs/decisions.md`「已推翻的决策」表 | 用一张表追踪"哪条不算了"——这是编年史 | 教训并入现行决策的理由里，表删除 |

### 提交纪律（与上面对称）

1. 一个 commit 一件事。
2. message 写「为什么」，不写「改了什么」——改了什么 `git show` 自己会说。
3. **删历史的 commit 本身就是历史的存档**：message 必须写清删了什么、为什么可以删、结论现在在哪。
4. 删掉的、不入库的原件，先归档到 `data/archive/`（在忽略范围内，永不进仓库）。

---

## 三、地图

```
AGENT.md                   ★ Agent 接入的唯一入口。任何 agent 框架先读这个。
README.md                  人的入口。项目是什么、怎么跑起来、去哪看细节。

docs/
  README.md                本文件：地图、划分规范、历史归 git
  architecture.md          ★ 当前架构的唯一权威描述（分层、真相源、边界）
  vision.md                ★ 愿景与数据模型总纲（流批关系、事件不可变、推进顺序）
  operations.md            ★ 运维手册：每天/每周要做什么、怎么验证
  workflow.md              ★ 开发流程：分支模型、worktree 隔离、PR 门禁（改代码前先读）
  frontend.md              前端与主页参考
  incidents.md             ★ 事故库：每条 = 现象 + 根因 + 现在的防线
  decisions.md             决策台账：已定（含否决项）/ 待定

notes/                     领域知识与欠账。**整体不入库**（含真实身体数值）。
  judgment-discipline.md   ★ 判读纪律全本（补数/采集/负荷/恢复/口径）
  confidence_model.md      ★ 噪声门槛（mdc）的推导与实测值
  gait_cadence_baseline.md 步频基线
  leg_circumference_method_map.md  围度测量方法
  inbody_baseline_2026-10-18.md    体成分基线
  backlog.md               欠账清单（滚动更新）—— **唯一入库的 notes 文件**

skills/body-health/references/   口径数值与指标定义（代码读它，不是文档）
  rules.json               阈值与 confidence 噪声门槛（只放通用口径，不含个人档案）
  metrics.json             指标注册表（语义、聚合、达标方向）
  lexicon.json             呈现词表（断言 id 的中文名）
  exercise_science.md      运动学判据
  profile.example.json     个人档案模板（真实值在 data/profile.json，不入库）

skills/                    能力包
  body-health/scripts/     引擎：采集/物化/判读编排/调度；settings.py 是路径唯一入口
  body-health/SKILL.md     判读说明（面向 agent）
  garmin-connect-cn/       采集通道与鉴权

tools/                     构建、推送、冒烟、交互探针、数据守卫、加密备份、CI
site/                      前端（原生 ES module，无构建）+ 同源可写服务

data/                      ★ 唯一数据根，整体不入库（结构见 data/README.md）
  raw/ inbody/ db/ reports/ logs/ state/ tests/ archive/ profile.json
```

代码分层与调用关系见 `docs/architecture.md`。

---

## 四、判定表：新内容该写哪

| 你要写的东西 | 写这里 | 不要写这里 |
|---|---|---|
| 「这个模块为什么拆成两层」 | `docs/architecture.md` | SKILL.md（那是给 agent 的执行说明） |
| 「怎么跑一次补数」 | `docs/operations.md` | README（README 只放入口链接） |
| 「改代码怎么开分支、怎么跑门禁」 | `docs/workflow.md` | AGENT.md（那里只放禁令，不放步骤） |
| 「10-08 久坐 22h 是分母伪影」 | `docs/incidents.md` | 任何日志（没人会回头翻） |
| 「今天修了什么、被什么坑到」 | **commit message** | 任何 md 文件 |
| 「agent 调用时必须先 resolve 目标日」 | `AGENT.md` 或对应 skill | docs/ |
| 「判读报告该怎么写、禁止什么」 | `skills/*/SKILL.md` 的判读 prompt 段 | `docs/architecture.md` |
| 「步数达标线是 8000」 | `skills/*/references/rules.json` | 任何 md（会漂） |
| 「某指标的噪声门槛是 20ms」 | `references/rules.json` 的 `confidence` | `incidents.md`（那里只写当时的判据） |
| 「待办、欠账」 | `notes/backlog.md` | README |
| 「新方案，还没定」 | `docs/decisions.md` 的「待定」段 | `incidents.md` |

**一条判据**：如果这条知识**需要在两个地方保持一致**，那它必须只存在于一处，另一处用链接。任何"复制粘贴保持同步"的做法都会漂。

---

## 五、四个反模式

1. **把事故写进 SKILL.md**
   SKILL.md 是给 agent 的执行说明。事故复盘写进去，agent 每晚要吞几百行，其中大半与「今天怎么判」无关。事故的正确形态是 `docs/incidents.md` 里一条「现象 + 根因 + 防线」+ 一条进了 `data/tests/golden.json` 的断言。

2. **把 prompt 当文档写**
   「解释为什么这样做」和「命令 agent 这样做」是两种文体。混写的结果是 agent 读到大量背景后开始自由发挥。判读 prompt 里只留祈使句和拒判条件。

3. **把数字写进 md**
   任何阈值、基线、门槛，只要代码要用，就必须在 `rules.json` / `metrics.json` 里。md 里写死一个 8000，三个月后就是静默漂移。文档里只能写"达标线见 `rules.activity.steps_good`"。

4. **把结论留在记忆里**
   `MEMORY.md` 是**agent 的常驻上下文**，不是架构文档，也不是变更日志。它有长期价值的结论应该逐步下沉：能变成断言的进 `assertions.py`，能变成规范的进 `docs/`，只有会话纪律、个人偏好、环境坑才留记忆。**记忆里出现日期流水，就是该删的信号。**

---

## 六、与记忆系统的关系

| 文件 | 入库 | 性质 | 谁写 |
|---|---|---|---|
| `.workbuddy/memory/MEMORY.md` | ❌ ignored | 会话引导：环境坑、执行锚点、工程约定（含个人数值，故不入库） | 每次会话按需收敛 |
| `.workbuddy/memory/personal_action_notes.md` | ❌ ignored | 动作/功能类主诉的判据沉淀 | 同上 |
| `.workbuddy/memory/[日期].md` | ❌ ignored | **会话草稿**：未定的事、待办、Sir 的口头偏好。**不记已完成的工程历史** | 需要时写 |
| `data/logs/events.jsonl` | ❌ ignored | 个人健康原文（症状/作息/饮食） | 会话中即时归档 |
| `data/logs/daily_log.jsonl` | ❌ ignored | 主观标量（体重等） | 晨间补数 |
| `data/archive/` | ❌ ignored | 已删除/已退役文档的原件，只查不改 | 清理历史时归档 |

**为什么日志不入库**：append-only 文件天天变，diff 噪音会淹没真正的代码变更；多 agent 并行时同一文件必冲突。有长期价值的判断已经沉进 `docs/` 或代码，流水由 git 承载。

---

## 七、写作检查表

写完一份文档，问五句：

1. **它的读者是谁？** 答不出就是还没想清楚该放哪。
2. **它引用了会变的数字吗？** 有 → 换成路径引用。
3. **它描述了"曾经怎样"吗？** 有 → 删掉，除非它是 `decisions.md` 的否决项或 `incidents.md` 的防线。
4. **它和别的文件重复吗？** 有 → 删掉重复那份，留一个指针。
5. **半年后有人会来改它吗？** 不会 → 它是历史，该进 `git log` 或 `data/archive/`，不该在 `docs/` 里占位。
