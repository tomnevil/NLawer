import type { Metadata, Viewport } from "next";
import "./globals.css";
import { ThemeProvider, ThemeScript, ToastProvider } from "@nlaw/ui";

export const metadata: Metadata = {
  title: "律师工作台 · 律小智",
  description: "律小智 AI 法务助手",
};

/**
 * `viewportFit: "cover"` 是整个安全区体系的**前置开关**，不是可选优化。
 * 不声明它时 iOS Safari 会把 `env(safe-area-inset-*)` 一律解析为 `0`，
 * 于是 AppShell / TabBar / MobileActionBar / BottomSheet 里所有
 * `var(--safe-top)`、`var(--safe-bottom)` 全部失效，刘海与 Home Indicator
 * 会直接压住顶栏和底部操作条。详见 `apps/web/app/layout.tsx` 的完整说明。
 *
 * 律师端是四端里移动需求最强的一个（派单 / 案件阅读 / 证据拍照 / 复核审批），
 * 这条开关缺了等于移动端规范第 08 节的安全区一节整体不生效。
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
        {/* ToastProvider 挂在最外层：案件详情页的写操作（提交复核 / 出结论 / 归档 /
            导出开庭材料包）需要统一的成功与失败反馈。`useToast()` 在无 Provider
            时会直接抛错，因此只要有用例就必须挂在这里，而不是各页自己包一层。 */}
        <ThemeProvider showFloatingToggle={false}>
          <ToastProvider>{children}</ToastProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
