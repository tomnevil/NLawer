"use client";

import React from "react";
import { createPortal } from "react-dom";
import { API_BASE, authed, tokenStore } from "@nlaw/sdk";
import { cn } from "../lib/cn";
import {
  HIDDEN_RECONNECT_MS,
  buildNotificationsWsUrl,
  isTerminalCloseCode,
  mergeNotifications,
  nextReconnectDelay,
  type NotificationItem,
  type NotificationWsFrame,
} from "../lib/notificationRealtime";
import { BottomSheet } from "./mobile/BottomSheet";
import { PullToRefresh } from "./mobile/PullToRefresh";

/* ------------------------------------------------------------------ 类型 */

/**
 * 通知条目。定义已迁到 `lib/notificationRealtime.ts`（WS 帧与 REST 列表项
 * 共用同一形状），这里**原样再导出**，调用方无需改动 import 路径。
 */
export type { NotificationItem };

export interface NotificationUnread {
  total: number;
  by_type: Record<string, number>;
  latest_id: number;
}

export interface NotificationCenterProps {
  /**
   * 点击通知后的跳转。**由调用方决定路由**（各端路由表不同），
   * 组件自身不假设任何 URL 结构。不传则仅标记已读。
   */
  onNavigate?: (n: NotificationItem) => void;
  /** 「查看全部」入口 */
  onViewAll?: () => void;
  /**
   * 未读数轮询间隔（ms）。默认 30000；传 0 关闭轮询。
   *
   * 语义**未变**，只是当实时通道连上时会被临时抑制：它现在是
   * 「实时通道不可用时的兜底间隔」，`0` 依然是「不做兜底轮询」。
   */
  pollMs?: number;
  /** 是否使用 WebSocket 实时通道。默认 true；连接不可用时**自动降级为轮询**（pollMs）。 */
  realtime?: boolean;
  /** 低于该宽度走 BottomSheet，否则走下拉面板 */
  mobileBreakpoint?: number;
  className?: string;
}

/* ------------------------------------------------------------------ 图标 */

const Svg: React.FC<React.SVGProps<SVGSVGElement>> = (props) => (
  <svg
    fill="none"
    viewBox="0 0 24 24"
    stroke="currentColor"
    strokeWidth={1.8}
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
    {...props}
  />
);

const BellIcon = (p: React.SVGProps<SVGSVGElement>) => (
  <Svg {...p}>
    <path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
    <path d="M13.7 21a2 2 0 0 1-3.4 0" />
  </Svg>
);

const CloseIcon = (p: React.SVGProps<SVGSVGElement>) => (
  <Svg {...p}>
    <path d="M6 6l12 12M18 6 6 18" />
  </Svg>
);

const CheckIcon = (p: React.SVGProps<SVGSVGElement>) => (
  <Svg {...p}>
    <path d="M20 6 9 17l-5-5" />
  </Svg>
);

const InboxIcon = (p: React.SVGProps<SVGSVGElement>) => (
  <Svg {...p}>
    <path d="M22 12h-6l-2 3h-4l-2-3H2" />
    <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11Z" />
  </Svg>
);

const HandIcon = (p: React.SVGProps<SVGSVGElement>) => (
  <Svg {...p}>
    <path d="M18 11V6a2 2 0 0 0-4 0v5M14 10V4a2 2 0 0 0-4 0v6M10 10.5V6a2 2 0 1 0-4 0v8" />
    <path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2a8 8 0 0 1-8-8" />
  </Svg>
);

const ClipboardCheckIcon = (p: React.SVGProps<SVGSVGElement>) => (
  <Svg {...p}>
    <rect x="8" y="2" width="8" height="4" rx="1" />
    <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
    <path d="m9 14 2 2 4-4" />
  </Svg>
);

const FileTextIcon = (p: React.SVGProps<SVGSVGElement>) => (
  <Svg {...p}>
    <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" />
    <path d="M14 2v6h6M9 13h6M9 17h4" />
  </Svg>
);

const ArchiveIcon = (p: React.SVGProps<SVGSVGElement>) => (
  <Svg {...p}>
    <rect x="2" y="4" width="20" height="5" rx="1" />
    <path d="M4 9v9a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9M10 13h4" />
  </Svg>
);

const AlertIcon = (p: React.SVGProps<SVGSVGElement>) => (
  <Svg {...p}>
    <path d="M12 9v4M12 17h.01" />
    <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" />
  </Svg>
);

const ReceiptIcon = (p: React.SVGProps<SVGSVGElement>) => (
  <Svg {...p}>
    <path d="M4 2v20l2-1.5L8 22l2-1.5L12 22l2-1.5L16 22l2-1.5L20 22V2l-2 1.5L16 2l-2 1.5L12 2l-2 1.5L8 2 6 3.5Z" />
    <path d="M8 8h8M8 12h8M8 16h5" />
  </Svg>
);

/**
 * 通知类型 → 图标 / 强调色 / 目标名词。
 *
 * 集中在组件内而非散落到各端：9 种类型是**后端枚举的映射**，多端各写一份
 * 必然出现某一端漏配新类型的情况（后端加一种类型，某端就渲染成空白）。
 * 未知类型有兜底，不会渲染失败。
 */
const TYPE_META: Record<
  string,
  { icon: React.FC<React.SVGProps<SVGSVGElement>>; tone: string; noun?: string }
> = {
  DISPATCH_CREATED: { icon: HandIcon, tone: "text-brand-600", noun: "案件" },
  CASE_ACCEPTED: { icon: InboxIcon, tone: "text-verified-600", noun: "案件" },
  EVIDENCE_MISSING: { icon: AlertIcon, tone: "text-pending-600", noun: "证据" },
  // 待复核用 pending 琥珀（责任边界三态：AI 生成 ai / 已确认 verified / 待复核 pending）
  REVIEW_REQUIRED: { icon: ClipboardCheckIcon, tone: "text-pending-600", noun: "复核" },
  REVIEW_DECIDED: { icon: ClipboardCheckIcon, tone: "text-verified-600", noun: "复核" },
  DOCUMENT_CONFIRMED: { icon: FileTextIcon, tone: "text-verified-600", noun: "文书" },
  CASE_ARCHIVED: { icon: ArchiveIcon, tone: "text-ink-500", noun: "归档" },
  // 额度预警是风险信号，用 danger 而非 pending
  QUOTA_WARNING: { icon: AlertIcon, tone: "text-danger-500", noun: "用量" },
  WORK_ORDER_CREATED: { icon: ReceiptIcon, tone: "text-pending-600", noun: "工单" },
};

const FALLBACK_META = { icon: BellIcon, tone: "text-ink-500", noun: undefined };

/* ------------------------------------------------- 类型元数据（对外导出） */

/** 通知类型的展示元数据。 */
export interface NotificationTypeMeta {
  icon: React.FC<React.SVGProps<SVGSVGElement>>;
  /**
   * 语义强调色，用于给图标着色。已对齐责任边界三态：
   * 待复核 `pending` / 已确认 `verified` / 风险 `danger`。
   */
  tone: string;
  /** 该类型通知指向的业务对象名词（如「案件」「复核」），无则 undefined。 */
  noun?: string;
}

/**
 * 取通知类型的展示元数据，未知类型兜底为铃铛 + 中性色。
 *
 * **各端不要自己再维护一份 `type → 图标 / 颜色` 映射。** 9 种类型是后端
 * 枚举的直接映射，多端各写一份必然出现「后端新增一种类型，某一端渲染成
 * 空白」。律师端通知页曾因此重复实现了 45 行 switch，而且用的还是旧色系
 * ——结果是**同一条通知在铃铛下拉面板里和通知页里颜色不一样**。
 * 统一从组件库取，两处展示必然一致。
 */
export function notificationMeta(type: string): NotificationTypeMeta {
  return TYPE_META[type] ?? FALLBACK_META;
}

/* ------------------------------------------------------------------ 工具 */

/** 相对时间。只做展示，不参与任何判断，故不引入 dayjs 等依赖。 */
export function formatRelativeTime(iso?: string | null): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const diff = Date.now() - t;
  if (diff < 0) return "刚刚";
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "昨天";
  if (days < 7) return `${days} 天前`;
  return new Date(t).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

/** 角标文案：超过 99 显示 99+，避免角标被数字撑变形。 */
export function formatBadge(n: number): string {
  if (n <= 0) return "";
  return n > 99 ? "99+" : String(n);
}

function useIsMobile(breakpoint: number): boolean {
  const [mobile, setMobile] = React.useState(false);
  React.useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia(`(max-width: ${breakpoint - 1}px)`);
    const apply = () => setMobile(mq.matches);
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [breakpoint]);
  return mobile;
}

/* -------------------------------------------------------------- 单个条目 */

interface RowProps {
  item: NotificationItem;
  onActivate: (n: NotificationItem) => void;
}

const NotificationRow: React.FC<RowProps> = ({ item, onActivate }) => {
  const meta = TYPE_META[item.type] ?? FALLBACK_META;
  const Icon = meta.icon;
  const unread = !item.is_read;

  return (
    <button
      type="button"
      onClick={() => onActivate(item)}
      className={cn(
        "flex w-full items-start gap-3 px-4 text-left transition-colors duration-fast",
        // 触控目标：移动端行高 ≥ 64px，远高于 48px 下限（规范 08 节）
        "min-h-[64px] py-3",
        "hover:bg-surface-hover focus-visible:bg-surface-hover focus-visible:outline-none",
        unread && "bg-brand-50/40"
      )}
    >
      <span className={cn("mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-r2 bg-surface-subtle", meta.tone)}>
        <Icon className="h-4 w-4" />
      </span>

      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          {unread && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-brand-600" aria-hidden />}
          <span className={cn("truncate text-body-sm", unread ? "font-medium text-ink-900" : "text-ink-700")}>
            {item.title}
          </span>
        </span>
        {item.content && (
          <span className="mt-0.5 line-clamp-2 block text-body-sm text-ink-500">{item.content}</span>
        )}
        <span className="mt-1 flex items-center gap-2 text-caption text-ink-400">
          <span>{formatRelativeTime(item.created_at)}</span>
          {meta.noun && item.ref_id != null && <span>· 前往{meta.noun}</span>}
        </span>
      </span>
    </button>
  );
};

/* ------------------------------------------------------------------ 主体 */

/**
 * 通知中心（P0-15）。
 *
 * ## 两种形态
 * - **≥ breakpoint**：锚定铃铛的下拉面板（360px），点击外部 / Esc 关闭
 * - **< breakpoint**：底部 BottomSheet，可下滑关闭、带安全区
 *
 * ## 未读数的获取策略：实时通道为主，轮询为兜底
 *
 * 后端已提供 `/api/v1/ws/notifications` 推送通道，因此改为**推送优先**：
 * 连上就抑制轮询，断开/被终止/浏览器不支持时**自动退回轮询**。
 * 两条路径共用同一份状态，对用户而言只有「角标准不准」，没有「走的哪条路」。
 *
 * 轮询路径保留原设计的三条纪律（它现在只在降级时才跑）：
 * 1. **页面隐藏时停止轮询**（`visibilitychange`）——后台标签页不该持续耗电
 * 2. **回到前台立即拉取一次**——避免用户切回来看到过期角标
 * 3. **失败指数退避**（最长 5 分钟）——服务端故障时不形成重试风暴
 *
 * 未读数端点返回 `latest_id`，据此可判断「有没有新通知」；列表只在
 * 打开面板或 `latest_id` 变化时才重新拉取，避免每 30 秒重建列表
 * 打断用户的滚动位置。
 *
 * ## 实时通道的失败必须是**静默**的
 *
 * 弱网是这版产品的常态（律师在法院、地下室、电梯里用），把一次重连失败
 * 变成 toast 或错误条，只会训练用户忽略所有提示。因此本组件对 WS 的
 * 一切异常只做 `console.debug`，UI 上唯一的体现是「角标更新得慢一点」。
 */
export const NotificationCenter: React.FC<NotificationCenterProps> = ({
  onNavigate,
  onViewAll,
  pollMs = 30000,
  realtime = true,
  mobileBreakpoint = 768,
  className,
}) => {
  const isMobile = useIsMobile(mobileBreakpoint);

  const [open, setOpen] = React.useState(false);
  const [unread, setUnread] = React.useState<NotificationUnread>({ total: 0, by_type: {}, latest_id: 0 });
  const [items, setItems] = React.useState<NotificationItem[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  /** 实时通道是否已连接。**它同时是轮询的开关**：连上停表，断开恢复。 */
  const [wsConnected, setWsConnected] = React.useState(false);

  const rootRef = React.useRef<HTMLDivElement>(null);
  const failRef = React.useRef(0);
  const lastLatestRef = React.useRef(0);
  /**
   * 已见过的最大通知 id —— 重连补拉的 `since_id` 锚点。
   * `0` 表示本地还没有任何锚点（首次连接），此时没有「错过的」可补，跳过往返。
   */
  const maxIdRef = React.useRef(0);

  /* ------------------------------------------------- 未读数：轮询 + 退避 */
  const fetchUnread = React.useCallback(async () => {
    try {
      const data = await authed<NotificationUnread>("/api/v1/notifications/unread-count");
      failRef.current = 0;
      setUnread(data ?? { total: 0, by_type: {}, latest_id: 0 });
      return true;
    } catch {
      // 静默失败：轮询不该给用户弹错，角标保持上一次的值即可
      failRef.current += 1;
      return false;
    }
  }, []);

  React.useEffect(() => {
    // 实时通道已连上 ⇒ 轮询停表（它只是兜底，不是被删掉的旧代码）。
    // 连接一断 `wsConnected` 变化会重跑本 effect，轮询自动恢复——
    // 「自动降级为轮询」就落在这一行上，不需要额外的状态机。
    if (realtime && wsConnected) return;
    if (pollMs <= 0) {
      void fetchUnread();
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const schedule = (delay: number) => {
      if (cancelled) return;
      timer = setTimeout(tick, delay);
    };

    const tick = async () => {
      if (cancelled) return;
      if (typeof document !== "undefined" && document.visibilityState === "hidden") {
        // 后台不轮询；回到前台时由 visibilitychange 重新启动
        return;
      }
      const ok = await fetchUnread();
      const backoff = Math.min(pollMs * 2 ** Math.min(failRef.current, 4), 300000);
      schedule(ok ? pollMs : backoff);
    };

    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        if (timer) clearTimeout(timer);
        void tick(); // 立即拉一次，避免切回来看到过期角标
      }
    };

    void tick();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [fetchUnread, pollMs, realtime, wsConnected]);

  /* ------------------------------------------- 实时通道：推送优先，轮询兜底 */
  /**
   * WebSocket 实时通道（P0-15 收口）。
   *
   * 定位是**轮询的加速器而非替代品**：连上就抑制轮询；任何一步失败
   * （连不上、被终止、浏览器不支持）都静默退回轮询，用户无感。
   * 因此本 effect 里**没有一处会把错误暴露到 UI**。
   *
   * 关闭码语义见 `lib/notificationRealtime.ts`：`1008`/`4429` 是终止码，
   * 命中即停止重连、永久降级为轮询；其余一律退避重连。
   */
  React.useEffect(() => {
    if (!realtime) return;
    // SSR / 不支持 WebSocket 的环境：不启通道，轮询照旧（这就是兜底的意义）。
    if (typeof window === "undefined" || typeof WebSocket === "undefined") return;

    let stopped = false;
    /** 命中终止关闭码后置 true：本 effect 生命周期内不再重连。 */
    let terminal = false;
    let attempt = 0;
    let socket: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    /**
     * 页面隐藏期间收到的推送**暂存于此**。
     *
     * 隐藏时不碰 React 状态（后台标签页的渲染用户看不到，纯耗电），
     * 但数据不能丢——轮询此时已被抑制，丢了就再也没有第二次机会，
     * 用户切回来会看到过期角标。回到前台一次性冲入列表。
     */
    let buffered: NotificationItem[] = [];
    let hiddenSince = document.visibilityState === "hidden" ? Date.now() : 0;

    const isHidden = () => document.visibilityState === "hidden";

    const clearTimer = () => {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    };

    /** 摘掉回调再关闭。否则 close 会走 onclose → 再排一次重连。 */
    const dropSocket = () => {
      if (!socket) return;
      socket.onopen = null;
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;
      try {
        socket.close();
      } catch {
        // 关闭失败无补救手段，忽略即可
      }
      socket = null;
    };

    const scheduleReconnect = () => {
      if (stopped || terminal) return;
      const delay = nextReconnectDelay(attempt);
      attempt += 1;
      clearTimer();
      timer = setTimeout(connect, delay);
    };

    /**
     * 重连后把错过的补回来。
     *
     * `since_id` 是 keyset 补拉，后端**故意按 id 正序**返回（倒序 + offset
     * 在追赶场景会因新写入位移而产生重复与漏项），所以这里不做任何顺序假设，
     * 一律交给 `mergeNotifications` 定序。
     */
    const backfill = async () => {
      const sinceId = maxIdRef.current;
      // 首次连接本地没有任何 id 锚点 = 没有「错过的」，跳过这次往返。
      if (sinceId <= 0) return;
      try {
        const page = await authed<{ items?: NotificationItem[] }>("/api/v1/notifications", {
          query: { since_id: sinceId, page_size: 100 },
        });
        const missed = page?.items ?? [];
        if (missed.length > 0) {
          for (const n of missed) if (n.id > maxIdRef.current) maxIdRef.current = n.id;
          setItems((prev) => mergeNotifications(prev, missed));
        }
      } catch {
        // 静默：补拉失败只是这一轮少了几条，下一次重连还会再补；
        // 给用户弹错没有任何可执行的动作。
      } finally {
        // 角标一律与服务端对齐（成功与否都要校正，避免乐观增量漂移）
        void fetchUnread();
      }
    };

    const handleFrame = (raw: string) => {
      let frame: NotificationWsFrame;
      try {
        frame = JSON.parse(raw) as NotificationWsFrame;
      } catch {
        return; // 非 JSON 帧：协议里不存在，忽略
      }
      if (!frame || typeof frame !== "object") return;

      switch (frame.type) {
        case "connected": {
          // 服务端真值。**不做乐观运算**：角标可信度是硬要求，
          // 客户端自己加减迟早会与服务端漂移。
          const total = frame.data?.unread_total;
          if (typeof total === "number") setUnread((u) => ({ ...u, total }));
          void backfill();
          break;
        }

        case "notification": {
          const item = frame.data;
          if (!item || typeof item.id !== "number") return;
          if (item.id > maxIdRef.current) maxIdRef.current = item.id;

          if (isHidden()) {
            buffered.push(item);
            return;
          }
          // 只插入这一条，**不整表重拉**：重拉会重建 DOM、打断滚动位置。
          setItems((prev) => mergeNotifications(prev, [item]));
          if (!item.is_read) setUnread((u) => ({ ...u, total: u.total + 1 }));
          break;
        }

        case "ping":
          // 必须原样回 "pong"：服务端 90s 收不到任何客户端帧就判「假活」回收。
          try {
            socket?.send("pong");
          } catch {
            // 发送失败说明连接已坏，交给 onclose 统一走重连
          }
          break;

        case "error":
          console.debug("[notifications] 服务端错误帧：", frame.message);
          break;

        default:
          break;
      }
    };

    const connect = () => {
      if (stopped || terminal) return;

      // 令牌只存在内存里（见 @nlaw/sdk 的承载策略），刷新页面后要等
      // `restoreSession()` 才有值。此时**不建连**——带着空 token 连上去必然
      // 被 1008 拒绝并永久终止；按退避稍后重试，令牌恢复后自然连上。
      const token = tokenStore.get();
      if (!token) {
        scheduleReconnect();
        return;
      }

      let next: WebSocket;
      try {
        next = new WebSocket(buildNotificationsWsUrl(API_BASE, token));
      } catch {
        scheduleReconnect();
        return;
      }
      socket = next;

      next.onopen = () => {
        if (stopped || socket !== next) return;
        // 连上就重置退避：否则一次长连接之后的短暂抖动会直接从 30s 起跳。
        attempt = 0;
        setWsConnected(true);
      };

      next.onmessage = (ev: MessageEvent) => {
        if (stopped || socket !== next) return;
        if (typeof ev.data === "string") handleFrame(ev.data);
      };

      next.onerror = () => {
        // 静默：弱网是常态，弹错只会制造噪音。重连统一由 onclose 驱动。
        console.debug("[notifications] 实时通道异常");
      };

      next.onclose = (ev: CloseEvent) => {
        if (socket === next) socket = null;
        if (stopped) return;
        setWsConnected(false);

        if (isTerminalCloseCode(ev.code)) {
          // 1008/4429：重试永远不会成功，只会变成重试风暴（令牌过期的标签页
          // 会永久敲打服务端；连接数超限时重试让连接数更难降下来）。
          // 置 terminal 后永久降级为轮询——轮询对这两种情况都不受影响。
          terminal = true;
          console.debug("[notifications] 实时通道终止，降级为轮询，close code =", ev.code);
          return;
        }
        scheduleReconnect();
      };
    };

    const onVisibility = () => {
      if (isHidden()) {
        hiddenSince = Date.now();
        return;
      }
      const hiddenFor = hiddenSince ? Date.now() - hiddenSince : 0;
      hiddenSince = 0;

      // 先把后台攒下的推送一次冲入列表，再拉未读数校正角标。
      const flushed = buffered.length > 0;
      if (flushed) {
        const pending = buffered;
        buffered = [];
        setItems((prev) => mergeNotifications(prev, pending));
      }
      // 通道在跑时轮询是停表的，回到前台没人替我们校正角标，必须自己拉一次；
      // 通道没跑时轮询 effect 的 visibilitychange 会处理，不必重复发请求。
      if (flushed || (socket && socket.readyState === WebSocket.OPEN)) void fetchUnread();

      // 长时间隐藏的标签页，socket 可能已被系统/代理**静默回收**
      // （TCP 无 FIN，本地却仍显示「已连接」，表现为永远收不到推送）。
      // 主动重建连接，由 connected 帧触发补拉把这段空白填上。
      if (hiddenFor > HIDDEN_RECONNECT_MS) {
        attempt = 0;
        dropSocket();
        setWsConnected(false);
        connect();
      }
    };

    document.addEventListener("visibilitychange", onVisibility);
    connect();

    return () => {
      stopped = true;
      clearTimer();
      // 必须真正 close：StrictMode 下 effect 跑两次，漏关就会留下一条没人
      // 持有的「幽灵连接」。后端单用户上限 5，泄漏两三条就会把正常标签页挤掉。
      dropSocket();
      document.removeEventListener("visibilitychange", onVisibility);
      // 必须复位：否则 realtime 被关掉后 wsConnected 仍是 true，轮询会被
      // 永久抑制，等于两条通道全断。
      setWsConnected(false);
    };
  }, [realtime, fetchUnread]);

  /* ------------------------------------------------------------ 列表加载 */
  const loadList = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await authed<{ items: NotificationItem[]; unread_total?: number }>(
        "/api/v1/notifications",
        { query: { page: 1, page_size: 20 } }
      );
      const list = page?.items ?? [];
      setItems(list);
      // 维护补拉锚点：列表里最新的 id 就是「本地已见过」的高水位。
      for (const n of list) if (n.id > maxIdRef.current) maxIdRef.current = n.id;
      if (typeof page?.unread_total === "number") {
        setUnread((u) => ({ ...u, total: page.unread_total as number }));
      }
    } catch (e) {
      // 失败时**不清空已有列表**：弱网下把用户已看到的内容清掉，
      // 比展示略旧的内容更糟（且法律行业弱网是常态）
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    if (!open) return;
    void loadList();
    void fetchUnread();
  }, [open, loadList, fetchUnread]);

  /* ------------------------------------------------- 点击外部 / Esc 关闭 */
  React.useEffect(() => {
    if (!open || isMobile) return;
    const onPointerDown = (e: MouseEvent | TouchEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("touchstart", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("touchstart", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, isMobile]);

  /* ------------------------------------------------------------- 交互 */
  const activate = async (n: NotificationItem) => {
    // 先乐观更新本地状态：点一下要立刻有反馈，不等网络
    if (!n.is_read) {
      setItems((prev) => prev.map((x) => (x.id === n.id ? { ...x, is_read: true } : x)));
      setUnread((u) => ({ ...u, total: Math.max(0, u.total - 1) }));
      try {
        await authed(`/api/v1/notifications/${n.id}/read`, { method: "POST" });
      } catch {
        // 标记失败不阻断跳转：用户意图是「去看这条通知指向的业务对象」，
        // 已读状态下次轮询会自动纠正
      }
    }
    onNavigate?.(n);
    if (isMobile) setOpen(false);
  };

  const markAll = async () => {
    const prev = items;
    setItems((p) => p.map((x) => ({ ...x, is_read: true })));
    setUnread((u) => ({ ...u, total: 0, by_type: {}, latest_id: 0 }));
    try {
      await authed("/api/v1/notifications/read-all", { method: "POST" });
    } catch {
      setItems(prev); // 失败回滚，避免角标与实际不符（角标可信度是硬要求）
      void fetchUnread();
    }
  };

  const badge = formatBadge(unread.total);
  const unreadItems = items.filter((x) => !x.is_read).length;

  /* --------------------------------------------------------------- 列表 */
  // ⚠️ 拆成三块（2026-09-26）：桌面下拉面板与移动 BottomSheet 的**装配方式不同** ——
  //    桌面：面板自带滚动容器，三块直接并排；
  //    移动：`scrollOwner="content"` 把滚动权交给 `PullToRefresh`（**NR-11 点名要求**）。
  //    共用同一份 header / listBody / footer，避免两份实现漂移。
  const header = (
    <div className="flex shrink-0 items-center justify-between gap-2 border-b border-line px-4 py-2.5">
      <div className="flex items-center gap-2">
        <h2 className="text-h4 text-ink-900">通知</h2>
        {unread.total > 0 && (
          <span className="rounded-r1 bg-brand-600/10 px-1.5 py-0.5 text-caption font-medium text-brand-700">
            {unread.total} 条未读
          </span>
        )}
      </div>
      {unreadItems > 0 && (
        <button
          type="button"
          onClick={markAll}
          className="tap-ghost flex items-center gap-1 rounded-r2 px-2 py-1.5 text-body-sm text-brand-600 transition-colors duration-fast hover:bg-surface-hover"
        >
          <CheckIcon className="h-3.5 w-3.5" />
          全部已读
        </button>
      )}
    </div>
  );

  /** 列表内容 —— **不含**滚动容器，滚动权由装配方决定（见上方注释） */
  const listBody = (
    <>
      {loading && items.length === 0 && (
        <div className="space-y-3 p-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-14 animate-pulse-soft rounded-r2 bg-surface-subtle" />
          ))}
        </div>
      )}

      {error && items.length === 0 && (
        <div className="flex flex-col items-center gap-3 px-4 py-10 text-center">
          <p className="text-body-sm text-ink-500">加载失败，请检查网络</p>
          <button
            type="button"
            onClick={() => void loadList()}
            className="rounded-r2 border border-line px-3 py-2 text-body-sm text-ink-700 transition-colors duration-fast hover:bg-surface-hover"
          >
            重试
          </button>
        </div>
      )}

      {!loading && !error && items.length === 0 && (
        <div className="flex flex-col items-center gap-2 px-4 py-12 text-center">
          <BellIcon className="h-8 w-8 text-ink-300" />
          <p className="text-body-sm text-ink-500">暂无通知</p>
          <p className="text-caption text-ink-400">派单、复核、归档等节点会在这里提醒你</p>
        </div>
      )}

      {items.map((n) => (
        <NotificationRow key={n.id} item={n} onActivate={activate} />
      ))}
    </>
  );

  const footer = onViewAll ? (
    <div className="shrink-0 border-t border-line">
      <button
        type="button"
        onClick={() => {
          setOpen(false);
          onViewAll();
        }}
        className="flex min-h-[48px] w-full items-center justify-center text-body-sm text-brand-600 transition-colors duration-fast hover:bg-surface-hover"
      >
        查看全部通知
      </button>
    </div>
  ) : null;

  /** 下拉刷新：未读数与列表**一起**拉（NR-11 的「支持下拉刷新」） */
  const refresh = React.useCallback(
    () => Promise.all([fetchUnread(), loadList()]).then(() => undefined),
    [fetchUnread, loadList]
  );

  return (
    <div ref={rootRef} className={cn("relative shrink-0", className)}>
      {/* 铃铛。未读为 0 时**不渲染任何红点** —— 常亮红点会让用户学会忽略它 */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={unread.total > 0 ? `通知，${unread.total} 条未读` : "通知"}
        aria-haspopup="dialog"
        aria-expanded={open}
        className={cn(
          "relative flex h-12 w-12 shrink-0 items-center justify-center rounded-r2 text-ink-600",
          "transition-colors duration-fast hover:bg-surface-hover md:h-9 md:w-9",
          open && "bg-surface-hover text-ink-900"
        )}
      >
        <BellIcon className="h-[18px] w-[18px]" />
        {badge && (
          <span
            className={cn(
              "absolute right-1 top-1 flex min-w-[16px] items-center justify-center rounded-full bg-danger-500 px-1",
              "text-caption font-medium leading-4 text-white md:right-0 md:top-0"
            )}
          >
            {badge}
          </span>
        )}
      </button>

      {/* 桌面：锚定下拉面板（**面板自带滚动容器**） */}
      {open && !isMobile && (
        <div
          role="dialog"
          aria-label="通知"
          className="absolute right-0 top-[calc(100%+6px)] z-drawer flex max-h-[min(70vh,560px)] w-[360px] animate-fade-in flex-col overflow-hidden rounded-r3 border border-line bg-surface shadow-s3"
        >
          {header}
          <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">{listBody}</div>
          {footer}
        </div>
      )}

      {/* 移动：BottomSheet **把滚动权交给** PullToRefresh（NR-11 点名要求） */}
      {isMobile && (
        <BottomSheet
          isOpen={open}
          onClose={() => setOpen(false)}
          heightRatio={0.7}
          scrollOwner="content"
        >
          {header}
          <PullToRefresh onRefresh={refresh} className="flex-1">
            {listBody}
          </PullToRefresh>
          {footer}
        </BottomSheet>
      )}
    </div>
  );
};

NotificationCenter.displayName = "NotificationCenter";
