"use client";

import React from "react";
import { cn } from "../lib/cn";

/** v2 语义色调。旧名 `emerald/teal/amber/cyan/red` 为兼容保留。 */
export type KpiTone = "brand" | "verified" | "pending" | "danger" | "info" | "ai" | "gold";

const LEGACY_TONE: Record<string, KpiTone> = {
  emerald: "verified",
  teal: "info",
  cyan: "info",
  amber: "pending",
  red: "danger",
};

export interface KpiCardProps {
  label: string;
  value: string | number;
  /** 单位以小字附在数值之后（如「元」「件」） */
  unit?: string;
  icon?: React.ReactNode;
  /** 环比变化，正数向上、负数向下 */
  change?: number;
  changeLabel?: string;
  color?: KpiTone | "emerald" | "teal" | "amber" | "cyan" | "red";
  className?: string;
}

const toneStyles: Record<KpiTone, { bg: string; text: string }> = {
  brand: { bg: "bg-brand-500/15", text: "text-link" },
  verified: { bg: "bg-verified-500/15", text: "text-verified-600" },
  pending: { bg: "bg-pending-500/15", text: "text-pending-600" },
  danger: { bg: "bg-danger-500/15", text: "text-danger-600" },
  info: { bg: "bg-info-500/15", text: "text-info-600" },
  ai: { bg: "bg-ai-500/15", text: "text-ai-600" },
  gold: { bg: "bg-gold-500/15", text: "text-gold-600" },
};

/**
 * KPI 指标卡（v2「墨与纸」）。
 *
 * 数值采用 28px 等宽 + `tabular-nums`，保证多卡并排时数字对齐。
 * `color` 已修正为语义色调 —— 旧版 `color="emerald"` 因色板键名与色值错位，
 * 实际渲染出的是靛蓝（详见 `theme/colors.ts` 的修复说明）。
 */
export const KpiCard: React.FC<KpiCardProps> = ({
  label,
  value,
  unit,
  icon,
  change,
  changeLabel,
  color = "brand",
  className,
}) => {
  const tone: KpiTone = (LEGACY_TONE[color] ?? color) as KpiTone;
  const toneStyle = toneStyles[tone] ?? toneStyles.brand;
  const isPositive = change !== undefined && change >= 0;

  return (
    <div
      className={cn(
        "rounded-r3 border border-line bg-surface p-4 shadow-s1",
        "transition-colors duration-base ease-out hover:border-brand-400/60",
        className
      )}
    >
      <div className="mb-3 flex items-center justify-between">
        {icon && (
          <div className={cn("flex h-9 w-9 items-center justify-center rounded-r2", toneStyle.bg)}>
            <span className={cn("text-body-sm", toneStyle.text)}>{icon}</span>
          </div>
        )}
        {change !== undefined && (
          <span
            className={cn(
              "rounded-r1 px-2 py-0.5 text-label font-medium",
              isPositive ? "bg-verified-500/15 text-verified-600" : "bg-danger-500/15 text-danger-600"
            )}
          >
            {isPositive ? "↑" : "↓"} {Math.abs(change)}%
          </span>
        )}
      </div>
      <div className="mb-1 text-label text-ink-500">{label}</div>
      <div className="flex items-baseline gap-1">
        <span className="num text-[28px] font-semibold leading-tight text-ink-900">{value}</span>
        {unit && <span className="text-body-sm text-ink-500">{unit}</span>}
      </div>
      {changeLabel && <div className="mt-1 text-label text-ink-500">{changeLabel}</div>}
    </div>
  );
};

KpiCard.displayName = "KpiCard";
