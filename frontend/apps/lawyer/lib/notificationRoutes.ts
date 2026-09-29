import type { NotificationItem } from "@nlaw/ui";

/**
 * 通知 -> 目标路由（律师端）。
 *
 * ## 为什么放在应用侧而不是 `packages/ui`
 *
 * `NotificationCenter` 是纯展示组件，它**不应该知道任何 URL 结构**——
 * 四端（web / lawyer / admin / im）的路由表完全不同。因此组件只负责
 * 把「用户点了哪条通知」抛出来，路由映射由各应用自己提供。
 *
 * ## 为什么「按类型优先、按 ref 兜底」
 *
 * `type` 回答的是「用户该去做什么」，`ref_type` 只回答「这条通知挂着
 * 哪个对象」。前者更具体，所以优先。反过来写会出两类问题：
 *
 * 1. **死分支**：`DISPATCH_CREATED` 与 `CASE_ACCEPTED` 的 `ref_type` 都是
 *    `"case"`，若先判 `ref_type === "case"`，那么「派单待接」会被送到
 *    案件详情页——而案件此时**尚未接单**，详情页没有可操作内容，
 *    用户还得自己找派单池。按类型优先才能把它正确送到 `/dispatches`。
 * 2. **漏映射**：`REVIEW_DECIDED` 的 `ref_type` 是 `"review"`，而
 *    `REVIEW_REQUIRED` 的 `ref_type` 是 `target_type.value`（如
 *    `"CASE_ANALYSIS"`）。只按 `ref_type` 映射必然漏掉其中一种，
 *    表现为「点通知没反应」——静默失效，最难排查的一类。
 *
 * ## 与后端的对应关系（事实基准，改动需同步）
 *
 * | 通知类型 | 后端 ref_type / ref_id | 目标路由 |
 * |---|---|---|
 * | `DISPATCH_CREATED` | `case` / `case.id` | `/dispatches` |
 * | `CASE_ACCEPTED` | `case` / `case.id` | `/cases/{id}` |
 * | `EVIDENCE_MISSING` | `case` / `case.id` | `/cases/{id}` |
 * | `REVIEW_REQUIRED` | `target_type.value` / `target_id` | `/reviews` |
 * | `REVIEW_DECIDED` | `review` / `review.id` | `/reviews` |
 * | `CASE_ARCHIVED` | `case` / `case.id` | `/archives` |
 * | `QUOTA_WARNING` | 调用方传入（如 `conversation`）/ 可为空 | 无（本端暂无用量页） |
 * | `WORK_ORDER_CREATED` | 调用方传入 / 可为空 | 无（本端暂无工单页） |
 * | `DOCUMENT_CONFIRMED` | **后端尚未接线** | 无 |
 *
 * 返回 `null` 表示「没有合适的落点」——此时只标记已读，不跳转，
 * 前端也不显示「点击前往」提示。**不要**为了「都有跳转」而兜底到
 * 首页：把用户送到一个与他刚点的通知无关的页面，比不跳更糟。
 */
export function notificationHref(n: NotificationItem): string | null {
  switch (n.type) {
    // 案件尚未接单，详情页无内容；派单池才是可操作的地方
    case "DISPATCH_CREATED":
      return "/dispatches";

    // 复核：待办与结论都落在复核队列（列表内含状态筛选）
    case "REVIEW_REQUIRED":
    case "REVIEW_DECIDED":
      return "/reviews";

    case "CASE_ARCHIVED":
      return "/archives";

    // 本端暂无用量看板 / 计费工单页面，先不跳转
    case "QUOTA_WARNING":
    case "WORK_ORDER_CREATED":
      return null;
  }

  // 兜底：已接单案件的详情页
  if (n.ref_type === "case" && n.ref_id != null) return `/cases/${n.ref_id}`;

  return null;
}
