"use client";

import React from "react";
import { createPortal } from "react-dom";
import { cn } from "../../lib/cn";

export interface BottomSheetProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  description?: string;
  /** 面板高度占视口的比例，默认 0.6（内容超出则内部滚动） */
  heightRatio?: number;
  /** 是否允许下拉关闭 */
  dismissible?: boolean;
  /** 是否显示顶部拖拽把手 */
  handle?: boolean;
  /** 吸底操作区，会自动留出安全区 */
  footer?: React.ReactNode;
  /** 点击遮罩关闭 */
  closeOnOverlayClick?: boolean;
  children: React.ReactNode;
  className?: string;
  /**
   * 内容区的**滚动权**归谁。
   *
   * - `"sheet"`（默认）：`BottomSheet` 自带滚动容器（`overflow-y-auto` + `px-4 py-4`）
   * - `"content"`：把滚动权交给调用方 —— 内容区变成**不滚动、不带内边距**的
   *   `flex min-h-0 flex-1 flex-col` 槽位，由调用方的子元素自己滚动。
   *   适用于内嵌 `PullToRefresh` / 虚拟列表等**自带滚动容器**的组件。
   *
   * ⚠️ `"content"` 模式的**两条义务**：
   * 1. 自己滚动的那一层要 `flex-1`（即给 `PullToRefresh` 传 `className="flex-1"`）；
   * 2. 若再包一层 `flex` 容器，那一层**必须带 `min-h-0`** —— 否则调用方会被撑到
   *    内容高、内层**滚不动**（`scrollTop` 恒 0，**静默失效**，不报错）。
   *
   * 实测依据：`evidence/probe_sheet_scroll_contract.py`；契约与取舍见
   * `deliverables/ui-design/mobile-feature-integration-spec.md` §5「✅ 定稿」。
   */
  scrollOwner?: "sheet" | "content";
}

/** 拖拽超过该位移（px）才判定为关闭意图，避免误触。 */
const DISMISS_THRESHOLD = 96;

function useMounted() {
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);
  return mounted;
}

/**
 * 底部抽屉（移动端主交互容器）。
 *
 * 手机上「从下方升起」比「居中弹窗」更贴近拇指操作半径，也天然容纳
 * 更长的表单。支持拖拽下拉关闭（位移超过 96px 才生效，避免误触），
 * Esc 与遮罩点击均可关闭，打开时锁定背景滚动。
 *
 * 桌面端如确需使用，宽度会被限制在 560px 并居中——避免出现横跨
 * 1440px 的怪异长条。
 */
export const BottomSheet: React.FC<BottomSheetProps> = ({
  isOpen,
  onClose,
  title,
  description,
  heightRatio = 0.6,
  dismissible = true,
  handle = true,
  footer,
  closeOnOverlayClick = true,
  children,
  className,
  scrollOwner = "sheet",
}) => {
  const mounted = useMounted();
  const [dragY, setDragY] = React.useState(0);
  const [dragging, setDragging] = React.useState(false);
  const startYRef = React.useRef(0);

  // 锁定背景滚动
  React.useEffect(() => {
    if (!isOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [isOpen]);

  // Esc 关闭
  React.useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [isOpen, onClose]);

  // 关闭时复位拖拽位移
  React.useEffect(() => {
    if (!isOpen) setDragY(0);
  }, [isOpen]);

  if (!mounted || !isOpen) return null;

  const onPointerDown = (e: React.PointerEvent) => {
    if (!dismissible) return;
    startYRef.current = e.clientY;
    setDragging(true);
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!dragging) return;
    // 只允许向下拖，向上回弹到 0
    setDragY(Math.max(0, e.clientY - startYRef.current));
  };

  const onPointerUp = () => {
    if (!dragging) return;
    setDragging(false);
    if (dismissible && dragY > DISMISS_THRESHOLD) onClose();
    else setDragY(0);
  };

  return createPortal(
    <div className="fixed inset-0 z-sheet">
      <div
        className="absolute inset-0 animate-fade-in backdrop-blur-sm"
        style={{ backgroundColor: "var(--overlay-scrim)" }}
        onClick={closeOnOverlayClick ? onClose : undefined}
        aria-hidden
      />

      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={cn(
          "absolute inset-x-0 bottom-0 mx-auto flex w-full max-w-[560px] flex-col",
          "rounded-t-r4 border border-b-0 border-line bg-surface shadow-s3",
          "sm:inset-x-auto sm:bottom-6 sm:rounded-r4 sm:border-b",
          !dragging && "transition-transform duration-base ease-soft",
          className
        )}
        style={{
          height: `min(${Math.round(heightRatio * 100)}vh, calc(100vh - var(--statusbar-h) - 24px))`,
          transform: `translateY(${dragY}px)`,
        }}
      >
        {/* 拖拽把手 + 头部 */}
        <div
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          className={cn("shrink-0 px-4 pt-2", dismissible && "cursor-grab touch-none active:cursor-grabbing")}
        >
          {handle && (
            <div className="mx-auto mb-2 h-1 w-9 rounded-full bg-ink-300" aria-hidden />
          )}

          {(title || description) && (
            <div className="flex items-start justify-between gap-3 pb-3">
              <div className="min-w-0">
                {title && <h2 className="text-h4 text-ink-900">{title}</h2>}
                {description && <p className="mt-0.5 text-body-sm text-ink-500">{description}</p>}
              </div>
              <button
                type="button"
                onClick={onClose}
                aria-label="关闭"
                className="tap-ghost -mr-1 -mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-r2 text-ink-500 transition-colors duration-fast hover:bg-surface-hover hover:text-ink-900"
              >
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          )}

          {(title || description) && <div className="h-px bg-line" />}
        </div>

        {/* 内容区：滚动权按 `scrollOwner` 分派（契约见 spec §5「✅ 定稿」） */}
        {scrollOwner === "content" ? (
          <div className="flex min-h-0 flex-1 flex-col">{children}</div>
        ) : (
          <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-4 py-4">{children}</div>
        )}

        {/* 吸底操作区 */}
        {footer && (
          <div
            className="shrink-0 border-t border-line bg-surface px-4 pt-3"
            style={{ paddingBottom: "calc(0.75rem + var(--safe-bottom))" }}
          >
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body
  );
};

BottomSheet.displayName = "BottomSheet";
