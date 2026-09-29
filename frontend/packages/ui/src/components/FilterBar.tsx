"use client";

import React from "react";
import { cn } from "../lib/cn";

export interface FilterChip {
  id: string;
  /** 字段名，如「案件等级」 */
  label: string;
  /** 值，如「S」 */
  value: string;
}

export interface FilterBarProps {
  /** 已选条件，逐条回显 */
  chips: FilterChip[];
  onRemove?: (id: string) => void;
  onClearAll?: () => void;
  /** 结果条数，回显在左侧 */
  resultCount?: number;
  /** 右侧操作区（列设置、导出等） */
  actions?: React.ReactNode;
  /** 筛选器控件（下拉、搜索框等），排在 chips 之后 */
  children?: React.ReactNode;
  className?: string;
}

/**
 * 筛选条件条：已选条件回显 + 逐条移除 + 一键清空。
 *
 * 法律场景的筛选经常叠加多层（案件等级 × 阶段 × 律师 × 时间），
 * 因此「现在到底筛了什么」必须一眼可见，且能单条撤下而不是只能全部重置。
 */
export const FilterBar: React.FC<FilterBarProps> = ({
  chips,
  onRemove,
  onClearAll,
  resultCount,
  actions,
  children,
  className,
}) => {
  return (
    <div className={cn("flex flex-wrap items-center gap-x-3 gap-y-2", className)}>
      {children}

      {chips.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {chips.map((chip) => (
            <span
              key={chip.id}
              className="inline-flex items-center gap-1.5 rounded-r1 border border-brand-500/30 bg-brand-500/10 py-0.5 pl-2 pr-1 text-label text-link"
            >
              <span className="text-ink-500">{chip.label}</span>
              <span className="font-medium">{chip.value}</span>
              {onRemove && (
                <button
                  type="button"
                  onClick={() => onRemove(chip.id)}
                  aria-label={`移除筛选条件 ${chip.label} ${chip.value}`}
                  className="tap-ghost flex h-4 w-4 items-center justify-center rounded-[3px] text-ink-400 transition-colors duration-fast hover:bg-brand-500/20 hover:text-link"
                >
                  <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.2} aria-hidden>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              )}
            </span>
          ))}

          {onClearAll && chips.length > 1 && (
            <button
              type="button"
              onClick={onClearAll}
              className="text-label text-ink-500 underline-offset-2 transition-colors duration-fast hover:text-ink-900 hover:underline"
            >
              清空全部
            </button>
          )}
        </div>
      )}

      <div className="ml-auto flex items-center gap-2">
        {resultCount !== undefined && (
          <span className="text-caption text-ink-500">
            共 <span className="num text-ink-700">{resultCount}</span> 条
          </span>
        )}
        {actions}
      </div>
    </div>
  );
};

FilterBar.displayName = "FilterBar";
