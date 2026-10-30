"use client";

import React from "react";
import { cn } from "../lib/cn";
import type { ProvenanceState } from "../theme/colors";
import { ProvenanceBadge } from "./ProvenanceBadge";

export interface ProvenanceBlockProps {
  state: ProvenanceState;
  /** AI 生成可传模型名；律师已确认传确认人 */
  by?: string;
  at?: string;
  onOpenHistory?: () => void;
  /** 不渲染徽章，仅保留边框与底色（用于嵌套在已有徽章的容器内） */
  hideBadge?: boolean;
  /** 徽章右对齐（默认）或左对齐 */
  badgeAlign?: "left" | "right";
  /**
   * 根元素标签，默认 `"div"`。
   *
   * **契约：不传 = 与加本参数之前逐字相同**（与 `PullToRefresh.scrollTarget`
   * 的形态一致）。需要保留语义标签的调用方传 `"article"` / `"section"` ——
   * 例如 im 的咨询卡片、web 问答页的回答卡片，它们本来就是 `<article>`。
   *
   * 只开这三档，**不是** `keyof JSX.IntrinsicElements`：本组件承载的是
   * 「内容段」，能当内容段用的标签就这几个；放开全部会让人把它当万能容器。
   */
  as?: "div" | "article" | "section";
  children: React.ReactNode;
  className?: string;
}

/** 三态对应的装饰类，定义在 `styles.css`。 */
const stateClass: Record<ProvenanceState, string> = {
  ai: "provenance-ai",
  verified: "provenance-verified",
  pending: "provenance-pending",
};

/**
 * 责任边界内容段容器（规范第 05 节）。
 *
 * 三种视觉语言：
 *   AI 生成      → 紫色**虚线上边框** + 浅紫底
 *   律师已确认   → 绿色**实线左边条 3px** + 浅绿底
 *   待复核/风险  → 琥珀**左边条** + 浅琥珀底
 *
 * 装饰（边框与底色）来自 `styles.css`，内边距由使用方用 Tailwind 控制。
 * 导出 PDF / Word 时三态标记必须保留（合规要求）。
 */
export const ProvenanceBlock: React.FC<ProvenanceBlockProps> = ({
  state,
  by,
  at,
  onOpenHistory,
  hideBadge = false,
  badgeAlign = "right",
  as: Tag = "div",
  children,
  className,
}) => {
  return (
    <Tag className={cn("rounded-r2 p-4", stateClass[state], className)}>
      {!hideBadge && (
        <div
          className={cn(
            "mb-2.5 flex items-center gap-2",
            badgeAlign === "right" ? "justify-end" : "justify-start"
          )}
        >
          <ProvenanceBadge state={state} by={by} at={at} onOpenHistory={onOpenHistory} size="sm" />
        </div>
      )}
      <div className="text-body text-ink-800">{children}</div>
    </Tag>
  );
};

ProvenanceBlock.displayName = "ProvenanceBlock";
