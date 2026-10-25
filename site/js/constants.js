/* 设计常量：配色、指标图标、指标定义表、指数色阶。
   改配色 / 图标 / 指标只动本文件。阈值不在这里——一律来自 index.json 的
   goals / baselines（后端从 rules.json 下发）。 */

export var C = {
  green: "#10B981", greenDeep: "#047857", amber: "#F59E0B", red: "#EF4444",
  gray: "#9CA3AF", border: "#E7E7EA",
  deep: "#4338CA", light: "#60A5FA", rem: "#34D399", awake: "#D1D5DB",
  calm: "#CBD5E1"
};

/* 指数分档色：良好 / 一般 / 待注意 / 无数据 */
export var BAND = {
  good: "#10B981", fair: "#F59E0B", low: "#EF4444", none: "#D1D5DB"
};

export function bandOf(v, baselines) {
  if (v == null) return "none";
  var b = (baselines && baselines.score) || { good_min: 80, fair_min: 60 };
  return v >= b.good_min ? "good" : v >= b.fair_min ? "fair" : "low";
}

export function bandColor(v, baselines) { return BAND[bandOf(v, baselines)]; }

/* 四项达标维度：日历格内的四个点、KPI 卡、聚合表共用同一组键 */
export var CHECKS = [
  { key: "sleep", label: "睡眠", goalText: "≥ 7h" },
  { key: "steps", label: "步数", goalText: "≥ 10k" },
  { key: "hrv", label: "HRV", goalText: "≥ 基线下限" },
  { key: "sedentary", label: "久坐", goalText: "≤ 8h" }
];

/* Garmin 准备度档位：设备自己给的五档。**不套用 index.json 的 score 阈值**——
   综合指数与准备度是两个体系（行为达标度 vs 恢复/负荷准备度），
   用我们的线去改写设备判断，会把「09-07 综合指数 92 / 准备度 41 LOW」
   这类背离直接抹平，而那正是两项并排的意义所在。
   档位缺失时退回按分数分档（两条线由本机实测反推：41 LOW、58 MODERATE、
   74 MODERATE、76 HIGH ⇒ 50 / 75；拿不到设备档位就不断言 PRIME）。 */
export var READY = {
  POOR: { label: "差", color: "#B91C1C" },
  LOW: { label: "低", color: "#EF4444" },
  MODERATE: { label: "中", color: "#B45309" },
  HIGH: { label: "高", color: "#10B981" },
  PRIME: { label: "极高", color: "#047857" }
};

export function readyOf(level, score) {
  if (READY[level]) return READY[level];
  if (score == null) return { label: "—", color: "#9CA3AF" };
  return score >= 75 ? READY.HIGH : score >= 50 ? READY.MODERATE : READY.LOW;
}

/* 指标定义表。dir: above=越高越好 / below=越低越好 / band=落在个人带内最好；
   agg: 单元聚合方式（日/周/月尺度下都是同一个量，只是粒度不同）。
   detail: 日尺度下卡内展开的细分渲染器名（见 detail.js）。 */
export var METRICS = [
  {
    key: "sleep_h", label: "睡眠时长", unit: "h", dir: "above", agg: "mean", detail: "sleep",
    goal: function (ix) { return ix.goals.sleep_h; },
    goalText: function (ix) { return "目标 ≥ " + ix.goals.sleep_h + "h"; },
    fmt: function (v) { return v == null ? "—" : v.toFixed(1) + "h"; }
  },
  {
    key: "hrv", label: "HRV 心率变异性", unit: "ms", dir: "band", agg: "median", detail: "hrv",
    band: function (ix) { return ix.baselines.hrv; },
    goalText: function (ix) { return "个人区间 " + ix.baselines.hrv.low + "–" + ix.baselines.hrv.high + " ms"; },
    fmt: function (v) { return v == null ? "—" : Math.round(v) + "ms"; }
  },
  {
    key: "rhr", label: "静息心率", unit: "bpm", dir: "below", agg: "mean", detail: "rhr",
    goal: function (ix) { return ix.baselines.rhr_ok_max; },
    goalText: function (ix) { return "目标 ≤ " + ix.baselines.rhr_ok_max + " bpm"; },
    fmt: function (v) { return v == null ? "—" : Math.round(v) + "bpm"; }
  },
  {
    key: "steps", label: "步数", unit: "步", dir: "above", agg: "mean", detail: "activity",
    goal: function (ix) { return ix.goals.steps; },
    goalText: function (ix) { return "日均目标 ≥ " + Math.round(ix.goals.steps / 1000) + "k"; },
    fmt: function (v) { return v == null ? "—" : Math.round(v).toLocaleString("en-US"); }
  },
  {
    key: "sedentary_min", label: "久坐时长", unit: "", dir: "below", agg: "mean", detail: "sedentary",
    goal: function (ix) { return ix.goals.sedentary_min; },
    goalText: function (ix) { return "日均上限 ≤ " + (ix.goals.sedentary_min / 60) + "h"; },
    fmt: function (v) { return v == null ? "—" : (v / 60).toFixed(1) + "h"; }
  },
  {
    key: "stress", label: "平均压力", unit: "", dir: "below", agg: "mean", detail: "stress",
    goal: function (ix) { return ix.baselines.stress_avg_good; },
    goalText: function (ix) { return "越低越放松（≤ " + ix.baselines.stress_avg_good + "）"; },
    fmt: function (v) { return v == null ? "—" : String(Math.round(v)); }
  },
  {
    key: "active_kcal", label: "活动消耗", unit: "kcal", dir: null, agg: "mean", detail: "energy",
    goalText: function () { return "越高说明活动量越大（无独立目标线）"; },
    fmt: function (v) { return v == null ? "—" : Math.round(v) + "kcal"; }
  },
  {
    key: "intensity_min", label: "中高强度分钟", unit: "min", dir: "above", agg: "mean", detail: null,
    goal: function (ix) { return ix.goals.intensity_min; },
    goalText: function (ix) { return "日均目标 ≥ " + ix.goals.intensity_min + " min（WHO 周 150min ÷ 7）"; },
    fmt: function (v) { return v == null ? "—" : Math.round(v) + "min"; }
  }
];

export var ICONS = {
  rhr: '<svg width="22" height="22" viewBox="0 0 44 44" fill="none"><path d="M22 32.5C22 32.5 11.5 26.4 11.5 19.5C11.5 16 14.2 13.5 17.2 13.5C19 13.5 20.7 14.4 22 16C23.3 14.4 25 13.5 26.8 13.5C29.8 13.5 32.5 16 32.5 19.5C32.5 26.4 22 32.5 22 32.5Z" stroke="#10B981" stroke-width="2.2" stroke-linejoin="round"/><path d="M14.5 21.5H18L19.5 18.5L22.5 25L24.5 21.5H29.5" stroke="#10B981" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  hrv: '<svg width="22" height="22" viewBox="0 0 44 44" fill="none"><path d="M33 22H28.5L25 31L19 13L15.5 22H11" stroke="#10B981" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  spo2: '<svg width="22" height="22" viewBox="0 0 44 44" fill="none"><path d="M22 11L29.2 18.2C31.1 20.1 32.2 22.7 32.2 25.4C32.2 28.1 31.1 30.7 29.2 32.6C27.3 34.5 24.7 35.6 22 35.6C19.3 35.6 16.7 34.5 14.8 32.6C12.9 30.7 11.8 28.1 11.8 25.4C11.8 22.7 12.9 20.1 14.8 18.2L22 11Z" stroke="#F59E0B" stroke-width="2.2" stroke-linejoin="round"/><path d="M22 30.5C20.1 30.5 18.5 28.9 18.5 27" stroke="#F59E0B" stroke-width="2.2" stroke-linecap="round"/></svg>',
  sleep: '<svg width="22" height="22" viewBox="0 0 44 44" fill="none"><path d="M33.5 26.2C32.5 30.9 28.7 34.6 24 35.5C19.3 36.4 14.6 34.4 12 30.6C9.4 26.8 9.4 21.9 12 18.1C14.6 14.3 19.3 12.3 24 13.2C24.5 13.3 24.9 13.7 25 14.2C25.1 14.7 25 15.2 24.6 15.6C22.5 17.7 21.2 20.7 21.2 23.8C21.2 28.5 24.4 32.6 28.9 33.8C29.4 33.9 29.8 34.3 29.9 34.8C30 35.3 29.9 35.8 29.5 36.1" stroke="#F59E0B" stroke-width="2.2" stroke-linecap="round"/></svg>',
  activity: '<svg width="22" height="22" viewBox="0 0 44 44" fill="none"><path d="M14 8V17M10 12.5H18" stroke="#10B981" stroke-width="2.2" stroke-linecap="round"/><path d="M9 34L17 22L23 29L33 16" stroke="#10B981" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  battery: '<svg width="22" height="22" viewBox="0 0 44 44" fill="none"><rect x="8" y="14" width="24" height="16" rx="4" stroke="#10B981" stroke-width="2.2"/><path d="M36 19V25" stroke="#10B981" stroke-width="2.2" stroke-linecap="round"/><path d="M13 19L17 25L21 19L25 25L29 19" stroke="#10B981" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  stress: '<svg width="22" height="22" viewBox="0 0 44 44" fill="none"><circle cx="22" cy="22" r="15" stroke="#F59E0B" stroke-width="2.2"/><circle cx="22" cy="22" r="9" stroke="#F59E0B" stroke-width="2.2" opacity=".55"/><circle cx="22" cy="22" r="3.5" fill="#F59E0B"/></svg>',
  energy: '<svg width="22" height="22" viewBox="0 0 44 44" fill="none"><path d="M24 6L12 25H21L19 38L32 19H23L24 6Z" stroke="#F59E0B" stroke-width="2.2" stroke-linejoin="round"/></svg>',
  hr: '<svg width="22" height="22" viewBox="0 0 44 44" fill="none"><path d="M33 22H28.5L25 31L19 13L15.5 22H11" stroke="#EF4444" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  calendar: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><rect x="3" y="5" width="18" height="16" rx="3" stroke="currentColor" stroke-width="1.8"/><path d="M3 10H21M8 3V7M16 3V7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>'
};
