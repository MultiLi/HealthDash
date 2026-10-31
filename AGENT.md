# AGENT.md — 接入契约

> **本文件是任何 agent 接入本项目的唯一入口。** 读完这一份就能知道：这是什么、你能调什么、你禁止做什么、怎么证明你做对了。
>
> 它只写**契约**，不写判读规则（在 skill 里）、不写设计理由（在 `docs/` 里）、不写历史（在 `git log` 里）。三者的分工见 `docs/README.md`。

---

## 1. 这是什么

个人健康数据中台：Garmin / InBody 采集 → 规范化观测 → 多尺度聚合 → 断言判定 → 日报告与健康主页。

**关键事实**：判定（数字、门槛、红黄绿）由代码算，不由你算。你的职责是**编排**与**叙述**，不是**计算**。

---

## 2. 环境

| 项 | 值 |
|---|---|
| 工作区根 | `/Users/tian.li/Documents/Health`（下文记作 `$W`） |
| 核心代码 | `$W/skills/body-health/scripts`（记作 `$S`）。`.workbuddy/skills/body-health` 是指向它的符号链接（兼容旧命令），**不要再写进新代码** |
| **路径解析** | `$S/settings.py` 是**唯一入口**：`HEALTH_ROOT` 环境变量 > 向上找锚点（含 `tools/`+`docs/` 的祖先）> 旧布局兜底。**不许再用 `__file__` 往上数层** |
| 采集通道 | `$W/skills/garmin-connect-cn/scripts`（旧名 `garmin-connect-cn-data__skillhub` 保留为兼容符号链接） |
| 运行时服务 | `$W/site/server.py`（标准库，单端口） |
| Python | `python3`（3.9+）；引擎零第三方依赖，见 `pyproject.toml` |
| **代理坑** | 系统代理指向 `127.0.0.1:1087/1080` 但进程常不在。**Python urllib 会 `Connection refused`，curl 正常**。跑任何联网脚本前加 `no_proxy='*' NO_PROXY='*'` |

---

## 3. 能力清单

每条都是「命令 → 产出 → 副作用」。**副作用列有内容时，必须确认再跑。**

| 能力 | 命令 | 产出 | 副作用 |
|---|---|---|---|
| 解析目标日 | `python3 $S/slot.py resolve --job evening\|morning` | `targets[]`（要处理的日期） | 无 |
| 采集 | `python3 $S/collect.py --days 3` | `data/raw/*.json`、`health.db` 重建、主页数据 | 写 raw、**推主页** |
| 采集（不推） | `python3 $S/collect.py --days 3 --no-push` | 同上，不推 | 写 raw |
| 物化 | `python3 $S/index.py build` | `health.db`（obs/units/assertions） | 可重建 |
| 自检 | `python3 $S/index.py verify` | 11 项一致性检查 | 无 |
| 路径自检 | `python3 $S/settings.py` | 打印 root / references / raw / reports 的解析结果 | 无 |
| 事实包 | `python3 $S/fact_pack.py --date D --days 4` | `data/reports/facts_D.json` | 覆盖该文件 |
| 报告骨架 | `python3 $S/compose.py --date D` | `data/reports/body_brief_D.skeleton.md` | **只写 skeleton** |
| 判读自检 | `python3 $S/verify_judgment.py --date D` | 五项校验结果 | 无 |
| 记完成 | `python3 $S/slot.py mark done --data-cutoff HH:MM` | `data/state/slots.json` | **不可逆**，会阻止重出 |
| 主页构建 | `python3 tools/build_homepage_data.py` | `site/index.json` + `site/daily/` | 覆盖产物 |
| **部署编排** | `python3 tools/deploy.py`（= 检查 + 需要就热更新）/ `--check` 只看 | 输出 `noop`/`hot_update`/`republish`；exit 2 = 需要重新发布 | 改了线上数据（热更新时） |
| 发布后登记 | `python3 tools/deploy.py --mark-app-deployed` | 更新上线游标 | 只写本地游标 |
| 主页推送 | `python3 tools/push_data.py` | 线上 `.data/` | 改线上数据 |
| 推送核验 | `python3 tools/push_data.py --check` | exit 0 = 线上本地逐文件一致 | 无 |
| 加密备份 | `python3 tools/backup_health.py --list`（只列）/ `--out <目录>`（打包） | `health_backup_<时间戳>.tar.gz.enc` | 写外部目录；口令走交互或 `HEALTH_BACKUP_PASS` |
| 回测 | `python3 $S/backtest.py diff` | 口径变更前后改判清单 | 无 |
| 金标 | `python3 $S/backtest.py golden` | 12 条锚点是否复现 | 无 |
| 前端冒烟 | `node tools/smoke_site.mjs` | 113 项断言 | 无 |
| 交互探针 | `python3 tools/probe_interactions.py` | 8 用例真浏览器点击 | 无 |

**完整参数与坑见各脚本 `--help` 和 `docs/operations.md`。**

---

## 4. 硬边界

以下六条**没有例外**。违反其中任何一条，产出的东西即使数字对也不可信。

| # | 禁止 | 原因 |
|---|---|---|
| 1 | **不许自己算数**。所有数字必须来自 `facts_D.json` 的 `assertions[].evidence` 或 `units[].value` | 你自己算第二次，就会和主页、和昨天的报告不一致。`verify_judgment.py` 会抓，但别指望它总能抓全 |
| 2 | **不许改总评档位**（红/黄/绿/⚪）。`rollup` 是代码定的 | 降档必须显式写 `downgrade_reason`，否则就是偷偷改判 |
| 3 | **不许凭报告文本判断数据完整性**。一律读 `data/raw/*.json` 的 `usersummary_daily.wellnessEndTimeLocal` | 关键词搜索必漏；「覆盖全天」这种自述与事实无关（`docs/incidents.md` #1） |
| 4 | **不许二次登录 Garmin**。token 在 `~/.config/garmin-cn/garth`，由 `sync.py` 自己管 | 会触发风控，且多套鉴权路径并存必出事故 |
| 5 | **不许覆盖终版报告**。`compose.py --write` 只写 `.skeleton.md`；`--as-final` 才碰正式报告且需 `--force` | 终版报告是唯一的健康档案，没有软删除可退 |
| 6 | **不许因「数据还没同步」就重出报告或催数**。先看 `lastSyncTimestampGMT` | 手表没同步 ≠ 拉数失败。提高拉数频率不会让云端凭空多出数据 |

---

## 5. 输入输出契约

整条链路是**文件契约**，不是函数调用。你只需知道每个环节读写哪个文件。

```
data/raw/<YYYY-MM-DD>.json            采集快照（不可变，只在缺失或截断时重拉）
  ↓ index.py build
data/db/health.db                     obs → units → assertions（可删除重建）
  ↓ fact_pack.py --date D
data/reports/facts_D.json             ★ 判读唯一合法输入（assertions / units / scales / gaps）
  ↓ 你（判读）
data/reports/judgment_D.json          判读结论（结构校验见 verify_judgment.py）
  ↓ build_homepage_data.py（同一进程内顺带把主观日志与报告镜像进库）
site/index.json + site/daily/*.json   前端数据层
  ↓ push_data.py
线上 .data/                           热更新，不需要重新发布
```

**全部个人数据都在 `data/` 下，这个目录整体不入库。** 你要读写的数据只能是
`data/` 下的路径；如果发现数据出现在别处（`reports/`、`notes/events.jsonl` 这类
旧位置），说明有人在按旧布局操作，应当纠正而不是照做。

**你可以读**：`facts_D.json`、`slot.py resolve` 的输出、`docs/incidents.md`、对应的 skill 说明。
**你不该读**：`data/raw/*`（除完整性判断）、`hr_series`、`hrvReadings`——给了你就会忍不住自己算。

---

## 6. 首次接入的阅读顺序

| 顺序 | 文件 | 读它是为了 |
|---|---|---|
| 1 | 本文件 | 契约与禁令 |
| 2 | `skills/body-health/SKILL.md` | 判读怎么说、怎么写、什么时候拒判 |
| 3 | `docs/operations.md` | 具体流程与验证手段 |
| 4 | `docs/incidents.md` | 每条都是「现象 + 根因 + 现在的防线」，省你重复踩 |
| 5 | `docs/architecture.md` | 只在你要改代码时读 |

**不要一上来读全部 `notes/`**：那里是领域知识与个人基线，读完只会让你对旧结论产生依赖；判读只以 `facts_D.json` 为准。

---

## 7. 怎么证明你做对了

**先跑这一条**（本地和 CI 跑的是同一份，覆盖下面的大部分）：

```bash
python3 tools/ci/check.py
```

它依次验：数据守卫 / Python 语法与 3.9 下限 / JS 语法 / 路径锚点 / 数据边界结构 /
前端冒烟 / 判读金标。exit 0 才继续。**它报 SKIP 不等于通过**——SKIP 是「这项没验」。

跑完一轮判读，这三条必须同时成立：

```bash
python3 $S/verify_judgment.py --date D   # 总评一致 / findings 覆盖 / 数字溯源 / 禁用词
python3 $S/backtest.py golden            # 12 条历史锚点必须 12/12（check.py 已含）
python3 $S/index.py verify               # 11 项一致性自检 exit 0
```

改过前端再加：

```bash
node tools/smoke_site.mjs                # 已含在 check.py 里
python3 tools/probe_interactions.py      # 8 用例（需真浏览器，不在 check.py 里）
```

**任何一项红了，先分清是真错还是锚点失效**（见 `docs/operations.md` 的「冒烟测试的锚点纪律」）。不要为了让测试变绿去改期望值——但也别把「测试锚定了一个已经变化的日子」当成产品缺陷。判据：这条断言跑的是规则，还是某一天的运气？

---

## 8. 多 agent 协作时的约定

| 约定 | 内容 |
|---|---|
| 边界 | 一个 agent 只做一件事：采集 / 物化 / 判读 / 呈现 / 问答。不要跨 |
| **分支** | **main 只接受 PR 合并，不许直推。** 远端没有强制（免费账号限制，见 `docs/workflow.md` §2）——就是说 GitHub **不会**拦你，所以更不能推。一个功能一个分支一个 worktree：`python3 tools/ci/wt.py new feat/xxx` |
| **隔离** | 两个 agent 并行时，各用各的 worktree。**data/ 是共享的唯一一份**（符号链接），`site/index.json` 各存各的（复制）——详见 `docs/workflow.md` §3 |
| 通信 | 只通过上表里的文件。不要直接调另一个 agent 的内部函数 |
| 幂等 | 任何步骤重复跑必须产出相同结果（`slot.py`、`collect.py`、`build` 都已保证） |
| 冲突 | `rules.json` 改动前先跑 `backtest.py snapshot`，改完跑 `diff`，把改判清单附在提交里 |
| 独占 | `slot.py mark`、`push_data.py`、发布动作是**独占操作**，同一时刻只允许一个 agent 执行。**worktree 隔离不了它们** |
| **部署** | **改了 `site/` 下任何文件，收尾必须跑 `python3 tools/deploy.py`。** 它输出 `republish` 就必须重新发布应用，然后 `--mark-app-deployed` 登记；输出 `hot_update` 会自动推送数据；`noop` 才代表线上已同步。**git push ≠ 上线**——两者的差集不会自己显形，只能靠这条命令显式核对 |
| **历史** | **不写变更记录。** 文件只写当前真相，谁在哪天改了什么由 `git log` 承载。commit message 写 why，不写「update」。删东西的 commit 要说明「删了什么、为什么可以删、结论在哪」。唯一例外是 `docs/decisions.md` 的否决项与 `docs/incidents.md` 的防线——那是 git 查不到的信息。判据见 `docs/README.md` §2 |
| 记账 | 会话草稿（未定的事、待办、个人偏好）写 `.workbuddy/memory/[日期].md`，**不写已完成的工程历史**；后者属于 commit |

---

## 9. 当前已知的宿主耦合

诚实标注，避免你在错误的假设上工作：

| 事实 | 影响 | 状态 |
|---|---|---|
| 定时班次的 prompt 在 WorkBuddy 自动化配置里，不在仓库 | 换调度器需人肉重写；prompt 里的路径若写死旧目录会失效 | 待落盘到 `skills/*/automations/` |
| 判读规则与架构约束、事故复盘混在同一份 SKILL.md | 判读 agent 要吞整份 | 待拆分为 prompt / docs / incidents |
| 发布参数（appId、启动命令）在 `tools/deploy.json`，不入库 | 换机器要重新配 | 设计如此：它含凭据 |

目标形态见 `docs/architecture.md` §4.4，待定项见 `docs/decisions.md` 第二部分。
