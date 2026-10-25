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
} from "./dateutil.js?v=1c2a93bd";

export var SCALES = [
  { key: "day", label: "日", unit: "天", seriesN: 30, seriesLabel: "近 30 天" },
  { key: "week", label: "周", unit: "周", seriesN: 12, seriesLabel: "近 12 周" },
  { key: "month", label: "月", unit: "月", seriesN: 12, seriesLabel: "近 12 个月" }
];

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
