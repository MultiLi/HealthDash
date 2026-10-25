# 前端与主页参考

> 前端细节的唯一权威来源。**流程级说明在 `docs/operations.md`，架构分层在 `docs/architecture.md`** —— 本文只写「怎么画、已知缺口、怎么验证」。
>
> **凡涉及主页数据推送、重新发布、前端 js/css 改动、综合指数口径、验证工具，先读本文件。**
>
> 原路径 `tools/HOMEPAGE_NOTES.md`，2026-10-25 移入 `docs/`（工具目录不放文档）。

**数据与代码分离（09-16 起，核心）**：`site/server.py`（纯标准库单端口服务）提供同源可写端点，数据落服务端 `.data/`，「换数据」= 一个 POST，**不需要重新上线**。
- 端点：`GET /api/data/index`、`GET /api/data/daily/YYYY-MM`（**不带 .json 后缀**，绕开按扩展名的边缘缓存；一律 `no-store`）· `GET /api/health` · `POST /api/push`（`X-Push-Token`）· `POST /api/rollback`。取数链路 `site/js/data.js`：**端点优先、失败回落随包静态副本**，走兜底时胶囊转琥珀标「静态副本」，不静默用旧数据。
- 更新与核验：`tools/build_homepage_data.py --push`（构建+推送+回读核验）· `tools/push_data.py --check`（exit 0 = 线上本地逐文件一致，**核验首选**，不必数响应头）。两脚本已自禁用系统代理。
- **只有前端代码/结构变更才需要重新发布**（`site/js/`、`site/css/`、`index.html`、`server.py`）：必须带 `language=python` + `startCmd="python3 server.py"` + `installCmd=""` + `appId=wbapp_3G1QX7kZjmy7RNbU9gBdT5` + `updateExistingApp=true`；**漏掉 language=python 就退回纯静态、热更新失效**。地址唯一来源 `tools/deploy.json`。
- **线上** https://health-daily-86790.app.workbuddy.host/ ，sandbox `76a3153c3ef5421cbcf482a2cb28373a`。旧域名 `personal-health-home-59181`（appId `wbapp_5qWlz1Cp4JKl8CtzKnZzsw`）09-16 已 unpublish 全 404。⚠️ 两个同名「个人健康主页」，认 appId。
- 服务端只服务扩展名白名单内文件：`server.py`、`.push_token`、`.data/` 一律 404。令牌在 `site/.push_token`，缺失则端点半闭并如实上报。启动自愈 `seed_state_from_bundle()`：`.data` 缺失或比打包副本旧则用副本播种。
- **资源必须带内容指纹**（`tools/homepage/assetver.py::stamp_all()`，随 build 幂等自动跑）：取数 URL 打 `?b=<token>`；**js 模块图内部 import 说明符也要打 `?v=<ver>`**（只打入口会得到「新入口+旧模块」混血）。**改了 js 必须重跑 build 再发布。** 数据两层：`index.json`（逐日轻量索引，首屏唯一载荷）+ `daily/YYYY-MM.json`（按月分片按需拉）。
- **判读按日装配**：一天一份，build 按日读全部 `body_brief_*.json`。新增一天 = 写 `judgment_D.json` → `fact_pack --date D --merge-judgment` → 重跑 build + push。- 综合指数（`scoring.py`）：睡眠达成/步数/HRV/静息心率/久坐五项等权（晨间快照日四项）。**铁律：分项必须由实测值经 rules.json 阈值推导，禁止常数短路**。**Garmin 的 BALANCED 由 7 日滚动均值决定，不能当「昨夜状态」；HRV 归一唯一入口 `recovery.py::hrv_view`**。KPI「HRV 达标率」= 当晚实测 ≥ 基线下限。
- **前端是单页单视图**，状态只有 `anchor` + `scale`（**六个**：日 / 周 / 月 / 近 7 天 / 近 28 天 / 近 90 天），没有「概览页/某日页」之分。逐指标统计按 `字段 != null` 过滤、不按 morning 整行排除（唯一整行特例是综合指数）。尺度决定日历选中 / 主值口径 / x 轴粒度 / 达标分母，由 `scope.js` 解析成「单元」。日历格三态点：实心绿达标 / 实心琥珀未达标 / 空心灰无实测——**缺数据不等于没做到**。
- **层次纪律**：①时间轴 → ②当前单元总览 → ③指标网格 → ④当日判读；**④只放人写的结论**，有量化指标的实测值一律归 ②/③。
- **准备度进总览、与综合指数并排**（行为达标度 vs 恢复/负荷准备度：各自具名 + 各自带环比，绝不合并）：档位用**设备自己的五档**（`constants.READY`），不套我们的 score 阈值。周/月是聚合值**不编档位词**。
- **空日文案只对今天说「进行中」**，过去/未来的空日标「无采集记录」。跨度内的空白日（有行但全 null）走**同一个空态**——`dayIsEmpty()`，否则总览说「无采集记录」、判读区说「简报尚未生成」，同屏两种说法。
- **指标定义来自后端，前端不留副本**（2026-10-25）：`constants.js` 的 `metrics(ix)` 从 `index.json.metric_defs` 派生 label/unit/dir/agg/goal/band，本文件只保留「怎么画」（fmt / detail 渲染器 / 目标线措辞）。老 index（无 metric_defs）走 `FALLBACK`，**先发 js 再推数据时不能白**。
- **环比与达标由后端判定**（2026-10-25）：`index.json.units[scale][key][metric]` = `{v 值, j 达标ok/no/na, c 覆盖率, n 实测天数, r 达标率, d 差值, dv 五档判定, vs 对比单元}`。前端只把 `dv` 翻成人话（`aggregate.VERDICT_TEXT`）：
  - `changed` → 才给红绿箭头 + 差值；`within-noise` 写「在误差内」；`suppressed`（步数这类 tier.change=low 的指标）写「不做环比」；`insufficient` 写「样本不足」。
  - **前端禁止自己比大小**——mdc 门槛在 `rules.confidence`，判定在 L2。以前这里画「↑7%」而报告写「在误差内」，两边都不算错，因为口径只约束了写报告的人。
  - `j=na`（覆盖不足）时卡片显示「覆盖 x% · 不给结论」，四项达标显示「–」而不是 0/0（0/0 会被读成「一天都没做到」）。
  - 滚动窗的环比**必须非重叠**（`prevUnitOf` 直接跳一个窗长）：相邻两窗只差一天时重叠 6/7，比它们等于比「自己和几乎自己」。
- **已知缺口（均未改代码）**：① 日历配色只由综合指数驱动，恢复报警日（09-07 准备度 41 LOW）日历上仍是绿色 92 徽标；② 主页准备度取单一主字段，**不认 `readiness_series`**——09-17 展示 HIGH(78)（来自 00:19 前夜重置），报告口径是日间 MODERATE(70)。
- **进行中的当天**：units 的 `j` 会是 `na`（coverage < 0.95），卡片显示「覆盖 x% · 不给结论」但**数字照给**——步数这类白天累计值比「—」有信息量，前提是明确标了不可判。注意 index 行的 `steps` 对 morning 日为 null（不含白天 activity），而 units 的 `v` 有值（raw 全天累计），两者不同源，**主值以后端 `v` 为准**。


### 云服务（WorkBuddy Cloud Service · 2026-10-25 开通）

**一句话：云端存「最近 7 天的逐日事实 + 版本指纹」，页面读它来做一致性校验——不是主页的数据来源。**

| 项 | 值 |
|---|---|
| applicationId | `wbapp_3G1QX7kZjmy7RNbU9gBdT5`（与发布同一个 id，域名不变才不影响 Origin 校验） |
| resourceId | `wbcs_jxiAJka8Buhis5RcgndO9u` |
| 表 | `health_meta`（1 行，版本指纹/基线/goals）· `health_days`（逐日轻量索引行）· `health_shards`（月度分片，建表但暂未写入） |
| RLS | 三张表均 `ENABLE RLS` + **只有 SELECT 策略**给 `anon, authenticated`；**没有任何客户端写路径** |
| 配置唯一来源 | `tools/cloud.json` → 构建时注入 `index.json` 的 `cloud` 节点 → 前端从数据里读 |

**为什么 SDK 自托管**：站是纯静态、无构建步骤（技能文档的 Form B）。`@dev` 的 IIFE 从
jsdelivr 取不到（本机 curl 返回 000），所以把 `npm pack @tencent-ai/workbuddy-cloud-sdk@dev`
解出来的 `lib/index.global.js`（0.1.4-dev，72KB，自包含）放在 `site/vendor/`。
**只换了 `<script src>` 的位置，初始化与模块 API 一字未改**。升级只需替换该文件。

**前端怎么用**（`site/js/cloud.js`）：
- `pull(ix)` 读 `health_meta(id=1)` + `health_days` 最近 60 行；
- `compare(ix, snap)` **按字段逐项比，不能整串比**——PostgreSQL 的 jsonb 会重排对象键，
  整串比会 100% 假阳性（smoke 里有一条专门测这个）；
- 状态：`in-sync` / `mismatch`（本地为准并标脏）/ `ahead`（云端有新日）/ `off` / `unavailable`；
- **首屏不等它**：`checkCloud()` 在 `init` 最后异步发起，拿到才补一句胶囊文案，
  失败标「云端不可用」但**不标脏**（标脏留给「用了旧数据」）。

**为什么不把 units 放进云**：units 是判定派生（165KB / 196 单元 / 1553 格，每天全量重算）。
云端存**事实**（逐日指标），判定留在构建产物里——两者用 `generated_at` 锁同源。

**同步方式**：`health_days` 只保留最近 `sync_window_days`（7）天。更新走管理通道
（MCP `workbuddy_cloudservice_db_exec_sql`，`mode=write`，用
`INSERT ... SELECT ... FROM jsonb_array_elements($1::jsonb) ... ON CONFLICT DO UPDATE`，
一次调用写一批）。**这是唯一写路径**：没有 token、没有匿名写接口，客户端即使拿到
publishableKey 也只能读。云端落后时胶囊会显示「云端 N 天不一致」，那时让 agent 跑一次同步。

⚠️ **可见性**：三张表是 public read，`publishableKey` 也随 `index.json` 下发。这与现状等价
（`index.json` 本来就是公开静态文件，含全部健康数据），**没有新增暴露面**；
但反过来也意味着——**它不提供任何隐私保护**，别把它当「加密存储」。

### 体成分（InBody）· 事件型数据源（2026-10-18 建）

**它不在 `days[]` 里。** `site/index.json` 顶层多了一个 `inbody` 块，由
`tools/homepage/inbody.py` 装配、`site/js/inbody.js` + `site/css/inbody.css` 渲染成独立一节
「体成分 InBody」（位于当日判读之后、footer 之前）。

- **不进日/周/月尺度**：InBody 一次体测一个截面，间隔以周/月计。塞进指标网格会暗示「每天都有值」，
  并让「月日均」这种不存在的口径凭空产生。所以它**不吃 anchor/scale，只在 boot 时渲染一次**。
- **只有 1 条记录时不画趋势**：页面明写「现在是截面，不是趋势」；`scan_count >= 2` 才谈变化。
- **体重必须给构成**（去脂 + 脂肪堆叠条），并把**晨起空腹体重并列**、写明「两个口径不可相减/不取平均」。
  不写这句，90.2 与 85.9 摆一起必然被读成「胖了 4.3kg」。
- **参考区间来自设备**（`MFA/BCA` 的 `*_MIN/_MAX`），前端不硬编码；每个数都给「相对区间站在哪」+ 越界量。
- **目标路径现算**：`体重 = FFM ÷ (1 − 目标体脂率)`，禁止写死 85.9/80.8。
- 数据源：`data/inbody/{index,latest,profile}.json` + `raw/scan_*.json`，由
  `body-health/scripts/sync_inbody.py` 采集（独立凭据，与佳明无关）。
- 尺度按钮从 3 个变 6 个（日/周/月/7天/28天/90天），`.seg` 在 ≤620px 收窄（`base.css`），否则窄屏顶出卡片。
- **改了 `js/` 或 `css/` 就必须重新发布**（数据推送不影响它）；新加 `inbody.css` 时记得在
  `index.html` 里加 `<link>`，否则样式静默失效。

### 前端改动的验证手段（09-16 立，血账）
- `tools/smoke_site.mjs`：**改完 js/ 必跑**（零依赖，113 项断言，含取数链路 + 空日/冻死回归）。须按 index.html 里带指纹的地址 import 模块，否则拿到第二个模块实例直接崩。DOM 桩抓不到事件层问题。
- `tools/probe_interactions.py`：**真浏览器真点**，抓「点完冻死」这类交互层问题（8 用例，约 30 秒）。⚠️ **给无头 Chrome 传 `--user-data-dir=<新建目录>` 会让它永不返回**，症状是「所有用例 HUNG」——**连基线都 HUNG 就是工具坏了**。默认复用默认 profile + `--incognito`。
- **截图验证**：`"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --no-sandbox --hide-scrollbars --window-size=1440,4200 --screenshot=out.png --virtual-time-budget=9000 <url>`。**起本地服务必须在同一条 bash 里「起→截图→kill」，或者用 `run_in_background=true` 起的常驻 server**（2026-10-25 实测可用；此前记的「run_in_background 起的 server 连不上」是因为用了 `(cmd &)` 子 shell，进程随命令退出就没了）。看非默认尺度就把 index.html 复制成 `site/_probe_*.html` 换显式 boot，截完即删。**呈现层问题只能靠截图发现。**
