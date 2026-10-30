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
  GRADE_TONE,
  useToast,
  type DataTableColumn,
} from "@nlaw/ui";

/* ============================================================================
 * 派单池（待接案件）
 * ----------------------------------------------------------------------------
 * GET  /api/v1/dispatches/pool?page&page_size  -> **Page<PoolItemOut>**
 * POST /api/v1/dispatches/{id}/accept          -> 接单
 *
 * ⚠️ 这里曾有一个 P0 级缺陷：旧实现写成
 *     `setItems(await authed<Dispatch[]>("/api/v1/dispatches/pool"))`
 * 而该端点返回的是 `Page` 对象（`{items, total, …}`）而非数组，
 * 于是 `items.map(...)` 抛 `TypeError: items.map is not a function`，
 * **整个派单池页面白屏**——律师端最核心的「接单」入口完全不可用。
 *
 * ⚠️ 契约事实（2026-10-10 改）：`DispatchOut` 只有
 *     `{id, case_id, lawyer_id, mode, status, score, reason}`，
 * **没有 `case_title` 也没有 `grade`**。
 *
 * 曾经的做法是「按 `case_id` 逐条回查 `GET /api/v1/cases/{id}` 拿标题」，
 * 但该做法在 **P2-10（2026-09-29）之后结构上不可能成立**：
 * 池子按**租户**取，而案件详情按**承办人**鉴权（律师只能读本人承办，
 * 否则需有效团队授权，无权按「不存在」处理）⇒ 池内案件对**任何**律师
 * 都读不到 ⇒ 派单池永远只显示「案件 #N」，律师看不到标题/等级/标的，
 * 无法决定要不要接单。
 *
 * 现在摘要由**服务端在同一租户内内联**（`case_brief`，见
 * `backend/app/schemas/case.py::CaseBriefOut`）。相比逐条回查：
 *   ① 不再需要放宽案件详情的归属鉴权（P2-10 原则不动）；
 *   ② `page_size` 次往返压成 1 次（默认 20 条时省掉 20 个请求）。
 * ⚠️ `case_brief` 是**服务端定义的白名单投影**，字段集与下面 `CaseBrief`
 *    一一对应（`backend/tests/test_dispatch_pool_brief.py::T2` 守着）。
 *    要加字段必须两边同时改。
 *
 * ⚠️ **进入案件详情的唯一入口是「接单成功后」**（2026-10-11）：
 *    池内案件在 `GET /cases/{id}` 上**必然 404**（池按**租户**取、详情按
 *    **承办人**鉴权，而池内案件 `Case.lawyer_id IS NULL`）。所以行内刻意
 *    不可点 —— 接单前只给摘要，接单成功后才 `router.push` 到详情。
 * ========================================================================== */

interface DispatchRow {
  id: number;
  case_id: number;
  lawyer_id?: number | null;
  mode: string;
  status: string;
  score?: number | null;
  reason?: string | null;
  /** 服务端内联的案件摘要。`null` = 该案件不在本租户（脏数据），页面按降级显示。 */
  case_brief?: CaseBrief | null;
}

/** 与 `backend/app/schemas/case.py::CaseBriefOut` 的字段集**精确对应**。 */
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
      /* 摘要由服务端内联在 `case_brief` 里——**不要**再逐条回查
       * `GET /api/v1/cases/{id}`：那条路在 P2-10 之后对池内案件必然 404
       * （池按租户取、详情按承办人鉴权），且会产生 page_size 个额外请求。 */
      const p = await authed<Paged<DispatchRow>>(
        `/api/v1/dispatches/pool?page=${page}&page_size=${pageSize}`
      );
      setRows(p.items ?? []);
      setTotal(p.total ?? 0);
      setIsLowerBound(Boolean(p.total_is_lower_bound));
    } catch (e) {
      setError(errText(e));
      setRows([]);
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
        const title = row.case_brief?.title;
        addToast({
          type: "success",
          title: "接单成功",
          message: title ? `《${title}》已进入你的案件列表` : undefined,
        });
        /* 先刷新列表（该行已不属于池子），再进详情。
         * 接单后案件归你承办（`case.lawyer_id = 你`）⇒ 此刻详情**才**可读。
         * 这是本页**唯一**进入案件详情的入口 —— 池内行刻意不可点，
         * 因为池内案件对任何律师都读不到（见 `columns` 里的注释）。 */
        await load();
        router.push(`/cases/${row.case_id}`);
      } catch (e) {
        // 并发抢单失败（已被他人接走）是高频且正常的情况，如实提示而非笼统报错
        addToast({ type: "error", title: "接单失败", message: errText(e) });
        await load();
      } finally {
        setAccepting(null);
      }
    },
    [addToast, load, router]
  );

  const columns: DataTableColumn<DispatchRow>[] = useMemo(
    () => [
      {
        key: "case_id",
        header: "案件",
        mobile: "primary",
        render: (_v: number, row) => {
          const b = row.case_brief;
          /* ⚠️ 这里**刻意不是**链接（2026-10-11 改）。
           *
           * 池内案件在 `GET /cases/{id}` 上**必然 404**：池子按**租户**取，
           * 案件详情按**承办人**鉴权（P2-10）—— 池内案件还没有承办人
           * （`Case.lawyer_id IS NULL`），所以对**任何**律师都读不到。
           * 旧实现把它渲染成 `<button onClick={router.push}>`，点进去是整页
           * 「案件不存在」错误态（详情页 `loadCase` 一失败即整页报错）。
           *
           * 而池内案件本来也**没有**详情可给：时间线 / 材料 / 文书都是
           * **接单之后**才产生的。⇒ 接单前只给摘要（服务端内联的白名单投影
           * `case_brief`），进入详情的入口放在**接单成功后**（见 `accept`）。
           */
          return (
            <span className="flex min-w-0 items-start gap-2 text-left">
              <Inbox className="mt-0.5 h-4 w-4 shrink-0 text-ink-400" aria-hidden />
              <span className="min-w-0">
                <span className="block truncate font-medium text-ink-900">
                  {b?.title ?? `案件 #${row.case_id}`}
                </span>
                <span className="num mt-0.5 block truncate text-caption text-ink-500">
                  {b?.case_no ?? "—"}
                  {b?.focus ? ` · ${b.focus}` : ""}
                </span>
              </span>
            </span>
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
          const g = row.case_brief?.grade;
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
        render: (_v: unknown, row) => row.case_brief?.dispute_type ?? <span className="text-ink-400">—</span>,
      },
      {
        key: "claim_amount",
        header: "争议标的",
        align: "right",
        numeric: true,
        mobile: "normal",
        render: (_v: unknown, row) => fmtAmount(row.case_brief?.claim_amount),
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
    [accepting, accept]
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
        <Button variant="outline" type="button" onClick={() => void load()} disabled={loading}>
          {loading ? <Spinner size="sm" label={null} /> : <RefreshCw className="h-4 w-4" />}
          刷新
        </Button>
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
