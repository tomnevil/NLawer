import type { Metadata, Viewport } from "next";
import "./globals.css";
import { ThemeProvider, ThemeScript, ToastProvider } from "@nlaw/ui";

export const metadata: Metadata = {
  title: "智能法律咨询 · 律小智",
  description: "律小智 AI 法务助手",
};

/**
 * `viewportFit: "cover"` 是安全区体系的前置开关（详见 `apps/web/app/layout.tsx`）。
 * IM 是四端里**移动优先**最彻底的一个（规范第 08 节：以 375px 为设计基准起草），
 * 这条开关缺了，输入框会被 Home Indicator 压住。
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
        {/* IM 端也会用 useToast()（如会话发送失败反馈、文件超限提示）。
            缺 Provider 会抛 "useToast must be used within a ToastProvider"。
            挂最外层，与 web / lawyer / admin 一致。 */}
        <ThemeProvider showFloatingToggle={false}>
          <ToastProvider>{children}</ToastProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
