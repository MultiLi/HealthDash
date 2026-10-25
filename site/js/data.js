/* 数据取数链路：**同源端点优先，随包静态副本兜底**。
 *
 * 两个来源（2026-09-16 起）：
 *   ① 端点 api/data/*  —— server.py 提供，no-store。构建完 push 一次即生效，**不用重新上线**；
 *      路径不带 .json 扩展名，绕开「按扩展名缓存」的边缘规则。
 *   ② 静态副本       —— index.json / daily/MM.json，随站点一起发布。
 *      端点挂了（本地直接开静态服务、或服务端异常）时回落，页面永远不会白。
 *
 * 兜底不是「降级糊弄」：它是同一份构建产物的两个位置，内容同源，只是新鲜度不同。
 * 走了哪条路记在 store.dataFrom，顶部同步胶囊会显式标注——不允许静默用旧数据。
 */
import { store } from "./store.js?v=ceda129d";
import { monthKey } from "./dateutil.js?v=ceda129d";

function getJSON(url) {
  return fetch(url, { cache: "no-store" }).then(function (r) {
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  });
}

/* 端点优先；端点 4xx/5xx 或网络异常都回落静态副本 */
function pick(apiUrl, staticUrl) {
  return getJSON(apiUrl).then(
    function (j) { store.dataFrom = "endpoint"; return j; },
    function (e) {
      store.lastEndpointError = (e && e.message) || String(e);
      return getJSON(staticUrl).then(function (j) {
        store.dataFrom = "bundled";
        return j;
      });
    }
  );
}

/* 首屏唯一载荷：逐日轻量索引 */
export function loadIndex() {
  return pick("api/data/index", "index.json?b=aa727e30");
}

/* 月度详情分片的按需加载与缓存：只有在真的要展开某天时才拉当月分片 */
export function ensureShard(dateStr) {
  var month = monthKey(dateStr);
  if (store.shards[month]) return Promise.resolve(store.shards[month]);
  if (store.pending[month]) return store.pending[month];
  store.pending[month] = pick("api/data/daily/" + month, "daily/" + month + ".json?b=aa727e30")
    .then(function (j) {
      store.shards[month] = j.days || {};
      delete store.pending[month];
      return store.shards[month];
    })
    .catch(function (e) {
      delete store.pending[month];
      throw e;
    });
  return store.pending[month];
}

/* 已加载则同步取，未加载返回 null */
export function dayOf(dateStr) {
  var shard = store.shards[monthKey(dateStr)];
  return shard ? shard[dateStr] || null : null;
}

/* 该月分片**是否已经加载完**。
   用来区分「还没加载」（该去拉）与「加载了但这一天没记录」（该给空态）。
   缺了它，「加载了但没这天」会被当成前者，触发反复重试的死循环。 */
export function hasShard(dateStr) {
  return !!store.shards[monthKey(dateStr)];
}
