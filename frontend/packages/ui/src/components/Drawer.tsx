"use client";

import React, { useEffect } from "react";
import { cn } from "../lib/cn";

export interface DrawerProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  description?: string;
  side?: "right" | "left";
  /** 桌面宽度 320 / 420 / 560；移动端一律占满 */
  width?: "sm" | "md" | "lg";
  footer?: React.ReactNode;
  closeOnOverlayClick?: boolean;
  closeOnEscape?: boolean;
  children: React.ReactNode;
}

const widthStyles: Record<string, string> = {
  sm: "sm:w-[320px]",
  md: "sm:w-[420px]",
  lg: "sm:w-[560px]",
};

/**
 * 右侧（或左侧）滑出面板。240ms，`cubic-bezier(.2,.8,.2,1)`。
 *
 * 与 Modal 的分工：Modal 用于需要用户立刻决策的阻塞式交互；
 * Drawer 用于「查看/编辑某条记录」这类可随时离开的辅助面板。
 * 移动端占满宽度，底部预留安全区。
 */
export const Drawer: React.FC<DrawerProps> = ({
  isOpen,
  onClose,
  title,
  description,
  side = "right",
  width = "md",
  footer,
  closeOnOverlayClick = true,
  closeOnEscape = true,
  children,
}) => {
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && closeOnEscape) onClose();
    };
    document.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [isOpen, closeOnEscape, onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-drawer" role="dialog" aria-modal="true" aria-label={title}>
      <div
        className="absolute inset-0 animate-fade-in backdrop-blur-sm"
        style={{ backgroundColor: "var(--overlay-scrim)" }}
        onClick={closeOnOverlayClick ? onClose : undefined}
        aria-hidden
      />

      <div
        className={cn(
          "absolute inset-y-0 flex w-full flex-col border-line bg-surface shadow-s3",
          side === "right"
            ? "right-0 animate-panel-in-right border-l"
            : "left-0 animate-panel-in-left border-r",
          widthStyles[width]
        )}
      >
        {(title || description) && (
          <header className="flex shrink-0 items-start justify-between gap-3 border-b border-line p-5">
            <div className="min-w-0">
              {title && <h2 className="text-h4 font-semibold text-ink-900">{title}</h2>}
              {description && <p className="mt-1 text-body-sm text-ink-500">{description}</p>}
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="关闭"
              className="tap-ghost -mr-2 -mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-r1 text-ink-400 transition-colors duration-fast hover:bg-surface-hover hover:text-ink-900"
            >
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </header>
        )}

        <div className="scroll-thin flex-1 overflow-y-auto p-5">{children}</div>

        {footer && (
          <footer
            className="flex shrink-0 items-center justify-end gap-3 border-t border-line p-5"
            style={{ paddingBottom: "calc(1.25rem + var(--safe-bottom))" }}
          >
            {footer}
          </footer>
        )}
      </div>
    </div>
  );
};

Drawer.displayName = "Drawer";
