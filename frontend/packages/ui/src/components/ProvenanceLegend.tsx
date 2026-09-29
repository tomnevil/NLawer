"use client";

import React from "react";
import { cn } from "../lib/cn";
import type { ProvenanceState } from "../theme/colors";
import { ProvenanceBadge } from "./ProvenanceBadge";

export interface ProvenanceLegendProps {
  /**
   * `default` 用于桌面顶栏常驻；
   * `compact` 用于移动端置于问题标题下方（更小、可换行）。
   */
  variant?: "default" | "compact";
  /** 只展示指定状态（默认三态全展示） */
  states?: ProvenanceState[];
  className?: string;
}

const ALL: ProvenanceState[] = ["ai", "verified", "pending"];

/**
 * 责任边界三态全局图例（规范第 05 节）。
 *
 * 问答页、文书页、案件详情页顶栏常驻；移动端置于问题标题下方。
 * 作用是让用户在任何位置都能立刻解读三态含义，不必猜测颜色。
 */
export const ProvenanceLegend: React.FC<ProvenanceLegendProps> = ({
  variant = "default",
  states = ALL,
  className,
}) => {
  const compact = variant === "compact";

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-2 gap-y-1.5",
        !compact && "rounded-r2 border border-line bg-surface-subtle px-3 py-1.5",
        className
      )}
      aria-label="责任边界图例"
    >
      {!compact && <span className="text-caption text-ink-500">责任边界</span>}
      {states.map((state) => (
        <ProvenanceBadge key={state} state={state} size={compact ? "sm" : "sm"} />
      ))}
    </div>
  );
};

ProvenanceLegend.displayName = "ProvenanceLegend";
