"use client";

import React, { useEffect, useRef, useState } from "react";
import { cn } from "../lib/cn";
import type { AppLink } from "../lib/apps";
import { AppShellLogoMark } from "./AppShell";

export interface AppSwitcherProps {
  /** 当前应用 id */
  appId: string;
  apps: AppLink[];
  /** 切换目标。四端是独立进程，调用方必须做整页跳转而非客户端路由 */
  onChange: (app: AppLink) => void;
  /**
   * `ink`     —— 墨底（AppShell 侧栏、IM 左栏头部）
   * `surface` —— 浅底（内容区内的紧凑变体）
   */
  variant?: "ink" | "surface";
  className?: string;
}

const Svg: React.FC<React.SVGProps<SVGSVGElement>> = (props) => (
  <svg
    fill="none"
    viewBox="0 0 24 24"
    stroke="currentColor"
    strokeWidth={1.8}
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
    {...props}
  />
);

/** 点击外部 / Esc 关闭的下拉。与 AppShell 内部实现同源，此处独立以免跨组件耦合。 */
function usePopover<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent | TouchEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("touchstart", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("touchstart", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return { ref, open, setOpen };
}

/**
 * 应用切换器（独立版）。
 *
 * `AppShell` 自带一个切换器，但它绑定在 240px 墨色侧栏里。IM 是**满屏三栏**
 * 布局——导航就是会话列表，没有 AppShell 侧栏，可仍然需要「切到律师端 /
 * 运营后台」的入口，否则用户在 IM 里就出不去了。本组件把这一个入口单独
 * 拿出来，视觉语言与侧栏版完全一致（金线描边标记 + 应用名 + 副标题 + 折角）。
 *
 * 与侧栏版的关键差异：`ink` 变体下应用名用**墨色文本**而非白色，因为它
 * 挂在白色左栏上而不是深色侧栏上。直接复用侧栏版会得到「白字白底」。
 */
export const AppSwitcher: React.FC<AppSwitcherProps> = ({
  appId,
  apps,
  onChange,
  variant = "ink",
  className,
}) => {
  const { ref, open, setOpen } = usePopover<HTMLDivElement>();
  const current = apps.find((a) => a.id === appId);
  const canSwitch = apps.length > 1;

  return (
    <div ref={ref} className={cn("relative", className)}>
      <button
        type="button"
        onClick={() => canSwitch && setOpen((v) => !v)}
        aria-haspopup={canSwitch ? "menu" : undefined}
        aria-expanded={canSwitch ? open : undefined}
        className={cn(
          "flex w-full items-center gap-2.5 rounded-r2 px-2 py-1.5 text-left",
          "transition-colors duration-fast ease-out",
          canSwitch && "hover:bg-surface-hover",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30"
        )}
      >
        <AppShellLogoMark />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-body-sm font-semibold text-ink-900">
            {current?.label ?? "律小智"}
          </span>
          <span className="block truncate text-caption text-ink-500">
            {current?.description ?? "AI 法律助手"}
          </span>
        </span>
        {canSwitch && (
          <Svg
            className={cn(
              "h-4 w-4 shrink-0 text-ink-400 transition-transform duration-fast",
              open && "rotate-180"
            )}
          >
            <path d="m6 9 6 6 6-6" />
          </Svg>
        )}
      </button>

      {open && canSwitch && (
        <div
          role="menu"
          className={cn(
            "absolute left-0 right-0 top-[calc(100%+6px)] z-drawer animate-fade-in",
            "overflow-hidden rounded-r3 border border-line bg-surface shadow-s3"
          )}
        >
          {apps.map((app) => {
            const isCurrent = app.id === appId;
            return (
              <button
                key={app.id}
                type="button"
                role="menuitem"
                onClick={() => {
                  setOpen(false);
                  if (!isCurrent) onChange(app);
                }}
                aria-current={isCurrent ? "true" : undefined}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-2.5 text-left transition-colors duration-fast",
                  isCurrent ? "bg-brand-500/10" : "hover:bg-surface-hover"
                )}
              >
                <span className="min-w-0 flex-1">
                  <span
                    className={cn(
                      "block truncate text-body-sm font-medium",
                      isCurrent ? "text-link" : "text-ink-800"
                    )}
                  >
                    {app.label}
                  </span>
                  {app.description && (
                    <span className="block truncate text-caption text-ink-500">{app.description}</span>
                  )}
                </span>
                {isCurrent && <span className="shrink-0 text-gold-500">●</span>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};

AppSwitcher.displayName = "AppSwitcher";
