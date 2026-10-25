/* 体成分（InBody）· 事件型数据区块的渲染。
 *
 * 与 metricgrid 的根本差别：那张网格的每一格都是「同一天的一个连续量」，
 * 而这里是「一次体测的一个截面」。所以：
 *   · 不吃 scale / anchor——时间语义只有「最近一次 + 距今 N 天」；
 *   · 不画 trend 线——只有 1 条记录时画趋势就是编数据；
 *   · 参考区间来自设备随记录下发的 MFA/BCA（后端已抽成 reference），前端不硬编码。
 *
 * 版式表达的核心是两件事：
 *   ① 体重**由什么构成**（去脂 + 脂肪的堆叠条），而不是一个孤零零的 90.2；
 *   ② 每个数**相对参考区间站在哪**（区间条 + 越界量），而不是只给颜色。
 */
import { esc, $ } from "./utils.js?v=b4d49e42";

function n(v, d) {
  if (v == null || isNaN(v)) return "–";
  return d == null ? String(Math.round(v * 10) / 10) : Number(v).toFixed(d);
}

/* 参考区间条：把「实测值 vs 区间」画成同一根尺子上的位置。
   域 = 区间与实测值的并集再各留 25% 余量，保证极端值也能落在条内。 */
function rangeRow(label, value, unit, ref, opts) {
  opts = opts || {};
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

function splitCard(d) {
  var L = d.latest || {};
  var lean = L.fat_free_mass_kg, fat = L.body_fat_mass_kg, wt = L.weight_kg;
  var lp = (lean && wt) ? lean / wt * 100 : 0;
  var fp = (fat && wt) ? fat / wt * 100 : 0;
  var html = '<article class="ib-card"><div class="ib-card-head"><h3>体重构成</h3>' +
    "<span>去脂体重 + 体脂量</span></div>" +
    '<div class="ib-split-top"><b>' + n(wt) + "</b><em>kg</em></div>" +
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
     不写这句，90.2 与 85.9 摆在一起必然被读成「胖了 4.3kg」。 */
  var mw = d.morning_weight;
  if (mw && mw.kg) {
    html += '<p class="ib-dual">晨起空腹体重 <b>' + n(mw.kg) + " kg</b>（" + esc(mw.date) +
      "）是另一条口径：体测仪读数含衣着/进食/时段影响。<em>两者不可相减，也不要取平均</em>——" +
      "要比较，就在同一口径下再测一次。</p>";
  }
  return html + "</article>";
}

function metricsCard(d) {
  var L = d.latest || {}, R = d.reference || {};
  var rows = [
    rangeRow("体重", L.weight_kg, " kg", R.weight_kg, { d: 1 }),
    rangeRow("体脂率", L.percent_body_fat, " %", R.percent_body_fat, { d: 1 }),
    rangeRow("骨骼肌", L.skeletal_muscle_mass_kg, " kg", R.skeletal_muscle_kg, { d: 1 }),
    rangeRow("体脂量", L.body_fat_mass_kg, " kg", R.body_fat_kg, { d: 1 })
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
  if (L.visceral_fat_level != null)
    chip("内脏脂肪等级", n(L.visceral_fat_level, 0), "", L.visceral_fat_level > 9);
  if (L.whr != null) chip("腰臀比", n(L.whr, 2), "", R.whr && L.whr > R.whr[1]);
  if (L.ffmi != null) chip("FFMI", n(L.ffmi), "", false);
  if (L.bmr_kcal != null) chip("基础代谢", n(L.bmr_kcal, 0), " kcal", false);
  if (L.total_body_water_kg != null) chip("体水分", n(L.total_body_water_kg), " kg", false);
  return out.length ? '<div class="ib-chips">' + out.join("") + "</div>" : "";
}

export function renderInBody() {
  var zone = $("inbody-zone");
  if (!zone) return;
  var d = _data;
  if (!d || !d.available) {
    zone.hidden = false;
    $("ib-sub").textContent = "事件型数据：一次体测一个截面，不参与日/周/月尺度。";
    $("ib-stamp").innerHTML = "";
    $("ib-grid").innerHTML = '<p class="ib-empty">' +
      "InBody 尚未采集到记录。在体测仪上测一次并同步进 App 后，这里会出现第一份截面。" +
      "</p>";
    return;
  }

  var L = d.latest || {};
  zone.hidden = false;
  $("ib-sub").textContent =
    "事件型数据：一次体测一个截面，不参与日/周/月尺度——所以它不随上面的日期与尺度变化。";

  var ageTxt = d.age_days == null ? "未知"
    : (d.age_days === 0 ? "今天测得" : "距今 " + d.age_days + " 天");
  $("ib-stamp").innerHTML =
    "<b>" + esc((L.datetime || "").replace("T", " ").slice(5, 16)) + "</b>" +
    "InBody " + esc(d.equipment || "") + " · 机身 " + esc(d.serial || "—") +
    '<span class="ib-age' + (d.stale ? " stale" : "") + '">' + esc(ageTxt) + "</span>";

  var trendNote = d.scan_count >= 2
    ? "已有 " + d.scan_count + " 次扫描。"
    : "账号下仅 1 次扫描——<b>现在是截面，不是趋势</b>；第二次同口径测量之后才谈变化。";

  $("ib-grid").innerHTML =
    '<div class="ib-grid">' + splitCard(d) + metricsCard(d) + "</div>" +
    '<div class="ib-grid">' + targetCard(d) + segCard(d) + "</div>" +
    chips(d) +
    '<p class="ib-caveats">' + trendNote + "</p>" +
    '<p class="ib-caveats">' + (d.caveats || []).map(function (c) {
      return "<span>" + esc(c) + "</span>";
    }).join("") + "</p>";
}

/* main.js 在 init 后注入数据（避免模块间循环依赖） */
var _data = null;
export function setInBody(d) { _data = d; }
