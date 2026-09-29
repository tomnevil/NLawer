"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Hand, Inbox, RefreshCw } from "lucide-react";
import { ApiError, authed } from "@nlaw/sdk";
import {
  Badge,
  Button,
  DataTable,
  EmptyState,
  Pagination,
  Skeleton,
  Spinner,
  cn,
  useToast,
  type BadgeProps,
  type DataTableColumn,
} from "@nlaw/ui";

/* ============================================================================
 * 派单池（待接案件）
 * ----------------------------------------------------------------------------
 * GET  /api/v1/dispatches/pool?page&page_size  -> **Page<DispatchOut>**
 * POST /api/v1/dispatches/{id}/accept          -> 接单
 *
 * ⚠️ 这里曾有一个 P0 级缺陷：旧实现写成
 *     `setItems(await authed<Dispatch[]>("/api/v1/dispatches/pool"))`
 * 而该端点返回的是 `Page` 对象（`{items, total, …}`）而非数组，
 * 于是 `items.map(...)` 抛 `TypeError: items.map is not a function`，
 * **整个派单池页面白屏**——律师端最核心的「接单」入口完全不可用。
 *
 * ⚠️ 另一个契约事实：`DispatchOut` 只有
 *     `{id, case_id, lawyer_id, mode, status, score, reason}`，
 * **没有 `case_title` 也没有 `grade`**。旧实现读的 `d.case_title` / `d.grade`
 * 恒为 `undefined`，所以卡片永远只显示「案件 #12」。
 * 案件标题必须按 `case_id` 回查 `GET /api/v1/cases/{id}` 才能拿到。
 * ========================================================================== */

interface DispatchRow {
  id: number;
  case_id: number;
  lawyer_id?: number | null;
  mode: string;
  status: string;
  score?: number | null;
  reason?: string | null;
}

interface CaseBrief {
  id: number;
  case_no: string;
  title: string;
  grade: string;
  status: string;
  dispute_type?: string | null;
  claim_amount?: number | null;
  focus?: string | null;
}

interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_is_lower_bound?: boolean;
}

const MODE_LABEL: Record<string, string> = {
  AUTO: "系统派单",
  DESIGNATED: "指定律师",
  POOL: "律师抢单",
};

const GRADE_TONE: Record<string, BadgeProps["variant"]> = {
  S: "danger",
  A: "pending",
  B: "info",
  C: "neutral",
};

function errText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "请求失败";
}

function fmtAmount(n?: number | null): string {
  if (n === null || n === undefined) return "—";
  return `¥${n.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}`;
}

export default function DispatchesPage() {
  const router = useRouter();
  const { addToast } = useToast();

  const [rows, setRows] = useState<DispatchRow[]>([]);
  const [briefs, setBriefs] = useState<Record<number, CaseBrief>>({});
  const [total, setTotal] = useState(0);
  const [isLowerBound, setIsLowerBound] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [accepting, setAccepting] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const p = await authed<Paged<DispatchRow>>(
        `/api/v1/dispatches/pool?page=${page}&page_size=${pageSize}`
      );
      const items = p.items ?? [];
      setRows(items);
      setTotal(p.total ?? 0);
      setIsLowerBound(Boolean(p.total_is_lower_bound));

      /* 标题/等级只能按 case_id 回查。
       * 用 `allSettled`：某一条案件读不到（例如刚被他人接走、或已越权）时，
       * 其余行仍应正常渲染，不能因为一条失败让整页空白。
       * 并发数受 page_size 约束（默认 20），不会放大。 */
      const results = await Promise.allSettled(
        items.map((d) => authed<CaseBrief>(`/api/v1/cases/${d.case_id}`))
      );
      const map: Record<number, CaseBrief> = {};
      results.forEach((r, i) => {
        if (r.status === "fulfilled" && r.value) map[items[i].case_id] = r.value;
      });
      setBriefs(map);
    } catch (e) {
      setError(errText(e));
      setRows([]);
      setBriefs({});
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [page, pageSize]);

  useEffect(() => {
    void load();
  }, [load]);

  const accept = useCallback(
    async (row: DispatchRow) => {
      setAccepting(row.id);
      try {
        await authed(`/api/v1/dispatches/${row.id}/accept`, { method: "POST" });
        const title = briefs[row.case_id]?.title;
        addToast({
          type: "success",
          title: "接单成功",
          message: title ? `《${title}》已进入你的案件列表` : undefined,
        });
        await load();
      } catch (e) {
        // 并发抢单失败（已被他人接走）是高频且正常的情况，如实提示而非笼统报错
        addToast({ type: "error", title: "接单失败", message: errText(e) });
        await load();
      } finally {
        setAccepting(null);
      }
    },
    [addToast, briefs, load]
  );

  const columns: DataTableColumn<DispatchRow>[] = useMemo(
    () => [
      {
        key: "case_id",
        header: "案件",
        mobile: "primary",
        render: (_v: number, row) => {
          const b = briefs[row.case_id];
          return (
            <button
              type="button"
              onClick={() => router.push(`/cases/${row.case_id}`)}
              className="flex min-w-0 items-start gap-2 text-left"
            >
              <Inbox className="mt-0.5 h-4 w-4 shrink-0 text-ink-400" aria-hidden />
              <span className="min-w-0">
                <span className="block truncate font-medium text-ink-900 transition-colors duration-fast hover:text-link">
                  {b?.title ?? `案件 #${row.case_id}`}
                </span>
                <span className="num mt-0.5 block truncate text-caption text-ink-500">
                  {b?.case_no ?? "—"}
                  {b?.focus ? ` · ${b.focus}` : ""}
                </span>
              </span>
            </button>
          );
        },
      },
      {
        key: "grade",
        header: "等级",
        align: "center",
        width: "84px",
        mobile: "normal",
        render: (_v: unknown, row) => {
          const g = briefs[row.case_id]?.grade;
          return g ? (
            <Badge variant={GRADE_TONE[g] ?? "neutral"} size="sm">
              {g} 级
            </Badge>
          ) : (
            <span className="text-ink-400">—</span>
          );
        },
      },
      {
        key: "dispute_type",
        header: "争议类型",
        mobile: "normal",
        render: (_v: unknown, row) => briefs[row.case_id]?.dispute_type ?? <span className="text-ink-400">—</span>,
      },
      {
        key: "claim_amount",
        header: "争议标的",
        align: "right",
        numeric: true,
        mobile: "normal",
        render: (_v: unknown, row) => fmtAmount(briefs[row.case_id]?.claim_amount),
      },
      {
        key: "mode",
        header: "派单方式",
        mobile: "normal",
        render: (v: string) => (
          <Badge variant={v === "POOL" ? "outline" : "neutral"} size="sm">
            {MODE_LABEL[v] ?? v}
          </Badge>
        ),
      },
      {
        key: "id",
        header: "操作",
        align: "right",
        width: "104px",
        mobile: "status",
        render: (_v: number, row) => (
          <Button
            size="sm"
            variant="primary"
            leftIcon={<Hand className="h-3.5 w-3.5" />}
            isLoading={accepting === row.id}
            onClick={() => void accept(row)}
          >
            接单
          </Button>
        ),
      },
    ],
    [briefs, accepting, accept, router]
  );

  const countText = isLowerBound ? `${total}+ 件` : `${total} 件`;

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-h2 text-ink-900">派单池</h1>
          <p className="mt-1 text-body-sm text-ink-500">
            待接案件 · 系统派单 / 指定律师 / 律师抢单 · 先到先得
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

      {error ? (
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
      ) : loading && rows.length === 0 ? (
        <div className="space-y-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          icon={<Inbox className="h-5 w-5" />}
          title="派单池暂无待接案件"
          description="有新案件派发时会出现在这里。你也可以稍后刷新查看。"
          action={
            <Button variant="outline" leftIcon={<RefreshCw className="h-3.5 w-3.5" />} onClick={() => void load()}>
              刷新
            </Button>
          }
        />
      ) : (
        <>
          <DataTable
            columns={columns}
            data={rows}
            rowKey={(r) => String(r.id)}
            caption="可抢单案件列表"
            emptyMessage="派单池暂无待接案件"
          />
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="num text-body-sm text-ink-500">共 {countText}</p>
            <Pagination
              page={page}
              pageSize={pageSize}
              total={total}
              onPageChange={setPage}
              onPageSizeChange={(n) => {
                setPageSize(n);
                setPage(1);
              }}
            />
          </div>
        </>
      )}
    </div>
  );
}
