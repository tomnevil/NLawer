"use client";

import React, { createContext, useContext, useEffect, useRef, useState } from "react";
import { cn } from "../lib/cn";

type Theme = "light" | "dark";

interface ThemeContextValue {
  theme: Theme;
  toggle: () => void;
  setTheme: (t: Theme) => void;
}

const ThemeContext = createContext<ThemeContextValue>({
  theme: "light",
  toggle: () => {},
  setTheme: () => {},
});

export function useTheme() {
  return useContext(ThemeContext);
}

/** 在 <body> 顶部注入，避免主题切换白屏闪烁（首屏前即确定 class）。 */
export function ThemeScript() {
  const code = `(function(){try{var t=localStorage.getItem('nlaw-theme');var d=t==='dark'||(!t&&window.matchMedia('(prefers-color-scheme:dark)').matches);if(d){document.documentElement.classList.add('dark');}}catch(e){}})();`;
  return <script dangerouslySetInnerHTML={{ __html: code }} />;
}

/**
 * 主题切换按钮。v2 起颜色全部取自令牌，不再需要 `dark:` 硬编码；
 * 移动端自动上抬到 Tab Bar 之上，避免遮挡底部导航。
 */
function ThemeToggle({ theme, onToggle }: { theme: Theme; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label={theme === "dark" ? "切换到浅色模式" : "切换到深色模式"}
      className={cn(
        "fixed right-5 z-toast flex h-11 w-11 items-center justify-center rounded-full",
        "bottom-[calc(var(--tabbar-h)+var(--safe-bottom)+16px)] lg:bottom-[calc(20px+var(--safe-bottom))]",
        "border border-line bg-surface/90 text-link shadow-s2 backdrop-blur",
        "transition-colors duration-fast ease-out hover:border-brand-400 hover:shadow-s3"
      )}
    >
      {theme === "dark" ? (
        <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"
          />
        </svg>
      ) : (
        <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"
          />
        </svg>
      )}
    </button>
  );
}

export interface ThemeProviderProps {
  children: React.ReactNode;
  /**
   * 是否渲染右下角悬浮切换按钮，默认 `true`。
   *
   * 接入 `AppShell` 的应用应传 `false`——顶栏已经有切换入口，
   * 再挂一个悬浮球就是两个控件干同一件事，而且悬浮球会压住内容。
   * 保持默认 `true` 是为了让未接入骨架的页面（如组件预览页）仍然可切主题。
   */
  showFloatingToggle?: boolean;
}

export function ThemeProvider({ children, showFloatingToggle = true }: ThemeProviderProps) {
  const [theme, setThemeState] = useState<Theme>("light");

  /**
   * 🚨 **首帧绝不回写 `<html>` 的 class**（缺陷 #40：深色模式首屏闪白）。
   *
   * 因果链：`ThemeScript` 已在首屏前把 `.dark` 放到 `<html>` 上 ⇒ 此时 **DOM 是唯一可信来源**；
   * 而本组件的初始 state 恒为 `"light"`——SSR 与客户端首帧必须一致，**不能**在这里读 `document`，
   * 否则水合不匹配。若挂载时就拿这个 state 去 `classList.toggle("dark", false)`，会把刚加上的
   * `.dark` **摘掉**，等 state 与 DOM 对齐后再补回来 ⇒ 用户看到「深→浅→深」闪白。
   * 探针 observer 序列（`evidence/verify_dark_mode.py` D0）：`['dark','','dark']`。
   *
   * 所以：**只有用户显式切换**才回写 DOM 与 `localStorage`。class 只会被「用户意图」改动。
   */
  const userChanged = useRef(false);

  // 挂载后与 DOM 对齐：只同步 state，**不碰 class**。
  useEffect(() => {
    setThemeState(document.documentElement.classList.contains("dark") ? "dark" : "light");
  }, []);

  // 用户切换后才回写 —— 保证 class 不再出现「摘除又加回」。
  useEffect(() => {
    if (!userChanged.current) return;
    document.documentElement.classList.toggle("dark", theme === "dark");
    try {
      localStorage.setItem("nlaw-theme", theme);
    } catch {
      /* 忽略隐私模式写入失败 */
    }
  }, [theme]);

  const setTheme = (t: Theme) => {
    userChanged.current = true;
    setThemeState(t);
  };
  const toggle = () => setTheme(theme === "dark" ? "light" : "dark");

  return (
    <ThemeContext.Provider value={{ theme, toggle, setTheme }}>
      {children}
      {showFloatingToggle && <ThemeToggle theme={theme} onToggle={toggle} />}
    </ThemeContext.Provider>
  );
}
