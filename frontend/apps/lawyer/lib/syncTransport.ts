import { ApiError, authed } from "@nlaw/sdk";
import type { SyncTask } from "@nlaw/ui";

/**
 * 律师端离线同步任务的上行通道。
 *
 * ## 为什么需要它
 *
 * 律师的工作场景**天然弱网**：法院走廊、看守所会见室、地下车库、电梯。
 * 而这条主线上的关键动作（提交复核 / 出结论 / 归档）恰好都是**一次性、
 * 有时效**的——错过窗口期（比如上诉期）就不是「晚一点再点」的问题。
 * 因此这些动作在断网时不能只是弹一个「操作失败」然后丢掉。
 *
 * ## 为什么 transport 必须由应用提供，而不是组件库给个默认实现
 *
 * 「这条任务该发到哪个端点」是应用级知识，组件库不可能猜。给默认 no-op
 * 会让任务安静地积压在队列里永远不上行，而界面还在承诺「恢复后自动同步」。
 * 一个永远不同步的同步队列比没有队列更糟。
 *
 * ## 哪些动作**不**入队，以及为什么
 *
 * | 动作 | 是否入队 | 原因 |
 * |---|---|---|
 * | `review.submit` | ✅ | 提交复核；无 review id 时先 `ensure` 再 submit，见下 |
 * | `review.decide` | ✅ | 出复核结论，幂等性由后端 FSM 保证（重复调用会被状态机拒绝） |
 * | `review.archive` | ✅ | 归档，同上 |
 * | `notification.read` / `readAll` | ✅ | 标记已读，天然幂等，重放无副作用 |
 * | `analyses/case/{id}/generate` | ❌ | 会**新建生成任务**，重放可能产生重复任务；且它本身是异步作业，用户重新点一次的成本极低 |
 * | `archives/cases/{id}` | ❌ | 案件级归档会写卷宗号，重复提交的后果未经确认，不放进「自动重放」通道 |
 * | `archives/cases/{id}/hearing-pack` | ❌ | 导出动作的幂等性未确认 |
 *
 * 判断原则：**只有「重放一次无害」的动作才允许进自动重试队列**。
 * 语义不确定的写操作宁可让用户手动重试，也不要自动重放出一个脏数据。
 */

/**
 * 队列任务的负载。用 `op` 做判别字段——`SyncTask` 本身只有
 * `id/label/payload/section`，操作类型放在 payload 里可以让
 * `SyncTask` 保持对业务无感知（组件库不该知道 `review.decide` 是什么）。
 */
export type LawyerSyncPayload =
  | {
      op: "review.submit";
      /** 已存在的复核任务 id；为空则先走 `ensure` 创建 */
      reviewId?: number;
      /** 创建复核任务所需的信息（离线提交时必然要带） */
      ensure?: {
        targetType: string;
        targetId: number;
        caseId: number;
        requiredLevel: string;
      };
      comment?: string;
    }
  | {
      op: "review.decide";
      reviewId: number;
      decision: "APPROVED" | "REVISION_REQUESTED";
      comment?: string;
    }
  | { op: "review.archive"; reviewId: number }
  | { op: "notification.read"; notificationId: number }
  | { op: "notification.readAll" };

/**
 * 任务分区 id —— 必须与 `apps/lawyer/app/(app)/layout.tsx` 里
 * `TABS` / `NAV` 的 id 一致，否则琥珀角标会挂到一个不存在的页签上（静默失效）。
 */
export const SYNC_SECTION = {
  reviews: "reviews",
  notifications: "notifications",
} as const;

/** 是否为「网络不可达」类失败（区别于服务端明确给出的业务错误）。 */
export function isOfflineFailure(e: unknown): boolean {
  // 浏览器明确报告离线：最可靠的信号
  if (typeof navigator !== "undefined" && navigator.onLine === false) return true;
  // fetch 在网络层失败时抛 TypeError，SDK 不会把它包装成 ApiError。
  // 反过来，只要拿到了 HTTP 响应（含 4xx/5xx），就说明网络是通的，
  // 那是业务错误——重试同样的请求只会得到同样的结果，不该入队。
  return !(e instanceof ApiError);
}

/**
 * 单条任务的上行通道。抛错即视为失败并计入重试（见 `SyncQueueProvider`）。
 *
 * **无法识别的任务要抛错，不能静默返回成功。** 静默返回会让任务从队列里
 * 消失而什么都没发生——用户看到「已同步」，服务端却没有这条记录。
 * 抛错至少会把它标记为 failed，用户能在同步队列面板里看到并处理。
 */
export async function lawyerSyncTransport(task: SyncTask): Promise<void> {
  const p = task.payload as LawyerSyncPayload | undefined;
  if (!p || typeof p !== "object" || !("op" in p)) {
    throw new Error("无法识别的同步任务");
  }

  switch (p.op) {
    case "review.submit": {
      let reviewId = p.reviewId;
      if (reviewId === undefined) {
        if (!p.ensure) throw new Error("同步任务缺少复核任务标识");
        // 离线时 `ensure` 也没成功，所以这里补做一次：先建任务再提交。
        const r = await authed<{ id: number }>("/api/v1/reviews/ensure", {
          method: "POST",
          query: {
            target_type: p.ensure.targetType,
            target_id: p.ensure.targetId,
            case_id: p.ensure.caseId,
            required_level: p.ensure.requiredLevel,
          },
        });
        reviewId = r.id;
      }
      await authed(`/api/v1/reviews/${reviewId}/submit`, {
        method: "POST",
        body: { comment: p.comment ?? "提交复核" },
      });
      return;
    }

    case "review.decide":
      await authed(`/api/v1/reviews/${p.reviewId}/decide`, {
        method: "POST",
        body: { decision: p.decision, comment: p.comment },
      });
      return;

    case "review.archive":
      await authed(`/api/v1/reviews/${p.reviewId}/archive`, { method: "POST" });
      return;

    case "notification.read":
      await authed(`/api/v1/notifications/${p.notificationId}/read`, { method: "POST" });
      return;

    case "notification.readAll":
      await authed("/api/v1/notifications/read-all", { method: "POST" });
      return;
  }
}
