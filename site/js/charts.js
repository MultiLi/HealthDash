/* SVG 图表组件。
   单日页细分：折线 / 睡眠时间轴 / 堆叠条 / 逐时柱 / 范围条。
   指标卡序列：sparkline——x 轴粒度跟随全局尺度（日/周/月），
   同一个量在同一张卡上换粒度，而不是拆到不同页面。 */
import { C } from "./constants.js?v=ceda129d";
import { esc } from "./utils.js?v=ceda129d";

/* 折线图：pts [[label, v], ...]，band [lo,hi] 画参考区间 */
export function lineChart(pts, o) {
  o = o || {};
  if (!pts || !pts.length) return "";
  var W = o.w || 340, H = o.h || 130, P = 34;
  var vals = pts.map(function (p) { return p[1]; }).filter(function (v) { return v != null; });
  if (!vals.length) return "";
  var min = o.min != null ? o.min : Math.min.apply(null, vals);
  var max = o.max != null ? o.max : Math.max.apply(null, vals);
  var span = max - min || 1;
  min -= span * 0.08; max += span * 0.08; span = max - min;
  var n = pts.length;
  var x = function (i) { return P + (W - 2 * P) * (n === 1 ? 0.5 : i / (n - 1)); };
  var y = function (v) { return H - P - (H - 2 * P) * ((v - min) / span); };
  var s = '<svg viewBox="0 0 ' + W + " " + H + '" class="detail-chart">';
  if (o.band) {
    var y1 = y(Math.min(o.band[1], max)), y2 = y(Math.max(o.band[0], min));
    s += '<rect x="' + P + '" y="' + y1.toFixed(1) + '" width="' + (W - 2 * P) + '" height="' + Math.max(2, y2 - y1).toFixed(1) + '" fill="#10B981" opacity=".08"/>';
    s += '<line x1="' + P + '" x2="' + (W - P) + '" y1="' + y1.toFixed(1) + '" y2="' + y1.toFixed(1) + '" stroke="#10B981" stroke-dasharray="3 4" opacity=".4"/>';
    s += '<line x1="' + P + '" x2="' + (W - P) + '" y1="' + y2.toFixed(1) + '" y2="' + y2.toFixed(1) + '" stroke="#10B981" stroke-dasharray="3 4" opacity=".4"/>';
  }
  s += '<text class="axis-val" x="' + (P - 6) + '" y="' + (y(min + span * 0.08) + 4) + '" text-anchor="end">' + Math.round(min + span * 0.08) + "</text>";
  s += '<text class="axis-val" x="' + (P - 6) + '" y="' + (y(max - span * 0.08) + 4) + '" text-anchor="end">' + Math.round(max - span * 0.08) + "</text>";
  var d = "", started = false;
  pts.forEach(function (p, i) {
    if (p[1] == null) return;
    d += (started ? "L" : "M") + x(i).toFixed(1) + " " + y(p[1]).toFixed(1);
    started = true;
  });
  s += '<path d="' + d + '" fill="none" stroke="' + (o.color || C.green) + '" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>';
  if (n <= 14) {
    pts.forEach(function (p, i) {
      if (p[1] == null) return;
      s += '<circle cx="' + x(i).toFixed(1) + '" cy="' + y(p[1]).toFixed(1) + '" r="3" fill="' + (o.color || C.green) + '"/>';
    });
  }
  var idxs = n > 10 ? [0, Math.floor((n - 1) / 2), n - 1] : pts.map(function (_, i) { return i; });
  idxs.forEach(function (i) {
    s += '<text class="axis-lab" x="' + x(i).toFixed(1) + '" y="' + (H - 8) + '" text-anchor="middle">' + esc(pts[i][0]) + "</text>";
  });
  return s + "</svg>";
}

/* 小倍数单元：单元序列 + 目标参考线/带，点按达标着色。
   points 由 aggregate.seriesPoints 生成：[{label, v, ok, partial}]。
   x 轴粒度跟随全局尺度——日尺度是逐日、周尺度是逐周、月尺度是逐月，
   所以同一张卡在三个尺度下是同一个量的三种粒度，不需要另开页面。 */
export function sparkline(points, m, ix, o) {
  o = o || {};
  var W = 320, H = 92, PL = 34, PR = 12, PT = 12, PB = 16;
  var n = points.length;
  var present = points.map(function (p) { return p.v; }).filter(function (v) { return v != null; });
  if (!present.length) return '<p class="mini-note">该范围内无实测值。</p>';

  var lo = Math.min.apply(null, present), hi = Math.max.apply(null, present);
  var refs = [];
  if (m.dir === "band") { var b = m.band(ix); refs = [b.low, b.high]; }
  else if (m.dir) { refs = [m.goal(ix)]; }
  refs.forEach(function (g) { if (g != null) { lo = Math.min(lo, g); hi = Math.max(hi, g); } });
  if (hi === lo) hi = lo + 1;
  var span = hi - lo;
  lo -= span * 0.14; hi += span * 0.14; span = hi - lo;

  var X = function (i) { return PL + (W - PL - PR) * (n === 1 ? 0.5 : i / (n - 1)); };
  var Y = function (v) { return H - PB - (H - PT - PB) * ((v - lo) / span); };
  var s = '<svg viewBox="0 0 ' + W + " " + H + '" class="spark">';

  if (m.dir === "band") {
    var bb = m.band(ix);
    var gy1 = Y(bb.high), gy2 = Y(bb.low);
    s += '<rect x="' + PL + '" y="' + gy1.toFixed(1) + '" width="' + (W - PL - PR) + '" height="' + Math.max(2, gy2 - gy1).toFixed(1) + '" fill="#10B981" opacity=".10"/>';
  } else if (m.dir) {
    var g = m.goal(ix);
    if (g != null) {
      s += '<line x1="' + PL + '" x2="' + (W - PR) + '" y1="' + Y(g).toFixed(1) + '" y2="' + Y(g).toFixed(1) +
        '" stroke="#9CA3AF" stroke-width="1" stroke-dasharray="4 4"/>';
    }
  }

  /* 折线 + 淡填充：长序列下比逐点柱子好读一个量级 */
  var d = "", started = false, first = null, last = null;
  points.forEach(function (p, i) {
    if (p.v == null) return;
    var seg = (started ? "L" : "M") + X(i).toFixed(1) + " " + Y(p.v).toFixed(1);
    d += seg;
    if (!started) first = i;
    started = true; last = i;
  });
  if (started && last > first) {
    s += '<path d="' + d + "L" + X(last).toFixed(1) + " " + (H - PB) + "L" + X(first).toFixed(1) + " " + (H - PB) + 'Z" fill="' + C.green + '" opacity=".07"/>';
  }
  s += '<path d="' + d + '" fill="none" stroke="' + C.green + '" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>';

  /* 数据点：达成绿 / 未达成琥珀 / 无判定灰；进行中的单元画空心 */
  points.forEach(function (p, i) {
    if (p.v == null) return;
    var col = p.ok === null ? C.gray : p.ok ? "#10B981" : "#F59E0B";
    var r = n > 40 ? 1.8 : 2.8;
    if (p.partial) {
      s += '<circle cx="' + X(i).toFixed(1) + '" cy="' + Y(p.v).toFixed(1) + '" r="' + (r - 0.6) + '" fill="#fff" stroke="' + col + '" stroke-width="1.6"/>';
    } else {
      s += '<circle cx="' + X(i).toFixed(1) + '" cy="' + Y(p.v).toFixed(1) + '" r="' + r + '" fill="' + col + '"/>';
    }
  });

  /* 当前单元（最后一个点）的定位线 */
  if (started) {
    s += '<line x1="' + X(last).toFixed(1) + '" x2="' + X(last).toFixed(1) + '" y1="' + PT +
      '" y2="' + (H - PB) + '" stroke="#10B981" stroke-width="1" opacity=".28"/>';
  }

  s += '<text class="axis-lab" x="' + PL + '" y="' + (H - 3) + '" text-anchor="start">' + esc(points[0].label) + "</text>";
  if (n > 1) {
    s += '<text class="axis-lab" x="' + (W - PR) + '" y="' + (H - 3) + '" text-anchor="end">' + esc(points[n - 1].label) + "</text>";
  }
  return s + "</svg>";
}

/* 睡眠结构时间轴：segs [startMin, endMin, level]，0深 1浅 2REM 3清醒 */
export function hypnoSvg(segs) {
  if (!segs || !segs.length) return "";
  var W = 340, H = 132, L = 44, P = 14;
  var total = segs[segs.length - 1][1] || 1;
  var rows = [3, 2, 1, 0], names = { 3: "清醒", 2: "REM", 1: "浅睡", 0: "深睡" };
  var rowColor = { 0: C.deep, 1: C.light, 2: C.rem, 3: C.awake };
  var y = function (l) { return 10 + rows.indexOf(l) * 26; };
  var x = function (m) { return L + (W - L - P) * (m / total); };
  var s = '<svg viewBox="0 0 ' + W + " " + H + '" class="detail-chart">';
  rows.forEach(function (l) {
    s += '<text class="axis-lab" x="' + (L - 6) + '" y="' + (y(l) + 9) + '" text-anchor="end">' + names[l] + "</text>";
  });
  segs.forEach(function (g) {
    var x1 = x(g[0]), x2 = Math.max(x1 + 1.2, x(g[1]));
    s += '<rect x="' + x1.toFixed(1) + '" y="' + (y(g[2]) + 3) + '" width="' + (x2 - x1).toFixed(1) +
         '" height="13" rx="3" fill="' + rowColor[g[2]] + '" opacity=".9"/>';
  });
  [0, 120, 240, 360, 480].forEach(function (m) {
    if (m > total) return;
    s += '<text class="axis-lab" x="' + x(m).toFixed(1) + '" y="' + (H - 4) + '" text-anchor="middle">' + (m / 60) + "h</text>";
  });
  return s + "</svg>";
}

/* 堆叠条 + 图例：segs [{v,color,label}] */
export function stackBar(segs, total) {
  total = total || segs.reduce(function (a, b) { return a + (b.v || 0); }, 0) || 1;
  var s = '<div class="stack-bar">';
  segs.forEach(function (g) {
    if (!g.v) return;
    s += '<span style="width:' + (g.v / total * 100).toFixed(2) + "%;background:" + g.color + '"></span>';
  });
  s += '</div><div class="stack-legend">';
  segs.forEach(function (g) {
    s += '<span><i style="background:' + g.color + '"></i>' + esc(g.label) + "</span>";
  });
  return s + "</div>";
}

/* 24 小时逐时步数柱：hourly [[hour, v], ...] */
export function hourBars(hourly) {
  if (!hourly || !hourly.length) return "";
  var W = 340, H = 118, L = 16, P = 10;
  var maxV = Math.max.apply(null, hourly.map(function (p) { return p[1]; })) || 1;
  var bw = (W - L - P) / 24;
  var y = function (v) { return H - 18 - (H - 32) * (v / maxV); };
  var s = '<svg viewBox="0 0 ' + W + " " + H + '" class="detail-chart">';
  hourly.forEach(function (p) {
    var h = p[0], v = p[1];
    if (!v) return;
    s += '<rect x="' + (L + h * bw + 1.5).toFixed(1) + '" y="' + y(v).toFixed(1) + '" width="' + Math.max(2, bw - 3).toFixed(1) +
         '" height="' + (H - 18 - y(v)).toFixed(1) + '" rx="2.5" fill="' + (v >= 300 ? C.green : C.border) + '"/>';
  });
  [0, 6, 12, 18, 23].forEach(function (h) {
    s += '<text class="axis-lab" x="' + (L + h * bw + bw / 2).toFixed(1) + '" y="' + (H - 4) + '" text-anchor="middle">' + h + "时</text>";
  });
  return s + "</svg>";
}

/* 范围条：lo–hi 百分比位置 + 颜色 */
export function rangeBar(lo, hi, color) {
  return '<div class="range-bar"><div class="range-fill" style="margin-left:' + lo + "%;width:" + Math.max(1.5, hi - lo) + "%;background:" + color + '"></div></div>' +
    '<div class="range-lab"><span>0</span><span>100</span></div>';
}
