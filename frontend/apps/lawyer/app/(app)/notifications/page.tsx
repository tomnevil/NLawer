"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Bell, CheckCheck, RefreshCw } from "lucide-react";
import { authed } from "@nlaw/sdk";
import {
  Badge,
  EmptyState,
  InfiniteList,
  SegmentedControl,
  Skeleton,
  Spinner,
  cn,
  formatBadge,
  formatRelativeTime,
  notificationMeta,
  useSession,
  useSyncQueueOptional,
  type NotificationItem,
} from "@nlaw/ui";

import { notificationHref } from "../../../lib/notificationRoutes";
import { SYNC_SECTION, isOfflineFailure } from "../../../lib/syncTransport";

/* ============================================================================
 * 通知中心（P0-15）
 * ----------------------------------------------------------------------------
 * GET  /api/v1/notifications?page&page_size&is_read -> { items, total, unread_total }
 * POST /api/v1/notifications/{id}/read
 * POST /api/v1/notifications/read-all
 *
 * ## 为什么这一页在实现前不存在
 *
 * 后端从项目初期就在 5 个业务节点写入通知（派单 / 归档 / 计费 ×2 / 复核），
 * 但**读取侧从未实现**——没有列表接口、`is_read` 从未被写入过、前端铃铛是
 * 没有 onClick 的死按钮。通知写进库即成黑洞。本页是读路径的落地端之一。
 *
 * ## 路由映射由本端定义，不放在组件库里
 *
 * 四端路由表不同（律师端有 `/cases/[id]`，admin 端没有），所以
 * `NotificationCenter` 只负责「用户点了一条通知」，跳到哪里由各端自己决定，
 * 见 `lib/notificationRoutes.ts`。
 *
 * ## 图标与强调色统一由组件库导出（本次重构修正）
 *
 * 旧实现自己写了一份 `type → 图标 / 底色` 的 switch，带来两个后果：
 *   1. 同一条通知在**铃铛下拉面板里和本页里颜色不一样**——面板用语义色
 *      （`text-pending-600` 等），本页用的是旧色系「浅底 + 深字」色块。
 *   2. 后端新增一种类型时，只有本页会渲染成空白（面板有兜底）。
 * 现改为统一取 `notificationMeta(type)`，两处展示必然一致。
 *
 * ## 加载改成「state 驱动」而非「事件回调驱动」（本次重构修正）
 *
 * 旧实现在 `switchTab` / `open` / `markAll` 里各自手动调 `load()`，容易与
 * state 错位：切 Tab 时先发一次**旧页码**的请求，竞态下后到的响应会覆盖
 * 正确结果。现在只有一条路径——`(user, page, tab, reloadKey)` 变化就重拉，
 * `append` 由 `page > 1` 推导。任何入口都不可能拉错页码。
 * ========================================================================== */

const PAGE_SIZE = 20;

interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_is_lower_bound?: boolean;
}

type TabKey = "all" | "unread";

export default function NotificationsPage() {
  const router = useRouter();

  /* 会话恢复与未登录跳转由 `AppLayout`（内部 `useAuthGuard`）统一处理，
   * 本页不再自己调 `restoreSession()` + `router.push("/login")`。
   * 这里取 `user` 只作为「可以发请求了」的信号：`AppLayout` 在会话恢复期间
   * 直接返回加载占位、不渲染 children，所以本页挂载时 `user` 已就绪；
   * 若为 null 说明未登录（正在被重定向），此时不必再发一次注定 401 的请求。 */
  const { user } = useSession();

  /**
   * 离线队列（可选）。标记已读是**幂等**操作，重放无副作用，因此是本页
   * 最适合入队的动作——断网时点通知不该让「已读」这件事凭空消失。
   * 未挂 Provider 时为 `null`，行为退化为原来的「失败即忽略」。
   */
  const syncQueue = useSyncQueueOptional();

  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unreadTotal, setUnreadTotal] = useState(0);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [tab, setTab] = useState<TabKey>("all");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /**
   * 手动刷新的触发信号。
   * 单独用一个计数器而不是复用 `page`：当 `page` 已经是 1 时，`setPage(1)`
   * 不产生 state 变化，加载 effect 不会重跑，表现为「点了刷新没反应」。
   */
  const [reloadKey, setReloadKey] = useState(0);

  const load = useCallback(
    async (nextPage: number, nextTab: TabKey, append: boolean) => {
      setLoading(true);
      setError(null);
      try {
        const p = await authed<Paged<NotificationItem> & { unread_total?: number }>(
          "/api/v1/notifications",
          {
            query: {
              page: nextPage,
              page_size: PAGE_SIZE,
              ...(nextTab === "unread" ? { is_read: "false" } : {}),
            },
          }
        );
        const list = p?.items ?? [];
        setItems((prev) => (append ? [...prev, ...list] : list));
        setTotal(p?.total ?? 0);
        setUnreadTotal(p?.unread_total ?? 0);
      } catch (e) {
        setError(e instanceof Error ? e.message : "加载失败");
      } finally {
        setLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    if (!user) return;
    void load(page, tab, page > 1);
  }, [user, page, tab, reloadKey, load]);

  /** 切页签与归位页码同批提交，只触发一次加载。 */
  const switchTab = (next: TabKey) => {
    setTab(next);
    setPage(1);
  };

  const refresh = () => {
    setPage(1);
    setReloadKey((k) => k + 1);
  };

  const open = async (n: NotificationItem) => {
    if (!n.is_read) {
      // 乐观更新：点一下要立刻有反馈，不等网络
      setItems((prev) => prev.map((x) => (x.id === n.id ? { ...x, is_read: true } : x)));
      setUnreadTotal((v) => Math.max(0, v - 1));
      try {
        await authed(`/api/v1/notifications/${n.id}/read`, { method: "POST" });
      } catch (e) {
        /* 离线时入队，而不是像旧实现那样直接吞掉异常、指望「下次进页面
         * 自动纠正」——可用户下次进来同样可能离线，未读角标就会一直不准，
         * 而角标可信度是硬要求。
         *
         * 这里**不弹 toast**：用户点通知的意图是「去看这条通知指向的业务对象」，
         * 马上就要跳走；此刻弹一条提示既打断导航，也和顶栏的离线条重复。 */
        if (syncQueue && isOfflineFailure(e)) {
          syncQueue.enqueue({
            label: `标记已读：${n.title}`,
            section: SYNC_SECTION.notifications,
            payload: { op: "notification.read", notificationId: n.id },
          });
        }
      }
    }
    const href = notificationHref(n);
    if (href) router.push(href);
  };

  const markAll = async () => {
    setBusy(true);
    const prevItems = items;
    const prevUnread = unreadTotal;
    const prevTotal = total;

    setUnreadTotal(0);
    if (tab === "unread") {
      /* 在「未读」页签下，全部已读之后的正确结果就是空列表——不需要再发一次
       * 请求去确认，那一次往返还会把「刚刚清空」的界面重新打回加载态。
       * 这也顺手绕开了「page 已经是 1、重拉不生效」的问题。 */
      setItems([]);
      setTotal(0);
    } else {
      setItems((p) => p.map((x) => ({ ...x, is_read: true })));
    }

    try {
      await authed("/api/v1/notifications/read-all", { method: "POST" });
    } catch (e) {
      if (syncQueue && isOfflineFailure(e)) {
        /* 离线：入队而不是回滚。回滚会把用户刚刚做的操作当场撤销掉，
         * 而「全部已读」是幂等的，恢复网络后重放即可。乐观状态保留，
         * 顶栏的离线条会同时告诉用户「有 N 条待同步」。 */
        syncQueue.enqueue({
          label: "全部标记为已读",
          section: SYNC_SECTION.notifications,
          payload: { op: "notification.readAll" },
        });
      } else {
        // 失败回滚：角标与实际不符比「操作失败」更伤信任
        setItems(prevItems);
        setUnreadTotal(prevUnread);
        setTotal(prevTotal);
        setError("操作失败，请重试");
      }
    } finally {
      setBusy(false);
    }
  };

  const hasMore = items.length < total;

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="flex flex-wrap items-center gap-2 text-h2 text-ink-900">
            通知
            {unreadTotal > 0 && <Badge variant="danger">{formatBadge(unreadTotal)} 条未读</Badge>}
          </h1>
          <p className="mt-1 text-body-sm text-ink-500">
            派单 · 复核 · 证据缺失 · 归档等节点提醒
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={refresh}
            disabled={loading}
            className={cn(
              // `min-h-tap` 保证移动端 48px 触控目标（规范 08 节），桌面回到 36px
              "flex h-9 min-h-tap items-center gap-1.5 rounded-r2 border border-line px-3 sm:min-h-0",
              "text-body-sm text-ink-600 transition-colors duration-fast hover:bg-surface-hover",
              "disabled:opacity-50"
            )}
          >
            {loading ? <Spinner size="sm" label={null} /> : <RefreshCw className="h-4 w-4" />}
            刷新
          </button>

          {unreadTotal > 0 && (
            <button
              type="button"
              onClick={() => void markAll()}
              disabled={busy}
              className={cn(
                "flex h-9 min-h-tap items-center gap-1.5 rounded-r2 border border-line px-3 sm:min-h-0",
                "text-body-sm text-brand-600 transition-colors duration-fast hover:bg-surface-hover",
                "disabled:opacity-50"
              )}
            >
              {busy ? (
                <Spinner size="sm" label={null} />
              ) : (
                <CheckCheck className="h-4 w-4" />
              )}
              全部已读
            </button>
          )}
        </div>
      </header>

      <SegmentedControl<TabKey>
        value={tab}
        onChange={switchTab}
        ariaLabel="通知筛选"
        options={[
          { value: "all", label: "全部" },
          {
            value: "unread",
            label: "未读",
            badge: unreadTotal > 0 ? formatBadge(unreadTotal) : undefined,
          },
        ]}
      />

      {error && (
        <div className="flex items-center justify-between gap-3 rounded-r2 border border-danger-500/30 bg-danger-500/10 px-3 py-2 text-body-sm text-danger-600">
          <span className="min-w-0">{error}</span>
          <button
            type="button"
            onClick={refresh}
            className="shrink-0 underline underline-offset-2 transition-colors duration-fast hover:text-danger-700"
          >
            重试
          </button>
        </div>
      )}

      {/* §5.1 B.2 落点：纯时间序、且本页**本来就带 `hasMore`** ⇒ 换成 `InfiniteList`。
          原先是一个手动的「加载更多（n/N）」按钮 —— 移动端要先把拇指挪到底部才点得到；
          自动触发才是这类列表该有的形态。
          ⚠️ 三处刻意的「不变」：`listClassName="space-y-2"` 保住**卡片形态**
          （组件默认是分隔线形态，会和卡片自身的 `border` 叠成双线）·
          `loadingState` 把原来的**骨架屏**原样搬进来（否则这次接线会顺手把骨架屏降级成转圈）·
          `emptyState` 沿用原来的空态文案。⇒ 除「按钮 → 哨兵」外**视觉零变化**。 */}
      <InfiniteList<NotificationItem>
        items={items}
        rowKey={(n) => String(n.id)}
        hasMore={hasMore}
        loading={loading}
        onLoadMore={() => setPage((p) => p + 1)}
        listClassName="space-y-2"
        // 只在「已经有内容」时把错误交给它 —— 那时哨兵会被「错误 + 重试」取代，
        // 不会在失败后继续触发加载。首屏失败仍由上面那条横幅负责（它还覆盖「全部已读」失败）。
        error={items.length > 0 ? error : null}
        onRetry={refresh}
        loadingState={
          <>
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-[76px] w-full" />
            ))}
          </>
        }
        emptyState={
          <EmptyState
            icon={<Bell className="h-6 w-6" />}
            title={tab === "unread" ? "没有未读通知" : "暂无通知"}
            description="派单、复核、证据缺失、归档等节点会在这里提醒你"
          />
        }
        renderItem={(n) => {
          const meta = notificationMeta(n.type);
          const Icon = meta.icon;
          const href = notificationHref(n);
          const unread = !n.is_read;
          return (
            <button
              key={n.id}
              type="button"
              onClick={() => void open(n)}
              className={cn(
                // 整行可点，最小高度 76px，远高于 48px 触控下限（规范 08 节）
                "flex w-full items-start gap-3 rounded-r3 border border-line bg-surface p-4 text-left",
                "min-h-[76px] transition-colors duration-fast",
                "hover:bg-surface-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30",
                // 未读：左侧墨蓝竖条 + 品牌色圆点 + 加粗标题，三重编码
                unread && "border-l-2 border-l-brand-600"
              )}
            >
              <span
                className={cn(
                  "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-r2 bg-surface-subtle",
                  meta.tone
                )}
              >
                <Icon className="h-4 w-4" />
              </span>

              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5">
                  {unread && (
                    <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-brand-600" aria-hidden />
                  )}
                  <span
                    className={cn(
                      "truncate text-body-sm",
                      unread ? "font-medium text-ink-900" : "text-ink-600"
                    )}
                  >
                    {n.title}
                  </span>
                </span>

                {n.content && (
                  <span className="mt-1 line-clamp-2 block text-body-sm text-ink-500">
                    {n.content}
                  </span>
                )}

                <span className="mt-1.5 flex items-center gap-2 text-caption text-ink-400">
                  <span>{formatRelativeTime(n.created_at)}</span>
                  {/* 无可跳转目标的类型（如用量预警）不显示「点击查看」，
                      避免给出一个点了没反应的暗示 */}
                  {href && <span>· 点击查看</span>}
                </span>
              </span>
            </button>
          );
        }}
      />
    </div>
  );
}
