/* 主页渲染。**只有一个视图**——因为「概览」与「某日」本来就是同一个东西
   在月尺度与日尺度下的样子。尺度一变，时间轴选中的范围、每张卡的主值口径、
   序列的 x 轴粒度、达标统计的分母，全部一起变。这就是多尺度作为「形式」
   而不是「入口」的意思。
 *
 * 页面自下而上三个层次，每一层都服从全局尺度：
 *   ① 时间轴（同一个日历，尺度决定它选中一格 / 一行 / 整月）
 *   ② 当前单元总览（综合指数 + Garmin 准备度 并排 + 四项达标计数）
 *   ③ 指标网格（8 张同构卡：主值 + 环比 + 趋势线 + 达标统计；日尺度下可展开当日细分）
      趋势线的范围与粒度是两个独立参数（trend.js），与尺度无关——尺度只管主值口径。
 */
import { store } from "./store.js?v=913e3caa";
import { metrics, CHECKS, bandOf, BAND, READY, readyOf } from "./constants.js?v=913e3caa";
import { esc, $ } from "./utils.js?v=913e3caa";
import { metricValue, judgeValue, unitRowsAll, rowsWith,
         unitCell, cellJudge, okCount, VERDICT_TEXT } from "./aggregate.js?v=913e3caa";
import { unitOf, prevUnitOf } from "./scope.js?v=913e3caa";
import { trendPoints } from "./trend.js?v=913e3caa";
import { renderMonth, renderHeatstrip } from "./calendar.js?v=913e3caa";
import { sparkline } from "./charts.js?v=913e3caa";
import { DETAILS, readinessTrail, briefHtml } from "./detail.js?v=913e3caa";
import { ensureShard, dayOf, hasShard } from "./data.js?v=913e3caa";
import { monthLabel, fmtMD, weekdayCN } from "./dateutil.js?v=913e3caa";
import { bodyGroupHtml } from "./inbody.js?v=913e3caa";

function ix() { return store.ix; }

/* 口径文案：同一个指标在三种尺度下叫什么，用户必须一眼看懂 */
function unitNoun(scale) {
  var fixed = { day: "当日", week: "本周", month: "本月" }[scale];
  return fixed || (scale.indexOf("roll") === 0 ? "近 " + scale.slice(4) + " 天" : scale);
}
function aggNoun(scale, m) {
  if (scale === "day") return "当日值";
  return unitNoun(scale) + (m.agg === "median" ? "中位" : "日均");
}

/* ================= ① 时间轴 ================= */
function renderAxis(unit) {
  $("axis-month-label").textContent = monthLabel(store.month);
  document.querySelectorAll("[data-scale]").forEach(function (b) {
    b.classList.toggle("active", b.getAttribute("data-scale") === store.scale);
  });

  /* 月尺度下整月都是范围，逐格高亮没有信息量，故不高亮 */
  var scopeDates = store.scale === "month" ? null : unit.dates;
  var ctx = {
    ix: ix(), byDate: store.byDate, today: ix().today,
    scale: store.scale, anchor: store.anchor, scopeDates: scopeDates
  };
  var html = renderMonth(store.month, ctx);
  /* 长周期热力条要**半年以上**才出现：它和日历说的是同一件事（一天一个色块），
     只是覆盖全部数据而不是一个月。两个月的数据画出来只是把日历再铺一遍，
     多一块、不增信息。阈值按「能不能看出季节性的量级」定，不按「有几天数据」。 */
  if (store.rows.length >= 180) {
    html += '<div class="heat-wrap"><p class="heat-title">长周期热力 · 全部数据，一格一天，颜色为综合指数分档</p>' +
      renderHeatstrip(store.rows, ctx) + "</div>";
  }
  $("axis-cal").innerHTML = html;

  /* 一行讲清「这一档在选什么」，不写教程。操作细节（点哪、比哪段）
     属于用一次就会的事，占版面只会让人跳过整段。 */
  var hint = {
    day: "日尺度：点任意一格即切换当天。",
    week: "周尺度：点任意一格选中它所在的整周。",
    month: "月尺度：整个月是观测范围；点某天可下钻到日尺度。",
    roll7: "近 7 天：从锚点往前数，不受自然周切割；环比比前一个 7 天窗。",
    roll28: "近 28 天：从锚点往前数；环比比前一个 28 天窗。",
    roll90: "近 90 天：低频指标（体重、VO2 Max）才看得清的尺度。"
  }[store.scale];
  $("axis-note").textContent = hint;
}

/* ================= ② 当前单元总览 ================= */
/* 综合指数不混口径：晨间快照是四项合成、完整日是五项合成，
   两者平均到一起没有意义，所以指数只用完整日。 */
var SCORE_M = {
  key: "score", agg: "mean", dir: "above",
  fmt: function (v) { return String(Math.round(v)); },
  goal: function (i) { return i.baselines.score.good_min; }
};

function scoreRows(unit) {
  return unitRowsAll(unit, store.byDate).filter(function (r) { return !r.morning && r.score != null; });
}

/* 四项达标各自用自己的分母：该指标在该单元内有实测值的天数。
   进行中的今天仍有完整睡眠/HRV，会计入；它缺的步数/久坐自然不计入。 */
var CHECK_FIELD = { sleep: "sleep_h", steps: "steps", hrv: "hrv", sedentary: "sedentary_min" };

/* Garmin 准备度：与综合指数并排的第二项分数。单元粒度与其他指标一致——
   日尺度＝当天晨起值，周/月尺度＝日均值（aggregate 的 mean）。 */
var READY_M = { key: "readiness", agg: "mean" };

/* 一项分数的排版单元：大数字 + 名称 + 环比。两项口径不同，故各自带标签与环比，
   绝不合并成一个数——合并等于让「行为达标度」替「恢复准备度」回答。 */
function uscore(value, color, label, chip) {
  return '<div class="uscore">' +
    '<b style="color:' + color + '">' + esc(value) + "</b>" +
    '<span class="uscore-sub"><span class="uscore-lab">' + label + "</span>" + (chip || "") + "</span>" +
    "</div>";
}

function renderOverview(unit) {
  var prev = prevUnitOf(store.anchor, store.scale);
  var score = metricValue(scoreRows(unit), SCORE_M);
  var prevScore = metricValue(scoreRows(prev), SCORE_M);
  /* 先四舍五入到展示值再判分档：否则 74.5 会显示成「75」却按 74.5 判成琥珀，
     数字与颜色当众打架（2026-09-16 实测 9 月均准备度 74.5 → 显示 75 配琥珀）。 */
  var scoreV = score == null ? null : Math.round(score);
  var band = bandOf(scoreV, ix().baselines);
  var bandName = { good: "良好", fair: "一般", low: "待注意", none: "无数据" }[band];

  var closedN = scoreRows(unit).length;
  /* 「进行中 · 当日未结束」只对**今天**成立。过去的空日没有记录就是没有记录，
     标成「进行中」等于暗示它还会长出数据来（2026-09-16 实测：08-31 / 09-01 都被这么错过）。 */
  var note;
  if (store.scale !== "day") {
    note = "完整日 " + closedN + " 天";
  } else if (closedN) {
    note = "完整日";
  } else if (store.anchor === ix().today) {
    note = "进行中 · 当日未结束";
  } else {
    note = "无采集记录";
  }
  $("unit-scope").innerHTML =
    "<b>" + esc(unit.label) + "</b><span>" + esc(unit.sub) + " · " + esc(note) + "</span>";

  var ready = metricValue(rowsWith(unitRowsAll(unit, store.byDate), READY_M), READY_M);
  var prevReady = metricValue(rowsWith(unitRowsAll(prev, store.byDate), READY_M), READY_M);
  /* 设备档位只挂在「某一天」上。周/月是聚合值，没有单一档位可挂——
     此时**不编档位词**，只给数字与颜色，免得让「高」看起来像设备对整周下的判断。 */
  var rIsDay = store.scale === "day";
  var rLevel = rIsDay && store.byDate[store.anchor] ? store.byDate[store.anchor].readiness_level : null;
  var rBand = rLevel && READY[rLevel] ? READY[rLevel] : null;
  var readyV = ready == null ? null : Math.round(ready);
  /* 聚合尺度没有设备档位，颜色按**展示值**分档，与数字保持一致 */
  var rColor = rBand ? rBand.color : readyOf(null, readyV).color;
  var rLabel = "Garmin 准备度" + (rBand ? " · " + esc(rBand.label) : "") +
    "<em>（" + (rIsDay ? "晨起" : "日均") + "）</em>";

  $("unit-score").innerHTML =
    uscore(scoreV == null ? "–" : scoreV, BAND[band],
      "综合指数 · " + bandName,
      rawDeltaChip(score, prevScore, prev.label)) +
    uscore(readyV == null ? "–" : readyV, rColor, rLabel,
      rawDeltaChip(ready, prevReady, prev.label));

  renderReadyTrail();

  /* 四项达标：分子分母都来自**后端逐日判定**（units.day[date].j）。
     以前这里自己拿 goals 比大小，于是睡眠的「设备当晚需求」这类逐日阈值
     永远进不了主页口径——主页与报告一个用 7h、一个用 7.83h。 */
  var ms = metrics(ix());
  $("unit-checks").innerHTML = CHECKS.map(function (c) {
    var m = ms.filter(function (x) { return x.key === CHECK_FIELD[c.key]; })[0];
    var st = m ? okStat(unit, m) : null;
    if (!st) return "";
    var pct = st.tot ? Math.round(st.ok / st.tot * 100) : 0;
    /* 分母为 0 = 该单元内没有可判定的天（覆盖不足 / 进行中），显示「–」而不是 0/0：
       0/0 看起来像「一天都没达标」，那是把「不知道」写成了「没做到」。 */
    var num = st.tot ? st.ok : "–";
    var den = st.tot ? st.tot : "–";
    return '<div class="uc' + (st.tot ? "" : " uc-na") + '"><span class="uc-k">' + esc(c.label) + '</span>' +
      '<span class="uc-v">' + num + '<i>/' + den + '</i></span>' +
      '<span class="uc-bar"><i style="width:' + pct + '%"></i></span></div>';
  }).join("");

  /* ②b：这一段的形状。挂在总览末尾——它说的是「当前这个单元」的性质，
     和上面的综合指数、四项达标是同一层的问题，不是新板块。 */
  renderShape(unit);
}

/* ================= ②b 这一段的形状（窗口结论）=================
 * 卡片回答「这个单元的值是多少」，判读区回答「这一天怎么样」，中间缺一句
 * 「这一段是什么形状」：同样 26 天久坐超标，落在 4 段里是结构性作息、
 * 散在 26 段里是偶发——卡片、日历、判读流水都分不出这一层。
 *
 * 判定与措辞全部来自 index.json.conclusions（后端 conclusions.py 算的）。
 * 前端只做三件事：挑「成立」的摆出来、排序、拼表。**不比较、不改措辞、不上色**：
 * 红黄绿会把 30 天压成一个档位，那是日级总评的粗糙翻版，而这里要说的是形态。
 *
 * 日尺度不渲染——那一天的判断已经在判读区，再算一遍只是换个措辞复述。
 */
var GR_ORDER = { "恢复": 0, "活动": 1, "其他": 2 };

function shapeRate(r) { return r.n ? r.h / r.n : 0; }

function shapeSort(a, b) {
  var sa = a.s === "成立" ? 0 : 1, sb = b.s === "成立" ? 0 : 1;
  if (sa !== sb) return sa - sb;
  var ga = GR_ORDER[a.gr] == null ? 9 : GR_ORDER[a.gr];
  var gb = GR_ORDER[b.gr] == null ? 9 : GR_ORDER[b.gr];
  if (ga !== gb) return ga - gb;
  if (shapeRate(a) !== shapeRate(b)) return shapeRate(b) - shapeRate(a);
  return a.id < b.id ? -1 : 1;
}

function shapeChip(r) {
  /* run 型（「连续 N 晚」类）的命中天数是副产品，**判据是段结构与最长段**：
     写「11/22 天」会被读成「一半的日子都这样」，而它真正说的是「有一段连了 5 天」。 */
  var bits = r.op === "run"
    ? ["分 " + r.e + " 段", "最长连 " + (r.l || 0) + " 天"]
    : [r.h + "/" + r.n + " 天"];
  /* 段数是「结构性 vs 偶发」的唯一证据：40 天落在 2 段里＝作息如此，
     落在 9 段里＝时常如此。只给比率，这个区别就被平均掉了。 */
  if (r.op !== "run" && r.e >= 2) bits.push(r.e + " 段");
  if (r.op !== "run" && r.l >= 2) bits.push("最长连 " + r.l + " 天");
  var t = r.w + "（" + (r.op === "run" ? "共 " + r.e + " 段，最长连着 " + (r.l || 0) + " 天"
                                        : r.h + "/" + r.n + " 天可判定");
  if (r.op !== "run" && r.e > 1) t += "，分 " + r.e + " 段";
  if (r.op !== "run" && r.l >= 2) t += "，最长连着 " + r.l + " 天";
  t += "）";
  return '<span class="ush-c" title="' + esc(t) + '"><i>' + esc(r.gr) + "</i>" +
    esc(r.w) + "<em>" + esc(bits.join(" · ")) + "</em></span>";
}

/* 累计量按单位换成人话：分钟换成小时，其余原样。 */
function shapeExtra(r) {
  if (r.t) return esc(r.t);
  if (r.iv == null) return "";
  if (r.iu === "min") return (r.iv / 60).toFixed(1) + " h";
  return r.iv + (r.iu ? " " + r.iu : "");
}

function shapeTable(rows) {
  var head = "<tr><th>维度</th><th>结论</th><th>状态</th><th>命中/可判</th>" +
    "<th>段</th><th>最长</th><th>累计 / 趋势</th></tr>";
  var body = rows.map(function (r) {
    return '<tr class="' + (r.s === "成立" ? "on" : "off") + '">' +
      "<td>" + esc(r.gr) + "</td><td>" + esc(r.w || "—") + "</td><td>" + esc(r.s) + "</td>" +
      '<td class="num">' + r.h + "/" + r.n + '</td><td class="num">' + r.e + "</td>" +
      '<td class="num">' + (r.l || 0) + "</td><td>" + shapeExtra(r) + "</td></tr>";
  }).join("");
  return '<table class="ush-tb"><thead>' + head + "</thead><tbody>" + body + "</tbody></table>";
}

export function renderShape(unit) {
  var box = $("unit-shape");
  if (!box) return;
  box.hidden = true;
  box.innerHTML = "";
  /* 日尺度：这一天的判断在判读区，不在这里复述 */
  if (store.scale === "day") return;

  var cbs = ix().conclusions || {};
  var all = cbs[unit.scale] || {};
  var blk = all[unit.key];
  var head = function (w) {
    return '<div class="ush-h"><span class="ush-t">这一段的形状</span>' +
      (w ? '<span class="ush-w">' + esc(w.s) + " ~ " + esc(w.e) + " · " + w.n + " 天</span>" : "") +
      "</div>";
  };
  var put = function (html) { box.hidden = false; box.innerHTML = html; };

  if (!blk) {
    var keys = Object.keys(all);
    /* 滚动窗只算最新窗口（载荷原因，见 homepage/conclusions_layer.py）。
       这里必须说出来：空着不解释，读起来就是「这一段没问题」。 */
    if (keys.length === 1 && String(unit.scale).indexOf("roll") === 0) {
      put(head(null) + '<p class="ush-note">滚动窗的结论只算最新窗口（' + esc(keys[0]) +
        "），翻到历史日期时不重算——那一段看日历颜色与判读流水。</p>");
    } else if (keys.length && !cbs._error) {
      put(head(null) + '<p class="ush-note">这个单元还没有结论（重建索引后可见）。</p>');
    }
    return;
  }

  var w = blk.w || {};
  var rows = (blk.rows || []).slice().sort(shapeSort);
  var on = rows.filter(function (r) { return r.s === "成立"; });

  if (!rows.length) {
    put(head(w) + '<p class="ush-note">这一段只有 ' + (w.n || 0) +
      " 天，够不上 " + (cbs._min_n || 5) + " 天的最小样本——暂时不谈形状。" +
      "切到更长的窗口能看。</p>");
    return;
  }

  /* 每个维度先用 rate 型顶：它自带「命中/可判 + 段数 + 最长段」，信息完整。
     run 型（「连续 N 晚」）是同一批命中日的另一种切法，和 rate 并排会被读成
     两件事——只有当这个维度没有 rate 型成立时，才拿它顶上。 */
  var byGr = {}, chips = [];
  on.forEach(function (r) { (byGr[r.gr] = byGr[r.gr] || []).push(r); });
  Object.keys(byGr).forEach(function (g) {
    var rated = byGr[g].filter(function (r) { return r.op === "rate"; });
    chips = chips.concat(rated.length ? rated : byGr[g]);
  });
  chips.sort(function (a, b) {
    var ga = GR_ORDER[a.gr] == null ? 9 : GR_ORDER[a.gr];
    var gb = GR_ORDER[b.gr] == null ? 9 : GR_ORDER[b.gr];
    return ga !== gb ? ga - gb : shapeRate(b) - shapeRate(a);
  });

  var html = head(w);
  if (chips.length) {
    html += '<div class="ush-cs">' + chips.map(shapeChip).join("") + "</div>";
  } else {
    /* 一条「成立」都没有时**不能硬挑一条**当代表：那会把「时破时回」写成「常态」。
       直说这一段没有占多数的形态，下面给明细。 */
    html += '<p class="ush-note">这一段没有占多数的形态（各项都在参半或偶发），明细见下。</p>';
  }
  var rest = rows.length - on.length;
  html += '<details class="ush-all"><summary>全部 ' + rows.length + " 条结论" +
    (rest ? "（含 " + rest + " 条参半 / 不成立）" : "") +
    (blk.na ? "，另有 " + blk.na + " 项样本不足未给结论" : "") +
    "</summary>" + shapeTable(rows) + "</details>";
  put(html);
}

/* 准备度的日内重算轨迹：只存在于日分片，故日尺度下按需拉取。
   单点轨迹与主值重复，readinessTrail 会返回空串，此处即隐藏整行。 */
function renderReadyTrail() {
  var box = $("unit-ready-trail");
  if (store.scale !== "day") { box.hidden = true; box.innerHTML = ""; return; }
  var anchor = store.anchor;
  var fill = function () {
    /* 拉分片是异步的，锚点可能已经翻页——只认发起时的那一天 */
    if (store.anchor !== anchor) return;
    box.innerHTML = readinessTrail(dayOf(anchor));
    box.hidden = !box.innerHTML;
  };
  if (dayOf(anchor)) { fill(); return; }
  box.hidden = true;
  /* 同 renderBrief：索引里没有这一天就不会有分片，别发那个必然 404 的请求 */
  if (!store.byDate[anchor]) { box.innerHTML = ""; return; }
  ensureShard(anchor).then(fill).catch(function () { box.innerHTML = ""; });
}

function checkOne(r, key) {
  var g = ix().goals;
  if (key === "sleep") return r.sleep_h != null && r.sleep_h >= g.sleep_h;
  /* 步数按判读线（8000）判达标，goal（10000）只作目标 —— 与 aggregate.checkFns
     和后端 units.j 同源。两处实现都是历史遗留，改动时必须同步。 */
  if (key === "steps") {
    var line = g.steps_good || g.steps;
    return r.steps != null && r.steps >= line;
  }
  if (key === "hrv") return r.hrv_ok === true;
  return r.sedentary_min != null && r.sedentary_min <= g.sedentary_min;
}

/* 环比 chip。**措辞由后端的 delta_verdict 决定，不由数值大小决定**：
   `within-noise`（|Δ| < mdc）时不给箭头、不给百分比，只写「在误差内」；
   `suppressed`（tier.change=low，如步数）写「不做环比」，因为 6 天样本的均值
   本来就分不开 6400 和 10294；`insufficient` 写「样本不足」。 */
function deltaChip(cell, better, prevLabel) {
  if (!cell) return '<span class="dl flat">无环比基数</span>';
  var dv = cell.dv;
  if (!dv) return '<span class="dl flat">该指标未登记噪声门槛</span>';
  var word = VERDICT_TEXT[dv] || dv;
  if (dv !== "changed") return '<span class="dl flat">' + word + "</span>";
  var d = cell.d || 0;
  /* band 型指标（HRV）不给好/坏色：它的目标是「落在个人带内」，
     不是「越高越好」——从带外回到带内是好事，冲过带顶不是。方向交给判读，
     主页只报「变了多少、过没过门槛」。 */
  if (!better) {
    return '<span class="dl flat">' + (d > 0 ? "+" : "") + trimNum(d) + " " + word + "</span>";
  }
  var good = better === "down" ? d < 0 : d > 0;
  return '<span class="dl ' + (good ? "good" : "bad") + '">' +
    (d > 0 ? "+" : "") + trimNum(d) + " " + word + "</span>";
}

/* 分数类（综合指数 / 准备度）没有登记 mdc，因此**不判好坏**：
   只给差值，中性色。以前这里按百分比上红绿箭头，等于凭空给分数判了方向。 */
function rawDeltaChip(cur, prev, prevLabel) {
  if (cur == null || prev == null) return '<span class="dl flat">无环比基数</span>';
  var d = cur - prev;
  if (Math.abs(d) < 0.5) return '<span class="dl flat">与' + esc(prevLabel) + "持平</span>";
  /* 两个分数都是 0–100 的整数分，差值也取整：写「-13.7」会让读者以为
     分数本身有小数精度，而它没有。 */
  return '<span class="dl flat">' + (d > 0 ? "+" : "") + Math.round(d) +
    " vs " + esc(prevLabel) + "</span>";
}

function trimNum(v) {
  var s = String(Math.round(v * 10) / 10);
  return s.replace(/\.0$/, "");
}

/* ================= ③ 指标网格 ================= */
/* 某指标在单元内的达标计数：后端优先，缺 units 时回落前端现算 */
function okStat(unit, m) {
  if (!m || !m.dir) return null;
  var r = okCount(ix(), unit, m);
  /* 后端有单元就以后端为准，哪怕它判了 na（覆盖不足）——那时 tot=0，
     由卡面显示「覆盖 x% · 不给结论」。**不能回落到前端现算**：回落等于
     用 index 行的旧字段盖掉后端口径，而旧字段对「进行中的今天」是全 null
     （morning 行不含白天 activity），一回落就会把已有数字抹成「—」。 */
  if (r && r.src === "backend") return r.tot ? r : { ok: 0, tot: 0, na: true };
  var usable = rowsWith(unitRowsAll(unit, store.byDate), m);
  if (!usable.length) return null;
  return {
    ok: usable.filter(function (x) { return judgeValue(x[m.key], m, ix()) === true; }).length,
    tot: usable.length
  };
}

function cardHtml(m, unit, prevUnit) {
  var cell = unitCell(ix(), store.scale, unit.key, m);
  var val = cell ? cell.v : metricValue(rowsWith(unitRowsAll(unit, store.byDate), m), m);
  var prevCell = unitCell(ix(), prevUnit.scale, prevUnit.key, m);

  var j = cell ? cellJudge(cell) : judgeValue(val, m, ix());
  var na = j === "na";                     // 覆盖不足 / 无数据：不给结论
  var ok = na ? null : j;
  var color = m.dir == null ? "var(--text)"
    : na ? "var(--text-3)" : ok ? "var(--green-deep)" : "var(--amber-deep)";

  /* 趋势线由**两个独立参数**决定：范围（看多久）× 粒度（每个点多长），见 trend.js。
     观测尺度只决定「当前单元」的口径，不参与画图——换日/周/月，这张图纹丝不动。 */
  var pts = trendPoints(ix(), store.byDate, m, store.anchor,
                        store.trendSpan, store.trendGrain);
  var st = okStat(unit, m);
  var canDrill = store.scale === "day" && m.detail && DETAILS[m.detail];

  /* 覆盖率的诚实表达：后端把 coverage < 门槛的单元判成 na，
     这里必须说出来（「3/7 天数据」），而不是显示一个看起来很确定的数字。

     `cell.g`（2026-11-01）= 该单元里**采集侧没拿到**的天数。它回答的是覆盖率
     回答不了的那个问题：缺的这几天，是系统没采到，还是你没戴表？
     两个都让数字变少，但一个是该修管线、一个是该戴表——不能混着说。 */
  var gapNote = cell && cell.g
    ? '<span class="mx-gap">' + cell.g + " 天采集故障</span>" : "";
  var stat;
  if (!st) {
    stat = '<span class="mx-stat">该指标无独立达标线</span>';
  } else if (na && cell && cell.c != null) {
    stat = '<span class="mx-stat">覆盖 ' + Math.round(cell.c * 100) + "% · 不给结论</span>" + gapNote;
  } else {
    stat = '<span class="mx-stat">达标 <b>' + st.ok + "</b>/" + st.tot + " " +
      (store.scale === "day" ? "（当日）" : "天") + "</span>" + gapNote;
  }

  return '<article class="mx-card' + (ok === false ? " is-warn" : "") + '" data-metric="' + m.key + '">' +
    '<div class="mx-head">' +
      '<div class="mx-title"><h3>' + esc(m.label) + '</h3><p class="mx-goal">' + esc(m.goalText()) + "</p></div>" +
      '<div class="mx-value"><b style="color:' + color + '">' + esc(m.fmt(val)) + "</b></div>" +
    "</div>" +
    '<div class="mx-meta">' + esc(aggNoun(store.scale, m)) +
      deltaChip(cell, m.dir === "below" ? "down" : m.dir === "above" ? "up" : null,
        prevUnit.label) + "</div>" +
    '<div class="mx-chart">' + sparkline(pts, m, ix(), {}) + "</div>" +
    '<div class="mx-foot">' + stat +
      (canDrill ? '<button class="mx-more" type="button" data-detail="' + esc(m.detail) + '"><span>展开当日细分</span>' + caret() + "</button>" : "") +
    "</div>" +
    (canDrill ? '<div class="mx-detail" hidden></div>' : "") +
    "</article>";
}

function caret() {
  return '<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M3 5.5L7 9.5L11 5.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
}

/* 指标分组 · 按**信息维度**划，不按数据来源划（2026-11-02 重定）。
 *
 * 判据是「这个数在回答哪一类问题」，落成三条互不重叠的轴：
 *   恢复 —— 身体在补：睡得够不够、自主神经张力、承压多大
 *   活动 —— 身体在耗：走了多少、坐了多久、动了多猛、烧了多少
 *   结构 —— 身体是什么：体重、体脂率、去脂体重（慢变量，以周/月计）
 *
 * **为什么必须按维度、不能按来源**：按来源分，InBody 那三个量只能凑成一组
 * 「体成分」，和 Garmin 的组并列——于是它必须自带一套判定、一套时间语义、
 * 一套说明，最后必然长成页尾一块独立的附录。按维度分，体重和步数睡在同一个
 * 网格里，谁测的就不重要了；「结构」这一组只是比上面两组慢而已。
 *
 * 压力归「恢复」不归「活动」：它和 HRV、静息心率是同一件事（交感张力）的三个侧面，
 * 放到「消耗」那边是把它读成了行为。活动消耗归「活动」不单列「负荷」：
 * 它就是「今天动了多少」换算成能量，和步数中高强度同源，不该自成一轴。
 */
var GROUPS = [
  { k: "recover", t: "恢复", sub: "身体在补：睡够了没、张力如何、承压多大", keys: ["sleep_h", "hrv", "rhr", "stress"] },
  { k: "activity", t: "活动", sub: "身体在耗：走了多少、坐了多久、烧了多少", keys: ["steps", "sedentary_min", "intensity_min", "active_kcal"] }
];

function groupHtml(m, unit, prev) {
  return cardHtml(m, unit, prev);
}

function renderGrid(unit) {
  var prev = prevUnitOf(store.anchor, store.scale);
  var all = metrics(ix());
  var byKey = {};
  all.forEach(function (m) { byKey[m.key] = m; });
  var used = {};
  var html = "";
  GROUPS.forEach(function (g) {
    var ms = g.keys.map(function (k) { return byKey[k]; }).filter(Boolean);
    if (!ms.length) return;
    ms.forEach(function (m) { used[m.key] = 1; });
    html += '<section class="mx-group" data-group="' + g.k + '">' +
      '<div class="mx-gh"><h3>' + esc(g.t) + "</h3><span>" + esc(g.sub) + "</span></div>" +
      '<div class="metric-grid">' + ms.map(function (m) {
        return groupHtml(m, unit, prev);
      }).join("") + "</div></section>";
  });
  /* 结构组：卡壳与上面两组同构，差别只有时间语义（扫描点，不吃 anchor/scale）。
     它不是「InBody 专区」，是「结构」这个维度的三个量——所以组名取维度，
     不取产品名；数据来自哪台机器是脚注里的事。 */
  html += bodyGroupHtml();
  var rest = all.filter(function (m) { return !used[m.key]; });
  if (rest.length) {
    html += '<section class="mx-group" data-group="other">' +
      '<div class="mx-gh"><h3>其他</h3><span>尚未归组的指标</span></div>' +
      '<div class="metric-grid">' + rest.map(function (m) {
        return groupHtml(m, unit, prev);
      }).join("") + "</div></section>";
  }
  $("metric-grid").innerHTML = html;
}

/* ================= ④ 当日判读（仅日尺度）=================
   这一区**只放结论**：交叉判读 + 分组判点 + 数据缺口。
   任何有量化指标的实测值（如准备度）归总览或指标网格——判读区一旦夹带
   原始设备值，「交叉判读先行」这句副标题就会被自己的版式推翻。 */
/* 有行但全空：跨度内的空白日（raw 缺失）在分片里是「一行全 null」，
   不是「没有这一天」。**必须走与未来日相同的空态**——否则总览说
   「无采集记录」、判读区说「简报尚未生成」，同一屏两种说法（探针 2026-10-25 抓到）。 */
function dayIsEmpty(d) {
  return !!d && !d.has_brief && d.score == null && d.sleep_h == null &&
    d.hrv == null && d.steps == null;
}

function fillBrief(d) {
  if (dayIsEmpty(d)) { fillBriefEmpty(d.date); return; }
  var b = briefHtml(d);
  $("brief-grid").innerHTML = b.html;
  $("brief-sub").textContent = b.sub;
  $("gaps").textContent = b.gaps;
  renderDayEvents(d.date);
}

/* 当日背景事件（2026-11-01）。判读的结论常常建立在「那天在加班」「那次血氧是误报」
   这类上下文上，而这些话只存在于 events.jsonl。看不到依据的结论，三个月后回看
   会觉得判断来得莫名其妙——所以结论和依据必须同屏。 */
var EV_TYPE = {
  context: "背景", checkup: "排查", diet: "饮食", note: "记录", more: "另有"
};

function renderDayEvents(date) {
  var box = $("day-events");
  if (!box) return;
  var list = ((ix().events || {})[date]) || [];
  if (!list.length) { box.hidden = true; box.innerHTML = ""; return; }
  box.hidden = false;
  box.innerHTML = '<h4 class="de-title">当日背景事件<i>判读依赖的上下文，不是当天的实测值</i></h4>' +
    '<ul class="de-list">' + list.map(function (e) {
      return '<li><span class="de-t">' + esc(EV_TYPE[e.t] || e.t) + "</span>" +
        '<span class="de-c">' + esc(e.c) + "</span></li>";
    }).join("") + "</ul>";
}

/* 判读流水（2026-11-01）：把 60 多天的结论压成一行行。
   它放在独立 section 里、不吃 anchor/scale——这是「长期视角」，
   和「我现在在看哪一天」是两回事，跟着日历翻页只会互相打断。 */
export function renderBriefLog() {
  var zone = $("brief-log-zone");
  var bs = ix().briefs || {};
  var dates = Object.keys(bs).sort().reverse();
  if (!dates.length) { zone.hidden = true; return; }
  zone.hidden = false;
  $("brief-log-sum").textContent = "判读流水 · " + dates.length + " 天（点任一行跳到那天）";
  $("bl-list").innerHTML = dates.map(function (d) {
    var b = bs[d] || {};
    return '<li class="bl-i" data-date="' + d + '">' +
      '<span class="bl-d">' + fmtMD(d) + "</span>" +
      '<span class="bl-lv lv-' + esc(b.lv || "NEUTRAL") + '" title="' + esc(b.lv || "") + '"></span>' +
      '<span class="bl-t">' + esc(b.head || "") + "</span></li>";
  }).join("");
}

/* 该日没有采集记录。**必须是明确空态，不能是「加载中」**：
   转圈会让人以为再等等就有，而这天根本不会有数据。
   「缺数据 ≠ 没做到」——日历上它是空心灰，这里也不能暗示那天没做。 */
function fillBriefEmpty(anchor) {
  $("brief-sub").textContent = fmtMD(anchor) + " " + weekdayCN(anchor) + " · 该日没有采集记录";
  $("brief-grid").innerHTML =
    '<p class="section-empty">这一天没有 Garmin 记录，因此也没有当日判读。' +
    "可能是还没到（当天数据要等次日 02:00 出终版），也可能是当天没戴表或没同步——" +
    "日历上它是空心灰：<b>缺数据不等于没做到</b>。</p>";
  $("gaps").textContent = "";
  renderDayEvents(anchor);
}

export function renderBrief() {
  var zone = $("brief-zone");
  if (store.scale !== "day") { zone.hidden = true; return; }
  zone.hidden = false;

  var anchor = store.anchor;
  var d = dayOf(anchor);
  if (d) { fillBrief(d); return; }

  /* 索引里没有这一天 = 不在数据跨度内（未来日 / 本月尚未产生任何数据）。
     这类锚点**永远**拉不到分片，去拉就是自找 404：每月 1 号打开页面，
     判读区会直接写「详情分片加载失败（2026-11）」——把「还没到」说成了故障
     （2026-11-01 实证）。先查索引，再决定要不要发请求。 */
  if (!store.byDate[anchor]) { fillBriefEmpty(anchor); return; }

  /* 分片已加载、却仍然没有这一天 = 该日无记录，不是「还没加载」。
     这一句是**防死循环的闸门**：少了它，下面的 ensureShard 会立刻 resolve，
     .then 里又调回 renderBrief，形成微任务自旋，整个标签页彻底冻死
     （2026-09-16 实测：切到「日」尺度、或点日历上任何无数据的一格，必然卡死）。 */
  if (hasShard(anchor)) { fillBriefEmpty(anchor); return; }

  $("brief-sub").textContent = "正在读取 " + anchor + " 的当月详情分片…";
  $("brief-grid").innerHTML = "";
  $("gaps").textContent = "";
  ensureShard(anchor).then(function () {
    /* 拉分片是异步的，锚点可能已经翻页——只认发起时的那一天（同 renderReadyTrail） */
    if (store.anchor !== anchor) return;
    var dd = dayOf(anchor);
    if (dd) fillBrief(dd); else fillBriefEmpty(anchor);
  }).catch(function () {
    if (store.anchor !== anchor) return;
    $("brief-sub").textContent = "详情分片加载失败（" + anchor.slice(0, 7) + "）。";
    $("brief-grid").innerHTML = "";
    $("gaps").textContent = "";
  });
}

/* 逐日达标矩阵（2026-11-01）。
 *
 * 日历把每天的**四项达标**画成四个小点，那里只能看到「今天几项过线」。
 * 真正要回答的问题是另一个：**这几天是不是好几项一起变差**——
 * 睡眠掉了、HRV 也掉了、久坐还涨了，那是同一件事的三个侧面；
 * 只有一项掉，那才是那一项自己的问题。
 * 这件事在 8 张平铺的卡片里做不到（要在其间来回扫），在日历的四个点里也做不到
 * （只有 4 项、且没有指标身份）。矩阵是唯一能一眼看出来的形态。
 *
 * 受容器宽度限制，只画近 30 天；更长周期交给日历热力条。
 * 判定一律取后端 `units.day`（唯一口径），**前端不自己比大小**。 */
var MATRIX_N = 30;

function renderDayMatrix() {
  var box = $("day-matrix");
  if (!box) return;
  var rows = store.rows.slice(-MATRIX_N);
  if (rows.length < 8) { box.hidden = true; return; }
  var ms = metrics(ix());
  /* 只留**真的逐日判过**的指标。过滤器要用 units.day 里实际存在的键，
     不能用 CHECK_FIELD——那是「四项达标」的四个别名（sleep/steps/hrv/sedentary），
     拿它当白名单会把静息心率、中高强度、压力全筛掉，矩阵只剩两行
     （2026-11-01 实测：2×30=60 格，看起来像「只有两项被判定」）。 */
  ms = ms.filter(function (m) {
    var u = (ix().units || {}).day || {};
    return rows.some(function (r) {
      var c = (u[r.date] || {})[m.key];
      return c && c.j;
    });
  });
  if (!ms.length) { box.hidden = true; return; }

  var head = '<div class="dm-row dm-head"><span class="dm-k"></span>' +
    rows.map(function (r) {
      return '<i class="dm-day">' + (Number(r.date.slice(8, 10)) === 1 ? r.date.slice(5, 7) + "/1" : r.date.slice(8, 10)) + "</i>";
    }).join("") + '<span class="dm-s"></span></div>';

  var body = ms.map(function (m) {
    var ok = 0, tot = 0;
    var cells = rows.map(function (r) {
      var c = (((ix().units || {}).day || {})[r.date] || {})[m.key];
      var j = c ? c.j : null;
      var cls = j === "ok" ? "ok" : j === "no" ? "no" : "na";
      if (j === "ok" || j === "no") { tot++; if (j === "ok") ok++; }
      var tip = r.date + " · " + m.label + " " + (j === "ok" ? "达标" : j === "no" ? "未达标" : "无判定");
      return '<i class="dm-c ' + cls + '" title="' + esc(tip) + '"></i>';
    }).join("");
    return '<div class="dm-row" data-metric="' + m.key + '">' +
      '<span class="dm-k">' + esc(m.label) + "</span>" + cells +
      '<span class="dm-s">' + (tot ? ok + "/" + tot : "–") + "</span></div>";
  }).join("");

  box.hidden = false;
  /* 折叠：矩阵回答的是「这两天是不是好几项一起变差」——这是**核查**，不是日常。
     常驻展开时它和日历抢同一件事（哪些天不正常），而日历已经用颜色说过了。 */
  box.innerHTML = '<details class="dm-det">' +
    '<summary class="dm-sum">逐日达标矩阵 · 近 ' + rows.length + " 天" +
      "<i>行=指标，列=天；看的是「这几项有没有一起变差」，不是哪天分数高</i></summary>" +
    '<div class="dm-body"><div class="dm-grid">' + head + body + "</div></div>" +
    "</details>";
}

/* ================= 装配 ================= */
export function renderPage() {
  var unit = unitOf(store.anchor, store.scale);
  renderAxis(unit);
  renderDayMatrix();
  renderOverview(unit);
  renderGrid(unit);
  renderBrief();
}

/* 卡内细分展开：日尺度专属，按需拉分片 */
export function setupDetailToggle() {
  document.addEventListener("click", function (e) {
    if (!e.target.closest) return;
    var btn = e.target.closest("[data-detail]");
    if (!btn) return;
    e.preventDefault();
    var card = btn.closest(".mx-card");
    var box = card && card.querySelector(".mx-detail");
    if (!box) return;
    var label = btn.querySelector("span");

    if (!box.hidden) {
      box.hidden = true;
      btn.classList.remove("open");
      label.textContent = "展开当日细分";
      return;
    }
    var render = DETAILS[btn.getAttribute("data-detail")];
    var fill = function (day) {
      /* day 为 null = 该日无记录。绝不能把 null 塞进 render（会抛错，
         再被 .catch 误报成「加载失败」——明明只是这天没有数据）。 */
      var out = (day && render) ? render(day) : "";
      box.innerHTML = out || '<p class="mini-note">该日没有这一项的细分记录。</p>';
      box.hidden = false;
      btn.classList.add("open");
      label.textContent = "收起细分";
    };
    var anchor = store.anchor;
    var d = dayOf(anchor);
    if (d) { fill(d); return; }
    btn.disabled = true;
    ensureShard(anchor)
      .then(function () {
        btn.disabled = false;
        if (store.anchor !== anchor) return;   /* 锚点已翻页，丢弃 */
        fill(dayOf(anchor));
      })
      .catch(function () {
        btn.disabled = false;
        if (store.anchor !== anchor) return;
        box.innerHTML = '<p class="mini-note">该日详情加载失败。</p>';
        box.hidden = false;
      });
  });
}
