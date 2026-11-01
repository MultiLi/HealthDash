/* 查询视图：把后端算好的 saved view 渲染成一张表。
 *
 * **本模块一个字都不算。** 值、达标、变化、覆盖率全部来自 views/<id>.json，
 * 由后端 `query.run()` 算好（判定权不下放，见 docs/query-interface.md）。
 * 这里只做三件事：查 ?view= 参数、拉 JSON、把它排成表。
 *
 * 空态纪律沿用：`na` 是「不给结论」，不是「没达标」；两者在表格里必须长得不一样
 * （一个是灰字「不给结论」，一个是红字「未达标」）。看不清这两者的区别，
 * 就会把「没测到」读成「没做到」。 */
import { esc } from "./utils.js?v=4ed2490f";
import { loadJSON } from "./data.js?v=4ed2490f";

var JUDGE = {
  ok: ["达标", "q-ok"],
  no: ["未达标", "q-no"],
  na: ["不给结论", "q-na"]
};

var VERDICT = {
  changed: "有变化",
  "within-noise": "在误差内",
  insufficient: "样本不足",
  uncalibrated: "未标定",
  suppressed: "不做环比"
};

export function viewId(href) {
  var u = String(href == null ? (typeof location !== "undefined" ? location.search : "") : href);
  var m = /[?&]view=([A-Za-z0-9_-]+)/.exec(u);
  return m ? m[1] : null;
}

function fmt(v, unit) {
  if (v == null) return "—";
  var s = Math.abs(v - Math.round(v)) < 1e-9 ? String(Math.round(v)) : String(Math.round(v * 100) / 100);
  return unit === "bpm" || unit === "步" ? s : s;
}

function judgeCell(r) {
  if (r.judge == null) return '<span class="q-dash">—</span>';
  var j = JUDGE[r.judge] || [r.judge, "q-na"];
  var rate = "";
  if (r.judge_rate != null) {
    rate = ' <span class="q-rate">' + Math.round(r.judge_rate * 100) + "%</span>";
  }
  return '<span class="' + j[1] + '">' + esc(j[0]) + "</span>" + rate;
}

function deltaCell(r) {
  if (!r.delta_verdict) return '<span class="q-dash">—</span>';
  var label = VERDICT[r.delta_verdict] || r.delta_verdict;
  var d = r.delta_v == null ? "" : "Δ" + (r.delta_v > 0 ? "+" : "") + r.delta_v.toFixed(2);
  var tip = r.delta_note ? ' title="' + esc(r.delta_note) + '"' : "";
  return '<span class="q-delta"' + tip + ">" + esc(label) +
    (d ? ' <span class="q-dv">' + esc(d) + "</span>" : "") + "</span>";
}

function condText(q) {
  var cs = (q && q.conditions) || [];
  if (!cs.length) return "";
  var parts = cs.map(function (c) {
    var neg = c.negate ? "非" : "";
    if (c.kind === "metric") {
      if (c.range) return neg + c.metric + " ∈ [" + c.range.join(", ") + "]";
      return neg + c.metric + " " + c.op + " " + c.value;
    }
    if (c.kind === "event") {
      var bits = [];
      if (c.type) bits.push("type=" + c.type);
      if (c.metric) bits.push(c.metric + " " + (c.op || "存在"));
      if (c.contains) bits.push("含「" + c.contains + "」");
      if (c.shift) bits.push("前 " + c.shift + " 日");
      return neg + "事件 " + bits.join("·");
    }
    return neg + "采集 " + (c.level_in ? c.level_in.join("/") : "不含 " + (c.exclude_levels || []).join("/"));
  });
  return parts.join((q.logic || "and") === "or" ? " 或 " : " 且 ");
}

export function renderView(d) {
  if (!d) return "";
  var head = '<div class="qv-head"><h2 class="qv-title">' + esc(d.title || d.id || "查询视图") +
    '</h2><span class="qv-badge">查询视图</span>' +
    '<a class="qv-back" href="./">← 回到总览</a></div>';

  if (d.error) {
    return '<div class="qv">' + head +
      '<p class="qv-error">这个视图暂时算不出来：<code>' + esc(d.error) + "</code></p>" +
      '<p class="qv-hint">数据不足或口径变更都可能导致；修好后重新构建即可，页面不编数字。</p></div>';
  }

  var w = d.window || {};
  var s = d.selection;
  var meta = [];
  if (s) {
    meta.push("查询范围 " + esc(w.start) + " ~ " + esc(w.end));
    meta.push("命中 <b>" + s.matched + "</b> / 候选 " + s.candidates + " 天");
    if (s.undecidable) meta.push("不可判定 " + s.undecidable + " 天");
  } else {
    meta.push("窗口 " + esc(w.first) + " ~ " + esc(w.last) + "（" + w.days + " 天）");
  }
  meta.push("尺子 " + esc(d.ruler || "-"));
  if (d.min_n) meta.push("最小样本 " + d.min_n.required + " 天");

  var rows = (d.rows || []).map(function (r) {
    return "<tr><td class=\"qv-m\">" + esc(r.label || r.metric) +
      '<span class="qv-k">' + esc(r.metric) + "</span></td>" +
      "<td class=\"qv-v\">" + esc(fmt(r.value)) + "</td>" +
      "<td class=\"qv-n\">" + (r.n == null ? "—" : r.n) + "</td>" +
      "<td class=\"qv-c\">" + (r.coverage == null ? "—" : Math.round(r.coverage * 100) + "%") + "</td>" +
      "<td>" + judgeCell(r) + "</td>" +
      "<td>" + deltaCell(r) + "</td></tr>";
  }).join("");

  var cond = condText(d.query);
  var prov = d.provenance || {};
  var empty = (d.empty || []).length
    ? '<p class="qv-empty">无数据的指标：' + esc(d.empty.join("、")) + "（na ≠ 0，是没有观测，不是零）</p>"
    : "";

  return '<div class="qv">' + head +
    (d.note ? '<p class="qv-note">' + esc(d.note) + "</p>" : "") +
    '<div class="qv-meta">' + meta.join(" · ") + "</div>" +
    (cond ? '<div class="qv-cond">条件：' + esc(cond) + "</div>" : "") +
    '<table class="qv-tab"><thead><tr><th>指标</th><th>值</th><th>n</th><th>覆盖</th><th>达标</th><th>变化</th></tr></thead>' +
    "<tbody>" + (rows || '<tr><td colspan="6" class="q-dash">这个窗口里没有可用的值</td></tr>') + "</tbody></table>" +
    empty +
    '<div class="qv-prov">真值版本 rules ' + esc((prov.rules_hash || "-").slice(0, 8)) +
    " · data " + esc((prov.data_hash || "-").slice(0, 8)) +
    " · engine " + esc((prov.engine_ver || "-").slice(0, 8)) +
    (prov.events_hash ? " · events " + esc(prov.events_hash.slice(0, 8)) : "") +
    " · built " + esc(prov.built_at || "-") + "</div></div>";
}

export function initView() {
  var id = viewId();
  var box = typeof document !== "undefined" && document.getElementById
    ? document.getElementById("query-view") : null;
  if (!id || !box) return Promise.resolve(null);
  /* 与首页数据同一条取数纪律：同源端点优先（热更新即生效），随包静态副本兜底。
     视图是由构建算出来的数据，跟着每晚的 push 走，不需要重新发布。 */
  return loadJSON("api/data/views/" + id, "views/" + id + ".json")
    .then(function (d) {
      box.innerHTML = renderView(d);
      box.hidden = false;
      /* 让浏览器记住「这是视图页」——分享深链时标题也带上视图名，
         否则聊天窗口里所有链接都长一样，没人分得清点进去是哪张表。 */
      document.title = (d.title || d.id) + " · 健康主页";
      return d;
    })
    .catch(function (e) {
      box.innerHTML = '<div class="qv"><p class="qv-error">视图 <code>' + esc(id) +
        "</code> 加载失败：" + esc((e && e.message) || e) + "</p></div>";
      box.hidden = false;
      return null;
    });
}
