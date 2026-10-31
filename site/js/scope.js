/* 观测范围解析：把 (锚定日期, 尺度) 解析成【单元】。
 *
 * 这是本轮设计的枢纽。尺度不是一个页面上的开关，而是**全局的观测形式**：
 *  - 日尺度：单元 = 一天
 *  - 周尺度：单元 = 一周（周一为首日）
 *  - 月尺度：单元 = 一个自然月
 *
 * 每个指标卡都用同一套单元定义：主值取「当前单元」、环比对象是「上一单元」、
 * 序列是「往前 n 个同粒度单元」。所以换尺度时，整页所有指标在同一个位置
 * 换了口径——概览与某日不是两个页面，是同一个视图的两种尺度。
 *
 * 一个 anchor + 一个 scale 就是一个完整的观测状态，不需要别的东西。
 */
import {
  addDays, weekDates, weekStart, isoWeekKey, weekRangeLabel,
  monthKey, monthLabel, shiftMonth, fmtMD, weekdayCN
} from "./dateutil.js?v=9b687d78";

/* 六个尺度 = 两类单元：
 *  · 自然周期（日 / 周 / 月）：边界由日历决定，用来对齐「本月」「本周」这类复习惯例。
 *  · 滚动窗（7 / 28 / 90 天）：边界由锚点决定，**没有「本周还没结束」的问题**——
 *    自然周的周均在被问到的那一刻必然是被截断的，滚动窗不会。
 *    这也是为什么趋势判定优先看滚动窗：它的相邻两步重叠 6/7 天，环比必须用
 *    非重叠位移差（prevUnitOf 直接跳一整个窗长，见下）。 */
export var SCALES = [
  { key: "day", label: "日", unit: "天", seriesN: 30, seriesLabel: "近 30 天" },
  { key: "week", label: "周", unit: "周", seriesN: 12, seriesLabel: "近 12 周" },
  { key: "month", label: "月", unit: "月", seriesN: 12, seriesLabel: "近 12 个月" },
  { key: "roll7", label: "7天", unit: "窗", seriesN: 12, seriesLabel: "近 12 个 7 天窗" },
  { key: "roll28", label: "28天", unit: "窗", seriesN: 6, seriesLabel: "近 6 个 28 天窗" },
  { key: "roll90", label: "90天", unit: "窗", seriesN: 4, seriesLabel: "近 4 个 90 天窗" }
];

export function rollLen(scale) {
  return scale.indexOf("roll") === 0 ? parseInt(scale.slice(4), 10) : 0;
}

export function scaleDef(key) {
  return SCALES.filter(function (s) { return s.key === key; })[0] || SCALES[0];
}

/* 锚定日期 + 尺度 -> 当前单元 */
export function unitOf(anchor, scale) {
  if (scale === "day") {
    return { scale: scale, key: anchor, label: fmtMD(anchor), sub: weekdayCN(anchor), dates: [anchor] };
  }
  if (scale === "week") {
    return { scale: scale, key: isoWeekKey(anchor), label: weekRangeLabel(anchor),
             sub: "ISO " + isoWeekKey(anchor), dates: weekDates(anchor) };
  }
  var rn = rollLen(scale);
  if (rn) {
    var ds = [];
    for (var j = rn - 1; j >= 0; j--) ds.push(addDays(anchor, -j));
    return {
      scale: scale, key: anchor, label: fmtMD(ds[0]) + "–" + fmtMD(anchor),
      sub: "近 " + rn + " 天 · 滚动窗", dates: ds
    };
  }
  var mk = monthKey(anchor);
  var y = +mk.slice(0, 4), m = +mk.slice(5, 7);
  var n = new Date(y, m, 0).getDate();
  var dates = [];
  for (var i = 1; i <= n; i++) dates.push(mk + "-" + (i < 10 ? "0" : "") + i);
  return { scale: scale, key: mk, label: monthLabel(mk), sub: "整月 · " + n + " 天", dates: dates };
}

/* 上一单元（环比锚点） */
export function prevUnitOf(anchor, scale) {
  if (scale === "day") return unitOf(addDays(anchor, -1), "day");
  if (scale === "week") return unitOf(addDays(anchor, -7), "week");
  /* 滚动窗的环比必须**非重叠**：相邻两步（差 1 天）重叠 6/7，比它们等于比
     「自己和几乎自己」，任何差异都会被重叠部分稀释成 0。
     所以直接跳一整个窗长——7 天窗比的是「这 7 天 vs 前 7 天」。 */
  var rn = rollLen(scale);
  if (rn) return unitOf(addDays(anchor, -rn), scale);
  var mk = monthOf(shiftMonth(monthKey(anchor), -1));
  return unitOf(mk, "month");
}

function monthOf(mk) { return mk + "-01"; }

/* 往前 n 个同粒度单元（升序，含当前单元） */
export function unitSeries(anchor, scale, n) {
  var out = [unitOf(anchor, scale)];
  var cur = anchor;
  for (var i = 1; i < (n || 12); i++) {
    if (scale === "day") cur = addDays(cur, -1);
    else if (scale === "week") cur = addDays(cur, -7);
    else if (rollLen(scale)) cur = addDays(cur, -rollLen(scale));
    else cur = monthOf(shiftMonth(monthKey(cur), -1));
    out.push(unitOf(cur, scale));
  }
  return out.reverse();
}

/* 单元 -> 日期对应的索引行。withOpen=true 时含进行中的当天（日历/主值要用），
   统计口径一律用 withOpen=false。 */
export function unitRows(unit, byDate, withOpen) {
  return unit.dates.map(function (d) { return byDate[d]; })
    .filter(function (r) { return r && (withOpen || !r.morning); });
}

/* 单元里「有实测值的完整日」数——达标统计的分母 */
export function unitBase(unit, byDate) {
  return unitRows(unit, byDate, false).length;
}
