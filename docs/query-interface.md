# 查询层（任意窗口）

> L2 的第二个消费端。目标：**任意窗口、任意粒度的提问**，而判定与口径仍只有一份。现状覆盖「窗口 + 指标 + 环比」；条件组合、保存视图、报告接入见 §六（未落地）。

## 一、两条硬约束

1. **数学只有一份。** `query.py` 不自己聚合、不自己判达标——全部调 `units.py` 的 `compute_cell` / `apply_delta`，与 `units.rebuild()` 走同一段代码。想加口径就改那一处，两个消费端同时生效。
2. **判定永远在后端。** 返回的每行都带 `judge` / `delta_verdict` / `judge_rate`，由代码算好；页面、agent、报告只读结论。查询层不提供「原始序列让你自己算」的模式——那正是 2026-10-25 之前八份平行实现的起点。

## 二、查询定义

```json
{"window": {"start": "2026-10-01", "end": "2026-10-20"},
 "metrics": ["hrv_ms", "rhr_bpm"],
 "delta": true}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `window.start` / `.end` | 二选一 | `YYYY-MM-DD`，闭区间 |
| `window.scale` / `.key` | 二选一 | 已标定的单元：`day`/`week`/`month`/`roll7`/`roll28`/`roll90` + 键 |
| `metrics` | 否 | 省略 = 全部可查询指标（21 个：有 `agg` 的日级指标） |
| `delta` | 否 | 省略 = true（算与上一窗的位移差） |

**未知字段一律报错**：`metric`（少了 s）、`conditons` 这类拼错不会静默变成「按默认口径跑」，而是直接拒绝并列出合法字段。

## 三、窗口语义（决定了答案可不可信）

**分母**：`expected` = 窗口内**落在数据范围内的日历日**数。项目开始前、今天之后的日子不是「漏采」，不计入分母——否则查「过去一年」会永远得到一个很低的覆盖率，然后永远判 `na`。

**借尺子**：覆盖门禁（`coverage_min`）是按尺度标定过的（日 1.0 / 周 0.57 / 月 0.5 / 滚动 0.7）。任意窗口没有标定过，现编一个门槛等于「为了让结论过线而调门槛」。所以只**借用**长度最接近的已标定尺度，并把结果写进 `ruler` 字段：

| 窗口天数 | 借用的尺子 |
|---|---|
| 1 | `day` |
| 2–7 | `week` |
| 8–31 | `month` |
| 32–60 | `roll28` |
| >60 | `roll90` |

「这个判定用的是哪把尺子」必须可回答——与 `metricdefs` 的 `must_report_ruler` 同一条纪律。

**上一窗**：任意窗口的对比对象是**整段前移一个窗长**（非重叠，与滚动窗同一纪律）。重叠窗口的环比会凭空造出显著性。已标定尺度沿用各自的 `prev_key`（日→前一天、周→上一 ISO 周、月→上一月、滚动→位移 k 天）。

**截断日不进聚合**：`coverage < day_coverage_min`（0.95）的那天不参与聚合（会拉偏），但计入分母 → 覆盖率下降 → 该给 `na` 就给 `na`。这条与 `units.rebuild` 完全一致。

## 四、等价性门（P4 的硬门）

`tools/query_equivalence.py`，CI 项 `query:equivalence`：

- **bulk**：库里每一个单元（day/week/month/roll7/28/90 × 全部指标，现 4367 格）用当前代码重算，与 `units` 表逐字段比 `value / n / coverage / judge / judge_rate / delta_v / delta_verdict / delta_vs / start / end / expected`。一处不同即失败。
- **spot**：另挑 8 个单元走 `query.run()` 公开入口，确认壳层（`scale/key` → spec 解析、上一窗查找）没把值搞错。

这道门同时抓两件事：**第二套口径**（有人给查询另写一遍数学）与**物化过期**（改了 `compute_cell` 忘记 `index.py build`）。两者都不会让 golden 变红——金标只钉 11 个历史日子。

P4a 落地前的重构本身也过了同一把尺子：抽 `compute_cell` / `apply_delta` 前后，`units` 表 **13139 行逐字段零差异**。

## 五、代码地图

| 文件 | 职责 |
|---|---|
| `units.py::context` | 一次性装载求值输入（day_vals / cov / bands / 门槛），重建与查询共用 |
| `units.py::compute_cell(s)` | **唯一的「窗口 × 指标 → 单元格」实现**（聚合 + 覆盖 + 达标 + 逐日阈值） |
| `units.py::apply_delta` | **唯一的环比判定**（五档 verdict）；窗口层通过 `key_of_prev` 指定上一窗 |
| `units.py::window_spec` | 任意窗口 → 与 `unit_specs` 同形状的 spec（分母截断、借尺子） |
| `units.py::ruler_for` | 窗口长度 → 借用哪把尺子 |
| `query.py` | 查询定义的校验、解析、求值与展示；`run()` 是库入口，CLI 是壳 |
| `tools/query_equivalence.py` | 等价性门（CI 项 `query:equivalence`） |

命令：

```bash
python3 skills/body-health/scripts/query.py --days 14 --metrics hrv_ms,rhr_bpm
python3 skills/body-health/scripts/query.py --start 2026-10-01 --end 2026-10-20 --json
python3 skills/body-health/scripts/query.py --scale week --key 2026-W44
python3 skills/body-health/scripts/query.py --list-metrics
```

## 六、未落地

| 项 | 内容 |
|---|---|
| 条件组合 | 指标 / 事件 / 采集三类条件，AND/OR 组合。分母改为「符合条件的日子」，需配最小样本门（条件筛选后 n 天然变小，3 个样本的「熬夜次日 RHR 偏高」不是发现） |
| 保存视图 | 查询定义可存、可回放；深链（URL 即视图） |
| 报告接入 | 02:00 报告由查询接口产出，而非独立管线 |
| agent 编译入口 | 自然语言 → 查询定义；回答可溯源到断言 id |

## 七、明确不做

- 不做「原始序列导出让你自己算」——判定权不下放，这一条在 P4 不松口
- 不做报表编辑器 / 看板自由布局（视图是查询定义，不是画布）
- 不让调用方指定覆盖门槛或最小样本（那是「选到能过线的门槛」）
- 片段指标（`hour_*`）不进任意窗口口径：它们没有跨尺度的合格门槛，且日值仍由日汇总裁决（见 `fragment-layer.md`）
