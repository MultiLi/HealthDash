/* 日历：全局时间轴控件。**同一个日历，尺度决定它选中什么**——
   日尺度选中一格、周尺度选中一行、月尺度则是整月在范围内。
   这就是「多尺度是一种形式」的落点：不是另开一个多尺度区块，
   而是同一个控件用不同粒度去选。

   格内：日期 + 综合指数分档徽标 + 包边色条 + 四个达标点
   （实心绿=达标 / 实心琥珀=未达标 / 空心灰=该维度当日无实测）。
   数据满 60 天后自动出现长周期热力条。 */
import { bandOf, BAND, CHECKS } from "./constants.js?v=ae331b07";
import { esc } from "./utils.js?v=ae331b07";
import { rowChecks } from "./aggregate.js?v=ae331b07";
import { monthMatrix, weekRangeLabel, addDays, fmtMD, weekStart } from "./dateutil.js?v=ae331b07";

var WD = ["一", "二", "三", "四", "五", "六", "日"];

/* 整格无数据时**不画**四个空心点：那四个点说的是「这四项都没有实测」，
   而在一个根本没有采集记录的日子里，这句话没有信息量——它只是把空格
   填满，让整张月历看起来像有事发生。空就是空。
   （有行、只是某项缺测的日子仍然画：那时「哪一项缺」是有意义的。） */
function dotsHtml(row, ix) {
  if (!row) return "";
  var st = rowChecks(row, ix);
  return '<span class="cal-dots">' + CHECKS.map(function (c) {
    return '<i class="' + st[c.key] + '"></i>';
  }).join("") + "</span>";
}

/* 采集状态 -> 视觉与话术（2026-11-01）。
 *
 * 以前只有一种「空心灰」表示无数据，于是**三种完全不同的情况长得一样**：
 *   采集失败（端点报错）/ 原始数据没落盘（采集没跑）/ 设备没推数（没戴表）。
 * 把系统故障画成人的行为，是这一版要修的核心问题——归因错了，
 * 「我最近是不是懈怠了」这个判断从第一眼就跟着错。
 *
 * 状态本身由后端下发（row.cs，唯一口径），前端只负责翻译，不重新推断。
 * 视觉上只分三档（灰 / 斜纹 / 琥珀），因为日历格只有 74px：
 * 再细的区分写在 title 里，悬停即见，不靠颜色猜。 */
var CS_TEXT = {
  failed: "采集失败",
  missing: "无原始数据（采集未运行）",
  absent: "无佩戴记录",
  pending: "今日采集未完成"
};

function csNote(row) {
  var cs = row && row.cs;
  if (!cs || cs === "ok") return "";
  var t = CS_TEXT[cs] || "";
  if (cs === "failed" && row.ce && row.ce.length) t += "：" + row.ce.join("、");
  return t;
}

function cellHtml(cell, ctx) {
  var row = ctx.byDate[cell.date];
  var inScope = ctx.scopeDates && ctx.scopeDates.indexOf(cell.date) >= 0;
  /* 索引里没有这一行 = 不在数据跨度内（未来日 / 超出记录范围）。
     它和「有行但没数」是两回事，但对用户都是「这天没东西可看」，
     所以视觉上并入无数据，title 说实话。 */
  var cs = row ? (row.cs || "ok") : "outside";
  var cls = ["cal-cell"];
  if (!cell.inMonth) cls.push("out");
  if (cs !== "ok") cls.push("nodata");
  if (cs === "failed") cls.push("cs-failed");
  else if (cs === "missing") cls.push("cs-missing");
  else if (cs === "pending") cls.push("cs-pending");
  if (cell.date === ctx.today && row) cls.push("today");
  if (inScope) cls.push("in-scope");
  if (cell.date === ctx.anchor) cls.push("anchored");
  /* 有判读的日给一条底边线（2026-11-01）：判读是「这一天发生过判断」的证据，
     日历上却完全看不出来——想知道哪天有结论只能一天天点。
     用底边而不是角标，是因为右上角已经被分数占了、底部又被四个达标点占了。 */
  if (row && row.has_brief) cls.push("has-brief");
  var band = row ? bandOf(row.score, ctx.ix.baselines) : "none";
  cls.push("b-" + band);
  var style = row ? ' style="--bc:' + BAND[band] + '"' : "";
  var note = csNote(row);
  var title = cell.date + (note ? " · " + note : "");
  return '<button type="button" class="' + cls.join(" ") + '" data-date="' + cell.date +
    '" data-cs="' + cs + '" title="' + esc(title) + '"' + style + ">" +
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

  /* 整周都在今天之后 = 还没到，**压扁不删**。
     月初打开时月历有一半是空壳，那是给网格凑整齐的占位，不是排期。
     但也不能删——删掉之后「一个月」的形状就没了，两行的日历看起来像周视图。
     压到只剩日期数字（CSS .is-future），既保住月形，又把一半的高度还回来。 */
  var tod = ctx.today || "";
  var inner = '<div class="cal-grid">' +
    '<div class="cal-head">' + WD.map(function (w) { return "<span>" + w + "</span>"; }).join("") + "</div>" +
    weeks.map(function (w) {
      var future = tod && w.every(function (c) { return c.date > tod; });
      return '<div class="cal-week' + (future ? " is-future" : "") + '">' +
        w.map(function (c) { return cellHtml(c, ctx); }).join("") + "</div>";
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
