"use client";

import React from "react";
import { cn } from "../lib/cn";

export interface AlertProps {
  variant?: "info" | "success" | "warning" | "error";
  title?: string;
  children: React.ReactNode;
  icon?: React.ReactNode;
  onClose?: () => void;
  className?: string;
}

const variantStyles: Record<string, { bg: string; border: string; icon: string }> = {
  info: { bg: "bg-info-500/10", border: "border-info-500/30", icon: "text-info-600" },
  // success -> 律师已确认语义色
  success: { bg: "bg-verified-500/10", border: "border-verified-500/30", icon: "text-verified-600" },
  // warning -> 待复核语义色
  warning: { bg: "bg-pending-500/10", border: "border-pending-500/30", icon: "text-pending-600" },
  error: { bg: "bg-danger-500/10", border: "border-danger-500/30", icon: "text-danger-600" },
};

const defaultIcons: Record<string, React.ReactNode> = {
  info: (
    <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
      />
    </svg>
  ),
  success: (
    <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
      <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  ),
  warning: (
    <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
      />
    </svg>
  ),
  error: (
    <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z"
      />
    </svg>
  ),
};

export const Alert: React.FC<AlertProps> = ({ variant = "info", title, children, icon, onClose, className }) => {
  const styles = variantStyles[variant];

  return (
    <div
      className={cn("flex items-start gap-3 rounded-r2 border p-4", styles.bg, styles.border, className)}
      role="alert"
    >
      <span className={cn("mt-0.5 shrink-0", styles.icon)}>{icon || defaultIcons[variant]}</span>
      <div className="min-w-0 flex-1">
        {title && <h4 className="mb-1 text-body-sm font-medium text-ink-900">{title}</h4>}
        <div className="text-body-sm text-ink-700">{children}</div>
      </div>
      {onClose && (
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭提示"
          className="tap-ghost shrink-0 rounded-r1 p-1 text-ink-400 transition-colors duration-fast hover:bg-surface-hover hover:text-ink-700"
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      )}
    </div>
  );
};

Alert.displayName = "Alert";
