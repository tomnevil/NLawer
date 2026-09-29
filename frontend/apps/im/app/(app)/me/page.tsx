"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ChevronRight, LogOut, Moon, Sun } from "lucide-react";
import { authed } from "@nlaw/sdk";
import {
  AppSwitcher,
  Skeleton,
  buildAppLinks,
  displayName,
  roleLabel,
  useAuthGuard,
  useTheme,
} from "@nlaw/ui";

import { type CaseDetail, type Conversation, type Paged } from "../../../lib/imApi";

/* ============================================================================
 * 我的（客户端 · `/me`）
 * ----------------------------------------------------------------------------
 * 对齐概念图 08 屏 6 的结构：顶部深色用户区 + 三格数据 + 分组设置卡。
 * 但**去掉企业版内容**（08 稿这里是「智元科技 · 企业管理员」「企业知识库 1,284 份」，
 * 那是企业法务端的；本端是 C 端客户，没有企业知识库这个概念）。
 *
 * 三格数据各自取自真实端点，用 `page_size=1` 只为拿 `total`：
 *   GET /api/v1/cases?page_size=1          → 我的案件（CLIENT 已收窄）
 *   GET /api/v1/conversations?page_size=1  → 我的咨询（CLIENT 已收窄）
 *   GET /api/v1/notifications/unread-count → 未读通知
 * 任何一格拿不到就显示 `—`，**不显示 0** —— 0 是一个结论，`—` 才是「没测到」。
 * ========================================================================== */

const APPS = buildAppLinks({
  web: process.env.NEXT_PUBLIC_APP_WEB_URL,
  lawyer: process.env.NEXT_PUBLIC_APP_LAWYER_URL,
  admin: process.env.NEXT_PUBLIC_APP_ADMIN_URL,
  im: process.env.NEXT_PUBLIC_APP_IM_URL,
});

/** 三格数据里的一格。`null` 表示没取到，渲染成 `—`。 */
type Stat = number | null;

export default function ImMePage() {
  const { user, logout } = useAuthGuard("/login");
  const { theme, toggle: toggleTheme } = useTheme();

  const [caseCount, setCaseCount] = useState<Stat>(null);
  const [convCount, setConvCount] = useState<Stat>(null);
  const [unread, setUnread] = useState<Stat>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    // 三格互不依赖，且**任何一格失败都不该让另外两格变空** ⇒ allSettled
    Promise.allSettled([
      authed<Paged<CaseDetail>>("/api/v1/cases?page=1&page_size=1"),
      authed<Paged<Conversation>>("/api/v1/conversations?page=1&page_size=1"),
      authed<{ total: number }>("/api/v1/notifications/unread-count"),
    ]).then(([c, cv, u]) => {
      if (!alive) return;
      setCaseCount(c.status === "fulfilled" ? (c.value.total ?? 0) : null);
      setConvCount(cv.status === "fulfilled" ? (cv.value.total ?? 0) : null);
      setUnread(u.status === "fulfilled" ? (u.value.total ?? 0) : null);
      setLoading(false);
    });
    return () => {
      alive = false;
    };
  }, []);

  const stats: { key: string; label: string; value: Stat; href?: string }[] = [
    { key: "cases", label: "我的案件", value: caseCount, href: "/cases" },
    { key: "convs", label: "我的咨询", value: convCount, href: "/chat" },
    // 未读没有落地页（客户端没有通知页）⇒ 不给 href，避免死链
    { key: "unread", label: "未读通知", value: unread },
  ];

  return (
    <div className="scroll-thin h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-[560px] px-4 pb-6 lg:max-w-[720px] lg:px-6">
        {/* ─────────────────────── 用户区 ─────────────────────── */}
        <section className="mt-4 rounded-r4 bg-gradient-to-br from-brand-700 via-brand-800 to-brand-950 p-5 text-white">
          <div className="flex items-center gap-3.5">
            <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-white/15 text-h4 font-semibold">
              {(user?.full_name ?? user?.username ?? "?").slice(0, 1)}
            </span>
            <div className="min-w-0">
              <p className="truncate text-h4">{user ? displayName(user) : "—"}</p>
              <p className="mt-0.5 truncate text-caption text-brand-200">
                {roleLabel(user?.role) || "客户端"}
              </p>
            </div>
          </div>
        </section>

        {/* ─────────────────────── 三格数据 ─────────────────────── */}
        <section className="mt-3 grid grid-cols-3 overflow-hidden rounded-r3 border border-line bg-surface">
          {stats.map((s, i) => {
            const body = (
              <>
                <span className="block text-caption text-ink-500">{s.label}</span>
                {loading ? (
                  <Skeleton className="mx-auto mt-1.5 h-5 w-8" />
                ) : (
                  <span className="num mt-1 block text-h4 text-ink-900">
                    {s.value === null ? "—" : s.value}
                  </span>
                )}
              </>
            );
            const cls =
              "flex min-h-tap flex-col items-center justify-center px-2 py-3.5 text-center " +
              (i > 0 ? "border-l border-line " : "") +
              (s.href ? "transition-colors duration-fast hover:bg-surface-hover" : "");
            return s.href ? (
              <Link key={s.key} href={s.href} className={cls}>
                {body}
              </Link>
            ) : (
              <div key={s.key} className={cls}>
                {body}
              </div>
            );
          })}
        </section>

        {/* ─────────────────────── 设置 ─────────────────────── */}
        <h2 className="mt-5 px-0.5 text-caption font-medium uppercase tracking-wider text-ink-400">
          设置
        </h2>

        <div className="mt-2 overflow-hidden rounded-r3 border border-line bg-surface">
          <button
            type="button"
            onClick={toggleTheme}
            className="flex min-h-tap w-full items-center gap-3 border-b border-line px-4 py-3.5 text-left transition-colors duration-fast hover:bg-surface-hover"
          >
            {theme === "dark" ? (
              <Sun className="h-4 w-4 shrink-0 text-ink-500" />
            ) : (
              <Moon className="h-4 w-4 shrink-0 text-ink-500" />
            )}
            <span className="flex-1 text-body-sm text-ink-800">外观</span>
            <span className="text-caption text-ink-400">
              {theme === "dark" ? "深色" : "浅色"}
            </span>
            <ChevronRight className="h-4 w-4 shrink-0 text-ink-300" />
          </button>

          {/* 应用切换器：四端是独立进程，切换必须整页跳转（见 apps.ts 注释）。
              im 不套 AppShell，所以这里用独立版补上出口，保证用户不会被困在本端。 */}
          <div className="px-4 py-3.5">
            <p className="mb-2 text-caption text-ink-500">切换到其他端</p>
            <AppSwitcher
              appId="im"
              apps={APPS}
              onChange={(app) => window.location.assign(app.href)}
            />
          </div>
        </div>

        <button
          type="button"
          onClick={logout}
          className="mt-3 flex min-h-tap w-full items-center justify-center gap-2 rounded-r3 border border-line bg-surface text-body-sm text-ink-600 transition-colors duration-fast hover:bg-surface-hover"
        >
          <LogOut className="h-4 w-4" />
          退出登录
        </button>

        <p className="mt-5 text-center text-caption leading-relaxed text-ink-400">
          律小智 · AI 生成内容仅供参考
          <br />
          不构成正式法律意见
        </p>
      </div>
    </div>
  );
}
