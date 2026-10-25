/* 日尺度下的卡内细分：把「某日」原来那一整页的明细，拆回它所属的指标卡。
 *
 * 这是「概览与某日不再割裂」的关键——某日的睡眠分期属于睡眠卡、
 * HRV 曲线属于 HRV 卡、24 小时步数分布属于步数卡。
 * 于是「看某天」不需要换页面，只需要在日尺度下把卡展开。
 * 每个函数返回 HTML 字符串，数据取自按月分片的完整 day 对象。
 */
import { C } from "./constants.js?v=6cd0915d";
import { esc, dash, fm } from "./utils.js?v=6cd0915d";
import { lineChart, hypnoSvg, stackBar, hourBars, rangeBar } from "./charts.js?v=6cd0915d";
import { facts, subChips } from "./components.js?v=6cd0915d";

function t(title) { return '<h4 class="detail-title">' + esc(title) + "</h4>"; }

/* ---------------- 睡眠 ---------------- */
export function sleepDetail(d) {
  var sl = (d.recovery || {}).sleep;
  if (!sl) return "";
  var st = sl.stages || {};
  var h = t("睡眠结构（占睡眠期）") +
    stackBar([
      { v: st.deep_min, color: C.deep, label: "深睡 " + st.deep_min + " 分钟" },
      { v: st.light_min, color: C.light, label: "浅睡 " + st.light_min + " 分钟" },
      { v: st.rem_min, color: C.rem, label: "REM " + st.rem_min + " 分钟" },
      { v: st.awake_min, color: C.awake, label: "清醒 " + st.awake_min + " 分钟" }
    ]) +
    (sl.stage_pct ? '<div class="mini-note">深睡 ' + sl.stage_pct.deep + "%（合理 16–33%） · REM " + sl.stage_pct.rem + "%（合理 21–31%）</div>" : "");
  if (sl.hypno && sl.hypno.length) h += t("睡眠结构时间轴") + hypnoSvg(sl.hypno);
  if (sl.sub_scores && sl.sub_scores.length) h += t("Garmin 睡眠子项评分") + subChips(sl.sub_scores);
  h += facts([
    ["入睡 / 醒来", dash(sl.bed) + " / " + dash(sl.wake)],
    ["夜间清醒", dash(sl.awake_count) + " 次 · 翻身 " + dash(sl.restless)],
    ["呼吸", "均值 " + dash(sl.resp && sl.resp.avg, 0) + "（" + dash(sl.resp && sl.resp.low, 0) + "–" + dash(sl.resp && sl.resp.high, 0) + "）次/分"],
    ["呼吸中断风险", dash(sl.breathing)],
    ["睡眠压力", dash(sl.sleep_stress, 0) + "（越低越好）"],
    ["睡眠需求基线", "约 " + dash(sl.need_h) + " 小时" + (sl.nap_min ? " · 小睡 " + sl.nap_min + " 分钟" : "")]
  ]);
  var sp = (d.recovery || {}).spo2;
  if (sp) {
    h += t("睡眠期血氧 SpO2");
    if (sp.curve && sp.curve.length) {
      h += lineChart(sp.curve, { min: Math.min(85, Math.min.apply(null, sp.curve.map(function (p) { return p[1]; })) - 2), max: 100 }) +
        '<div class="mini-note">看趋势不看单点；单点 <92% 属测量噪声，持续低才需关注</div>';
    }
    h += facts([["昨夜平均", dash(sp.avg, 0) + "%"], ["7 日均值", dash(sp.avg7, 0) + "%"], ["瞬时最低", dash(sp.low) + "%"]]);
  }
  return h;
}

/* ---------------- HRV ---------------- */
export function hrvDetail(d) {
  var hv = (d.recovery || {}).hrv;
  if (!hv) return "";
  /* 只看当晚实测值与本卡的个人区间，不看 Garmin status——
     status 由 7 日滚动均值决定，单夜暴跌时照旧显示 BALANCED
     （2026-09-11 实测 43 跌破下限 51 仍报 BALANCED）。 */
  var h = "";
  if (hv.curve && hv.curve.length) {
    h += t("夜间 HRV 逐点曲线（绿带 = 个人区间 " + dash(hv.low) + "–" + dash(hv.high) + " ms）") +
      lineChart(hv.curve, { band: [hv.low, hv.high] });
  }
  return h + facts([
    ["当晚实测", dash(hv.value) + " ms"],
    ["7 日均值", dash(hv.weekly_avg)],
    ["Garmin 状态", hv.status === "BALANCED" ? "平衡（7 日均口径）" : hv.status === "UNBALANCED" ? "失衡" : dash(hv.status)]
  ]);
}

/* ---------------- 静息心率（含睡眠期心率） ---------------- */
export function rhrDetail(d) {
  var h = "";
  var sh = (d.recovery || {}).sleep_hr;
  if (sh && sh.curve && sh.curve.length) {
    h += t("睡眠期心率曲线（bpm）") + lineChart(sh.curve, { color: "#F59E0B" }) +
      facts([["睡眠期均值", dash(sh.avg, 0) + " bpm"], ["睡眠期最低", dash(sh.lowest) + " bpm"]]);
  }
  var hr = (d.load || {}).hr;
  if (hr) {
    h += t("全天心率曲线（bpm，30 分钟粒度）") + lineChart(hr.curve, { color: C.red }) +
      facts([["全天最高", dash(hr.max) + " bpm"], ["全天最低", dash(hr.min) + " bpm"], ["静息心率", dash(hr.rhr) + " bpm"]]);
  }
  return h;
}

/* ---------------- 步数 / 活动行为 ---------------- */
export function activityDetail(d) {
  var a = d.activity;
  if (!a) return "";
  var h = "";
  if (a.hourly_steps) {
    h += t("24 小时步数分布") + hourBars(a.hourly_steps) +
      '<div class="mini-note">深绿为活跃时段（≥300 步/时），灰色为低活动时段</div>';
  }
  h += facts([
    ["距离", dash(a.distance_km) + " km"],
    ["强度分钟", dash(a.intensity_min) + " 分钟"],
    ["活动消耗", dash(a.active_kcal) + " kcal"],
    ["爬楼", a.floors != null ? a.floors + " 层" : "—"]
  ]);
  if (a.sessions && a.sessions.length) {
    h += t("正式活动记录（按了开始键的）") + '<ul class="session-list">' +
      a.sessions.map(function (s) {
        return "<li><span class=\"sess-name\">" + esc(s.name) + '</span><span class="sess-meta">' + esc(s.time) +
          " · " + dash(s.duration_min) + " 分钟" + (s.distance_km ? " · " + s.distance_km + " km" : "") + "</span></li>";
      }).join("") + "</ul>";
  } else {
    h += '<div class="mini-note">当日没有按开始键的计时记录——这不代表没动：你当天全部走动（' + dash(a.steps) +
      ' 步 / ' + dash(a.distance_km) + ' km / 强度 ' + dash(a.intensity_min) +
      ' 分钟）已完整计入步数与 24 小时分布，无需按开始键也如实统计。</div>';
  }
  return h;
}

/* ---------------- 久坐 ---------------- */
export function sedentaryDetail(d) {
  var a = d.activity;
  if (!a) return "";
  var blk = a.sedentary_max_block_min;
  return facts([
    ["全天久坐", a.sedentary_min != null ? fm(a.sedentary_min) : "—"],
    ["最长连续块", blk != null ? blk + " 分钟" : "—"],
    ["步数", dash(a.steps) + " 步"]
  ]) + (blk != null && blk >= 120
    ? '<div class="mini-note">出现了 ≥2 小时的连续久坐块——这类长块对代谢的伤害不是"总时长"能反映的，中间打断一次就够。</div>'
    : "");
}

/* ---------------- 压力 ---------------- */
export function stressDetail(d) {
  var st = (d.load || {}).stress;
  if (!st) return "";
  var du = st.dur_min || {};
  var total = (du.rest || 0) + (du.low || 0) + (du.medium || 0) + (du.high || 0);
  return t("全天压力时长分布（共 " + fm(total) + "）") +
    stackBar([
      { v: du.rest, color: C.calm, label: "平静 " + fm(du.rest || 0) },
      { v: du.low, color: C.green, label: "低压力 " + fm(du.low || 0) },
      { v: du.medium, color: C.amber, label: "中压力 " + fm(du.medium || 0) },
      { v: du.high, color: C.red, label: "高压力 " + fm(du.high || 0) }
    ], total) +
    facts([["全天平均", dash(st.avg) + "（0–100，越低越放松）"], ["压力峰值", dash(st.max)]]);
}

/* ---------------- 活动消耗（含身体电量与能量账本） ---------------- */
export function energyDetail(d) {
  var l = d.load || {};
  var h = "";
  var en = l.energy;
  if (en) {
    var total = en.total || (en.bmr + en.active) || 1;
    h += t("总消耗 " + dash(en.total) + " kcal 的构成") +
      stackBar([
        { v: en.bmr, color: C.light, label: "基础代谢 " + dash(en.bmr) + " kcal" },
        { v: en.active, color: C.green, label: "活动消耗 " + dash(en.active) + " kcal" }
      ], total) +
      '<div class="mini-note">基础代谢是维持生命的底账，活动消耗才是可调节部分；活动占比 ' +
      Math.round((en.active || 0) / total * 100) + "%</div>";
  }
  var bb = l.bb;
  if (bb) {
    var night = bb.night || {};
    h += t("身体电量（0–100）") +
      (night.start != null ? facts([
        ["入睡时电量", dash(night.start)],
        ["醒来时电量", dash(night.end)],
        ["夜间净充", (night.delta >= 0 ? "+" : "") + dash(night.delta)]
      ]) : '<p class="mini-note">昨夜身体电量明细暂缺</p>') +
      rangeBar(bb.low, bb.high, C.green) +
      '<div class="mini-note">当日最低 ' + dash(bb.low) + " · 最高 " + dash(bb.high) + "</div>";
  }
  return h;
}

/* ---------------- 准备度：日内重算轨迹（日尺度专属） ----------------
   准备度本身是**指标**，主值与环比在「当前单元总览」里和综合指数并排
   （见 metricgrid.renderOverview）——这里只补它在一天之内的重算轨迹。
   为什么不塞回判读区：判读是人写的结论、一天一份，放进指标会同时毁掉
   两边（结论区混进原始设备值，指标又因此进不了周/月聚合）。 */
export function readinessTrail(d) {
  var rd = ((d || {}).recovery || {}).readiness;
  if (!rd) return "";
  var ser = rd.series || [];
  /* 单点轨迹没有信息量（「09:26 晨起 94」等于主值本身），只在真的重算过时给 */
  if (ser.length < 2) return "";
  return "日内重算：" + ser.map(function (p) {
    return esc(p[0]) + " " + esc(p[2]) + " <b>" + esc(p[1]) + "</b>";
  }).join(" · ") +
    '<span class="unit-trail-note">（晨起值回答「恢复得怎么样」，运动后重算回答「今天被消耗掉多少」）</span>';
}

/* ---------------- 判读简报（一天一份，无对应量化指标，单独成区） ---------------- */
export function briefHtml(d) {
  var b = d.brief;
  var empty = !b || !((b.sections && b.sections.length) || (b.cross && b.cross.length));
  if (empty) {
    return { sub: "分析日 " + d.label + " " + d.weekday, html: '<p class="section-empty">' +
      (b ? "本日简报尚未生成——简报每晚 23:30 为当日写入正文，当前只有占位。" :
        "该日无判读简报存档（简报每晚 23:30 为分析日生成）。") +
      (d.morning ? "本日数据仍在进行中，今晚 23:30 出终版。" : "") + "</p>", gaps: "" };
  }
  var html = "";
  if (b.cross && b.cross.length) {
    html += '<div class="brief-cross-wrap"><div class="brief-cross-head">交叉判读 · 多指标互证（简报主体）</div>' +
      b.cross.map(function (mk) {
        return '<div class="brief-cross-item"><span class="level-dot ' + mk.level + '"></span><span>' + esc(mk.text) + "</span></div>";
      }).join("") + "</div>";
  }
  html += b.sections.map(function (s) {
    return '<div class="brief-card"><div class="brief-card-head"><span class="level-dot ' + s.level + '"></span><h3>' +
      esc(s.title) + '</h3></div><ul>' + s.marks.map(function (mk) {
        return '<li><span class="level-dot ' + mk.level + '"></span><span>' + esc(mk.text) + "</span></li>";
      }).join("") + "</ul></div>";
  }).join("");
  return {
    sub: "分析日 " + d.label + " " + d.weekday + " · 每晚 23:30 自动生成 · 交叉判读先行",
    html: html,
    gaps: b.gaps && b.gaps.length ? "数据缺口：" + b.gaps.join("；") : ""
  };
}

/* 指标卡 -> 细分渲染器。key 来自 constants.METRICS[].detail */
export var DETAILS = {
  sleep: sleepDetail, hrv: hrvDetail, rhr: rhrDetail,
  activity: activityDetail, sedentary: sedentaryDetail,
  stress: stressDetail, energy: energyDetail
};
