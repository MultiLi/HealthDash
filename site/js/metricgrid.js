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
import { store } from "./store.js?v=2f374a58";
import { metrics, CHECKS, bandOf, BAND, READY, readyOf } from "./constants.js?v=2f374a58";
import { esc, $ } from "./utils.js?v=2f374a58";
import { metricValue, seriesPoints, judgeValue, unitRowsAll, rowsWith,
         unitCell, cellJudge, okCount, VERDICT_TEXT } from "./aggregate.js?v=2f374a58";
import { unitOf, prevUnitOf, unitSeries, scaleDef } from "./scope.js?v=2f374a58";
import { renderMonth, renderHeatstrip } from "./calendar.js?v=2f374a58";
import { sparkline } from "./charts.js?v=2f374a58";
import { DETAILS, readinessTrail, briefHtml } from "./detail.js?v=2f374a58";
import { ensureShard, dayOf, hasShard } from "./data.js?v=2f374a58";
import { monthLabel, fmtMD, weekdayCN } from "./dateutil.js?v=2f374a58";

function ix() { return store.ix; }

/* 口径文案：同一个指标在三种尺度下叫什么，用户必须一眼看懂 */
function unitNoun(scale) {
  var fixed = { day: "当日", week: "本周", month: "本月" }[scale];
  return fixed || (scale.indexOf("roll") === 0 ? "近 " + scale.slice(4) + " 天" : scale);
}
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
    month: "月尺度：整个月就是观测范围；每张卡显示月日均与近 12 个月的走向。点日历某天可直接下钻到日尺度看那天。",
    roll7: "近 7 天滚动窗：从锚点往前数 7 天，不受自然周切割——所以「本周还没结束」那种偏差在这里不存在。环比比的是前一个 7 天窗（非重叠）。",
    roll28: "近 28 天滚动窗：比自然月稳（月长 28–31 天不一致），适合看月度级趋势。环比比前一个 28 天窗。",
    roll90: "近 90 天滚动窗：低频指标（体重、VO2 Max、体成分）才看得清的尺度。覆盖不足时只报数字不给结论。"
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
      rawDeltaChip(score, prevScore, prev.label)) +
    uscore(readyV == null ? "–" : readyV, rColor, rLabel,
      rawDeltaChip(ready, prevReady, prev.label));

  renderReadyTrail();

  /* 四项达标：分子分母都来自**后端逐日判定**（units.day[date].j）。
     以前这里自己拿 goals 比大小，于是睡眠的「设备当晚需求」这类逐日阈值
     永远进不了主页口径——主页与报告一个用 7h、一个用 7.83h。 */
  var ms = metrics(ix());
  $("unit-checks").innerHTML = CHECKS.map(function (c) {
    var m = ms.filter(function (x) { return x.key === CHECK_FIELD[c.key]; })[0];
    var st = m ? okStat(unit, m) : null;
    if (!st) return "";
    var pct = st.tot ? Math.round(st.ok / st.tot * 100) : 0;
    /* 分母为 0 = 该单元内没有可判定的天（覆盖不足 / 进行中），显示「–」而不是 0/0：
       0/0 看起来像「一天都没达标」，那是把「不知道」写成了「没做到」。 */
    var num = st.tot ? st.ok : "–";
    var den = st.tot ? st.tot : "–";
    return '<div class="uc' + (st.tot ? "" : " uc-na") + '"><span class="uc-k">' + esc(c.label) + '</span>' +
      '<span class="uc-v">' + num + '<i>/' + den + '</i></span>' +
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
  /* 步数按判读线（8000）判达标，goal（10000）只作目标 —— 与 aggregate.checkFns
     和后端 units.j 同源。两处实现都是历史遗留，改动时必须同步。 */
  if (key === "steps") {
    var line = g.steps_good || g.steps;
    return r.steps != null && r.steps >= line;
  }
  if (key === "hrv") return r.hrv_ok === true;
  return r.sedentary_min != null && r.sedentary_min <= g.sedentary_min;
}

/* 环比 chip。**措辞由后端的 delta_verdict 决定，不由数值大小决定**：
   `within-noise`（|Δ| < mdc）时不给箭头、不给百分比，只写「在误差内」；
   `suppressed`（tier.change=low，如步数）写「不做环比」，因为 6 天样本的均值
   本来就分不开 6400 和 10294；`insufficient` 写「样本不足」。 */
function deltaChip(cell, better, prevLabel) {
  if (!cell) return '<span class="dl flat">无环比基数</span>';
  var dv = cell.dv;
  if (!dv) return '<span class="dl flat">该指标未登记噪声门槛</span>';
  var word = VERDICT_TEXT[dv] || dv;
  if (dv !== "changed") return '<span class="dl flat">' + word + "</span>";
  var d = cell.d || 0;
  /* band 型指标（HRV）不给好/坏色：它的目标是「落在个人带内」，
     不是「越高越好」——从带外回到带内是好事，冲过带顶不是。方向交给判读，
     主页只报「变了多少、过没过门槛」。 */
  if (!better) {
    return '<span class="dl flat">' + (d > 0 ? "+" : "") + trimNum(d) + " " + word + "</span>";
  }
  var good = better === "down" ? d < 0 : d > 0;
  return '<span class="dl ' + (good ? "good" : "bad") + '">' +
    (d > 0 ? "+" : "") + trimNum(d) + " " + word + "</span>";
}

/* 分数类（综合指数 / 准备度）没有登记 mdc，因此**不判好坏**：
   只给差值，中性色。以前这里按百分比上红绿箭头，等于凭空给分数判了方向。 */
function rawDeltaChip(cur, prev, prevLabel) {
  if (cur == null || prev == null) return '<span class="dl flat">无环比基数</span>';
  var d = cur - prev;
  if (Math.abs(d) < 0.5) return '<span class="dl flat">与' + esc(prevLabel) + "持平</span>";
  /* 两个分数都是 0–100 的整数分，差值也取整：写「-13.7」会让读者以为
     分数本身有小数精度，而它没有。 */
  return '<span class="dl flat">' + (d > 0 ? "+" : "") + Math.round(d) +
    " vs " + esc(prevLabel) + "</span>";
}

function trimNum(v) {
  var s = String(Math.round(v * 10) / 10);
  return s.replace(/\.0$/, "");
}

/* ================= ③ 指标网格 ================= */
/* 某指标在单元内的达标计数：后端优先，缺 units 时回落前端现算 */
function okStat(unit, m) {
  if (!m || !m.dir) return null;
  var r = okCount(ix(), unit, m);
  /* 后端有单元就以后端为准，哪怕它判了 na（覆盖不足）——那时 tot=0，
     由卡面显示「覆盖 x% · 不给结论」。**不能回落到前端现算**：回落等于
     用 index 行的旧字段盖掉后端口径，而旧字段对「进行中的今天」是全 null
     （morning 行不含白天 activity），一回落就会把已有数字抹成「—」。 */
  if (r && r.src === "backend") return r.tot ? r : { ok: 0, tot: 0, na: true };
  var usable = rowsWith(unitRowsAll(unit, store.byDate), m);
  if (!usable.length) return null;
  return {
    ok: usable.filter(function (x) { return judgeValue(x[m.key], m, ix()) === true; }).length,
    tot: usable.length
  };
}

function cardHtml(m, unit, prevUnit) {
  var cell = unitCell(ix(), store.scale, unit.key, m);
  var val = cell ? cell.v : metricValue(rowsWith(unitRowsAll(unit, store.byDate), m), m);
  var prevCell = unitCell(ix(), prevUnit.scale, prevUnit.key, m);

  var j = cell ? cellJudge(cell) : judgeValue(val, m, ix());
  var na = j === "na";                     // 覆盖不足 / 无数据：不给结论
  var ok = na ? null : j;
  var color = m.dir == null ? "var(--text)"
    : na ? "var(--text-3)" : ok ? "var(--green-deep)" : "var(--amber-deep)";

  var pts = seriesPoints(unitSeries(store.anchor, store.scale, scaleDef(store.scale).seriesN), store.byDate, m, ix());
  var st = okStat(unit, m);
  var canDrill = store.scale === "day" && m.detail && DETAILS[m.detail];

  /* 覆盖率的诚实表达：后端把 coverage < 门槛的单元判成 na，
     这里必须说出来（「3/7 天数据」），而不是显示一个看起来很确定的数字。 */
  var stat;
  if (!st) {
    stat = '<span class="mx-stat">该指标无独立达标线</span>';
  } else if (na && cell && cell.c != null) {
    stat = '<span class="mx-stat">覆盖 ' + Math.round(cell.c * 100) + "% · 不给结论</span>";
  } else {
    stat = '<span class="mx-stat">达标 <b>' + st.ok + "</b>/" + st.tot + " " +
      (store.scale === "day" ? "（当日）" : "天") + "</span>";
  }

  return '<article class="mx-card' + (ok === false ? " is-warn" : "") + '" data-metric="' + m.key + '">' +
    '<div class="mx-head">' +
      '<div class="mx-title"><h3>' + esc(m.label) + '</h3><p class="mx-goal">' + esc(m.goalText()) + "</p></div>" +
      '<div class="mx-value"><b style="color:' + color + '">' + esc(m.fmt(val)) + "</b></div>" +
    "</div>" +
    '<div class="mx-meta">' + esc(aggNoun(store.scale, m)) +
      deltaChip(cell, m.dir === "below" ? "down" : m.dir === "above" ? "up" : null,
        prevUnit.label) + "</div>" +
    '<div class="mx-chart">' + sparkline(pts, m, ix(), {}) + "</div>" +
    '<div class="mx-foot">' + stat +
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
  $("metric-grid").innerHTML = metrics(ix()).map(function (m) {
    return cardHtml(m, unit, prev);
  }).join("");
}

/* ================= ④ 当日判读（仅日尺度）=================
   这一区**只放结论**：交叉判读 + 分组判点 + 数据缺口。
   任何有量化指标的实测值（如准备度）归总览或指标网格——判读区一旦夹带
   原始设备值，「交叉判读先行」这句副标题就会被自己的版式推翻。 */
/* 有行但全空：跨度内的空白日（raw 缺失）在分片里是「一行全 null」，
   不是「没有这一天」。**必须走与未来日相同的空态**——否则总览说
   「无采集记录」、判读区说「简报尚未生成」，同一屏两种说法（探针 2026-10-25 抓到）。 */
function dayIsEmpty(d) {
  return !!d && !d.has_brief && d.score == null && d.sleep_h == null &&
    d.hrv == null && d.steps == null;
}

function fillBrief(d) {
  if (dayIsEmpty(d)) { fillBriefEmpty(d.date); return; }
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
