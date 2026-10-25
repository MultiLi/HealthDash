/* 日期工具：一律按本地日期字符串 YYYY-MM-DD 运算，不碰时区。
   周从周一起始（与 Garmin / 国内习惯一致），ISO 周编号。 */

export function parseD(s) {
  var p = String(s).split("-");
  return new Date(+p[0], +p[1] - 1, +p[2]);
}

export function isoD(d) {
  var m = d.getMonth() + 1, dd = d.getDate();
  return d.getFullYear() + "-" + (m < 10 ? "0" : "") + m + "-" + (dd < 10 ? "0" : "") + dd;
}

export function addDays(s, n) {
  var d = parseD(s);
  d.setDate(d.getDate() + n);
  return isoD(d);
}

export function monthKey(s) { return String(s).slice(0, 7); }

export function monthLabel(key) {
  return key.slice(0, 4) + " 年 " + parseInt(key.slice(5, 7), 10) + " 月";
}

export function shiftMonth(key, n) {
  var y = +key.slice(0, 4), m = +key.slice(5, 7) - 1 + n;
  var d = new Date(y, m, 1);
  return d.getFullYear() + "-" + (d.getMonth() + 1 < 10 ? "0" : "") + (d.getMonth() + 1);
}

export function fmtMD(s) { return s.slice(5, 7) + "/" + s.slice(8, 10); }

export function weekdayShort(s) {
  return "一二三四五六日"[parseD(s).getDay() === 0 ? 6 : parseD(s).getDay() - 1];
}

/* 该日所在周的周一 */
export function weekStart(s) {
  var d = parseD(s);
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
  return isoD(d);
}

/* 该周的 7 天 */
export function weekDates(s) {
  var mon = weekStart(s), out = [];
  for (var i = 0; i < 7; i++) out.push(addDays(mon, i));
  return out;
}

/* ISO 周编号 '2026-W37' */
export function isoWeekKey(s) {
  var d = parseD(s);
  var t = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  t.setDate(t.getDate() - ((t.getDay() + 6) % 7) + 3);   // 移到本周四
  var ft = new Date(t.getFullYear(), 0, 4);
  ft.setDate(ft.getDate() - ((ft.getDay() + 6) % 7) + 3);
  var wk = 1 + Math.round((t - ft) / (7 * 86400000));
  return t.getFullYear() + "-W" + (wk < 10 ? "0" : "") + wk;
}

/* 月历矩阵：6 行 × 7 列（末行整行不在当月则裁掉），周一为首列 */
export function monthMatrix(key) {
  var y = +key.slice(0, 4), m = +key.slice(5, 7);
  var first = new Date(y, m - 1, 1);
  var off = (first.getDay() + 6) % 7;
  var cells = [];
  for (var i = 0; i < 42; i++) {
    var dt = new Date(y, m - 1, 1 - off + i);
    cells.push({ date: isoD(dt), inMonth: dt.getMonth() === m - 1, day: dt.getDate() });
  }
  var last = cells.slice(35);
  if (last.every(function (c) { return !c.inMonth; })) cells = cells.slice(0, 35);
  return cells;
}

/* 周区间文案 '09/08–09/14' */
export function weekRangeLabel(s) {
  var mon = weekStart(s);
  return fmtMD(mon) + "–" + fmtMD(addDays(mon, 6));
}

export function weekdayCN(s) {
  return "周" + weekdayShort(s);
}
