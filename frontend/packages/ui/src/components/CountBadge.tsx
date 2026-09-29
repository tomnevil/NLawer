"use client";

import React from "react";
import { cn } from "../lib/cn";

export interface CountBadgeProps {
  /** 当前**字符**数（不是 token）。 */
  value: number;
  /**
   * 上限（**字符**）。应取自后端 413 错误体的 `max_length`，
   * 不要在前端另写一份常量——两处不一致就会「前端没预警却被拒」。
   */
  max: number;
  /** 软上限比例：达到即黄条预警。默认 0.9（裁定：90%）。 */
  warnRatio?: number;
  /** 隐藏「/ max」部分（输入框极窄时用）。 */
  hideMax?: boolean;
  className?: string;
}

/**
 * 实时字数计数徽标 `X / N`。
 *
 * ## 为什么按**字符**而不是 token 计（2026-09-23 裁定）
 *
 * 后端 `max_length` 校验的是**字符数**（pydantic 对 `str` 校验 `len()`）。
 * token 计数（中文 1 字 ≈ 1.5–2 token，英文 1 词 ≈ 1.3 token，**非线性**）会让
 * 前端预警与后端拒绝**错位** —— 表现为「前端还没提示就被 413」，
 * 正是本组件要消灭的「事后惩罚式拦截」。
 *
 * ## 软上限的设计意图
 *
 * 达 `warnRatio` 就**提前**给黄条 + 上传引导，而不是等真超限才报错：
 * 用户研究（瑞思）的共识是「无预警的事后拦截体验最差」。
 */
export const CountBadge: React.FC<CountBadgeProps> = ({
  value,
  max,
  warnRatio = 0.9,
  hideMax = false,
  className,
}) => {
  const ratio = max > 0 ? value / max : 0;
  const warn = ratio >= warnRatio;
  const over = ratio > 1;

  return (
    <span
      className={cn(
        "inline-flex items-center whitespace-nowrap rounded-r1 border px-1.5 py-0.5 text-caption font-medium tabular-nums transition-colors duration-fast",
        warn
          ? "bg-[rgb(var(--count-warn-bg))] text-[rgb(var(--count-warn-fg))] border-[rgb(var(--pending-300))]"
          : "bg-[rgb(var(--surface-subtle))] text-[rgb(var(--text-muted))] border-line",
        className
      )}
      // 超限必须读屏可感知：软上限用 polite，超限用 assertive
      aria-live={over ? "assertive" : "polite"}
      title={warn ? `已达上限的 ${Math.round(ratio * 100)}%，建议改用文件上传` : undefined}
    >
      {value}
      {!hideMax && <> / {max}</>}
    </span>
  );
};

CountBadge.displayName = "CountBadge";
