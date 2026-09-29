"use client";

import React from "react";
import { cn } from "../../lib/cn";
import { Spinner } from "../Spinner";

/**
 * 外部滚动目标 —— 给「页面由**文档**滚动」的骨架页用（见 `scrollTarget`）。
 *
 * - `"window"`：页面由文档滚动。真正在滚的是 `document.scrollingElement`
 *   （`AppShell` 型骨架：根是 `min-h-screen`、`<main>` **没有 `overflow`**）。
 * - `string`：CSS 选择器，经 `document.querySelector` 解析。
 * - `{ current }`：任意 ref —— 写成**结构化**类型而不是 `React.RefObject<HTMLElement | null>`，
 *   因为后者 `current` 可写 ⇒ **不变** ⇒ `RefObject<HTMLDivElement | null>` 传不进来。
 */
export type PullToRefreshScrollTarget = "window" | string | { readonly current: HTMLElement | null };

export interface PullToRefreshProps {
  onRefresh: () => Promise<void> | void;
  /** 触发刷新所需的下拉位移（px），默认 64 */
  threshold?: number;
  /** 最大下拉位移（px），默认 120 */
  maxPull?: number;
  /** 阻尼系数，越小越"拉不动"，默认 0.5 */
  resistance?: number;
  disabled?: boolean;
  /**
   * 外部滚动元素（见 `PullToRefreshScrollTarget`）。
   *
   * **不传 = 现状逐字不变**：组件自带滚动容器，手势守卫读它自己的 `scrollTop`。
   * 传了 ⇒ 组件**不再自建滚动容器**（内容随页面自然流动），守卫改读该目标的 `scrollTop`。
   *
   * 为什么需要它：守卫是「不在顶部就不接管」（`scrollTop > 0 ⇒ return`），
   * 而它默认读的是**自己那个内层容器**。走 `AppShell` 的页面由**文档**滚动，
   * 内层**永远不会滚**、`scrollTop` 恒 0 ⇒ 守卫失效（列表滚到中段上滑仍会触发刷新）。
   */
  scrollTarget?: PullToRefreshScrollTarget;
  /** 刷新中显示的文案 */
  refreshingLabel?: string;
  /** 下拉但未达阈值时的提示 */
  pullLabel?: string;
  /** 已达阈值、松手即刷新 */
  releaseLabel?: string;
  className?: string;
  children: React.ReactNode;
}

/**
 * 读外部滚动目标的滚动位置；`null` = **取不到目标**。
 *
 * 取不到（选择器无命中 / ref 未挂载 / 选择器非法）时**刻意返回 `null` 而不是 0**：
 * `0` 会被当成「在顶部」⇒ **永远接管**手势 ⇒ 列表滚到中段仍触发刷新 ——
 * 正是 `scrollTarget` 要修的那个 bug。宁可**不接管**，也不要错动。
 */
function readExternalScrollTop(target: PullToRefreshScrollTarget): number | null {
  if (target === "window") {
    const se = document.scrollingElement ?? document.documentElement;
    return se ? se.scrollTop : window.scrollY;
  }
  if (typeof target === "string") {
    try {
      const el = document.querySelector(target);
      return el ? el.scrollTop : null;
    } catch {
      return null; // 非法选择器（例如把选择器写成 `a b`）—— 同样按「取不到」处理
    }
  }
  return target.current ? target.current.scrollTop : null;
}

/**
 * 下拉刷新。
 *
 * 只在容器滚动到顶部时接管手势，因此不会和页面正常滚动打架；
 * 位移按阻尼系数衰减，给用户"有阻力"的物理反馈。刷新期间指示器
 * 保持在阈值高度，让「正在刷新」这件事在拇指遮挡区域之外可见。
 */
export const PullToRefresh: React.FC<PullToRefreshProps> = ({
  onRefresh,
  threshold = 64,
  maxPull = 120,
  resistance = 0.5,
  disabled = false,
  scrollTarget,
  refreshingLabel = "正在刷新…",
  pullLabel = "下拉刷新",
  releaseLabel = "松开刷新",
  className,
  children,
}) => {
  const [pull, setPull] = React.useState(0);
  const [refreshing, setRefreshing] = React.useState(false);
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const startYRef = React.useRef<number | null>(null);

  const reachThreshold = pull >= threshold;

  /**
   * 手势守卫：**内容是否已在顶部**。
   *
   * 不传 `scrollTarget` ⇒ 读自带内层容器（现状逐字不变）。
   * 传了 ⇒ 读外部目标；**取不到目标 ⇒ 判「不在顶部」= 不接管**（见 `readExternalScrollTop`）。
   */
  const atTop = (): boolean => {
    if (scrollTarget === undefined) {
      const el = scrollRef.current;
      return el !== null && el.scrollTop <= 0;
    }
    const top = readExternalScrollTop(scrollTarget);
    return top !== null && top <= 0;
  };

  const onTouchStart = (e: React.TouchEvent) => {
    if (disabled || refreshing) return;
    // 仅当内容已在顶部时才记录起点
    if (!atTop()) return;
    startYRef.current = e.touches[0].clientY;
  };

  const onTouchMove = (e: React.TouchEvent) => {
    if (disabled || refreshing || startYRef.current === null) return;
    if (!atTop()) {
      startYRef.current = null;
      setPull(0);
      return;
    }

    const delta = e.touches[0].clientY - startYRef.current;
    if (delta <= 0) {
      setPull(0);
      return;
    }
    setPull(Math.min(maxPull, delta * resistance));
  };

  const onTouchEnd = async () => {
    if (disabled || refreshing) return;
    startYRef.current = null;

    if (pull >= threshold) {
      setRefreshing(true);
      setPull(threshold);
      try {
        await onRefresh();
      } finally {
        setRefreshing(false);
        setPull(0);
      }
    } else {
      setPull(0);
    }
  };

  const label = refreshing ? refreshingLabel : reachThreshold ? releaseLabel : pullLabel;

  return (
    <div className={cn("relative flex min-h-0 flex-col", className)}>
      {/* 指示器：随下拉位移从顶部推出 */}
      <div
        aria-hidden={!refreshing}
        className="pointer-events-none absolute inset-x-0 top-0 z-10 flex items-end justify-center overflow-hidden"
        style={{ height: pull }}
      >
        <div className="flex items-center gap-2 pb-2 text-caption text-ink-500">
          {refreshing ? (
            <Spinner size="sm" label={null} />
          ) : (
            <svg
              className={cn("h-4 w-4 transition-transform duration-base", reachThreshold && "rotate-180")}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
              aria-hidden
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 14l-7 7-7-7M12 21V3" />
            </svg>
          )}
          <span>{label}</span>
        </div>
      </div>

      <div
        ref={scrollRef}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        onTouchCancel={onTouchEnd}
        className={cn(
          // 默认：组件自带滚动容器（现状逐字不变）。
          // 外部模式：**必须**去掉 `overflow-y-auto` —— 留着它就还是「自己拥有滚动权」，
          // 既与外层形成双层滚动，又让内层 `scrollTop` 恒 0（守卫照样失效）。
          // 也**不带 `flex-1`**：外部模式下根没有确定高度，`flex-basis:0%` 会退化成
          // 依赖「auto 高度容器里百分比基准按 content 解析」这条细节 —— 没必要押注它。
          scrollTarget === undefined
            ? "scroll-thin min-h-0 flex-1 overflow-y-auto overscroll-contain"
            : "overflow-visible"
        )}
        style={{
          transform: `translateY(${pull}px)`,
          transition: startYRef.current === null ? "transform var(--dur-base) var(--ease-soft)" : "none",
        }}
      >
        {children}
      </div>

      {/* 刷新中：给屏幕阅读器播报 */}
      <span role="status" aria-live="polite" className="sr-only">
        {refreshing ? refreshingLabel : ""}
      </span>
    </div>
  );
};

PullToRefresh.displayName = "PullToRefresh";
