"use client";

import React from "react";
import { cn } from "../lib/cn";

export interface TabBarItem {
  id: string;
  label: string;
  href?: string;
  icon?: React.ReactNode;
  /** 角标：红色=待办，琥珀色=待同步 */
  badge?: string | number;
  badgeTone?: "danger" | "pending";
}

export interface TabBarProps {
  /** 最多 5 项；次级功能收进「我的」或工作台快捷入口 */
  items: TabBarItem[];
  activeId: string;
  onSelect?: (item: TabBarItem) => void;
  className?: string;
}

/**
 * 移动端底部导航（规范第 08 节）。
 * - 触控目标 ≥ 48px，图标可只有 22px 但热区恒为 48px
 * - 底部预留 `env(safe-area-inset-bottom)`，Home Indicator 区不放可点元素
 * - 角标双色：红=待办，琥珀=待同步，与「待复核」语义色一致
 */
export const TabBar: React.FC<TabBarProps> = ({ items, activeId, onSelect, className }) => {
  if (items.length === 0) return null;

  return (
    <nav
      className={cn(
        "fixed inset-x-0 bottom-0 z-tabbar lg:hidden",
        "border-t border-sidebar-border bg-brand-950",
        className
      )}
      style={{ paddingBottom: "var(--safe-bottom)" }}
      aria-label="主导航"
    >
      <ul className="flex items-stretch" style={{ paddingLeft: "var(--safe-left)", paddingRight: "var(--safe-right)" }}>
        {items.slice(0, 5).map((item) => {
          const active = item.id === activeId;
          return (
            <li key={item.id} className="flex-1">
              <button
                type="button"
                onClick={() => onSelect?.(item)}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "relative flex w-full flex-col items-center justify-center gap-1",
                  "h-tabbar min-h-tap",
                  "transition-colors duration-fast ease-out",
                  active ? "text-white" : "text-sidebar-muted hover:text-white"
                )}
              >
                {/* 激活指示条：金色，与侧栏激活项语言一致 */}
                <span
                  aria-hidden
                  className={cn(
                    "absolute top-0 h-0.5 w-8 rounded-full bg-gold-500 transition-opacity duration-fast",
                    active ? "opacity-100" : "opacity-0"
                  )}
                />
                {item.icon && (
                  <span className="relative flex h-6 w-6 items-center justify-center [&>svg]:h-[22px] [&>svg]:w-[22px]">
                    {item.icon}
                    {item.badge !== undefined && item.badge !== 0 && (
                      <span
                        className={cn(
                          "absolute -right-2 -top-1.5 min-w-[16px] rounded-full px-1 text-center text-caption font-medium leading-4 text-white",
                          item.badgeTone === "pending" ? "bg-pending-500" : "bg-danger-500"
                        )}
                      >
                        {item.badge}
                      </span>
                    )}
                  </span>
                )}
                <span className="text-[11px] font-medium leading-none">{item.label}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
};

TabBar.displayName = "TabBar";
