import type { Metadata, Viewport } from "next";
import "./globals.css";
import { ThemeProvider, ThemeScript, ToastProvider } from "@nlaw/ui";

export const metadata: Metadata = {
  title: "法务助手 · 律小智",
  description: "律小智 AI 法务助手",
};

/**
 * `viewportFit: "cover"` 是整个安全区体系的**前置开关**，不是可选优化。
 *
 * 不声明它时 iOS Safari 会把 `env(safe-area-inset-*)` 一律解析为 `0`——
 * 于是 `tokens.css` 里的 `--safe-top` / `--safe-bottom` 全为 0，
 * AppShell 顶栏、TabBar、MobileActionBar、BottomSheet、Drawer 里所有
 * 安全区 padding 都成了**死代码**，刘海与 Home Indicator 会直接压住
 * 顶栏和底部导航。声明 `cover` 后浏览器才把布局视口铺满整块屏幕，
 * 再由我们自己的 padding 把内容让回安全区。
 *
 * 注意这是**全局生效**的开关：开启后凡贴着视口边缘的元素都必须自带
 * 安全区处理，否则会立刻暴露为可见缺陷（IM 端不套 AppShell，
 * 已在 `apps/im/app/(app)/layout.tsx` 里自行兜住四边）。
 */
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <ThemeScript />
        {/* 顶栏已有主题切换入口，关掉悬浮球避免两个控件干同一件事 */}
        <ThemeProvider showFloatingToggle={false}>
          {/* 文书 / 合规 / 计费 / 知识库四页都有写操作，需要统一反馈；
              `useToast()` 在无 Provider 时会直接抛错 */}
          <ToastProvider>{children}</ToastProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
