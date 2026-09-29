"use client";

import React from "react";
import { cn } from "../lib/cn";
import { Spinner } from "./Spinner";

export interface TableColumn<T> {
  key: string;
  header: string;
  headerZh?: string;
  width?: string;
  align?: "left" | "center" | "right";
  render?: (value: any, row: T) => React.ReactNode;
}

export interface TableProps<T> {
  columns: TableColumn<T>[];
  data: T[];
  language?: "en" | "zh";
  loading?: boolean;
  emptyMessage?: string;
  emptyMessageZh?: string;
  onRowClick?: (row: T) => void;
  className?: string;
  headerClassName?: string;
  rowClassName?: string | ((row: T) => string);
}

/**
 * 基础表格（v2「墨与纸」）。
 * 表头浅填充 + 1px 边框分隔，行悬停用品牌色 5% 极淡填充。
 * 复杂的排序 / 筛选 / 列设置 / 三档密度 / 移动端转卡片由 DataTable 承担（阶段二）。
 */
export function Table<T extends Record<string, any>>({
  columns,
  data,
  language = "en",
  loading = false,
  emptyMessage = "No data available",
  emptyMessageZh = "暂无数据",
  onRowClick,
  className,
  headerClassName,
  rowClassName,
}: TableProps<T>) {
  const isZh = language === "zh";

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Spinner size="md" />
      </div>
    );
  }

  if (data.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12">
        <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-r3 border border-line bg-surface-subtle">
          <svg className="h-6 w-6 text-ink-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4"
            />
          </svg>
        </div>
        <p className="text-body-sm text-ink-500">{isZh ? emptyMessageZh : emptyMessage}</p>
      </div>
    );
  }

  return (
    <div className={cn("scroll-thin overflow-x-auto", className)}>
      <table className="w-full border-collapse">
        <thead>
          <tr className={cn("bg-surface-subtle", headerClassName)}>
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={cn(
                  "border-b border-line px-4 py-2.5 text-label font-medium",
                  "text-ink-500",
                  column.align === "center" && "text-center",
                  column.align === "right" && "text-right",
                  column.width && `w-[${column.width}]`
                )}
              >
                {isZh && column.headerZh ? column.headerZh : column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((row, rowIndex) => (
            <tr
              key={rowIndex}
              onClick={() => onRowClick?.(row)}
              className={cn(
                "border-b border-line transition-colors duration-fast",
                "hover:bg-brand-500/5",
                onRowClick && "cursor-pointer",
                typeof rowClassName === "function" ? rowClassName(row) : rowClassName
              )}
            >
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={cn(
                    "px-4 py-2.5 text-body-sm text-ink-700",
                    column.align === "center" && "text-center",
                    column.align === "right" && "text-right"
                  )}
                >
                  {column.render ? column.render(row[column.key], row) : row[column.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

Table.displayName = "Table";
