"use client";

import React from "react";
import { cn } from "../lib/cn";

export interface PaginationProps {
  /** 当前页，从 1 开始 */
  page: number;
  pageSize: number;
  /** 总条数 */
  total: number;
  onPageChange: (page: number) => void;
  onPageSizeChange?: (pageSize: number) => void;
  pageSizeOptions?: number[];
  className?: string;
}

/** 生成页码序列，过长时用省略号折叠（始终保持首尾与当前页附近可见）。 */
function pageList(current: number, totalPages: number): (number | "gap")[] {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, i) => i + 1);

  const out: (number | "gap")[] = [1];
  const start = Math.max(2, current - 1);
  const end = Math.min(totalPages - 1, current + 1);

  if (start > 2) out.push("gap");
  for (let p = start; p <= end; p++) out.push(p);
  if (end < totalPages - 1) out.push("gap");
  out.push(totalPages);

  return out;
}

/* 🚨 §8.3 的两条要求（「48px 不可见热区」· 「相邻可点元素间距 ≥ 8px」）对**并排小目标**
   **在数学上不相容**：热区每侧外扩 `(48 − W) / 2` ⇒ 两个热区不重叠 ⇔ **`g ≥ 48 − W`**。
   取 `g = 8`（`gap-2`）⇒ **`W ≥ 40`**。故盒宽取 **40px**（`min-w-10`，与 `Button` 的 `h-10` 同刻度）
   —— 这是**唯一不必在规范里开例外**的解法。
   ⚠️ 宽度侧**必须无条件生效**（不能只在移动端放大），因为判据量的是**水平相邻**；
      此前 `min-w-8`（32px）恒定 ⇒ 热区各外扩 8px ⇒ 与 8px 间距重叠 12px。
   定稿记录：`deliverables/ui-design/design-spec-addendum-8.3-tap-targets.md`（方案 ①）。 */
const btn =
  "num inline-flex h-10 min-h-tap sm:min-h-0 min-w-10 items-center justify-center rounded-r2 px-2 text-body-sm " +
  "transition-colors duration-fast ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30 " +
  "disabled:cursor-not-allowed disabled:opacity-40";

/**
 * 分页器：页码 + 每页条数 + 总数。
 * 移动端触控目标 48px；**盒宽 40px + 相邻间距 8px**（§8.3 的相容阈值，见 `btn` 的注释）。
 * 页码过长时自动折叠为省略号。
 */
export const Pagination: React.FC<PaginationProps> = ({
  page,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = [10, 20, 50, 100],
  className,
}) => {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const current = Math.min(Math.max(1, page), totalPages);
  const from = total === 0 ? 0 : (current - 1) * pageSize + 1;
  const to = Math.min(current * pageSize, total);

  return (
    <div
      className={cn(
        "flex flex-wrap items-center justify-between gap-x-4 gap-y-3 border-t border-line px-4 py-3",
        className
      )}
    >
      <p className="text-caption text-ink-500">
        共 <span className="num text-ink-700">{total}</span> 条
        {total > 0 && (
          <>
            ，当前 <span className="num text-ink-700">{from}</span>–
            <span className="num text-ink-700">{to}</span>
          </>
        )}
      </p>

      <div className="flex items-center gap-3">
        {onPageSizeChange && (
          <label className="flex items-center gap-1.5 text-caption text-ink-500">
            每页
            <select
              value={pageSize}
              onChange={(e) => onPageSizeChange(Number(e.target.value))}
              className={cn(
                "num h-8 min-h-tap sm:min-h-0 rounded-r2 border border-line bg-surface px-1.5 text-body-sm text-ink-800",
                "focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-500/30"
              )}
            >
              {pageSizeOptions.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
            条
          </label>
        )}

        <nav className="flex items-center gap-2" aria-label="分页">
          <button
            type="button"
            className={cn(btn, "text-ink-600 hover:bg-surface-hover hover:text-ink-900")}
            onClick={() => onPageChange(current - 1)}
            disabled={current <= 1}
            aria-label="上一页"
          >
            ‹
          </button>

          {pageList(current, totalPages).map((p, i) =>
            p === "gap" ? (
              <span key={`gap-${i}`} className="px-1 text-ink-400" aria-hidden>
                …
              </span>
            ) : (
              <button
                key={p}
                type="button"
                onClick={() => onPageChange(p)}
                aria-current={p === current ? "page" : undefined}
                className={cn(
                  btn,
                  p === current
                    ? "bg-brand-600 text-white"
                    : "text-ink-600 hover:bg-surface-hover hover:text-ink-900"
                )}
              >
                {p}
              </button>
            )
          )}

          <button
            type="button"
            className={cn(btn, "text-ink-600 hover:bg-surface-hover hover:text-ink-900")}
            onClick={() => onPageChange(current + 1)}
            disabled={current >= totalPages}
            aria-label="下一页"
          >
            ›
          </button>
        </nav>
      </div>
    </div>
  );
};

Pagination.displayName = "Pagination";
