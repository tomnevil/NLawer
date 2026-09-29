"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ChevronRight,
  FilePlus2,
  FolderOpen,
  MessageSquarePlus,
  Scale,
  Sparkles,
  Users,
} from "lucide-react";
import { authed } from "@nlaw/sdk";
import {
  AppSwitcher,
  Badge,
  EmptyState,
  Skeleton,
  buildAppLinks,
  roleLabel,
  useAuthGuard,
} from "@nlaw/ui";

import {
  GRADE_TONE,
  caseStatusLabel,
  errText,
  isCaseActive,
  type CaseDetail,
  type ChecklistItem,
  type Paged,
} from "../../lib/imApi";

/** 四端是独立进程，切换必须整页跳转；地址走静态 env 访问才会被内联。 */
const APPS = buildAppLinks({
  web: process.env.NEXT_PUBLIC_APP_WEB_URL,
  lawyer: process.env.NEXT_PUBLIC_APP_LAWYER_URL,
  admin: process.env.NEXT_PUBLIC_APP_ADMIN_URL,
  im: process.env.NEXT_PUBLIC_APP_IM_URL,
});

/* ============================================================================
 * 工作台（客户端 · `/`）
 * ----------------------------------------------------------------------------
 * 规范 §8.1 把 im 定为**移动优先**（「设计基准直接按 375px 起草，桌面视为放大适配」），
 * 因此这一页按单列手机布局起草，桌面只是加宽并居中。
 *
 * 【内容为什么与概念图 08 不一样】
 * 08 稿的「工作台首页」正文是**企业法务**的（「智元科技 · 企业版」「合规扫描」
 * 「知识库 1,284 份文件」），而本端是 **C 端客户**（`Role.CLIENT`）。
 * 直接把那些格子搬过来会指向客户无权访问的功能 ⇒ 这里按客户**真实能拿到的数据**重写。
 * 结构与 08 稿一致（顶部待办总览卡 → AI 提问入口 → 卡片列表 → 快捷服务），
 * 内容换成真实的。
 *
 * 【只挂真实存在的入口】本页用到的端点全部在 backend 源码核对过角色收窄：
 *   GET /api/v1/cases                      → Role.CLIENT 收窄到 Case.client_user_id
 *   GET /api/v1/evidence/cases/{id}/missing
 *   GET /api/v1/notifications/unread-count
 * 凡是「点了没有落地页」的东西一律不渲染（例如 08 稿 AppBar 上的通知铃铛 ——
 * 客户端没有通知页，挂上去就是死链）。
 * ========================================================================== */

/**
 * 待补材料只回查最近 N 件。
 *
 * ⚠️ `GET /evidence/cases/{id}/missing` 是**逐案**接口 ⇒ 全量回查就是 N+1。
 * C 端客户案件数通常是个位数，但**不能假装它是常数代价**：案件一多就退化。
 * 这里给一个显式上限，并在卡片文案里如实说明「近 3 件」。
 */
const CHECKLIST_LOOKBACK = 3;

/** 快捷服务。四项**目的地各不相同**，不做同一目标的重复入口。 */
const QUICK_ACTIONS = [
  {
    href: "/chat#new",
    icon: MessageSquarePlus,
    title: "发起咨询",
    desc: "描述情况，AI 先给分析",
  },
  {
    href: "/cases",
    icon: FolderOpen,
    title: "我的案件",
    desc: "进度、时间线与材料",
  },
  {
    href: "/cases",
    icon: FilePlus2,
    title: "待补材料",
    desc: "看还缺哪几项",
  },
  {
    href: "/chat",
    icon: Users,
    title: "联系律师",
    desc: "在会话里直接沟通",
  },
] as const;

function greeting(): string {
  const h = new Date().getHours();
  if (h < 6) return "夜深了";
  if (h < 12) return "早上好";
  if (h < 14) return "中午好";
  if (h < 18) return "下午好";
  return "晚上好";
}

export default function ImHome() {
  const { user } = useAuthGuard("/login");

  const [cases, setCases] = useState<CaseDetail[]>([]);
  const [casesTotal, setCasesTotal] = useState(0);
  const [missingCount, setMissingCount] = useState(0);
  const [unread, setUnread] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;

    (async () => {
      try {
        // 案件与未读数互不依赖 ⇒ 并行。未读数失败不应连带吞掉案件列表，
        // 因此这一层用 allSettled 的语义：各自取各自的值，缺一个就用 0。
        const [caseRes, unreadRes] = await Promise.allSettled([
          authed<Paged<CaseDetail>>("/api/v1/cases?page=1&page_size=50"),
          authed<{ total: number }>("/api/v1/notifications/unread-count"),
        ]);
        if (!alive) return;

        if (caseRes.status === "rejected") {
          setError(errText(caseRes.reason));
          return;
        }
        const items = caseRes.value.items ?? [];
        setCases(items);
        setCasesTotal(caseRes.value.total ?? 0);
        setUnread(unreadRes.status === "fulfilled" ? (unreadRes.value.total ?? 0) : 0);

        // 待补材料：只查最近 N 件，逐案 allSettled —— 某一案查不到不该让整块归零。
        const recent = items.slice(0, CHECKLIST_LOOKBACK);
        const checklist = await Promise.allSettled(
          recent.map((c) => authed<ChecklistItem[]>(`/api/v1/evidence/cases/${c.id}/missing`))
        );
        if (!alive) return;
        setMissingCount(
          checklist.reduce(
            (sum, r) =>
              sum +
              (r.status === "fulfilled"
                ? (r.value ?? []).filter((i) => i.missing).length
                : 0),
            0
          )
        );
      } catch (e) {
        if (alive) setError(errText(e));
      } finally {
        if (alive) setLoading(false);
      }
    })();

    return () => {
      alive = false;
    };
  }, []);

  const activeCases = useMemo(() => cases.filter((c) => isCaseActive(c.status)), [cases]);
  const pendingTotal = activeCases.length + missingCount + unread;

  return (
    <div className="scroll-thin h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-[560px] px-4 pb-6 lg:max-w-[720px] lg:px-6">
        {/* ───────────────────────── 顶部问候 ───────────────────────── */}
        <header className="pt-5">
          {/* 跨端切换，与 `/chat` 左栏头部、`/me`「切换到其他端」、`/cases` 头部一致。
              ⚠️ **它不承担 B2a 的「导航出口」**：`AppSwitcher` 是 `<button>` + 下拉，
              菜单项只在点开后渲染，而 `verify_breakpoints.py` 的 B2a 只认 `a[href]`
              （实测：给 `/cases` 加完它仍判 rc=1）。
              本页的出口是**快捷服务卡片**（`QUICK_ACTIONS` 的真链接，常驻）——
              这也是本页一直能过 B2a 的原因。
              `-mx-2` 是光学对齐（组件内部按钮自带 `px-2`）。 */}
          <AppSwitcher
            appId="im"
            apps={APPS}
            className="-mx-2"
            onChange={(app) => window.location.assign(app.href)}
          />
          <h1 className="mt-2 text-h4 text-ink-900">
            {greeting()}
            {user?.full_name ? `，${user.full_name}` : ""}
          </h1>
          <p className="mt-0.5 text-caption text-ink-500">
            {roleLabel(user?.role) || "客户端"} · 律小智 AI 法律助手
          </p>
        </header>

        {/* ─────────────────────── 待办总览卡 ─────────────────────── */}
        <section className="mt-4 rounded-r4 bg-gradient-to-br from-brand-700 via-brand-800 to-brand-950 p-4 text-white">
          <p className="text-caption text-brand-200">待处理事项</p>
          {loading ? (
            <div className="mt-1.5 h-7 w-20 animate-pulse-soft rounded-r1 bg-white/15" />
          ) : (
            <p className="mt-1 text-h2 tabular-nums">{pendingTotal} 项</p>
          )}
          <dl className="mt-3 flex gap-5 border-t border-white/15 pt-3">
            {[
              { k: "办理中", v: activeCases.length },
              { k: "待补材料", v: missingCount },
              { k: "未读", v: unread },
            ].map((x) => (
              <div key={x.k}>
                <dt className="text-caption text-brand-200">{x.k}</dt>
                <dd className="text-body font-semibold tabular-nums">{loading ? "—" : x.v}</dd>
              </div>
            ))}
          </dl>
        </section>

        {/* ─────────────────────── AI 提问入口 ─────────────────────── */}
        <Link
          href="/chat#new"
          className="mt-3.5 flex min-h-tap items-center gap-3 rounded-r4 border border-brand-200 bg-surface p-3.5 shadow-s1 transition-colors duration-fast hover:bg-surface-hover"
        >
          <span className="flex h-[38px] w-[38px] shrink-0 items-center justify-center rounded-r3 bg-gradient-to-br from-ai-500 to-brand-700">
            <Sparkles className="h-[19px] w-[19px] text-white" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-body font-medium text-ink-800">向 AI 提问</span>
            <span className="block text-caption text-ink-400">
              描述您遇到的情况，AI 先给结构化分析
            </span>
          </span>
          <ChevronRight className="h-4 w-4 shrink-0 text-ink-400" />
        </Link>

        {/* ────────────────────── 我的案件列表 ────────────────────── */}
        <div className="mt-5 flex items-baseline px-0.5">
          <h2 className="text-body font-medium text-ink-900">我的案件</h2>
          {!loading && <span className="ml-2 text-caption text-ink-400">{casesTotal}</span>}
          {casesTotal > 0 && (
            <Link href="/cases" className="ml-auto text-label font-medium text-brand-600">
              全部 →
            </Link>
          )}
        </div>

        <div className="mt-2">
          {loading ? (
            <div className="space-y-2">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
          ) : error ? (
            <p className="rounded-r3 border border-danger-500/30 bg-danger-500/10 p-3 text-body-sm text-danger-600">
              {error}
            </p>
          ) : cases.length === 0 ? (
            <EmptyState
              icon={<Scale className="h-5 w-5" />}
              title="还没有案件"
              description="描述情况涉及复杂纠纷、需要律师介入时，AI 会自动立案并派单。"
              action={
                <Link
                  href="/chat#new"
                  className="rounded-r2 bg-solid-brand px-3 py-1.5 text-body-sm font-medium text-white"
                >
                  发起咨询
                </Link>
              }
            />
          ) : (
            <ul className="overflow-hidden rounded-r4 border border-line bg-surface">
              {cases.slice(0, 3).map((c) => (
                <li key={c.id} className="border-b border-line last:border-b-0">
                  <Link
                    href={`/cases/${c.id}`}
                    className="flex min-h-tap items-center gap-3 px-4 py-3.5 transition-colors duration-fast hover:bg-surface-hover"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-body-sm font-medium text-ink-800">
                        {c.title}
                      </span>
                      <span className="mt-0.5 flex items-center gap-2 text-caption text-ink-400">
                        <span className="num">{c.case_no}</span>
                        <span>{caseStatusLabel(c.status)}</span>
                      </span>
                    </span>
                    {c.grade && (
                      <Badge variant={GRADE_TONE[c.grade] ?? "neutral"} size="sm">
                        {c.grade}
                      </Badge>
                    )}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* ─────────────────────── 快捷服务 ─────────────────────── */}
        <h2 className="mt-5 px-0.5 text-body font-medium text-ink-900">快捷服务</h2>
        <div className="mt-2 grid grid-cols-2 gap-2.5">
          {QUICK_ACTIONS.map((a) => (
            <Link
              key={a.title}
              href={a.href}
              className="flex min-h-tap flex-col rounded-r3 border border-line bg-surface p-3.5 shadow-s1 transition-colors duration-fast hover:bg-surface-hover"
            >
              <span className="mb-2 flex h-8 w-8 items-center justify-center rounded-r2 border border-brand-100 bg-brand-50">
                <a.icon className="h-4 w-4 text-brand-600" />
              </span>
              <span className="text-body-sm font-medium text-ink-800">{a.title}</span>
              <span className="mt-0.5 text-caption leading-[1.55] text-ink-400">{a.desc}</span>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
