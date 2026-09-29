"use client";

import React from "react";
import { cn } from "../lib/cn";

export interface EmptyStateProps {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}

/** 空状态。颜色取自令牌，深色模式自动适配。 */
export const EmptyState: React.FC<EmptyStateProps> = ({ icon, title, description, action, className }) => {
  return (
    <div className={cn("flex flex-col items-center justify-center py-12", className)}>
      {icon && (
        <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-r3 border border-line bg-surface-subtle">
          <span className="text-ink-400">{icon}</span>
        </div>
      )}
      <h3 className="mb-2 text-h4 font-semibold text-ink-900">{title}</h3>
      {description && <p className="max-w-sm text-center text-body-sm text-ink-500">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
};

EmptyState.displayName = "EmptyState";
