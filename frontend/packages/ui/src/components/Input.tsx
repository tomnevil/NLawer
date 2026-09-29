"use client";

import React from "react";
import { cn } from "../lib/cn";

export interface InputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "size"> {
  label?: string;
  error?: string;
  helperText?: string;
  leftIcon?: React.ReactNode;
  rightIcon?: React.ReactNode;
  size?: "sm" | "md" | "lg";
}

const sizeStyles: Record<string, string> = {
  sm: "h-8 min-h-tap sm:min-h-0 px-3 text-body-sm",
  md: "h-[34px] min-h-tap sm:min-h-0 px-3 text-body",
  lg: "h-11 min-h-tap sm:min-h-0 px-3.5 text-body",
};

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, label, error, helperText, leftIcon, rightIcon, size = "md", id, ...props }, ref) => {
    const inputId = id || label?.toLowerCase().replace(/\s+/g, "-");

    return (
      <div className="w-full">
        {label && (
          <label htmlFor={inputId} className="mb-1.5 block text-body-sm font-medium text-ink-700">
            {label}
          </label>
        )}
        <div className="relative">
          {leftIcon && (
            <div className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-400">
              {leftIcon}
            </div>
          )}
          <input
            ref={ref}
            id={inputId}
            className={cn(
              "w-full rounded-r2 border bg-surface text-ink-900 placeholder:text-ink-400",
              "transition-colors duration-fast ease-out",
              "focus:outline-none focus:ring-2 focus:ring-brand-500/30",
              error
                ? "border-danger-500/50 focus:border-danger-500"
                : "border-line-strong focus:border-brand-400",
              leftIcon && "pl-10",
              rightIcon && "pr-10",
              sizeStyles[size],
              className
            )}
            aria-invalid={error ? true : undefined}
            {...props}
          />
          {rightIcon && (
            <div className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-400">{rightIcon}</div>
          )}
        </div>
        {error && <p className="mt-1.5 text-label text-danger-600">{error}</p>}
        {helperText && !error && <p className="mt-1.5 text-label text-ink-500">{helperText}</p>}
      </div>
    );
  }
);

Input.displayName = "Input";
