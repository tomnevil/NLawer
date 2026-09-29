"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Archive, ClipboardCheck, FolderOpen, Inbox, RefreshCw } from "lucide-react";
import { authed } from "@nlaw/sdk";
import { Badge, EmptyState, KpiCard, Skeleton, Spinner, cn, displayName, useSession } from "@nlaw/ui";

/* ============================================================================
 * 律师工作台（首页）
 * ----------------------------------------------------------------------------
 * 数据来源（4 个只读请求，`Promise.allSettled` 并发）：
 *   GET /api/v1/dispatches/pool?page_size=1            -> Page，取 total
 *   GET /api/v1/cases?page_size=1                      -> Page，取 total
 *   GET /api/v1/reviews?status=pending_confirm&…       -> Page，取 total
 *   GET /api/v1/cases?status=ARCHIVED&page_size=1      -> Page，取 total
 *
 * 全部只取 `total`（`page_size=1`），不为了凑数字把列表数据拉下来。
 * 注意 `total_is_lower_bound`：命中有界计数上限时显示「200+」，不谎报精确值。
 *
 * ⚠️ 重构前的问题：本页是接入 `AppShell` 之前写的「整屏深色渐变 hero」
 *     （`min-h-screen` + 靛蓝 950 -> 800 对角渐变）＋ 4 个渐变图标块。
 *     接入骨架后它被渲染在**内容区内部**，于是浅色外壳里嵌了一整屏深色块，
 *     层级关系完全错乱；渐变也是设计系统 v2 已废弃的手法。
 * ========================================================================== */

interface Paged {
  total: number;
  total_is_lower_bound?: boolean;
}

interface Count {
  value: number;
  lowerBound: boolean;
}

type CountKey = "pool" | "cases" | "reviews" | "archived";

const EMPTY: Count = { value: 0, lowerBound: false };

export default function LawyerHome() {
  const router = useRouter();
  const { user } = useSession();

  const [counts, setCounts] = useState<Record<CountKey, Count>>({
    pool: EMPTY,
    cases: EMPTY,
    reviews: EMPTY,
    archived: EMPTY,
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [pool, cases, reviews, archived] = await Promise.allSettled([
        authed<Paged>("/api/v1/dispatches/pool?page=1&page_size=1"),
        authed<Paged>("/api/v1/cases?page=1&page_size=1"),
        authed<Paged>("/api/v1/reviews?status=pending_confirm&page=1&page_size=1"),
        authed<Paged>("/api/v1/cases?status=ARCHIVED&page=1&page_size=1"),
      ]);

      const pick = (r: PromiseSettledResult<Paged>): Count =>
        r.status === "fulfilled"
          ? { value: r.value?.total ?? 0, lowerBound: Boolean(r.value?.total_is_lower_bound) }
          : EMPTY;

      setCounts({
        pool: pick(pool),
        cases: pick(cases),
        reviews: pick(reviews),
        archived: pick(archived),
      });

      // 四个全失败才算页面级错误；单边失败保留其余数字可用
      if ([pool, cases, reviews, archived].every((r) => r.status === "rejected")) {
        setError("无法加载工作台数据，请检查网络后重试。");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const fmt = (c: Count) => (c.lowerBound ? `${c.value}+` : String(c.value));

  const KPIS: {
    key: CountKey;
    label: string;
    unit: string;
    href: string;
    color: "pending" | "brand" | "ai" | "gold";
    icon: ReactNode;
  }[] = [
    {
      key: "pool",
      label: "待接派单",
      unit: "件",
      href: "/dispatches",
      color: "pending",
      icon: <Inbox className="h-4 w-4" />,
    },
    {
      key: "cases",
      label: "我的案件",
      unit: "件",
      href: "/cases",
      color: "brand",
      icon: <FolderOpen className="h-4 w-4" />,
    },
    {
      key: "reviews",
      label: "待我复核",
      unit: "条",
      href: "/reviews",
      color: "ai",
      icon: <ClipboardCheck className="h-4 w-4" />,
    },
    {
      key: "archived",
      label: "已归档卷宗",
      unit: "卷",
      href: "/archives",
      color: "gold",
      icon: <Archive className="h-4 w-4" />,
    },
  ];

  const ACTIONS: { href: string; title: string; desc: string; icon: ReactNode }[] = [
    {
      href: "/dispatches",
      title: "派单池",
      desc: "查看待接案件并抢单",
      icon: <Inbox className="h-4 w-4" />,
    },
    {
      href: "/cases",
      title: "我的案件",
      desc: "六段式分析 · 证据材料 · 复核流转",
      icon: <FolderOpen className="h-4 w-4" />,
    },
    {
      href: "/reviews",
      title: "复核队列",
      desc: "L1 自检 / L2 律师复核 / L3 终审",
      icon: <ClipboardCheck className="h-4 w-4" />,
    },
    {
      href: "/archives",
      title: "归档与卷宗",
      desc: "卷宗版本 · 开庭材料包导出",
      icon: <Archive className="h-4 w-4" />,
    },
  ];

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-h2 text-ink-900">
            {displayName(user) ? `${displayName(user)}，欢迎回来` : "律师工作台"}
          </h1>
          <p className="mt-1 text-body-sm text-ink-500">
            接单 → 办案 → 送审 → 归档，全流程留痕
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className={cn(
            "flex h-9 items-center gap-1.5 rounded-r2 border border-line px-3",
            "text-body-sm text-ink-600 transition-colors duration-fast hover:bg-surface-hover",
            "disabled:opacity-50"
          )}
        >
          {loading ? <Spinner size="sm" label={null} /> : <RefreshCw className="h-4 w-4" />}
          刷新
        </button>
      </header>

      {error && (
        <div className="rounded-r3 border border-danger-500/30 bg-danger-500/10 p-4">
          <p className="flex items-start gap-2 text-body-sm text-danger-600">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            {error}
          </p>
          <button
            type="button"
            onClick={() => void load()}
            className="mt-2 text-body-sm text-link hover:text-link-hover"
          >
            重试
          </button>
        </div>
      )}

      {/* ── 关键指标 ───────────────────────────────────────────── */}
      {loading ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {KPIS.map((k) => (
            /* KpiCard 本身是纯展示的 div，可点击性由外层 button 提供，
               这样键盘可达性与 aria 语义都由原生元素保证 */
            <button
              key={k.key}
              type="button"
              onClick={() => router.push(k.href)}
              className={cn(
                "rounded-r3 text-left",
                "transition-transform duration-fast ease-out hover:-translate-y-0.5",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30"
              )}
            >
              <KpiCard
                label={k.label}
                value={fmt(counts[k.key])}
                unit={k.unit}
                color={k.color}
                icon={k.icon}
              />
            </button>
          ))}
        </div>
      )}

      {/* 待办提示：只在真的有活时出现，不做常驻装饰 */}
      {!loading && (counts.pool.value > 0 || counts.reviews.value > 0) && (
        <div className="rounded-r3 border border-pending-500/30 bg-pending-500/10 p-3.5">
          <p className="text-body-sm text-pending-600">
            当前有
            {counts.pool.value > 0 && (
              <>
                <b className="num mx-1">{fmt(counts.pool)}</b>件待接派单
              </>
            )}
            {counts.pool.value > 0 && counts.reviews.value > 0 && "、"}
            {counts.reviews.value > 0 && (
              <>
                <b className="num mx-1">{fmt(counts.reviews)}</b>条复核任务待处理
              </>
            )}
            。
          </p>
        </div>
      )}

      {/* ── 快捷入口 ───────────────────────────────────────────── */}
      <section className="space-y-2">
        <h2 className="text-body-sm font-semibold text-ink-600">快捷入口</h2>
        {ACTIONS.length === 0 ? (
          <EmptyState title="暂无可用入口" />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {ACTIONS.map((a) => (
              <button
                key={a.href}
                type="button"
                onClick={() => router.push(a.href)}
                className={cn(
                  "flex items-center gap-3.5 rounded-r3 border border-line bg-surface p-4 text-left",
                  "transition-colors duration-fast hover:border-brand-300 hover:bg-surface-hover",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30"
                )}
              >
                {/* 图标块用令牌实底，不再用渐变（v2 已废弃渐变手法） */}
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-r2 bg-brand-500/15 text-link">
                  {a.icon}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block font-medium text-ink-900">{a.title}</span>
                  <span className="mt-0.5 block text-caption text-ink-500">{a.desc}</span>
                </span>
                <Badge variant="neutral" size="sm">
                  进入
                </Badge>
              </button>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
