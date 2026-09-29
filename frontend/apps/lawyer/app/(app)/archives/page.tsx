"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Archive, RefreshCw } from "lucide-react";
import { ApiError, authed } from "@nlaw/sdk";
import {
  Badge,
  Button,
  DataTable,
  EmptyState,
  Pagination,
  SegmentedControl,
  Skeleton,
  Spinner,
  cn,
  type BadgeProps,
  type DataTableColumn,
} from "@nlaw/ui";

/* ============================================================================
 * 归档与卷宗
 * ----------------------------------------------------------------------------
 * GET /api/v1/cases?status&page&page_size   -> Page<CaseOut>
 * GET /api/v1/archives/cases/{id}           -> ArchiveOut（404 = 未归档）
 *
 * 后端没有「卷宗列表」端点，因此本页由案件列表派生。
 *
 * ⚠️ 旧实现的两个缺陷：
 *  1. 拉一页 50 条后在**前端**过滤 `ARCHIVED || CONFIRMED`。案件总数超过 50 时，
 *     第 2 页之后的已归档案件永远不出现，而用户看到的是「暂无已定稿案件」。
 *     现改为**服务端按 status 过滤** + 真分页。
 *  2. 状态列直接渲染 `ARCHIVED` / `CONFIRMED` 裸枚举值。
 * ========================================================================== */

type ArchiveTabKey = "ARCHIVED" | "CONFIRMED";

interface CaseRow {
  id: number;
  case_no: string;
  title: string;
  status: string;
  grade: string;
  dispute_type?: string | null;
  claim_amount?: number | null;
  focus?: string | null;
}

interface ArchiveInfo {
  id: number;
  archive_no: string;
  current_version: number;
  retention_years: number;
  archived_at?: string | null;
  hearing_pack_path?: string | null;
}

interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_is_lower_bound?: boolean;
}

const STATUS_LABEL: Record<string, string> = {
  ARCHIVED: "已归档",
  CONFIRMED: "已定稿",
};

const STATUS_TONE: Record<string, BadgeProps["variant"]> = {
  ARCHIVED: "neutral",
  CONFIRMED: "verified",
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

function fmtDate(raw?: string | null): string {
  if (!raw) return "—";
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export default function ArchivesPage() {
  const router = useRouter();

  const [tab, setTab] = useState<ArchiveTabKey>("ARCHIVED");
  const [rows, setRows] = useState<CaseRow[]>([]);
  const [archives, setArchives] = useState<Record<number, ArchiveInfo>>({});
  const [total, setTotal] = useState(0);
  const [isLowerBound, setIsLowerBound] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const qs = new URLSearchParams({
        status: tab,
        page: String(page),
        page_size: String(pageSize),
      });
      const p = await authed<Paged<CaseRow>>(`/api/v1/cases?${qs.toString()}`);
      const items = p.items ?? [];
      setRows(items);
      setTotal(p.total ?? 0);
      setIsLowerBound(Boolean(p.total_is_lower_bound));

      /* 卷宗号是这一页的核心信息，只能逐案回查 `/archives/cases/{id}`。
       * 用 `allSettled`：已定稿但尚未生成卷宗的案件会返回 404，
       * 这是正常业务状态，不能因此让整页报错。 */
      const results = await Promise.allSettled(
        items.map((c) => authed<ArchiveInfo>(`/api/v1/archives/cases/${c.id}`))
      );
      const map: Record<number, ArchiveInfo> = {};
      results.forEach((r, i) => {
        if (r.status === "fulfilled" && r.value) map[items[i].id] = r.value;
      });
      setArchives(map);
    } catch (e) {
      setError(errText(e));
      setRows([]);
      setArchives({});
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [tab, page, pageSize]);

  useEffect(() => {
    void load();
  }, [load]);

  const columns: DataTableColumn<CaseRow>[] = useMemo(
    () => [
      {
        key: "title",
        header: "案件",
        mobile: "primary",
        render: (v: string, row) => (
          <button
            type="button"
            onClick={() => router.push(`/cases/${row.id}`)}
            className="flex min-w-0 items-start gap-2 text-left"
          >
            <Archive className="mt-0.5 h-4 w-4 shrink-0 text-ink-400" aria-hidden />
            <span className="min-w-0">
              <span className="block truncate font-medium text-ink-900 transition-colors duration-fast hover:text-link">
                {v}
              </span>
              <span className="num mt-0.5 block truncate text-caption text-ink-500">
                {row.case_no}
              </span>
            </span>
          </button>
        ),
      },
      {
        key: "archive_no",
        header: "卷宗号",
        numeric: true,
        mobile: "normal",
        render: (_v: unknown, row) => {
          const a = archives[row.id];
          return a ? (
            <span className="flex items-center gap-1.5">
              {a.archive_no}
              <span className="text-caption text-ink-400">v{a.current_version}</span>
            </span>
          ) : (
            <span className="text-ink-400">{row.status === "CONFIRMED" ? "待生成" : "—"}</span>
          );
        },
      },
      {
        key: "grade",
        header: "等级",
        align: "center",
        width: "84px",
        mobile: "normal",
        render: (v: string) => (
          <Badge variant={GRADE_TONE[v] ?? "neutral"} size="sm">
            {v} 级
          </Badge>
        ),
      },
      {
        key: "dispute_type",
        header: "争议类型",
        mobile: "normal",
        render: (v: string | null) => v ?? <span className="text-ink-400">—</span>,
      },
      {
        key: "claim_amount",
        header: "争议标的",
        align: "right",
        numeric: true,
        mobile: "normal",
        render: (v: number | null) => fmtAmount(v),
      },
      {
        key: "archived_at",
        header: "归档时间",
        numeric: true,
        mobile: "normal",
        render: (_v: unknown, row) => {
          const a = archives[row.id];
          return a?.archived_at ? fmtDate(a.archived_at) : <span className="text-ink-400">—</span>;
        },
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
        render: (_v: number, row) => (
          <Button size="sm" variant="outline" onClick={() => router.push(`/cases/${row.id}`)}>
            查看卷宗
          </Button>
        ),
      },
    ],
    [archives, router]
  );

  const countText = isLowerBound ? `${total}+ 件` : `${total} 件`;

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-h2 text-ink-900">归档与卷宗</h1>
          <p className="mt-1 text-body-sm text-ink-500">
            已定稿 / 已归档案件 · 进入案件可导出开庭材料包
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

      {/* 两个状态互斥，用分段控件而非下拉——一眼可见当前在看哪一类 */}
      <SegmentedControl<ArchiveTabKey>
        options={[
          { value: "ARCHIVED", label: "已归档" },
          { value: "CONFIRMED", label: "已定稿（待归档）" },
        ]}
        value={tab}
        onChange={(v) => {
          setTab(v);
          setPage(1);
        }}
        ariaLabel="按卷宗状态切换"
      />

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
          icon={<Archive className="h-5 w-5" />}
          title={tab === "ARCHIVED" ? "暂无已归档案件" : "暂无已定稿案件"}
          description={
            tab === "ARCHIVED"
              ? "案件复核定稿后可在此归档，归档后不可修改。"
              : "案件分析经复核确认后进入已定稿状态，随后可归档。"
          }
          action={
            <button
              type="button"
              onClick={() => router.push("/cases")}
              className="rounded-r2 bg-solid-brand px-3 py-1.5 text-body-sm font-medium text-white"
            >
              前往我的案件
            </button>
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
            caption="归档卷宗列表"
            emptyMessage="暂无卷宗"
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
