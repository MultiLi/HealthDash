# 欠账清单（2026-10-25 15:50 盘点）

> 对照 `notes/architecture_v4.md` 的 S1~S5 计划逐项核验代码现状。
> **一句话结论：S1 的三层（obs / units / assertions）建好并验证了，但生产链路一条都没接上；
> 计划里让这套东西"可复现"的四件关键件——compose、verify、backtest、golden——一件都没做。**

---

## A. 建好了但没接线（优先级最高：不接就等于没做）

| # | 项 | 现状证据 | 后果 |
|---|---|---|---|
| A1 | **每晚报告不读断言** | `fact_pack.py` 里 `assert` 出现 **0** 次；`collect.py` 调 `index.py` **0** 次；5 个自动化记忆文件里引用 `index.py/units.py/assertions` 的 **0** 个 | 23:30 的报告仍然读 `facts_*.json` + 散文规则。新三层只在我手动跑时存在，**没有一天的业务经过它** |
| A2 | **前端不消费 units/metric_defs** | `site/js/*.js` 里 `units` / `metric_defs` 命中 **0** 个文件；`constants.js` 仍自带 `METRICS` 表；`aggregate.js` 的 `CHECKS` 仍自己判达标 | 定义了「指标定义唯一来源」，但**前端还是第二份定义**——收口只完成了后端半程 |
| A3 | **没有无 LLM 路径** | `compose.py` 不存在；`verify_judgment.py` 不存在 | 架构里承诺「LLM 挂了也能出骨架报告」，实际仍然是「LLM 不给就空白」；也没有任何机制拦「报告里的数字是编的」 |

## B. 计划里明确写了、确实没做

| # | 项 | 现状 | 计划出处 |
|---|---|---|---|
| B1 | `judgment.findings[]` 结构化 | 32 份 judgment 里含 `findings` 的 **0** 份 | 决策 ③（新报告起用） |
| B2 | 金标用例 | `tests/golden.json` 不存在 | §七 一致性与可复现 |
| B3 | 回测 | `backtest.py` 不存在 | §七「改阈值前必跑」 |
| B4 | 增量索引 | `--since` 参数在，但 `build()` 仍是全量重算（1.98s，暂时无所谓，但依赖图没做） | §六 增量与失效传播 |
| B5 | `events.jsonl` 进库 | `index.py` 里 `events.jsonl` 出现 0 次 | §五 预处理清单（自由文本） |
| B6 | 体成分结论变断言 | `dimension='bodycomp'` 断言 **0** 条（InBody 观测已进 obs，但 `check_inbody` 的 verdict 没转成断言） | §五 |
| B7 | 主观数据参与判定 | `sleep_quality` 有 5 条 obs，参与断言的 **0** 条 | §五「主观」行 |

## C. 过程中发现的缺陷 / 技术债

| # | 项 | 证据 | 影响 |
|---|---|---|---|
| C1 | **步数双尺子** | `metricdefs resolve steps_daily` → goal 10000 / judge 8000 | 9000 步那天主页显示「未达标」、报告不吭声。**等你拍** |
| C2 | **rollup 与历史报告 8/30 不一致** | 代码总评红、历史报告全黄，32 份无一红灯 | 「总评=最差维度」从未被 LLM 真正执行。**等你拍**（改历史 or 允许降档留痕） |
| C3 | `summary.py` 算完就丢 | `build.py` 调用 `build_summary`，但 `index.json` 里没有 `summary` | 每次构建白算一套全量 KPI |
| C4 | `observations.py` 仍是 0 调用方 | 引用文件数 0 | 它的 `comparable_on` / `resolve()`（多源不可比裁决）没人用；units/assert 自己又实现了一遍取值 |
| C5 | `assetver` 每次构建重写 js | 模块 hash 不剥离 `?b=` | 每天 git 里 10 个 js「已修改」，真实改动被淹没 |
| C6 | smoke 一处口径型伪阳性 | 9 月均准备度期望 74.5、实际显示 76 | 每次跑都是 78/1，真失败容易被忽略 |
| C7 | `daily_brief.py` 身份混着 | `fact_pack` 从它 import 7 个符号（`compute_z2` / `detect_exercise` / `build_trend`…） | 文档说「已降级为 legacy」，实际是主流程计算库 |
| C8 | 报告 trend 仍是 4 天固定表 | `fact_pack.build_trend(days)`，`--days 4` | units 能出 7/30/90 天序列，报告没用上 |
| C9 | 设备入睡窗 20/36 缺失 | `data.reference.missing` 16 天 | 已用 `ruler` 显式化，但根源（设备何时给窗）未解 |

---

## 建议的推进顺序

**第一步：A1 + A2（接线）** —— 不接，前面所有工作都只是「一个能跑但没人用的库」。
- A1：`collect.py` 末尾加 `index.py build`（失败不影响采集）；`fact_pack` 增加 `assertions_today` 节点；
  23:30 的 prompt 从「读 facts」改成「读 assertions + facts 补充」。
- A2：前端改读 `index.json.metric_defs` + `units`（S2）。**顺序不能反：先发兼容版 js，再推数据。**

**第二步：A3 + B1（可复现闭环）** —— 这两件一起做才成立：
`compose.py`（断言直渲骨架）→ LLM 只补交叉段 → `verify_judgment.py` 校验数字与断言引用 →
`findings[]` 落进 judgment。做完之后「LLM 挂了也有报告」与「报告数字不可编」同时成立。

**第三步：B2 + B3（回归能力）** —— 有了 findings 才有东西可比；金标用例先挑 10 天
（09-11 评分事故 / 09-12 半天账 / 10-22 同向偏软 / 10-24 单腿过门槛 / 10-08 久坐伪影 / 10-25 空占位…）。

**第四步：清债** —— C3 / C4 / C5 / C6 / C7 可以一次清完，都是"删代码"为主，风险低。

**待你拍的两件（不拍就一直在）**：C1 步数双尺子、C2 总评分歧。

---

## 附：C 决策已落地（缺参考量 → 退静态 + 强制标尺）

`metrics.json` 增 `reference_policy`：`on_missing=fallback_static`、`must_report_ruler=true`，
并记录被否掉的两个备选（`skip_judgement` / `always_static`）。
`metricdefs.ruler_of()` 是唯一尺子判定入口；`index.py verify` 新增第 11 项检查
「凡 rule_ref 含 `ref:` 的断言，evidence 必须带 ruler」——现在 0 条违规，
入睡判定实测 7 天用设备窗、10 天退回静态。
