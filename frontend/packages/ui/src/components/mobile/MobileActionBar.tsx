"use client";

import React from "react";
import { cn } from "../../lib/cn";

export interface MobileActionBarProps {
  children: React.ReactNode;
  /**
   * 左侧说明文案（如「当前步骤：待复核确认」）。
   * 窄屏空间有限，正文会自动截断，因此**不要把关键信息只放在这里**。
   */
  hint?: React.ReactNode;
  /** 在流内保留等高占位，避免固定条遮挡正文。默认 true。 */
  reserveSpace?: boolean;
  className?: string;
}

/**
 * 移动端常驻底部操作条（设计规范第 08 节）。
 *
 * ## 为什么需要它
 *
 * 规范把律师端定为「5 项 Tab + 常驻底部操作条」。原因是律师在移动端的主线动作
 * （接单 → 办案 → 送审 → 出结论）都是**单一决定性动作**，而这些动作在桌面端
 * 位于右侧栏或页面顶部——在手机上需要先滚到顶部才能点到。
 * 底部操作条把「当前上下文里最重要的那一个动作」固定在拇指可达区。
 *
 * ## 位置计算
 *
 * 底边偏移取 `--actionbar-bottom`，即 `--tabbar-h + --safe-bottom`：
 * 正好叠在 Tab Bar 之上，且不会掉进 Home Indicator 区。
 * **不要**在这里写死 `bottom-14` 之类的值——Tab Bar 高度与安全区都是令牌，
 * 写死会在带 Home Indicator 的机型上被系统手势条盖住。
 *
 * ## 为什么自带占位
 *
 * 固定定位脱离文档流，正文末尾的内容会被永久盖住。这里在流内渲染一个等高占位，
 * 页面就**不需要知道**操作条的存在，也不需要各自去猜该加多少 padding。
 *
 * ⚠️ **「等高」是靠 `hairline-top` 保证的，不是靠 `border-t`**：
 * 本条的 border-box 高**恒等于** `--actionbar-h`，占位与条高是**同一个表达式**。
 * 若把分隔线改回 `border-t`，条会变成 `--actionbar-h + 1px`，占位就少 1px——
 * 在零呼吸容器里会真的裁掉正文最后一行。详见 `admin-gap-analysis.md` #17。
 *
 * ## 桌面端
 *
 * `lg:hidden` 整体隐藏：桌面端的同一批动作放在右侧栏常驻卡片里，
 * 底部再压一条会与侧栏形成两个「主操作区」，反而模糊层级。
 */
export const MobileActionBar: React.FC<MobileActionBarProps> = ({
  children,
  hint,
  reserveSpace = true,
  className,
}) => {
  return (
    <>
      {reserveSpace && (
        <div aria-hidden className="lg:hidden" style={{ height: "var(--actionbar-h)" }} />
      )}

      <div
        className={cn(
          "fixed inset-x-0 z-tabbar lg:hidden",
          // 半透明 + 模糊：正文从条下滚过时仍可感知内容在移动，不会显得界面被切断
          //
          // ⚠️ 分隔线**不能用 `border-t`**：border 会把 border-box 撑高 1px，
          // 而下面那个「等高占位」只留了 `--actionbar-h` ⇒ 占位与条高差 1px，
          // 本组件 docblock 里「在流内保留**等高**占位」这条契约会静默失效。
          // `hairline-top` 用背景渐变画线，**不占盒模型高度** ⇒ 由构造等高。
          // 见 `deliverables/ui-design/admin-gap-analysis.md` #17。
          "hairline-top bg-surface/95 backdrop-blur",
          className
        )}
        style={{ bottom: "var(--actionbar-bottom)" }}
      >
        <div
          className="flex items-center gap-2 px-3"
          style={{
            height: "var(--actionbar-h)",
            // 横屏刘海在左右两侧，safe-left/right 会变成 40px 上下，
            // 此时 `px-3`（12px）不够——最右侧的按钮会压进刘海区。
            // 加在内容层而不是条本身：条的底色仍通栏到屏幕边缘。
            paddingLeft: "calc(0.75rem + var(--safe-left))",
            paddingRight: "calc(0.75rem + var(--safe-right))",
          }}
        >
          {hint !== undefined && (
            <div className="min-w-0 flex-1 truncate text-body-sm text-ink-600">{hint}</div>
          )}
          <div className={cn("flex shrink-0 items-center gap-2", hint === undefined && "w-full justify-end")}>
            {children}
          </div>
        </div>
      </div>
    </>
  );
};

MobileActionBar.displayName = "MobileActionBar";
