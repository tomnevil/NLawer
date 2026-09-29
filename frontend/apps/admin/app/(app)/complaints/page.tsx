"use client";

import React from "react";
import { AlertTriangle, CheckCircle2, Clock, Inbox, RefreshCw, ShieldAlert } from "lucide-react";
import { ApiError, authed } from "@nlaw/sdk";
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
  type DataTableColumn,
  type FilterChip,
} from "@nlaw/ui";

/* ────────────────────────── 后端契约 ────────────────────────── */

/** `app/core/pagination.py` 的 `Page`：`total` 在**顶层**，不在 `meta` 下。 */
interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
  /** `total` 是否为下界（实际匹配数 > COUNT_CAP=200）。为真时不能显示精确数字。 */
  total_is_lower_bound?: boolean;
}

/** `app/api/v1/complaints.py::_admin_view()`。 */
interface Complaint {
  id: number;
  ticket_no: string;
  type: string;
  status: string;
  description: string;
  created_at: string | null;
  due_at: string | null;
  handle_note: string | null;
  handled_at: string | null;
  feedback_sent: boolean;
  // 以下为管理端专有字段
  tenant_id: string;
  reporter_id: number | null;
  contact: string | null;
  target_type: string | null;
  target_id: string | null;
  related_moderation_id: number | null;
  handler_id: number | null;
  ip_address: string | null;
}

/** `ComplaintService.counts()`。**跨租户全局统计**，非本租户。 */
interface Stats {
  by_status: Record<string, number>;
  pending: number;
  processing: number;
  resolved: number;
  rejected: number;
  overdue: number;
  /** 承诺反馈时限（自然日），来自 `COMPLAINT_DUE_DAYS`。 */
  due_days: number;
}

/* ────────────────────────── 常量映射 ────────────────────────── */

const TYPE_LABEL: Record<string, string> = {
  CONTENT_MISJUDGED: "误判申诉",
  ILLEGAL_CONTENT: "举报违法内容",
  SERVICE_ABUSE: "举报服务滥用",
  OTHER: "其他",
};

const STATUS_META: Record<
  string,
  { label: string; tone: "pending" | "info" | "verified" | "neutral" }
> = {
  PENDING: { label: "待受理", tone: "pending" },
  PROCESSING: { label: "处理中", tone: "info" },
  RESOLVED: { label: "已办结", tone: "verified" },
  REJECTED: { label: "不予受理", tone: "neutral" },
};

const STATUS_OPTIONS = ["PENDING", "PROCESSING", "RESOLVED", "REJECTED"] as const;

/** 后端 `ComplaintService.handle()` 对终态结论的最小长度要求。 */
const MIN_NOTE = 5;

interface HandleAction {
  status: string;
  label: string;
  /** 是否必须填写处理结论（后端对办结/不予受理强制） */
  needsNote: boolean;
  variant: "secondary" | "verify" | "outline";
}

/**
 * 处理动作表——**镜像后端文档的流转**，而不是靠报错教学。
 *
 * `ComplaintService.handle()` 的文档写的是「受理 → 处理中 → 办结/不予受理」。
 * 注意后端本身**不校验流转合法性**（只校验终态必须填结论），
 * 所以这里的克制是**刻意的**：终态不再提供任何处理入口。
 *
 * 若在终态上再给一个「改回处理中」，审计日志里就会出现
 * 「已办结 → 处理中」这种无法解释的记录，而投诉处理是要留痕备查的。
 */
const ACTIONS: Record<string, HandleAction[]> = {
  PENDING: [
    { status: "PROCESSING", label: "受理", needsNote: false, variant: "secondary" },
    { status: "REJECTED", label: "不予受理", needsNote: true, variant: "outline" },
  ],
  PROCESSING: [
    { status: "RESOLVED", label: "办结", needsNote: true, variant: "verify" },
    { status: "REJECTED", label: "不予受理", needsNote: true, variant: "outline" },
  ],
};

const SELECT_CLS =
  "h-9 rounded-r2 border border-line bg-surface px-2.5 text-body-sm text-ink-800 " +
  "transition-colors duration-fast focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-500/30";

const PAGE_SIZE = 20;

/* ────────────────────────── 工具 ────────────────────────── */

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 距期限还有多久。逾期返回负数。 */
function daysLeft(dueAt: string | null): number | null {
  if (!dueAt) return null;
  const due = new Date(dueAt).getTime();
  if (Number.isNaN(due)) return null;
  return Math.ceil((due - Date.now()) / 86_400_000);
}

function isOpen(status: string): boolean {
  return status === "PENDING" || status === "PROCESSING";
}

/* ────────────────────────── 页面 ────────────────────────── */

export default function ComplaintsPage() {
  const [stats, setStats] = React.useState<Stats | null>(null);
  const [rows, setRows] = React.useState<Complaint[]>([]);
  const [total, setTotal] = React.useState(0);
  const [pages, setPages] = React.useState(1);
  const [lowerBound, setLowerBound] = React.useState(false);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");

  // 筛选
  const [status, setStatus] = React.useState("");
  const [type, setType] = React.useState("");
  const [overdueOnly, setOverdueOnly] = React.useState(false);
  const [page, setPage] = React.useState(1);

  // 处理弹窗
  const [target, setTarget] = React.useState<Complaint | null>(null);
  const [action, setAction] = React.useState<HandleAction | null>(null);
  const [note, setNote] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const [formError, setFormError] = React.useState("");
  const [done, setDone] = React.useState("");

  const load = React.useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [s, l] = await Promise.all([
        authed<Stats>("/api/v1/complaints/stats"),
        authed<Paged<Complaint>>("/api/v1/complaints", {
          query: {
            page,
            page_size: PAGE_SIZE,
            status: status || undefined,
            type: type || undefined,
            overdue_only: overdueOnly ? "true" : undefined,
          },
        }),
      ]);
      setStats(s);
      setRows(l?.items ?? []);
      setTotal(l?.total ?? 0);
      setPages(l?.pages ?? 1);
      setLowerBound(Boolean(l?.total_is_lower_bound));
    } catch (e) {
      // 401 交给外壳跳转，页面不自作主张
      if (e instanceof ApiError && e.status === 401) return;
      setError(e instanceof ApiError ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [page, status, type, overdueOnly]);

  React.useEffect(() => {
    void load();
  }, [load]);

  /** 筛选条件变化一律回到第 1 页——否则会出现「第 7 页 + 新条件」的空结果。 */
  const resetTo = (fn: () => void) => {
    fn();
    setPage(1);
  };

  const openHandle = (row: Complaint, a: HandleAction) => {
    setTarget(row);
    setAction(a);
    setNote(row.handle_note ?? "");
    setFormError("");
    setDone("");
  };

  const closeModal = () => {
    if (submitting) return;
    setTarget(null);
    setAction(null);
    setFormError("");
  };

  const submit = async () => {
    if (!target || !action) return;
    const text = note.trim();

    // 前端先挡一道：后端会拒，但「靠报错教学」的体验很差，
    // 而且投诉处理是有时限压力的事，不该让人先失败一次。
    if (action.needsNote && text.length < MIN_NOTE) {
      setFormError(`办结或不予受理必须填写处理结论（不少于 ${MIN_NOTE} 个字）`);
      return;
    }

    setSubmitting(true);
    setFormError("");
    try {
      await authed<Complaint>(`/api/v1/complaints/${target.id}/handle`, {
        method: "POST",
        body: { status: action.status, handle_note: text },
      });
      setDone(`${target.ticket_no} 已更新为「${STATUS_META[action.status]?.label ?? action.status}」`);
      setTarget(null);
      setAction(null);
      await load();
    } catch (e) {
      setFormError(e instanceof ApiError ? e.message : "处理失败");
    } finally {
      setSubmitting(false);
    }
  };

  const chips: FilterChip[] = [];
  if (status) chips.push({ id: "status", label: "状态", value: STATUS_META[status]?.label ?? status });
  if (type) chips.push({ id: "type", label: "类型", value: TYPE_LABEL[type] ?? type });
  if (overdueOnly) chips.push({ id: "overdue", label: "范围", value: "仅逾期未办结" });

  const clearAll = () => resetTo(() => {
    setStatus("");
    setType("");
    setOverdueOnly(false);
  });

  const columns: DataTableColumn<Complaint>[] = React.useMemo(
    () => [
      {
        key: "ticket_no",
        header: "工单号",
        numeric: true,
        width: "148px",
        mobile: "primary",
        render: (_v, row) => <span className="font-medium text-ink-900">{row.ticket_no}</span>,
      },
      {
        key: "type",
        header: "类型",
        width: "128px",
        render: (_v, row) => TYPE_LABEL[row.type] ?? row.type,
      },
      {
        key: "status",
        header: "状态",
        width: "104px",
        mobile: "status",
        render: (_v, row) => {
          const m = STATUS_META[row.status];
          return <Badge variant={m?.tone ?? "neutral"}>{m?.label ?? row.status}</Badge>;
        },
      },
      {
        // 这一列是**跨租户**页面的关键：平台管理员必须一眼看出工单属于哪个租户。
        // 后端未提供租户列表接口，因此这里直接用工单自带的 tenant_id，
        // 它也是目前唯一能从真实数据里读到租户标识的地方。
        key: "tenant_id",
        header: "所属租户",
        width: "112px",
        render: (_v, row) => <span className="num text-ink-700">{row.tenant_id}</span>,
      },
      {
        key: "due_at",
        header: "反馈期限",
        width: "160px",
        render: (_v, row) => {
          const d = daysLeft(row.due_at);
          const overdue = isOpen(row.status) && d !== null && d < 0;
          return (
            <span className={overdue ? "num font-medium text-danger-600" : "num text-ink-600"}>
              {fmtDate(row.due_at)}
              {overdue && <span className="ml-1.5 text-caption">（逾期 {-d!} 天）</span>}
            </span>
          );
        },
      },
      {
        key: "_actions",
        header: "操作",
        width: "96px",
        align: "right",
        render: (_v, row) => (
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              const list = ACTIONS[row.status] ?? [];
              openHandle(row, list[0] ?? { status: "", label: "查看", needsNote: false, variant: "outline" });
            }}
          >
            {isOpen(row.status) ? "处理" : "查看"}
          </Button>
        ),
      },
    ],
    [],
  );

  const openCount = (stats?.pending ?? 0) + (stats?.processing ?? 0);

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-h1 text-ink-900">投诉举报</h1>
          <p className="mt-1 text-body-sm text-ink-500">
            《生成式人工智能服务管理暂行办法》第十五条要求提供便捷投诉举报入口并公布反馈时限。
            本页为平台侧受理台，承诺时限 <span className="num">{stats?.due_days ?? 15}</span> 个自然日。
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

      {done && (
        <div className="flex items-start gap-2 rounded-r2 border border-verified-500/30 bg-verified-500/10 px-3 py-2 text-body-sm text-verified-700">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{done}</span>
        </div>
      )}

      {/*
        必须显式标注「全平台」。
        这个页面和驾驶舱不同：投诉端点在服务端对 PLATFORM_ADMIN 走**全局查询**
        （`counts(tenant_id=None)`），不受租户视角影响。
        若不标注，管理员会以为这是当前视角租户的数据，进而误判工单归属。
      */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-r2 border border-line bg-surface-subtle px-3.5 py-2.5">
        <Badge variant="info">全平台</Badge>
        <p className="min-w-0 flex-1 text-caption text-ink-500">
          以下统计与列表为<strong className="font-medium text-ink-600">跨租户全局数据</strong>，
          不受顶部「租户视角」影响——投诉受理台需要一次看全所有租户的工单。
        </p>
      </div>

      {/* ── 看板 ─────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {loading && !stats ? (
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[92px] w-full" />)
        ) : (
          <>
            <KpiCard
              label="待受理"
              value={stats?.pending ?? 0}
              unit="单"
              icon={<Inbox className="h-4 w-4" />}
              color={(stats?.pending ?? 0) > 0 ? "pending" : "verified"}
            />
            <KpiCard
              label="处理中"
              value={stats?.processing ?? 0}
              unit="单"
              icon={<Clock className="h-4 w-4" />}
              color="info"
            />
            <KpiCard
              label="逾期未办结"
              value={stats?.overdue ?? 0}
              unit="单"
              icon={<AlertTriangle className="h-4 w-4" />}
              color={(stats?.overdue ?? 0) > 0 ? "danger" : "verified"}
            />
            <KpiCard
              label="已办结"
              value={stats?.resolved ?? 0}
              unit="单"
              icon={<ShieldAlert className="h-4 w-4" />}
              color="brand"
            />
          </>
        )}
      </div>

      {!loading && (stats?.overdue ?? 0) > 0 && (
        <Alert
          variant="warning"
          title={`${stats?.overdue} 单已超过 ${stats?.due_days ?? 15} 天反馈时限`}
        >
          逾期是违约信号，也会在算法备案审查中被直接问到。请优先处理下方标记为红色的工单。
          <button
            type="button"
            className="ml-1 font-medium text-link underline underline-offset-2"
            onClick={() => resetTo(() => setOverdueOnly(true))}
          >
            只看逾期 →
          </button>
        </Alert>
      )}

      {/* ── 列表 ─────────────────────────────────────── */}
      <Card hover={false}>
        <div className="border-b border-line px-4 py-3">
          <FilterBar
            chips={chips}
            onRemove={(id) =>
              resetTo(() => {
                if (id === "status") setStatus("");
                if (id === "type") setType("");
                if (id === "overdue") setOverdueOnly(false);
              })
            }
            onClearAll={clearAll}
            resultCount={total}
          >
            <label className="sr-only" htmlFor="complaint-status">
              按处理状态筛选
            </label>
            <select
              id="complaint-status"
              value={status}
              onChange={(e) => resetTo(() => setStatus(e.target.value))}
              className={SELECT_CLS}
            >
              <option value="">全部状态</option>
              {STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {STATUS_META[s].label}
                </option>
              ))}
            </select>

            <label className="sr-only" htmlFor="complaint-type">
              按投诉类型筛选
            </label>
            <select
              id="complaint-type"
              value={type}
              onChange={(e) => resetTo(() => setType(e.target.value))}
              className={SELECT_CLS}
            >
              <option value="">全部类型</option>
              {Object.entries(TYPE_LABEL).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>

            <label className="flex cursor-pointer items-center gap-1.5 text-body-sm text-ink-700">
              <input
                type="checkbox"
                checked={overdueOnly}
                onChange={(e) => resetTo(() => setOverdueOnly(e.target.checked))}
                className="h-4 w-4 rounded-r1 border-line accent-brand-600"
              />
              仅逾期未办结
            </label>
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
            emptyMessage={
              chips.length > 0 ? "没有符合当前筛选条件的工单" : "暂无投诉举报工单"
            }
          />
        )}

        {total > 0 && (
          <div className="border-t border-line px-4 py-3">
            {/* 有界计数：超过 COUNT_CAP 时 total 是下界，不能显示成精确值 */}
            {lowerBound && (
              <p className="mb-2 text-caption text-ink-400">
                匹配工单超过计数上限，总数显示为 <span className="num">{total}+</span>（下界）。
              </p>
            )}
            <Pagination
              page={page}
              pageSize={PAGE_SIZE}
              total={total}
              onPageChange={setPage}
            />
          </div>
        )}
      </Card>

      {/* ── 处理弹窗 ─────────────────────────────────── */}
      <Modal
        isOpen={target !== null}
        onClose={closeModal}
        size="lg"
        title={target ? `工单 ${target.ticket_no}` : ""}
        description={
          target
            ? `${TYPE_LABEL[target.type] ?? target.type} · ${STATUS_META[target.status]?.label ?? target.status} · 租户 ${target.tenant_id}`
            : undefined
        }
      >
        {target && (
          <div className="space-y-4">
            <div>
              <h4 className="text-caption text-ink-500">问题描述</h4>
              <p className="mt-1 whitespace-pre-wrap rounded-r2 border border-line bg-surface-subtle p-3 text-body-sm text-ink-800">
                {target.description}
              </p>
            </div>

            <dl className="grid gap-x-6 gap-y-2 text-body-sm sm:grid-cols-2">
              <div>
                <dt className="text-caption text-ink-500">提交时间</dt>
                <dd className="num text-ink-800">{fmtDate(target.created_at)}</dd>
              </div>
              <div>
                <dt className="text-caption text-ink-500">反馈期限</dt>
                <dd className="num text-ink-800">{fmtDate(target.due_at)}</dd>
              </div>
              <div>
                <dt className="text-caption text-ink-500">联系方式</dt>
                <dd className="text-ink-800">{target.contact || "（匿名，未留）"}</dd>
              </div>
              <div>
                <dt className="text-caption text-ink-500">关联对象</dt>
                <dd className="num text-ink-800">
                  {target.target_type
                    ? `${target.target_type} #${target.target_id ?? "—"}`
                    : "—"}
                  {target.related_moderation_id
                    ? ` · 审核记录 #${target.related_moderation_id}`
                    : ""}
                </dd>
              </div>
              <div>
                <dt className="text-caption text-ink-500">提交 IP</dt>
                <dd className="num text-ink-600">{target.ip_address || "—"}</dd>
              </div>
              <div>
                <dt className="text-caption text-ink-500">处理人</dt>
                <dd className="num text-ink-600">
                  {target.handler_id ? `用户 #${target.handler_id}` : "未处理"}
                </dd>
              </div>
            </dl>

            {target.handle_note && (
              <div>
                <h4 className="text-caption text-ink-500">已有处理结论</h4>
                <p className="mt-1 rounded-r2 border border-line bg-surface-subtle p-3 text-body-sm text-ink-700">
                  {target.handle_note}
                </p>
              </div>
            )}

            {/* 终态：只读展示，不提供任何再处理入口 */}
            {(ACTIONS[target.status] ?? []).length === 0 ? (
              <Alert variant="success" title="该工单已进入终态">
                已{STATUS_META[target.status]?.label ?? target.status}
                {target.handled_at ? `（${fmtDate(target.handled_at)}）` : ""}，不再提供处理入口。
                如需重新处理，应由投诉人凭原工单号再次提交。
              </Alert>
            ) : (
              <div className="space-y-3 rounded-r2 border border-line p-3.5">
                <h4 className="text-body-sm font-medium text-ink-800">处理动作</h4>

                <div className="flex flex-wrap gap-2">
                  {(ACTIONS[target.status] ?? []).map((a) => {
                    const active = action?.status === a.status;
                    return (
                      <button
                        key={a.status}
                        type="button"
                        onClick={() => {
                          setAction(a);
                          setFormError("");
                        }}
                        className={
                          "rounded-r2 border px-3 py-1.5 text-body-sm transition-colors duration-fast " +
                          (active
                            ? "border-brand-500 bg-brand-500/10 font-medium text-link"
                            : "border-line text-ink-600 hover:bg-surface-subtle")
                        }
                      >
                        {a.label}
                      </button>
                    );
                  })}
                </div>

                {action && (
                  <>
                    <div>
                      <label htmlFor="handle-note" className="mb-1.5 block text-body-sm text-ink-700">
                        处理结论
                        {action.needsNote ? (
                          <span className="ml-1 text-danger-600">（必填，不少于 {MIN_NOTE} 字）</span>
                        ) : (
                          <span className="ml-1 text-ink-400">（选填）</span>
                        )}
                      </label>
                      <textarea
                        id="handle-note"
                        value={note}
                        onChange={(e) => {
                          setNote(e.target.value);
                          setFormError("");
                        }}
                        rows={3}
                        placeholder={
                          action.needsNote
                            ? "写明核实过程与结论——这段文字会作为反馈内容发给投诉人"
                            : "可记录受理人、预计处理方式"
                        }
                        className="w-full rounded-r2 border border-line bg-surface px-3 py-2 text-body-sm text-ink-800 placeholder:text-ink-400 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-500/30"
                      />
                      {action.needsNote && (
                        <p className="mt-1 text-caption text-ink-400">
                          已输入 <span className="num">{note.trim().length}</span> / {MIN_NOTE} 字
                        </p>
                      )}
                    </div>

                    {formError && (
                      <p role="alert" className="text-body-sm text-danger-600">
                        {formError}
                      </p>
                    )}

                    <div className="flex items-center gap-2">
                      <Button variant="primary" onClick={() => void submit()} disabled={submitting}>
                        {submitting ? <Spinner size="sm" label={null} /> : null}
                        确认{action.label}
                      </Button>
                      <Button variant="ghost" onClick={closeModal} disabled={submitting}>
                        取消
                      </Button>
                      <p className="ml-auto text-caption text-ink-400">
                        处理动作会写入审计日志
                      </p>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}
