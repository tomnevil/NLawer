"use client";

import React from "react";
import { AlertTriangle, ClipboardCheck, RefreshCw, ShieldAlert, Undo2 } from "lucide-react";
import { ApiError, authed, tenantScope } from "@nlaw/sdk";
import {
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
  FilterBar,
  KpiCard,
  Modal,
  Pagination,
  Skeleton,
  Spinner,
  Timeline,
  cn,
  type BadgeProps,
  type DataTableColumn,
  type FilterChip,
} from "@nlaw/ui";

/* ────────────────────────── 后端契约 ────────────────────────── */

/** `app/schemas/review.py::ReviewOut`。 */
interface ReviewRow {
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

/** `ReviewRecordOut`：留痕。 */
interface ReviewRecord {
  id: number;
  review_id: number;
  action: string;
  actor_id?: number | null;
  actor_role?: string | null;
  level?: string | null;
  from_status?: string | null;
  to_status?: string | null;
  changes?: Record<string, unknown> | null;
  comment?: string | null;
}

interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
  total_is_lower_bound?: boolean;
}

/**
 * `app/schemas/analysis.py` 的 `CaseAnalysisOut`（简化版）。
 *
 * 只取 admin 监督页会用到的字段：
 * - summary: 摘要
 * - related_laws / similar_cases: 法条与类案（引用溯源）
 * - suggestions: 路径建议列表
 * - status / required_level: 与复核侧对照
 *
 * ⚠️ 不能臆造字段——所有显示项必须真实来自端点响应。
 */
interface AnalysisSummary {
  id: number;
  case_id: number;
  version: number;
  summary?: string | null;
  legal_analysis?: string | null;
  /**
   * ⚠️ 键名是 `law_name` / `article_no`，**不是** `law` / `article`。
   * 权威来源 `case_copilot.py::_build_sections`；与 `apps/lawyer` 的
   * `RelatedLaw` 定义一致。写错键名不会报错，只会**渲染出空法条**。
   */
  related_laws?:
    | { law_name?: string; article_no?: string; excerpt?: string; citation_id?: number }[]
    | null;
  /** 类案：`citation_id` 指向 `case_precedents.id`（与法条**不是**同一张表）。 */
  similar_cases?:
    | { case_no?: string; title?: string; court?: string; holding?: string; citation_id?: number }[]
    | null;
  suggestions?: { path?: string; pros?: string; cons?: string }[] | null;
  status: string;
  required_level: string;
  ai_generated?: boolean;
}

/* ────────────────────────── 常量映射 ────────────────────────── */

/** `app/models/enums.py::ReviewStatus`。 */
const STATUS_META: Record<string, { label: string; tone: BadgeProps["variant"] }> = {
  draft: { label: "AI 初稿", tone: "neutral" },
  lawyer_editing: { label: "律师修改中", tone: "info" },
  pending_confirm: { label: "待确认", tone: "pending" },
  confirmed: { label: "已确认定稿", tone: "verified" },
  archived: { label: "已归档", tone: "neutral" },
  voided: { label: "已作废", tone: "danger" },
};

/** `ReviewTargetType` 五成员。 */
const TARGET_LABEL: Record<string, string> = {
  CASE_ANALYSIS: "案件分析",
  DOCUMENT: "文书",
  EVIDENCE_LIST: "证据清单",
  COMPLIANCE_REPORT: "合规报告",
  LEGAL_OPINION: "法律意见",
};

/** `ReviewLevel`：三级复核。 */
const LEVEL_LABEL: Record<string, string> = {
  L1: "L1 AI 自检",
  L2: "L2 律师复核",
  L3: "L3 合伙人终审",
};

const LEVEL_ORDER: Record<string, number> = { L1: 1, L2: 2, L3: 3 };

/** `ReviewRecord.action` 的实际取值（`grep _record(` 实测）。 */
const ACTION_LABEL: Record<string, string> = {
  CREATE: "创建复核",
  EDIT: "律师修改",
  SUBMIT: "提交复核",
  APPROVE: "复核通过",
  APPROVE_PARTIAL: "降级通过（强制放行）",
  REJECT: "复核驳回",
  REQUEST_REVISION: "要求修改",
  ARCHIVE: "归档",
  VOID: "作废",
};

const STATUS_OPTIONS = Object.keys(STATUS_META);
const TARGET_OPTIONS = Object.keys(TARGET_LABEL);

const PAGE_SIZE = 20;

/* ────────────────────────── 工具 ────────────────────────── */

function fmtDate(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 已满足级别是否达到要求级别。 */
function levelOk(required: string, satisfied?: string | null): boolean | null {
  if (!satisfied) return null;
  const r = LEVEL_ORDER[required];
  const s = LEVEL_ORDER[satisfied];
  if (r === undefined || s === undefined) return null;
  return s >= r;
}

/* ────────────────────────── 页面 ────────────────────────── */

/**
 * 运营后台·复核队列（**只读监控台**）。
 *
 * ## 为什么刻意不提供任何写操作
 *
 * 后端在这一模块提供了 `submit` / `edit` / `decide` / `archive` / `void`
 * 五个写端点，本页**一个都不接**。三条理由：
 *
 * 1. **责任边界**：`design-spec.md` 把「律师已确认」定义为**律师侧**的确认动作。
 *    平台运营方在后台点「确认」，会生成一条 `decided_by = 平台管理员` 的定稿记录——
 *    而法律工作成果的确认必须由执业律师作出。
 * 2. **§08.1** 把运营后台限定为「只读查看：驾驶舱核心指标、告警、案件查询」。
 * 3. `apps/lawyer/app/(app)/reviews/page.tsx` 已经是律师执行复核动作的地方。
 *    在 admin 再造一套动作面，会让「同一件事有两个地方能做、责任却不同」。
 *
 * ## ⚠️ 需要产品拍板的一个后端能力边界问题
 *
 * `ReviewService.resolve_actor_level()` 对 `PLATFORM_ADMIN` 与 `FIRM_ADMIN`
 * **无条件返回 L3**，绕过了 `LawyerProfile.can_l3_review` 这个
 * 「谁有资格终审定稿」的机制。也就是说**后端目前允许平台管理员定稿法律成果**。
 *
 * 前端不提供入口，但这属于后端层面的能力边界问题，建议单独评估。
 * （后端已冻结，本轮只登记不修改。）
 */
export default function AdminReviewsPage() {
  const [rows, setRows] = React.useState<ReviewRow[]>([]);
  const [total, setTotal] = React.useState(0);
  const [pages, setPages] = React.useState(1);
  const [lowerBound, setLowerBound] = React.useState(false);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [counts, setCounts] = React.useState<Record<string, number> | null>(null);

  const [status, setStatus] = React.useState("");
  const [targetType, setTargetType] = React.useState("");
  const [page, setPage] = React.useState(1);

  // 详情 + 留痕
  const [detail, setDetail] = React.useState<ReviewRow | null>(null);
  const [records, setRecords] = React.useState<ReviewRecord[]>([]);
  const [detailLoading, setDetailLoading] = React.useState(false);
  const [detailError, setDetailError] = React.useState("");
  // 被复核对象：仅对 CASE_ANALYSIS 类型有意义。
  // 拉取策略与复核本身并行，404 是合法状态（演示数据缺失 / 生产被撤回）。
  const [analysis, setAnalysis] = React.useState<AnalysisSummary | null>(null);
  const [analysisMissing, setAnalysisMissing] = React.useState(false);
  const [analysisLoading, setAnalysisLoading] = React.useState(false);

  const [scope, setScope] = React.useState<string | null>(null);
  React.useEffect(() => setScope(tenantScope.get()), []);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [list, pending, editing, confirmed, archived] = await Promise.all([
        authed<Paged<ReviewRow>>("/api/v1/reviews", {
          query: {
            page,
            page_size: PAGE_SIZE,
            status: status || undefined,
            target_type: targetType || undefined,
          },
        }),
        authed<Paged<ReviewRow>>("/api/v1/reviews", {
          query: { page: 1, page_size: 1, status: "pending_confirm" },
        }),
        authed<Paged<ReviewRow>>("/api/v1/reviews", {
          query: { page: 1, page_size: 1, status: "lawyer_editing" },
        }),
        authed<Paged<ReviewRow>>("/api/v1/reviews", {
          query: { page: 1, page_size: 1, status: "confirmed" },
        }),
        authed<Paged<ReviewRow>>("/api/v1/reviews", {
          query: { page: 1, page_size: 1, status: "archived" },
        }),
      ]);
      setRows(list?.items ?? []);
      setTotal(list?.total ?? 0);
      setPages(list?.pages ?? 1);
      setLowerBound(Boolean(list?.total_is_lower_bound));
      setCounts({
        pending_confirm: pending?.total ?? 0,
        lawyer_editing: editing?.total ?? 0,
        confirmed: confirmed?.total ?? 0,
        archived: archived?.total ?? 0,
      });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return;
      setError(e instanceof ApiError ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [page, status, targetType]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const resetTo = (fn: () => void) => {
    fn();
    setPage(1);
  };

  const openDetail = async (row: ReviewRow) => {
    setDetail(row);
    setRecords([]);
    setDetailError("");
    setAnalysis(null);
    setAnalysisMissing(false);
    setAnalysisLoading(false);
    setDetailLoading(true);
    try {
      const fetches: [Promise<ReviewRow>, Promise<ReviewRecord[]>, Promise<AnalysisSummary | null>?] = [
        authed<ReviewRow>(`/api/v1/reviews/${row.id}`),
        authed<ReviewRecord[]>(`/api/v1/reviews/${row.id}/records`),
      ];
      // 仅 CASE_ANALYSIS 类型才去拉被复核对象，且按 case_id（target_id 是分析 id，
      // case_id 是案件 id，复核行同时带这两个字段）。
      const willFetchAnalysis =
        row.target_type === "CASE_ANALYSIS" && row.case_id != null;
      if (willFetchAnalysis) {
        setAnalysisLoading(true);
        fetches[2] = authed<AnalysisSummary>(
          `/api/v1/analyses/case/${row.case_id}`
        ).catch((e: unknown) => {
          if (e instanceof ApiError && e.status === 404) {
            setAnalysisMissing(true);
            return null;
          }
          throw e;
        });
      }
      const [d, recs, ana] = await Promise.all(fetches);
      setDetail(d ?? row);
      setRecords(Array.isArray(recs) ? recs : []);
      if (ana) setAnalysis(ana);
    } catch (e) {
      setDetailError(e instanceof ApiError ? e.message : "留痕加载失败");
    } finally {
      setDetailLoading(false);
      setAnalysisLoading(false);
    }
  };

  const chips: FilterChip[] = [];
  if (status) chips.push({ id: "status", label: "状态", value: STATUS_META[status]?.label ?? status });
  if (targetType)
    chips.push({ id: "target_type", label: "对象", value: TARGET_LABEL[targetType] ?? targetType });

  const clearAll = () =>
    resetTo(() => {
      setStatus("");
      setTargetType("");
    });

  /** 本页中「级别未达标却已定稿」的条数。刻意标注「本页」——后端无该维度的筛选参数。 */
  const forcedOnPage = rows.filter((r) => r.is_forced).length;

  const columns: DataTableColumn<ReviewRow>[] = React.useMemo(
    () => [
      {
        key: "id",
        header: "复核任务",
        numeric: true,
        width: "104px",
        mobile: "primary",
        // 详情入口挂在 primary 列：DataTable 在列数 >6 的卡片模式下会切掉末尾的操作列
        render: (_v, row) => (
          <button
            type="button"
            onClick={() => void openDetail(row)}
            className="font-medium text-link underline-offset-2 hover:underline"
          >
            #{row.id}
          </button>
        ),
      },
      {
        key: "target_type",
        header: "复核对象",
        width: "120px",
        render: (_v, row) => TARGET_LABEL[row.target_type] ?? row.target_type,
      },
      {
        key: "case_id",
        header: "关联案件",
        numeric: true,
        width: "104px",
        render: (_v, row) => (row.case_id ? `#${row.case_id}` : "—"),
      },
      {
        key: "status",
        header: "状态",
        width: "120px",
        mobile: "status",
        render: (_v, row) => (
          <Badge variant={STATUS_META[row.status]?.tone ?? "neutral"}>
            {STATUS_META[row.status]?.label ?? row.status}
          </Badge>
        ),
      },
      {
        key: "required_level",
        header: "要求级别",
        width: "88px",
        align: "center",
        render: (_v, row) => <span className="num">{row.required_level}</span>,
      },
      {
        key: "satisfied_level",
        header: "已满足",
        width: "88px",
        align: "center",
        render: (_v, row) => {
          const ok = levelOk(row.required_level, row.satisfied_level);
          if (!row.satisfied_level) return <span className="text-ink-400">—</span>;
          return (
            <span className={cn("num", ok ? "text-verified-600" : "text-danger-600 font-medium")}>
              {row.satisfied_level}
            </span>
          );
        },
      },
      {
        // 强制放行是合规风险点：级别未达标仍然定稿。必须一眼可见。
        key: "is_forced",
        header: "风险",
        width: "104px",
        align: "center",
        render: (_v, row) =>
          row.is_forced ? (
            <Badge variant="danger">强制放行</Badge>
          ) : (
            <span className="text-ink-300">—</span>
          ),
      },
      {
        key: "_actions",
        header: "操作",
        width: "88px",
        align: "right",
        render: (_v, row) => (
          <Button variant="outline" size="sm" onClick={() => void openDetail(row)}>
            详情
          </Button>
        ),
      },
    ],
    [],
  );

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-h1 text-ink-900">复核队列</h1>
          <p className="mt-1 text-body-sm text-ink-500">
            三级复核流转的监控台。数据范围跟随顶部「租户视角」。
          </p>
        </div>
        <Button variant="outline" onClick={() => void load()} disabled={loading}>
          {loading ? <Spinner size="sm" label={null} /> : <RefreshCw className="h-4 w-4" />}
          刷新
        </Button>
      </header>

      {error && (
        <div
          role="alert"
          className="rounded-r2 border border-danger-500/30 bg-danger-500/10 px-3 py-2 text-body-sm text-danger-600"
        >
          {error}
        </div>
      )}

      {/*
        只读声明。这不是免责话术——它必须说清「为什么不给按钮」，
        否则运维会反复反馈「复核队列不能操作」。
      */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-r2 border border-line bg-surface-subtle px-3.5 py-2.5">
        <Badge variant="neutral">只读监控</Badge>
        <Badge variant={scope ? "primary" : "neutral"}>
          {scope ? `租户 ${scope}` : "账号自身租户"}
        </Badge>
        <p className="min-w-0 flex-1 text-caption text-ink-500">
          本页仅用于监控与留痕查阅，<strong className="font-medium text-ink-600">不提供复核动作</strong>
          。法律工作成果的确认必须由执业律师在律师端完成——平台运营方在后台点「确认」，
          会产生一条责任主体不符的定稿记录。
        </p>
      </div>

      {/* ── 状态计数 ─────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {loading && !counts ? (
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[92px] w-full" />)
        ) : (
          <>
            <KpiCard
              label="待确认"
              value={counts?.pending_confirm ?? 0}
              unit="件"
              icon={<ClipboardCheck className="h-4 w-4" />}
              color={(counts?.pending_confirm ?? 0) > 0 ? "pending" : "verified"}
            />
            <KpiCard
              label="律师修改中"
              value={counts?.lawyer_editing ?? 0}
              unit="件"
              icon={<Undo2 className="h-4 w-4" />}
              color="info"
            />
            <KpiCard
              label="已确认定稿"
              value={counts?.confirmed ?? 0}
              unit="件"
              icon={<ShieldAlert className="h-4 w-4" />}
              color="brand"
            />
            <KpiCard
              label="已归档"
              value={counts?.archived ?? 0}
              unit="件"
              icon={<ClipboardCheck className="h-4 w-4" />}
              color="verified"
            />
          </>
        )}
      </div>

      {forcedOnPage > 0 && (
        <Alert variant="warning" title={`本页有 ${forcedOnPage} 条为「强制放行」`}>
          这些复核的已满足级别低于要求级别却仍然定稿。强制放行是合规审查会直接问到的问题，
          请逐条确认是否留有充分理由。
        </Alert>
      )}

      {/* ── 队列 ─────────────────────────────────────── */}
      <Card hover={false}>
        <div className="border-b border-line px-4 py-3">
          <FilterBar
            chips={chips}
            onRemove={(id) =>
              resetTo(() => {
                if (id === "status") setStatus("");
                if (id === "target_type") setTargetType("");
              })
            }
            onClearAll={clearAll}
            resultCount={total}
          >
            <label className="sr-only" htmlFor="review-status">
              按复核状态筛选
            </label>
            <select
              id="review-status"
              value={status}
              onChange={(e) => resetTo(() => setStatus(e.target.value))}
              className={cn(
                "h-9 rounded-r2 border border-line bg-surface px-2.5 text-body-sm text-ink-800",
                "transition-colors duration-fast focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-500/30",
              )}
            >
              <option value="">全部状态</option>
              {STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {STATUS_META[s].label}
                </option>
              ))}
            </select>

            <label className="sr-only" htmlFor="review-target">
              按复核对象筛选
            </label>
            <select
              id="review-target"
              value={targetType}
              onChange={(e) => resetTo(() => setTargetType(e.target.value))}
              className={cn(
                "h-9 rounded-r2 border border-line bg-surface px-2.5 text-body-sm text-ink-800",
                "transition-colors duration-fast focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-500/30",
              )}
            >
              <option value="">全部对象</option>
              {TARGET_OPTIONS.map((t) => (
                <option key={t} value={t}>
                  {TARGET_LABEL[t]}
                </option>
              ))}
            </select>
          </FilterBar>
        </div>

        {loading && rows.length === 0 ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : (
          <DataTable
            columns={columns}
            data={rows}
            rowKey={(r) => String(r.id)}
            emptyMessage={chips.length > 0 ? "没有符合当前筛选条件的复核任务" : "当前租户下暂无复核任务"}
          />
        )}

        {total > 0 && (
          <div className="border-t border-line px-4 py-3">
            {lowerBound && (
              <p className="mb-2 text-caption text-ink-400">
                匹配任务超过计数上限，总数显示为 <span className="num">{total}+</span>（下界）。
              </p>
            )}
            <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
          </div>
        )}
      </Card>

      {/* ── 详情 + 留痕 ──────────────────────────────── */}
      <Modal
        isOpen={detail !== null}
        onClose={() => {
          setDetail(null);
          setAnalysis(null);
          setAnalysisMissing(false);
        }}
        size="lg"
        title={detail ? `复核任务 #${detail.id}` : ""}
        description={
          detail
            ? `${TARGET_LABEL[detail.target_type] ?? detail.target_type} #${detail.target_id} · ${
                STATUS_META[detail.status]?.label ?? detail.status
              }`
            : undefined
        }
      >
        {detail && (
          <div className="space-y-4">
            {detailError && (
              <p role="alert" className="text-body-sm text-danger-600">
                {detailError}
              </p>
            )}

            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={STATUS_META[detail.status]?.tone ?? "neutral"}>
                {STATUS_META[detail.status]?.label ?? detail.status}
              </Badge>
              <Badge variant="neutral">要求 {LEVEL_LABEL[detail.required_level] ?? detail.required_level}</Badge>
              {detail.satisfied_level && (
                <Badge
                  variant={levelOk(detail.required_level, detail.satisfied_level) ? "verified" : "danger"}
                >
                  已满足 {detail.satisfied_level}
                </Badge>
              )}
              {detail.is_forced && <Badge variant="danger">强制放行</Badge>}
            </div>

            {detail.is_forced && (
              <Alert variant="warning" title="该复核为强制放行">
                已满足级别（{detail.satisfied_level ?? "无"}）低于要求级别（{detail.required_level}）
                却仍然定稿。强制放行记录{detail.forced_hits?.length ?? 0} 条命中项。
              </Alert>
            )}

            <dl className="grid gap-x-6 gap-y-2 text-body-sm sm:grid-cols-2">
              <div>
                <dt className="text-caption text-ink-500">关联案件</dt>
                <dd className="num text-ink-800">{detail.case_id ? `#${detail.case_id}` : "—"}</dd>
              </div>
              <div>
                <dt className="text-caption text-ink-500">复核结论</dt>
                <dd className="text-ink-800">{detail.decision || "—"}</dd>
              </div>
              <div>
                <dt className="text-caption text-ink-500">指派给</dt>
                <dd className="num text-ink-800">
                  {detail.assignee_id ? `用户 #${detail.assignee_id}` : "未指派"}
                </dd>
              </div>
              <div>
                <dt className="text-caption text-ink-500">定稿人</dt>
                <dd className="num text-ink-800">
                  {detail.decided_by ? `用户 #${detail.decided_by}` : "未定稿"}
                </dd>
              </div>
            </dl>

            {detail.comment && (
              <div>
                <h4 className="text-caption text-ink-500">结论说明</h4>
                <p className="mt-1 rounded-r2 border border-line bg-surface-subtle p-3 text-body-sm text-ink-700">
                  {detail.comment}
                </p>
              </div>
            )}

            {/* ── 被复核对象 ───────────────────────── */}
            {/*
              admin 是监督视角，不做编辑器：只展示「复核的是什么东西」最核心的字段。
              不渲染完整六段式（那是律师端的事），避免与律师端职责重叠。
              404（合法状态：演示数据缺失 / 生产被撤回）显式空态，**不假装有内容**。
            */}
            <div>
              <h4 className="mb-1.5 text-caption text-ink-500">
                被复核对象（{TARGET_LABEL[detail.target_type] ?? detail.target_type} #{detail.target_id}）
              </h4>
              {analysisLoading ? (
                <Skeleton className="h-16 w-full" />
              ) : analysisMissing ? (
                <p className="rounded-r2 border border-line bg-surface-subtle p-3 text-body-sm text-ink-500">
                  分析对象不存在。可能是演示数据缺失（早期种子里 review.target_id
                  指向了案件 id 而不是分析 id），或被复核对象已被撤回。
                </p>
              ) : analysis ? (
                <div className="space-y-2 rounded-r2 border border-line bg-surface-subtle p-3 text-body-sm">
                  {analysis.summary && (
                    <p className="text-ink-800">
                      <span className="text-caption text-ink-500">摘要　</span>
                      {analysis.summary}
                    </p>
                  )}
                  {analysis.related_laws && analysis.related_laws.length > 0 && (
                    <div>
                      {/*
                        「可溯源」= 该条引用带 `citation_id`，能在平台法条库里查到原文。
                        本项目原则是「引用必须库内可验证，否则不显示引用」——
                        所以这个比值是**监督信号**：低于 100% 说明有引用不可溯源。
                        没有 citation_id 时**不显示 0/0**，而是整块不渲染。
                      */}
                      <p className="text-caption text-ink-500">
                        关键法条
                        <span className="ml-1 text-ink-400">
                          （{analysis.related_laws.filter((l) => l.citation_id != null).length}/
                          {analysis.related_laws.length} 可溯源）
                        </span>
                      </p>
                      <ul className="mt-1 space-y-1">
                        {analysis.related_laws.slice(0, 3).map((l, i) => (
                          <li key={i} className="text-ink-700">
                            《{l.law_name ?? "—"}》{l.article_no ?? ""}
                            {l.excerpt ? `：${l.excerpt}` : ""}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {analysis.similar_cases && analysis.similar_cases.length > 0 && (
                    <div>
                      <p className="text-caption text-ink-500">
                        类案
                        <span className="ml-1 text-ink-400">
                          （{analysis.similar_cases.filter((c) => c.citation_id != null).length}/
                          {analysis.similar_cases.length} 可溯源）
                        </span>
                      </p>
                      <ul className="mt-1 space-y-1">
                        {analysis.similar_cases.slice(0, 3).map((c, i) => (
                          <li key={i} className="text-ink-700">
                            {c.case_no ? `${c.case_no}　` : ""}
                            {c.title ?? "—"}
                            {c.court ? `（${c.court}）` : ""}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {analysis.suggestions && analysis.suggestions.length > 0 && (
                    <p className="text-caption text-ink-500">
                      路径：{analysis.suggestions
                        .map((s) => s.path ?? "—")
                        .filter(Boolean)
                        .join(" · ") || "—"}
                    </p>
                  )}
                </div>
              ) : (
                <p className="rounded-r2 border border-line bg-surface-subtle p-3 text-body-sm text-ink-500">
                  此复核类型无对应对象可展示。
                </p>
              )}
            </div>

            {/* ── 留痕 ─────────────────────────────── */}
            <div>
              <h4 className="mb-2 text-caption text-ink-500">
                复核留痕（{records.length} 条）
              </h4>
              {detailLoading ? (
                <div className="space-y-2">
                  {Array.from({ length: 3 }).map((_, i) => (
                    <Skeleton key={i} className="h-10 w-full" />
                  ))}
                </div>
              ) : records.length === 0 ? (
                <p className="rounded-r2 border border-line bg-surface-subtle p-3 text-body-sm text-ink-500">
                  该复核任务暂无留痕记录。
                </p>
              ) : (
                <Timeline
                  items={records.map((r) => ({
                    id: String(r.id),
                    title: ACTION_LABEL[r.action] ?? r.action,
                    // `ReviewRecordOut` 无时间字段 → 刻意不传 `time`。
                    // 传一个假时间比不传更糟（留痕是合规证据，时间不能编）。
                    actor: [
                      r.actor_role ?? null,
                      r.actor_id ? `用户 #${r.actor_id}` : null,
                      r.level ?? null,
                    ]
                      .filter(Boolean)
                      .join(" · "),
                    description: [
                      r.from_status || r.to_status
                        ? `${STATUS_META[r.from_status ?? ""]?.label ?? r.from_status ?? "—"} → ${
                            STATUS_META[r.to_status ?? ""]?.label ?? r.to_status ?? "—"
                          }`
                        : null,
                      r.comment || null,
                      r.changes && Object.keys(r.changes).length > 0
                        ? `变更字段：${Object.keys(r.changes).join("、")}`
                        : null,
                    ]
                      .filter(Boolean)
                      .join("　·　"),
                    status: (r.action === "REJECT" || r.action === "VOID"
                      ? "rejected"
                      : r.action === "APPROVE" || r.action === "ARCHIVE"
                        ? "done"
                        : "pending") as "done" | "rejected" | "pending",
                  }))}
                />
              )}
            </div>

            <p className="flex items-start gap-1.5 text-caption text-ink-400">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              本页为只读监控台，不提供复核动作。需要执行复核请由执业律师在律师端操作。
            </p>
          </div>
        )}
      </Modal>
    </div>
  );
}
