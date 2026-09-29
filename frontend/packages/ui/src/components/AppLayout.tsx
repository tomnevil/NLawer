"use client";

import React from "react";
import { usePathname, useRouter } from "next/navigation";
import { cn } from "../lib/cn";
import { Spinner } from "./Spinner";
import { AppShell, type AppShellApp, type AppShellNavGroup } from "./AppShell";
import type { NotificationItem } from "./NotificationCenter";
import type { TabBarItem } from "./TabBar";
import { displayName, roleLabel } from "../hooks/useSession";
import { useAuthGuard } from "../hooks/useAuthGuard";
import { useTheme } from "../theme/ThemeProvider";
import { OfflineBanner } from "./mobile/OfflineBanner";
import {
  SyncQueueBadge,
  SyncQueueProvider,
  SyncQueueSheet,
  useSyncQueueOptional,
  type SyncTask,
} from "./mobile/SyncQueue";

export interface AppLayoutProps {
  /** 侧栏导航分组 */
  nav: AppShellNavGroup[];
  /** 移动端底部 Tab（≤5 项） */
  tabs?: TabBarItem[];
  appId: string;
  /** 应用切换器数据，通常来自 `buildAppLinks()` */
  apps?: AppShellApp[];
  /**
   * 首段路径 → 面包屑文案。
   * 同时用于推导当前导航 id（首段路径即 id，根路径归到 `rootId`）。
   */
  sectionLabels: Record<string, string>;
  /** 根路径对应的导航 id，默认 `"home"` */
  rootId?: string;
  /** 内容区最大宽度：阅读型 720 / 工作台 1280 / 驾驶舱 1600 */
  contentWidth?: "reading" | "workbench" | "wide";
  searchPlaceholder?: string;
  onSearch?: (keyword: string) => void;
  /** 未登录时的跳转目标，默认 `"/login"` */
  loginPath?: string;
  /**
   * 本端允许的角色（跨端 refresh Cookie 串号防线，P2-9）。
   *
   * 四端共享同一后端 origin 的 HttpOnly refresh Cookie——浏览器里 A 端
   * 登录后，B 端刷新页面会用同一 Cookie 恢复出 **A 端用户**（前端角色
   * UI 串号；服务端 RBAC 仍兜底不至越权）。传入本端合法角色清单后，
   * 恢复出角色不符的会话会被强制登出（服务端吊销 + 跳登录页）。
   * 不传 = 不校验（组件预览页等免鉴权场景）。
   */
  allowedRoles?: string[];
  /** 顶栏右侧追加操作（主题切换之外） */
  actions?: React.ReactNode;
  /**
   * 通知中心。**不传则不渲染铃铛**。
   *
   * 为什么必须显式传入、不能给个默认值：铃铛点开后需要「点某条通知去哪」，
   * 而四端路由表不同（律师端 `/cases/{id}`、企业端可能根本没有案件页）。
   * 若给一个猜测的默认路由，会在没有对应页面的应用里产生**死链**——
   * 用户点了通知，跳到 404。宁可不显示铃铛，也不显示一个会跳错的铃铛。
   *
   * `href` 由各应用提供（如律师端 `lib/notificationRoutes.ts`），
   * 返回 `null` 表示该条通知无可跳转目标（仅标记已读）。
   */
  notifications?: {
    href: (n: NotificationItem) => string | null;
    /** 「查看全部」目标，默认 `/notifications` */
    allHref?: string;
    /** 未读数轮询间隔 ms，默认 30000；传 0 关闭（如已接 WebSocket） */
    pollMs?: number;
  };
  /**
   * 离线 / 弱网支持（规范第 08 节）。**传入才启用**：不传则完全不挂
   * `SyncQueueProvider`，同步相关的 UI 一个都不渲染。
   *
   * ## 为什么必须显式传入，而不是给个默认的 no-op transport
   *
   * `transport` 回答的是「这一条任务该发到哪个端点」，那是**应用级知识**
   * （律师端发 `/reviews/{id}/submit`，企业端可能发别的），组件库不可能猜。
   * 若给一个默认的空实现，任务会被安静地积压在队列里永远不上行——
   * 而界面还在对用户承诺「已保存到本机，恢复网络后自动同步」。
   * **一个永远不同步的同步队列，比没有同步队列更糟。**
   *
   * 同理，`section` 让待同步数落到具体的 Tab 上（见 `SyncTask.section`）。
   */
  sync?: {
    transport: (task: SyncTask) => Promise<void>;
    storageKey?: string;
    /** 失败多少次后标记 failed 并停止自动重试，默认 3 */
    maxAttempts?: number;
  };
  children: React.ReactNode;
}

/**
 * 应用外壳（会话恢复 + 鉴权跳转 + AppShell + 顶栏主题切换）。
 *
 * 把「每个 app 都要写一遍」的四件事收敛到一处：静默恢复会话、
 * 未登录跳转、面包屑推导、主题切换按钮位置。各 app 只需提供导航
 * 与文案配置，`(app)/layout.tsx` 因此能压到 30 行左右。
 *
 * 不放在 `AppShell` 里的原因：`AppShell` 是纯展示骨架，可以被不需要
 * 鉴权的页面（组件预览页）直接使用；把会话逻辑塞进去会让它无法复用。
 *
 * ## 本组件是一层薄包装
 *
 * 它自己**不持有任何 hook**，只负责「需要离线能力时补上 Provider」。
 * 所有实际逻辑在同文件的 `AppLayoutInner` 里。这样拆开的原因是
 * `SyncQueueProvider` 必须包在**调用 `useSyncQueueOptional()` 的组件之外**，
 * 而 `useAuthGuard` / `useTheme` 等 hook 又必须在 Provider 内部——
 * 混在一个组件里会陷入「Provider 要用自己的 children 的结果」的死结。
 */
export function AppLayout({ sync, ...rest }: AppLayoutProps) {
  if (!sync) return <AppLayoutInner {...rest} />;

  return (
    <SyncQueueProvider
      transport={sync.transport}
      storageKey={sync.storageKey}
      maxAttempts={sync.maxAttempts}
    >
      <AppLayoutInner {...rest} />
    </SyncQueueProvider>
  );
}

function AppLayoutInner({
  nav,
  tabs,
  appId,
  apps,
  sectionLabels,
  rootId = "home",
  contentWidth = "workbench",
  searchPlaceholder,
  onSearch,
  loginPath = "/login",
  allowedRoles,
  actions,
  notifications,
  children,
}: Omit<AppLayoutProps, "sync">) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, loading, logout } = useAuthGuard(loginPath, allowedRoles);
  const { theme, toggle: toggleTheme } = useTheme();

  /**
   * 同步队列是**可选**的：用 `useSyncQueueOptional()` 而不是 `useSyncQueue()`。
   * 四端里只有需要离线写入的应用才挂 Provider，若这里用会抛错的版本，
   * 任何漏挂 Provider 的应用都会整页崩溃（`useToast` 已经踩过这个坑）。
   * 可选访问器把后果从「崩溃」降级为「少一个同步徽标」。
   */
  const syncQueue = useSyncQueueOptional();
  const [syncSheetOpen, setSyncSheetOpen] = React.useState(false);

  /**
   * 把待同步数换算成 Tab 上的**琥珀色**角标（规范 08 节：琥珀 = 待同步）。
   *
   * 只统计 `queued` / `syncing`：`failed` 的任务需要用户介入，语义上是「待办」
   * 而非「待同步」，由顶栏徽标（会转红）与队列面板负责呈现。
   * 一个页签同时显示两种含义的角标只会让人分不清该不该等。
   *
   * 没有 `section` 的任务不落 Tab——宁可不显示，也不要挂在一个错误的页签上。
   */
  const tabsWithBadges = React.useMemo<TabBarItem[] | undefined>(() => {
    if (!tabs || !syncQueue) return tabs;
    const bySection = new Map<string, number>();
    for (const t of syncQueue.tasks) {
      if (!t.section || t.status === "failed") continue;
      bySection.set(t.section, (bySection.get(t.section) ?? 0) + 1);
    }
    if (bySection.size === 0) return tabs;
    return tabs.map((t) => {
      const n = bySection.get(t.id);
      return n ? { ...t, badge: n, badgeTone: "pending" as const } : t;
    });
  }, [tabs, syncQueue]);

  // 只在调用方提供了 href 时才渲染铃铛：没有路由映射的端宁可没有铃铛，
  // 也不要一个点开就跳 404 的铃铛。用 useMemo 保持引用稳定，
  // 否则 NotificationCenter 的轮询 effect 会因 props 变化反复重启。
  const shellNotifications = React.useMemo(
    () =>
      notifications
        ? {
            onNavigate: (n: NotificationItem) => {
              const href = notifications.href(n);
              if (href) router.push(href);
            },
            onViewAll: () => router.push(notifications.allHref ?? "/notifications"),
            pollMs: notifications.pollMs,
          }
        : undefined,
    [notifications, router]
  );

  // 未登录跳转已由 useAuthGuard 统一处理（replace，避免返回键弹回）

  const segments = pathname.split("/").filter(Boolean);
  const activeId = segments[0] ?? rootId;

  // 会话恢复中：给一个稳定的占位，避免先渲染空壳再跳登录造成闪烁
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-ink-50">
        <Spinner size="md" label="正在恢复会话" />
      </div>
    );
  }

  return (
    <>
      <AppShell
        nav={nav}
        activeId={activeId}
        onNavigate={(item) => item.href && router.push(item.href)}
        appId={appId}
        apps={apps}
        // 四端是独立进程，切换必须整页跳转，不能走客户端路由
        onAppChange={(app) => window.location.assign(app.href)}
        breadcrumb={<span className="text-ink-500">{sectionLabels[activeId] ?? sectionLabels[rootId]}</span>}
        searchPlaceholder={searchPlaceholder}
        onSearch={onSearch}
        contentWidth={contentWidth}
        user={{ name: displayName(user) ?? "—", role: roleLabel(user?.role) }}
        notifications={shellNotifications}
        offlineBanner={
          syncQueue ? (
            <OfflineBanner
              pendingCount={syncQueue.pendingCount + syncQueue.failedCount}
              onSync={() => void syncQueue.flush()}
              syncing={syncQueue.isSyncing}
              // 吸附交给 AppShell 的容器（它知道顶栏高度），此处不叠加 sticky
              aboveTabBar={false}
            />
          ) : undefined
        }
        onLogout={() => {
          void logout().finally(() => router.replace(loginPath));
        }}
        actions={
          <>
            {actions}
            {syncQueue && (
              <SyncQueueBadge
                onClick={() => setSyncSheetOpen(true)}
                // 桌面顶栏只有 56px，48px 的触控热区在这里显得笨重
                className="sm:min-h-0"
              />
            )}
            <button
              type="button"
              onClick={toggleTheme}
              aria-label={theme === "dark" ? "切换到浅色模式" : "切换到深色模式"}
              className={cn(
                // `tap-ghost`：图标只有 16px，但**热区恒为 48px**（规范 §8.3）。
                // 这里是「不可见」热区（`::after` 伪元素），不改变 32×32 的可见盒子，
                // 因此桌面顶栏的视觉密度不受影响。
                // 实测（`evidence/probe_tapghost_desktop.py`）390/1024/1280/1440 四档
                // 热区均由 32×33 提升到 48×49，且**不抢任何邻居的点击**。
                "tap-ghost flex h-8 w-8 items-center justify-center rounded-r2 text-ink-500",
                "transition-colors duration-fast hover:bg-surface-hover hover:text-ink-900"
              )}
            >
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
                {theme === "dark" ? (
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"
                  />
                ) : (
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"
                  />
                )}
              </svg>
            </button>
          </>
        }
        tabs={tabsWithBadges}
        onTabSelect={(t) => t.href && router.push(t.href)}
      >
        {children}
      </AppShell>

      {/* 同步队列详情：放在骨架之外，避免被内容区的 max-w / padding 约束 */}
      {syncQueue && (
        <SyncQueueSheet isOpen={syncSheetOpen} onClose={() => setSyncSheetOpen(false)} />
      )}
    </>
  );
}

AppLayoutInner.displayName = "AppLayoutInner";
AppLayout.displayName = "AppLayout";
