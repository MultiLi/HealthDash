# 欠账清单

> 2026-10-25 15:50 首次盘点 → 16:40 A/B 段与 C3~C8 已清 → **17:15 C1/C2 定案落盘，本表技术债结清。**

---

## A. 建好了但没接线 —— ✅ 全部接上

| # | 项 | 状态 | 落地方式 |
|---|---|---|---|
| A1 | 每晚报告不读断言 | ✅ | `assertions.snapshot()` 是唯一对外形态 → `facts.assertions`；`collect.py` 第 ④ 步自动 `index.py build`（失败不影响采集）；SKILL.md 新增「判读框架与纪律」节，明确总评取 `rollup` 且降档必须并列声明 |
| A2 | 前端不消费 units/metric_defs | ✅ | `index.json` 新增 `units`（六尺度 × 8 指标：值/达标/覆盖/达标率/差/五档判定）；`constants.metrics(ix)` 从 `metric_defs` 派生；`deltaChip` 只翻译 `delta_verdict`；新增 7/28/90 天滚动窗尺度 |
| A3 | 没有无 LLM 路径 | ✅ | `compose.py` 从断言直渲（`--as-final` 顶替）；`verify_judgment.py` 五项自检 |

## B. 计划里写了、没做 —— ✅ 全部做了

| # | 项 | 状态 | 备注 |
|---|---|---|---|
| B1 | `judgment.findings[]` | ✅ 机制就绪 | 骨架版自动生成（含 id/level/evidence/n_hist）；**历史 32 份不回填**（按决策③） |
| B2 | 金标用例 | ✅ | `tests/golden.json` 12 条，全是真实发生过的日子；`backtest.py golden` 12/12 |
| B3 | 回测 | ✅ | `backtest.py snapshot / diff`：改口径前后逐条对比，列出哪几天改判 |
| B4 | 增量索引 | ⬜ 未做 | 全量重建 2s，当前不构成瓶颈。依赖图做了一半的成本收益不划算，等数据量翻倍再说 |
| B5 | `events.jsonl` 进库 | ⬜ 未做 | 自由文本事件目前只进 facts（`events_recent`）；进库需要先定结构（时间/类型/标签） |
| B6 | 体成分结论变断言 | ⬜ 未做 | InBody 观测已进 `obs`，但闸门 verdict 未转断言。金标用例里没有体成分锚点 |
| B7 | 主观数据参与判定 | ⬜ 未做 | `daily_log` 的 5 条 obs 未进断言。**主观断档 38 天**（最近 09-16），样本不足以定规则 |

## C. 技术债

| # | 项 | 状态 | 处理 |
|---|---|---|---|
| C1 | 步数双尺子（展示 10000 / 判读 8000） | ✅ **2026-10-25 定案 → docs/decisions.md D-14** | 保留双尺子并显式呈现：`units._judge` 改用 `judge_of`(8000)（此前用 goal，是全仓唯一「展示覆盖判读」处）；`metric_defs` 同时下发 goal/judge；前端两处达标判定改读 `steps_good`。实测 33 个有效日里 **7 天从「未达标」翻为「达标」** |
| C2 | rollup 与历史报告 8/30 不一致 | ✅ **2026-10-25 定案 → docs/decisions.md D-15** | 逐份核查后分级处理：**3 份无降档论证的改红**（09-03/04/14，其中 09-03 自称「四条线同向变差」仍写黄）、**5 份有明确论证的保留黄并补 `downgrade_reason`**（09-13/17、10-14/20/24）。改后 8/8 与 rollup 一致。历史 findings 仍不回填（D-05） |
| C3 | `summary.py` 算完就丢 | ✅ | 从 `build.py` 移除调用（每次构建白算一套 KPI）；文件移入 `tools/homepage/legacy/` |
| C4 | `observations.py` 0 调用方 | ✅ | 移入 `scripts/legacy/`，文件头写明两项职责已由 `obs` 表与 `metrics.json` 承担；SKILL.md 对应章节改写 |
| C5 | `assetver` 每次构建重写 10 个 js | ✅ **确认是设计使然，不是缺陷** | 数据 token 必须参与资源指纹——`data.js` 里嵌着 `index.json?b=<token>`，若数据变而 `data.js` 的 URL 不变，边缘缓存会一直吐旧数据。代价是每天 10 个 js 出现「已修改」，这是可接受的成本；**改掉它会破坏缓存策略** |
| C6 | smoke 一处口径型伪阳性 | ✅ | 断言从「等于 75」改成「显示值 = 四舍五入后的月均、配色按显示值判」——钉的是口径同源，不是记住某个月的数值 |
| C7 | `daily_brief` 身份混着 | ✅ **文档纠正，未搬迁** | 它是事实包的计算库（`fact_pack` import 7 个符号），不是 legacy。SKILL.md 与本文档已改口径；**物理拆分（抽出 `calc.py`）零行为收益、有回归风险，留到 S5** |
| C8 | 报告 trend 只有 4 天 | ✅ | `facts.scales`：week/month/roll7/28/90 各给 `latest` + `series`（滚动窗按非重叠抽样），锚定目标日而非全库最新 |
| C9 | 设备入睡窗 20/36 缺失 | 观察中 | 已用 `ruler` 显式化，根源（设备何时给窗）在设备侧，无法从我们这端解 |

---

## 顺带修掉的三个真 bug（都是这次接线过程中暴露的）

1. **空白日两套说法**：跨度内无 raw 的日子（如 09-30）在分片里是「一行全 null」，总览说「无采集记录」而判读区说「简报尚未生成」。交互探针抓到，已统一走 `dayIsEmpty()`。
2. **`delta` 没有覆盖门禁**：近 28/90 天窗覆盖 50% 时仍给「明显变化/在误差内」——把「缺数据」算成了「变了」。现在 coverage < 门槛直接判 `insufficient`。
3. **时段键语义冲突**：`threshold_min` 在睡眠里是钟点（90=01:30）、在久坐里是时长（480），渲染层只能二选一。已把语义写进键名（`bed_threshold_min` / `limit_min`），并让「键缺词表」直接抛错而不是凑合。

---

## 下一步（口径债已清，转工程侧）

- **C1/C2 已结清**，详见 `docs/decisions.md` D-14 / D-15。
- **B4 增量索引**：等 raw 天数 ≥ 180（现在 36）再评估，全量重建 2 秒不构成瓶颈。
- **B5/B6/B7**（自由文本事件 / 体成分断言 / 主观参与判定）：都需要先定结构，不是纯代码活。
- **S3 起（可选）**：`verify_judgment` 升级为「数字必须命中 evidence」的硬门槛（现在数字溯源是提示级，因为历史报告会引用 facts 窗口外的数值）。
- **工程侧 H1–H6**（路径集中 → prompt 入库 → skills 搬迁 → 拆 SKILL.md → reports 分层 → CI）：见 `notes/architecture_review_2026-10-25.md` 与 `docs/architecture.md` §6。
