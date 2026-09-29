"use client";

import React from "react";
import { cn } from "../lib/cn";
import { Spinner } from "./Spinner";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /**
   * 变体。规范第 06.1 节定义 5 种：primary(pri) / verify(vfy) / secondary(sec) / ghost(gho) / danger(dgr)。
   * `outline` 与 `accent` 为兼容保留。
   *
   * ⚠️ 主色按钮全局唯一 —— 每屏最多一个 `primary`，用于该屏最关键动作。
   */
  variant?: "primary" | "verify" | "secondary" | "outline" | "ghost" | "danger" | "accent";
  /** 桌面高度 28 / 34 / 40px；移动端统一 48px（触控目标） */
  size?: "sm" | "md" | "lg";
  isLoading?: boolean;
  leftIcon?: React.ReactNode;
  rightIcon?: React.ReactNode;
}

const variantStyles: Record<string, string> = {
  // pri：主按钮，墨蓝实底。去掉了旧的靛蓝→青蓝渐变
  primary:
    "bg-brand-600 text-white shadow-s1 hover:bg-brand-700 active:bg-brand-800 border border-transparent",
  // vfy：律师已确认类动作，绿色实底
  verify:
    "bg-verified-600 text-white shadow-s1 hover:bg-verified-700 active:bg-verified-700 border border-transparent",
  // sec：次级，浅填充 + 边框
  secondary: "bg-surface-subtle text-ink-800 border border-line hover:bg-surface-hover",
  // outline：描边按钮
  outline: "bg-surface text-ink-700 border border-line-strong hover:bg-surface-hover hover:border-brand-400",
  // gho：幽灵按钮，无边框无底色
  ghost: "bg-transparent text-ink-600 border border-transparent hover:bg-surface-hover hover:text-ink-900",
  // dgr：破坏性操作
  danger:
    "bg-danger-500 text-white shadow-s1 hover:bg-danger-600 active:bg-danger-700 border border-transparent",
  // accent：香槟金强调（低频使用，注意全局金色面积 ≤ 3%）
  accent: "bg-gold-500 text-white shadow-s1 hover:bg-gold-600 border border-transparent",
};

// 桌面 28 / 34 / 40px；`min-h-tap` 保证移动端 48px 触控目标
const sizeStyles: Record<string, string> = {
  sm: "h-7 min-h-tap sm:min-h-0 px-3 text-label rounded-r2 gap-1.5",
  md: "h-[34px] min-h-tap sm:min-h-0 px-4 text-body-sm rounded-r2 gap-2",
  lg: "h-10 min-h-tap sm:min-h-0 px-5 text-body rounded-r2 gap-2",
};

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      className,
      variant = "primary",
      size = "md",
      isLoading = false,
      leftIcon,
      rightIcon,
      disabled,
      children,
      ...props
    },
    ref
  ) => {
    return (
      <button
        ref={ref}
        className={cn(
          "inline-flex items-center justify-center font-medium whitespace-nowrap",
          "transition-colors duration-fast ease-out",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30 focus-visible:ring-offset-2 focus-visible:ring-offset-surface",
          "disabled:cursor-not-allowed disabled:opacity-50",
          variantStyles[variant],
          sizeStyles[size],
          className
        )}
        disabled={disabled || isLoading}
        aria-busy={isLoading || undefined}
        {...props}
      >
        {isLoading ? (
          <Spinner size="sm" label={null} />
        ) : (
          leftIcon
        )}
        {children}
        {!isLoading && rightIcon}
      </button>
    );
  }
);

Button.displayName = "Button";
