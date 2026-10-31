# HealthDash

个人健康数据中台：Garmin / InBody 采集 → 规范化观测 → 多尺度聚合 → 断言判定 → 日报告与健康主页。

**一句话架构**：判定（数字、门槛、红黄绿）由代码算，叙述由 LLM 写。两者之间是 `assertions` 表。

---

## 先读哪一份

| 你是 | 读这个 | 大约花 |
|---|---|---|
| **Agent**（任意框架，第一次接入） | [`AGENT.md`](AGENT.md) | 5 分钟。契约、能力清单、六条硬边界 |
| **人**，想改代码 | [`docs/architecture.md`](docs/architecture.md) → [`docs/README.md`](docs/README.md) | 15 分钟。分层与文档规范 |
| **人**，想跑一次补数 / 发一次版 | [`docs/operations.md`](docs/operations.md) | 10 分钟。流程、命令、故障排查 |
| 想判读报告怎么写 | `skills/body-health/SKILL.md` | 判读 prompt |
| 想知道踩过哪些坑 | [`docs/incidents.md`](docs/incidents.md) | 23 条实证事故 |

---

## 五分钟跑起来

```bash
cd ~/Documents/Health

# 系统代理常失效，跑联网脚本前统一加这个前缀（否则 Python 报 Connection refused）
export no_proxy='*' NO_PROXY='*'

# 1. 采集 + 回补截断日 + 重建物化层 + 推主页（一条命令四件事）
python3 skills/body-health/scripts/collect.py --days 3

# 2. 自检
python3 skills/body-health/scripts/index.py verify     # 11 项，exit 0 = 通过
python3 skills/body-health/scripts/backtest.py golden  # 12 条历史锚点

# 3. 核验线上数据
python3 tools/push_data.py --check                                # exit 0 = 逐文件一致
```

**出报告**是另一条链路（`slot.py resolve` → 闸门 → `fact_pack` → 判读 → `verify_judgment` → `mark`），见 `docs/operations.md`。不要手动跑——它由定时班次驱动。

---

## 目录

```
AGENT.md                 Agent 接入契约（唯一入口）
README.md                本文件：人的门厅
docs/                    项目文档：架构 / 运维 / 前端 / 事故 / 决策
notes/                   历史推导、领域知识基线、欠账清单
skills/                  能力包（2026-10-25 H3 已从 .workbuddy/skills/ 搬到这里）
  body-health/           判读能力 + 引擎 + 口径（rules.json / metrics.json 在这里）
  garmin-connect-cn/     采集通道与鉴权
healthd/                 ★ 目标位置（可选独立包，见 docs/decisions.md D3）
tools/                   构建、推送、冒烟、交互探针
site/                    前端（原生 ES module，无构建）+ 同源可写服务
tests/                   golden.json 金标用例
data/                    采集原始 + 物化数据库（都不入库）
data/reports/                 报告产物（不入库）
.workbuddy/skills/       ← 两个指向 skills/ 的符号链接（兼容旧路径，勿删）
```

---

## 三条最容易踩的边界

1. **`data/raw/` 与 `data/reports/` 不入库**，`site/index.json` 也不入库。判据：删掉它能不能重建。
2. **只有改前端代码才需要重新发布**，「换数据」只需一次 POST。发布必须带 `language=python`，漏了就静默退回纯静态。
3. **改 `rules.json` 前先 `backtest.py snapshot`**，改完 `diff`，把改判清单附在提交里。

---

## 当前状态（2026-10-25）

**能跑通的**：采集 → 物化三层 → 断言 → 判读 → 主页 → 云端一致性校验，全链路。`index.py verify` 11 项、`backtest golden` 12/12、`smoke_site` 113 项。

**已知的债**（详见 [`docs/decisions.md`](docs/decisions.md) 待定部分与 [`notes/backlog.md`](notes/backlog.md)）：

| 债 | 状态 |
|---|---|
| 13 处脚本用 `dirname(__file__)` 反推根目录，代码位置即运行时契约 | 待 H1 集中化 |
| skills 住在 `.workbuddy/` 私有目录 | 待 H3 搬到 `skills/` |
| 5 个定时班次的 prompt 不在仓库 | 待 H2 落盘 |
| SKILL.md 644 行混装 prompt / 架构 / 事故 / 手册 | 待 H4 拆分 |
| 步数两把尺子（10000 展示 / 8000 判读） | 等拍 |
| rollup 与历史 8 份报告总评不一致 | 等拍 |

迁移顺序见 [`docs/architecture.md`](docs/architecture.md) §6。
