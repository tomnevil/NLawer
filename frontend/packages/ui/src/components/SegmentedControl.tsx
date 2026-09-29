"use client";

import React, { useRef } from "react";
import { cn } from "../lib/cn";

export interface SegmentedControlOption<T extends string = string> {
  value: T;
  label: string;
  icon?: React.ReactNode;
  badge?: string | number;
  disabled?: boolean;
}

export interface SegmentedControlProps<T extends string = string> {
  options: SegmentedControlOption<T>[];
  value: T;
  onChange: (value: T) => void;
  size?: "sm" | "md";
  fullWidth?: boolean;
  ariaLabel?: string;
  className?: string;
}

const sizeStyles: Record<string, string> = {
  sm: "h-7 px-2.5 text-label gap-1.5",
  md: "h-8 min-h-tap sm:min-h-0 px-3 text-body-sm gap-1.5",
};

/**
 * 分段控制器（角色 / 模式 / 时间范围切换）。
 *
 * 用 `role="radiogroup"` + `role="radio"` 语义，支持左右方向键切换，
 * 键盘可达。移动端触控目标 48px。
 */
export function SegmentedControl<T extends string = string>({
  options,
  value,
  onChange,
  size = "md",
  fullWidth = false,
  ariaLabel,
  className,
}: SegmentedControlProps<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const move = (from: number, delta: number) => {
    const n = options.length;
    for (let step = 1; step <= n; step++) {
      const next = (from + delta * step + n * step) % n;
      if (!options[next]?.disabled) {
        onChange(options[next].value);
        refs.current[next]?.focus();
        return;
      }
    }
  };

  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className={cn(
        "inline-flex rounded-r2 border border-line bg-surface-subtle p-0.5",
        fullWidth && "flex w-full",
        className
      )}
    >
      {options.map((option, i) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={active}
            disabled={option.disabled}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(option.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowRight" || e.key === "ArrowDown") {
                e.preventDefault();
                move(i, 1);
              } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
                e.preventDefault();
                move(i, -1);
              }
            }}
            className={cn(
              "inline-flex items-center justify-center whitespace-nowrap rounded-[4px] font-medium",
              "transition-colors duration-fast ease-out",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30",
              "disabled:cursor-not-allowed disabled:opacity-50",
              fullWidth && "flex-1",
              sizeStyles[size],
              active
                ? "bg-surface text-ink-900 shadow-s1"
                : "text-ink-600 hover:text-ink-900"
            )}
          >
            {option.icon && (
              <span className="flex h-4 w-4 items-center justify-center [&>svg]:h-4 [&>svg]:w-4">
                {option.icon}
              </span>
            )}
            {option.label}
            {option.badge !== undefined && option.badge !== 0 && (
              <span
                className={cn(
                  "num rounded-r1 px-1 text-caption leading-4",
                  active ? "bg-brand-500/15 text-link" : "bg-surface-hover text-ink-500"
                )}
              >
                {option.badge}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

SegmentedControl.displayName = "SegmentedControl";
