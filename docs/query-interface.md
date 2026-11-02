# 查询层（任意窗口）

> L2 的第二个消费端。目标：**任意窗口、任意粒度的提问**，而判定与口径仍只有一份。覆盖「窗口 + 指标 + 环比」「条件组合」「保存视图」「定性结论」四个自由度。

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
| `conclusions.py` | 窗口结论：注册表、`rate`/`run` 算子、`integral`/`trend` 附加量、措辞 |
| `references/conclusions.json` | 每条断言的 `kind` / `operator` / `inputs` / 措辞（窗口语义的唯一来源） |
| `tools/query_equivalence.py` | 等价性门（CI 项 `query:equivalence`） |
| `tools/query_checks.py` | 条件不变量检查（CI 项 `query:conditions`） |
| `tools/window_conclusions_check.py` | 窗口结论不变量（CI 项 `conclusions:window`） |

命令：

```bash
python3 skills/body-health/scripts/query.py --days 14 --metrics hrv_ms,rhr_bpm
python3 skills/body-health/scripts/query.py --start 2026-10-01 --end 2026-10-20 --json
python3 skills/body-health/scripts/query.py --scale week --key 2026-W44
python3 skills/body-health/scripts/query.py --list-metrics
python3 skills/body-health/scripts/query.py --days 30 --conclusions sleep.duration.short,activity.sedentary.over
python3 skills/body-health/scripts/query.py --list-conclusions
python3 skills/body-health/scripts/conclusions.py --days 30          # 只看结论，不看数值
python3 skills/body-health/scripts/conclusions.py --list            # 注册表全表
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

## 九、定性结论的窗口化

数值可以任意窗口聚合，判断原先只能按天。这一节把「这一段的形状是什么」补上：
`assertions` 的日级布尔 → `conclusions.py` 的窗口陈述。**不重算任何日判定**，只数。

```json
{"window": {"days": 30},
 "metrics": ["hrv_ms"],
 "conclusions": ["sleep.duration.short", "activity.sedentary.over"]}
```

### 三种断言，三种命运

| kind | 里子 | 窗口化的形态 |
|---|---|---|
| `judge` | 当天阈值越线 | `rate`（命中÷可判定）或 `run`（段结构），加措辞 |
| `cross` | 两个 L1 条件同时成立 | 只做**共现计数**，`--conclusions` 直接拒绝 |
| `status` / `passthrough` / `point` | 采集状态 / 设备透传 / 点测量 | 不窗口化（分别是 coverage、无独立含义、取最新） |

登记在 `references/conclusions.json`：`kind`、`operator`、`inputs`（分母来源）、`words`（措辞）。
**未登记的 id 会让 `assertions.py rebuild` 直接报错**——新增一条断言却没定义它跨日怎么读，是契约破坏。

### 四条语义（决定了答案可不可信）

**① 分母 = 可判定天数，不是行数，也不是窗口天数。** 可判定 = 该断言的 `inputs` 当天都有值。
行数当分母会把「没测到」算成「没发生」；窗口天数当分母会把「没数据」算成「没触发」。
`tools/window_conclusions_check.py` 的 `denominator` 项逐日核对「有行 ⟺ 可判定」。

**② 门槛一个不新造。** 发生率分界读 `rules.units.daily_ok_rate`（0.6），样本门读
`rules.query.min_n_selected`（5），趋势门槛读 `confidence.<metric>.mdc_change`。
唯一的换算是**趋势门槛按 √n 收缩**：`mdc_change` 是两次单次读数之间的门槛，
n 天均值之间要除以 √n。不收缩的话，六周 6.7ms 的真实下行会被 20ms 判定成「在误差内」。

**③ 措辞按状态取，状态按数字定。** 成立 / 参半 / 不成立 / 不可判定。参半是独立的：
发生率 55% 既不是「常态」也不是「偶发」，说任一都是编。不可判定专指样本不足，
**不指「说不准」**——那正是参半。

**④ 精度是声明，不是门禁。** 结果带 Wilson 区间，但判定用点估计。
拿「CI 下界必须高过 0.6」当门禁，实测会让 7 天窗口 5 条结论里 4 条变成「不确定」，
功能等于没有。区间用来防止把 77% 说成「全部」。

### 算子

| 算子 | 输出 | 例 |
|---|---|---|
| `rate` | 命中/可判定 + CI + 段数 + 最长段 | 久坐 22/23、2 段、最长 20 天 |
| `run` | 段数与最长段，门槛由 `min_run_ref` 指向 rules | 连续欠觉最长 5 天（门槛 2） |
| `integral`（附加） | Σ 越过逐日判读线的部分 | 睡眠欠账 10.6 h |
| `trend`（附加） | 斜率 + 总变化 + `mdc/√n` 门槛 | HRV −0.37/天，累计 −7.7，可判 |

`integral` / `trend` 不单独成 id：它们是同一条结论的另一个时间面，挂在结论上。

### 与条件正交

`conditions` 筛完日子后，结论只在命中的日子上求值（分母随之变小，样本门自动更严）。
「熬夜日之后的恢复结论」因此不需要新接口。

**但条件不许碰到结论自己的输入。** 筛「睡眠 < 6.5h 的日子」再算「睡眠不足发生率」，
结果必然是 100%——数字没错，它什么都没说。`cond_metrics` 与结论 `inputs` 有交集时，
该结论强制 `state=不可判定` 且不给措辞，理由写进 `reason`。趋势的 `basis` 同时标成
`selected_days`（斜率按命中日计，不是日历日）。

### 消费端

- **报告**：`facts.conclusions`（近 14 天，锚定目标日），跨日措辞只能引自这里。
- **主页**：`index.json.conclusions`（周/月按单元全量、滚动窗只算最新），渲染成总览末尾的
  「这一段的形状」。位置与三条纪律见 `docs/frontend.md` 同名章节。

### 门禁

`tools/window_conclusions_check.py`（CI 项 `conclusions:window`）验五条恒真命题：

| 项 | 抓什么 |
|---|---|
| `registry` | 库里的 id 没登记 / 登记了没中文名 / judge 缺 operator 或 inputs |
| `denominator` | 只在触发时落库（分母偏小）或缺失被当成未触发（分母偏大） |
| `degenerate` | 窗长 1 时窗口结论 ≠ 当日断言（第二套日判定，或聚合错位） |
| `closure` | 计数不闭合、发生率越界、状态有而措辞缺、有命中却不可判定 |
| `additive` | 窗口命中 ≠ 单日命中之和（日期错位一天） |

`degenerate` 只查 `rate` 类：`run` 的日级 verdict 已含历史（streak），单日窗口重建不出同一语义。

## 十、明确不做

- 不做「原始序列导出让你自己算」——判定权不下放，这一条在 P4 不松口
- 不做报表编辑器 / 看板自由布局（视图是查询定义，不是画布）
- 不让调用方指定覆盖门槛或最小样本（那是「选到能过线的门槛」）
- 不做**窗口级红黄绿总评**：30 天压成一个档位，丢掉的正是「哪几天差」，还比日级总评更像权威结论
- 不做**窗口级归因**（「这 30 天 HRV 低是因为熬夜」）：日级共现是同一时点的事实，窗口级共现是跨时间的巧合
- 不开放任意聚合函数：算子只能是 `rate`/`run`/`integral`/`trend` 四个，与「数学只有一份」同一条纪律
- 片段指标（`hour_*`）不进任意窗口口径：它们没有跨尺度的合格门槛，且日值仍由日汇总裁决（见 `fragment-layer.md`）
