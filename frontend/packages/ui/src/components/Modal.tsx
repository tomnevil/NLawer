"use client";

import React, { useEffect, useCallback } from "react";
import { cn } from "../lib/cn";

export interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  description?: string;
  size?: "sm" | "md" | "lg" | "xl" | "full";
  closeOnOverlayClick?: boolean;
  closeOnEscape?: boolean;
  showCloseButton?: boolean;
  children: React.ReactNode;
  footer?: React.ReactNode;
}

const sizeStyles: Record<string, string> = {
  sm: "sm:max-w-sm",
  md: "sm:max-w-md",
  lg: "sm:max-w-lg",
  xl: "sm:max-w-xl",
  full: "sm:max-w-4xl",
};

/**
 * 模态框（v2「墨与纸」）：圆角 12px（`--r4`）、1px 边框 + 三级阴影。
 * 移动端占满宽度并预留安全区；需要底部上浮面板请使用 BottomSheet（阶段二）。
 */
export const Modal: React.FC<ModalProps> = ({
  isOpen,
  onClose,
  title,
  description,
  size = "md",
  closeOnOverlayClick = true,
  closeOnEscape = true,
  showCloseButton = true,
  children,
  footer,
}) => {
  const handleEscape = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape" && closeOnEscape) onClose();
    },
    [closeOnEscape, onClose]
  );

  useEffect(() => {
    if (!isOpen) return;
    document.addEventListener("keydown", handleEscape);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", handleEscape);
      document.body.style.overflow = previous;
    };
  }, [isOpen, handleEscape]);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-modal flex items-center justify-center"
      style={{ paddingTop: "var(--safe-top)", paddingBottom: "var(--safe-bottom)" }}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        className="absolute inset-0 animate-fade-in backdrop-blur-sm"
        style={{ backgroundColor: "var(--overlay-scrim)" }}
        onClick={closeOnOverlayClick ? onClose : undefined}
        aria-hidden
      />
      <div
        className={cn(
          "relative mx-3 flex max-h-full w-full flex-col animate-fade-in",
          "rounded-r4 border border-line bg-surface shadow-s3 sm:mx-4",
          sizeStyles[size]
        )}
      >
        {(title || showCloseButton) && (
          <div className="flex items-start justify-between gap-3 border-b border-line p-5">
            <div className="min-w-0">
              {title && <h2 className="text-h3 font-semibold text-ink-900">{title}</h2>}
              {description && <p className="mt-1 text-body-sm text-ink-500">{description}</p>}
            </div>
            {showCloseButton && (
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
            )}
          </div>
        )}
        <div className="scroll-thin max-h-[70vh] flex-1 overflow-y-auto p-5">{children}</div>
        {footer && (
          <div className="flex items-center justify-end gap-3 border-t border-line p-5">{footer}</div>
        )}
      </div>
    </div>
  );
};

Modal.displayName = "Modal";
