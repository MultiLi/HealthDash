/* 多尺度聚合：逐日索引行 -> 日 / 周 / 月分组 或 滚动窗口切片 -> 统计量。
   所有判好阈值来自 index.json（后端自 rules.json 下发），本文件不硬编码。
   跨日结论（HRV 达标）不重判，只消费后端给的 r.hrv_ok。 */

export function nonNull(v) { return v != null; }

export function mean(vals) {
  var a = vals.filter(nonNull);
  return a.length ? a.reduce(function (x, y) { return x + y; }, 0) / a.length : null;
}

export function median(vals) {
  var a = vals.filter(nonNull).slice().sort(function (x, y) { return x - y; });
  if (!a.length) return null;
  var m = Math.floor(a.length / 2);
  return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
}

export function sum(vals) {
  var a = vals.filter(nonNull);
  return a.length ? a.reduce(function (x, y) { return x + y; }, 0) : null;
}

/* 四项达标判定（与 CHECKS 的键一一对应） */
export function checkFns(ix) {
  return {
    sleep: function (r) { return r.sleep_h != null && r.sleep_h >= ix.goals.sleep_h; },
    steps: function (r) { return r.steps != null && r.steps >= ix.goals.steps; },
    hrv: function (r) { return r.hrv_ok === true; },
    sedentary: function (r) { return r.sedentary_min != null && r.sedentary_min <= ix.goals.sedentary_min; }
  };
}

/* 单行四项达标的三态：ok / no / na（该维度当日无实测值）。
   na 与 no 必须分开——缺数据不等于没做到（判读纪律：不用不存在的证据扣分）。 */
export function rowChecks(r, ix) {
  if (!r) return { sleep: "na", steps: "na", hrv: "na", sedentary: "na" };
  var fn = checkFns(ix), out = {};
  var has = {
    sleep: r.sleep_h != null,
    steps: r.steps != null,
    hrv: r.hrv_ok != null,
    sedentary: r.sedentary_min != null
  };
  Object.keys(fn).forEach(function (k) {
    out[k] = !has[k] ? "na" : (fn[k](r) ? "ok" : "no");
  });
  return out;
}

/* 一组日的聚合统计。rows 为空返回 null。 */
export function aggRows(rows, ix) {
  if (!rows || !rows.length) return null;
  var fn = checkFns(ix);
  var pick = function (k) { return rows.map(function (r) { return r[k]; }); };
  var cnt = function (k) { return rows.filter(fn[k]).length; };
  var n = rows.length;
  var rate = function (k) { return Math.round(cnt(k) / n * 100); };
  return {
    n: n,
    first: rows[0].date,
    last: rows[n - 1].date,
    avg: {
      sleep_h: mean(pick("sleep_h")),
      steps: mean(pick("steps")),
      hrv: mean(pick("hrv")),
      sedentary_min: mean(pick("sedentary_min")),
      stress: mean(pick("stress")),
      active_kcal: mean(pick("active_kcal")),
      score: mean(pick("score"))
    },
    med: { hrv: median(pick("hrv")) },
    total: { steps: sum(pick("steps")) },
    ok: { sleep: cnt("sleep"), steps: cnt("steps"), hrv: cnt("hrv"), sedentary: cnt("sedentary") },
    rate: { sleep: rate("sleep"), steps: rate("steps"), hrv: rate("hrv"), sedentary: rate("sedentary") }
  };
}

/* 一个指标在若干行上的代表值。agg 决定用均值还是中位数——
   HRV 这类偏态指标用中位数，避免单夜暴跌把周/月代表值带跑。 */
export function metricValue(rows, m) {
  if (!rows || !rows.length) return null;
  var vals = rows.map(function (r) { return r[m.key]; }).filter(nonNull);
  if (!vals.length) return null;
  return m.agg === "median" ? median(vals) : mean(vals);
}

/* 单元序列 -> 图用的点序列 [{label, v, ok, partial}]
   统计口径是「该指标在该单元内有实测值」，不是「整行是否完整」——
   进行中的今天仍有完整的昨夜睡眠与 HRV，不该被一并排除；
   它缺失的步数 / 久坐本来就不会进对应指标的分母。 */
export function seriesPoints(units, byDate, m, ix) {
  return units.map(function (u) {
    var rows = rowsWith(unitRowsAll(u, byDate), m);
    var v = metricValue(rows, m);
    return {
      label: u.label,
      v: v,
      ok: judgeValue(v, m, ix),
      partial: rows.some(function (r) { return r.morning; })
    };
  });
}

/* 单元内的全部索引行（含进行中的当天；缺哪些字段由 rowsWith 逐指标过滤） */
export function unitRowsAll(unit, byDate) {
  return unit.dates.map(function (d) { return byDate[d]; }).filter(Boolean);
}

/* 只保留该指标有实测值的行 */
export function rowsWith(rows, m) {
  return rows.filter(function (r) { return r[m.key] != null; });
}

export function judgeValue(v, m, ix) {
  if (v == null || !m.dir) return null;
  if (m.dir === "band") {
    var b = m.band(ix);
    return b ? (v >= b.low && v <= b.high) : null;
  }
  var g = m.goal(ix);
  if (g == null) return null;
  return m.dir === "below" ? v <= g : v >= g;
}

/* 滚动窗口：取末尾 n 天；n<=0 表示全部 */
export function windowRows(rows, n) {
  if (!n || n <= 0 || n >= rows.length) return rows.slice();
  return rows.slice(rows.length - n);
}

/* 尾部滚动均值（含当日，窗口不足时用已有天数） */
export function rollingMean(rows, key, k) {
  return rows.map(function (_, i) {
    var from = Math.max(0, i - k + 1);
    return mean(rows.slice(from, i + 1).map(function (r) { return r[key]; }));
  });
}

/* 线性回归斜率（单位 / 天）——回答「最近在变好还是变坏」。
   数据点少于 3 个时不给结论。 */
export function slope(rows, key) {
  var pts = [];
  rows.forEach(function (r, i) { if (r[key] != null) pts.push([i, r[key]]); });
  if (pts.length < 3) return null;
  var n = pts.length, sx = 0, sy = 0, sxy = 0, sxx = 0;
  pts.forEach(function (p) { sx += p[0]; sy += p[1]; sxy += p[0] * p[1]; sxx += p[0] * p[0]; });
  var den = n * sxx - sx * sx;
  return den === 0 ? null : (n * sxy - sx * sy) / den;
}

/* 按 ISO 周分组（升序） */
export function groupByWeek(rows, weekKeyFn, labelFn) {
  var map = {}, order = [];
  rows.forEach(function (r) {
    var k = weekKeyFn(r.date);
    if (!map[k]) { map[k] = []; order.push(k); }
    map[k].push(r);
  });
  return order.map(function (k) {
    return { key: k, label: labelFn(map[k][0].date), rows: map[k] };
  });
}

/* 按月分组（升序） */
export function groupByMonth(rows, labelFn) {
  var map = {}, order = [];
  rows.forEach(function (r) {
    var k = r.date.slice(0, 7);
    if (!map[k]) { map[k] = []; order.push(k); }
    map[k].push(r);
  });
  return order.map(function (k) {
    return { key: k, label: labelFn(k), rows: map[k] };
  });
}

/* 环比：当前值相对上一期，返回 {delta, pct, dir}；无上期返回 null */
export function delta(cur, prev) {
  if (cur == null || prev == null) return null;
  var d = cur - prev;
  return { delta: d, pct: prev === 0 ? null : Math.round(d / Math.abs(prev) * 100), dir: d > 0 ? "up" : d < 0 ? "down" : "flat" };
}
