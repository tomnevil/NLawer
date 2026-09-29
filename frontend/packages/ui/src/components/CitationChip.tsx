"use client";

import React from "react";
import { cn } from "../lib/cn";

export interface CitationChipProps {
  /** 引用序号，与 CitationPanel 中的条目一一对应 */
  index: number;
  /** 当前被选中的引用（正文对应处可同步高亮） */
  active?: boolean;
  onClick?: () => void;
  /** 悬浮提示，一般传法条名 */
  title?: string;
  className?: string;
}

/**
 * 正文内引用序号（规范第 02.3 节：引用序号用点缀金）。
 *
 * 作为上标嵌在结论文字之后，点击后联动右侧常驻的 `CitationPanel`。
 * 移动端改为页内展开，行为一致。
 */
export const CitationChip: React.FC<CitationChipProps> = ({
  index,
  active = false,
  onClick,
  title,
  className,
}) => {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title ? `引用 ${index}：${title}` : `引用 ${index}`}
      aria-label={title ? `引用 ${index}：${title}` : `引用 ${index}`}
      aria-pressed={active}
      className={cn(
        "num mx-0.5 inline-flex h-[18px] min-w-[18px] -translate-y-1 items-center justify-center",
        "rounded-r1 border px-1 align-super text-caption font-medium leading-none",
        "transition-colors duration-fast ease-out",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30",
        active
          ? "border-gold-600 bg-gold-600 text-white"
          : "border-gold-400/60 bg-gold-500/15 text-gold-700 hover:border-gold-500 hover:bg-gold-500/25",
        className
      )}
    >
      {index}
    </button>
  );
};

CitationChip.displayName = "CitationChip";
