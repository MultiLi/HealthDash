# 片段层（小时 / 半区）

> L2 在日 / 周 / 月 / 滚动窗之外的两个尺度。**目标不是「多几张表」，而是让观测单元细到小时，同时一个日层结论都不改**。硬门就一条：`backtest.py golden` 11/11 不变。

## 一、它是什么

| 项 | 内容 |
|---|---|
| 尺度 | `hour`（`2026-10-31T14`）、`daypart`（`2026-10-31-afternoon`） |
| 指标 | `hour_` 前缀的 11 个：心率均/最低/最高、压力、身体电量、步数、久坐分钟、深/浅/REM/清醒分钟 |
| 存储 | 复用 `obs`（`method=garmin_hour`），**不新建 fragments 表** |
| 判定权 | **没有**。`judge` / `delta_verdict` 恒为空，`index.py verify` 有断言守着 |
| 日值关系 | 日值仍由日汇总裁决；片段只在实测恒等时标 `derived_from`，绝不改值 |

半区固定四段（`references/fragments.json`）：`night` 00–06、`morning` 06–12、`afternoon` 12–18、`evening` 18–24。由小时确定性聚合，不单独落盘。

## 二、数据现实（决定一切）

`data/raw/<date>.json` 里真正有子日粒度的序列只有这些，其余指标只有日汇总：

| 序列 | 原生粒度 | 单日点数 | 片段用途 |
|---|---|---|---|
| `dailyhr.heartRateValues` | 2 分钟 | 654–720 | 每小时心率均/最低/最高 |
| `stress_daily.stressValuesArray` | 3 分钟 | 480 | 每小时压力均值 |
| `stress_daily.bodyBatteryValuesArray` | 3 分钟 | 480 | 每小时身体电量（取第 3 位数值，-1 为无读数） |
| `summary_chart.values` | 15 分钟 | 96 | 每小时步数、久坐分钟（`primaryActivityLevel`） |
| `sleep_daily.sleepLevels` | 分段（10–15 分钟） | ~23 段 | 每小时深/浅/REM/清醒分钟 |

**只有日汇总、没有子日粒度的**：`distance_km`、`active_kcal`、`intensity_min`、`floors`、`rhr`、`hrv_ms`、`spo2_avg`、`stress_avg` 的日值。这些**不建片段**——片段层不替源编造分辨率。

## 三、实测结论（决定白名单，不是「应该能切」）

`tools/fragment_consistency.py` 每个指标都有一句结论（40 天样本）：

| 指标 | 结论 | 实测 |
|---|---|---|
| `hour_steps` → `steps_daily` | **条件恒等** | 96 桶齐全的 37/40 天精确相等；3 天例外全部是源两侧不一致（10-07 差 4088 步：43 个桶 `activityLevel=none` 但 usersummary 仍记步），已逐日登记 `known_exceptions` |
| 深+浅+REM 分钟 → `sleep_h` | **恒等** | 38/38 天，最大差 0.0037h；逐级对得上（深/浅/REM 分钟分别等于 `deep_s`/`light_s`/`rem_s`） |
| `hour_sedentary_min` → `sedentary_min` | 不恒等 | 桶×15 远小于日值（10-03：90 vs 936）——`sedentarySeconds` 把 `activityLevel=none` 的时段也计入。只作相对分布 |
| `hour_stress` → `stress_avg` | 不恒等 | 小时均值的均值 ≠ 日 avg（分母不是小时数，且跳过无读数时段），最大差 6.6 分 |
| 心率三项 / 电量 | 无日级对照 | 日级没有「全天心率均值」；`bb_low` 是点值不可比。理由写在 `no_counterpart` |

**方法上的教训**：早期用 4 天样本得出「steps 逐日精确相等」，扩到 40 天后发现 3 天不等。白名单只能由全样本实测支撑，不能由小样本的巧合支撑。

## 四、三条不变式（代码里兑现，不靠自觉）

1. **日值仍是日汇总裁决。** 片段回推不等 → 不改日值，只登记差值。所以 golden 天然零 diff。
2. **判定不下放。** `hour` / `daypart` 单元 `judge` 与 `delta_verdict` 恒空。理由不是洁癖：小时级判定立刻会被当成「这小时状态不好」，那是把噪声包装成结论。
3. **不为缺失造假。** 没有原生粒度的指标不建片段；覆盖不足的小时如实写 `coverage`（实测点数 × 采样间隔 ÷ 60），不插值、不补零。

## 五、代码地图

| 文件 | 职责 |
|---|---|
| `adapters/garmin_cn.py::_subday` | 六类源序列归一成 `subday` 块（本地分钟序号 / 绝对本地时间），**只做形态归一，不聚合** |
| `adapters/gtime.py` | 绝对时间换算唯一出口（`iso_gmt_to_local_iso/min`）——只有 HH:MM 无法判断跨零点的睡眠段落在哪一天 |
| `index.py::_hour_obs` | 分钟 → 小时的归组与覆盖率（ETL 唯一入口） |
| `index.py::_annotate_fragments` | 2c：白名单恒等时标 `derived_from`，不改值 |
| `units.py::_frag_units` | hour / daypart 两个尺度，只出值 |
| `references/fragments.json` | 半区窗口、片段↔日级对照、白名单与已登记例外（改它等于改口径） |
| `references/metrics.json` | 11 个 `hour_*` 指标定义（`scales: [hour, daypart]`，无 `dir` → 结构上没有判定） |
| `tools/fragment_consistency.py` | 一致性报告；CI 项 `fragments:consistency` |

命令：

```bash
python3 skills/body-health/scripts/index.py build       # 片段随全量构建落盘
python3 tools/fragment_consistency.py                   # 逐指标给结论（白名单未登记异常 → 非零退出）
python3 skills/body-health/scripts/units.py get --scale daypart --key 2026-10-31-afternoon
python3 skills/body-health/scripts/units.py get --scale hour --key 2026-10-31T14
```

## 六、指标名前缀为什么是 `hour_`

`units` 与 `assertions` 读日值用的是 `SELECT metric, substr(t,1,10) ... ORDER BY t` + 后写覆盖先写。若片段沿用 `steps_daily` 这样的日级名，同一天会有 24 行，**23 点那一行会静默盖掉日值**——一次构建就把所有日层结论改掉，且不报错。

前缀让「未加过滤的日读取」在任何情况下都取不到片段（结构性防御）。两处读取点（`units._read_obs`、`assertions._day_values`）另外显式排除了 `hour_%`，这是第二层保险，不是唯一防线。

## 七、明确不做

- 不做秒级/分钟级落盘：2 分钟心率已是设备底，再细只增加体积不增加信息
- 不给片段任何判定权（含「这小时算不算久坐」这类看似无害的判定）
- 不新建 `fragments` 表（复用 `obs`）
- 不为无原生粒度的指标伪造片段（sedentary / intensity / kcal / floors 都不摊派）
- 不做任意窗口查询（那是 P4 的查询接口；本层只保证数据形状支持它）

## 八、规模与风险

| 项 | 现状 |
|---|---|
| 片段行数 | 7,406 条 obs（43 天 × 约 172 条/天），`health.db` 5.2MB |
| 上限判据 | 超过 10 万行再考虑按月裁剪；现在远未到 |
| 15 分钟桶的时区边界 | 一律经 `adapters/gtime.py` 换算，`index.py` 保持零 epoch 运算 |
| 某日点数不足 | `coverage` 如实写，不插值；一致性报告按覆盖率分档比较 |
| 源自身不一致 | 登记进 `known_exceptions`（含日期与原因）；未登记的新异常让 CI 红——这是发现「源换了切法」的唯一机制 |
