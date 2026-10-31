# 前端与主页参考

> 前端细节的唯一权威来源。**流程级说明在 `docs/operations.md`，架构分层在 `docs/architecture.md`** —— 本文只写「怎么画、已知缺口、怎么验证」。
>
> **凡涉及主页数据推送、重新发布、前端 js/css 改动、综合指数口径、验证工具，先读本文件。**

**数据与代码分离（核心）**：`site/server.py`（纯标准库单端口服务）提供同源可写端点，数据落服务端 `.data/`，「换数据」= 一个 POST，**不需要重新上线**。
- 端点：`GET /api/data/index`、`GET /api/data/daily/YYYY-MM`（**不带 .json 后缀**，绕开按扩展名的边缘缓存；一律 `no-store`）· `GET /api/health` · `POST /api/push`（`X-Push-Token`）· `POST /api/rollback`。取数链路 `site/js/data.js`：**端点优先、失败回落随包静态副本**，走兜底时胶囊转琥珀标「静态副本」，不静默用旧数据。
- 更新与核验：`tools/build_homepage_data.py --push`（构建+推送+回读核验）· `tools/push_data.py --check`（exit 0 = 线上本地逐文件一致，**核验首选**，不必数响应头）。两脚本已自禁用系统代理。
- **只有前端代码/结构变更才需要重新发布**（`site/js/`、`site/css/`、`index.html`、`server.py`）：必须带 `language=python` + `startCmd="python3 server.py"` + `installCmd=""` + `appId=wbapp_3G1QX7kZjmy7RNbU9gBdT5` + `updateExistingApp=true`；**漏掉 language=python 就退回纯静态、热更新失效**。地址唯一来源 `tools/deploy.json`。
- **线上** https://health-daily-86790.app.workbuddy.host/ ，sandbox `76a3153c3ef5421cbcf482a2cb28373a`。⚠️ 曾有两个同名「个人健康主页」，认 appId；`wbapp_5qWlz1Cp4JKl8CtzKnZzsw` 已下线全 404。
- 服务端只服务扩展名白名单内文件：`server.py`、`.push_token`、`.data/` 一律 404。令牌在 `site/.push_token`，缺失则端点半闭并如实上报。启动自愈 `seed_state_from_bundle()`：`.data` 缺失或比打包副本旧则用副本播种。
- **资源必须带内容指纹**（`tools/homepage/assetver.py::stamp_all()`，随 build 幂等自动跑）：取数 URL 打 `?b=<token>`；**js 模块图内部 import 说明符也要打 `?v=<ver>`**（只打入口会得到「新入口+旧模块」混血）。**改了 js 必须重跑 build 再发布。** 数据两层：`index.json`（逐日轻量索引，首屏唯一载荷）+ `daily/YYYY-MM.json`（按月分片按需拉）。
- **判读按日装配**：一天一份，build 按日读全部 `body_brief_*.json`。新增一天 = 写 `judgment_D.json` → `fact_pack --date D --merge-judgment` → 重跑 build + push。- 综合指数（`scoring.py`）：睡眠达成/步数/HRV/静息心率/久坐五项等权（晨间快照日四项）。**铁律：分项必须由实测值经 rules.json 阈值推导，禁止常数短路**。**Garmin 的 BALANCED 由 7 日滚动均值决定，不能当「昨夜状态」；HRV 归一唯一入口 `recovery.py::hrv_view`**。KPI「HRV 达标率」= 当晚实测 ≥ 基线下限。
- **前端是单页单视图**，状态只有 `anchor` + `scale`（**六个**：日 / 周 / 月 / 近 7 天 / 近 28 天 / 近 90 天），没有「概览页/某日页」之分。逐指标统计按 `字段 != null` 过滤、不按 morning 整行排除（唯一整行特例是综合指数）。尺度决定日历选中 / 主值口径 / x 轴粒度 / 达标分母，由 `scope.js` 解析成「单元」。日历格三态点：实心绿达标 / 实心琥珀未达标 / 空心灰无实测——**缺数据不等于没做到**。
- **层次纪律**：①时间轴 → ②当前单元总览 → ③指标网格 → ④当日判读；**④只放人写的结论**，有量化指标的实测值一律归 ②/③。
- **准备度进总览、与综合指数并排**（行为达标度 vs 恢复/负荷准备度：各自具名 + 各自带环比，绝不合并）：档位用**设备自己的五档**（`constants.READY`），不套我们的 score 阈值。周/月是聚合值**不编档位词**。
- **空日文案只对今天说「进行中」**，过去/未来的空日标「无采集记录」。跨度内的空白日（有行但全 null）走**同一个空态**——`dayIsEmpty()`，否则总览说「无采集记录」、判读区说「简报尚未生成」，同屏两种说法。
- **指标定义来自后端，前端不留副本**：`constants.js` 的 `metrics(ix)` 从 `index.json.metric_defs` 派生 label/unit/dir/agg/goal/band，本文件只保留「怎么画」（fmt / detail 渲染器 / 目标线措辞）。老 index（无 metric_defs）走 `FALLBACK`，**先发 js 再推数据时不能白**。
- **环比与达标由后端判定**：`index.json.units[scale][key][metric]` = `{v 值, j 达标ok/no/na, c 覆盖率, n 实测天数, r 达标率, d 差值, dv 五档判定, vs 对比单元}`。前端只把 `dv` 翻成人话（`aggregate.VERDICT_TEXT`）：
  - `changed` → 才给红绿箭头 + 差值；`within-noise` 写「在误差内」；`suppressed`（步数这类 tier.change=low 的指标）写「不做环比」；`insufficient` 写「样本不足」。
  - **前端禁止自己比大小**——mdc 门槛在 `rules.confidence`，判定在 L2。前端画「↑7%」而报告写「在误差内」，两边看着都不算错，因为口径只约束了写报告的人。这条就是禁止它的理由。
  - **步数有两把尺子，必须并列呈现**（2026-10-25 C1）：`units[].j` 按判读线 **8000** 判达标，`goal` **10000** 只作目标；卡片文案写「目标 ≥ 10k · 达标线 ≥ 8k」。`metric_defs.steps_daily` 同时下发 `goal` 与 `judge`。⚠️ 达标判定在前端有**两处重复实现**——`aggregate.checkFns()` 与 `metricgrid.checkOne()`，改一处必须同步改另一处（两处都读 `ix.goals.steps_good`，旧 index 无此字段时回落 `ix.goals.steps`）。
  - `j=na`（覆盖不足）时卡片显示「覆盖 x% · 不给结论」，四项达标显示「–」而不是 0/0（0/0 会被读成「一天都没做到」）。
  - 滚动窗的环比**必须非重叠**（`prevUnitOf` 直接跳一个窗长）：相邻两窗只差一天时重叠 6/7，比它们等于比「自己和几乎自己」。
- **已知缺口（均未改代码）**：① 日历配色只由综合指数驱动，恢复报警日（09-07 准备度 41 LOW）日历上仍是绿色 92 徽标；② 主页准备度取单一主字段，**不认 `readiness_series`**——09-17 展示 HIGH(78)（来自 00:19 前夜重置），报告口径是日间 MODERATE(70)。
- **采集失败日不进达标率的分子分母**：`units.py` 的 `value` 只对有值日求平均、`judge_rate` 只统计有值日，失败日从不参与，所以日均与达标率没有被污染。真正的影响只有一处：`coverage = n / expected` 的分母是单元总天数，失败日会把它压低（10 月 19/31 ≈ 0.61，门槛 0.5；roll7 ≈ 0.71，门槛 0.7——**再多一个失败日就会掉成 `na` 不给结论**）。处置是把成因讲明白而不是改判：`units[].g` = 该单元内采集故障天数，卡片并列显示「覆盖 61% · 10 天采集故障」。**判定口径一个字没动**——真要动 L2 分母得先改 `scripts/units.py` 与 golden 用例，那是另一件事，且未必该做（样本少本身就会让均值不稳，压低 coverage 是诚实的）。

### 采集健康度与补数提示

**起因（要记住的错法）**：日历上「没有数」原来只有一种画法——空心灰点。但它混了三种完全不同的情况：端点报错（系统故障）、原始数据没落盘（采集没跑到）、设备没推数（没戴表）。**把故障画成人的行为，归因就错了**，「我最近是不是懈怠了」这个判断从第一眼就跟着错。

**数据侧（后端唯一口径）**
- `index.json.collect` = `{summary:{n,ok,failed,missing,absent,pending}, recent_errors:[{date,keys}], missing_ranges:["09-18~10-01",…]}`
- `days[].cs` = 该日采集状态四态；`days[].ce` = 失败端点名（仅 `cs=failed` 时下发）
- 判定唯一入口 `tools/homepage/indexlayer.py::collect_state()`，**优先级：报错 > 有数 > 无 raw > 今天未到 > 无佩戴**。报错排第一是因为「部分维度丢了」和「全好」长得一样（10-07 有 11379 步但 hrv 挂了）。
- 依赖两处 canonical 字段：`collect.errors`（来自 raw `sync_errors`，由 `adapters/garmin_cn.py` 带进 canonical）与 `coverage.has_data`。**`build.py` 重建 day 时必须原样透传这两个字段**——漏传会让整段跨度全被判成 `absent`。

**呈现侧**
- 日历三档（`calendar.js`）：`cs-failed` 琥珀底+橙左边条（5px）、`cs-missing` 斜纹（135° repeating-linear-gradient）、`cs-pending` 虚线边框；`absent` 沿用原空心灰。`title` 是唯一说清「为什么这天是空的」的地方，必须带原因，不能只写日期。
- **CSS 顺序纪律**：`.in-scope` / `.anchored` 必须用 `background-color` 而不是 `background` 简写——简写会把 `background-image` 重置成 `none`，把斜纹抹掉。轨迹类新样式一律放在它们之后。
- 胶囊（`main.js::syncText()`，纯函数、可单测）：`Garmin 已同步 · 37/60 天有实测 · 23 天未采到 · 更新于 11:31`。**禁止再用「N 个数据日」**——跨度 60 天里 22 天一个实测值都没有，那个措辞把空壳算成了有数据。
- 告警条 `#collect-bar`：只在有 failed/missing 时出现，**两行以内**。只展开最近一次，缺失按区间报（`missing_ranges`）——把 5 次失败的日期与端点全列出来会占三行，没人读，等于没提示。
- 补数提示 `#nudge-bar`：`index.json.nudge` = `{missing[], labels, last_logged, gap_days, prompt}`，由 `tools/homepage/nudge.py` 生成。**只读，不提供写入**——主页是公开站点，开写接口等于给任何人一个往 `daily_log.jsonl` 写字的洞；写入仍然只能经由 agent。断档天数要写出来：缺一天是疏忽，断 46 天是链路断了。

**小屏（≤430px）**：日历 7 列每列只剩 ~37px，内容最小需 ~60px，`grid` 项默认 `min-width:auto` 不收缩，于是整页横向溢出、最右列被裁。已加 `.cal-cell{min-width:0}` + 430px 断点压缩内边距/点位，并**隐藏分数徽标**（放不下时会被压成一位数，「88」显示「8」——错误数字比没有数字更糟），分档交给左色条、逐项交给四个点。

**跨月首日的三个坑（都已加防线，别改回去）**：
1. `smoke_site.mjs` 用 `ix.today` 当锚点读当月分片，每月 1 号读不存在的 `daily/YYYY-MM.json` 直接 ENOENT。已改成与 `main.js` 一致的「今天有行才用今天」。
2. `renderBrief` / `renderReadyTrail` 对索引里没有的日期照样去拉分片，1 号打开页面判读区显示「详情分片加载失败（2026-11）」。已加 `store.byDate[anchor]` 守卫。
3. `data.js::getJSON` 直接 `fetch(...)`，fetch 缺失时抛的是**同步** ReferenceError，逃出 `pick()` 的 then/catch，回落静态副本根本不触发，整页白屏。已改成先判能力、异常转 rejected promise。

**冒烟新增 24 项断言**（`== 采集健康度 ==` / `== 主观补数 ==` 两节）：验的是**归因没被混淆**——四态之和等于总行数、failed 行必须带端点名、非 failed 行不下发端点名、DOM 上三种状态类都在、title 写得清原因、胶囊不出现「N 个数据日」、告警条点明「不是没戴表」。`main.js` 为此导出 `syncText` / `renderNudge` / `renderCollectBar`，并用 `globalThis.__SMOKE_NO_BOOT__` 关掉 import 时的自动 boot。
- **进行中的当天**：units 的 `j` 会是 `na`（coverage < 0.95），卡片显示「覆盖 x% · 不给结论」但**数字照给**——步数这类白天累计值比「—」有信息量，前提是明确标了不可判。注意 index 行的 `steps` 对 morning 日为 null（不含白天 activity），而 units 的 `v` 有值（raw 全天累计），两者不同源，**主值以后端 `v` 为准**。


### 体成分（InBody）· 事件型数据源

**它不在 `days[]` 里。** `site/index.json` 顶层多了一个 `inbody` 块，由
`tools/homepage/inbody.py` 装配、`site/js/inbody.js` + `site/css/inbody.css` 渲染成独立一节
「体成分 InBody」（位于当日判读之后、footer 之前）。

- **不进日/周/月尺度**：InBody 一次体测一个截面，间隔以周/月计。塞进指标网格会暗示「每天都有值」，
  并让「月日均」这种不存在的口径凭空产生。所以它**不吃 anchor/scale，只在 boot 时渲染一次**。
- **只有 1 条记录时不画趋势**：页面明写「现在是截面，不是趋势」；`scan_count >= 2` 才谈变化。
- **体重必须给构成**（去脂 + 脂肪堆叠条），并把**晨起空腹体重并列**、写明「两个口径不可相减/不取平均」。
  不写这句，两个口径的数字摆在一起必然被读成「胖了 4.3kg」。
- **参考区间来自设备**（`MFA/BCA` 的 `*_MIN/_MAX`），前端不硬编码；每个数都给「相对区间站在哪」+ 越界量。
- **目标路径现算**：`体重 = FFM ÷ (1 − 目标体脂率)`，禁止写死具体体重值。
- 数据源：`data/inbody/{index,latest,profile}.json` + `raw/scan_*.json`，由
  `body-health/scripts/sync_inbody.py` 采集（独立凭据，与佳明无关）。
- 尺度按钮从 3 个变 6 个（日/周/月/7天/28天/90天），`.seg` 在 ≤620px 收窄（`base.css`），否则窄屏顶出卡片。
- **改了 `js/` 或 `css/` 就必须重新发布**（数据推送不影响它）；新加 `inbody.css` 时记得在
  `index.html` 里加 `<link>`，否则样式静默失效。

### 前端改动的验证手段
- `tools/smoke_site.mjs`：**改完 js/ 必跑**（零依赖，113 项断言，含取数链路 + 空日/冻死回归）。须按 index.html 里带指纹的地址 import 模块，否则拿到第二个模块实例直接崩。DOM 桩抓不到事件层问题。
- `tools/probe_interactions.py`：**真浏览器真点**，抓「点完冻死」这类交互层问题（8 用例，约 30 秒）。⚠️ **给无头 Chrome 传 `--user-data-dir=<新建目录>` 会让它永不返回**，症状是「所有用例 HUNG」——**连基线都 HUNG 就是工具坏了**。默认复用默认 profile + `--incognito`。
- **截图验证**：`"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --no-sandbox --hide-scrollbars --window-size=1440,4200 --screenshot=out.png --virtual-time-budget=9000 <url>`。**起本地服务必须在同一条 bash 里「起→截图→kill」，或者用 `run_in_background=true` 起的常驻 server**——用 `(cmd &)` 子 shell 起的不行，进程会随命令退出被回收。看非默认尺度就把 index.html 复制成 `site/_probe_*.html` 换显式 boot，截完即删。**呈现层问题只能靠截图发现。**

### 判读可达性与联动视图

**这一组解决的是「有结论看不见、有联动看不出来」。** 四个新增区块都不吃 `anchor`/`scale`——它们是长期视角，跟着日历翻页只会互相打断。

**① 判读流水 `#brief-log`**
- 数据：`index.json.briefs = {date: {head, lv}}`，由 `homepage/brief.py::brief_head()` 从 `judgment.cross[0]` 取**第一句**（≤86 字，超长截断加省略号）。**不取 `summary`**：那是当天的完整交代（带档位理由与截断声明），塞进列表会变成一坨。
- 形态：`<details>` 折叠面板，每行 = 日期 + 等级色点 + 首句；点任一行 → `store.scale="day"` + 跳到那天。
- **必须一并切到日尺度**：判读区只在日尺度存在，只改 anchor 不切尺度，点了没反应，看起来像坏了。
- 37 天约 6KB。

**② 当日背景事件 `#day-events`**
- 数据：`index.json.events = {date: [{t, c, ts}]}`，由 `homepage/events.py` 从 `data/logs/events.jsonl` 全量读入（20 多条约 6KB，不值得为它开按需端点）。单日超 6 条截断为「另有 N 条」。
- 位置：**判读正文之后、缺口之前**。结论和依据同屏——否则三个月后回看，会觉得那个判断来得莫名其妙。
- 类型顺序 `context > checkup > diet > note`：背景类最可能改变结论。

**③ 逐日达标矩阵 `#day-matrix`**
- 日历的**转置**：行=指标、列=最近 30 天。回答「这几天是不是好几项一起变差」——这个判断在 8 张平铺卡片之间来回扫是做不出来的，在日历的四个点里也做不出来（只有 4 项、没有指标身份）。
- 判定一律取 `units.day[date][metric].j`（后端唯一口径），**前端不自己比大小**。
- 行的过滤条件是「`units.day` 里真的出现过 `j`」，**不能用 `CHECK_FIELD` 当白名单**——那是四项达标的四个别名（sleep/steps/hrv/sedentary），拿它筛会把静息心率、中高强度、压力全滤掉，矩阵只剩两行（2×30=60 格，看起来像「只有两项被判定」）。实际应为 7 行 × 30 天 = 210 格。
- `na`（无判定）用斜纹，与 `no`（未达标）严格分开——**把「不知道」画成「没做到」是这个页面最容易犯的错**。
- 受容器宽度限制只画 30 天；更长的周期交给日历下方的热力条。

**④ 指标分组**
- `metric-grid` 由「8 张卡平铺」改为三组：**恢复**（睡眠 / HRV / 静息心率）、**行为**（步数 / 久坐 / 中高强度）、**负荷**（活动消耗 / 压力）。分组依据是「这个数回答什么问题」，不是医学分类。
- **组内才可比，跨组比较没有意义**（步数和 HRV 谁「更好」不是个问题）。
- 未列入的指标自动落到「其他」组，**不静默丢弃**——新增指标时页面不会悄悄少一张卡。
- 外层容器 class 从 `metric-grid` 改成 `mx-groups`，否则两层 grid 打架。

**⑤ `units[].g`（采集缺口）**
- 定义见上文「已澄清 ③」。字段 `g` 只在 `>0` 时下发。

**工具陷阱**：`probe_interactions.py` 若用「轮询 `p.poll()` 再 `stdout.read()`」，`--dump-dom` 输出走管道而**管道缓冲区只有 ~64KB**；页面变长后 Chrome 阻塞在 write 上永不退出，看门狗把**全部用例**判成 HUNG——**连不点击的基线也 HUNG**。必须用 `communicate(timeout=...)` 边读边等。**判据：连基线都 HUNG，先怀疑测量工具，不是产品。**
