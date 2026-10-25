/* 入口：拉索引 → 装配 → 全局事件代理。
 *
 * 观测状态只有两个自由度：**anchor（锚定日期）+ scale（尺度）**。
 * 没有「概览页」「某日页」之分——那两者是同一个视图在月尺度与日尺度下的样子。
 * 首屏只拉索引；单日细分在展开时才拉当月分片（取数链路见 data.js）。 */
import { store } from "./store.js?v=2df8ee29";
import { esc, $ } from "./utils.js?v=2df8ee29";
import { renderPage, setupDetailToggle } from "./metricgrid.js?v=2df8ee29";
import { shiftMonth, monthKey } from "./dateutil.js?v=2df8ee29";
import { loadIndex } from "./data.js?v=2df8ee29";
import { renderInBody, setInBody } from "./inbody.js?v=2df8ee29";

function setScale(scale) {
  store.scale = scale;
  renderPage();
}

function selectDate(date) {
  store.anchor = date;
  store.month = monthKey(date);
  renderPage();
}

function setupNav() {
  document.addEventListener("click", function (e) {
    if (!e.target.closest) return;
    var el = function (sel) { return e.target.closest(sel); };
    var a;

    if ((a = el("[data-scale]"))) { e.preventDefault(); setScale(a.getAttribute("data-scale")); return; }

    if ((a = el("[data-month-shift]"))) {
      e.preventDefault();
      store.month = shiftMonth(store.month, parseInt(a.getAttribute("data-month-shift"), 10));
      renderPage();
      return;
    }
    if ((a = el("[data-month-today]"))) { e.preventDefault(); store.month = monthKey(store.anchor); renderPage(); return; }

    if ((a = el(".cal-cell"))) {
      e.preventDefault();
      var d = a.getAttribute("data-date");
      /* 月尺度下点某天 = 下钻到日尺度看那天（整月范围内点一天，本意就是看那天） */
      if (store.scale === "month") store.scale = "day";
      selectDate(d);
      return;
    }
  });
}

function renderSync() {
  var pill = $("sync-pill");
  pill.classList.remove("pending");
  var s = store.ix.span;
  /* 走的是随包静态副本时显式标注——不能用旧数据冒充「已同步」，也不让兜底静默发生 */
  var via = store.dataFrom === "bundled" ? " · 静态副本" : "";
  pill.classList.toggle("stale", store.dataFrom === "bundled");
  $("sync-text").textContent = "Garmin 已同步 · " + s.n + " 个数据日" +
    (store.ix.synced_at ? " · 更新于 " + store.ix.synced_at : "") + via;
}

function fail(err) {
  document.body.innerHTML =
    '<div class="load-error">数据加载失败：<code>index.json</code> 与热更新端点 <code>api/data/index</code> ' +
    '都读取异常（' + esc((err && err.message) || err) +
    "）。<br>请通过 HTTP 服务访问本页（不要直接双击打开文件）。</div>";
}

function init(ixd) {
  store.ix = ixd;
  store.rows = ixd.days.slice();
  /* 统计口径只认完整日；日历与当前单元主值才用得上进行中的当天 */
  store.closed = ixd.days.filter(function (r) { return !r.morning; });
  ixd.days.forEach(function (r) { store.byDate[r.date] = r; });

  /* 锚点优先落在今天；但今天常常**还没有任何记录**（当天要等当晚 23:30 出终版），
     此时退回最近一个有数据的日——否则一按「日」尺度就是一张空页。 */
  var tod = ixd.today || "";
  var todayHasRow = !!store.byDate[tod];
  store.anchor = todayHasRow ? tod : (ixd.default_day || (ixd.days.length ? ixd.days[ixd.days.length - 1].date : tod));
  store.month = monthKey(store.anchor);
  store.scale = "month";

  setupNav();
  setupDetailToggle();
  /* 体成分是事件型数据，不随 anchor/scale 重绘——只在 boot 时渲染一次。
     把它塞进 renderPage 会让每点一次日历都重算一遍慢变量，且暗示它跟着日期变。 */
  setInBody(ixd.inbody);
  renderInBody();
  renderPage();
  renderSync();
}

loadIndex()
  .then(init)
  .catch(fail);
