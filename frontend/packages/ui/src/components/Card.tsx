"use client";

import React from "react";
import { cn } from "../lib/cn";

export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: "default" | "glass" | "elevated";
  padding?: "none" | "sm" | "md" | "lg";
  /** hover 仅做边框与阴影的轻微变化，不再有位移（法律产品要稳） */
  hover?: boolean;
}

const variantStyles: Record<string, string> = {
  // 常规内容卡片：1px 边框优先于阴影
  default: "bg-surface border border-line shadow-s1",
  // 墨色玻璃：用于侧栏 / 深色区 / 驾驶舱深色模块（恒为深色，不随主题翻转）
  glass: "bg-brand-950/90 backdrop-blur-xl border border-sidebar-border",
  elevated: "bg-surface border border-line shadow-s2",
};

const paddingStyles: Record<string, string> = {
  none: "p-0",
  sm: "p-4",
  md: "p-5",
  lg: "p-6",
};

export const Card = React.forwardRef<HTMLDivElement, CardProps>(
  ({ className, variant = "default", padding = "md", hover = false, children, ...props }, ref) => {
    return (
      <div
        ref={ref}
        className={cn(
          "rounded-r3 transition-colors duration-base ease-out",
          variantStyles[variant],
          paddingStyles[padding],
          hover && "hover:border-brand-400/60 hover:shadow-s2",
          className
        )}
        {...props}
      >
        {children}
      </div>
    );
  }
);

Card.displayName = "Card";

// Card 子组件
export const CardHeader: React.FC<React.HTMLAttributes<HTMLDivElement>> = ({
  className,
  children,
  ...props
}) => (
  <div className={cn("border-b border-line pb-4", className)} {...props}>
    {children}
  </div>
);

export const CardTitle: React.FC<React.HTMLAttributes<HTMLHeadingElement>> = ({
  className,
  children,
  ...props
}) => (
  <h3 className={cn("text-h4 font-semibold text-ink-900", className)} {...props}>
    {children}
  </h3>
);

export const CardDescription: React.FC<React.HTMLAttributes<HTMLParagraphElement>> = ({
  className,
  children,
  ...props
}) => (
  <p className={cn("mt-1 text-body-sm text-ink-500", className)} {...props}>
    {children}
  </p>
);

export const CardContent: React.FC<React.HTMLAttributes<HTMLDivElement>> = ({
  className,
  children,
  ...props
}) => (
  <div className={cn("pt-4", className)} {...props}>
    {children}
  </div>
);

export const CardFooter: React.FC<React.HTMLAttributes<HTMLDivElement>> = ({
  className,
  children,
  ...props
}) => (
  <div className={cn("mt-4 border-t border-line pt-4", className)} {...props}>
    {children}
  </div>
);
