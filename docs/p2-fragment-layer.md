# P2 片段层（小时/半区）设计

> 本文是 `docs/roadmap.md` P2 行的落地设计。目标不是「加一张表」，而是**让 L2 拥有比日更细的观测单元，同时不改变任何已有日层结论**。验收判据只有一条硬门：**golden 零 diff**。

## 一、目标与判据

| 项 | 内容 |
|---|---|
| 目标 | L2 在现有 day / week / month / rollN 之外，增加 **hour** 与 **daypart（半区）** 两个尺度；日层判读改为「可切的指标从片段聚合，不可切的仍由日汇总裁决」 |
| 硬判据 | `backtest.py golden` **11/11 不变**；`index.py verify` 全过 |
| 软判据 | 一致性报告里**每个指标都有结论**（恒等 / 不恒等 + 原因），不允许「没测过」 |
| 不做的前提 | 片段层不得引入任何新的档位/红黄绿——判定权只在日及以上（`docs/vision.md` §三、`architecture.md` §八） |

## 二、数据现实（2026-11-01 实测，决定一切）

`data/raw/<date>.json` 里真正有**子日粒度**的序列只有这些：

| 序列 | 粒度 | 单日点数 | 可用于片段 |
|---|---|---|---|
| `dailyhr.heartRateValues` | 2 分钟 | 654–720 | 每小时心率（均/最低/最高） |
| `stress_daily.stressValuesArray` | 3 分钟 | 480 | 每小时压力均值 |
| `stress_daily.bodyBatteryValuesArray` | 3 分钟 | 480 | 每小时身体电量 |
| `summary_chart.values` | 15 分钟 | 96 | 每小时步数、**活动等级**（sleeping/sedentary/active/highlyActive） |
| `sleep_daily.sleepMovement` | 1 分钟 epoch | ~588 | 睡眠片段（运动量） |
| `sleep_daily.wellnessEpochSPO2DataDTOList` | epoch | ~383 | 睡眠期血氧分布 |
| `sleep_daily.sleepHeartRate` / `sleepStress` / `sleepBodyBattery` | ~1 分钟 | 157–235 | 夜间 HR / 压力 / 电量 |
| `sleep_daily.wellnessEpochRespirationDataDTOList` / `hrvData` | epoch | 94–235 | 夜间呼吸率 / HRV 读数 |

**只有日汇总、没有子日粒度的指标**：`sedentary_min`（有 15min 活动等级可近似，但**不等于**日值）、`intensity_min`、`active_kcal`、`distance_km`、`floors`、`sleep_h`、`rhr`、`hrv_ms`、`spo2_avg`、`stress_avg` 的日值。这些指标的片段层**只能是「日值挂在 00:00」这一种退化形态**，不得摊派到小时。

### 关键实测（决定 2c 能不能切）

| 指标 | 15min 桶回推 vs 日值 | 结论 |
|---|---|---|
| `steps_daily` | **逐日精确相等**（4/4 天） | **可切**：日层可由片段求和得出 |
| `sedentary_min` | 桶 × 15 分钟 **小于**日值（如某日 660 vs 841） | **不可切**：片段只作相对分布，日值仍由 `usersummary` 裁决 |
| `floors` | 桶内 `pushes` 求和恒为 0（日值非零） | **不可用**：该桶字段不可信，不建片段 |
| `active_kcal` | 桶里没有这个字段 | 不建片段 |

> 这张表就是「白名单」的来源：**能切的是被实测证明恒等的指标，不是主观上「应该能切」的指标。**

## 三、三条不变式

1. **日值仍是日汇总裁决。** 片段回推若与日值不等，**不改日值**，只在一致性报告里登记「不恒等 + 差值口径」。这样 golden 天然零 diff。
2. **判定不下放。** hour / daypart 尺度的 units **只出值、不出 judge / delta_verdict**；`rules.json` 的 `confidence` 门槛与红黄绿只在 day 及以上生效。
3. **不为缺失造假。** 没有原生粒度的指标不建片段行；有片段但覆盖不足的小时，`coverage` 如实给（该小时实测点数 / 应有 120 点），不许用 0 填。

## 四、模型：不建新表，扩 obs

`obs` 表已有 `t` / `t_end` / `kind(point|interval)` / `coverage` / `method`——这正是片段需要的形状。**不新增 `fragments` 表**（第二套结构必然与 obs 漂）。

```
obs 行（片段）：
  metric = steps_daily            # 沿用指标注册表，不发明新指标名
  t      = 2026-10-31T14:00:00    # 小时起点（本地时）
  t_end  = 2026-10-31T15:00:00
  value  = 1320                   # 该小时聚合值（sum）
  kind   = interval
  coverage = 1.0                  # 该小时实测占比（2min HR → 30/30；15min steps → 4/4）
  method = garmin_hour            # 与日级 garmin_usersummary 区分
  source = garmin
```

半区（daypart）由小时确定性聚合而来，**定义固定**：

| daypart | 时间窗 | 用途 |
|---|---|---|
| night | 00:00–06:00 | 睡眠/夜间恢复 |
| morning | 06:00–12:00 | 上午行为 |
| afternoon | 12:00–18:00 | 下午久坐块（vision 里的例子） |
| evening | 18:00–24:00 | 晚间活动/加班 |

聚合规则按 `references/metrics.json` 现有 `agg` 字段走（sum 类：steps；mean 类：hr/stress/bb）。

**跨零点归属**：睡眠相关片段归属**起床日**（与现有 `sleep_h` 日层口径一致），其余按自然日。

## 五、分步实施（每步独立可验收）

### 2a 片段落盘 + 补齐主观 obs（低风险，不动任何判定）

- `index.py` 新增 `_hour_obs()`：把上表六类序列切成小时/半区行，写 obs（`method=garmin_hour`）。
- **同时修一个既有缺陷**：`index.py:40` 的 `DAILY_LOG` 仍指向迁移前路径，导致**主观数据从未进 L1**（`obs` 里 `source=daily_log` 的行数为 0，已实测确认）。改为从 `settings.DAILY_LOG` 取，并改为读 `events.jsonl` 里的 `measurement/diet/context` 事件（P1 已把主观数据沉淀为事件，`daily_log` 是视图）。
- 新增 `tools/fragment_consistency.py`：逐指标比对「片段回推 vs 日值」，输出恒等/不恒等结论表；CI 加检查项 `fragments:consistency`（本机跑，CI 无数据 SKIP）。
- 验收：片段行数符合预期（小时数 × 指标数）；一致性报告每条都有结论；golden 不变。

### 2b hour / daypart 进入 units（只出值）

- `units.py` 增加两个 scale；只为「片段的 coverage 达该单元应有覆盖」的单元出行。
- 字段与现有 units 一致，但 `judge` / `delta_verdict` 恒为空（写死，不许上游填）——判定不下放这条要在代码里，不是靠自觉。
- 验收：golden 零 diff（日层未动）；随机抽 3 个单元，与手算一致。

### 2c 日层按白名单改为由片段聚合（逐指标）

- 白名单起步只有 `steps_daily`（实测恒等）。每加一个指标，前置条件是**影子跑**：同一天用「片段求和」与「现状日值」各算一遍，diff 必须为空，否则不进白名单。
- 切换后 `obs` 的日级 `steps_daily` 行改为 `derived_from` 指向片段（保留原 `usersummary` 行作为对照，标记 superseded 语义由 method 区分，不改历史行）。
- 验收：golden 零 diff + 影子 diff 为空 + 一致性报告里该指标从「不可比」变「恒等」。

## 六、明确不做

- **不做秒级/分钟级落盘**。2 分钟 HR 已是设备底，再细只增加体积不增加信息。
- **不给 hour / daypart 任何判定权**，包括「这小时算不算久坐」这类看似无害的判定。
- **不新建 fragments 表**（复用 obs）。
- **不为无原生粒度的指标伪造片段**（sedentary / intensity / kcal / floors 都不摊派）。
- **不在 P2 做任意窗口查询**（那是 P4 的查询接口；P2 只保证数据形状支持它）。

## 七、风险与对策

| 风险 | 对策 |
|---|---|
| 15min 桶的时区边界（`startGMT` 与本地日切） | 一律经 `adapters/gtime.py` 换算，`index.py` 保持零 epoch 运算的现状 |
| 某日 HR 点数不足（654 而非 720） | `coverage` 如实写，不做插值；一致性报告按覆盖率分档比较 |
| 片段行数膨胀（24 行/指标/天 × 约 6 指标 × 60 天 ≈ 8.6k 行） | 可接受（db 现有 ~1.9MB）；超过 10 万行再考虑按月裁剪 |
| 2c 切换后新旧日值有微小差 | 影子跑阶段就拦住，不进白名单即不切 |

## 八、开搞前的第一步

`2a` 的第一件事是 `index.py::_hour_obs()` + `tools/fragment_consistency.py`，然后是主观 obs 的旧路径修复——后者是**已经确认的缺陷**（不是新功能），改动小但会改变 `obs` 内容，改完必须立刻跑 `golden` 与 `index.py verify`。
