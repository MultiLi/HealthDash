#!/usr/bin/env node
/* 健康主页前端冒烟测试。
 *
 * 为什么需要它：站点是零依赖的原生 ES module，没有构建步骤，也没有测试。
 * 改完 js/ 之后，语法错误能靠 node --check 抓，但「渲染逻辑跑不跑得通、
 * 日历 / 单元聚合 / sparkline 有没有真产出」抓不到——必须真的执行一遍。
 * 本机 headless Chrome 在沙箱里起不来（exit 137），浏览器截图路线走不通，
 * 这个 DOM 桩方案是唯一可靠的自动验证手段。
 *
 * 用法: node tools/smoke_site.mjs    （退出码非 0 表示有断言失败）
 */
import fs from "node:fs";
import path from "node:path";

const SITE = path.join(path.dirname(new URL(import.meta.url).pathname), "..", "site");

/* ---------------- 最小 DOM 桩 ---------------- */
const nodes = new Map();
function mkEl(id) {
  const el = {
    id, _html: "", textContent: "", hidden: false, attrs: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    setAttribute(k, v) { el.attrs[k] = v; },
    getAttribute(k) { return el.attrs[k] ?? null; },
    querySelector() { return null; },
    querySelectorAll() { return []; }
  };
  Object.defineProperty(el, "innerHTML", { get() { return el._html; }, set(v) { el._html = String(v); } });
  return el;
}
globalThis.document = {
  getElementById(id) { if (!nodes.has(id)) nodes.set(id, mkEl(id)); return nodes.get(id); },
  querySelectorAll() { return []; },
  addEventListener() {},
  body: mkEl("body")
};
globalThis.window = { scrollTo() {} };

/* ---------------- 断言 ---------------- */
let failed = 0, passed = 0;
function ok(cond, label, detail) {
  if (cond) { passed++; console.log("  ✓ " + label + (detail ? "  " + detail : "")); }
  else { failed++; console.log("  ✗ " + label + (detail ? "  " + detail : "")); }
}
const count = (s, re) => (s.match(re) || []).length;
const html = (id) => document.getElementById(id).innerHTML;
/* 有些节点是 textContent 写入（不反映到 innerHTML），断言要按对应通道读 */
const text = (id) => document.getElementById(id).textContent;
const hidden = (id) => document.getElementById(id).hidden;

/* ---------------- 装配 ---------------- */
/* 模块必须按 index.html 里那个带内容指纹的地址加载：js 之间互 import 时用的都是
   `./x.js?v=<指纹>`，若测试自己按无指纹路径 import，就会拿到**第二个模块实例**，
   store 里的状态写不到渲染器读的那一份上（2026-09-16 踩过：store.ix 恒 undefined）。 */
const idxHtml = fs.readFileSync(path.join(SITE, "index.html"), "utf8");
const VER = (idxHtml.match(/js\/main\.js\?v=([0-9a-f]+)/) || [])[1] || "";
const mod = (rel) => path.join(SITE, rel) + (VER ? "?v=" + VER : "");

const { store } = await import(mod("js/store.js"));
/* main.js 的 boot 会在 import 时自动触发（拉数据 + 整页渲染）。
   DOM 桩下那一步既没意义又会把 body 写脏，用显式开关关掉。
   ——但纯函数（syncText）和片段渲染器（renderNudge）仍然要能单独调用。 */
globalThis.__SMOKE_NO_BOOT__ = true;
const { syncText, renderNudge, renderCollectBar } = await import(mod("js/main.js"));
const { renderPage, renderBriefLog } = await import(mod("js/metricgrid.js"));
const { unitOf, prevUnitOf, unitSeries, unitRows } = await import(mod("js/scope.js"));
const { DETAILS } = await import(mod("js/detail.js"));
const { metrics } = await import(mod("js/constants.js"));
const { metricValue, rowsWith, unitRowsAll, unitCell, cellJudge } = await import(mod("js/aggregate.js"));

console.log("== 发布产物指纹 ==");
ok(!!VER, "index.html 的资源引用已带内容指纹（CDN 免疫）", VER ? "v=" + VER : "缺失！");

/* ---------------- 取数链路：端点优先，静态副本兜底 ----------------
   这两条路径是「数据不用重新上线」的全部依据，必须真的执行一遍：
   端点可用 → 必须走端点；端点 5xx → 必须回落打包副本，且回落的 URL 要带数据指纹。 */
console.log("\n== 取数链路：热更新端点优先，打包副本兜底 ==");
const { loadIndex, ensureShard } = await import(mod("js/data.js"));
let calls = [], endpointOk = true;
globalThis.fetch = function (url) {
  url = String(url);
  calls.push(url);
  const isApi = url.indexOf("api/data/") === 0;
  if (isApi && !endpointOk) return Promise.resolve({ ok: false, status: 503 });
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ way: isApi ? "endpoint" : "bundled" }) });
};

calls = []; endpointOk = true;
let got = await loadIndex();
ok(got.way === "endpoint" && store.dataFrom === "endpoint", "端点可用时走端点",
  calls.join(" → "));
ok(calls[0] === "api/data/index", "端点路径不带 .json 后缀（绕开按扩展名的边缘缓存）", calls[0]);

calls = []; endpointOk = false;
got = await loadIndex();
ok(got.way === "bundled" && store.dataFrom === "bundled", "端点异常时回落随包静态副本",
  calls.join(" → "));
ok(calls.length === 2 && /^index\.json\?b=[0-9a-f]{8}$/.test(calls[1]),
  "回落的确实是带数据指纹的静态路径", calls[1]);
ok(store.lastEndpointError === "HTTP 503", "端点失败原因被记下（不静默吞掉）", store.lastEndpointError);

calls = []; endpointOk = true;
await ensureShard("2026-08");                       // 未加载过的月份才真的发请求
ok(calls[0] === "api/data/daily/2026-08", "分片同样先打端点", calls[0]);
delete store.shards["2026-08"];
calls = []; endpointOk = false;
await ensureShard("2026-08");
ok(/^daily\/2026-08\.json\?b=[0-9a-f]{8}$/.test(calls[1]), "分片也会回落静态副本", calls[1]);
delete store.shards["2026-08"];
delete store.pending["2026-08"];
delete globalThis.fetch;

const ix = JSON.parse(fs.readFileSync(path.join(SITE, "index.json"), "utf8"));
store.ix = ix;
store.rows = ix.days.slice();
store.closed = ix.days.filter((r) => !r.morning);
ix.days.forEach((r) => { store.byDate[r.date] = r; });
/* 锚点必须复刻 main.js 的判定，不能简化成 `ix.today || ix.default_day`：
   今天常常**还没有行**（当晚才出终版），main.js 于是退回 default_day。
   测试若照抄简化版，每月 1 号就会去读「当月分片」——那天根本还没有这个月的数据，
   直接 ENOENT 崩在 117 行（2026-11-01 实证）。测试替身跑偏，报出来的是假 bug。 */
store.anchor = store.byDate[ix.today] ? ix.today : (ix.default_day || ix.days[ix.days.length - 1].date);
store.month = store.anchor.slice(0, 7);
store.scale = "month";
const shardMonth = store.anchor.slice(0, 7);
const shardPath = path.join(SITE, "daily", shardMonth + ".json");
store.shards[shardMonth] = fs.existsSync(shardPath)
  ? JSON.parse(fs.readFileSync(shardPath, "utf8")).days
  : {};

/* ---------------- 测试锚点：动态挑，不写死日期 ----------------
   这些断言验证的是**规则**（「无数据日要给空态」「单点轨迹不画」），
   不是**某一天的运气**。凡依赖真实数据状态的锚点，一律从当前数据里挑；
   挑不到就明确跳过（打印 `– 跳过`），而不是让整个测试变红。

   为什么改成这样（2026-10-31 复盘）：10-25 曾被当作「覆盖不足的典型」写死在
   三处断言里，而该日 raw 后来被重采补全成 complete（7729 步 / 截止次日 00:00），
   于是三条断言同时失效——看起来像产品坏了，其实只是那一天不再是那样。
   同理，拿 `ix.today` 当锚点也不安全：当天可能整日无数据（手表没同步），
   而无数据日按设计不渲染四项达标。 */
/* 无数据日 = 索引里**有这一行**，但没有任何实测值。
   注意不是「索引里没有这一天」——主页跨度含断档空白格，缺采集的日子照样有行。 */
const isEmptyRow = (r) => r.steps == null && r.sleep_h == null &&
  r.hrv == null && r.score == null;

/* 锚点必须是**最后一个有实测的完整日**，不能只取 `closed` 末项。
   2026-11-01 实证：10-31 采集整日失败（steps/sleep 全 null，三个端点报错），
   但它 `morning=false`（不是当天，所以不算进行中），于是成为 `closed` 末项，
   被当作「代表日」去验四项达标与卡内细分——七条细分同时为 0 字符，看起来像
   产品把渲染炸了，其实只是那天的数据没采到。**测试的基准日必须是真有数据的那天。**
   （10-31 这类"采集失败但非进行中"的日子，产品层由 collect 状态字段标记，见 index.json.collect。） */
const closedLast = (store.closed.filter((r) => !isEmptyRow(r)).pop()
  || store.closed[store.closed.length - 1]).date;
const anchorMonth = ix.default_day.slice(0, 7);
const inAnchorMonth = (d) => d.slice(0, 7) === anchorMonth;

/* 无数据日（isEmptyRow 已在锚点段定义）——这里只挑「锚点月内、且不是今天」的空日。
   **必须再排除 has_brief**：采集失败的那天仍可能产出一份「数据缺失」的判读简报
   （2026-10-31 就是：raw 三个端点报错，报告照样写了 gaps），此时判读区有正文、
   不是空态。把这种日子当空态样本，验的是报告层，不是空态渲染。 */
const emptyDays = ix.days.filter((r) => inAnchorMonth(r.date) && isEmptyRow(r) && !r.has_brief)
  .map((r) => r.date);
/* 必须排除今天：今天标「进行中」，过去的空日标「无采集记录」——两种文案，
   本节验的是后者。原判据会挑到今天，然后断言"不标进行中"必然失败。 */
const noDataDate = emptyDays.filter((d) => d !== ix.today).pop() || null;

/* 有多点准备度轨迹的日子（晨起之后又因运动重算过）。
   单点轨迹与主值重复，readinessTrail 按设计返回空串 → 整行隐藏（detail.js:184）。 */
const shardDays = store.shards[shardMonth] || {};
const trailDay = Object.keys(shardDays).sort().filter((d) => {
  const rd = ((shardDays[d] || {}).recovery || {}).readiness;
  return rd && (rd.series || []).length >= 2;
}).pop() || null;

console.log("== 数据层 ==");
ok(store.rows.length > 0, "索引行非空", store.rows.length + " 行");
ok(store.closed.length > 0, "完整日非空", store.closed.length + " 天");
ok(store.closed.every((r) => !r.morning), "完整日不含进行中的当天");
ok(ix.goals && ix.baselines && ix.today, "goals / baselines / today 已随索引下发");
ok(ix.baselines.rhr_ok_max != null, "静息心率达标上限已下发", "≤" + ix.baselines.rhr_ok_max + " bpm");

console.log("\n== 观测范围（尺度 -> 单元） ==");
const uDay = unitOf("2026-09-12", "day");
const uWeek = unitOf("2026-09-12", "week");
const uMonth = unitOf("2026-09-12", "month");
ok(uDay.dates.length === 1 && uDay.dates[0] === "2026-09-12", "日单元 = 1 天");
ok(uWeek.dates.length === 7 && uWeek.dates[0] === "2026-09-07" && uWeek.dates[6] === "2026-09-13", "周单元 = 周一至周日");
ok(uMonth.dates.length === 30 && uMonth.dates[0] === "2026-09-01", "月单元 = 整月", uMonth.dates.length + " 天");
ok(prevUnitOf("2026-09-12", "week").dates[0] === "2026-08-31", "上一周锚点正确");
ok(prevUnitOf("2026-09-12", "month").key === "2026-08", "上一月锚点正确");
const s12 = unitSeries("2026-09-12", "week", 12);
ok(s12.length === 12 && s12[11].key === uWeek.key && s12[0].key < s12[11].key, "周序列 12 个单元升序");

console.log("\n== 三尺度渲染（同一视图，换粒度） ==");
for (const scale of ["day", "week", "month"]) {
  store.scale = scale;
  /* 日尺度锚在最后一个完整日：ix.today 可能整日无数据（手表没同步），
     而无数据日按设计不渲染四项达标，用它当锚点测不出任何东西。
     周/月尺度同理——2026-11-01 是月初第一天，`ix.today` 落在 11 月，
     而 11 月一个数据日都没有，四项达标天然是 0 个 uc（不是缺陷）。
     基准必须落在**有数据的那个月**，否则验的是空月份的形状。 */
  store.anchor = scale === "day" ? closedLast
    : (scale === "month" ? closedLast.slice(0, 8) + "15" : ix.today);
  renderPage();
  const cal = html("axis-cal");
  const grid = html("metric-grid");
  const drillable = count(grid, /data-detail=/g);
  ok(count(cal, /cal-cell/g) % 7 === 0, `[${scale}] 日历渲染`, count(cal, /cal-cell/g) + " 格");
  ok(html("unit-scope").length > 0 && html("unit-score").length > 0, `[${scale}] 当前单元总览有内容`);
  ok(count(html("unit-checks"), /class="uc[ "]/g) === 4, `[${scale}] 四项达标计数`);
  const nCards = metrics(ix).length;
  ok(count(grid, /mx-card/g) === nCards, `[${scale}] 指标卡 ${nCards} 张`);
  ok(count(grid, /class="spark"/g) === nCards, `[${scale}] 每卡一条单元序列`);
  ok(scale === "day" ? !hidden("brief-zone") : hidden("brief-zone"),
    `[${scale}] 当日判读区 ${scale === "day" ? "显示" : "隐藏（它一天一份）"}`);
  ok(scale === "day" ? drillable > 0 : drillable === 0,
    `[${scale}] 卡内细分可展开数 ${drillable}（仅日尺度）`);
}

console.log("\n== 尺度真的改变口径（同一指标三尺度对比） ==");
const mSleep = metrics(ix).filter((m) => m.key === "sleep_h")[0];
for (const scale of ["day", "week", "month"]) {
  const u = unitOf(store.anchor, scale);
  const rows = rowsWith(unitRowsAll(u, store.byDate), mSleep);
  const v = metricValue(rows, mSleep);
  console.log(`  · ${scale}: 单元「${u.label}」 该指标有效 ${rows.length} 天 → 睡眠代表值 ${v == null ? "—" : v.toFixed(2) + "h"}`);
  ok(true, `[${scale}] 口径可算`);
}
const dVals = ["2026-09-11", "2026-09-10", "2026-09-09"].map((d) => store.byDate[d].sleep_h);
ok(new Set(dVals).size > 1, "日尺度逐日值互不相同（不是聚合值冒充）", dVals.join(" / "));

console.log("\n== 环比与达标由后端判定（S2 接线） ==");
ok(ix.units && ix.units.day && ix.units.week, "index.json 已下发 units（各尺度单元判定）",
  Object.keys(ix.units || {}).join("/"));
const mHrv = metrics(ix).filter((m) => m.key === "hrv")[0];
const cW43 = unitCell(ix, "week", "2026-W43", mHrv);
ok(cW43 && cW43.dv, "周单元带 delta_verdict", JSON.stringify(cW43));
store.scale = "week"; store.anchor = "2026-10-22"; renderPage();
const gridWeek = html("metric-grid");
ok(/在误差内/.test(gridWeek), "within-noise 的指标出「在误差内」而不是箭头百分比");
ok(!/dl (good|bad)/.test(gridWeek.split('data-metric="hrv"')[1] || ""),
  "within-noise 的 HRV 卡不画红绿箭头");
ok(/不做环比/.test(gridWeek), "tier.change=low 的指标（步数）写「不做环比」");
const mSteps = metrics(ix).filter((m) => m.key === "steps")[0];
const cW = unitCell(ix, "week", "2026-W43", mSteps);
ok(cW && cW.dv === "suppressed", "步数周环比被判 suppressed（6 天样本分不开 6400 与 10294）",
  JSON.stringify(cW));

/* 覆盖不足的文案分支：**不依赖真实数据里有没有截断日**。
   日尺度单元的 c 是「有值天数 / 应有天数」，完整日恒为 1.0，并不反映当天
   wellnessEndTime 的 cutoff——所以现实里可能整月都不出现 c < 1，靠「挑一天」
   来测这条永远不可靠（这正是原先写死 10-25 失效的原因）。
   改成在索引副本上把一个单元改成 na + c=0.5，直接验证渲染分支。 */
const ixPatched = JSON.parse(JSON.stringify(ix));
const patchCell = (((ixPatched.units || {}).day || {})[closedLast] || {}).sleep_h;
if (!patchCell) {
  console.log("  – 跳过：索引里没有可改造的日单元");
} else {
  patchCell.j = "na";
  patchCell.c = 0.5;
  const bakIx = store.ix;
  store.ix = ixPatched;
  store.scale = "day"; store.anchor = closedLast; renderPage();
  ok(/覆盖 50% · 不给结论/.test(html("metric-grid")),
    "覆盖不足的当日卡明说「覆盖 50% · 不给结论」，不给看起来确定的数字");
  store.ix = bakIx;
  renderPage();
}

store.scale = "month"; store.anchor = ix.today; renderPage();
ok(hidden("brief-zone"), "月尺度判读区仍隐藏");

console.log("\n== 滚动窗尺度（7/28/90 天） ==");
for (const scale of ["roll7", "roll28", "roll90"]) {
  store.scale = scale;
  store.anchor = ix.today;
  renderPage();
  const n = parseInt(scale.slice(4), 10);
  const u = unitOf(store.anchor, scale);
  ok(u.dates.length === n && u.dates[n - 1] === store.anchor,
    `[${scale}] 单元 = 锚点往前 ${n} 天`, u.label);
  ok(prevUnitOf(store.anchor, scale).key === unitOf(store.anchor, scale).key ? false : true,
    `[${scale}] 环比对象是非重叠的上一窗`);
  const g = html("metric-grid");
  ok(count(g, /mx-card/g) === metrics(ix).length, `[${scale}] 指标卡齐全`);
  ok(/近 \d+ 天/.test(nodes.get("axis-note").textContent), `[${scale}] 时间轴说明点明窗口长度`);
}

/* 2026-11-01：云服务副本整条链路已移除（数据只存本地 data/ 单根）。
   这里原本有 20+ 项测试：publicConfig 下发、vendor IIFE 初始化、比对逻辑
   （in-sync / mismatch / ahead）。全部随功能下线——留着一组测不存在能力的用例，
   只会让冒烟失败变成噪音，掩盖真正的回归。 */

console.log("\n== 卡内细分渲染器（喂完整分片） ==");
const day = store.shards[closedLast.slice(0, 7)][closedLast];
for (const key of Object.keys(DETAILS)) {
  let out = "", err = "";
  try { out = DETAILS[key](day); } catch (e) { err = e.message; }
  ok(!err && out.length > 0, `细分 ${key}`, err || out.length + " 字符");
}

console.log("\n== 准备度：进总览与综合指数并排，不再寄生判读区 ==");
ok(ix.days.some((r) => r.readiness != null), "索引行已带 readiness",
  ix.days.filter((r) => r.readiness != null).length + "/" + ix.days.length + " 天");
ok(ix.days.every((r) => r.readiness == null ||
  /^(POOR|LOW|MODERATE|HIGH|PRIME)$/.test(r.readiness_level)),
  "档位取值落在设备五档内");
const { readyOf } = await import(mod("js/constants.js"));
ok(readyOf("MODERATE", 74).label === "中" && readyOf("PRIME", 100).label === "极高",
  "设备档位原样映射（不再把 MODERATE 显示成「—」）");
ok(readyOf(null, 41).label === "低" && readyOf(null, 88).label === "高",
  "档位缺失时按分数兜底分档", "41→低 / 88→高");

/* 数字与颜色必须同源：74.5 显示成「75」却判琥珀，是当众打架（实测 9 月均准备度） */
store.scale = "month"; store.anchor = "2026-09-15"; renderPage();
const mReady = [...html("unit-score").matchAll(/<b style="color:(#[0-9A-Fa-f]{6})">(\d+)<\/b>/g)]
  .map((m) => ({ color: m[1], n: +m[2] }));
ok(mReady.length === 2, "总览两项分数都能取出数字与颜色", JSON.stringify(mReady));
/* 断言的**意图**是「显示值与配色同源」，不是记住 2026-09 的具体数值——
   写死 75 会在数据回补后变成伪阳性（2026-10-25 实测已变成 76）。
   所以现算月均，再要求显示值 = 四舍五入后的原始值、颜色按显示值判。 */
const sepRows = ix.days.filter((r) => r.date.slice(0, 7) === "2026-09" &&
  !r.morning && r.readiness != null).map((r) => r.readiness);
const sepAvg = sepRows.reduce((a, b) => a + b, 0) / sepRows.length;
const sepShow = Math.round(sepAvg);
ok(mReady[1].n === sepShow && mReady[1].color === readyOf(null, sepShow).color,
  "月均准备度显示值与配色同源（先四舍五入再分档）",
  `${sepRows.length} 天均值 ${sepAvg.toFixed(1)} → 显示 ${mReady[1].n} · ${mReady[1].color}`);

store.scale = "day"; store.anchor = closedLast; renderPage();
const rsDay = html("unit-score");
ok(count(rsDay, /class="uscore"/g) === 2, "总览两项分数并排（综合指数 / 准备度）");
ok(rsDay.includes("综合指数") && rsDay.includes("Garmin 准备度"), "两项各自具名，未合并");
ok(rsDay.includes("晨起"), "日尺度准备度标「晨起」");
/* 日内重算轨迹只存在于「晨起之后又被重算过」的日子——单点轨迹与主值重复，
   readinessTrail 返回空串、整行隐藏，那是设计（detail.js:184）而不是缺陷。
   所以不能随便挑一个完整日就要求它有轨迹，那取决于那天有没有运动后重算。 */
if (!trailDay) {
  console.log("  – 跳过：锚点月内没有多点准备度轨迹的日子（该行按设计隐藏）");
} else {
  store.anchor = trailDay; renderPage();
  ok(html("unit-ready-trail").length > 0 && !hidden("unit-ready-trail"),
    "多点轨迹的日子给出日内重算轨迹", trailDay);
  store.anchor = closedLast; renderPage();
}
ok(!nodes.has("readiness-banner"), "判读区不再承载准备度横幅（元素已不存在）");
ok(!html("brief-grid").includes("readiness-banner"), "判读正文里也没有准备度横幅");

store.scale = "week"; renderPage();
const rsWeek = html("unit-score");
ok(rsWeek.includes("日均"), "周尺度准备度改标「日均」");
ok(!/准备度 · (极高|高|中|低|差)/.test(rsWeek),
  "聚合尺度不编设备档位词（档位只挂在某一天上）");
ok(rsWeek !== rsDay, "准备度主值随尺度换口径（不是常数）");
ok(hidden("unit-ready-trail"), "非日尺度隐藏日内轨迹（它只存在于某一天）");
store.scale = "month"; renderPage();
ok(hidden("brief-zone"), "月尺度判读区仍隐藏");

console.log("\n== 进行中的当天（是部分指标缺，不是整行作废） ==");
// 进行中的当天 = 索引里 date === ix.today 的那一行。「进行中」这个标记只挂在今天，
// 所以锚点不能换成别的日子（换过去就没有"进行中"文案可验了）。
// 但今天可能整日无数据（手表没同步）——那是另一种空态，本段测不了，明确跳过。
const liveRow = store.rows.filter((r) => r.date === ix.today)[0];
if (!liveRow) {
  console.log(`  – 跳过：ix.today=${ix.today} 无数据行，最后一行 ${store.rows[store.rows.length - 1].date} 是完整日`);
} else if (isEmptyRow(liveRow)) {
  console.log(`  – 跳过：ix.today=${ix.today} 整日无数据（手表未同步）。` +
    "本段验的是「部分指标缺 ≠ 整行作废」，整日缺是另一种空态，走下面的「无数据日」用例");
} else {
  store.scale = "day";
  store.anchor = liveRow.date;
  renderPage();
  const liveGrid = html("metric-grid");
  ok(html("unit-scope").includes("进行中"), "明确标注进行中",
    html("unit-scope").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim());
  ok(/[0-9]+\.[0-9]h/.test(liveGrid), "昨夜睡眠仍给出实测值（不被整行排除）");
  ok(count(liveGrid, /mx-card/g) === metrics(ix).length, "仍渲染全部指标卡");
  const liveDay = store.byDate[store.anchor];
  ok(liveDay.steps == null, "当日步数本就为 null（未结束），不进步数分母");
}
store.anchor = closedLast;

console.log("\n== 空月份（无数据） ==");
store.scale = "month";
store.month = "2020-01";
renderPage();
ok(count(html("axis-cal"), /cal-cell/g) > 0, "空月份仍渲染出日历骨架");
store.month = ix.default_day.slice(0, 7);

/* ---------------- 无数据日：必须给明确空态，且绝不能自旋 ----------------
   2026-09-16 线上事故：切到日尺度、或点日历上任何没有记录的一格（今天 / 月初 / 跨月空格），
   整个页面冻死。根因是 renderBrief 在「分片已加载、但没有这一天」时会异步重试自己——
   ensureShard 命中缓存立刻 resolve → .then 里又调回 renderBrief → 微任务自旋，永不返回。
   「缓存里没有」与「还没加载」必须分开判断（hasShard）。
   本节**全部是同步断言**：真回归时渲染根本填不上内容，断言先失败，
   随后同步 process.exit 立刻结束进程，测试不会跟着一起挂死。 */
console.log("\n== 无数据日：明确空态，绝不自旋 ==");
/* 判据是「索引里有这一行、但值全空」，不是「索引里没有这一天」——
   主页跨度含断档空白格，缺采集的日子照样在索引里占一行。原判据
   （`monthDays.filter(d => !withRow.has(d))`）在新数据下永远找不到目标，
   而且会把 undefined 传给 renderPage，直接崩在 monthLabel 上。 */
const withRow = new Set(ix.days.map((r) => r.date));
if (!noDataDate) {
  console.log("  – 跳过：锚点月内没有「有行但无实测值」的日子");
} else {
  store.scale = "day";
  store.anchor = noDataDate;
  store.month = anchorMonth;
  renderPage();
  ok(text("brief-sub").includes("没有采集记录"),
    "无数据日给同步空态（是空态，不是「加载中」转圈）",
    noDataDate + " · " + text("brief-sub").trim());
  ok(html("brief-grid").includes("缺数据不等于没做到"), "空态文案守住「缺数据 ≠ 没做到」");
  ok(html("unit-scope").includes("无采集记录"), "过去的空日标「无采集记录」",
    html("unit-scope").replace(/<[^>]+>/g, " ").trim());
  ok(!html("unit-scope").includes("进行中"), "过去的空日不标「进行中」（那是今天的说法）");
  ok(!hidden("brief-zone"), "日尺度下判读区仍显示（只是内容是空态）");
}

/* 没有记录的「今天」——这才是唯一该说「进行中」的情形 */
if (!withRow.has(ix.today)) {
  store.anchor = ix.today;
  renderPage();
  ok(html("unit-scope").includes("进行中"), "今天无数据时标「进行中 · 当日未结束」");
  ok(text("brief-sub").includes("没有采集记录"), "今天同样给空态，不转圈");
} else {
  console.log("  – 跳过：今天已有数据行");
}
store.anchor = closedLast;
store.scale = "month";

/* ================= 采集健康度（2026-11-01） =================
   这一节验的不是「画得好看」，是**归因没被混淆**：
   采集失败 / 原始数据没落盘 / 设备没推数，三者必须在数据与 DOM 上都分得开。
   以前它们在前端塌成同一个空心灰点，于是「采集挂了」被读成「我没戴表」。 */
console.log("\n== 采集健康度：三种「没数」必须分得开 ==");
const col = ix.collect || {};
const csSum = col.summary || {};
ok(!!ix.collect, "index.json 下发 collect 块");
ok(csSum.n === ix.days.length, "采集汇总覆盖全部索引行", `${csSum.n}/${ix.days.length}`);
ok(ix.days.every((r) => !!r.cs), "每一行都有 cs 采集状态",
  [...new Set(ix.days.map((r) => r.cs))].join("/"));
ok((csSum.ok || 0) + (csSum.failed || 0) + (csSum.missing || 0) +
   (csSum.absent || 0) + (csSum.pending || 0) === csSum.n,
  "四态之和 = 总行数（无漏判状态）");
/* 失败日必须带端点名，否则告警条只能说「失败了」却说不出哪个端点 */
const failedRow = ix.days.filter((r) => r.cs === "failed")[0];
ok(!failedRow || (failedRow.ce && failedRow.ce.length > 0),
  "failed 行带失败端点名", failedRow ? failedRow.ce.join(",") : "无 failed 样本");
ok(!ix.days.filter((r) => r.cs !== "failed").some((r) => r.ce),
  "非 failed 行不下发端点名（不制造噪声）");

/* 日历：状态必须落到 DOM，不能只停在数据层 */
store.scale = "month";
store.anchor = closedLast.slice(0, 8) + "15";
renderPage();
const calHtml = html("axis-cal");
ok(/data-cs="ok"/.test(calHtml), "日历格带 data-cs 状态");
if (csSum.failed > 0) {
  ok(/cal-cell[^"]*cs-failed/.test(calHtml), "采集失败日渲染 cs-failed 类");
} else {
  console.log("  – 跳过：跨度内没有采集失败日");
}
if (csSum.missing > 0) {
  ok(/cal-cell[^"]*cs-missing/.test(calHtml), "无原始数据日渲染 cs-missing 类（不是普通灰）");
} else {
  console.log("  – 跳过：跨度内没有 missing 日");
}
/* title 是唯一能说清「为什么这天是空的」的地方，必须带原因 */
if (csSum.failed > 0) {
  ok(/title="[^"]*采集失败/.test(calHtml), "失败格的 title 写明「采集失败」");
}
if (csSum.missing > 0) {
  ok(/title="[^"]*无原始数据/.test(calHtml), "缺失格的 title 写明「采集未运行」");
}

/* 胶囊：不能说「N 个数据日」——那是把空壳算成有数据 */
const pill = syncText(ix, "endpoint");
ok(/\d+\/\d+ 天有实测/.test(pill.text), "胶囊按「有实测天数/跨度」报数", pill.text);
ok(!/个数据日/.test(pill.text), "胶囊不再用「N 个数据日」这种含混口径");
ok(pill.warn === ((csSum.failed || 0) + (csSum.missing || 0) > 0),
  "有未采到的日子时胶囊转警示态", "warn=" + pill.warn);
ok(syncText(ix, "bundled").text.includes("静态副本"), "走静态副本时胶囊明说（不冒充已同步）");

/* 告警条：只在管线出过问题时出现，且必须给出可定位的信息 */
renderCollectBar(col, csSum);
const bar = document.getElementById("collect-bar");
if (csSum.failed > 0 || csSum.missing > 0) {
  ok(!bar.hidden && bar.innerHTML.length > 0, "有采集故障时显示告警条");
  ok(/不是「没戴表」/.test(bar.innerHTML), "告警条点明「不是没戴表」（纠正归因）");
  if (csSum.missing > 0) {
    ok((col.missing_ranges || []).length > 0, "缺失按区间聚合，不是逐日罗列",
      (col.missing_ranges || []).join("、"));
  }
} else {
  console.log("  – 跳过：没有采集故障，告警条应隐藏");
}

/* ================= 主观补数提示（2026-11-01） ================= */
console.log("\n== 主观补数提示 ==");
const nud = ix.nudge;
if (nud && nud.missing && nud.missing.length) {
  ok(true, "index.json 下发 nudge 缺口", nud.labels + " · 断 " + nud.gap_days + " 天");
  ok(typeof nud.prompt === "string" && nud.prompt.length > 0, "带一句话补数模板");
  store.ix = ix;
  renderNudge();
  ok(!document.getElementById("nudge-bar").hidden, "待补提示可见");
  ok(/还缺/.test(html("nudge-bar")), "待补条说明缺哪几项");
  ok(/补数：/.test(html("nudge-bar")), "待补条给出可复制的回复模板");
  if (nud.gap_days > 1) {
    ok(/已断 \d+ 天/.test(html("nudge-bar")), "断档天数写出来（缺一天是疏忽，断几十天是链路断了）");
  }
} else {
  console.log("  – 跳过：今天没有待补项（三项都答了）");
}

/* ================= 判读可达性 + 分组 + 矩阵（2026-11-01） ================= */
console.log("\n== 判读历史可达 ==");
const briefs = ix.briefs || {};
const evs = ix.events || {};
const briefDays = Object.keys(briefs);
ok(briefDays.length > 0, "index.json 下发判读流水（briefs）", briefDays.length + " 天");
ok(briefDays.every((d) => briefs[d] && briefs[d].head && briefs[d].head.length > 8),
  "每条流水都有结论首句");
ok(briefDays.every((d) => briefs[d].head.length <= 90),
  "首句有长度上限（列表里一行就是一行）",
  "最长 " + Math.max(...briefDays.map((d) => briefs[d].head.length)));
/* 有判读的日子必须在日历上看得出来，否则 37 条结论「藏在页脚」等于没有 */
ok(ix.days.filter((r) => r.has_brief && r.cs !== "failed").length >= briefDays.length - 5,
  "has_brief 与 briefs 基本对齐",
  ix.days.filter((r) => r.has_brief).length + " / " + briefDays.length);
renderBriefLog();
ok(!document.getElementById("brief-log-zone").hidden, "判读流水区可见");
ok(document.getElementById("bl-list").innerHTML.split("bl-i").length - 1 === briefDays.length,
  "流水条数 = 判读天数",
  String(document.getElementById("bl-list").innerHTML.split("bl-i").length - 1));
ok(/data-date="\d{4}-\d{2}-\d{2}"/.test(document.getElementById("bl-list").innerHTML),
  "每条流水带日期（可点击跳转）");

console.log("\n== 当日背景事件 ==");
ok(Object.keys(evs).length > 0, "index.json 下发事件流", Object.keys(evs).length + " 天");
const evDay = Object.keys(evs).sort().pop();
store.scale = "day";
store.anchor = evDay;
store.month = evDay.slice(0, 7);
renderPage();
ok(!document.getElementById("day-events").hidden, "有事件的日期显示背景事件区", evDay);
ok(document.getElementById("day-events").innerHTML.includes("当日背景事件"), "事件区带标题");
store.anchor = closedLast.slice(0, 7) + "-15";
store.scale = "month";
renderPage();

console.log("\n== 指标分组 ==");
const gridHtml = html("metric-grid");
ok((gridHtml.match(/class="mx-group"/g) || []).length >= 3, "指标按恢复/行为/负荷分组",
  String((gridHtml.match(/class="mx-group"/g) || []).length) + " 组");
ok(count(gridHtml, /mx-card/g) === metrics(ix).length, "分组后卡片总数不变（一张都没丢）",
  count(gridHtml, /mx-card/g) + "/" + metrics(ix).length);
ok(/data-group="recover"/.test(gridHtml) && /data-group="behave"/.test(gridHtml),
  "恢复组与行为组都在");

console.log("\n== 逐日达标矩阵 ==");
const matHtml = html("day-matrix");
ok(!document.getElementById("day-matrix").hidden, "矩阵可见");
ok(count(matHtml, /dm-c /g) > 40, "矩阵画出逐日格子", count(matHtml, /dm-c /g) + " 格");
ok(/dm-c na/.test(matHtml), "无判定格与未达标格分开画（na 不等于 no）");
ok(/dm-s/.test(matHtml), "每行给达标比");

console.log("\n" + "=".repeat(46));
console.log(failed === 0 ? `全部通过：${passed} 项` : `${passed} 通过 / ${failed} 失败`);
process.exit(failed === 0 ? 0 : 1);
