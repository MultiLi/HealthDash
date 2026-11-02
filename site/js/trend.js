/* 趋势线 = 两个独立参数：**范围（看多久）× 粒度（每个点多长）**。
 *
 * 这两个参数和「观测尺度」是三件不同的事：
 *   观测尺度   决定当前单元是日 / 周 / 月——主值、判定、环比按那个单元算；
 *   趋势范围   决定这张图往回看多久；
 *   趋势粒度   决定图上一个点代表多久。
 *
 * 早先把「粒度」写死在尺度定义里（选了月就画 12 个「月点」），等于让两个
 * 独立参数互相绑死，后果全都能一眼看见：
 *   · 数据撑不住粒度：项目只有 3 个月，「月线」就 3 个点，横铺全宽＝几段直线；
 *   · 同一指标在周面板与月面板上是两张互不相干的图：起止不同、粒度不同、
 *     连 y 轴都对不上，根本没法对照——看起来就像「实现对不齐」。
 *
 * 现在两者正交：范围可换、粒度可换、二者任意组合；观测尺度怎么切都不影响这张图。
 */
import { addDays, isoWeekKey, monthKey, fmtMD } from "./dateutil.js?v=913e3caa";
import { metricValue, rowsWith, judgeValue, unitCell, cellJudge } from "./aggregate.js?v=913e3caa";

/* 范围：往回看多久。days=0 表示「全部」（从最早一天算起）。 */
export var TREND_SPANS = [
  { key: "14d", label: "近 2 周", days: 14 },
  { key: "30d", label: "近 1 月", days: 30 },
  { key: "90d", label: "近 3 月", days: 90 },
  { key: "365d", label: "近 1 年", days: 365 },
  { key: "all", label: "全部", days: 0 }
];

/* 粒度：一个点代表多久。 */
export var TREND_GRAINS = [
  { key: "day", label: "逐日" },
  { key: "week", label: "逐周" },
  { key: "month", label: "逐月" }
];

export function spanDays(span) {
  var s = TREND_SPANS.filter(function (x) { return x.key === span; })[0];
  return s ? s.days : 30;
}

/* 范围解析成 [start, end]。end 取锚点日期（看历史某天时，趋势截至那天），
   但不超过数据最后一天——否则未来日期会把窗口整体推出数据区。 */
export function trendRange(ix, anchor, span) {
  var end = anchor;
  if (!end || end > ix.default_day) end = ix.default_day;
  var n = spanDays(span);
  var start = n ? addDays(end, -(n - 1)) : ((ix.span && ix.span.first) || end);
  return { start: start, end: end };
}

/* 点标签：只在首尾显示，所以取最短且不歧义的写法。 */
function labelOf(key, grain) {
  if (grain === "week") return "W" + String(key).slice(-2);
  if (grain === "month") return parseInt(String(key).slice(5, 7), 10) + " 月";
  return fmtMD(key);
}

/* 范围 + 粒度 -> 点序列 [{label, v, ok, partial, n}]。
 * 值按「范围内、该粒度分组内的实测日」聚合（agg 决定均值还是中位数）。
 * 判定优先用后端 day 单元（吃动态阈值），后端没有才现算——与卡片主值同一纪律。 */
export function trendPoints(ix, byDate, m, anchor, span, grain) {
  var r = trendRange(ix, anchor, span);
  var keys = [], buf = {};
  var d = r.start;
  var guard = 0;
  while (d <= r.end && guard++ < 4000) {
    var row = byDate[d];
    if (row) {
      var k = grain === "week" ? isoWeekKey(d)
        : grain === "month" ? monthKey(d) : d;
      if (!buf[k]) { buf[k] = []; keys.push(k); }
      buf[k].push(row);
    }
    d = addDays(d, 1);
  }
  return keys.map(function (k) {
    var rows = buf[k];
    var usable = rowsWith(rows, m);
    var v = usable.length ? metricValue(usable, m) : null;
    var ok = null;
    if (v != null) {
      /* 逐日粒度直接吃后端 day 单元（动态阈值逐日变，重判会失真）；
         周/月粒度是「范围切片」，后端单元可能不是同一段日子，必须现算。 */
      var cell = grain === "day" ? unitCell(ix, "day", k, m) : null;
      var j = cell ? cellJudge(cell) : judgeValue(v, m, ix);
      ok = j === "na" ? null : j;
    }
    return {
      label: labelOf(k, grain), v: v, ok: ok,
      /* 末端分组可能还没结束（今天、本周、本月）→ 空心点，别当成完整单元 */
      partial: k === (grain === "week" ? isoWeekKey(r.end)
        : grain === "month" ? monthKey(r.end) : r.end),
      n: usable.length
    };
  });
}
