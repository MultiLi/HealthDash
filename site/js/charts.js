/* SVG 图表组件。
   单日页细分：折线 / 睡眠时间轴 / 堆叠条 / 逐时柱 / 范围条。
   指标卡序列：sparkline——x 轴粒度跟随全局尺度（日/周/月），
   同一个量在同一张卡上换粒度，而不是拆到不同页面。 */
import { C } from "./constants.js?v=ae331b07";
import { esc } from "./utils.js?v=ae331b07";

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
  if (!points || !points.length) return '<p class="mini-note">该范围内无实测值。</p>';

  /* **x 轴只覆盖真正有数据的区间。**
     原来的写法把「近 12 个月」12 个槽位全画出来，而项目只跑了两个月：于是轴上
     写着「2025 年 11 月 → 2026 年 10 月」，一条线缩在最右边两个点上——
     轴在宣称跨越一年，图里只有两个月，读的人只会觉得「粒度不对」。
     两头空槽没有信息量，裁掉；真实跨度的起止才该出现在轴上。 */
  var i0 = 0, i1 = points.length - 1;
  while (i0 <= i1 && points[i0].v == null) i0++;
  while (i1 >= i0 && points[i1].v == null) i1--;
  if (i1 < i0) return '<p class="mini-note">该范围内无实测值。</p>';
  var pts = points.slice(i0, i1 + 1);

  var n = pts.length;
  var present = pts.map(function (p) { return p.v; }).filter(function (v) { return v != null; });

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
  pts.forEach(function (p, i) {
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
  pts.forEach(function (p, i) {
    if (p.v == null) return;
    var col = p.ok === null ? C.gray : p.ok ? "#10B981" : "#F59E0B";
    var r = n > 40 ? 1.8 : 2.8;
    if (p.partial) {
      s += '<circle cx="' + X(i).toFixed(1) + '" cy="' + Y(p.v).toFixed(1) + '" r="' + (r - 0.6) + '" fill="#fff" stroke="' + col + '" stroke-width="1.6"/>';
    } else {
      s += '<circle cx="' + X(i).toFixed(1) + '" cy="' + Y(p.v).toFixed(1) + '" r="' + r + '" fill="' + col + '"/>';
    }
  });

  /* 当前单元（最后一个点）的定位线。稀疏事件型序列（体成分）关掉它：
     一条近乎水平的线加一根竖直定位线，看起来像「最后一天跳了一下」。 */
  if (started && o.cursor !== false) {
    s += '<line x1="' + X(last).toFixed(1) + '" x2="' + X(last).toFixed(1) + '" y1="' + PT +
      '" y2="' + (H - PB) + '" stroke="#10B981" stroke-width="1" opacity=".28"/>';
  }

  s += '<text class="axis-lab" x="' + PL + '" y="' + (H - 3) + '" text-anchor="start">' + esc(pts[0].label) + "</text>";
  if (n > 1) {
    s += '<text class="axis-lab" x="' + (W - PR) + '" y="' + (H - 3) + '" text-anchor="end">' + esc(pts[n - 1].label) + "</text>";
  }
  s += "</svg>";
  /* 两个点连起来是「一段」，不是「趋势」。低于三个单元就明说，
     与「覆盖不足不给结论」同一条纪律——不拿一条斜线冒充走势。 */
  if (n < 3) {
    s += '<p class="mini-note">只有 ' + n + " 个" + (o.unitWord || "单元") + "有数据，还看不出趋势" +
      (o.finer ? '（' + esc(o.finer) + '）' : '') + "</p>";
  }
  return s;
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

/* ============ 共享时间轴的多带对照（结构 × 行为）============
 *
 * 这张图存在的理由只有一个：**让两个采样频率完全不同的源落在同一根日期轴上。**
 *
 * 体成分是扫描事件（三次），Garmin 是逐日序列（几十天）。它们以前各画一份，
 * 再用一张「同期均值」的表格去连线——那是把两个源**并置**，不是整合。
 * 整合的形态是：同一条 x 轴，四带垂直对齐，扫描日打竖线。
 *
 * **不做双轴叠加。** 把体重和步数塞进同一个坐标系，画图的人可以调出任何想要的
 * "相关性"；各自独立纵轴、只对齐日期，是唯一不撒谎的画法。
 *
 * 采样密度的差异必须看得见：扫描之外没有体重读数，折线就把那一段**跨过去**
 * （不插值、不补点）。缺测日同样断开，不连——跨缺口连线会编出不存在的走势。
 *
 * 竖线用 `vector-effect` 保持线宽：SVG 用 preserveAspectRatio="none" 横向铺满，
 * 不这么做的话线宽会跟着横向拉伸一起变粗。**点不画**——扫描竖线已经标出了实测位置，
 * 而且圆点会被横向拉伸成椭圆。
 */
export function alignBands(block) {
  if (!block || !block.dates || !block.dates.length) return "";
  var rows = [block.structure].concat(block.behaviors || []).filter(Boolean);
  if (!rows.length) return "";

  var dates = block.dates;
  var N = dates.length;
  var idx = {};
  dates.forEach(function (d, i) { idx[d] = i; });

  var W = 1200, PAD = 4;
  function X(i) { return PAD + (W - 2 * PAD) * (N === 1 ? 0.5 : i / (N - 1)); }

  var scans = (block.scan_dates || []).map(function (d) {
    return idx[d] == null ? null : X(idx[d]);
  }).filter(function (v) { return v != null; });

  var out = '<div class="abands">';
  rows.forEach(function (b) {
    var last = b.points && b.points.length ? b.points[b.points.length - 1][1] : null;
    out += '<div class="ab-row" data-key="' + esc(b.key) + '">' +
      '<div class="ab-k"><span>' + esc(b.label) + "</span>" +
        (b.key === "weight_kg" && last != null
          ? "<b>" + fmt(last, b.precision) + "</b><em>" + esc(b.unit) + "</em>" : "") +
      "</div>" +
      '<div class="ab-t">' + bandSvg(b, idx, X, W, scans) + "</div>" +
      "</div>";
  });
  out += axisHtml(dates, scans, X, W) + "</div>";
  return out;
}

function fmt(v, d) {
  if (v == null || isNaN(v)) return "–";
  var p = d == null ? 0 : d;
  var s = Number(v).toFixed(p);
  /* 千分位：步数是五位数字，不分组读不出量级 */
  if (p === 0 && Math.abs(v) >= 1000) {
    return s.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }
  return s;
}

function bandSvg(b, idx, X, W, scans) {
  var H = 56, TOP = 6, BOT = 6;
  var vals = (b.points || []).map(function (p) { return p[1]; })
    .filter(function (v) { return v != null; });
  if (!vals.length) return '<p class="ab-none">无实测</p>';

  var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
  if (b.kind === "bar") {
    /* 柱状图必须从 0 起：柱高是「量」，不是「相对于最小值的偏移」。
       按 min 起画的话，16 步和 11379 步的柱子高度差会被压成不到两倍，
       而真实差距是 700 倍——那是柱状图最常见的撒谎方式。 */
    lo = 0;
  }
  if (hi === lo) hi = lo + 1;
  var sp = hi - lo;
  if (b.kind !== "bar") { lo -= sp * 0.12; }
  hi += sp * 0.12; sp = hi - lo;
  function Y(v) { return H - BOT - (H - TOP - BOT) * ((v - lo) / sp); }

  var s = '<svg viewBox="0 0 ' + W + " " + H + '" class="ab-svg" preserveAspectRatio="none">';
  /* 基准线：没有它，柱状图与折线图的零点看起来一样高 */
  s += '<line x1="0" x2="' + W + '" y1="' + (H - 0.5) + '" y2="' + (H - 0.5) +
    '" stroke="var(--border)" stroke-width="1" vector-effect="non-scaling-stroke"/>';

  if (b.kind === "bar") {
    var bw = Math.max((W - 8) / Math.max(b.points.length, 1) * 0.62, 1);
    (b.points || []).forEach(function (p) {
      var v = p[1];
      if (v == null) return;
      var i = idx[p[0]];
      if (i == null) return;
      var y = Y(v), h = H - BOT - y;
      if (h < 1) h = 1;
      s += '<rect x="' + (X(i) - bw / 2).toFixed(1) + '" y="' + y.toFixed(1) +
        '" width="' + bw.toFixed(1) + '" height="' + h.toFixed(1) +
        '" rx="1" fill="#10B981" opacity=".52"/>';
    });
  } else {
    /* 断点：缺测日不连。`started` 在缺口后复位，下一段重新起笔。 */
    var d = "", pen = false;
    (b.points || []).forEach(function (p) {
      var v = p[1];
      if (v == null) { pen = false; return; }
      var i = idx[p[0]];
      if (i == null) { pen = false; return; }
      d += (pen ? "L" : "M") + X(i).toFixed(1) + " " + Y(v).toFixed(1);
      pen = true;
    });
    if (d) {
      s += '<path d="' + d + '" fill="none" stroke="' + (b.key === "weight_kg" ? "#0E9F6E" : "#38BDF8") +
        '" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"' +
        ' vector-effect="non-scaling-stroke"/>';
    }
  }

  /* 扫描日竖线：贯穿所有带。它就是「实测发生在哪」的唯一标记。 */
  scans.forEach(function (x) {
    s += '<line x1="' + x.toFixed(1) + '" x2="' + x.toFixed(1) + '" y1="0" y2="' + H +
      '" stroke="#10B981" stroke-width="1" stroke-dasharray="3 3" opacity=".38"' +
      ' vector-effect="non-scaling-stroke"/>';
  });
  return s + "</svg>";
}

/* 轴标签：首尾 + 扫描日。**扫描日优先保留**——它是这张图的主语，
   和末日挨得太近时，宁可让「11/02」让步给「10/31」。
   这不是排版细节：扫描日记错位置，整张图就在说另一件事。 */
function axisHtml(dates, scans, X, W) {
  var N = dates.length;
  var w2 = W - 8;
  var lab = function (i) { return dates[i].slice(5).replace("-", "/"); };
  /* 首末两端**永远保留**（它们定义"这段时间从哪天到哪天"），
     扫描日作为可选项插进去——插不下就交给竖线和下面的报告行去说。
     最小间距按**容器宽度**定：390px 屏上 9% 只有 28px，而「10/18」要 34px，
     两个标签会叠成一团黑字，比没有标签更糟。 */
  var vw = (typeof window !== "undefined" && window.innerWidth) || 1440;
  var gap = vw < 680 ? 11 : 6;

  var kept = [{ i: 0, p: 0 }, { i: N - 1, p: 100 }];
  (scans || []).forEach(function (x) {
    var i = Math.round((x - 4) / w2 * (N - 1));
    var p = N === 1 ? 50 : i / (N - 1) * 100;
    var room = kept.every(function (k) { return Math.abs(k.p - p) >= gap; });
    if (room) kept.push({ i: i, p: p, scan: true });
  });
  kept.sort(function (a, b) { return a.p - b.p; });

  return '<div class="ab-x"><div class="ab-x-l"></div><div class="ab-x-r">' +
    kept.map(function (m) {
      return '<span class="' + (m.scan ? "is-scan" : "") + '" style="left:' +
        m.p.toFixed(1) + '%">' + esc(lab(m.i)) + "</span>";
    }).join("") + "</div></div>";
}
