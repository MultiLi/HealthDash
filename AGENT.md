# AGENT.md — 接入契约

> **本文件是任何 agent 接入本项目的唯一入口。** 读完这一份就能知道：这是什么、你能调什么、你禁止做什么、怎么证明你做对了。
>
> 它只写**契约**，不写判读规则（在 skill 里）、不写设计理由（在 `docs/` 里）、不写历史（在 `notes/` 里）。三者的分工见 `docs/README.md`。

---

## 1. 这是什么

个人健康数据中台：Garmin / InBody 采集 → 规范化观测 → 多尺度聚合 → 断言判定 → 日报告与健康主页。

**关键事实**：判定（数字、门槛、红黄绿）由代码算，不由你算。你的职责是**编排**与**叙述**，不是**计算**。

---

## 2. 环境

| 项 | 值 |
|---|---|
| 工作区根 | `/Users/tian.li/Documents/Health`（下文记作 `$W`） |
| 核心代码 | `$W/.workbuddy/skills/body-health/scripts`（记作 `$S`）· 目标位置为 `$W/skills/body-health/`，搬迁见 `docs/decisions.md` |
| 采集通道 | `$W/.workbuddy/skills/garmin-connect-cn-data__skillhub/scripts` |
| 运行时服务 | `$W/site/server.py`（标准库，单端口） |
| Python | `python3`（3.13+） |
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
| 事实包 | `python3 $S/fact_pack.py --date D --days 4` | `reports/facts_D.json` | 覆盖该文件 |
| 报告骨架 | `python3 $S/compose.py --date D` | `reports/body_brief_D.skeleton.md` | **只写 skeleton** |
| 判读自检 | `python3 $S/verify_judgment.py --date D` | 五项校验结果 | 无 |
| 记完成 | `python3 $S/slot.py mark done --data-cutoff HH:MM` | `data/state/slots.json` | **不可逆**，会阻止重出 |
| 主页构建 | `python3 tools/build_homepage_data.py` | `site/index.json` + `site/daily/` | 覆盖产物 |
| 主页推送 | `python3 tools/push_data.py` | 线上 `.data/` | 改线上数据 |
| 推送核验 | `python3 tools/push_data.py --check` | exit 0 = 线上本地逐文件一致 | 无 |
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
| 3 | **不许凭报告文本判断数据完整性**。一律读 `data/raw/*.json` 的 `usersummary_daily.wellnessEndTimeLocal` | 关键词搜索必漏。曾有报告自称"覆盖全天"实则截止 23:29 |
| 4 | **不许二次登录 Garmin**。token 在 `~/.config/garmin-cn/garth`，由 `sync.py` 自己管 | 会触发风控，且多套鉴权路径并存必出事故 |
| 5 | **不许覆盖终版报告**。`compose.py --write` 只写 `.skeleton.md`；`--as-final` 才碰正式报告且需 `--force` | 2026-10-25 实际发生过一次覆盖，靠会话转录逐字节恢复 |
| 6 | **不许因「数据还没同步」就重出报告或催数**。先看 `lastSyncTimestampGMT` | 手表没同步 ≠ 拉数失败。提高拉数频率不会让云端凭空多出数据 |

---

## 5. 输入输出契约

整条链路是**文件契约**，不是函数调用。你只需知道每个环节读写哪个文件。

```
data/raw/<YYYY-MM-DD>.json          采集快照（不可变，只在缺失或截断时重拉）
  ↓ index.py build
data/state/health.db                 obs → units → assertions（可删除重建）
  ↓ fact_pack.py --date D
reports/facts_D.json                 ★ 判读唯一合法输入（含 assertions / units / scales / gaps）
  ↓ 你（判读）
reports/judgment_D.json              判读结论（结构校验见 verify_judgment.py）
  ↓ build_homepage_data.py
site/index.json + site/daily/*.json  前端数据层
  ↓ push_data.py
线上 .data/                          热更新，不需要重新发布
```

**你可以读**：`facts_D.json`、`slot.py resolve` 的输出、`docs/incidents.md`、对应的 skill 说明。
**你不该读**：`data/raw/*`（除完整性判断）、`hr_series`、`hrvReadings`——给了你就会忍不住自己算。

---

## 6. 首次接入的阅读顺序

| 顺序 | 文件 | 读它是为了 |
|---|---|---|
| 1 | 本文件 | 契约与禁令 |
| 2 | `skills/body-health/SKILL.md`（当前在 `.workbuddy/skills/`） | 判读怎么说、怎么写、什么时候拒判 |
| 3 | `docs/operations.md` | 具体流程与验证手段 |
| 4 | `docs/incidents.md` | 20+ 条已经踩过的坑（省你重复踩） |
| 5 | `docs/architecture.md` | 只在你要改代码时读 |

**不要一上来读全部 `notes/`**：那里是历史推导，读完只会让你对旧结论产生依赖。

---

## 7. 怎么证明你做对了

跑完一轮，这三条必须同时成立：

```bash
python3 $S/verify_judgment.py --date D   # 总评一致 / findings 覆盖 / 数字溯源 / 禁用词
python3 $S/backtest.py golden            # 12 条历史锚点必须 12/12
python3 $S/index.py verify               # 11 项一致性自检 exit 0
```

改过前端再加：

```bash
node tools/smoke_site.mjs                # 113 项
python3 tools/probe_interactions.py      # 8 用例
```

**任何一项红了，先分清是真错还是已知伪阳性**（见 `docs/operations.md` 的「验证工具的两个已知假警报」）。不要为了让测试变绿去改期望值。

---

## 8. 多 agent 协作时的约定

| 约定 | 内容 |
|---|---|
| 边界 | 一个 agent 只做一件事：采集 / 物化 / 判读 / 呈现 / 问答。不要跨 |
| 通信 | 只通过上表里的文件。不要直接调另一个 agent 的内部函数 |
| 幂等 | 任何步骤重复跑必须产出相同结果（`slot.py`、`collect.py`、`build` 都已保证） |
| 冲突 | `rules.json` 改动前先跑 `backtest.py snapshot`，改完跑 `diff`，把改判清单附在提交里 |
| 独占 | `slot.py mark`、`push_data.py`、发布动作是**独占操作**，同一时刻只允许一个 agent 执行 |
| 记账 | 做实质工作后在 `.workbuddy/memory/[日期].md` 追加一条；那是本地日志，不入库 |

---

## 9. 当前已知的宿主耦合（迁移中）

诚实标注，避免你在错误的假设上工作：

| 事实 | 影响 | 状态 |
|---|---|---|
| 核心代码住在 `.workbuddy/skills/body-health/scripts/` | 13 个脚本各用 `dirname(__file__)/../../../..` 反推根目录，**代码位置即运行时契约** | 待迁移到 `$W/skills/` |
| 5 个定时班次的 prompt 在 WorkBuddy 自动化配置里，不在仓库 | 换调度器需人肉重写 | 待落盘到 `skills/*/automations/` |
| 判读规则与架构约束、事故复盘混在同一份 SKILL.md | 判读 agent 要吞 644 行 | 待拆分为 prompt / docs / incidents |

迁移方案与顺序见 `docs/architecture.md` 第 6 节、`docs/decisions.md` 的「待定」。
