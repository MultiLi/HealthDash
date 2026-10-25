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
const { renderPage } = await import(mod("js/metricgrid.js"));
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
store.anchor = ix.today || ix.default_day;
store.month = store.anchor.slice(0, 7);
store.scale = "month";
const shardMonth = store.anchor.slice(0, 7);
store.shards[shardMonth] = JSON.parse(fs.readFileSync(path.join(SITE, "daily", shardMonth + ".json"), "utf8")).days;

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

store.scale = "day"; store.anchor = "2026-10-25"; renderPage();
const gridToday = html("metric-grid");
ok(/覆盖 4[0-9]% · 不给结论|不给结论/.test(gridToday),
  "覆盖不足的当日卡明确「不给结论」，而不是给个看起来很确定的数字");
ok(!/class="uc"/.test(html("unit-checks")) || /uc-na/.test(html("unit-checks")),
  "四项达标里进行中的当天显示「–」而不是 0/0");

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

console.log("\n== 云服务（读侧镜像 + 一致性校验） ==");
ok(!!(ix.cloud && ix.cloud.endpoint && ix.cloud.publishableKey),
  "index.json 下发了云服务 publicConfig（endpoint/publishableKey 不硬编码在代码里）",
  ix.cloud ? ix.cloud.endpoint : "缺失");
/* 真的把 vendor 的 IIFE 加载进全局，验证「初始化契约」而不只是文件存在。
   indirect eval 才会写到全局作用域（直接 eval 的 var 只在局部）。 */
const sdkPath = path.join(SITE, "vendor", "workbuddy-cloud.global.js");
const sdkSrc = fs.readFileSync(sdkPath, "utf8");
(0, eval)(sdkSrc);
ok(!!globalThis.WorkBuddyCloud && !!WorkBuddyCloud.createWorkBuddyCloud,
  "vendor 的 IIFE 暴露全局 WorkBuddyCloud（Form B）", sdkSrc.length + " 字节");
/* SDK 在初始化时校验运行时能力（fetch 必须存在）——前面测取数链路时把 fetch 删掉了，
   这里临时装回一个桩，只为验证初始化契约，不发真请求。 */
globalThis.fetch = function () {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
};
const probeClient = WorkBuddyCloud.createWorkBuddyCloud({
  endpoint: ix.cloud.endpoint,
  oauthRelayBaseUrl: ix.cloud.oauthRelayBaseUrl,
  publishableKey: ix.cloud.publishableKey
});
ok(!!probeClient.database && typeof probeClient.database.from === "function",
  "用 publicConfig 初始化成功，database 模块可用（与 cloud.js 的参数一致）");
ok(["auth", "database", "storage", "llm"].every((m) => !!probeClient[m]),
  "四个模块都挂在同一个客户端上（一次初始化，不用二次 createClient）");
delete globalThis.fetch;

const cloudMod = await import(mod("js/cloud.js"));
ok(cloudMod.statusText({ status: "off" }) === " · 云端未配置", "未配置时如实标注，不写成已同步");
ok(cloudMod.statusText({ status: "unavailable" }) === " · 云端不可用", "读取失败标不可用");

/* 用真实索引行构造一份「云端快照」，验证比对逻辑（不联网） */
const cloudRows = ix.days.slice(-7).map((r) => Object.assign({}, r));
const snapSame = { rows: cloudRows, generated_at: ix.generated_at };
let cs = cloudMod.compare(ix, snapSame);
ok(cs.status === "in-sync" && cs.n === 7, "云端与本地逐字段一致 → in-sync", JSON.stringify(cs));
ok(/云端 7 天一致/.test(cloudMod.statusText(cs)), "一致时给出天数");

/* jsonb 会重排键顺序：整串比会假阳性，所以按字段比 */
const reordered = cloudRows.map((r) => {
  const o = {};
  Object.keys(r).reverse().forEach((k) => { o[k] = r[k]; });
  return o;
});
cs = cloudMod.compare(ix, { rows: reordered });
ok(cs.status === "in-sync", "键顺序不同不算不一致（jsonb 会重排）");

const tampered = cloudRows.map((r) => Object.assign({}, r));
tampered[tampered.length - 1].hrv = 999;
cs = cloudMod.compare(ix, { rows: tampered });
ok(cs.status === "mismatch" && cs.n_diff === 1, "字段被改 → mismatch 且定位到日期",
  JSON.stringify(cs.diffs));
ok(/云端 1 天不一致/.test(cloudMod.statusText(cs)), "不一致时说清天数");

cs = cloudMod.compare(ix, { rows: cloudRows.concat([{ date: "2026-10-30", hrv: 60 }]) });
ok(cs.status === "ahead" && cs.cloud_only.length === 1, "云端多出的日期单独标 ahead");

cs = cloudMod.compare(ix, null);
ok(cs.status === "off", "没有云端快照时不假装校验过");

console.log("\n== 卡内细分渲染器（喂完整分片） ==");
const closedLast = store.closed[store.closed.length - 1].date;
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
ok(html("unit-ready-trail").length > 0 && !hidden("unit-ready-trail"),
  "日尺度给出准备度日内重算轨迹");
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
// 进行中的当天 = 索引里 date === ix.today 的那一行。深夜构建时当天可能尚无任何数据
// （手表未同步 / 晨间任务未跑），最后一行就是完整日——此时不能按「最后一行＝进行中」断言。
const liveRow = store.rows.filter((r) => r.date === ix.today)[0];
if (!liveRow) {
  console.log(`  – 跳过：ix.today=${ix.today} 无数据行，最后一行 ${store.rows[store.rows.length - 1].date} 是完整日`);
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
const anchorMonth = ix.default_day.slice(0, 7);
const withRow = new Set(ix.days.map((r) => r.date));
const monthDays = (() => {
  const [y, m] = anchorMonth.split("-").map(Number);
  const n = new Date(y, m, 0).getDate();
  return Array.from({ length: n }, (_, i) => `${anchorMonth}-${String(i + 1).padStart(2, "0")}`);
})();
const noDataDate = monthDays.filter((d) => !withRow.has(d))[0];
ok(!!noDataDate, "锚点月内存在无数据的一天", noDataDate || "（该月已填满）");

store.scale = "day";
store.anchor = noDataDate;
store.month = anchorMonth;
renderPage();
ok(text("brief-sub").includes("没有采集记录"),
  "无数据日给同步空态（是空态，不是「加载中」转圈）",
  text("brief-sub").trim());
ok(html("brief-grid").includes("缺数据不等于没做到"), "空态文案守住「缺数据 ≠ 没做到」");
ok(html("unit-scope").includes("无采集记录"), "过去的空日标「无采集记录」",
  html("unit-scope").replace(/<[^>]+>/g, " ").trim());
ok(!html("unit-scope").includes("进行中"), "过去的空日不标「进行中」（那是今天的说法）");
ok(!hidden("brief-zone"), "日尺度下判读区仍显示（只是内容是空态）");

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

console.log("\n" + "=".repeat(46));
console.log(failed === 0 ? `全部通过：${passed} 项` : `${passed} 通过 / ${failed} 失败`);
process.exit(failed === 0 ? 0 : 1);
