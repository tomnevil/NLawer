"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ChevronRight, FolderOpen } from "lucide-react";
import { authed } from "@nlaw/sdk";
import { AppSwitcher, Badge, EmptyState, PullToRefresh, Skeleton, buildAppLinks } from "@nlaw/ui";

import {
  CASE_STATUS_TONE,
  GRADE_TONE,
  caseStatusLabel,
  errText,
  fmtAmount,
  isCaseActive,
  type CaseDetail,
  type Paged,
} from "../../../lib/imApi";

/** 四端是独立进程，切换必须整页跳转；地址走静态 env 访问才会被内联。 */
const APPS = buildAppLinks({
  web: process.env.NEXT_PUBLIC_APP_WEB_URL,
  lawyer: process.env.NEXT_PUBLIC_APP_LAWYER_URL,
  admin: process.env.NEXT_PUBLIC_APP_ADMIN_URL,
  im: process.env.NEXT_PUBLIC_APP_IM_URL,
});

/* ============================================================================
 * 我的案件（客户端 · `/cases`）
 * ----------------------------------------------------------------------------
 * 数据源：`GET /api/v1/cases`
 * 后端已按角色收窄（`app/api/v1/cases.py:48`）：
 *   `if ctx.role == Role.CLIENT: base = base.where(Case.client_user_id == ctx.user_id)`
 * ⇒ **不需要前端再过滤一次归属**；前端过滤只做展示分组，不是安全边界。
 *
 * 【为什么一次取 50 条再前端分组，而不是按状态多次请求】
 * `GET /cases?status=X` 只接受**单个**状态值，而「办理中」是 3 个状态的并集
 * （见 `ACTIVE_CASE_STATUSES`）⇒ 按状态查要打 3 次、且分页口径各不相同。
 * C 端客户的案件数通常是个位数，一次取回再分组更简单也更少出错。
 * `total` 超出取回条数时如实提示，不假装已经看全。
 * ========================================================================== */

type GroupKey = "all" | "active" | "closed";

const GROUPS: { key: GroupKey; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "active", label: "办理中" },
  { key: "closed", label: "已结案" },
];

/** 一次取回的条数。超出部分靠 `total` 提示，不静默截断。 */
const PAGE_SIZE = 50;

export default function ImCasesPage() {
  const [cases, setCases] = useState<CaseDetail[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [group, setGroup] = useState<GroupKey>("all");

  /** 唯一的数据入口 —— 首屏与下拉刷新共用，避免两份会漂移的取数逻辑 */
  const load = useCallback(async () => {
    const page = await authed<Paged<CaseDetail>>(`/api/v1/cases?page=1&page_size=${PAGE_SIZE}`);
    setCases(page.items ?? []);
    setTotal(page.total ?? 0);
  }, []);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    load()
      .catch((e) => {
        if (alive) setError(errText(e));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [load]);

  /** 下拉刷新：**不清空已有列表** —— 弱网下一次失败就白屏是最坏的体验 */
  const refresh = useCallback(async () => {
    setError(null);
    try {
      await load();
    } catch (e) {
      setError(errText(e));
    }
  }, [load]);

  const counts = useMemo(
    () => ({
      all: cases.length,
      active: cases.filter((c) => isCaseActive(c.status)).length,
      closed: cases.filter((c) => !isCaseActive(c.status)).length,
    }),
    [cases]
  );

  const shown = useMemo(() => {
    if (group === "all") return cases;
    if (group === "active") return cases.filter((c) => isCaseActive(c.status));
    return cases.filter((c) => !isCaseActive(c.status));
  }, [cases, group]);

  return (
    /* 下拉刷新（§5.1 B.1）：本页自带滚动容器 ⇒ 直接把它换成 `PullToRefresh`
       —— 它内部就是 `scroll-thin min-h-0 flex-1 overflow-y-auto`，
       `className="h-full"` 把外层高度接上，滚动权仍在**同一个**元素上（不会双层滚动）。 */
    <PullToRefresh onRefresh={refresh} className="h-full">
      <div className="mx-auto w-full max-w-[560px] px-4 pb-6 lg:max-w-[720px] lg:px-6">
        <header className="pt-5">
          {/* 跨端切换（与 `/chat` 左栏头部、`/me`「切换到其他端」一致）。
              🚨 **但它不算 B2a 的「导航出口」** —— 实测结论，别再照着旧结论改回去：
              `AppSwitcher` 是 `<button>` + 下拉，菜单项只在点开后渲染；而
              `verify_breakpoints.py` 的 B2a 只认 `a[href]` ⇒ 加完它 `/cases` 仍判
              **rc=1**（`页内链接=['cases']`，只剩自链）。真正的常驻出口是下面那个
              `<Link href="/chat#new">`：`/me` 靠「我的咨询」数据格 `href="/chat"`
              通过、根页靠快捷入口卡片通过，**属同一类手段**（真链接、常驻、不自链）。
              `-mx-2` 是**光学对齐**：组件内部按钮自带 `px-2`，抵消后与 `h1` 左缘对齐。 */}
          <AppSwitcher
            appId="im"
            apps={APPS}
            className="-mx-2"
            onChange={(app) => window.location.assign(app.href)}
          />
          <div className="mt-2 flex items-baseline justify-between gap-3">
            <h1 className="text-h4 text-ink-900">我的案件</h1>
            {/* 🚨 **常驻导航出口**（B2a）：≥1024 时 TabBar 被 `lg:hidden` 隐藏，
                该宽度下必须有指向**其它**一级路由的站内链接（**自链不算**）。
                此前本页唯一的出口在**空态**里（`shown.length === 0` 时的「发起咨询」）
                ⇒ **有案件的客户反而被困**，且判据结论会随种子数据变化。
                放**头部**符合 §8.3「仅放标题、返回、低频操作」；措辞与目标沿用空态与根页的
                同一入口（「发起咨询」→ `/chat#new`），不新造第二种说法。
                ⚠️ 带可见文字的链接**不在** `verify_tap_targets.py` 范围内
                （它只扫「有 svg 且无可见文字」的图标按钮）。 */}
            <Link
              href="/chat#new"
              className="shrink-0 text-label font-medium text-brand-600 hover:text-brand-700"
            >
              发起咨询
            </Link>
          </div>
          {!loading && !error && (
            <p className="mt-0.5 text-caption text-ink-500">共 {total} 件</p>
          )}
        </header>

        {/* 分组筛选：状态并集（办理中 = 3 个状态）在前端算，见文件头说明 */}
        <div className="mt-3 flex gap-2" role="tablist" aria-label="案件分组">
          {GROUPS.map((g) => {
            const on = g.key === group;
            return (
              <button
                key={g.key}
                type="button"
                role="tab"
                aria-selected={on}
                onClick={() => setGroup(g.key)}
                className={
                  "min-h-tap rounded-r2 border px-3 text-body-sm transition-colors duration-fast " +
                  (on
                    ? "border-brand-600 bg-brand-50 font-medium text-brand-600"
                    : "border-line bg-surface text-ink-600 hover:bg-surface-hover")
                }
              >
                {g.label}
                {!loading && <span className="num ml-1.5 text-caption text-ink-400">{counts[g.key]}</span>}
              </button>
            );
          })}
        </div>

        <div className="mt-3">
          {loading ? (
            <div className="space-y-2">
              <Skeleton className="h-20 w-full" />
              <Skeleton className="h-20 w-full" />
              <Skeleton className="h-20 w-full" />
            </div>
          ) : error ? (
            <p className="rounded-r3 border border-danger-500/30 bg-danger-500/10 p-3 text-body-sm text-danger-600">
              {error}
            </p>
          ) : shown.length === 0 ? (
            <EmptyState
              icon={<FolderOpen className="h-5 w-5" />}
              title={group === "all" ? "还没有案件" : "这个分组下没有案件"}
              description={
                group === "all"
                  ? "描述情况涉及复杂纠纷、需要律师介入时，AI 会自动立案并派单。"
                  : "换个分组看看。"
              }
              action={
                group === "all" ? (
                  <Link
                    href="/chat#new"
                    className="rounded-r2 bg-solid-brand px-3 py-1.5 text-body-sm font-medium text-white"
                  >
                    发起咨询
                  </Link>
                ) : undefined
              }
            />
          ) : (
            <ul className="space-y-2">
              {shown.map((c) => (
                <li key={c.id}>
                  <Link
                    href={`/cases/${c.id}`}
                    className="flex min-h-tap items-center gap-3 rounded-r3 border border-line bg-surface p-4 shadow-s1 transition-colors duration-fast hover:bg-surface-hover"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-2">
                        <span className="truncate text-body font-medium text-ink-800">{c.title}</span>
                        {c.grade && (
                          <Badge variant={GRADE_TONE[c.grade] ?? "neutral"} size="sm">
                            {c.grade}
                          </Badge>
                        )}
                      </span>
                      <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-ink-400">
                        <span className="num">{c.case_no}</span>
                        {c.dispute_type && <span>{c.dispute_type}</span>}
                        {c.claim_amount != null && (
                          <span className="num">{fmtAmount(c.claim_amount)}</span>
                        )}
                      </span>
                      <span className="mt-2 inline-block">
                        <Badge variant={CASE_STATUS_TONE[c.status] ?? "neutral"} size="sm" dot>
                          {caseStatusLabel(c.status)}
                        </Badge>
                      </span>
                    </span>
                    <ChevronRight className="h-4 w-4 shrink-0 text-ink-400" />
                  </Link>
                </li>
              ))}
            </ul>
          )}

          {/* 取回条数不足 total 时如实说明，不假装已经看全 */}
          {!loading && !error && total > cases.length && (
            <p className="mt-3 text-caption text-ink-400">
              已显示最近 {cases.length} 件，共 {total} 件。
            </p>
          )}
        </div>
      </div>
    </PullToRefresh>
  );
}
