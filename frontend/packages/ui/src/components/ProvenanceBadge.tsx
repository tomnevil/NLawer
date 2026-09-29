"use client";

import React from "react";
import { cn } from "../lib/cn";
import { provenanceStates, type ProvenanceState } from "../theme/colors";

export interface ProvenanceBadgeProps {
  state: ProvenanceState;
  /** AI 生成可传模型名；律师已确认传确认人 */
  by?: string;
  /** 已格式化的时间字符串 */
  at?: string;
  /** 点击查看流转记录（规范第 05 节：状态可迁移、留痕） */
  onOpenHistory?: () => void;
  size?: "sm" | "md";
  className?: string;
}

const stateStyles: Record<ProvenanceState, string> = {
  ai: "bg-ai-500/15 text-ai-600 border-ai-500/30",
  verified: "bg-verified-500/15 text-verified-600 border-verified-500/30",
  pending: "bg-pending-500/15 text-pending-600 border-pending-500/30",
};

const sizeStyles: Record<string, string> = {
  sm: "gap-1 px-1.5 py-0.5 text-caption",
  md: "gap-1.5 px-2 py-0.5 text-label",
};

/**
 * 责任边界三态徽章（规范第 05 节）。
 *
 * **三重编码**：颜色 + 文字 + 图标。颜色不可独立承载语义——
 * 色盲用户与黑白打印必须仍能分辨，因此图标与文字是必需的，不是装饰。
 *
 * 三态为穷尽且互斥：任何法律内容必须且只能属于其中之一。
 */
export const ProvenanceBadge: React.FC<ProvenanceBadgeProps> = ({
  state,
  by,
  at,
  onOpenHistory,
  size = "md",
  className,
}) => {
  const meta = provenanceStates[state];
  const clickable = Boolean(onOpenHistory);

  const inner = (
    <>
      <span aria-hidden className="leading-none">
        {meta.icon}
      </span>
      <span>{meta.label}</span>
      {by && <span className="opacity-75">· {by}</span>}
      {at && <span className="num opacity-75">{at}</span>}
      {clickable && (
        <svg
          className="h-3 w-3 opacity-60"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
          aria-hidden
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="m9 6 6 6-6 6" />
        </svg>
      )}
    </>
  );

  const base = cn(
    "inline-flex items-center whitespace-nowrap rounded-r1 border font-medium",
    stateStyles[state],
    sizeStyles[size],
    clickable &&
      "cursor-pointer transition-colors duration-fast hover:brightness-[0.97] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30",
    className
  );

  if (clickable) {
    return (
      <button
        type="button"
        onClick={onOpenHistory}
        className={base}
        title={`${meta.label}${by ? ` · ${by}` : ""}${at ? ` · ${at}` : ""} — 查看流转记录`}
      >
        {inner}
      </button>
    );
  }

  return (
    <span className={base} title={`${meta.label}${by ? ` · ${by}` : ""}${at ? ` · ${at}` : ""}`}>
      {inner}
    </span>
  );
};

ProvenanceBadge.displayName = "ProvenanceBadge";
