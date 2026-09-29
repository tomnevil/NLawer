"use client";

import React from "react";
import { cn } from "../lib/cn";

export interface SkeletonProps extends React.HTMLAttributes<HTMLDivElement> {
  /** 预设形状：文本行 / 圆形头像 / 卡片块 */
  variant?: "text" | "circle" | "block";
  lines?: number;
}

/** 骨架屏占位（加载态）。颜色取自令牌，深色模式自动适配。 */
export const Skeleton: React.FC<SkeletonProps> = ({ className, variant = "block", lines = 1, ...props }) => {
  if (variant === "text" && lines > 1) {
    return (
      <div className={cn("space-y-2", className)} {...props}>
        {Array.from({ length: lines }).map((_, i) => (
          <div
            key={i}
            className={cn("h-3.5 animate-pulse-soft rounded-r1 bg-surface-hover", i === lines - 1 ? "w-2/3" : "w-full")}
          />
        ))}
      </div>
    );
  }
  const shape =
    variant === "circle"
      ? "rounded-full bg-surface-hover animate-pulse-soft"
      : "rounded-r2 bg-surface-hover animate-pulse-soft";
  return <div className={cn(shape, className)} {...props} />;
};

Skeleton.displayName = "Skeleton";
