"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, ClipboardCheck, Lock, RefreshCw } from "lucide-react";
import { ApiError, authed } from "@nlaw/sdk";
import {
  Badge,
  Button,
  DataTable,
  EmptyState,
  FilterBar,
  Pagination,
  Skeleton,
  Spinner,
  cn,
  type BadgeProps,
  type DataTableColumn,
  type FilterChip,
} from "@nlaw/ui";

/* ============================================================================
 * 复核队列
 * ----------------------------------------------------------------------------
 * GET /api/v1/reviews?page&page_size&status&target_type -> Page<ReviewOut>
 *
 * ⚠️ 旧实现直接把后端枚举值当文案渲染：状态列显示的是 `pending_confirm`、
 * `lawyer_editing` 这样的裸英文，目标列显示 `CASE_ANALYSIS #12`。
 * 律师看到的应该是「待复核确认」「案件分析」，因此这里补全了两张映射表。
 *
 * 另一个行为修正：旧实现只拉一页（`page_size=50`）且**无分页控件**，
 * 超过 50 条的任务不可达。现改为服务端分页 + 状态/类型筛选。
 * ========================================================================== */

interface ReviewRow {
  id: number;
  target_type: string;
  target_id: number;
  case_id?: number | null;
  status: string;
  required_level: string;
  satisfied_level?: string | null;
  is_forced?: boolean;
  forced_hits?: unknown[] | null;
  assignee_id?: number | null;
  decided_by?: number | null;
  decision?: string | null;
  comment?: string | null;
}

interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_is_lower_bound?: boolean;
}

/** `ReviewStatus` 六态，全小写。缺项会漏出裸英文。 */
const STATUS_LABEL: Record<string, string> = {
  draft: "AI 初稿",
  lawyer_editing: "律师修改中",
  pending_confirm: "待复核确认",
  confirmed: "已定稿",
  archived: "已归档",
  voided: "已作废",
};

const STATUS_TONE: Record<string, BadgeProps["variant"]> = {
  draft: "neutral",
  lawyer_editing: "pending",
  pending_confirm: "primary",
  confirmed: "verified",
  archived: "neutral",
  voided: "danger",
};

const TARGET_LABEL: Record<string, string> = {
  CASE_ANALYSIS: "案件分析",
  DOCUMENT: "法律文书",
  EVIDENCE_LIST: "证据清单",
  COMPLIANCE_REPORT: "合规报告",
  LEGAL_OPINION: "法律意见书",
};

const LEVEL_LABEL: Record<string, string> = {
  L1: "L1 AI 自检",
  L2: "L2 律师复核",
  L3: "L3 合伙人终审",
};

const DECISION_LABEL: Record<string, string> = {
  APPROVED: "已通过",
  REJECTED: "未通过",
  REVISION_REQUESTED: "已退回修改",
};

/** 需要我处理的（尚未定稿）状态——用于「待我处理」筛选与排序提示。 */
const OPEN_STATUSES = ["draft", "lawyer_editing", "pending_confirm"];

const STATUS_OPTIONS = Object.keys(STATUS_LABEL);
const TARGET_OPTIONS = Object.keys(TARGET_LABEL);

function errText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "请求失败";
}

const SELECT_CLS = cn(
  "h-9 rounded-r2 border border-line bg-surface px-2.5 text-body-sm text-ink-800",
  "transition-colors duration-fast focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-500/30"
);

export default function ReviewsPage() {
  const router = useRouter();

  const [rows, setRows] = useState<ReviewRow[]>([]);
  const [total, setTotal] = useState(0);
  const [isLowerBound, setIsLowerBound] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [status, setStatus] = useState("");
  const [targetType, setTargetType] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const qs = new URLSearchParams({
        page: String(page),
        page_size: String(pageSize),
      });
      if (status) qs.set("status", status);
      if (targetType) qs.set("target_type", targetType);

      const p = await authed<Paged<ReviewRow>>(`/api/v1/reviews?${qs.toString()}`);
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
  }, [page, pageSize, status, targetType]);

  useEffect(() => {
    void load();
  }, [load]);

  // 改筛选必须回到第 1 页，否则会停在超出结果集的空页
  const resetTo = useCallback((apply: () => void) => {
    apply();
    setPage(1);
  }, []);

  const chips: FilterChip[] = useMemo(() => {
    const out: FilterChip[] = [];
    if (status) out.push({ id: "status", label: "复核状态", value: STATUS_LABEL[status] ?? status });
    if (targetType)
      out.push({ id: "target_type", label: "复核对象", value: TARGET_LABEL[targetType] ?? targetType });
    return out;
  }, [status, targetType]);

  const removeChip = useCallback(
    (id: string) => {
      resetTo(() => {
        if (id === "status") setStatus("");
        if (id === "target_type") setTargetType("");
      });
    },
    [resetTo]
  );

  const columns: DataTableColumn<ReviewRow>[] = useMemo(
    () => [
      {
        key: "target_type",
        header: "复核对象",
        mobile: "primary",
        render: (v: string, row) => (
          <span className="flex items-center gap-2">
            <ClipboardCheck className="h-4 w-4 shrink-0 text-ink-400" aria-hidden />
            <span className="min-w-0">
              <span className="block truncate font-medium text-ink-900">
                {TARGET_LABEL[v] ?? v}
                <span className="num ml-1.5 text-caption font-normal text-ink-500">
                  #{row.target_id}
                </span>
              </span>
              {row.comment && (
                <span className="mt-0.5 block truncate text-caption text-ink-500">
                  {row.comment}
                </span>
              )}
            </span>
          </span>
        ),
      },
      {
        key: "required_level",
        header: "要求级别",
        mobile: "normal",
        render: (v: string, row) => (
          <span className="flex items-center gap-1.5">
            <span>{LEVEL_LABEL[v] ?? v}</span>
            {/* 级别未满足时明确标出，避免「已通过却还没定稿」的困惑 */}
            {row.satisfied_level && row.satisfied_level !== v && (
              <span className="text-caption text-pending-600">（现 {row.satisfied_level}）</span>
            )}
          </span>
        ),
      },
      {
        key: "decision",
        header: "结论",
        mobile: "normal",
        render: (v: string | null) => (v ? DECISION_LABEL[v] ?? v : <span className="text-ink-400">—</span>),
      },
      {
        key: "is_forced",
        header: "强制",
        align: "center",
        width: "72px",
        mobile: "normal",
        render: (v: boolean, row) =>
          v ? (
            <span
              className="inline-flex items-center gap-1 text-caption text-danger-600"
              title={
                Array.isArray(row.forced_hits) && row.forced_hits.length > 0
                  ? `命中 ${row.forced_hits.length} 项强制复核场景`
                  : "强制复核"
              }
            >
              <Lock className="h-3.5 w-3.5" aria-hidden />
              强制
            </span>
          ) : (
            <span className="text-ink-400">—</span>
          ),
      },
      {
        key: "status",
        header: "状态",
        mobile: "status",
        render: (v: string) => (
          <Badge variant={STATUS_TONE[v] ?? "neutral"} size="sm">
            {STATUS_LABEL[v] ?? v}
          </Badge>
        ),
      },
      {
        key: "id",
        header: "操作",
        align: "right",
        width: "104px",
        mobile: "hidden",
        render: (_v: number, row) =>
          row.case_id ? (
            <Button
              size="sm"
              variant={OPEN_STATUSES.includes(row.status) ? "primary" : "outline"}
              onClick={() => router.push(`/cases/${row.case_id}`)}
            >
              {OPEN_STATUSES.includes(row.status) ? "去处理" : "查看"}
            </Button>
          ) : (
            <span className="text-caption text-ink-400">无关联案件</span>
          ),
      },
    ],
    [router]
  );

  const countText = isLowerBound ? `${total}+ 条` : `${total} 条`;

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-h2 text-ink-900">复核队列</h1>
          <p className="mt-1 text-body-sm text-ink-500">
            L1 AI 自检 / L2 律师复核 / L3 合伙人终审 · 全程留痕 · 未确认不可定稿
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
            setTargetType("");
          })
        }
        resultCount={total}
      >
        <label className="sr-only" htmlFor="review-status-filter">
          按复核状态筛选
        </label>
        <select
          id="review-status-filter"
          value={status}
          onChange={(e) => resetTo(() => setStatus(e.target.value))}
          className={SELECT_CLS}
        >
          <option value="">全部状态</option>
          {STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>
              {STATUS_LABEL[s]}
            </option>
          ))}
        </select>

        <label className="sr-only" htmlFor="review-target-filter">
          按复核对象筛选
        </label>
        <select
          id="review-target-filter"
          value={targetType}
          onChange={(e) => resetTo(() => setTargetType(e.target.value))}
          className={SELECT_CLS}
        >
          <option value="">全部对象</option>
          {TARGET_OPTIONS.map((t) => (
            <option key={t} value={t}>
              {TARGET_LABEL[t]}
            </option>
          ))}
        </select>
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
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          icon={<ClipboardCheck className="h-5 w-5" />}
          title={chips.length > 0 ? "没有符合条件的复核任务" : "暂无复核任务"}
          description={
            chips.length > 0
              ? "试试撤下部分筛选条件。"
              : "案件分析提交复核后，任务会出现在这里。"
          }
          action={
            chips.length > 0 ? (
              <button
                type="button"
                onClick={() =>
                  resetTo(() => {
                    setStatus("");
                    setTargetType("");
                  })
                }
                className="rounded-r2 border border-line px-3 py-1.5 text-body-sm text-ink-700"
              >
                清空筛选
              </button>
            ) : (
              <button
                type="button"
                onClick={() => router.push("/cases")}
                className="rounded-r2 bg-solid-brand px-3 py-1.5 text-body-sm font-medium text-white"
              >
                前往我的案件
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
            onRowClick={(r) => {
              if (r.case_id) router.push(`/cases/${r.case_id}`);
            }}
            columnSettings
            caption="复核任务列表"
            emptyMessage="暂无复核任务"
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
