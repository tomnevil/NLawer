"use client";

import React from "react";
import Link from "next/link";
import {
  FolderOpen,
  ClipboardCheck,
  Receipt,
  ShieldCheck,
  ArrowRight,
  AlertTriangle,
} from "lucide-react";
import { ApiError, authed } from "@nlaw/sdk";
import {
  Badge,
  Card,
  DataTable,
  KpiCard,
  Skeleton,
  Timeline,
  type DataTableColumn,
} from "@nlaw/ui";

/* ────────────────────────── 后端契约 ────────────────────────── */

/** `app/core/pagination.py` 的 `Page`：`total` 在顶层。 */
interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
  total_is_lower_bound?: boolean;
}

interface Review {
  id: number;
  target_type: string;
  target_id: number;
  case_id?: number | null;
  status: string;
  required_level: string;
  satisfied_level?: string | null;
  is_forced: boolean;
  forced_hits?: unknown[] | null;
  assignee_id?: number | null;
  decided_by?: number | null;
  decision?: string | null;
  comment?: string | null;
}

interface Scan {
  id: number;
  title: string;
  overall_risk: string;
}

interface Dashboard {
  period: string;
  quotas: { usage_type: string; used: number; limit: number; remaining: number; percent: number }[];
  work_orders: { total: number; pending: number; amount_cents: number };
}

/**
 * `GET /api/v1/complaints/stats` —— 全后端**唯一**一个由服务端算好的 SLA 指标。
 *
 * 为什么值得单独接进驾驶舱：
 * - `overdue` 是后端按 `due_at < now 且 status ∈ {PENDING, PROCESSING}` 算出来的，
 *   **不是前端能推出来的**（前端拿不到全量 `due_at`，且判定口径在服务端）。
 * - 《网络安全法》/ 平台合规要求「公布处理流程和反馈时限」，`due_days` 即承诺时限。
 *   逾期 = 对外承诺违约，是运营方最该先看到的东西，而**驾驶舱此前对投诉零可见性**。
 *
 * `overdue=0` 时**必须**同时给出 `pending + processing`（未办结总数）作为分母，
 * 否则「0 件逾期」在投诉本来就为空时是**没有意义的好消息**（已实测：种子零投诉）。
 */
interface ComplaintStats {
  by_status: Record<string, number>;
  pending: number;
  processing: number;
  resolved: number;
  rejected: number;
  overdue: number;
  due_days: number;
}

/* ────────────────────────── 常量映射 ────────────────────────── */

/**
 * 案件状态机（`app/models/enums.py::CaseStatus`）的**流水线顺序**。
 *
 * 顺序即因果：案件必然先接待、再派单、再办案、再复核、最后归档/结案。
 * 漏斗的「已推进至该阶段及以后」正是建立在这个顺序之上。
 * `VOIDED`（已作废）不在流水线内，单列展示。
 */
const PIPELINE: { status: string; label: string }[] = [
  { status: "INTAKE", label: "接待中" },
  { status: "PENDING_DISPATCH", label: "待派单" },
  { status: "DISPATCHED", label: "已派单待接" },
  { status: "ACCEPTED", label: "已接单办案" },
  { status: "IN_REVIEW", label: "复核中" },
  { status: "CONFIRMED", label: "已确认定稿" },
  { status: "ARCHIVED", label: "已归档" },
  { status: "CLOSED", label: "已结案" },
];

const REVIEW_STATUS: Record<string, { label: string; tone: "pending" | "info" | "verified" | "neutral" | "danger" }> = {
  draft: { label: "AI 初稿", tone: "neutral" },
  lawyer_editing: { label: "律师修改中", tone: "info" },
  pending_confirm: { label: "待确认", tone: "pending" },
  confirmed: { label: "已确认", tone: "verified" },
  archived: { label: "已归档", tone: "neutral" },
  voided: { label: "已作废", tone: "danger" },
};

const TARGET_LABEL: Record<string, string> = {
  CASE_ANALYSIS: "案件分析",
  DOCUMENT: "文书",
  EVIDENCE_LIST: "证据清单",
  COMPLIANCE_REPORT: "合规报告",
  LEGAL_OPINION: "法律意见",
};

const REVIEW_COLUMNS: DataTableColumn<Review>[] = [
  {
    key: "id",
    header: "复核任务",
    numeric: true,
    width: "96px",
    sortable: true,
    render: (v: number) => <span className="font-medium text-ink-900">#{v}</span>,
  },
  {
    key: "target_type",
    header: "对象",
    mobile: "primary",
    width: "112px",
    render: (v: string) => TARGET_LABEL[v] ?? v,
  },
  {
    key: "status",
    header: "状态",
    mobile: "status",
    width: "112px",
    render: (v: string) => (
      <Badge variant={REVIEW_STATUS[v]?.tone ?? "neutral"}>{REVIEW_STATUS[v]?.label ?? v}</Badge>
    ),
  },
  {
    key: "required_level",
    header: "要求等级",
    width: "92px",
    align: "center",
    sortable: true,
    render: (v: string) => <span className="num text-label">{v}</span>,
  },
  {
    key: "satisfied_level",
    header: "已满足",
    width: "88px",
    align: "center",
    render: (v: string | null | undefined) =>
      v ? <span className="num text-label text-verified-600">{v}</span> : <span className="text-ink-400">—</span>,
  },
  {
    key: "case_id",
    header: "关联案件",
    numeric: true,
    width: "104px",
    sortable: true,
    render: (v: number | null | undefined) =>
      v ? <span>#{v}</span> : <span className="text-ink-400">—</span>,
  },
  {
    key: "is_forced",
    header: "强制复核",
    width: "96px",
    align: "center",
    render: (v: boolean) =>
      v ? <Badge variant="pending">强制</Badge> : <span className="text-ink-400">—</span>,
  },
  {
    key: "decision",
    header: "结论",
    width: "104px",
    render: (v: string | null | undefined) => {
      if (!v) return <span className="text-ink-400">待处理</span>;
      const tone = v === "APPROVED" ? "verified" : v === "REJECTED" ? "danger" : "pending";
      const label = v === "APPROVED" ? "通过" : v === "REJECTED" ? "退回" : "要求修改";
      return <Badge variant={tone}>{label}</Badge>;
    },
  },
];

const RISK_TONE: Record<string, "verified" | "pending" | "danger" | "neutral"> = {
  LOW: "verified",
  MEDIUM: "pending",
  HIGH: "danger",
  CRITICAL: "danger",
};

const RISK_LABEL: Record<string, string> = {
  LOW: "低风险",
  MEDIUM: "中风险",
  HIGH: "高风险",
  CRITICAL: "严重风险",
};

/** `GET /cases/stats` 的聚合响应（P1-10）：一条 GROUP BY 返回全状态精确计数。 */
interface CaseStats {
  by_status: Record<string, number>;
}

/* ────────────────────────── 页面 ────────────────────────── */

export default function AdminCockpit() {
  const [stageCounts, setStageCounts] = React.useState<number[] | null>(null);
  const [voided, setVoided] = React.useState(0);
  const [hasLowerBound, setHasLowerBound] = React.useState(false);
  const [reviews, setReviews] = React.useState<Paged<Review> | null>(null);
  const [scans, setScans] = React.useState<Scan[] | null>(null);
  const [dashboard, setDashboard] = React.useState<Dashboard | null>(null);
  const [complaints, setComplaints] = React.useState<ComplaintStats | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    let alive = true;

    (async () => {
      try {
        /**
         * 漏斗数据来自**一个聚合端点**（P1-10）。
         *
         * 曾用 9 个 `?status=X&page_size=1` 逐状态计数拼漏斗——能用，但与
         * 其余分区合计 13 个并发请求**全有或全无**，任一失败整页只剩一条
         * 笼统错误。`GET /cases/stats` 一条 GROUP BY 返回全状态精确计数
         * （status 列有索引，无下界问题），请求数 13 → 5。
         *
         * 五个分区用 `allSettled` 而非 `all`：某一块挂了只降级那一块
         * （如合规扫描失败不影响漏斗与复核队列），不再整页白屏。
         * 401 必须原样上抛——外壳统一处理跳转，这里不能吞成「数据缺失」。
         */
        const [s0, s1, s2, s3, s4] = await Promise.allSettled([
          authed<CaseStats>("/api/v1/cases/stats"),
          authed<Paged<Review>>("/api/v1/reviews?page_size=50"),
          authed<Scan[]>("/api/v1/compliance/scans"),
          authed<Dashboard>("/api/v1/billing/dashboard"),
          authed<ComplaintStats>("/api/v1/complaints/stats"),
        ]);
        if (!alive) return;

        for (const r of [s0, s1, s2, s3, s4]) {
          if (r.status === "rejected" && r.reason instanceof ApiError && r.reason.status === 401) {
            throw r.reason;
          }
        }
        const val = <T,>(r: PromiseSettledResult<T>, fallback: T): T =>
          r.status === "fulfilled" ? r.value : fallback;

        const byStatus = val(s0, { by_status: {} as Record<string, number> }).by_status ?? {};
        setStageCounts(PIPELINE.map((s) => byStatus[s.status] ?? 0));
        setVoided(byStatus["VOIDED"] ?? 0);
        setHasLowerBound(false); // 聚合端点是 GROUP BY 精确计数，不再有下界语义
        setReviews(val(s1, null));
        setScans(val(s2, null));
        setDashboard(val(s3, null));
        setComplaints(val(s4, null));

        // 非鉴权失败：诚实标注哪个分区挂了，页面其余部分照常渲染
        const partitionNames = ["案件漏斗", "复核队列", "合规扫描", "计费总览", "投诉统计"];
        const failed = [s0, s1, s2, s3, s4]
          .map((r, i) => (r.status === "rejected" ? partitionNames[i] : null))
          .filter((x): x is string => x !== null);
        if (failed.length > 0) {
          setError(`部分数据加载失败：${failed.join("、")}，其余分区正常`);
        }
      } catch (e) {
        if (!alive) return;
        if (e instanceof ApiError && e.status === 401) return; // 外壳会处理跳转
        setError(e instanceof ApiError ? e.message : "数据加载失败");
      } finally {
        if (alive) setLoading(false);
      }
    })();

    return () => {
      alive = false;
    };
  }, []);

  // 「已推进至该阶段及以后」：由当前状态快照累加得出，天然单调递减
  const cumulative = React.useMemo(() => {
    if (!stageCounts) return null;
    const out: number[] = [];
    let sum = 0;
    for (let i = stageCounts.length - 1; i >= 0; i--) {
      sum += stageCounts[i];
      out[i] = sum;
    }
    return out;
  }, [stageCounts]);

  const entered = cumulative?.[0] ?? 0;
  const riskScans = (scans ?? []).filter((s) => s.overall_risk === "HIGH" || s.overall_risk === "CRITICAL");
  const pendingReviews = (reviews?.items ?? []).filter((r) => r.status === "pending_confirm");

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-h1 text-ink-900">律所运营驾驶舱</h1>
          <p className="mt-1 text-body-sm text-ink-500">案件 · 复核 · 计费 · 合规 全链路监控</p>
        </div>
        {dashboard?.period && <span className="num text-caption text-ink-400">账期 {dashboard.period}</span>}
      </header>

      {error && (
        <div role="alert" className="rounded-r2 border border-danger-500/30 bg-danger-500/10 px-3 py-2 text-body-sm text-danger-600">
          {error}
        </div>
      )}

      {/* ── SLA 预警条（投诉承诺时限）──────────────────── */}
      {/*
        这是概念图 P3「SLA 预警条」唯一可诚实实现的一项，原因见 §四.11：
        全后端只有投诉域有 `due_at`（承诺反馈时限）与服务端算好的 `overdue`。
        案件 / 复核 / 派单的 DTO **均无时间字段**，做不出任何时限预警。

        `overdue=0` 时仍要显示未办结总数作分母——种子零投诉已实测，
        「0 件逾期」在根本没有投诉时是**不含信息量的好消息**。
      */}
      {complaints && (
        <div
          className={`rounded-r2 border px-4 py-3 ${
            complaints.overdue > 0
              ? "border-danger-500/30 bg-danger-500/10"
              : "border-line bg-surface-subtle"
          }`}
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-start gap-2.5">
              <AlertTriangle
                className={`mt-0.5 h-4 w-4 shrink-0 ${
                  complaints.overdue > 0 ? "text-danger-600" : "text-ink-400"
                }`}
              />
              <div>
                <p
                  className={`text-body-sm font-medium ${
                    complaints.overdue > 0 ? "text-danger-600" : "text-ink-700"
                  }`}
                >
                  {complaints.overdue > 0
                    ? `${complaints.overdue} 件投诉已超过 ${complaints.due_days ?? 15} 个自然日承诺时限，仍未办结`
                    : `暂无投诉超过 ${complaints.due_days ?? 15} 个自然日承诺时限`}
                </p>
                <p className="mt-0.5 text-caption text-ink-500">
                  未办结 {complaints.pending + complaints.processing} 件（待受理{" "}
                  {complaints.pending} · 处理中 {complaints.processing}）；已办结{" "}
                  {complaints.resolved} 件、不予受理 {complaints.rejected} 件。
                  {complaints.pending + complaints.processing === 0 &&
                    "当前没有任何未办结投诉，因此「0 件逾期」在此刻不含信息量。"}
                </p>
              </div>
            </div>
            <Link
              href="/complaints"
              className="inline-flex shrink-0 items-center gap-1 text-body-sm font-medium text-brand-600 hover:text-brand-700"
            >
              查看投诉举报
              <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>
        </div>
      )}

      {/* ── KPI ──────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        {loading ? (
          Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-[92px] w-full" />)
        ) : (
          <>
            <KpiCard
              label="在办案件"
              value={entered}
              unit="件"
              icon={<FolderOpen className="h-4 w-4" />}
              color="brand"
            />
            <KpiCard
              label="复核待确认"
              value={pendingReviews.length}
              unit="项"
              icon={<ClipboardCheck className="h-4 w-4" />}
              color="pending"
            />
            <KpiCard
              label="工单待处理"
              value={dashboard?.work_orders.pending ?? 0}
              unit="笔"
              icon={<Receipt className="h-4 w-4" />}
              color="info"
            />
            <KpiCard
              label="工单金额"
              value={Math.round((dashboard?.work_orders.amount_cents ?? 0) / 100)}
              unit="元"
              icon={<Receipt className="h-4 w-4" />}
              color="gold"
            />
            <KpiCard
              label="高风险扫描"
              value={riskScans.length}
              unit="项"
              icon={<ShieldCheck className="h-4 w-4" />}
              color={riskScans.length > 0 ? "danger" : "verified"}
            />
          </>
        )}
      </div>

      {/* ── 案件流转漏斗 ─────────────────────────────── */}
      <Card hover={false}>
        <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line px-4 py-3">
          <div>
            <h2 className="text-h4 text-ink-900">案件流转漏斗</h2>
            <p className="mt-0.5 text-caption text-ink-500">
              各阶段为「当前状态处于该阶段或其后」的案件数，由状态快照累加得出
            </p>
          </div>
          {hasLowerBound && (
            <span className="inline-flex items-center gap-1 text-caption text-pending-600">
              <AlertTriangle className="h-3.5 w-3.5" />
              部分计数已达上限，占比不予计算
            </span>
          )}
        </div>

        {loading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-7 w-full" />
            ))}
          </div>
        ) : !cumulative || entered === 0 ? (
          <p className="px-4 py-10 text-center text-body-sm text-ink-500">
            暂无案件数据，漏斗将在产生案件后自动填充
          </p>
        ) : (
          <div className="space-y-1.5 p-4">
            {PIPELINE.map((stage, i) => {
              const count = cumulative[i];
              const ratio = entered > 0 ? count / entered : 0;
              // 前 4 段用品牌色，后 4 段转验证绿，直观区分「办案」与「定稿归档」
              const barColor = i < 4 ? "bg-brand-600" : "bg-verified-500";
              return (
                <div key={stage.status} className="flex items-center gap-3">
                  <span className="w-24 shrink-0 text-caption text-ink-600">{stage.label}</span>
                  <div className="h-6 min-w-0 flex-1 overflow-hidden rounded-r1 bg-surface-subtle">
                    <div
                      className={`flex h-full items-center justify-end rounded-r1 px-2 ${barColor}`}
                      style={{ width: `${Math.max(ratio * 100, count > 0 ? 6 : 0)}%` }}
                    >
                      {count > 0 && (
                        <span className="num text-[11px] font-medium text-white">{count}</span>
                      )}
                    </div>
                  </div>
                  <span className="num w-14 shrink-0 text-right text-caption text-ink-500">
                    {hasLowerBound ? "—" : `${Math.round(ratio * 100)}%`}
                  </span>
                </div>
              );
            })}

            {voided > 0 && (
              <p className="pt-2 text-caption text-ink-400">
                另有已作废案件 <span className="num">{voided}</span> 件，不计入漏斗
              </p>
            )}
          </div>
        )}
      </Card>

      {/* ── 复核队列 ─────────────────────────────────── */}
      <div>
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h2 className="text-h3 text-ink-900">复核队列</h2>
            <p className="mt-1 text-body-sm text-ink-500">
              未确认不可定稿、不可归档——这是本产品的责任边界底线
            </p>
          </div>
          <Link
            href="/audit"
            className="inline-flex items-center gap-1 text-label text-link transition-colors duration-fast hover:text-link-hover"
          >
            审计保留期 <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        </div>

        <DataTable
          columns={REVIEW_COLUMNS}
          data={reviews?.items ?? []}
          rowKey={(r) => String(r.id)}
          loading={loading}
          emptyMessage="暂无复核任务"
          columnSettings
          caption="复核队列"
        />
      </div>

      {/* ── 高风险扫描 + 流转说明 ────────────────────── */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card hover={false}>
          <h2 className="border-b border-line px-4 py-3 text-h4 text-ink-900">高风险合规发现</h2>
          {loading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : riskScans.length === 0 ? (
            <p className="px-4 py-8 text-center text-body-sm text-ink-500">没有高风险发现</p>
          ) : (
            <ul className="divide-y divide-line">
              {riskScans.slice(0, 6).map((s) => (
                <li key={s.id} className="flex items-center gap-3 px-4 py-3">
                  <ShieldCheck className="h-4 w-4 shrink-0 text-danger-500" />
                  <span className="min-w-0 flex-1 truncate text-body-sm text-ink-800">{s.title}</span>
                  <Badge variant={RISK_TONE[s.overall_risk] ?? "neutral"}>
                    {RISK_LABEL[s.overall_risk] ?? s.overall_risk}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card hover={false}>
          <h2 className="border-b border-line px-4 py-3 text-h4 text-ink-900">责任边界底线</h2>
          <div className="p-4">
            <Timeline
              items={[
                {
                  id: "s1",
                  title: "AI 生成初稿",
                  description: "所有内容默认标记为「AI 生成」，紫色虚线框",
                  status: "done",
                },
                {
                  id: "s2",
                  title: "律师复核",
                  description: "L2 律师确认后转为「律师已确认」，绿色左实线",
                  status: "current",
                },
                {
                  id: "s3",
                  title: "合伙人终审",
                  description: "S / A 级案件需 L3 终审，未确认不可定稿",
                  status: "pending",
                },
                {
                  id: "s4",
                  title: "归档留痕",
                  description: "流转全程写入审计日志，按保留期管理",
                  status: "pending",
                },
              ]}
            />
          </div>
        </Card>
      </div>
    </div>
  );
}
