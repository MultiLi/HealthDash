# 文档地图与划分规范

> 本文回答一个具体问题：**我想写点东西，应该写在哪？**
>
> 写错地方是这套系统里最贵的错误——同一条知识散在三处，改一处就漂两处，而且没有任何测试能发现漂移。

---

## 一、三类文档，三种读者，三种写法

划分依据不是「内容主题」，而是**谁读、会不会被机器执行**。同一个主题（比如"HRV 低于基线带怎么判"）在三个地方出现是合理的，但**写法必须不同**。

| 类 | 读者 | 回答什么问题 | 写法 | 位置 |
|---|---|---|---|---|
| **A 项目文档** | 人（维护者、协作者、三个月后的你） | 为什么这么设计？怎么维护？踩过什么坑？ | 叙事、给背景、承认取舍、可以写"我们当初试过 X，失败了" | `docs/` |
| **B 接入契约** | Agent（任意框架，第一次加载） | 我是谁？我能调什么？我禁止做什么？怎么证明我做对了？ | 祈使句、无历史、每条断言都必须有命令或路径支撑 | `AGENT.md` + `skills/*/` |
| **C 领域知识** | 人（判读时查）+ 判读 agent（当依据） | 生理学上这个数字意味着什么？个人基线是多少？ | 事实与判据、附证据来源、可被引用 | `references/` + `notes/` 里的基线文档 |
| **D 记录** | 自己 | 今天改了什么？当时为什么那么决定？ | 追加式、带时间戳、允许口语 | `.workbuddy/memory/[日期].md` |

**关键区别**：A 和 B 是**产物**，写一次就要同步维护；D 是**流水**，写完不再回头改。

---

## 二、地图

```
AGENT.md                   ★ Agent 接入的唯一入口。任何 agent 框架先读这个。
README.md                  人的入口。项目是什么、怎么跑起来、去哪看细节。

docs/
  README.md                本文件：地图与划分规范
  architecture.md          ★ 当前架构的唯一权威描述（分层、真相源、边界）
  operations.md            ★ 运维手册：每天/每周要做什么、怎么验证
  frontend.md              前端与主页参考（原 tools/HOMEPAGE_NOTES.md）
  incidents.md             ★ 事故库：判读陷阱的实证记录，每条带日期与数字
  decisions.md             决策记录：已定 / 待定，含否决选项与理由

notes/                     历史记录与领域知识原始档案。**整体不入库**（含真实身体数值）。
  architecture_v4.md       2026-10-25 的架构推导过程与 S1/S2 落地记录（历史，不再更新）
  architecture_review_2026-10-25.md  工程分层与宿主解耦的审视（历史）
  confidence_model.md      ★ 噪声门槛（mdc）的推导与实测值
  gait_cadence_baseline.md ★ 步频基线（领域知识）
  leg_circumference_method_map.md   围度测量方法（领域知识）
  inbody_baseline_2026-10-18.md     体成分基线（领域知识）
  backlog.md               欠账清单（滚动更新）—— **唯一入库的 notes 文件**

references/                口径数值与指标定义（代码读它，不是文档）
  rules.json               阈值与 confidence 噪声门槛（**只放通用口径，不含个人档案**）
  profile.example.json     个人档案模板（真实值在 data/profile.json，不入库）
  metrics.json             指标注册表（语义、聚合、达标方向）
  lexicon.json             呈现词表（断言 id 的中文名）
  exercise_science.md      运动学判据

skills/                    能力包（2026-10-25 H3 起在根目录；代码读它，不是文档）
  body-health/scripts/     引擎：采集/物化/判读编排/调度；settings.py 是路径唯一入口
  body-health/references/  口径（rules / metrics / lexicon / profile 模板）
  body-health/SKILL.md     判读说明（面向 agent）
  garmin-connect-cn/       采集通道与鉴权

tools/                     构建、推送、冒烟、交互探针、数据守卫、加密备份
site/                      前端（原生 ES module，无构建）+ 同源可写服务

data/                      ★ 唯一数据根，整体不入库（结构见 data/README.md）
  raw/ inbody/ db/ reports/ logs/ state/ tests/ profile.json
.workbuddy/skills/         ← 两个指向 skills/ 的符号链接（兼容旧路径，勿删）
```

代码分层与调用关系见 `docs/architecture.md`。

---

## 三、判定表：新内容该写哪

| 你要写的东西 | 写这里 | 不要写这里 |
|---|---|---|
| 「这个模块为什么拆成两层」 | `docs/architecture.md` | SKILL.md（那是给 agent 的执行说明） |
| 「怎么跑一次补数」 | `docs/operations.md` | README（README 只放入口链接） |
| 「10-08 久坐 22h 是分母伪影」 | `docs/incidents.md` | 每日日志（没人会回头翻） |
| 「今天修了什么、被什么坑到」 | `.workbuddy/memory/[日期].md` | docs/（那不是流水账） |
| 「agent 调用时必须先 resolve 目标日」 | `AGENT.md` 或对应 skill | docs/（协作者不需要知道执行细节） |
| 「判读报告该怎么写、禁止什么」 | `skills/*/SKILL.md` 的判读 prompt 段 | docs/architecture.md |
| 「步数达标线是 8000」 | `references/rules.json` | 任何 md 文件（会漂） |
| 「HRV 带下沿 57」 | `references/metrics.json` + `@personal` 指向 rules | 报告模板 |
| 「某指标的噪声门槛是 20ms」 | `references/rules.json` 的 `confidence` | incidents.md（那里只写"当时怎么判断的"） |
| 「待办、欠账」 | `notes/backlog.md` | README |
| 「新阈值方案，还没定」 | `docs/decisions.md` 的「待定」段 | incidents.md |

**一条判据**：如果这条知识**需要在两个地方保持一致**，那它必须只存在于一处，另一处用链接。任何"复制粘贴保持同步"的做法都会漂。

---

## 四、四个反模式（都是已经发生过或险些发生的）

1. **把事故写进 SKILL.md**
   SKILL.md 是给 agent 的执行说明。事故复盘写进去，agent 每晚要吞 644 行，其中 65% 与「今天怎么判」无关。事故的正确形态是 `docs/incidents.md` 里一条带日期的记录 + 一条进了 `data/tests/golden.json` 的断言。

2. **把 prompt 当文档写**
   「解释为什么这样做」和「命令 agent 这样做」是两种文体。混写的结果是 agent 读到大量背景后开始自由发挥。判读 prompt 里只留祈使句和拒判条件。

3. **把数字写进 md**
   任何阈值、基线、门槛，只要代码要用，就必须在 `rules.json` / `metrics.json` 里。md 里写死一个 8000，三个月后就是静默漂移。文档里只能写"达标线见 `rules.activity.steps_good`"。

4. **把结论留在记忆里**
   `.workbuddy/memory/MEMORY.md` 是**agent 的常驻上下文**，不是架构文档。它现在 30KB，承载了大量本该由代码或 docs 承担的职责。有长期价值的结论应该逐步下沉：能变成断言的进 `assertions.py`，能变成规范的进 `docs/`，只有个人偏好和会话纪律才留记忆。

---

## 五、与记忆系统的关系

| 文件 | 入库 | 性质 | 谁写 |
|---|---|---|---|
| `.workbuddy/memory/MEMORY.md` | ✅ | 人工策展的长期上下文（偏好、纪律、数据地图） | 每次会话按需更新 |
| `.workbuddy/memory/personal_action_notes.md` | ✅ | 动作/功能类主诉的判据沉淀 | 同上 |
| `.workbuddy/memory/[日期].md` | ❌ ignored | 每日工作流水 | 每次做完实质性工作追加 |
| `.workbuddy/memory/automations/*/memory.md` | ❌ ignored | 定时班次的执行记录 | 各自动化自己写 |
| `data/logs/events.jsonl` | ❌ ignored | 个人健康原文（症状/作息/饮食） | 会话中即时归档 |
| `data/logs/daily_log.jsonl` | ❌ ignored | 主观标量（体重等） | 晨间补数 |

**为什么日志不入库**：append-only 文件天天变，diff 噪音会淹没真正的代码变更；多 agent 并行时同一文件必冲突。有长期价值的判断已经写进 `docs/` 或 `MEMORY.md`，日志只是过程。

---

## 六、写作检查表

写完一份文档，问四句：

1. **它的读者是谁？** 答不出就是还没想清楚该放哪。
2. **它引用了会变的数字吗？** 有 → 换成路径引用。
3. **它和别的文件重复吗？** 有 → 删掉重复那份，留一个指针。
4. **半年后有人会来改它吗？** 不会 → 它可能是历史记录，该进 `notes/` 而不是 `docs/`。
