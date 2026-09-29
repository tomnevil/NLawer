import type { Metadata, Viewport } from "next";
import "./globals.css";
import { ThemeProvider, ThemeScript, ToastProvider } from "@nlaw/ui";

export const metadata: Metadata = {
  title: "律所运营后台 · 律小智",
  description: "律小智 AI 法务助手",
};

/**
 * `viewportFit: "cover"` 是安全区体系的前置开关（详见 `apps/web/app/layout.tsx`）。
 * 后台以桌面为主，但移动端仍是只读可用视图，顶栏与抽屉同样依赖它。
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
        {/* 投诉举报 / 任务中心 / 内容超限网关(ContentTooLargeGate) 都用 useToast()，
            缺 Provider 会直接抛 "useToast must be used within a ToastProvider" 导致整页崩溃。
            ToastProvider 挂最外层，与 web / lawyer 一致。 */}
        <ThemeProvider showFloatingToggle={false}>
          <ToastProvider>{children}</ToastProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
