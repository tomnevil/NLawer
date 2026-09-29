"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "../lib/cn";
import { NotificationCenter, type NotificationItem } from "./NotificationCenter";
import { TabBar, type TabBarItem } from "./TabBar";

/* -------------------------------------------------------------------- 图标 */

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

const MenuIcon = (p: React.SVGProps<SVGSVGElement>) => (
  <Svg {...p}>
    <path d="M4 6h16M4 12h16M4 18h16" />
  </Svg>
);
const PanelLeftIcon = (p: React.SVGProps<SVGSVGElement>) => (
  <Svg {...p}>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M9 4v16" />
  </Svg>
);
const SearchIcon = (p: React.SVGProps<SVGSVGElement>) => (
  <Svg {...p}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.5-3.5" />
  </Svg>
);
const ChevronDownIcon = (p: React.SVGProps<SVGSVGElement>) => (
  <Svg {...p}>
    <path d="m6 9 6 6 6-6" />
  </Svg>
);
const ChevronRightIcon = (p: React.SVGProps<SVGSVGElement>) => (
  <Svg {...p}>
    <path d="m9 6 6 6-6 6" />
  </Svg>
);
const CloseIcon = (p: React.SVGProps<SVGSVGElement>) => (
  <Svg {...p}>
    <path d="M6 6l12 12M18 6 6 18" />
  </Svg>
);
const LogoutIcon = (p: React.SVGProps<SVGSVGElement>) => (
  <Svg {...p}>
    <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" />
  </Svg>
);

/** 品牌标记：描边金线，不再是靛蓝→青蓝渐变方块 */
const LogoMark: React.FC<{ className?: string }> = ({ className }) => (
  <span
    className={cn(
      "flex h-8 w-8 shrink-0 items-center justify-center rounded-r2 border border-gold-500/60",
      className
    )}
  >
    <Svg className="h-4 w-4 text-gold-500">
      <path d="M12 3v18M7 21h10M5 7h14M5 7l-2.5 5a3.5 3.5 0 0 0 5 0L5 7Zm14 0-2.5 5a3.5 3.5 0 0 0 5 0L19 7Z" />
    </Svg>
  </span>
);

/* -------------------------------------------------------------------- 类型 */

export interface AppShellNavItem {
  id: string;
  label: string;
  href?: string;
  icon?: React.ReactNode;
  badge?: string | number;
  /** 红色=待办，琥珀色=待同步 */
  badgeTone?: "danger" | "pending";
}

export interface AppShellNavGroup {
  title: string;
  items: AppShellNavItem[];
}

export interface AppShellApp {
  id: string;
  label: string;
  href: string;
  description?: string;
}

export interface AppShellUser {
  name: string;
  role?: string;
  avatar?: string;
}

/**
 * 通知中心配置（P0-15）。
 *
 * **必须显式传入才会渲染铃铛**——刻意不设默认开启：骨架层默认发起网络
 * 轮询，会让未登录页（登录页、组件预览页）产生无意义的 401 请求，
 * 也让「一个页面为什么在发请求」变得难以追查。
 */
export interface AppShellNotifications {
  /** 点击通知后的跳转；由各端自行映射路由（四端路由表不同） */
  onNavigate?: (n: NotificationItem) => void;
  /** 「查看全部」入口 */
  onViewAll?: () => void;
  /** 未读数轮询间隔（ms），默认 30000；传 0 关闭轮询 */
  pollMs?: number;
}

export interface AppShellProps {
  /** 侧栏导航分组 */
  nav: AppShellNavGroup[];
  activeId: string;
  onNavigate?: (item: AppShellNavItem) => void;

  /** 应用切换器：打通 web / lawyer / admin / im 四端 */
  appId: string;
  apps?: AppShellApp[];
  onAppChange?: (app: AppShellApp) => void;

  /** 顶栏 */
  breadcrumb?: React.ReactNode;
  searchPlaceholder?: string;
  onSearch?: (keyword: string) => void;
  actions?: React.ReactNode;
  /**
   * 顶栏下方的通栏提示条（离线 / 待同步）。**不传则不渲染**。
   *
   * 放在骨架里而不是让各页自己渲染，是因为它是**全站级状态**，需要两件事：
   * ① 通栏满宽（不能受内容区 `max-w-reading` 之类的约束而被截窄）；
   * ② 吸附在顶栏正下方（`top: var(--topbar-total)`），滚动时不消失——
   *    离线条一旦滚出视口，用户就会忘记自己正处在离线状态，
   *    而「知道现在没网」正是这条提示的全部意义。
   */
  offlineBanner?: React.ReactNode;
  user?: AppShellUser;
  onLogout?: () => void;

  /** 通知中心。不传则不渲染铃铛（见 `AppShellNotifications` 说明） */
  notifications?: AppShellNotifications;

  /** 内容区最大宽度：阅读型 720 / 工作台 1280 / 驾驶舱 1600 */
  contentWidth?: "reading" | "workbench" | "wide";
  /** 内容区是否使用默认内边距（桌面 24px / 移动 16px） */
  padded?: boolean;

  /** 移动端底部 Tab（≤5 项）。传入才渲染 TabBar */
  tabs?: TabBarItem[];
  onTabSelect?: (item: TabBarItem) => void;

  children: React.ReactNode;
  className?: string;
}

/* ------------------------------------------------------------------ 工具钩子 */

/** 点击外部关闭弹层；返回 ref 与当前开关状态 */
function usePopover<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent | TouchEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("touchstart", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("touchstart", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return { ref, open, setOpen };
}

/* -------------------------------------------------------------- 导航列表 */

interface ShellNavProps {
  nav: AppShellNavGroup[];
  activeId: string;
  onItemClick: (item: AppShellNavItem) => void;
  appId: string;
  apps?: AppShellApp[];
  onAppChange?: (app: AppShellApp) => void;
  user?: AppShellUser;
  onLogout?: () => void;
}

const ShellNav: React.FC<ShellNavProps> = ({
  nav,
  activeId,
  onItemClick,
  appId,
  apps,
  onAppChange,
  user,
  onLogout,
}) => {
  const { ref: switcherRef, open: switcherOpen, setOpen: setSwitcherOpen } = usePopover<HTMLDivElement>();
  const currentApp = apps?.find((a) => a.id === appId);

  return (
    <div className="flex h-full flex-col">
      {/* 应用切换器 —— 四端共用，顶部对齐 56px 顶栏 */}
      <div ref={switcherRef} className="relative min-h-topbar shrink-0 border-b border-sidebar-border">
        <button
          type="button"
          onClick={() => apps && apps.length > 1 && setSwitcherOpen((v) => !v)}
          aria-haspopup={apps && apps.length > 1 ? "menu" : undefined}
          aria-expanded={switcherOpen}
          className="flex h-full w-full items-center gap-2.5 px-3 text-left transition-colors duration-fast hover:bg-sidebar-hover"
        >
          <LogoMark />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-body font-semibold text-white">
              {currentApp?.label ?? "律小智"}
            </span>
            <span className="block truncate text-caption text-sidebar-dim">
              {currentApp?.description ?? "AI 法律助手"}
            </span>
          </span>
          {apps && apps.length > 1 && (
            <ChevronDownIcon
              className={cn(
                "h-4 w-4 shrink-0 text-sidebar-muted transition-transform duration-fast",
                switcherOpen && "rotate-180"
              )}
            />
          )}
        </button>

        {switcherOpen && apps && (
          <div
            role="menu"
            className="absolute left-2 right-2 top-[calc(var(--topbar-h)-4px)] z-drawer animate-fade-in overflow-hidden rounded-r3 border border-sidebar-border bg-brand-900 shadow-s3"
          >
            {apps.map((app) => (
              <button
                key={app.id}
                type="button"
                role="menuitem"
                onClick={() => {
                  setSwitcherOpen(false);
                  onAppChange?.(app);
                }}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-2.5 text-left transition-colors duration-fast",
                  app.id === appId ? "bg-sidebar-active/40 text-white" : "text-sidebar-muted hover:bg-sidebar-hover hover:text-white"
                )}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-body-sm font-medium">{app.label}</span>
                  {app.description && (
                    <span className="block truncate text-caption text-sidebar-dim">{app.description}</span>
                  )}
                </span>
                {app.id === appId && <span className="text-gold-500">●</span>}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* 导航分组 */}
      <nav className="scroll-thin flex-1 overflow-y-auto px-2 py-3">
        {nav.map((group) => (
          <div key={group.title} className="mb-4 last:mb-0">
            <div className="px-3 pb-1.5 text-caption uppercase tracking-wider text-sidebar-dim">{group.title}</div>
            {group.items.map((item) => {
              const active = item.id === activeId;
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => onItemClick(item)}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "relative mb-0.5 flex w-full items-center gap-3 rounded-r2 px-3 py-2 text-left",
                    "transition-colors duration-fast ease-out",
                    active
                      ? "bg-sidebar-active/40 text-white"
                      : "text-sidebar-muted hover:bg-sidebar-hover hover:text-white"
                  )}
                >
                  {/* 激活项左侧 3px 金色指示条 */}
                  <span
                    aria-hidden
                    className={cn(
                      "absolute inset-y-1.5 left-0 w-[3px] rounded-full bg-gold-500 transition-opacity duration-fast",
                      active ? "opacity-100" : "opacity-0"
                    )}
                  />
                  {item.icon && (
                    <span className="flex h-5 w-5 shrink-0 items-center justify-center [&>svg]:h-[18px] [&>svg]:w-[18px]">
                      {item.icon}
                    </span>
                  )}
                  <span className="min-w-0 flex-1 truncate text-body-sm">{item.label}</span>
                  {item.badge !== undefined && item.badge !== 0 && (
                    <span
                      className={cn(
                        "shrink-0 rounded-r1 px-1.5 py-0.5 text-caption",
                        item.badgeTone === "pending"
                          ? "bg-pending-500/20 text-sidebar-badge-pending"
                          : "bg-danger-500/20 text-sidebar-badge-danger"
                      )}
                    >
                      {item.badge}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        ))}
      </nav>

      {/* 底部用户区 */}
      {user && (
        <div className="shrink-0 border-t border-sidebar-border p-2">
          <div className="flex items-center gap-2.5 rounded-r2 px-2 py-2">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-sidebar-active text-body-sm font-semibold text-white">
              {user.avatar ?? user.name.charAt(0)}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-body-sm font-medium text-white">{user.name}</span>
              {user.role && <span className="block truncate text-caption text-sidebar-dim">{user.role}</span>}
            </span>
            {onLogout && (
              <button
                type="button"
                onClick={onLogout}
                aria-label="退出登录"
                className="tap-ghost flex h-8 w-8 shrink-0 items-center justify-center rounded-r2 text-sidebar-muted transition-colors duration-fast hover:bg-sidebar-hover hover:text-white"
              >
                <LogoutIcon className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

/* ------------------------------------------------------------------ AppShell */

/**
 * 四端统一骨架（规范第 04.1 / 08.2 节）。
 *
 * 布局形态：
 *   ≥ 1024px  左侧 240px 墨色侧栏常驻（可整栏收起，**不做 64px 图标条**）
 *   <  1024px 侧栏完全收起为抽屉，底部渲染 TabBar（若传入 tabs）
 *
 * 深色模式完全由 tokens.css 的 CSS 变量驱动，本组件不含任何 dark: 变体。
 */
export const AppShell: React.FC<AppShellProps> = ({
  nav,
  activeId,
  onNavigate,
  appId,
  apps,
  onAppChange,
  breadcrumb,
  searchPlaceholder = "搜索案件、文书、法条…",
  onSearch,
  actions,
  offlineBanner,
  user,
  onLogout,
  notifications,
  contentWidth = "workbench",
  padded = true,
  tabs,
  onTabSelect,
  children,
  className,
}) => {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [keyword, setKeyword] = useState("");
  const { ref: userMenuRef, open: userMenuOpen, setOpen: setUserMenuOpen } = usePopover<HTMLDivElement>();

  const handleNavigate = useCallback(
    (item: AppShellNavItem) => {
      setDrawerOpen(false);
      onNavigate?.(item);
    },
    [onNavigate]
  );

  // 抽屉打开时锁定页面滚动
  useEffect(() => {
    if (!drawerOpen) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setDrawerOpen(false);
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previous;
      document.removeEventListener("keydown", onKey);
    };
  }, [drawerOpen]);

  const widthClass = {
    reading: "max-w-reading",
    workbench: "max-w-workbench",
    wide: "max-w-wide",
  }[contentWidth];

  const hasTabs = Boolean(tabs && tabs.length > 0);

  return (
    <div className={cn("min-h-screen bg-ink-50 font-sans text-ink-900", className)}>
      {/* ---------------------------------------------------- 桌面常驻侧栏 */}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-sidebar hidden w-sidebar flex-col bg-brand-950 lg:flex",
          collapsed && "lg:hidden"
        )}
      >
        <ShellNav
          nav={nav}
          activeId={activeId}
          onItemClick={handleNavigate}
          appId={appId}
          apps={apps}
          onAppChange={onAppChange}
          user={user}
          onLogout={onLogout}
        />
      </aside>

      {/* ---------------------------------------------------- 移动端抽屉 */}
      {drawerOpen && (
        <div className="lg:hidden">
          <div
            className="fixed inset-0 z-drawer animate-fade-in"
            style={{ backgroundColor: "var(--overlay-scrim)" }}
            onClick={() => setDrawerOpen(false)}
            aria-hidden
          />
          {/* 移动端抽屉：竖屏时左右安全区为 0，但**横屏刘海机上为 40px 上下**，
              抽屉贴的是 `left-0`，导航项会被刘海切掉左侧一截。
              桌面常驻侧栏（`lg:flex`）不需要——能触发它的最小宽度是 1024px，
              而当前所有左右带刘海的机型横屏都到不了这个宽度。 */}
          <aside
            className="fixed inset-y-0 left-0 z-drawer flex w-sidebar flex-col bg-brand-950 shadow-s3"
            style={{ paddingLeft: "var(--safe-left)", paddingRight: "var(--safe-right)" }}
          >
            <div className="flex items-center justify-end px-2 pt-2" style={{ paddingTop: "var(--safe-top)" }}>
              <button
                type="button"
                onClick={() => setDrawerOpen(false)}
                aria-label="关闭导航"
                className="flex h-12 w-12 items-center justify-center rounded-r2 text-sidebar-muted hover:text-white"
              >
                <CloseIcon className="h-5 w-5" />
              </button>
            </div>
            <div className="min-h-0 flex-1">
              <ShellNav
                nav={nav}
                activeId={activeId}
                onItemClick={handleNavigate}
                appId={appId}
                apps={apps}
                onAppChange={onAppChange}
                user={user}
                onLogout={onLogout}
              />
            </div>
          </aside>
        </div>
      )}

      {/* ------------------------------------------------------------ 主区 */}
      <div className={cn("flex min-h-screen flex-col", !collapsed && "lg:pl-sidebar")}>
        {/* 顶栏 56px：面包屑 + 全局搜索 + 通知 + 用户菜单 */}
        <header
          className="sticky top-0 z-topbar flex min-h-topbar shrink-0 items-center gap-2 border-b border-line bg-surface/80 px-3 backdrop-blur lg:px-6"
          style={{ paddingTop: "var(--safe-top)" }}
        >
          {/* 移动端：打开抽屉 */}
          <button
            type="button"
            onClick={() => setDrawerOpen(true)}
            aria-label="打开导航"
            className="flex h-12 w-12 shrink-0 items-center justify-center rounded-r2 text-ink-600 transition-colors duration-fast hover:bg-surface-hover lg:hidden"
          >
            <MenuIcon className="h-5 w-5" />
          </button>

          {/* 桌面端：整栏收起 / 展开（不做 64px 图标条） */}
          <button
            type="button"
            onClick={() => setCollapsed((v) => !v)}
            aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
            aria-pressed={collapsed}
            className="hidden h-9 w-9 shrink-0 items-center justify-center rounded-r2 text-ink-500 transition-colors duration-fast hover:bg-surface-hover hover:text-ink-800 lg:flex"
          >
            <PanelLeftIcon className="h-[18px] w-[18px]" />
          </button>

          {/* 面包屑 */}
          <div className="flex min-w-0 flex-1 items-center gap-1.5 text-body-sm">
            {breadcrumb ?? <span className="truncate font-medium text-ink-800">{activeId}</span>}
          </div>

          {/* 全局搜索 */}
          {onSearch && (
            <div className="relative hidden md:block">
              <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400" />
              <input
                type="search"
                value={keyword}
                onChange={(e) => {
                  setKeyword(e.target.value);
                  onSearch(e.target.value);
                }}
                placeholder={searchPlaceholder}
                className={cn(
                  "h-9 w-56 rounded-r2 border border-line bg-surface-subtle pl-8 pr-14 text-body-sm text-ink-800 lg:w-72",
                  "placeholder:text-ink-400 transition-colors duration-fast",
                  "focus:border-brand-400 focus:bg-surface focus:outline-none focus:ring-2 focus:ring-brand-500/30"
                )}
              />
              <kbd className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 rounded-r1 border border-line bg-surface px-1.5 py-0.5 text-caption text-ink-500">
                ⌘K
              </kbd>
            </div>
          )}

          {/* 自定义操作区 */}
          {actions}

          {/* 通知中心（P0-15）。
              修复前这里是一个**没有 onClick 的死按钮**，红点是硬编码常亮
              `<span>`——用户很快学会忽略它，通知系统等于不存在。
              现在角标由真实未读数驱动，未读为 0 时完全不渲染红点。 */}
          {notifications && (
            <NotificationCenter
              onNavigate={notifications.onNavigate}
              onViewAll={notifications.onViewAll}
              pollMs={notifications.pollMs}
            />
          )}

          {/* 用户菜单 */}
          {user && (
            <div ref={userMenuRef} className="relative shrink-0">
              <button
                type="button"
                onClick={() => setUserMenuOpen((v) => !v)}
                aria-haspopup="menu"
                aria-expanded={userMenuOpen}
                className="flex h-12 items-center gap-2 rounded-r2 px-1.5 transition-colors duration-fast hover:bg-surface-hover md:h-9"
              >
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand-600 text-caption font-semibold text-white">
                  {user.avatar ?? user.name.charAt(0)}
                </span>
                <span className="hidden text-body-sm text-ink-800 lg:block">{user.name}</span>
                <ChevronDownIcon className="hidden h-4 w-4 text-ink-400 lg:block" />
              </button>

              {userMenuOpen && (
                <div
                  role="menu"
                  className="absolute right-0 top-[calc(100%+6px)] z-drawer w-48 animate-fade-in overflow-hidden rounded-r3 border border-line bg-surface shadow-s2"
                >
                  <div className="border-b border-line px-3 py-2.5">
                    <div className="truncate text-body-sm font-medium text-ink-900">{user.name}</div>
                    {user.role && <div className="truncate text-caption text-ink-500">{user.role}</div>}
                  </div>
                  {onLogout && (
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setUserMenuOpen(false);
                        onLogout();
                      }}
                      className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-body-sm text-ink-700 transition-colors duration-fast hover:bg-surface-hover"
                    >
                      <LogoutIcon className="h-4 w-4" />
                      退出登录
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </header>

        {/* 全站级提示条：吸附在顶栏正下方，滚动时不消失。
            偏移必须用 `--topbar-total`（= safe-top + 56px）而非 `--topbar-h`：
            顶栏自身带 `paddingTop: var(--safe-top)`，刘海屏上实际占位更高，
            用 `--topbar-h` 会让提示条被顶栏盖住上半个字。 */}
        {offlineBanner && (
          <div className="sticky z-topbar" style={{ top: "var(--topbar-total)" }}>
            {offlineBanner}
          </div>
        )}

        {/* 内容区：页边距走 `--page-pad` / `--page-pad-desktop` 两个令牌
            （`design-spec.md` §4.2：移动端 16px / 桌面 24px）。
            ⚠️ 别把字面量 `px-4 … lg:px-6` 加回来 —— 那样令牌又变成死令牌，
            `verify_page_padding.py` 的 A1/R1 会立刻发现（A1 判令牌值、R1 数消费者）。 */}
        <main
          className={cn("flex-1", padded && "p-[var(--page-pad)] lg:p-[var(--page-pad-desktop)]")}
          style={hasTabs ? { paddingBottom: "calc(var(--tabbar-h) + var(--safe-bottom) + 16px)" } : undefined}
        >
          <div className={cn("mx-auto w-full", widthClass)}>{children}</div>
        </main>
      </div>

      {/* ------------------------------------------------------ 移动底部导航 */}
      {hasTabs && <TabBar items={tabs!} activeId={activeId} onSelect={onTabSelect} />}
    </div>
  );
};

AppShell.displayName = "AppShell";

export { LogoMark as AppShellLogoMark, ChevronRightIcon as AppShellChevronRight };
