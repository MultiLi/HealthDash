/* 入口：拉索引 → 装配 → 全局事件代理。
 *
 * 观测状态只有两个自由度：**anchor（锚定日期）+ scale（尺度）**。
 * 没有「概览页」「某日页」之分——那两者是同一个视图在月尺度与日尺度下的样子。
 * 首屏只拉索引；单日细分在展开时才拉当月分片（取数链路见 data.js）。 */
import { store } from "./store.js?v=b8889ec3";
import { esc, $ } from "./utils.js?v=b8889ec3";
import { renderPage, setupDetailToggle, renderBriefLog } from "./metricgrid.js?v=b8889ec3";
import { shiftMonth, monthKey, fmtMD } from "./dateutil.js?v=b8889ec3";
import { loadIndex } from "./data.js?v=b8889ec3";
import { renderInBody, setInBody } from "./inbody.js?v=b8889ec3";

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
      /* 月与滚动窗都没有「某一天」的概念，点日历即下钻到日尺度 */
      if (store.scale === "month" || store.scale.indexOf("roll") === 0) store.scale = "day";
      selectDate(d);
      return;
    }

    /* 判读流水：点一行 = 跳到那天看它的判读。
       **必须一并切到日尺度**——判读区只在日尺度存在，不切的话点了没反应，
       看起来像坏了。 */
    if ((a = el(".bl-i"))) {
      e.preventDefault();
      store.scale = "day";
      selectDate(a.getAttribute("data-date"));
      return;
    }
  });
}

/* 胶囊文案：**纯函数**，不碰 DOM。
   抽出来是为了让冒烟能直接验口径（`37/60 天有实测` 这种比率不该靠读 DOM 反推）。
   参数都是显式的，不读全局 store——测试才能构造边界情况。 */
export function syncText(ix, dataFrom) {
  var span = ix.span || {};
  var col = ix.collect || {};
  var sum = col.summary || {};
  var via = dataFrom === "bundled" ? " · 静态副本" : "";
  /* 「N 个数据日」这个措辞是错的：跨度 60 天，其中 22 天一个实测值都没有，
     却照样被算进「数据日」。改说「多少天真的有数」——分母是跨度，分子是实测。
     这是全站最显眼的一句话，它要是虚的，下面所有图表都跟着虚。 */
  var measured = sum.ok;
  var total = sum.n || span.n;
  var bad = (sum.failed || 0) + (sum.missing || 0);
  var text;
  if (measured == null) {
    text = "Garmin 已同步 · " + span.n + " 天跨度";
  } else {
    text = "Garmin 已同步 · " + measured + "/" + total + " 天有实测";
  }
  if (bad) text += " · " + bad + " 天未采到";
  text += (ix.synced_at ? " · 更新于 " + ix.synced_at : "") + via;
  /* 管线问题用琥珀，不用红：它不是「身体出事了」，是「数据没到手」 */
  return { text: text, warn: bad > 0, stale: dataFrom === "bundled" };
}

function renderSync() {
  var pill = $("sync-pill");
  pill.classList.remove("pending");
  var r = syncText(store.ix, store.dataFrom);
  /* 走的是随包静态副本时显式标注——不能用旧数据冒充「已同步」，也不让兜底静默发生 */
  /* 2026-11-01：云端副本已整套移除。数据只有本地一份（data/ 单根 + 热更新端点），
     所以这里不再有「云端是否一致」这个状态——少一个可失败的链路。 */
  pill.classList.toggle("stale", r.stale);
  pill.classList.toggle("warn", r.warn);
  $("sync-text").textContent = r.text;
  pill.title = "";
  renderCollectBar(store.ix.collect || {}, (store.ix.collect || {}).summary || {});
}

/* 采集告警条：只在「管线出过问题」时出现，说清哪几天、哪个端点。
   它不是错误日志归档，回答的只有一句：「刚才那些空白格，凭什么是空白」。

   **必须短。** 第一版把 5 次失败的日期和端点全列出来，占了三行——
   信息没错，但没人会读三行橙色小字，等于没提示。
   现在只展开最近一次（其余折成「等 N 次」），缺失改成按段报。 */
export function renderCollectBar(col, sum) {
  var box = $("collect-bar");
  if (!box) return;
  var errs = (col && col.recent_errors) || [];
  var miss = sum && sum.missing;
  if (!errs.length && !miss) { box.hidden = true; box.innerHTML = ""; return; }
  var msg = [];
  if (errs.length) {
    var top = errs[0];
    var t = "端点报错 " + (sum.failed || errs.length) + " 天（最近 " + top.date.slice(5);
    if (top.keys && top.keys.length) t += "：" + top.keys.join("、");
    t += "）";
    if (errs.length > 1) t += "，另有 " + (errs.length - 1) + " 天同类";
    msg.push(t);
  }
  if (miss) {
    var rg = (col.missing_ranges || []).slice(0, 3);
    msg.push(miss + " 天无原始数据（采集未运行）" + (rg.length ? "：" + rg.join("、") : ""));
  }
  box.hidden = false;
  box.innerHTML = '<span class="cb-ico">!</span><span class="cb-txt">' +
    esc(msg.join(" · ")) +
    '。<b>这些不是「没戴表」，是数据没取到</b>——空白格不代表你那几天没动。</span>';
}

/* 主观补数提示（2026-11-01）。
 *
 * **它解决的是摩擦，不是记性。** 能为设备自动采的一直在跑（60 天里 37 天有实测），
 * 要靠人说一句的断了 46 天。补一句「精力 4 分」得先打开对话、找到 agent、
 * 说完整一段话——成本远大于收益，于是永远排在别的之后。
 * 这里只把「还缺哪几项」和「一句话模板」摆出来，让人少走两步路。
 *
 * **只读，不写**：主页是公开站点，index.json 本就是全量健康数据，在上面开个
 * 写接口等于给任何人一个往主观日志里写字的洞。写入仍然只能经由 agent。 */
/* 导出给冒烟测试：HTML 模板的正确性只能靠执行一遍来验，
   静态读源码看不出「插值拼错导致整块空白」这类问题。 */
export function renderNudge() {
  var box = $("nudge-bar");
  if (!box) return;
  var n = store.ix.nudge;
  if (!n || !n.missing || !n.missing.length) { box.hidden = true; box.innerHTML = ""; return; }

  /* 断档天数比「今天缺几项」更值得说：缺一天是疏忽，断 46 天是这条链路断了。
     两者都写出来，但不做说教——页面不负责劝人，只负责把事实摆清楚。 */
  var gap = "";
  if (n.gap_days != null && n.gap_days > 1) {
    gap = '<span class="nb-gap">主观数据已断 ' + n.gap_days + " 天" +
      (n.last_logged ? "（上次 " + fmtMD(n.last_logged) + "）" : "") + "</span>";
  }
  box.hidden = false;
  box.innerHTML =
    '<div class="nb-main">' +
      '<span class="nb-k">今日提醒</span>' +
      '<span class="nb-v">还缺 <b>' + esc(n.labels) + "</b></span>" +
      gap +
    "</div>" +
    '<div class="nb-do">' +
      '<code class="nb-prompt" id="nb-prompt">' + esc(n.prompt) + "</code>" +
      '<button type="button" class="nb-copy" data-copy-nudge>复制</button>' +
    "</div>";
}

/* 复制话术：成功就地把按钮改成「已复制」。
   剪贴板 API 在非 https / 老浏览器里会失败——**失败要说出来**，
   静默失败会让人以为复制成功了，粘贴出去是空的。 */
function copyNudge(btn) {
  var el = $("nb-prompt");
  if (!el) return;
  var txt = el.textContent;
  var done = function (okFlag) {
    btn.textContent = okFlag ? "已复制" : "请手动选中";
    setTimeout(function () { btn.textContent = "复制"; }, 1800);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(txt).then(function () { done(true); },
      function () { done(false); });
  } else {
    done(false);
  }
}

function setupNudge() {
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-copy-nudge]");
    if (b) { e.preventDefault(); copyNudge(b); }
  });
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
  setupNudge();
  /* 体成分是事件型数据，不随 anchor/scale 重绘——只在 boot 时渲染一次。
     把它塞进 renderPage 会让每点一次日历都重算一遍慢变量，且暗示它跟着日期变。 */
  setInBody(ixd.inbody);
  renderInBody();
  renderPage();
  renderSync();
  /* 待补提示只依赖「今天」这个事实，不随尺度/锚点变化，故不进 renderPage */
  renderNudge();
  /* 判读流水同理：它是长期视角，跟着日历翻页重建 37 行只会互相打断 */
  renderBriefLog();
}

/* 冒烟测试要 import 本模块的纯函数，但不想触发整页 boot
   （DOM 桩没有真实布局，boot 会白白跑一遍还会设脏 body）。
   走一个显式开关，而不是让测试去猜时序。 */
if (!globalThis.__SMOKE_NO_BOOT__) {
  loadIndex()
    .then(init)
    .catch(fail);
}
