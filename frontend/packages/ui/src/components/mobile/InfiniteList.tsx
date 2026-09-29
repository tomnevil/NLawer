"use client";

import React from "react";
import { cn } from "../../lib/cn";
import { Spinner } from "../Spinner";

export interface InfiniteListProps<T> {
  items: T[];
  renderItem: (item: T, index: number) => React.ReactNode;
  rowKey: (item: T, index: number) => string;
  /** 是否还有下一页 */
  hasMore: boolean;
  /** 是否正在加载下一页 */
  loading?: boolean;
  onLoadMore: () => void;
  /** 哨兵元素提前触发的距离，默认 240px */
  rootMargin?: string;
  emptyState?: React.ReactNode;
  /**
   * **首屏**加载中（`items` 还空着、`loading` 为真）时渲染的内容，如骨架屏。
   *
   * 不传则落到下面的「终态区」——即一个居中的小转圈。骨架屏比转圈更能表达
   * 「即将出现的是一个列表」，所以已上线的列表页接进来时应当把原来的骨架屏传进来，
   * **否则这次接线会顺手把一个骨架屏降级成转圈**。
   */
  loadingState?: React.ReactNode;
  /** 全部加载完毕后的提示 */
  endMessage?: string;
  /** 加载失败时的提示与重试 */
  error?: string | null;
  onRetry?: () => void;
  className?: string;
  /**
   * 列表容器（`<ul>`）的类名，**默认 `"divide-y divide-line"`**（分隔线形态）。
   *
   * 为什么需要这个口子：本组件默认按「紧凑行 + 分隔线」画（如通知行）。
   * 但同一个 `InfiniteList` 也要用在**卡片列表**上（律师端 `/notifications` 的
   * 每行自带 `rounded-r3 border`）—— 那时分隔线会和卡片边框叠成双线。
   * 传 `"space-y-2"` 即可切回卡片形态，**默认值逐字不变**（老调用点零影响）。
   */
  listClassName?: string;
}

/**
 * 无限滚动列表。
 *
 * 用 IntersectionObserver 观察底部哨兵元素，`rootMargin` 提前 240px
 * 触发，让用户在滚到底之前数据就已就位——避免「滚到底 → 空转等待」
 * 的卡顿感。所有终态（加载中 / 加载失败 / 已到底 / 空）都有明确出口，
 * 不会出现无限转圈的幽灵状态。
 */
export function InfiniteList<T>({
  items,
  renderItem,
  rowKey,
  hasMore,
  loading = false,
  onLoadMore,
  rootMargin = "240px",
  emptyState,
  loadingState,
  endMessage = "没有更多了",
  error = null,
  onRetry,
  className,
  listClassName = "divide-y divide-line",
}: InfiniteListProps<T>) {
  const sentinelRef = React.useRef<HTMLDivElement>(null);

  // 用 ref 持有最新回调，避免把 onLoadMore 放进依赖导致观察器反复重建
  const loadMoreRef = React.useRef(onLoadMore);
  loadMoreRef.current = onLoadMore;
  const loadingRef = React.useRef(loading);
  loadingRef.current = loading;

  React.useEffect(() => {
    const el = sentinelRef.current;
    if (!el || !hasMore) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && !loadingRef.current) loadMoreRef.current();
      },
      { rootMargin, threshold: 0 }
    );

    observer.observe(el);
    return () => observer.disconnect();
  }, [hasMore, rootMargin]);

  if (items.length === 0 && loading && loadingState) {
    return <div className={className}>{loadingState}</div>;
  }

  if (items.length === 0 && !loading) {
    return (
      <div className={className}>
        {emptyState ?? (
          <p className="py-14 text-center text-body-sm text-ink-500">暂无数据</p>
        )}
      </div>
    );
  }

  return (
    <div className={className}>
      <ul className={listClassName}>
        {items.map((item, index) => (
          <li key={rowKey(item, index)}>{renderItem(item, index)}</li>
        ))}
      </ul>

      {/* 终态区 */}
      <div className="px-4 py-4">
        {error ? (
          <div className="flex flex-col items-center gap-2">
            <p className="text-body-sm text-danger-600">{error}</p>
            {onRetry && (
              <button
                type="button"
                onClick={onRetry}
                className="min-h-tap rounded-r2 border border-line px-3 text-label text-ink-700 transition-colors duration-fast hover:bg-surface-hover"
              >
                重试
              </button>
            )}
          </div>
        ) : loading ? (
          <div className="flex items-center justify-center gap-2 text-caption text-ink-500">
            <Spinner size="sm" label={null} />
            加载中…
          </div>
        ) : hasMore ? (
          // 哨兵：进入视口即加载
          <div ref={sentinelRef} className="h-8" aria-hidden />
        ) : (
          items.length > 0 && <p className="text-center text-caption text-ink-400">{endMessage}</p>
        )}
      </div>

      <span role="status" aria-live="polite" className="sr-only">
        {loading ? "正在加载更多" : hasMore ? "" : endMessage}
      </span>
    </div>
  );
}

InfiniteList.displayName = "InfiniteList";
