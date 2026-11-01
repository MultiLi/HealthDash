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
| `conditions` | 否 | 条件数组，见 §四。省略 = 不筛日子 |
| `logic` | 否 | `and`（默认）/ `or` |

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

## 四、条件（看一类日子，而不是一段时间）

```json
{"window": {"start": "2026-09-02", "end": "2026-11-01"},
 "metrics": ["rhr_bpm"],
 "conditions": [{"kind": "metric", "metric": "sleep_h", "op": "<", "value": 6.5}]}
```

三类条件，字段严格校验（未知键直接报错）：

| kind | 写法 | 语义 |
|---|---|---|
| `metric` | `{"metric":"sleep_h","op":"<","value":6.5}` 或 `{"metric":"hrv_ms","range":[50,60]}` | 当天该指标满足比较 |
| `event` | `{"type":"note","contains":"熬夜"}` / `{"metric":"alcohol_cups","op":">","value":0}` / 带 `"shift":1` | 当天（或**前 N 天**）有匹配事件；`shift:1` 就是「次日」 |
| `collect` | `{"level_in":["complete"]}` 或 `{"exclude_levels":["missing_raw"]}` | 当天采集闸门级别 |

任何条件都可用 `"negate": true` 取反（「非熬夜日」＝同一条加 negate，不必另写规则）。

### 三个必须钉住的语义

**① 分母 = 命中天数**，不是窗口天数。筛「睡眠<6.5h 的日子」时问的是这些天平均如何，所以 `coverage = 有该指标的天数 ÷ 命中天数`。

**② 不可判定 ≠ 未命中。** 那天没测睡眠，既不能算「熬夜日」也不能算「非熬夜日」。把缺失当 False 会让筛选悄悄偏向有数据的日子，而这个偏差读起来像结论。所以结果里单列 `undecidable`，并保证 **命中 + 排除 + 不可判定 = 候选天数**（`tools/query_checks.py` 的三桶闭合检查守着）。

**③ 最小样本门。** 条件筛选天然把样本变小，而小样本的均值差看起来总是很显著——3 个熬夜日的 RHR 略高不是发现。`rules.query.min_n_selected`（现 5）以下的实测天数一律 `judge=na` / `delta_verdict=insufficient`，并写明原因。**这个门槛只从口径文件读，不接受调用方传参**——否则就成了「选一个能让结论过线的门槛」。

对比窗同样施加相同条件（整段前移一窗长），这样「这一段熬夜日 vs 上一段熬夜日」才是同质比较。想要「熬夜日 vs 非熬夜日」，用 `negate` 查两次。

## 五、保存视图与深链

查询定义可以存下来，构建时求值成静态 JSON，页面按 `?view=<id>` 渲染。

```bash
python3 $S/query.py --save late-bed --title "晚睡日的静息心率" \
  --q '{"window":{"days":60},"metrics":["rhr_bpm"],"conditions":[{"kind":"metric","metric":"sleep_bed_min","op":">=","value":120}]}'
python3 $S/query.py --list-views        # 看已存的
python3 $S/query.py --rm-view late-bed  # 删
python3 tools/build_homepage_data.py    # 求值 → site/views/<id>.json
# 深链：<域名>/?view=late-bed
```

| 环节 | 谁做 | 说明 |
|---|---|---|
| 定义 | `data/views.json` | 只存**查询定义**，不存结果（定义入库/入档，结果每次重算） |
| 求值 | `tools/homepage/views.py` | 构建时调 `query.run()`；一个视图坏了写 `{"error":...}`，不拖垮整次构建 |
| 传输 | `site/views/<id>.json` | 与月度分片同性质的数据，走 `push_data.py` 热更新（每晚随报告刷新） |
| 渲染 | `site/js/view.js` | **一个字都不算**：值/达标/变化/覆盖率全部来自 JSON |
| 取数 | `api/data/views/<id>` 优先，`views/<id>.json` 兜底 | 与首页同一套「端点优先、静态兜底」纪律 |

**为什么不在浏览器里算**：判定必须在后端（§一）。代价是「深链只对已保存的视图生效」——临时问题由 agent 直接答，不生成 URL。这不是妥协，是顺序：先有后端算好的答案，才有可分享的链接。

**视图失败要说出来**：页面显示「这个视图暂时算不出来：<错误>」，不画空表。空表与查询失败在页面上长得一样，那是最坏的一种沉默。

## 六、等价性门（P4 的硬门）

`tools/query_equivalence.py`，CI 项 `query:equivalence`：

- **bulk**：库里每一个单元（day/week/month/roll7/28/90 × 全部指标，现 4367 格）用当前代码重算，与 `units` 表逐字段比 `value / n / coverage / judge / judge_rate / delta_v / delta_verdict / delta_vs / start / end / expected`。一处不同即失败。
- **spot**：另挑 8 个单元走 `query.run()` 公开入口，确认壳层（`scale/key` → spec 解析、上一窗查找）没把值搞错。

这道门同时抓两件事：**第二套口径**（有人给查询另写一遍数学）与**物化过期**（改了 `compute_cell` 忘记 `index.py build`）。两者都不会让 golden 变红——金标只钉 11 个历史日子。

P4a 落地前的重构本身也过了同一把尺子：抽 `compute_cell` / `apply_delta` 前后，`units` 表 **13139 行逐字段零差异**。

## 七、代码地图

| 文件 | 职责 |
|---|---|
| `units.py::context` | 一次性装载求值输入（day_vals / cov / bands / 门槛），重建与查询共用 |
| `units.py::compute_cell(s)` | **唯一的「窗口 × 指标 → 单元格」实现**（聚合 + 覆盖 + 达标 + 逐日阈值） |
| `units.py::apply_delta` | **唯一的环比判定**（五档 verdict）；窗口层通过 `key_of_prev` 指定上一窗 |
| `units.py::window_spec` | 任意窗口 → 与 `unit_specs` 同形状的 spec（分母截断、借尺子） |
| `units.py::ruler_for` | 窗口长度 → 借用哪把尺子 |
| `query.py` | 查询定义的校验、解析、求值与展示；`run()` 是库入口，CLI 是壳 |
| `query.py::select_days` | 条件 → 命中日集合（三态：命中/未命中/不可判定） |
| `query.py::apply_min_n` | 条件筛选后的最小样本门（判定权在这里，不在调用方） |
| `tools/query_equivalence.py` | 等价性门（CI 项 `query:equivalence`） |
| `tools/query_checks.py` | 条件不变量检查（CI 项 `query:conditions`） |

命令：

```bash
python3 skills/body-health/scripts/query.py --days 14 --metrics hrv_ms,rhr_bpm
python3 skills/body-health/scripts/query.py --start 2026-10-01 --end 2026-10-20 --json
python3 skills/body-health/scripts/query.py --scale week --key 2026-W44
python3 skills/body-health/scripts/query.py --list-metrics
```

## 八、报告与 agent 怎么用它

**报告（02:00）**：`fact_pack.py` 新增 `queries` 块，由 `query.run()` 产出两条预置查询——
「最近 14 天」与「晚睡日 vs 非晚睡日的 RHR/HRV」。它们锚定**目标日**（回补历史日时窗口跟着走），
不是「全库最新」。报告因此能引用「一类日子」的对照结论，而不是只有日/周/月的固定刻度。

`scales` 块仍读 `units` 表：那不是第二条路径——`units` 与 `query` 用的是同一段数学
（§六 的等价性门逐格证过），读表只是省一次重算。要改口径，只改 `units.compute_cell`。

**agent（对话）**：`query.py` 就是编译目标，不需要另一套接口。

| 步骤 | 命令 | 作用 |
|---|---|---|
| 复述意图 | `query.py --explain ...` | 先打印「我理解你要问的是…」，说错了当场改 |
| 取答案 | `query.py --json ...` | 值 + 判定 + 覆盖 + 变化 + 真值版本 + **断言溯源** |
| 给链接 | `query.py --save <id>` → 重建 | 只对已保存的视图生成深链（判定必须在后端算，§五） |

`--json` 结果里的 `trace` 是**断言溯源**：窗口内与被查指标相关的断言 id、日期与档位。
对话里的每个数字都能指回「哪条断言支持它」——这是 `verify_judgment` 的数值溯源在对话侧的延伸。
trace 只给引用，不产生新结论。

## 九、明确不做

- 不做「原始序列导出让你自己算」——判定权不下放，这一条在 P4 不松口
- 不做报表编辑器 / 看板自由布局（视图是查询定义，不是画布）
- 不让调用方指定覆盖门槛或最小样本（那是「选到能过线的门槛」）
- 片段指标（`hour_*`）不进任意窗口口径：它们没有跨尺度的合格门槛，且日值仍由日汇总裁决（见 `fragment-layer.md`）
