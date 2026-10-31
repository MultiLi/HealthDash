/* 通用 HTML 组件：指标卡 / 事实清单 / 子项评分 chips。
   卡片结构（含「收起细分」按钮）统一在这里改。
   概览页的 KPI 卡与均值 chip 在 overview.js 内联生成（它们随观测窗口变化）。 */
import { ICONS } from "./constants.js?v=7b20e1ba";
import { esc } from "./utils.js?v=7b20e1ba";

/* 指标卡：m = {icon,label,sub,state,value,unit,note,detail} */
export function card(m) {
  var pending = m.state === "pending";
  var value = pending ? "待出数" : m.value;
  return '<div class="metric-card">' +
    '<div class="metric-top">' +
      '<div class="metric-icon ' + m.state + '">' + (ICONS[m.icon] || ICONS.activity) + "</div>" +
      '<div class="metric-label">' + esc(m.label) + (m.sub ? '<span class="metric-sublabel">' + esc(m.sub) + "</span>" : "") + "</div>" +
    "</div>" +
    '<div class="metric-value-row">' +
      '<span class="metric-value ' + m.state + '">' + esc(value) + "</span>" +
      (m.unit && !pending ? '<span class="metric-unit">' + esc(m.unit) + "</span>" : "") +
    "</div>" +
    '<div class="metric-note ' + m.state + '">' + esc(m.note) + "</div>" +
    (m.detail ? '<div class="metric-detail">' + m.detail + "</div>" +
      '<button class="metric-more open" type="button" aria-expanded="true"><span>收起细分</span><svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M3 5.5L7 9.5L11 5.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></button>' : "") +
    "</div>";
}

/* 键值对清单：pairs [[k, v], ...] */
export function facts(pairs) {
  return '<div class="fact-grid">' + pairs.map(function (p) {
    return '<div class="fact"><span class="fact-k">' + esc(p[0]) + '</span><span class="fact-v">' + esc(p[1]) + "</span></div>";
  }).join("") + "</div>";
}

/* 子项评分 chips：list [{k, q}] */
export function subChips(list) {
  return '<div class="chip-row">' + list.map(function (c) {
    return '<span class="chip">' + esc(c.k) + " · " + esc(c.q) + "</span>";
  }).join("") + "</div>";
}
