"use client";

import React from "react";
import { FolderOpen, Gavel, RefreshCw, Scale, Send } from "lucide-react";
import { ApiError, authed, tenantScope } from "@nlaw/sdk";
import {
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
  FilterBar,
  Input,
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

/** `app/schemas/case.py::CaseOut`。 */
interface CaseRow {
  id: number;
  case_no: string;
  tenant_id: string;
  title: string;
  client_user_id?: number | null;
  lawyer_id?: number | null;
  conversation_id?: number | null;
  status: string;
  intent?: string | null;
  grade: string;
  dispute_type?: string | null;
  party_a?: string | null;
  party_b?: string | null;
  focus?: string | null;
  claim_amount?: number | null;
  urgency?: number;
  complexity?: number;
  require_formal_opinion?: boolean;
  summary?: string | null;
}

/** `app/core/pagination.py` 的 `Page`：`total` 在**顶层**。 */
interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
  total_is_lower_bound?: boolean;
}

/** `GET /cases/{id}/events` 的条目。 */
interface CaseEvent {
  id: number;
  event_type: string;
  title: string;
  description?: string | null;
  occurred_at: string;
  actor_user_id?: number | null;
}

/* ────────────────────────── 常量映射 ────────────────────────── */

/**
 * 后端 `CaseStatus` 全量九态。
 *
 * ⚠️ `IN_REVIEW` 取枚举注释的原义「复核中」，**不是**律师端案件列表里的「办案中」。
 * 后者与 `ACCEPTED`（已接单）语义重叠，会让「已接单」和「办案中」看起来是两个阶段
 * 却指同一件事。本页作为平台侧口径，以枚举为准。
 */
const STATUS_LABEL: Record<string, string> = {
  INTAKE: "接待中",
  PENDING_DISPATCH: "待派单",
  DISPATCHED: "已派单待接",
  ACCEPTED: "已接单办案",
  IN_REVIEW: "复核中",
  CONFIRMED: "已确认定稿",
  ARCHIVED: "已归档",
  CLOSED: "已结案",
  VOIDED: "已作废",
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

const GRADE_TONE: Record<string, BadgeProps["variant"]> = {
  S: "danger",
  A: "pending",
  B: "info",
  C: "neutral",
};

/** 派单模式（`app/models/enums.py::DispatchMode`）。 */
const DISPATCH_MODES = {
  AUTO: { label: "自动派单", hint: "按系统评分选律师，无需人工指定" },
  POOL: { label: "放入派单池", hint: "进入律师抢单池，由律师自主接单" },
} as const;

const STATUS_OPTIONS = Object.keys(STATUS_LABEL);
const GRADE_OPTIONS = ["S", "A", "B", "C"];

const PAGE_SIZE = 20;

const SELECT_CLS = cn(
  "h-9 rounded-r2 border border-line bg-surface px-2.5 text-body-sm text-ink-800",
  "transition-colors duration-fast focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-500/30",
);

/* ────────────────────────── 工具 ────────────────────────── */

function fmtAmount(n?: number | null): string {
  if (n === null || n === undefined) return "—";
  return `¥${n.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}`;
}

function fmtDate(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/* ────────────────────────── 页面 ────────────────────────── */

export default function AdminCasesPage() {
  const [rows, setRows] = React.useState<CaseRow[]>([]);
  const [total, setTotal] = React.useState(0);
  const [pages, setPages] = React.useState(1);
  const [lowerBound, setLowerBound] = React.useState(false);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");

  // 分阶段计数（无聚合端点，沿用驾驶舱的「逐状态取 total」做法）
  const [counts, setCounts] = React.useState<Record<string, number> | null>(null);

  // 筛选
  const [status, setStatus] = React.useState("");
  const [grade, setGrade] = React.useState("");
  const [disputeType, setDisputeType] = React.useState("");
  const [keyword, setKeyword] = React.useState("");
  const [page, setPage] = React.useState(1);

  // 详情弹窗
  const [detail, setDetail] = React.useState<CaseRow | null>(null);
  const [events, setEvents] = React.useState<CaseEvent[]>([]);
  const [detailLoading, setDetailLoading] = React.useState(false);
  const [detailError, setDetailError] = React.useState("");

  // 派单
  const [dispatching, setDispatching] = React.useState(false);
  /**
   * 派单结果。刻意用 `ok` 布尔量而不是「字符串里含『失败』」来判断成败——
   * 后者是脆弱的字符串嗅探：后端一旦返回含「失败」字样的**成功**文案
   * （例如「上次派单失败已回滚，本次成功」），界面就会显示成告警。
   */
  const [dispatchResult, setDispatchResult] = React.useState<{ ok: boolean; text: string } | null>(
    null,
  );

  /** 当前租户视角。未切换时为 `null`（跟随账号自身租户）。 */
  const [scope, setScope] = React.useState<string | null>(null);
  React.useEffect(() => setScope(tenantScope.get()), []);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [list, all, pendingDispatch, inReview, closed] = await Promise.all([
        authed<Paged<CaseRow>>("/api/v1/cases", {
          query: {
            page,
            page_size: PAGE_SIZE,
            status: status || undefined,
            grade: grade || undefined,
            dispute_type: disputeType || undefined,
            keyword: keyword || undefined,
          },
        }),
        authed<Paged<CaseRow>>("/api/v1/cases", { query: { page: 1, page_size: 1 } }),
        authed<Paged<CaseRow>>("/api/v1/cases", {
          query: { page: 1, page_size: 1, status: "PENDING_DISPATCH" },
        }),
        authed<Paged<CaseRow>>("/api/v1/cases", {
          query: { page: 1, page_size: 1, status: "IN_REVIEW" },
        }),
        authed<Paged<CaseRow>>("/api/v1/cases", {
          query: { page: 1, page_size: 1, status: "CLOSED" },
        }),
      ]);
      setRows(list?.items ?? []);
      setTotal(list?.total ?? 0);
      setPages(list?.pages ?? 1);
      setLowerBound(Boolean(list?.total_is_lower_bound));
      setCounts({
        all: all?.total ?? 0,
        PENDING_DISPATCH: pendingDispatch?.total ?? 0,
        IN_REVIEW: inReview?.total ?? 0,
        CLOSED: closed?.total ?? 0,
      });
    } catch (e) {
      // 401 交给外壳跳转
      if (e instanceof ApiError && e.status === 401) return;
      setError(e instanceof ApiError ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [page, status, grade, disputeType, keyword]);

  React.useEffect(() => {
    void load();
  }, [load]);

  /** 筛选变化一律回第 1 页——否则会出现「第 7 页 + 新条件」的空结果。 */
  const resetTo = (fn: () => void) => {
    fn();
    setPage(1);
  };

  const openDetail = async (row: CaseRow) => {
    setDetail(row);
    setEvents([]);
    setDetailError("");
    setDispatchResult(null);
    setDetailLoading(true);
    try {
      // 详情与时间线并发取；任一失败都只影响自己的区块
      const [d, ev] = await Promise.all([
        authed<CaseRow>(`/api/v1/cases/${row.id}`),
        authed<CaseEvent[]>(`/api/v1/cases/${row.id}/events`),
      ]);
      setDetail(d ?? row);
      setEvents(Array.isArray(ev) ? ev : []);
    } catch (e) {
      setDetailError(e instanceof ApiError ? e.message : "详情加载失败");
    } finally {
      setDetailLoading(false);
    }
  };

  const doDispatch = async (mode: "AUTO" | "POOL") => {
    if (!detail) return;
    setDispatching(true);
    setDispatchResult(null);
    try {
      await authed(`/api/v1/cases/${detail.id}/dispatch`, {
        method: "POST",
        body: { mode },
      });
      setDispatchResult({
        ok: true,
        text: `已提交「${DISPATCH_MODES[mode].label}」，列表已刷新。`,
      });
      await load();
    } catch (e) {
      setDispatchResult({
        ok: false,
        text: e instanceof ApiError ? `派单失败：${e.message}` : "派单失败",
      });
    } finally {
      setDispatching(false);
    }
  };

  const chips: FilterChip[] = [];
  if (status) chips.push({ id: "status", label: "阶段", value: STATUS_LABEL[status] ?? status });
  if (grade) chips.push({ id: "grade", label: "等级", value: `${grade} 级` });
  if (disputeType) chips.push({ id: "dispute_type", label: "案由", value: disputeType });
  if (keyword) chips.push({ id: "keyword", label: "关键词", value: keyword });

  const clearAll = () =>
    resetTo(() => {
      setStatus("");
      setGrade("");
      setDisputeType("");
      setKeyword("");
    });

  const columns: DataTableColumn<CaseRow>[] = React.useMemo(
    () => [
      {
        key: "case_no",
        header: "案号",
        numeric: true,
        width: "150px",
        mobile: "primary",
        /*
         * 案号本身是详情入口。
         *
         * 为什么不用单独的「详情」按钮列：`DataTable` 在卡片模式且列数 >6 时
         * 只保留 primary + status + 1 个字段（见 DataTable.tsx:293），
         * 排在后面的操作列**会被切掉**——移动端就再也打不开详情了。
         * 挂在 primary 上则两种形态都在。
         */
        render: (_v, row) => (
          <button
            type="button"
            onClick={() => void openDetail(row)}
            className="font-medium text-link underline-offset-2 hover:underline"
          >
            {row.case_no}
          </button>
        ),
      },
      {
        key: "title",
        header: "案件标题",
        render: (_v, row) => <span className="text-ink-800">{row.title}</span>,
      },
      {
        // 平台视角的关键列：不显示租户，管理员无法判断一条案件归谁
        key: "tenant_id",
        header: "所属租户",
        width: "112px",
        render: (_v, row) => <span className="num text-ink-700">{row.tenant_id}</span>,
      },
      {
        key: "status",
        header: "阶段",
        width: "112px",
        mobile: "status",
        render: (_v, row) => (
          <Badge variant={STATUS_TONE[row.status] ?? "neutral"}>
            {STATUS_LABEL[row.status] ?? row.status}
          </Badge>
        ),
      },
      {
        key: "grade",
        header: "等级",
        width: "72px",
        align: "center",
        render: (_v, row) => (
          <Badge variant={GRADE_TONE[row.grade] ?? "neutral"}>{row.grade}</Badge>
        ),
      },
      {
        key: "dispute_type",
        header: "案由",
        width: "112px",
        render: (_v, row) => row.dispute_type || "—",
      },
      {
        key: "claim_amount",
        header: "标的额",
        numeric: true,
        width: "112px",
        align: "right",
        render: (_v, row) => fmtAmount(row.claim_amount),
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
          <h1 className="text-h1 text-ink-900">案件管理</h1>
          <p className="mt-1 text-body-sm text-ink-500">
            平台视角的跨租户案件查询台。数据范围<strong className="font-medium text-ink-700">跟随顶部「租户视角」</strong>
            ，未切换时只看到当前账号自身租户的案件。
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
        作用域提示条。后端没有跨租户聚合端点，因此本页**永远只反映单个租户**。
        不写清楚这一点，管理员会把「4 条」当成全平台的案件总量。
      */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-r2 border border-line bg-surface-subtle px-3.5 py-2.5">
        <Badge variant={scope ? "primary" : "neutral"}>
          {scope ? `租户 ${scope}` : "账号自身租户"}
        </Badge>
        <p className="min-w-0 flex-1 text-caption text-ink-500">
          本页数据为<strong className="font-medium text-ink-600">单租户范围</strong>
          （后端无跨租户聚合端点）。要看其它租户请切换顶部「租户视角」。
        </p>
      </div>

      {/* ── 分阶段计数 ───────────────────────────────── */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {loading && !counts ? (
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[92px] w-full" />)
        ) : (
          <>
            <KpiCard
              label="案件总量"
              value={counts?.all ?? 0}
              unit="件"
              icon={<FolderOpen className="h-4 w-4" />}
              color="brand"
            />
            <KpiCard
              label="待派单"
              value={counts?.PENDING_DISPATCH ?? 0}
              unit="件"
              icon={<Send className="h-4 w-4" />}
              color={(counts?.PENDING_DISPATCH ?? 0) > 0 ? "pending" : "verified"}
            />
            <KpiCard
              label="复核中"
              value={counts?.IN_REVIEW ?? 0}
              unit="件"
              icon={<Scale className="h-4 w-4" />}
              color="info"
            />
            <KpiCard
              label="已结案"
              value={counts?.CLOSED ?? 0}
              unit="件"
              icon={<Gavel className="h-4 w-4" />}
              color="verified"
            />
          </>
        )}
      </div>

      {/* ── 列表 ─────────────────────────────────────── */}
      <Card hover={false}>
        <div className="border-b border-line px-4 py-3">
          <FilterBar
            chips={chips}
            onRemove={(id) =>
              resetTo(() => {
                if (id === "status") setStatus("");
                if (id === "grade") setGrade("");
                if (id === "dispute_type") setDisputeType("");
                if (id === "keyword") setKeyword("");
              })
            }
            onClearAll={clearAll}
            resultCount={total}
          >
            <label className="sr-only" htmlFor="case-status">
              按案件阶段筛选
            </label>
            <select
              id="case-status"
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

            <label className="sr-only" htmlFor="case-grade">
              按案件等级筛选
            </label>
            <select
              id="case-grade"
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

            {/* 案由是自由文本字段（后端无枚举），因此用输入框而非下拉——
                给一个猜测的候选列表会把「能查到的案由」限制成假集合。 */}
            <Input
              value={disputeType}
              onChange={(e) => resetTo(() => setDisputeType(e.target.value))}
              placeholder="案由（如 合同纠纷）"
              className="w-40"
              aria-label="按案由筛选"
            />

            <Input
              value={keyword}
              onChange={(e) => resetTo(() => setKeyword(e.target.value))}
              placeholder="搜索案件标题…"
              className="w-48"
              aria-label="按标题关键词搜索"
            />
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
            emptyMessage={chips.length > 0 ? "没有符合当前筛选条件的案件" : "当前租户下暂无案件"}
          />
        )}

        {total > 0 && (
          <div className="border-t border-line px-4 py-3">
            {lowerBound && (
              <p className="mb-2 text-caption text-ink-400">
                匹配案件超过计数上限，总数显示为 <span className="num">{total}+</span>（下界）。
              </p>
            )}
            <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
          </div>
        )}
      </Card>

      {/* ── 详情弹窗 ─────────────────────────────────── */}
      <Modal
        isOpen={detail !== null}
        onClose={() => setDetail(null)}
        size="xl"
        title={detail ? detail.title : ""}
        description={
          detail
            ? `${detail.case_no} · ${STATUS_LABEL[detail.status] ?? detail.status} · 租户 ${detail.tenant_id}`
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
              <Badge variant={GRADE_TONE[detail.grade] ?? "neutral"}>{detail.grade} 级</Badge>
              <Badge variant={STATUS_TONE[detail.status] ?? "neutral"}>
                {STATUS_LABEL[detail.status] ?? detail.status}
              </Badge>
              {detail.require_formal_opinion && <Badge variant="gold">需正式法律意见</Badge>}
              {detail.dispute_type && <Badge variant="neutral">{detail.dispute_type}</Badge>}
            </div>

            <dl className="grid gap-x-6 gap-y-2 text-body-sm sm:grid-cols-2">
              <div>
                <dt className="text-caption text-ink-500">标的额</dt>
                <dd className="num text-ink-800">{fmtAmount(detail.claim_amount)}</dd>
              </div>
              <div>
                <dt className="text-caption text-ink-500">紧急度 / 复杂度</dt>
                <dd className="num text-ink-800">
                  {detail.urgency ?? 0} / {detail.complexity ?? 0}
                </dd>
              </div>
              <div>
                <dt className="text-caption text-ink-500">委托人</dt>
                <dd className="num text-ink-800">
                  {detail.client_user_id ? `用户 #${detail.client_user_id}` : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-caption text-ink-500">承办律师</dt>
                <dd className="num text-ink-800">
                  {detail.lawyer_id ? `律师 #${detail.lawyer_id}` : "未指派"}
                </dd>
              </div>
              <div>
                <dt className="text-caption text-ink-500">当事人</dt>
                <dd className="text-ink-800">
                  {detail.party_a || detail.party_b
                    ? `${detail.party_a ?? "—"} / ${detail.party_b ?? "—"}`
                    : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-caption text-ink-500">会话</dt>
                <dd className="num text-ink-800">
                  {detail.conversation_id ? `#${detail.conversation_id}` : "—"}
                </dd>
              </div>
            </dl>

            {(detail.focus || detail.summary) && (
              <div className="space-y-2">
                {detail.focus && (
                  <div>
                    <h4 className="text-caption text-ink-500">争议焦点</h4>
                    <p className="mt-1 text-body-sm text-ink-800">{detail.focus}</p>
                  </div>
                )}
                {detail.summary && (
                  <div>
                    <h4 className="text-caption text-ink-500">案情摘要</h4>
                    <p className="mt-1 whitespace-pre-wrap rounded-r2 border border-line bg-surface-subtle p-3 text-body-sm text-ink-700">
                      {detail.summary}
                    </p>
                  </div>
                )}
              </div>
            )}

            {/* ── 时间线 ───────────────────────────── */}
            <div>
              <h4 className="mb-2 text-caption text-ink-500">案件动态</h4>
              {detailLoading ? (
                <div className="space-y-2">
                  {Array.from({ length: 3 }).map((_, i) => (
                    <Skeleton key={i} className="h-10 w-full" />
                  ))}
                </div>
              ) : events.length === 0 ? (
                <p className="rounded-r2 border border-line bg-surface-subtle p-3 text-body-sm text-ink-500">
                  该案件暂无动态记录。
                </p>
              ) : (
                <Timeline
                  items={events.map((e) => ({
                    id: String(e.id),
                    title: e.title,
                    time: fmtDate(e.occurred_at),
                    description: e.description ?? undefined,
                    status: "done" as const,
                  }))}
                />
              )}
            </div>

            {/* ── 派单（唯一的写操作，仅桌面）─────────────
                §08.1 规定运营后台移动端「仅支持只读查看，批量操作、规则配置不移动化」，
                因此派单按钮在 <1024px 不渲染。
                只提供 AUTO / POOL 两种模式：DESIGNATED 需要指定律师，
                而后端**没有律师候选列表端点**，做不出真实的选择器——
                宁可不给这个入口，也不放一个填不出值的输入框。 */}
            {detail.status === "PENDING_DISPATCH" && (
              <div className="hidden rounded-r2 border border-line p-3.5 lg:block">
                <h4 className="text-body-sm font-medium text-ink-800">派单</h4>
                <p className="mt-0.5 text-caption text-ink-500">
                  案件当前处于「待派单」，可在此触发派单。
                </p>
                <div className="mt-2.5 flex flex-wrap items-center gap-2">
                  {(Object.keys(DISPATCH_MODES) as Array<keyof typeof DISPATCH_MODES>).map((m) => (
                    <Button
                      key={m}
                      variant="secondary"
                      onClick={() => void doDispatch(m)}
                      disabled={dispatching}
                      title={DISPATCH_MODES[m].hint}
                    >
                      {dispatching ? <Spinner size="sm" label={null} /> : <Send className="h-4 w-4" />}
                      {DISPATCH_MODES[m].label}
                    </Button>
                  ))}
                </div>
                <p className="mt-2 text-caption text-ink-400">
                  「客户指定律师」模式需要指定具体律师，而后端未提供律师候选列表接口，故不在此提供。
                </p>
              </div>
            )}

            {dispatchResult && (
              <Alert
                variant={dispatchResult.ok ? "success" : "warning"}
                title={dispatchResult.ok ? "派单已提交" : "派单未成功"}
              >
                {dispatchResult.text}
              </Alert>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}
