/* 主页渲染。**只有一个视图**——因为「概览」与「某日」本来就是同一个东西
   在月尺度与日尺度下的样子。尺度一变，时间轴选中的范围、每张卡的主值口径、
   序列的 x 轴粒度、达标统计的分母，全部一起变。这就是多尺度作为「形式」
   而不是「入口」的意思。
 *
 * 页面自下而上三个层次，每一层都服从全局尺度：
 *   ① 时间轴（同一个日历，尺度决定它选中一格 / 一行 / 整月）
 *   ② 当前单元总览（综合指数 + Garmin 准备度 并排 + 四项达标计数）
 *   ③ 指标网格（8 张同构卡：主值 + 环比 + 单元序列 + 达标统计；日尺度下可展开当日细分）
 */
import { store } from "./store.js?v=a4398e13";
import { METRICS, CHECKS, bandOf, BAND, READY, readyOf } from "./constants.js?v=a4398e13";
import { esc, $ } from "./utils.js?v=a4398e13";
import { metricValue, seriesPoints, judgeValue, delta, unitRowsAll, rowsWith } from "./aggregate.js?v=a4398e13";
import { unitOf, prevUnitOf, unitSeries, scaleDef } from "./scope.js?v=a4398e13";
import { renderMonth, renderHeatstrip } from "./calendar.js?v=a4398e13";
import { sparkline } from "./charts.js?v=a4398e13";
import { DETAILS, readinessTrail, briefHtml } from "./detail.js?v=a4398e13";
import { ensureShard, dayOf, hasShard } from "./data.js?v=a4398e13";
import { monthLabel, fmtMD, weekdayCN } from "./dateutil.js?v=a4398e13";

function ix() { return store.ix; }

/* 口径文案：同一个指标在三种尺度下叫什么，用户必须一眼看懂 */
function unitNoun(scale) { return { day: "当日", week: "本周", month: "本月" }[scale]; }
function aggNoun(scale, m) {
  if (scale === "day") return "当日值";
  return unitNoun(scale) + (m.agg === "median" ? "中位" : "日均");
}

/* ================= ① 时间轴 ================= */
function renderAxis(unit) {
  $("axis-month-label").textContent = monthLabel(store.month);
  document.querySelectorAll("[data-scale]").forEach(function (b) {
    b.classList.toggle("active", b.getAttribute("data-scale") === store.scale);
  });

  /* 月尺度下整月都是范围，逐格高亮没有信息量，故不高亮 */
  var scopeDates = store.scale === "month" ? null : unit.dates;
  var ctx = {
    ix: ix(), byDate: store.byDate, today: ix().today,
    scale: store.scale, anchor: store.anchor, scopeDates: scopeDates
  };
  var html = renderMonth(store.month, ctx);
  if (store.rows.length >= 60) {
    html += '<div class="heat-wrap"><p class="heat-title">长周期热力 · 全部数据，一格一天，颜色为综合指数分档</p>' +
      renderHeatstrip(store.rows, ctx) + "</div>";
  }
  $("axis-cal").innerHTML = html;

  var hint = {
    day: "日尺度：点日历任意一格即切换日期；每张卡显示该日的值与该日所在周的逐日序列。",
    week: "周尺度：点任意一格即选中它所在的整周（高亮整行）；每张卡显示该周日均与近 12 周的走向。",
    month: "月尺度：整个月就是观测范围；每张卡显示月日均与近 12 个月的走向。点日历某天可直接下钻到日尺度看那天。"
  }[store.scale];
  $("axis-note").textContent = hint;
}

/* ================= ② 当前单元总览 ================= */
/* 综合指数不混口径：晨间快照是四项合成、完整日是五项合成，
   两者平均到一起没有意义，所以指数只用完整日。 */
var SCORE_M = {
  key: "score", agg: "mean", dir: "above",
  fmt: function (v) { return String(Math.round(v)); },
  goal: function (i) { return i.baselines.score.good_min; }
};

function scoreRows(unit) {
  return unitRowsAll(unit, store.byDate).filter(function (r) { return !r.morning && r.score != null; });
}

/* 四项达标各自用自己的分母：该指标在该单元内有实测值的天数。
   进行中的今天仍有完整睡眠/HRV，会计入；它缺的步数/久坐自然不计入。 */
var CHECK_FIELD = { sleep: "sleep_h", steps: "steps", hrv: "hrv", sedentary: "sedentary_min" };

/* Garmin 准备度：与综合指数并排的第二项分数。单元粒度与其他指标一致——
   日尺度＝当天晨起值，周/月尺度＝日均值（aggregate 的 mean）。 */
var READY_M = { key: "readiness", agg: "mean" };

/* 一项分数的排版单元：大数字 + 名称 + 环比。两项口径不同，故各自带标签与环比，
   绝不合并成一个数——合并等于让「行为达标度」替「恢复准备度」回答。 */
function uscore(value, color, label, chip) {
  return '<div class="uscore">' +
    '<b style="color:' + color + '">' + esc(value) + "</b>" +
    '<span class="uscore-sub"><span class="uscore-lab">' + label + "</span>" + (chip || "") + "</span>" +
    "</div>";
}

function renderOverview(unit) {
  var prev = prevUnitOf(store.anchor, store.scale);
  var score = metricValue(scoreRows(unit), SCORE_M);
  var prevScore = metricValue(scoreRows(prev), SCORE_M);
  /* 先四舍五入到展示值再判分档：否则 74.5 会显示成「75」却按 74.5 判成琥珀，
     数字与颜色当众打架（2026-09-16 实测 9 月均准备度 74.5 → 显示 75 配琥珀）。 */
  var scoreV = score == null ? null : Math.round(score);
  var band = bandOf(scoreV, ix().baselines);
  var bandName = { good: "良好", fair: "一般", low: "待注意", none: "无数据" }[band];

  var closedN = scoreRows(unit).length;
  /* 「进行中 · 当日未结束」只对**今天**成立。过去的空日没有记录就是没有记录，
     标成「进行中」等于暗示它还会长出数据来（2026-09-16 实测：08-31 / 09-01 都被这么错过）。 */
  var note;
  if (store.scale !== "day") {
    note = "完整日 " + closedN + " 天";
  } else if (closedN) {
    note = "完整日";
  } else if (store.anchor === ix().today) {
    note = "进行中 · 当日未结束";
  } else {
    note = "无采集记录";
  }
  $("unit-scope").innerHTML =
    "<b>" + esc(unit.label) + "</b><span>" + esc(unit.sub) + " · " + esc(note) + "</span>";

  var ready = metricValue(rowsWith(unitRowsAll(unit, store.byDate), READY_M), READY_M);
  var prevReady = metricValue(rowsWith(unitRowsAll(prev, store.byDate), READY_M), READY_M);
  /* 设备档位只挂在「某一天」上。周/月是聚合值，没有单一档位可挂——
     此时**不编档位词**，只给数字与颜色，免得让「高」看起来像设备对整周下的判断。 */
  var rIsDay = store.scale === "day";
  var rLevel = rIsDay && store.byDate[store.anchor] ? store.byDate[store.anchor].readiness_level : null;
  var rBand = rLevel && READY[rLevel] ? READY[rLevel] : null;
  var readyV = ready == null ? null : Math.round(ready);
  /* 聚合尺度没有设备档位，颜色按**展示值**分档，与数字保持一致 */
  var rColor = rBand ? rBand.color : readyOf(null, readyV).color;
  var rLabel = "Garmin 准备度" + (rBand ? " · " + esc(rBand.label) : "") +
    "<em>（" + (rIsDay ? "晨起" : "日均") + "）</em>";

  $("unit-score").innerHTML =
    uscore(scoreV == null ? "–" : scoreV, BAND[band],
      "综合指数 · " + bandName,
      deltaChip(delta(score, prevScore), "up", prev.label)) +
    uscore(readyV == null ? "–" : readyV, rColor, rLabel,
      deltaChip(delta(ready, prevReady), "up", prev.label));

  renderReadyTrail();

  $("unit-checks").innerHTML = CHECKS.map(function (c) {
    var rows = rowsWith(unitRowsAll(unit, store.byDate), { key: CHECK_FIELD[c.key] });
    var n = rows.filter(function (r) { return checkOne(r, c.key); }).length;
    var tot = rows.length;
    var pct = tot ? Math.round(n / tot * 100) : 0;
    return '<div class="uc"><span class="uc-k">' + esc(c.label) + '</span>' +
      '<span class="uc-v">' + n + '<i>/' + tot + '</i></span>' +
      '<span class="uc-bar"><i style="width:' + pct + '%"></i></span></div>';
  }).join("");
}

/* 准备度的日内重算轨迹：只存在于日分片，故日尺度下按需拉取。
   单点轨迹与主值重复，readinessTrail 会返回空串，此处即隐藏整行。 */
function renderReadyTrail() {
  var box = $("unit-ready-trail");
  if (store.scale !== "day") { box.hidden = true; box.innerHTML = ""; return; }
  var anchor = store.anchor;
  var fill = function () {
    /* 拉分片是异步的，锚点可能已经翻页——只认发起时的那一天 */
    if (store.anchor !== anchor) return;
    box.innerHTML = readinessTrail(dayOf(anchor));
    box.hidden = !box.innerHTML;
  };
  if (dayOf(anchor)) { fill(); return; }
  box.hidden = true;
  ensureShard(anchor).then(fill).catch(function () { box.innerHTML = ""; });
}

function checkOne(r, key) {
  var g = ix().goals;
  if (key === "sleep") return r.sleep_h != null && r.sleep_h >= g.sleep_h;
  if (key === "steps") return r.steps != null && r.steps >= g.steps;
  if (key === "hrv") return r.hrv_ok === true;
  return r.sedentary_min != null && r.sedentary_min <= g.sedentary_min;
}

function deltaChip(d, better, prevLabel) {
  if (!d || d.pct == null) return '<span class="dl flat">无环比基数</span>';
  if (d.dir === "flat") return '<span class="dl flat">与' + esc(prevLabel) + "持平</span>";
  var good = d.dir === better;
  return '<span class="dl ' + (good ? "good" : "bad") + '">' + (d.delta > 0 ? "+" : "") + d.pct +
    "% vs " + esc(prevLabel) + "</span>";
}

/* ================= ③ 指标网格 ================= */
function cardHtml(m, unit, prevUnit) {
  var usable = rowsWith(unitRowsAll(unit, store.byDate), m);
  var val = metricValue(usable, m);
  var prevUsable = rowsWith(unitRowsAll(prevUnit, store.byDate), m);
  var prevVal = metricValue(prevUsable, m);
  var ok = judgeValue(val, m, ix());
  var color = m.dir == null ? "var(--text)" : ok === null ? "var(--text-3)" : ok ? "var(--green-deep)" : "var(--amber-deep)";

  var pts = seriesPoints(unitSeries(store.anchor, store.scale, scaleDef(store.scale).seriesN), store.byDate, m, ix());

  /* 达标统计只认该指标有实测的天数；dir=null 的指标不判达标 */
  var okCount = null, tot = usable.length;
  if (m.dir && tot) {
    okCount = usable.filter(function (r) { return judgeValue(r[m.key], m, ix()) === true; }).length;
  }

  var canDrill = store.scale === "day" && m.detail && DETAILS[m.detail];
  return '<article class="mx-card' + (ok === false ? " is-warn" : "") + '" data-metric="' + m.key + '">' +
    '<div class="mx-head">' +
      '<div class="mx-title"><h3>' + esc(m.label) + '</h3><p class="mx-goal">' + esc(m.goalText(ix())) + "</p></div>" +
      '<div class="mx-value"><b style="color:' + color + '">' + esc(m.fmt(val)) + "</b></div>" +
    "</div>" +
    '<div class="mx-meta">' + esc(aggNoun(store.scale, m)) +
      (deltaChip(delta(val, prevVal), m.dir === "below" ? "down" : "up", prevUnit.label)) + "</div>" +
    '<div class="mx-chart">' + sparkline(pts, m, ix(), {}) + "</div>" +
    '<div class="mx-foot">' +
      (okCount == null
        ? '<span class="mx-stat">该指标无独立达标线</span>'
        : '<span class="mx-stat">达标 <b>' + okCount + "</b>/" + tot + " " +
          (store.scale === "day" ? "（当日）" : "天") + "</span>") +
      (canDrill ? '<button class="mx-more" type="button" data-detail="' + esc(m.detail) + '"><span>展开当日细分</span>' + caret() + "</button>" : "") +
    "</div>" +
    (canDrill ? '<div class="mx-detail" hidden></div>' : "") +
    "</article>";
}

function caret() {
  return '<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M3 5.5L7 9.5L11 5.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
}

function renderGrid(unit) {
  var prev = prevUnitOf(store.anchor, store.scale);
  $("metric-grid").innerHTML = METRICS.map(function (m) { return cardHtml(m, unit, prev); }).join("");
}

/* ================= ④ 当日判读（仅日尺度）=================
   这一区**只放结论**：交叉判读 + 分组判点 + 数据缺口。
   任何有量化指标的实测值（如准备度）归总览或指标网格——判读区一旦夹带
   原始设备值，「交叉判读先行」这句副标题就会被自己的版式推翻。 */
function fillBrief(d) {
  var b = briefHtml(d);
  $("brief-grid").innerHTML = b.html;
  $("brief-sub").textContent = b.sub;
  $("gaps").textContent = b.gaps;
}

/* 该日没有采集记录。**必须是明确空态，不能是「加载中」**：
   转圈会让人以为再等等就有，而这天根本不会有数据。
   「缺数据 ≠ 没做到」——日历上它是空心灰，这里也不能暗示那天没做。 */
function fillBriefEmpty(anchor) {
  $("brief-sub").textContent = fmtMD(anchor) + " " + weekdayCN(anchor) + " · 该日没有采集记录";
  $("brief-grid").innerHTML =
    '<p class="section-empty">这一天没有 Garmin 记录，因此也没有当日判读。' +
    "可能是还没到（当天数据要等当晚 23:30 出终版），也可能是当天没戴表或没同步——" +
    "日历上它是空心灰：<b>缺数据不等于没做到</b>。</p>";
  $("gaps").textContent = "";
}

export function renderBrief() {
  var zone = $("brief-zone");
  if (store.scale !== "day") { zone.hidden = true; return; }
  zone.hidden = false;

  var anchor = store.anchor;
  var d = dayOf(anchor);
  if (d) { fillBrief(d); return; }

  /* 分片已加载、却仍然没有这一天 = 该日无记录，不是「还没加载」。
     这一句是**防死循环的闸门**：少了它，下面的 ensureShard 会立刻 resolve，
     .then 里又调回 renderBrief，形成微任务自旋，整个标签页彻底冻死
     （2026-09-16 实测：切到「日」尺度、或点日历上任何无数据的一格，必然卡死）。 */
  if (hasShard(anchor)) { fillBriefEmpty(anchor); return; }

  $("brief-sub").textContent = "正在读取 " + anchor + " 的当月详情分片…";
  $("brief-grid").innerHTML = "";
  $("gaps").textContent = "";
  ensureShard(anchor).then(function () {
    /* 拉分片是异步的，锚点可能已经翻页——只认发起时的那一天（同 renderReadyTrail） */
    if (store.anchor !== anchor) return;
    var dd = dayOf(anchor);
    if (dd) fillBrief(dd); else fillBriefEmpty(anchor);
  }).catch(function () {
    if (store.anchor !== anchor) return;
    $("brief-sub").textContent = "详情分片加载失败（" + anchor.slice(0, 7) + "）。";
    $("brief-grid").innerHTML = "";
    $("gaps").textContent = "";
  });
}

/* ================= 装配 ================= */
export function renderPage() {
  var unit = unitOf(store.anchor, store.scale);
  renderAxis(unit);
  renderOverview(unit);
  renderGrid(unit);
  renderBrief();
}

/* 卡内细分展开：日尺度专属，按需拉分片 */
export function setupDetailToggle() {
  document.addEventListener("click", function (e) {
    if (!e.target.closest) return;
    var btn = e.target.closest("[data-detail]");
    if (!btn) return;
    e.preventDefault();
    var card = btn.closest(".mx-card");
    var box = card && card.querySelector(".mx-detail");
    if (!box) return;
    var label = btn.querySelector("span");

    if (!box.hidden) {
      box.hidden = true;
      btn.classList.remove("open");
      label.textContent = "展开当日细分";
      return;
    }
    var render = DETAILS[btn.getAttribute("data-detail")];
    var fill = function (day) {
      /* day 为 null = 该日无记录。绝不能把 null 塞进 render（会抛错，
         再被 .catch 误报成「加载失败」——明明只是这天没有数据）。 */
      var out = (day && render) ? render(day) : "";
      box.innerHTML = out || '<p class="mini-note">该日没有这一项的细分记录。</p>';
      box.hidden = false;
      btn.classList.add("open");
      label.textContent = "收起细分";
    };
    var anchor = store.anchor;
    var d = dayOf(anchor);
    if (d) { fill(d); return; }
    btn.disabled = true;
    ensureShard(anchor)
      .then(function () {
        btn.disabled = false;
        if (store.anchor !== anchor) return;   /* 锚点已翻页，丢弃 */
        fill(dayOf(anchor));
      })
      .catch(function () {
        btn.disabled = false;
        if (store.anchor !== anchor) return;
        box.innerHTML = '<p class="mini-note">该日详情加载失败。</p>';
        box.hidden = false;
      });
  });
}
