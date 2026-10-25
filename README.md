# HealthDash

个人健康数据中台：Garmin / InBody 采集 → 规范化观测 → 多尺度聚合 → 断言判定 → 日报告与主页。

**架构与设计取舍见 [`notes/architecture_v4.md`](notes/architecture_v4.md)。**
改代码前先读它——尤其是「代码 vs LLM 的边界」和「数据不进库」两条。

## 分层

| 层 | 位置 | 职责 |
|---|---|---|
| L0 raw | `data/raw/*.json`（**不入库**） | 采集快照，不可变，溯源用 |
| L1 观测 | `scripts/index.py` → `obs` 表 | 唯一 ETL 点：源字段只在这里翻译 |
| L2 单元 | `scripts/units.py` | 日/周/月/滚动窗 × 指标；delta 与 mdc 判定的唯一入口 |
| L3 断言 | `scripts/assertions.py` | 命题 + 布尔 + 证据；总评 rollup；可跨日统计 |
| L4 编排 | `scripts/fact_pack.py`、`daily_brief.py`、自动化 prompt | 只算不判 / 判读 |
| 定义 | `references/metrics.json` + `scripts/metricdefs.py` | **指标定义的唯一来源与唯一解析器**：语义、聚合方式、达标方向、达标线映射。阈值数值仍在 rules.json |
| L5 呈现 | `site/`、`tools/homepage/` | 主页与报告 |

真相只有两个：`data/raw/`（采集）与 `references/rules.json`（口径）。
中间三层全是派生层，`data/state/health.db` 可随时删掉重建。

## 常用命令

```bash
S=.workbuddy/skills/body-health/scripts

# 物化三层（首次 / 改了 rules.json 或 metrics.json 之后必跑）
python3 $S/index.py build          # raw → obs → units → assertions
python3 $S/index.py verify         # 七项一致性自检，exit 0 = 通过

# 指标定义（唯一权威来源）：改完 rules.json 或 metrics.json 必跑
python3 $S/metricdefs.py validate   # 逐条解析声明，断链/区间写反都会报出来
python3 $S/metricdefs.py list       # 全部指标的 dir / agg / goal / judge / severe / band

# 查
python3 $S/units.py get --scale week --key 2026-W43
python3 $S/units.py series --metric hrv_ms --scale week --n 12
python3 $S/assertions.py list --date 2026-10-24 --level WARN
python3 $S/assertions.py summary --days 90      # 维度 × 断言的触发频次
python3 $S/assertions.py rollup --date 2026-10-24   # 总评（代码定）
```

采集与出报告的链路（`sync.py` / `collect.py` / `slot.py` / 自动化 prompt）不受影响，见 `.workbuddy/skills/body-health/SKILL.md`。

## 口径入口（改判定只改这两个文件）

- `references/rules.json` — 阈值、个人档案、`units` 门禁、`confidence` 噪声门槛（mdc）
- `references/metrics.json` — 指标注册表：语义、可比条件、聚合方式（`agg`）、达标方向（`dir`）

改完必须重跑 `index.py build` 并看 `verify`；口径变更后应跑新旧断言 diff 再决定是否保留。

## 数据边界

不入库：`data/raw/`、`data/inbody/`、`reports/`、`daily_log.jsonl`、`site/index.json`、`site/daily/`、`data/state/*.db`、`site/.push_token`。

原因与「要改成数据也入库怎么办」写在 `.gitignore` 头部。**任何情况下 `site/.push_token` 与个人健康原始数据都不能进公共仓。**
