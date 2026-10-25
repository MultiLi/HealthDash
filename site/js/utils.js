/* 小工具：转义 / DOM 查找 / 数值占位 / 分钟时长格式化。 */
export function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

export function $(id) { return document.getElementById(id); }

export function dash(v, nd) {
  return v == null ? "—" : (nd ? Number(v).toFixed(nd) : v);
}

/* 分钟 -> "2h35m" / "45m" */
export function fm(min) {
  var h = Math.floor(min / 60), m = min % 60;
  return h ? h + "h" + (m ? m + "m" : "") : m + "m";
}
