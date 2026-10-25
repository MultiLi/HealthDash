# 健康主页（site/ + tools/）· 运维与前端参考

> 本文件是工作空间记忆 `.workbuddy/memory/MEMORY.md` 中「健康主页」与「前端改动验证」两节的完整版。
> **凡涉及主页数据推送、重新发布、前端 js/css 改动、综合指数口径、验证工具，先读本文件。**

**数据与代码分离（09-16 起，核心）**：`site/server.py`（纯标准库单端口服务）提供同源可写端点，数据落服务端 `.data/`，「换数据」= 一个 POST，**不需要重新上线**。
- 端点：`GET /api/data/index`、`GET /api/data/daily/YYYY-MM`（**不带 .json 后缀**，绕开按扩展名的边缘缓存；一律 `no-store`）· `GET /api/health` · `POST /api/push`（`X-Push-Token`）· `POST /api/rollback`。取数链路 `site/js/data.js`：**端点优先、失败回落随包静态副本**，走兜底时胶囊转琥珀标「静态副本」，不静默用旧数据。
- 更新与核验：`tools/build_homepage_data.py --push`（构建+推送+回读核验）· `tools/push_data.py --check`（exit 0 = 线上本地逐文件一致，**核验首选**，不必数响应头）。两脚本已自禁用系统代理。
- **只有前端代码/结构变更才需要重新发布**（`site/js/`、`site/css/`、`index.html`、`server.py`）：必须带 `language=python` + `startCmd="python3 server.py"` + `installCmd=""` + `appId=wbapp_3G1QX7kZjmy7RNbU9gBdT5` + `updateExistingApp=true`；**漏掉 language=python 就退回纯静态、热更新失效**。地址唯一来源 `tools/deploy.json`。
- **线上** https://health-daily-86790.app.workbuddy.host/ ，sandbox `76a3153c3ef5421cbcf482a2cb28373a`。旧域名 `personal-health-home-59181`（appId `wbapp_5qWlz1Cp4JKl8CtzKnZzsw`）09-16 已 unpublish 全 404。⚠️ 两个同名「个人健康主页」，认 appId。
- 服务端只服务扩展名白名单内文件：`server.py`、`.push_token`、`.data/` 一律 404。令牌在 `site/.push_token`，缺失则端点半闭并如实上报。启动自愈 `seed_state_from_bundle()`：`.data` 缺失或比打包副本旧则用副本播种。
- **资源必须带内容指纹**（`tools/homepage/assetver.py::stamp_all()`，随 build 幂等自动跑）：取数 URL 打 `?b=<token>`；**js 模块图内部 import 说明符也要打 `?v=<ver>`**（只打入口会得到「新入口+旧模块」混血）。**改了 js 必须重跑 build 再发布。** 数据两层：`index.json`（逐日轻量索引，首屏唯一载荷）+ `daily/YYYY-MM.json`（按月分片按需拉）。
- **判读按日装配**：一天一份，build 按日读全部 `body_brief_*.json`。新增一天 = 写 `judgment_D.json` → `fact_pack --date D --merge-judgment` → 重跑 build + push。- 综合指数（`scoring.py`）：睡眠达成/步数/HRV/静息心率/久坐五项等权（晨间快照日四项）。**铁律：分项必须由实测值经 rules.json 阈值推导，禁止常数短路**。**Garmin 的 BALANCED 由 7 日滚动均值决定，不能当「昨夜状态」；HRV 归一唯一入口 `recovery.py::hrv_view`**。KPI「HRV 达标率」= 当晚实测 ≥ 基线下限。
- **前端是单页单视图**，状态只有 `anchor` + `scale`（日/周/月），没有「概览页/某日页」之分。逐指标统计按 `字段 != null` 过滤、不按 morning 整行排除（唯一整行特例是综合指数）。尺度决定日历选中 / 主值口径 / x 轴粒度 / 达标分母，由 `scope.js` 解析成「单元」。日历格三态点：实心绿达标 / 实心琥珀未达标 / 空心灰无实测——**缺数据不等于没做到**。
- **层次纪律**：①时间轴 → ②当前单元总览 → ③指标网格 → ④当日判读；**④只放人写的结论**，有量化指标的实测值一律归 ②/③。
- **准备度进总览、与综合指数并排**（行为达标度 vs 恢复/负荷准备度：各自具名 + 各自带环比，绝不合并）：档位用**设备自己的五档**（`constants.READY`），不套我们的 score 阈值。周/月是聚合值**不编档位词**。
- **空日文案只对今天说「进行中」**，过去/未来的空日标「无采集记录」。
- **已知缺口（均未改代码）**：① 日历配色只由综合指数驱动，恢复报警日（09-07 准备度 41 LOW）日历上仍是绿色 92 徽标；② 主页准备度取单一主字段，**不认 `readiness_series`**——09-17 展示 HIGH(78)（来自 00:19 前夜重置），报告口径是日间 MODERATE(70)；③ 数据模型无「部分日」标记，半天账会在主页当完整日展示（09-15）。

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
- **改了 `js/` 或 `css/` 就必须重新发布**（数据推送不影响它）；新加 `inbody.css` 时记得在
  `index.html` 里加 `<link>`，否则样式静默失效。

### 前端改动的验证手段（09-16 立，血账）
- `tools/smoke_site.mjs`：**改完 js/ 必跑**（零依赖，77 项断言，含取数链路 + 空日/冻死回归）。须按 index.html 里带指纹的地址 import 模块，否则拿到第二个模块实例直接崩。DOM 桩抓不到事件层问题。
- `tools/probe_interactions.py`：**真浏览器真点**，抓「点完冻死」这类交互层问题（8 用例，约 30 秒）。⚠️ **给无头 Chrome 传 `--user-data-dir=<新建目录>` 会让它永不返回**，症状是「所有用例 HUNG」——**连基线都 HUNG 就是工具坏了**。默认复用默认 profile + `--incognito`。
- **截图验证**：`"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --no-sandbox --hide-scrollbars --window-size=1440,4200 --screenshot=out.png --virtual-time-budget=9000 <url>`。**起本地服务必须在同一条 bash 里「起→截图→kill」**（`run_in_background` 起的 server 从别的调用连不上）。看非默认尺度就把 index.html 复制成 `site/_probe_*.html` 换显式 boot，截完即删。**呈现层问题只能靠截图发现。**
