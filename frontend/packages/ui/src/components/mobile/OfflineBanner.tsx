"use client";

import React from "react";
import { cn } from "../../lib/cn";

/** 订阅浏览器在线状态。SSR 期间一律返回 true，避免首屏闪现离线条。 */
export function useOnlineStatus(): boolean {
  const [online, setOnline] = React.useState(true);

  React.useEffect(() => {
    setOnline(navigator.onLine);
    const goOnline = () => setOnline(true);
    const goOffline = () => setOnline(false);
    window.addEventListener("online", goOnline);
    window.addEventListener("offline", goOffline);
    return () => {
      window.removeEventListener("online", goOnline);
      window.removeEventListener("offline", goOffline);
    };
  }, []);

  return online;
}

export interface OfflineBannerProps {
  /** 待同步条数；>0 时在线态也会提示 */
  pendingCount?: number;
  /** 点击「立即同步」 */
  onSync?: () => void;
  syncing?: boolean;
  /** 吸附在 TabBar 之上（移动端默认位置） */
  aboveTabBar?: boolean;
  className?: string;
}

/** 恢复在线后的「已恢复连接」提示停留时长 */
const RECOVERED_DURATION = 2500;

/**
 * 离线状态条。
 *
 * 移动端办案经常出现在电梯、地库、法院走廊等无网环境。这一条横幅的
 * 职责是让用户确信三件事：**知道现在没网、知道数据没丢、知道什么时候会同步**。
 * 因此文案一律给出确定信息，不使用「网络异常」这类模糊措辞。
 *
 * 恢复在线后短暂显示「已恢复连接」再自动消失，给一个明确的闭环信号。
 */
export const OfflineBanner: React.FC<OfflineBannerProps> = ({
  pendingCount = 0,
  onSync,
  syncing = false,
  aboveTabBar = true,
  className,
}) => {
  const online = useOnlineStatus();
  const [showRecovered, setShowRecovered] = React.useState(false);
  const wasOffline = React.useRef(false);

  React.useEffect(() => {
    if (!online) {
      wasOffline.current = true;
      setShowRecovered(false);
      return;
    }
    if (wasOffline.current) {
      wasOffline.current = false;
      setShowRecovered(true);
      const timer = setTimeout(() => setShowRecovered(false), RECOVERED_DURATION);
      return () => clearTimeout(timer);
    }
  }, [online]);

  const hasPending = pendingCount > 0;

  // 在线 + 无待同步 + 无恢复提示 → 不占任何空间
  if (online && !hasPending && !showRecovered) return null;

  const tone = !online
    ? {
        wrap: "border-pending-500/30 bg-pending-500/10 text-pending-700",
        dot: "bg-pending-500",
        text: "当前离线，操作会保存在本机，恢复后自动同步",
      }
    : hasPending
      ? {
          wrap: "border-info-500/30 bg-info-500/10 text-info-700",
          dot: "bg-info-500",
          text: `已恢复连接，${pendingCount} 条记录待同步`,
        }
      : {
          wrap: "border-verified-500/30 bg-verified-500/10 text-verified-700",
          dot: "bg-verified-500",
          text: "已恢复连接",
        };

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "flex items-center gap-2 border-b px-4 py-2 text-body-sm",
        tone.wrap,
        aboveTabBar && "sticky top-0 z-topbar",
        className
      )}
      // 横屏时左右安全区为 40px 上下，`px-4` 不够，文案会被刘海切掉。
      // 用内联样式覆盖，调用方传 `className` 加 padding 时也压不住它——
      // 这条提示的全部意义就是「能被读到」，位置比样式优先级更重要。
      style={{
        paddingLeft: "calc(1rem + var(--safe-left))",
        paddingRight: "calc(1rem + var(--safe-right))",
      }}
    >
      <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", tone.dot)} aria-hidden />
      <span className="min-w-0 flex-1">{tone.text}</span>

      {online && hasPending && onSync && (
        <button
          type="button"
          onClick={onSync}
          disabled={syncing}
          className={cn(
            // 注意：Tailwind v3 不支持在 currentColor 上叠加透明度修饰符，
            // 因此这里用下划线而非半透明边框来做视觉区分。
            "min-h-tap shrink-0 rounded-r2 px-2 text-label font-medium underline underline-offset-2",
            "transition-opacity duration-fast hover:opacity-80 disabled:no-underline disabled:opacity-50"
          )}
        >
          {syncing ? "同步中…" : "立即同步"}
        </button>
      )}
    </div>
  );
};

OfflineBanner.displayName = "OfflineBanner";
