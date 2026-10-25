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

/* 指标呈现表。**定义不在这里**——dir / agg / goal / band 全部来自
   index.json.metric_defs（后端 references/metrics.json + rules.json 的解析结果）。
   本表只留「怎么画」：卡内细分渲染器、数字格式、目标线的措辞。

   2026-10-25 之前这里同时持有 label/unit/dir/agg/goal/band 六份定义副本，
   与后端各算一套：同一份数据，主页画「↑ 7%」、报告写「在误差内」，两边都不算错。 */
var ORDER = ["sleep_h", "hrv", "rhr", "steps", "sedentary_min", "stress",
             "active_kcal", "intensity_min"];

var PRESENT = {
  sleep_h: {
    detail: "sleep",
    fmt: function (v) { return v == null ? "—" : v.toFixed(1) + "h"; },
    goalText: function (g) { return "目标 ≥ " + g + "h"; }
  },
  hrv: {
    detail: "hrv",
    fmt: function (v) { return v == null ? "—" : Math.round(v) + "ms"; },
    goalText: function (g, ix, m) {
      var b = m.band();
      return b ? "个人区间 " + b.low + "–" + b.high + " ms" : "个人区间未知";
    }
  },
  rhr: {
    detail: "rhr",
    fmt: function (v) { return v == null ? "—" : Math.round(v) + "bpm"; },
    goalText: function (g) { return "目标 ≤ " + g + " bpm"; }
  },
  steps: {
    detail: "activity",
    fmt: function (v) { return v == null ? "—" : Math.round(v).toLocaleString("en-US"); },
    goalText: function (g) { return "日均目标 ≥ " + Math.round(g / 1000) + "k"; }
  },
  sedentary_min: {
    detail: "sedentary",
    fmt: function (v) { return v == null ? "—" : (v / 60).toFixed(1) + "h"; },
    goalText: function (g) { return "日均上限 ≤ " + (g / 60) + "h"; }
  },
  stress: {
    detail: "stress",
    fmt: function (v) { return v == null ? "—" : String(Math.round(v)); },
    goalText: function (g) { return "越低越放松（≤ " + g + "）"; }
  },
  active_kcal: {
    detail: "energy",
    fmt: function (v) { return v == null ? "—" : Math.round(v) + "kcal"; },
    goalText: function () { return "越高说明活动量越大（无独立目标线）"; }
  },
  intensity_min: {
    detail: null,
    fmt: function (v) { return v == null ? "—" : Math.round(v) + "min"; },
    goalText: function (g) { return "日均目标 ≥ " + g + " min（WHO 周 150min ÷ 7）"; }
  }
};

/* 老 index.json（没有 metric_defs）时的兜底：数值仍来自 ix.goals / ix.baselines，
   与改造前的行为一致。**这是发布顺序的保险**——先发 js、再推数据，中间那段
   线上仍是旧 index，不能因为缺 metric_defs 就白屏或丢掉目标线。 */
var FALLBACK = {
  sleep_h: { key: "sleep_h", label: "睡眠时长", unit: "h", dir: "above", agg: "mean",
             goalOf: function (ix) { return ix.goals.sleep_h; } },
  hrv: { key: "hrv_ms", label: "HRV 心率变异性", unit: "ms", dir: "band", agg: "median",
         bandPersonal: true },
  rhr: { key: "rhr_bpm", label: "静息心率", unit: "bpm", dir: "below", agg: "mean",
         goalOf: function (ix) { return ix.baselines.rhr_ok_max; } },
  steps: { key: "steps_daily", label: "步数", unit: "步", dir: "above", agg: "mean",
           goalOf: function (ix) { return ix.goals.steps; } },
  sedentary_min: { key: "sedentary_min", label: "久坐时长", unit: "", dir: "below", agg: "mean",
                   goalOf: function (ix) { return ix.goals.sedentary_min; } },
  stress: { key: "stress_avg", label: "平均压力", unit: "", dir: "below", agg: "mean",
            goalOf: function (ix) { return ix.baselines.stress_avg_good; } },
  active_kcal: { key: "active_kcal", label: "活动消耗", unit: "kcal", dir: null, agg: "mean" },
  intensity_min: { key: "intensity_min", label: "中高强度分钟", unit: "min", dir: "above",
                   agg: "mean", goalOf: function (ix) { return ix.goals.intensity_min; } }
};

/* 指标列表（随 index.json 的 metric_defs 变）。每次渲染调用一次，不缓存：
   缓存会让「换了数据但没刷新」变成静默的口径错位。 */
export function metrics(ix) {
  var defs = (ix && ix.metric_defs) || {};
  var byFk = {};
  Object.keys(defs).forEach(function (k) {
    var d = defs[k];
    if (d && d.frontend_key) byFk[d.frontend_key] = d;
  });
  return ORDER.map(function (fk) {
    var p = PRESENT[fk] || {};
    var d = byFk[fk] || FALLBACK[fk] || { key: fk, label: fk };
    return {
      key: fk,
      metric: d.key || fk,
      label: d.label || fk,
      unit: d.unit || "",
      dir: d.dir === undefined ? null : d.dir,
      agg: d.agg || "mean",
      goal: function () {
        if (typeof d.goalOf === "function") return d.goalOf(ix);
        return d.goal == null ? null : d.goal;
      },
      band: function () {
        if (d.band === "@personal" || d.bandPersonal) {
          return ix && ix.baselines ? ix.baselines.hrv : null;
        }
        return d.band || null;
      },
      goalText: function () {
        var g = this.goal();
        return typeof p.goalText === "function" ? p.goalText(g, ix, this) : "";
      },
      fmt: p.fmt,
      detail: p.detail || null
    };
  });
}

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
