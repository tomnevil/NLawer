/**
 * 通知实时通道的**纯逻辑层**：无 React、无副作用、给定输入即可断言输出。
 *
 * 抽出来的动机：`NotificationCenter` 已经同时承担「渲染 + 轮询 + 交互」，
 * 再把 URL 拼接、退避计算、关闭码判定、列表合并塞进去，任何一处出错都只能
 * 在组件里靠肉眼追。这里只留下可以独立单测的部分。
 *
 * ## 为什么退避必须带抖动（jitter）
 *
 * 不带抖动的退避是**同步**的：所有客户端会在同一毫秒醒来重连。服务端重启 /
 * 发版后，成百上千个标签页会在恢复的第一秒同时建连，把连接配额瞬间打满
 * （本端点单用户上限 5，全局亦有配额）——先连上的被挤掉、被挤掉的再退避、
 * 再同一时刻回来，形成一轮轮同步的重试浪潮，恢复时间被显著拉长。
 * ±20% 的抖动把这一波摊平到 0.8x~1.2x 的区间：代价是重连平均慢一点点，
 * 收益是服务端不会在恢复瞬间被自己的客户端压垮。
 *
 * ## 为什么需要「终止关闭码」这个概念
 *
 * 断线分两类，**必须区别对待**：
 * - **可恢复**（网络抖动、服务端下线、空闲回收）：退避重连是对的。
 * - **不可恢复**（`1008` 令牌无效、`4429` 单用户连接数超限）：重连**永远不会
 *   成功**。若仍按退避重试，一个令牌过期的标签页会以 30s 一次的频率永久敲打
 *   服务端；`4429` 更糟——它已经说明「你占的连接太多」，重试只会让该用户的
 *   连接数更难降下来。
 *
 * 把「不可恢复」显式建模成 `isTerminalCloseCode`，命中即**永久降级为轮询**
 * （轮询路径自带 401→刷新，能自愈令牌过期；连接数超限对轮询毫无影响）。
 * 这是把「重试风暴」从一个只能靠运行时观察的问题，变成一行可审查的判断。
 */

/* ------------------------------------------------------------------ 类型 */

/**
 * 与后端 `schemas/notification.NotificationOut` 对齐。
 *
 * 定义在本模块而非组件内：REST 列表项与 WS `notification` 帧的 `data`
 * **形状完全一致**（后端刻意如此），两侧共用一个定义才能保证「推来的」
 * 与「拉来的」不会因为某一边加了字段而渲染出两种结果。
 */
export interface NotificationItem {
  id: number;
  type: string;
  title: string;
  content?: string | null;
  ref_type?: string | null;
  ref_id?: number | null;
  payload?: Record<string, unknown> | null;
  is_read: boolean;
  read_at?: string | null;
  created_at?: string | null;
}

/** 服务端 → 客户端的帧。`type` 是判别式，用于穷尽 switch。 */
export type NotificationWsFrame =
  | {
      type: "connected";
      data?: { user_id?: number; unread_total?: number; latest_id?: number };
    }
  | { type: "notification"; data?: NotificationItem }
  | { type: "ping" }
  | { type: "error"; message?: string };

/* ------------------------------------------------------------------ 常量 */

/** 重连退避的基数（attempt=0 时的延迟）。 */
export const RECONNECT_BASE_MS = 1000;
/** 重连退避的**上限**（抖动在其之上叠加，见 `nextReconnectDelay`）。 */
export const RECONNECT_MAX_MS = 30000;
/** 抖动幅度：±20%。 */
export const RECONNECT_JITTER_RATIO = 0.2;
/** 保留的通知条数上限。长驻标签页不能因为「从不刷新」而无限增长。 */
export const NOTIFICATION_LIST_CAP = 50;
/** 页面隐藏超过该时长后，回到前台要重建连接并补拉（socket 可能已被静默回收）。 */
export const HIDDEN_RECONNECT_MS = 5 * 60 * 1000;

/** 实时通道路径。与后端 `app/api/v1/ws.py` 的 `router.prefix + path` 一致。 */
const NOTIFICATIONS_WS_PATH = "/api/v1/ws/notifications";

/* ------------------------------------------------------------------ 工具 */

/**
 * 由 HTTP 基址推导 WS 端点。鉴权走查询参数（后端刻意不提供「订阅他人」的入参）。
 *
 * `http:`→`ws:`、`https:`→`wss:`；结尾斜杠要剥掉，否则会拼出 `//api/...`
 * （部分反向代理会把双斜杠当成不同的路由而 404）。token 必须 encode——
 * JWT 虽以 base64url 为主，但一旦出现 `+` `/` `=` 未编码就会截断查询串。
 */
export function buildNotificationsWsUrl(apiBase: string, token: string): string {
  const trimmed = apiBase.replace(/\/+$/, "");
  const wsBase = trimmed.replace(/^https:/i, "wss:").replace(/^http:/i, "ws:");
  return `${wsBase}${NOTIFICATIONS_WS_PATH}?token=${encodeURIComponent(token)}`;
}

/**
 * 第 `attempt` 次重连的等待时长（ms），`attempt` 从 0 开始。
 *
 * `1000 * 2**attempt`，先**截断到 30s**，再叠加 ±20% 抖动。顺序是有意的：
 * 抖动加在已截断的值上，所以实际上限是 36s——抖动若加在截断之前，
 * 上限就会随 attempt 无限增长，退避形同虚设。
 *
 * `rand` 可注入，测试里传常量即可得到确定值（`Math.random` 无法断言）。
 */
export function nextReconnectDelay(attempt: number, rand: () => number = Math.random): number {
  const n = Number.isFinite(attempt) && attempt > 0 ? Math.floor(attempt) : 0;
  const base = Math.min(RECONNECT_BASE_MS * 2 ** n, RECONNECT_MAX_MS);
  // rand() ∈ [0,1) ⇒ 系数 ∈ [-1,1) ⇒ 幅度 ∈ [-20%, +20%)
  const jitter = base * RECONNECT_JITTER_RATIO * (rand() * 2 - 1);
  return Math.max(0, Math.round(base + jitter));
}

/**
 * 该关闭码是否**不可恢复**。
 *
 * 命中即停止重连（见模块 docstring）。`undefined`（异常路径下浏览器可能不给
 * code）视为可恢复：不知道原因时按网络抖动处理，比永久放弃更安全。
 */
export function isTerminalCloseCode(code: number | undefined): boolean {
  return code === 1008 || code === 4429;
}

/**
 * 把一批通知并入已有列表：按 `id` 去重 → 倒序（新的在前）→ 截断到
 * `NOTIFICATION_LIST_CAP`。
 *
 * ## 为什么是「合并」而不是「重拉」
 *
 * 推送是增量的，重拉整表会重建 DOM、打断用户正在看的滚动位置，且弱网下
 * 一次 20 条的往返远贵于一条推送。合并是 O(n) 的内存操作，代价恒定。
 *
 * ## 冲突时为什么保留「已读」
 *
 * 同一 id 可能同时来自 WS 推送与 `since_id` 补拉。两者都是服务端数据，
 * 理论上一致；唯一会不一致的是**本地乐观更新**——用户刚点了一条，前端已置
 * `is_read: true` 并发出 `POST /read`，而此时补拉的响应是在该 POST 提交**之前**
 * 算出来的，返回的仍是未读。若让「后到的覆盖先到的」，这条通知会闪回未读、
 * 角标也回弹，而代码库明确把「角标可信度」列为硬要求。
 * 因此 `is_read` 取**单调或**：读过就是读过，服务端稍后返回的 `read_at`
 * 自然会补上时间戳。
 *
 * `incoming` 为空时也会走一遍去重与截断，用于把长驻列表收敛回上限。
 */
export function mergeNotifications(
  existing: readonly NotificationItem[],
  incoming: readonly NotificationItem[],
): NotificationItem[] {
  const byId = new Map<number, NotificationItem>();
  for (const item of existing) byId.set(item.id, item);
  for (const item of incoming) {
    const prev = byId.get(item.id);
    if (!prev) {
      byId.set(item.id, item);
      continue;
    }
    byId.set(item.id, {
      ...item,
      is_read: item.is_read || prev.is_read,
      read_at: item.read_at ?? prev.read_at,
    });
  }
  return Array.from(byId.values())
    .sort((a, b) => b.id - a.id)
    .slice(0, NOTIFICATION_LIST_CAP);
}
