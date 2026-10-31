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
| 想知道踩过哪些坑 | [`docs/incidents.md`](docs/incidents.md) | 每条 = 现象 + 根因 + 现在的防线 |

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
docs/                    项目文档：架构 / 运维 / 前端 / 事故 / 决策 / 文档规范
notes/                   领域知识与欠账（整体不入库，唯一例外 backlog.md）
skills/                  能力包
  body-health/           判读能力 + 引擎 + 口径（rules.json / metrics.json 在这里）
  garmin-connect-cn/     采集通道与鉴权
tools/                   构建、推送、冒烟、交互探针、CI
site/                    前端（原生 ES module，无构建）+ 同源可写服务
data/                    ★ 唯一数据根，整体不入库（结构见 data/README.md）
  raw/ inbody/ db/ reports/ logs/ state/ tests/ archive/ profile.json
.workbuddy/skills/       ← 指向 skills/ 的符号链接（兼容旧路径，勿删）
```

---

## 三条最容易踩的边界

1. **判断一个文件该不该入库，只问一句：里面有我的身体数据吗？** 有 → 它必须在 `data/` 下，不在 `data/` 下的本身就是设计错误。`site/index.json` 是派生数据，已 ignore。
2. **只有改前端代码才需要重新发布**，「换数据」只需一次 POST。发布必须带 `language=python`，漏了就静默退回纯静态。
3. **改 `rules.json` 前先 `backtest.py snapshot`**，改完 `diff`，把改判清单附在提交里。

---

## 当前状态

全链路跑通：采集 → 物化三层 → 断言 → 判读 → 主页。门禁一条命令：`python3 tools/ci/check.py`。

**欠账与待拍事项**见 [`notes/backlog.md`](notes/backlog.md)；**明确不做的事**见 [`docs/architecture.md`](docs/architecture.md) §8；**剩余迁移步骤**见同文件 §6。
