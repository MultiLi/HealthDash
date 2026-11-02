/* 结构（体重 / 体脂率 / 去脂体重）· 慢变量维度。
 *
 * **它不是一个板块，是三个维度之一。** 页面按「身体在补（恢复）／身体在耗（活动）／
 * 身体是什么（结构）」划分，InBody 只是「结构」这一个维度的测量工具。所以这里
 * 没有「体成分专区」——只有：
 *
 *   ① `bodyGroupHtml()` —— 指标区第三组「结构」，卡壳与前两组完全同构。
 *      差别只有时间语义：扫描点，不吃 anchor/scale。
 *   ② `renderStructure()` —— 「结构 × 活动」对照：四带共享一根日期轴。
 *      这不是「给体成分配一段 Garmin 数据」（那是数据源视角，产物就是一张
 *      两张均值对着看的表），而是**两条轴在时间上的并置**：同一段时间里，
 *      结构怎么变、活动怎么变。
 *   ③ 折叠的「测量报告」—— 节段分布、目标路径、参考区间、测量条件。
 *      这些**共享不了日期轴**（它们属于那一次测量），所以不进主视图。
 *
 * 判定的唯一来源是后端 `status.json`（check_inbody 过了 rules.confidence 的门槛），
 * 本文件**不算门槛、不比大小**——只把 verdict 翻成人话。
 */
import { esc, $ } from "./utils.js?v=ae331b07";
import { sparkline, alignBands } from "./charts.js?v=ae331b07";

function n(v, d) {
  if (v == null || isNaN(v)) return "–";
  if (d == null) return String(Math.round(v * 10) / 10);
  return Number(v).toFixed(d);
}

function md(iso) {
  return (iso || "").slice(5, 10).replace("-", "/");
}

var VERDICT = {
  changed: function (c) { return "过门槛 " + n(c.mdc, 1) + c.unit; },
  "within-noise": function (c) { return "在误差内（±" + n(c.mdc, 1) + c.unit + "）"; },
  "no-baseline": function () { return "首次测量，无可比"; },
  "not-comparable": function () { return "跨时段不可比"; },
  /* 有新扫描但闸门还没跑过它：宁可不给结论，也不拿上一次的差冒充这一次的 */
  "pending-check": function () { return "闸门未覆盖这次扫描，暂不比较"; }
};

/* 变化一句话：**不给好/坏色**。体重下降可能是掉脂肪，也可能是掉肌肉，
   方向要等判读层把去脂体重一起看才能说；这里只报「变了多少、过没过门槛」。 */
function deltaText(c) {
  if (c.verdict === "no-baseline" || c.verdict === "pending-check") {
    return (VERDICT[c.verdict] || VERDICT["no-baseline"])();
  }
  var v = c.delta;
  var sign = v > 0 ? "+" : (v === 0 ? "±" : "");
  var head = (c.prev_date ? "较 " + md(c.prev_date) + " " : "") +
    sign + n(v, c.precision) + c.unit;
  return head + " · " + (VERDICT[c.verdict] || VERDICT["not-comparable"])(c);
}

function refNote(c) {
  var r = c.ref, u = c.unit || "";
  if (r == null) return "";
  if (typeof r === "number") {
    return c.value > r ? "高于 " + r + " 级上限" : "在 " + r + " 级以内";
  }
  if (!r.length || r[0] == null) return "";
  if (c.value > r[1]) return "高于区间上限 " + n(c.value - r[1], c.precision) + " " + u;
  if (c.value < r[0]) return "低于区间下限 " + n(r[0] - c.value, c.precision) + " " + u;
  return "区间 " + r[0] + "–" + r[1] + " " + u + " 内";
}

/* ============ ① 指标区的「结构」组 ============ */

/* 扫描点要按**真实间隔**排：10/18 → 10/21 是 3 天，10/21 → 10/31 是 10 天。
   等距排会把 3 天和 10 天画成一样宽，看上去像「匀速下降」——那是图表在编故事。
   跨度超过半年就退回等距：一天一个槽会画出几百个空格，反而什么也看不出。 */
function seriesPoints(c) {
  var s = c.series || [];
  if (!s.length) return [];
  var first = s[0].d, last = s[s.length - 1].d;
  var span = (Date.parse(last) - Date.parse(first)) / 86400000;
  if (!(span >= 1) || span > 180) {
    return s.map(function (p) { return { label: md(p.d), v: p.v, ok: null, partial: false }; });
  }
  var map = {};
  s.forEach(function (p) { map[p.d] = p.v; });
  var out = [];
  for (var i = 0; i <= span; i++) {
    var d = new Date(Date.parse(first) + i * 86400000).toISOString().slice(0, 10);
    out.push({ label: md(d), v: map[d] == null ? null : map[d], ok: null, partial: false });
  }
  return out;
}

function cardHtml(c) {
  var pts = seriesPoints(c);
  /* dir:null = 不画目标线。体重/体脂没有「越高越好」的方向，画一条线等于替人判方向。 */
  var m = { dir: null, agg: "point", label: c.label, unit: c.unit };
  var svg = sparkline(pts, m, null, {
    cursor: false,
    unitWord: "次扫描",
    finer: "扫描是事件型数据，第二次同口径测量之后才谈变化"
  });
  return '<article class="mx-card ib-mx" data-sparse="' + esc(c.key) + '">' +
    '<div class="mx-head">' +
      '<div class="mx-title"><h3>' + esc(c.label) + "</h3>" +
        '<p class="mx-goal">' + c.scans + " 次扫描 · 最近 " +
        md((c.series[c.series.length - 1] || {}).d) + "</p></div>" +
      '<div class="mx-value"><b>' + n(c.value, c.precision) + "</b><em>" +
        esc(c.unit) + "</em></div>" +
    "</div>" +
    '<div class="mx-meta"><span class="dl flat">' + esc(deltaText(c)) + "</span></div>" +
    '<div class="mx-chart">' + svg + "</div>" +
    '<div class="mx-foot"><span class="mx-stat">' + esc(refNote(c) || "无设备参考区间") + "</span></div>" +
    "</article>";
}

export function bodyGroupHtml() {
  var d = _data;
  if (!d || !d.available || !d.cards || !d.cards.length) return "";
  var span = d.scan_count > 1
    ? "低频 · " + d.scan_count + " 次扫描，不随上方的日期与尺度变化"
    : "低频 · 1 次扫描，现在是截面不是趋势";
  return '<section class="mx-group" data-group="structure">' +
    '<div class="mx-gh"><h3>结构</h3><span>' + esc(span) + "</span></div>" +
    '<div class="metric-grid">' + d.cards.map(cardHtml).join("") + "</div></section>";
}

/* ============ ② 结构 × 活动：共享时间轴 ============ */

function plotNote(d) {
  var w = d.window;
  if (!w || !w.metrics || !w.metrics.length) return "";
  /* 只报**本窗**的均值，不报「与前窗比」：前窗在那张图上就是左边那一段，
     眼睛能直接看；再写一遍就是为对比而对比。 */
  /* 每个数都带指标名：只写「6.7h」得回去数第四位才知道那是睡眠。
     单位紧贴数字（中文单位不加空格），指标名用浅色小字。 */
  var parts = w.metrics.slice(0, 4).map(function (m) {
    var v = m.key === "steps"
      ? Math.round(m.now).toLocaleString("en-US")
      : n(m.now, m.precision);
    return '<span><i>' + esc(m.label) + "</i><b>" + v + "</b>" + esc(m.unit) + "</span>";
  });
  return '<p class="ab-sum"><em>上次扫描后 ' + w.days + " 天的日均</em>" +
    parts.join("") + "</p>";
}

export function renderStructure() {
  var zone = $("structure-zone");
  if (!zone) return;
  var d = _data;
  if (!d || !d.available) { zone.hidden = true; return; }
  zone.hidden = false;

  var b = d.bands;
  var plot = $("struct-plot");
  if (plot) {
    plot.innerHTML = b
      ? alignBands(b) + plotNote(d) +
        '<p class="ab-caveat">' + esc(b.note) + "</p>"
      : '<p class="ib-empty">扫描次数不足或 Garmin 数据跨度不够，画不出对齐对照。' +
        "两条轴要并排，至少需要两次扫描与它们之间的行为记录。</p>";
  }
  renderReport(d);
}

/* ============ ③ 这次测量的报告（折叠） ============ */

function splitCard(d) {
  var L = d.latest || {};
  var lean = L.fat_free_mass_kg, fat = L.body_fat_mass_kg, wt = L.weight_kg;
  var lp = (lean && wt) ? lean / wt * 100 : 0;
  var fp = (fat && wt) ? fat / wt * 100 : 0;
  var html = '<article class="ib-card"><div class="ib-card-head"><h3>体重构成</h3>' +
    "<span>去脂体重 + 体脂量</span></div>" +
    '<div class="ib-bar">' +
      '<i class="ib-lean" style="width:' + lp + '%">去脂 ' + n(lean) + "</i>" +
      '<i class="ib-fat" style="width:' + fp + '%">脂肪 ' + n(fat) + "</i>" +
    "</div>" +
    '<div class="ib-legend">' +
      '<span><i style="background:var(--green)"></i>去脂体重 <b>' + n(lean) + " kg</b></span>" +
      '<span><i style="background:var(--amber)"></i>体脂量 <b>' + n(fat) + " kg</b></span>" +
      '<span><i style="background:var(--text-3)"></i>体脂率 <b>' + n(L.percent_body_fat) + " %</b></span>" +
    "</div>";

  /* 双口径：体测仪读数与晨起空腹体重是两个口径，并列展示、**禁止相减**。
     不写这句，88.2 与 85.9 摆在一起必然被读成「胖了 2.3kg」。
     晨起那条要是很久没称了，日期与天数必须一起写出来——一个 57 天前的数
     与一个昨天的数并排，看上去同样「当前」。 */
  var mw = d.morning_weight;
  if (mw && mw.kg) {
    var age = mw.age_days == null ? "" :
      (mw.age_days > 30 ? "，已 " + mw.age_days + " 天未称" : "");
    html += '<p class="ib-dual">晨起空腹体重 <b>' + n(mw.kg) + " kg</b>（" + esc(mw.date) +
      age + "）是另一条口径：体测仪读数含衣着/进食/时段影响。" +
      "<em>两者不可相减，也不要取平均</em>——要比较，就在同一口径下再测一次。</p>";
  } else {
    html += '<p class="ib-dual">没有晨起空腹体重的记录，所以这里只有体测仪一个口径。' +
      "两条口径并列才有意义：<em>补一次晨起称重</em>，才看得出体测仪读数里有多少是时段与进食。</p>";
  }
  return html + "</article>";
}

/* 参考区间条：把「实测值 vs 区间」画成同一根尺子上的位置。
   域 = 区间与实测值的并集再各留 25% 余量，保证极端值也能落在条内。 */
function rangeRow(label, value, unit, ref, opts) {
  opts = opts || {};
  /* 等级型（内脏脂肪 1–9）：ref 是个数字上限，不是区间。画一根空轨道会让人
     以为「区间数据缺失」，其实它本来就没有区间——只说越没越线。 */
  if (typeof ref === "number") {
    var over = value != null && value > ref;
    return '<div class="ib-row ib-row-plain"><span class="ib-k">' + esc(label) + "</span>" +
      '<span class="ib-v' + (over ? " out" : "") + '">' + n(value, opts.d) +
      "<em>" + esc(unit || "") + "</em></span>" +
      '<span class="ib-note-inline' + (over ? " warn" : "") + '">' +
      (over ? "高于 " + ref + " 级上限" : "在 " + ref + " 级以内") + "</span></div>";
  }
  if (value == null) {
    return '<div class="ib-row"><span class="ib-k">' + esc(label) +
      '</span><span class="ib-v">–</span><span class="ib-track"></span></div>';
  }
  var lo = ref ? ref[0] : null, hi = ref ? ref[1] : null;
  var out = lo != null && (value > hi || value < lo);
  var html = '<div class="ib-row"><span class="ib-k">' + esc(label) + "</span>" +
    '<span class="ib-v' + (out ? " out" : "") + '">' + n(value, opts.d) +
    "<em>" + esc(unit || "") + "</em></span>";

  if (lo == null || hi == null) {
    html += '<span class="ib-track"></span></div>';
    return html;
  }
  var span = Math.max(hi - lo, 0.001);
  var min = Math.min(lo, value) - span * 0.28;
  var max = Math.max(hi, value) + span * 0.28;
  var p = function (x) { return Math.max(1.5, Math.min(98.5, (x - min) / (max - min) * 100)); };
  var zl = p(lo), zh = p(hi), pv = p(value);

  html += '<span class="ib-track">' +
    '<i class="ib-zone" style="left:' + zl + "%;width:" + Math.max(zh - zl, 1) + '%"></i>' +
    '<b class="ib-dot' + (out ? "" : " ok") + '" style="left:' + pv + '%"></b>' +
    "</span>";
  html += '<span class="ib-note' + (out ? " warn" : "") + '">' + diffText(value, lo, hi, unit, opts) + "</span></div>";
  return html;
}

function diffText(v, lo, hi, unit, opts) {
  if (v > hi) return "高于上限 " + n(v - hi, opts.d) + (unit || "");
  if (v < lo) return "低于下限 " + n(lo - v, opts.d) + (unit || "");
  return "区间内（上限余 " + n(hi - v, opts.d) + (unit || "") + "）";
}

function metricsCard(d) {
  var L = d.latest || {}, R = d.reference || {};
  var rows = [
    rangeRow("体重", L.weight_kg, " kg", R.weight_kg, { d: 1 }),
    rangeRow("体脂率", L.percent_body_fat, " %", R.percent_body_fat, { d: 1 }),
    rangeRow("骨骼肌", L.skeletal_muscle_mass_kg, " kg", R.skeletal_muscle_kg, { d: 1 }),
    rangeRow("体脂量", L.body_fat_mass_kg, " kg", R.body_fat_kg, { d: 1 }),
    /* 这两项不进卡片（三次都在同一平面），但报告里必须逐项列出——
       等级型指标的价值在「有没有越线」，不在「画一条线」。 */
    rangeRow("内脏脂肪", L.visceral_fat_level, " 级", R.visceral_fat_level, { d: 0 })
  ].join("");
  return '<article class="ib-card"><div class="ib-card-head"><h3>相对参考区间</h3>' +
    "<span>区间由设备内置判定表下发</span></div>" +
    '<div class="ib-rows">' + rows + "</div></article>";
}

function targetCard(d) {
  var t = d.targets || [];
  if (!t.length) {
    return '<article class="ib-card"><div class="ib-card-head"><h3>目标路径</h3></div>' +
      '<p class="ib-target-head">缺少去脂体重或体重，无法换算目标。</p></article>';
  }
  var rows = t.map(function (x) {
    return "<tr><td>" + x.pbf + "%</td>" +
      '<td class="num w">' + n(x.weight_kg) + " kg</td>" +
      '<td class="num loss">' + (x.fat_to_lose_kg > 0 ? "−" + n(x.fat_to_lose_kg) : "0") + " kg</td>" +
      "<td>" + esc(x.label) + "</td></tr>";
  }).join("");
  return '<article class="ib-card"><div class="ib-card-head"><h3>目标路径</h3>' +
    "<span>前提：去脂体重不变</span></div>" +
    '<p class="ib-target-head">以「减脂保肌」为前提——去脂体重 ' + n(d.latest.fat_free_mass_kg) +
    " kg 不变时，体重 = 去脂体重 ÷ (1 − 目标体脂率)。这是算术结果，不是承诺的周期。</p>" +
    '<table class="ib-tbl"><colgroup><col style="width:20%"><col style="width:24%">' +
    '<col style="width:24%"><col style="width:32%"></colgroup>' +
    '<thead><tr><th>目标体脂率</th><th style="text-align:right">对应体重</th>' +
    '<th style="text-align:right">需减脂肪</th><th>档位</th></tr></thead>' +
    "<tbody>" + rows + "</tbody></table></article>";
}

function segCard(d) {
  var L = d.latest || {};
  var lean = L.segmental_lean_kg, fat = L.segmental_fat_kg;
  if (!lean && !fat) return "";
  var order = [["trunk", "躯干"], ["right_leg", "右腿"], ["left_leg", "左腿"],
               ["right_arm", "右臂"], ["left_arm", "左臂"]];
  function group(obj, cls) {
    var vals = order.map(function (o) { return obj[o[0]]; }).filter(function (v) { return v != null; });
    var mx = Math.max.apply(null, vals.concat([0.001]));
    return order.map(function (o) {
      var v = obj[o[0]];
      if (v == null) return "";
      return '<div class="ib-seg-row ' + cls + '"><span>' + o[1] + "</span>" +
        '<span class="ib-mini"><i style="width:' + Math.max(v / mx * 100, 4) + '%"></i></span>' +
        "<b>" + n(v) + "</b></div>";
    }).join("");
  }
  var ftotal = 0;
  order.forEach(function (o) { ftotal += (fat && fat[o[0]]) || 0; });
  var trunkShare = (fat && fat.trunk && ftotal) ? fat.trunk / ftotal * 100 : null;
  return '<article class="ib-card"><div class="ib-card-head"><h3>节段分布</h3>' +
    "<span>" + (trunkShare ? "躯干占节段脂肪合计 " + n(trunkShare, 0) + "%" : "左右对比") + "</span></div>" +
    '<div class="ib-seg-wrap">' +
      '<div class="ib-seg"><h4>瘦体重 kg</h4>' + group(lean || {}, "") + "</div>" +
      '<div class="ib-seg"><h4>脂肪量 kg</h4>' + group(fat || {}, "fat") + "</div>" +
    "</div>" +
    '<p class="ib-caveats" style="margin-top:14px"><b>节段只用于看左右对称性与分布</b>' +
    "，不能用它判断「哪条腿更该减脂肪」——局部减脂不存在。</p></article>";
}

function chips(d) {
  var L = d.latest || {}, R = d.reference || {};
  var out = [];
  function chip(k, v, unit, warn) {
    out.push('<span class="ib-chip' + (warn ? " warn" : "") + '">' + esc(k) +
      "<b>" + v + "</b>" + esc(unit || "") + "</span>");
  }
  if (L.bmi != null) chip("BMI", n(L.bmi), "", R.bmi && L.bmi > R.bmi[1]);
  if (L.whr != null) chip("腰臀比", n(L.whr, 2), "", R.whr && L.whr > R.whr[1]);
  if (L.ffmi != null) chip("FFMI", n(L.ffmi), "", false);
  if (L.bmr_kcal != null) chip("基础代谢", n(L.bmr_kcal, 0), " kcal", false);
  if (L.total_body_water_kg != null) chip("体水分", n(L.total_body_water_kg), " kg", false);
  return out.length ? '<div class="ib-chips">' + out.join("") + "</div>" : "";
}

/* 扫描时间线：把「已有 N 次扫描」这句话换成真的三次。 */
function timeline(d) {
  var hs = d.history || [];
  if (!hs.length) return "";
  var pts = hs.map(function (h) {
    return md(h.datetime) + " " + n(h.weight_kg) + " kg / " + n(h.percent_body_fat, 1) + "%";
  }).join("　→　");
  var tail = hs.length >= 2
    ? "点少，只看方向；只有机型与时点一致的那两次，涨跌才可比。"
    : "<b>现在是截面，不是趋势</b>——第二次同口径测量之后才谈变化。";
  return '<p class="ib-timeline"><b>' + hs.length + " 次扫描</b>　" + esc(pts) + "</p>" +
    '<p class="ib-caveats">' + tail + "</p>";
}

function renderReport(d) {
  var box = $("struct-report");
  if (!box) return;
  var L = d.latest || {};
  var st = d.status || {};
  var ageTxt = d.age_days == null ? "未知"
    : (d.age_days === 0 ? "今天测得" : "距今 " + d.age_days + " 天");

  /* 摘要行要能独立成句：不展开也知道「哪次、多少、比上次怎么样」。 */
  var head = '<span class="sr-date">' + esc((L.datetime || "").replace("T", " ").slice(5, 16)) + "</span>" +
    '<b class="sr-wt">' + n(L.weight_kg) + " kg</b>" +
    '<span class="sr-pbf">体脂 ' + n(L.percent_body_fat) + "%</span>" +
    (d.cards && d.cards[0] ? '<span class="sr-delta">' + esc(deltaText(d.cards[0])) + "</span>" : "") +
    '<span class="sr-age' + (d.stale ? " stale" : "") + '">' + esc(ageTxt) + "</span>";

  var verdict = (st.notes && st.notes.length)
    ? '<p class="ib-verdict"><b>闸门判定</b>　' + esc(st.notes[0]) + "</p>" : "";

  box.innerHTML =
    '<details class="sr-wrap" id="sr-wrap">' +
      '<summary class="sr-sum"><span class="sr-ico">测量报告</span>' + head +
        '<span class="sr-more"></span></summary>' +
      '<div class="sr-body">' +
        '<div class="ib-grid">' + splitCard(d) + metricsCard(d) + "</div>" +
        '<div class="ib-grid">' + targetCard(d) + segCard(d) + "</div>" +
        chips(d) + timeline(d) + verdict +
        '<p class="ib-caveats">' + (d.caveats || []).map(function (c) {
          return "<span>" + esc(c) + "</span>";
        }).join("") + "</p>" +
        '<p class="ib-src">InBody ' + esc(d.equipment || "") + " · 机身 " +
          esc(d.serial || "—") + " · 与 Garmin 各自独立采集</p>" +
      "</div>" +
    "</details>";
}

/* main.js 在 init 后注入数据（避免模块间循环依赖） */
var _data = null;
export function setInBody(d) { _data = d; }
