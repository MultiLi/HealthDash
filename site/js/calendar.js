/* 日历：全局时间轴控件。**同一个日历，尺度决定它选中什么**——
   日尺度选中一格、周尺度选中一行、月尺度则是整月在范围内。
   这就是「多尺度是一种形式」的落点：不是另开一个多尺度区块，
   而是同一个控件用不同粒度去选。

   格内：日期 + 综合指数分档徽标 + 包边色条 + 四个达标点
   （实心绿=达标 / 实心琥珀=未达标 / 空心灰=该维度当日无实测）。
   数据满 60 天后自动出现长周期热力条。 */
import { bandOf, BAND, CHECKS } from "./constants.js?v=1abf30bd";
import { esc } from "./utils.js?v=1abf30bd";
import { rowChecks } from "./aggregate.js?v=1abf30bd";
import { monthMatrix, weekRangeLabel, addDays, fmtMD, weekStart } from "./dateutil.js?v=1abf30bd";

var WD = ["一", "二", "三", "四", "五", "六", "日"];

function dotsHtml(row, ix) {
  if (!row) return '<span class="cal-dots"><i class="na"></i><i class="na"></i><i class="na"></i><i class="na"></i></span>';
  var st = rowChecks(row, ix);
  return '<span class="cal-dots">' + CHECKS.map(function (c) {
    return '<i class="' + st[c.key] + '"></i>';
  }).join("") + "</span>";
}

function cellHtml(cell, ctx) {
  var row = ctx.byDate[cell.date];
  var inScope = ctx.scopeDates && ctx.scopeDates.indexOf(cell.date) >= 0;
  var cls = ["cal-cell"];
  if (!cell.inMonth) cls.push("out");
  if (!row) cls.push("nodata");
  if (cell.date === ctx.today && row) cls.push("today");
  if (inScope) cls.push("in-scope");
  if (cell.date === ctx.anchor) cls.push("anchored");
  var band = row ? bandOf(row.score, ctx.ix.baselines) : "none";
  cls.push("b-" + band);
  var style = row ? ' style="--bc:' + BAND[band] + '"' : "";
  return '<button type="button" class="' + cls.join(" ") + '" data-date="' + cell.date + '"' + style + ">" +
    '<span class="cal-top"><b class="cal-day">' + cell.day + "</b>" +
    (row && row.score != null ? '<span class="cal-score">' + row.score + "</span>" : "") +
    "</span>" +
    dotsHtml(row, ctx.ix) +
    "</button>";
}

/* month: 'YYYY-MM'；ctx: {ix, byDate, today, scale, anchor, scopeDates} */
export function renderMonth(month, ctx) {
  var cells = monthMatrix(month);
  var weeks = [];
  for (var i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
  var inner = '<div class="cal-grid">' +
    '<div class="cal-head">' + WD.map(function (w) { return "<span>" + w + "</span>"; }).join("") + "</div>" +
    weeks.map(function (w) {
      return '<div class="cal-week">' + w.map(function (c) { return cellHtml(c, ctx); }).join("") + "</div>";
    }).join("") +
    "</div>";
  return inner;
}

/* 长周期热力条：按周分列、每列 7 格（贡献图式）。数据量到半年以上才真正开始说话。 */
export function renderHeatstrip(rows, ctx) {
  if (!rows.length) return "";
  var byDate = ctx.byDate;
  var start = weekStart(rows[0].date);
  var end = rows[rows.length - 1].date;
  var cols = [];
  var cur = start;
  while (cur <= end) {
    var col = [];
    for (var i = 0; i < 7; i++) col.push({ date: addDays(cur, i) });
    cols.push({ start: cur, cells: col });
    cur = addDays(cur, 7);
  }
  return '<div class="heat-strip">' +
    cols.map(function (c) {
      return '<span class="heat-col" title="周 ' + weekRangeLabel(c.start) + '">' +
        c.cells.map(function (x) {
          var row = byDate[x.date];
          var band = row ? bandOf(row.score, ctx.ix.baselines) : "none";
          var inScope = ctx.scopeDates && ctx.scopeDates.indexOf(x.date) >= 0;
          return '<i class="heat-cell b-' + band + (x.date === ctx.anchor ? " sel" : "") +
            (!row ? " empty" : "") + (inScope ? " in-scope" : "") + '" style="--bc:' + BAND[band] + '"></i>';
        }).join("") + "</span>";
    }).join("") +
    "</div>" +
    '<div class="heat-axis"><span>' + fmtMD(rows[0].date) + "</span><span>" + fmtMD(end) + "</span></div>";
}
