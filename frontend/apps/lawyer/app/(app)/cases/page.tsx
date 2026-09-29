"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, FolderOpen, RefreshCw } from "lucide-react";
import { ApiError, authed } from "@nlaw/sdk";
import {
  Badge,
  DataTable,
  EmptyState,
  FilterBar,
  Pagination,
  PullToRefresh,
  Skeleton,
  Spinner,
  cn,
  type BadgeProps,
  type DataTableColumn,
  type FilterChip,
} from "@nlaw/ui";

/* ============================================================================
 * 后端契约（`app/api/v1/cases.py`）
 * ----------------------------------------------------------------------------
 * GET /api/v1/cases?page&page_size&status&grade&dispute_type&lawyer_id&keyword
 *   -> Page<CaseOut>，`total` 在顶层且带 `total_is_lower_bound`
 *
 * 律师角色的默认过滤是「只看自己的案件」（服务端按 ctx.user_id 收口），
 * 因此本页不需要传 lawyer_id。
 * ========================================================================== */

interface CaseRow {
  id: number;
  case_no: string;
  title: string;
  status: string;
  grade: string;
  dispute_type?: string | null;
  claim_amount?: number | null;
  focus?: string | null;
  urgency?: number;
}

interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_is_lower_bound?: boolean;
}

/** 后端 `CaseStatus` 全量九态 → 中文。缺项会让表格出现裸英文枚举值。 */
const STATUS_LABEL: Record<string, string> = {
  INTAKE: "已收案",
  PENDING_DISPATCH: "待派单",
  DISPATCHED: "已派单",
  ACCEPTED: "已接单",
  IN_REVIEW: "办案中",
  CONFIRMED: "已确认",
  ARCHIVED: "已归档",
  CLOSED: "已结案",
  VOIDED: "已作废",
};

const GRADE_TONE: Record<string, BadgeProps["variant"]> = {
  S: "danger",
  A: "pending",
  B: "info",
  C: "neutral",
};

const STATUS_TONE: Record<string, BadgeProps["variant"]> = {
  INTAKE: "neutral",
  PENDING_DISPATCH: "pending",
  DISPATCHED: "pending",
  ACCEPTED: "verified",
  IN_REVIEW: "primary",
  CONFIRMED: "verified",
  ARCHIVED: "neutral",
  CLOSED: "neutral",
  VOIDED: "danger",
};

/** 筛选下拉的候选值。与后端枚举一致，不用「全部」占位（空值即全部）。 */
const STATUS_OPTIONS = Object.keys(STATUS_LABEL);
const GRADE_OPTIONS = ["S", "A", "B", "C"];

function fmtAmount(n?: number | null): string {
  if (n === null || n === undefined) return "—";
  return `¥${n.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}`;
}

function errText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "请求失败";
}

const SELECT_CLS = cn(
  "h-9 rounded-r2 border border-line bg-surface px-2.5 text-body-sm text-ink-800",
  "transition-colors duration-fast focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-500/30"
);

export default function CasesPage() {
  const router = useRouter();

  const [rows, setRows] = useState<CaseRow[]>([]);
  const [total, setTotal] = useState(0);
  const [isLowerBound, setIsLowerBound] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [status, setStatus] = useState("");
  const [grade, setGrade] = useState("");
  const [keyword, setKeyword] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  /** 把一页响应写进状态 —— 首屏 / 「刷新」按钮 / 下拉刷新三条路径共用，避免三份会漂移的赋值 */
  const applyPage = useCallback((p: Paged<CaseRow>) => {
    setRows(p.items ?? []);
    setTotal(p.total ?? 0);
    setIsLowerBound(Boolean(p.total_is_lower_bound));
  }, []);

  /** 取数（**会抛**）。分页与筛选条件的唯一来源，三条路径共用。 */
  const fetchPage = useCallback(async () => {
    const qs = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    if (status) qs.set("status", status);
    if (grade) qs.set("grade", grade);
    if (keyword.trim()) qs.set("keyword", keyword.trim());

    return authed<Paged<CaseRow>>(`/api/v1/cases?${qs.toString()}`);
  }, [page, pageSize, status, grade, keyword]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      applyPage(await fetchPage());
    } catch (e) {
      setError(errText(e));
      setRows([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [fetchPage, applyPage]);

  /**
   * 下拉刷新（§5.1 B.1）：
   * - **失败不清空已有列表** —— 弱网下失败一次就白屏是最坏的体验（与 im `/cases` 同口径）；
   * - **不切 `loading`** —— `loading` 会触发首屏骨架屏，而下拉期间 `PullToRefresh`
   *   自带指示器，两个加载态叠加只会闪。
   */
  const refresh = useCallback(async () => {
    setError(null);
    try {
      applyPage(await fetchPage());
    } catch (e) {
      setError(errText(e));
    }
  }, [fetchPage, applyPage]);

  useEffect(() => {
    void load();
  }, [load]);

  // 改筛选条件必须回到第 1 页，否则会停在一个超出结果集的空页上
  const resetTo = useCallback((apply: () => void) => {
    apply();
    setPage(1);
  }, []);

  const chips: FilterChip[] = useMemo(() => {
    const out: FilterChip[] = [];
    if (status) out.push({ id: "status", label: "案件阶段", value: STATUS_LABEL[status] ?? status });
    if (grade) out.push({ id: "grade", label: "案件等级", value: `${grade} 级` });
    if (keyword.trim()) out.push({ id: "keyword", label: "关键词", value: keyword.trim() });
    return out;
  }, [status, grade, keyword]);

  const removeChip = useCallback(
    (id: string) => {
      resetTo(() => {
        if (id === "status") setStatus("");
        if (id === "grade") setGrade("");
        if (id === "keyword") setKeyword("");
      });
    },
    [resetTo]
  );

  const columns: DataTableColumn<CaseRow>[] = useMemo(
    () => [
      {
        key: "case_no",
        header: "案号",
        numeric: true,
        sortable: true,
        render: (v: string, row) => (
          <button
            type="button"
            onClick={() => router.push(`/cases/${row.id}`)}
            className="num text-left text-link transition-colors duration-fast hover:text-link-hover"
          >
            {v}
          </button>
        ),
      },
      {
        key: "title",
        header: "案件标题",
        sortable: true,
        mobile: "primary",
        render: (v: string, row) => (
          <span className="flex items-center gap-2">
            <FolderOpen className="h-4 w-4 shrink-0 text-ink-400" aria-hidden />
            <span className="min-w-0">
              <span className="block truncate font-medium text-ink-900">{v}</span>
              {row.focus && (
                <span className="mt-0.5 block truncate text-caption text-ink-500">{row.focus}</span>
              )}
            </span>
          </span>
        ),
      },
      {
        key: "dispute_type",
        header: "争议类型",
        sortable: true,
        mobile: "normal",
        render: (v: string | null) => v ?? <span className="text-ink-400">—</span>,
      },
      {
        key: "grade",
        header: "等级",
        align: "center",
        sortable: true,
        width: "84px",
        mobile: "normal",
        render: (v: string) => (
          <Badge variant={GRADE_TONE[v] ?? "neutral"} size="sm">
            {v} 级
          </Badge>
        ),
      },
      {
        key: "status",
        header: "阶段",
        sortable: true,
        mobile: "status",
        render: (v: string) => (
          <Badge variant={STATUS_TONE[v] ?? "neutral"} size="sm">
            {STATUS_LABEL[v] ?? v}
          </Badge>
        ),
      },
      {
        key: "claim_amount",
        header: "争议标的",
        align: "right",
        numeric: true,
        sortable: true,
        mobile: "normal",
        render: (v: number | null) => fmtAmount(v),
      },
    ],
    [router]
  );

  // 有下界标记时不能显示精确条数——把 200 当成「正好 200 件」是误导
  const countText = isLowerBound ? `${total}+ 件` : `${total} 件`;

  return (
    <PullToRefresh onRefresh={refresh} scrollTarget="window">
      <div className="space-y-4">
        <header className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-h2 text-ink-900">我的案件</h1>
            <p className="mt-1 text-body-sm text-ink-500">
              默认只显示由你承办的案件 · 点击案号或行进入详情
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

        <FilterBar
          chips={chips}
          onRemove={removeChip}
          onClearAll={() =>
            resetTo(() => {
              setStatus("");
              setGrade("");
              setKeyword("");
            })
          }
          resultCount={total}
        >
          <label className="sr-only" htmlFor="case-status-filter">
            按案件阶段筛选
          </label>
          <select
            id="case-status-filter"
            value={status}
            onChange={(e) => resetTo(() => setStatus(e.target.value))}
            className={SELECT_CLS}
          >
            <option value="">全部阶段</option>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABEL[s]}
              </option>
            ))}
          </select>

          <label className="sr-only" htmlFor="case-grade-filter">
            按案件等级筛选
          </label>
          <select
            id="case-grade-filter"
            value={grade}
            onChange={(e) => resetTo(() => setGrade(e.target.value))}
            className={SELECT_CLS}
          >
            <option value="">全部等级</option>
            {GRADE_OPTIONS.map((g) => (
              <option key={g} value={g}>
                {g} 级
              </option>
            ))}
          </select>

          <label className="sr-only" htmlFor="case-keyword-filter">
            按关键词搜索案件
          </label>
          <input
            id="case-keyword-filter"
            type="search"
            value={keyword}
            onChange={(e) => resetTo(() => setKeyword(e.target.value))}
            placeholder="搜索案件标题…"
            className={cn(SELECT_CLS, "w-44 placeholder:text-ink-400")}
          />
        </FilterBar>

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
            {[0, 1, 2, 3, 4].map((i) => (
              <Skeleton key={i} className="h-14 w-full" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<FolderOpen className="h-5 w-5" />}
            title={chips.length > 0 ? "没有符合条件的案件" : "还没有承办案件"}
            description={
              chips.length > 0
                ? "试试撤下部分筛选条件。"
                : "去派单池接单后，案件会出现在这里。"
            }
            action={
              chips.length > 0 ? (
                <button
                  type="button"
                  onClick={() =>
                    resetTo(() => {
                      setStatus("");
                      setGrade("");
                      setKeyword("");
                    })
                  }
                  className="rounded-r2 border border-line px-3 py-1.5 text-body-sm text-ink-700"
                >
                  清空筛选
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => router.push("/dispatches")}
                  className="rounded-r2 bg-solid-brand px-3 py-1.5 text-body-sm font-medium text-white"
                >
                  前往派单池
                </button>
              )
            }
          />
        ) : (
          <>
            <DataTable
              columns={columns}
              data={rows}
              rowKey={(r) => String(r.id)}
              onRowClick={(r) => router.push(`/cases/${r.id}`)}
              columnSettings
              caption="我的案件列表"
              emptyMessage="没有符合条件的案件"
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
    </PullToRefresh>
  );
}
