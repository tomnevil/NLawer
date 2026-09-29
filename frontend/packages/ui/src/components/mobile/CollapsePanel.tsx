"use client";

import React from "react";
import { cn } from "../../lib/cn";

interface CollapseContextValue {
  accordion: boolean;
  openId: string | null;
  setOpenId: (id: string | null) => void;
}

const CollapseContext = React.createContext<CollapseContextValue | null>(null);

export interface CollapseGroupProps {
  /** 手风琴模式：同时只允许一个面板展开 */
  accordion?: boolean;
  /** 面板之间的分隔线 */
  divided?: boolean;
  className?: string;
  children: React.ReactNode;
}

/**
 * 折叠面板组。手风琴模式下由组统一持有「当前展开项」，避免每块面板
 * 各自为政导致手机上同时摊开一屏内容。
 */
export const CollapseGroup: React.FC<CollapseGroupProps> = ({
  accordion = true,
  divided = true,
  className,
  children,
}) => {
  const [openId, setOpenId] = React.useState<string | null>(null);
  const value = React.useMemo(() => ({ accordion, openId, setOpenId }), [accordion, openId]);

  return (
    <CollapseContext.Provider value={value}>
      <div className={cn(divided && "divide-y divide-line", className)}>{children}</div>
    </CollapseContext.Provider>
  );
};

CollapseGroup.displayName = "CollapseGroup";

export interface CollapsePanelProps {
  /** 组内唯一标识；不传则自动生成（手风琴模式下建议显式传入以便外部控制） */
  id?: string;
  title: React.ReactNode;
  /** 标题下方的辅助说明（仅展开时显示） */
  subtitle?: string;
  /** 标题右侧徽标，如条数、状态 */
  badge?: React.ReactNode;
  /** 标题左侧图标 */
  icon?: React.ReactNode;
  defaultOpen?: boolean;
  /** 受控展开态，传入后接管内部状态 */
  open?: boolean;
  onToggle?: (open: boolean) => void;
  disabled?: boolean;
  className?: string;
  children: React.ReactNode;
}

/**
 * 折叠面板（表单分区、法条分组、详情折叠）。
 *
 * 展开动画用 `grid-template-rows: 0fr → 1fr`，无需测量内容高度，
 * 也不会有 max-height 猜错导致的截断。标题栏整体是按钮，
 * 触控目标 ≥48px。
 */
export const CollapsePanel: React.FC<CollapsePanelProps> = ({
  id: idProp,
  title,
  subtitle,
  badge,
  icon,
  defaultOpen = false,
  open: openProp,
  onToggle,
  disabled = false,
  className,
  children,
}) => {
  const autoId = React.useId();
  const id = idProp ?? autoId;
  const ctx = React.useContext(CollapseContext);
  const [localOpen, setLocalOpen] = React.useState(defaultOpen);

  const inAccordion = ctx?.accordion ?? false;
  const isOpen =
    openProp !== undefined ? openProp : inAccordion ? ctx?.openId === id : localOpen;

  const panelId = `collapse-panel-${id}`;
  const buttonId = `collapse-button-${id}`;

  const toggle = () => {
    if (disabled) return;
    const next = !isOpen;
    onToggle?.(next);
    if (openProp !== undefined) return;
    if (inAccordion) ctx?.setOpenId(next ? id : null);
    else setLocalOpen(next);
  };

  return (
    <div className={cn(disabled && "opacity-60", className)}>
      <h3>
        <button
          id={buttonId}
          type="button"
          onClick={toggle}
          disabled={disabled}
          aria-expanded={isOpen}
          aria-controls={panelId}
          className={cn(
            "tap flex w-full min-h-tap items-center gap-2.5 px-4 py-3 text-left",
            "transition-colors duration-fast",
            !disabled && "hover:bg-surface-hover",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500/30"
          )}
        >
          {icon && <span className="shrink-0 text-ink-500 [&>svg]:h-4 [&>svg]:w-4">{icon}</span>}

          <span className="min-w-0 flex-1">
            <span className={cn("block truncate text-body font-medium", isOpen ? "text-ink-900" : "text-ink-700")}>
              {title}
            </span>
            {subtitle && !isOpen && (
              <span className="mt-0.5 block truncate text-caption text-ink-500">{subtitle}</span>
            )}
          </span>

          {badge && <span className="shrink-0">{badge}</span>}

          <svg
            className={cn(
              "h-4 w-4 shrink-0 text-ink-400 transition-transform duration-base ease-soft",
              isOpen && "rotate-180"
            )}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
            aria-hidden
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </button>
      </h3>

      <div
        id={panelId}
        role="region"
        aria-labelledby={buttonId}
        className={cn(
          "grid transition-[grid-template-rows] duration-base ease-soft",
          isOpen ? "grid-rows-[1fr]" : "grid-rows-[0fr]"
        )}
      >
        <div className="overflow-hidden">
          <div className="px-4 pb-4 pt-0.5">{children}</div>
        </div>
      </div>
    </div>
  );
};

CollapsePanel.displayName = "CollapsePanel";
