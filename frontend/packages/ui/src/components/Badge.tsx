"use client";

import React from "react";
import { cn } from "../lib/cn";

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  /**
   * 语义变体。`verified` / `pending` 对应责任边界三态中的两态；
   * `success` / `warning` 为兼容别名，指向同一组语义色。
   */
  variant?:
    | "default"
    | "verified"
    | "pending"
    | "success"
    | "warning"
    | "error"
    | "danger"
    | "info"
    | "primary"
    | "ai"
    | "gold"
    | "neutral"
    | "ink"
    | "outline";
  size?: "sm" | "md" | "lg";
  dot?: boolean;
}

/** 把兼容别名归一到语义变体。 */
const normalize = (variant: string): string => {
  if (variant === "success") return "verified";
  if (variant === "warning") return "pending";
  if (variant === "error") return "danger";
  if (variant === "ink") return "neutral";
  return variant;
};

const variantStyles: Record<string, string> = {
  default: "bg-surface-subtle text-ink-600 border-line",
  // 律师已确认
  verified: "bg-verified-500/15 text-verified-600 border-verified-500/30",
  // 待复核 / 风险
  pending: "bg-pending-500/15 text-pending-600 border-pending-500/30",
  // 高风险
  danger: "bg-danger-500/15 text-danger-600 border-danger-500/30",
  info: "bg-info-500/15 text-info-600 border-info-500/30",
  primary: "bg-brand-500/15 text-link border-brand-500/30",
  outline: "bg-surface text-link border-brand-400",
  // AI 生成：专门标识 AI 生成内容，禁止作装饰色
  ai: "bg-ai-500/15 text-ai-600 border-ai-500/30",
  // S 级案件 / 高价值标记。金色全局面积 ≤3%，此处已是允许的少数用途之一
  gold: "bg-gold-500/15 text-gold-700 border-gold-500/30",
  // 已归档 / 中性状态：刻意不抢注意力
  neutral: "bg-surface-subtle text-ink-500 border-line",
};

const dotColor: Record<string, string> = {
  default: "bg-ink-400",
  verified: "bg-verified-500",
  pending: "bg-pending-500",
  danger: "bg-danger-500",
  info: "bg-info-500",
  primary: "bg-brand-500",
  ai: "bg-ai-500",
  gold: "bg-gold-500",
  neutral: "bg-ink-400",
};

const sizeStyles: Record<string, string> = {
  sm: "px-1.5 py-0.5 text-caption gap-1",
  md: "px-2 py-0.5 text-label gap-1.5",
  lg: "px-2.5 py-1 text-body-sm gap-1.5",
};

export const Badge = React.forwardRef<HTMLSpanElement, BadgeProps>(
  ({ className, variant = "default", size = "md", dot = false, children, ...props }, ref) => {
    const key = normalize(variant);
    return (
      <span
        ref={ref}
        className={cn(
          "inline-flex items-center whitespace-nowrap border font-medium rounded-r1",
          variantStyles[key],
          sizeStyles[size],
          className
        )}
        {...props}
      >
        {dot && <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", dotColor[key])} aria-hidden />}
        {children}
      </span>
    );
  }
);

Badge.displayName = "Badge";
