"use client";

import React from "react";
import { cn } from "../lib/cn";

export interface SpinnerProps extends Omit<React.ComponentPropsWithoutRef<"svg">, "color"> {
  size?: "sm" | "md" | "lg";
  color?: "primary" | "secondary" | "white";
  /**
   * 无障碍名称。
   *
   * - 字符串（默认 `"加载中"`）：渲染 `role="status" aria-label={label}` —— 读屏会播报。
   * - **`null`**：渲染 `aria-hidden`，即**纯装饰**。用在**父元素已经表达了忙碌语义**的地方
   *   （`Button` 的 `aria-busy`、紧邻「加载中…」「正在压缩…」文字、或按钮内已有文案），
   *   避免同一状态被播报两次。
   *
   * ⚠️ 这两个取值对应**收敛前各处的原始语义**（有的写了 `role="status"`、有的写了
   * `aria-hidden`、有的什么都没写）⇒ 收敛时**逐处照搬**，不要一律取默认值。
   */
  label?: string | null;
}

const sizeStyles: Record<string, string> = {
  sm: "h-4 w-4",
  md: "h-6 w-6",
  lg: "h-8 w-8",
};

const colorStyles: Record<string, string> = {
  primary: "text-brand-500",
  secondary: "text-verified-500",
  white: "text-white",
};

/**
 * 加载指示器 —— 全仓**唯一**表达「正在加载」的旋转元素（收敛于 #75）。
 *
 * 🚨 **不要用 Tailwind 内置的旋转类**（`animate-` + `spin`；已被 A4 禁止）：
 * 它把时长写死成 `1s linear infinite`、**不读 `--dur-*`** ⇒
 * `prefers-reduced-motion: reduce` 下**照转不停**。
 * 本组件走 `animate-spin-soft`（经 `--dur-spin` / `--spin-iter` 两个令牌），
 * reduce 下两者归零 ⇒ **停成静止的弧**：仍是可见的指示器，只是不再旋转
 * （「旋转」才是前庭反应的触发形态；指示器本身要保留，否则用户会以为界面卡死）。
 *
 * 收敛记录（2026-09-25）：全仓 **33 处**手搓旋转类已全部改为引用本组件
 * （25 处页面 + `packages/ui` 内部 7 处）⇒ 那个内置类全仓 **0 处**。
 * 裁定材料与实测：`deliverables/ui-design/motion-spin-audit.md`。
 *
 * ⚠️ **本文件的注释故意不把那个类名写全**（写作 `animate-` + `spin`）：
 * A4 是**按源码文本**扫的，**连注释一起扫** ⇒ 注释里写全名会把本文件自己判红。
 * 与坑 71 同型 —— 「写**关于转义**的文档，文档自己中了转义的坑」。
 */
export const Spinner: React.FC<SpinnerProps> = ({
  size = "md",
  color = "primary",
  label = "加载中",
  className,
  ...rest
}) => {
  return (
    <svg
      className={cn("animate-spin-soft", sizeStyles[size], colorStyles[color], className)}
      viewBox="0 0 24 24"
      fill="none"
      {...(label === null ? { "aria-hidden": true } : { role: "status", "aria-label": label })}
      {...rest}
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
      />
    </svg>
  );
};

Spinner.displayName = "Spinner";
