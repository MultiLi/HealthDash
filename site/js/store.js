/* 全局共享状态。
 *
 * 观测状态只有两个自由度：**锚定日期 anchor + 尺度 scale**。
 * 尺度是全局形式——它一变，整页每一个指标卡的粒度、序列、达标统计一起变。
 * 「概览」与「某日」不是两个页面，是同一个视图在 month / day 尺度下的样子。
 *
 * rows 含进行中的今天（日历与主值要用）；closed 仅完整日（统计口径唯一来源）。
 */
export var store = {
  ix: null,
  rows: [],
  closed: [],
  byDate: {},
  shards: {},
  pending: {},
  scale: "month",
  anchor: null,
  month: null,      // 日历当前显示的月份，与 anchor 解耦（允许只翻月不选日）
  dataFrom: null,   // "endpoint" | "bundled"：本次取数走的是热更新端点还是随包静态副本
  cloud: null,      // 云端一致性校验结果：{status, n, n_diff, ...}；null = 还没查过
  lastEndpointError: null
};
