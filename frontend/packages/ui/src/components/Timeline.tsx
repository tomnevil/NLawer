"use client";

import React from "react";
import { cn } from "../lib/cn";

export type TimelineStatus = "done" | "current" | "pending" | "rejected";

export interface TimelineItem {
  id: string;
  title: string;
  description?: string;
  /** 已格式化时间 */
  time?: string;
  /** 操作人 */
  actor?: string;
  status?: TimelineStatus;
  /** 自定义节点图标，覆盖默认样式 */
  icon?: React.ReactNode;
}

export interface TimelineProps {
  items: TimelineItem[];
  className?: string;
}

const NODE_STYLES: Record<TimelineStatus, { ring: string; dot: string; text: string }> = {
  done: { ring: "border-verified-500 bg-verified-500/15", dot: "bg-verified-500", text: "text-ink-800" },
  current: { ring: "border-brand-500 bg-brand-500/15", dot: "bg-brand-500", text: "text-ink-900" },
  pending: { ring: "border-ink-300 bg-surface", dot: "bg-ink-300", text: "text-ink-500" },
  rejected: { ring: "border-danger-500 bg-danger-500/15", dot: "bg-danger-500", text: "text-ink-800" },
};

/**
 * 时间线（复核流转、证据时间线）。
 *
 * 节点状态用「颜色 + 形状」双通道表达：实心点=已完成/当前，空心点=未开始，
 * 红色=已退回。连线用 1px 边框而非阴影，符合法律产品「稳定优于轻飘」的取向。
 */
export const Timeline: React.FC<TimelineProps> = ({ items, className }) => {
  if (items.length === 0) {
    return <p className={cn("py-6 text-center text-body-sm text-ink-500", className)}>暂无流转记录</p>;
  }

  return (
    <ol className={cn("relative", className)}>
      {items.map((item, i) => {
        const status = item.status ?? "pending";
        const s = NODE_STYLES[status];
        const isLast = i === items.length - 1;

        return (
          <li key={item.id} className="relative flex gap-3 pb-5 last:pb-0">
            {/* 连线：画在节点下方，最后一节不画 */}
            {!isLast && (
              <span
                aria-hidden
                className="absolute left-[7px] top-4 h-[calc(100%-8px)] w-px bg-line"
              />
            )}

            {/* 节点 */}
            <span
              aria-hidden
              className={cn(
                "relative z-10 mt-1 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border-2",
                s.ring
              )}
            >
              {(status === "done" || status === "current") && (
                <span className={cn("h-1.5 w-1.5 rounded-full", s.dot)} />
              )}
              {status === "rejected" && <span className={cn("h-1.5 w-1.5 rounded-full", s.dot)} />}
            </span>

            {item.icon && <span className="mt-0.5 shrink-0 text-ink-500">{item.icon}</span>}

            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className={cn("text-body-sm font-medium", s.text)}>{item.title}</span>
                {item.actor && <span className="text-caption text-ink-500">{item.actor}</span>}
                {item.time && <span className="num ml-auto text-caption text-ink-500">{item.time}</span>}
              </div>
              {item.description && (
                <p className="mt-1 text-body-sm text-ink-600">{item.description}</p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
};

Timeline.displayName = "Timeline";
