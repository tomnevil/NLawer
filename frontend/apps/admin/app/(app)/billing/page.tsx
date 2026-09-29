"use client";

import React from "react";
import { AlertTriangle, CircleDollarSign, Clock, FileText, Receipt, RefreshCw } from "lucide-react";
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
  Skeleton,
  Spinner,
  cn,
  type BadgeProps,
  type DataTableColumn,
  type FilterChip,
} from "@nlaw/ui";

/* ────────────────────────── 后端契约 ────────────────────────── */

/**
 * `GET /billing/dashboard` → `BillingService.dashboard()`。
 *
 * ⚠️ 形状是**自定义 dict，不是 `Page` 对象**，且三个字段的语义完全不同：
 * - `quotas` —— **按账期**查询（`UsageQuota.period`），切账期会变
 * - `work_orders` —— **不按账期**过滤（`list_work_orders` 只收 `status`）
 * 混在一起放在一个端点里，但**只有一半响应受账期影响**，
 * 这一点必须在界面上说清楚，否则用户切了账期会以为工单数也跟着变了。
 */
interface Dashboard {
  period: string;
  quotas: Quota[];
  work_orders: { total: number; pending: number; amount_cents: number };
}

interface Quota {
  usage_type: string;
  used: number;
  limit: number;
  remaining: number;
  percent: number;
}

/** `WorkOrderOut`。注意：**没有时间字段**，故本页不显示时间。 */
interface WorkOrder {
  id: number;
  order_no: string;
  tenant_id: string;
  usage_type: string;
  title: string;
  status: string;
  urgent: boolean;
  price_cents: number;
  escalate_to_lawyer: boolean;
  ref_type?: string | null;
  ref_id?: number | null;
  billing_note?: Record<string, unknown> | null;
}

/* ────────────────────────── 常量映射 ────────────────────────── */

/** `UsageType`。 */
const USAGE_LABEL: Record<string, string> = {
  QA: "智能咨询",
  DOCUMENT: "文书生成",
  CONTRACT_REVIEW: "合同审查",
  COMPLIANCE_SCAN: "合规扫描",
};

/** `WorkOrderStatus`。 */
const STATUS_META: Record<string, { label: string; tone: BadgeProps["variant"] }> = {
  PENDING: { label: "待处理", tone: "pending" },
  PROCESSING: { label: "处理中", tone: "info" },
  COMPLETED: { label: "已完成", tone: "verified" },
  CANCELLED: { label: "已取消", tone: "neutral" },
};

const STATUS_OPTIONS = Object.keys(STATUS_META);

/**
 * 分位转元。后端一律用 `*_cents`，前端一律按元展示。
 *
 * 刻意**不用** `style: "currency"`：Node 与浏览器的 ICU 数据可能不同
 * （如 `¥` vs `CN¥`），一旦该值参与预渲染就会造成 hydration mismatch。
 * 这里只让 `toLocaleString` 处理千分位，货币符号自己拼，结果完全确定。
 */
function yuan(cents?: number): string {
  if (cents === undefined || cents === null) return "—";
  return `¥${(cents / 100).toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

/**
 * 额度占用色阶。
 * 阈值与「额度预警」类产品的通行做法一致：≥90% 红、≥70% 琥珀、其余绿。
 */
function quotaTone(percent: number): string {
  if (percent >= 90) return "bg-danger-500";
  if (percent >= 70) return "bg-pending-500";
  return "bg-verified-500";
}

/** 生成最近 12 个账期（含当月），格式 `YYYY-MM`，与后端 `period` 一致。 */
function recentPeriods(today: Date, n = 12): string[] {
  const out: string[] = [];
  for (let i = 0; i < n; i++) {
    const d = new Date(today.getFullYear(), today.getMonth() - i, 1);
    out.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`);
  }
  return out;
}

/* ────────────────────────── 页面 ────────────────────────── */

/**
 * 运营后台·计费与工单（**只读**）。
 *
 * ## 为什么只读
 *
 * - `POST /billing/consume` 是**扣减用量**的动作，会真实消耗租户额度，
 *   属于业务操作而非运营监督——在后台放一个「帮租户扣一次量」的按钮没有合理场景。
 * - `POST /billing/project-revenue` 是**纯计算**端点（分项系数法），
 *   输入四个金额、返回预测值。它是给律所自己估算收入用的计算器，
 *   **不是运营指标**，放在平台后台会让人误以为那是平台实际收入。
 *
 * 因此本页只接两个读端点。
 *
 * ## ⚠️ 实测得到的三条数据事实（决定了界面怎么写）
 *
 * 1. **平台管理员默认视角（`platform`）没有任何套餐额度**——
 *    `UsageQuota` 只在业务种子里为 `firm_hlw` 创建（`seed/business.py:224`），
 *    `platform` 是共享域、没有订阅套餐。实测 `quotas=0`、`work_orders.total=3`。
 *    ⇒ 额度区必须能优雅空态，且**要说明原因**，不能只显示一片空白。
 * 2. **`work_orders` 不受账期参数影响**（`list_work_orders` 只收 `status`）。
 *    ⇒ 切账期时只有额度区会变，必须写明，否则像 bug。
 * 3. **金额字段是分**（`price_cents` / `amount_cents`）。
 *    ⇒ 展示前必须 ÷100，直接显示会把 ¥327 显示成 `32700`。
 */
export default function AdminBillingPage() {
  const [dash, setDash] = React.useState<Dashboard | null>(null);
  const [orders, setOrders] = React.useState<WorkOrder[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");

  // 账期：`?period=YYYY-MM`，空 = 后端取当期
  const [period, setPeriod] = React.useState("");
  const periods = React.useMemo(() => recentPeriods(new Date()), []);

  // 工单状态：服务端参数（后端支持 ?status=）
  const [status, setStatus] = React.useState("");

  const [detail, setDetail] = React.useState<WorkOrder | null>(null);
  const [scope, setScope] = React.useState<string | null>(null);
  React.useEffect(() => setScope(tenantScope.get()), []);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [d, w] = await Promise.all([
        authed<Dashboard>("/api/v1/billing/dashboard", {
          query: { period: period || undefined },
        }),
        authed<WorkOrder[]>("/api/v1/billing/work-orders", {
          query: { status: status || undefined },
        }),
      ]);
      setDash(d);
      setOrders(Array.isArray(w) ? w : []);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return;
      setError(
        e instanceof ApiError && e.status === 403
          ? "当前账号无 billing:read 权限"
          : e instanceof ApiError
            ? e.message
            : "加载失败",
      );
      setDash(null);
      setOrders([]);
    } finally {
      setLoading(false);
    }
  }, [period, status]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const chips: FilterChip[] = [];
  if (period)
    chips.push({
      id: "period",
      label: "账期",
      value: period,
    });
  if (status)
    chips.push({ id: "status", label: "状态", value: STATUS_META[status]?.label ?? status });

  const clearAll = () => {
    setPeriod("");
    setStatus("");
  };

  /** 金额合计（按当前筛选后的工单算，与后端 `amount_cents` 口径不同，需说明）。 */
  const shownCents = orders.reduce((n, o) => n + (o.price_cents ?? 0), 0);

  const columns: DataTableColumn<WorkOrder>[] = React.useMemo(
    () => [
      {
        key: "order_no",
        header: "工单号",
        mobile: "primary",
        width: "168px",
        // 详情入口挂 primary 列：DataTable 卡片模式会切掉末尾的操作列
        render: (_v, row) => (
          <button
            type="button"
            onClick={() => setDetail(row)}
            className="num text-left font-medium text-link underline-offset-2 hover:underline"
          >
            {row.order_no}
          </button>
        ),
      },
      {
        key: "title",
        header: "工单标题",
        render: (_v, row) => (
          <span className="flex flex-wrap items-center gap-1.5">
            <span className="truncate text-ink-800">{row.title}</span>
            {row.urgent && <Badge variant="danger" size="sm">加急</Badge>}
            {row.escalate_to_lawyer && <Badge variant="pending" size="sm">转律师</Badge>}
          </span>
        ),
      },
      {
        key: "usage_type",
        header: "类型",
        width: "104px",
        render: (_v, row) => (
          <span className="text-caption text-ink-600">
            {USAGE_LABEL[row.usage_type] ?? row.usage_type}
          </span>
        ),
      },
      {
        key: "status",
        header: "状态",
        width: "96px",
        mobile: "status",
        render: (_v, row) => (
          <Badge variant={STATUS_META[row.status]?.tone ?? "neutral"}>
            {STATUS_META[row.status]?.label ?? row.status}
          </Badge>
        ),
      },
      {
        key: "price_cents",
        header: "金额",
        numeric: true,
        width: "112px",
        align: "right",
        render: (_v, row) => <span className="num">{yuan(row.price_cents)}</span>,
      },
      {
        key: "_actions",
        header: "操作",
        width: "88px",
        align: "right",
        render: (_v, row) => (
          <Button variant="outline" size="sm" onClick={() => setDetail(row)}>
            详情
          </Button>
        ),
      },
    ],
    [],
  );

  const noteEntries = detail?.billing_note
    ? Object.entries(detail.billing_note).filter(([, v]) => v !== null && v !== undefined)
    : [];

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-h1 text-ink-900">计费与工单</h1>
          <p className="mt-1 text-body-sm text-ink-500">
            用量额度与增值工单。数据范围跟随顶部「租户视角」。
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

      {/* 只读声明 */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-r2 border border-line bg-surface-subtle px-3.5 py-2.5">
        <Badge variant="neutral">只读监控</Badge>
        <Badge variant={scope ? "primary" : "neutral"}>
          {scope ? `租户 ${scope}` : "账号自身租户"}
        </Badge>
        <p className="min-w-0 flex-1 text-caption text-ink-500">
          本页仅查看，
          <strong className="font-medium text-ink-600">不提供扣减用量或收入预测的入口</strong>
          ——扣减用量是业务动作；收入预测是律所端自己的估算工具，不是平台实际收入。
        </p>
      </div>

      {/* ── 账期说明（端点语义不对称，必须说清）───────────── */}
      <Alert variant="info" title="账期切换只影响「用量额度」，不影响工单列表">
        后端把两者放在同一个端点，但 <code>work_orders</code> 是
        <strong className="font-medium">全量统计</strong>（
        <code>list_work_orders</code> 只支持 <code>status</code>，没有账期参数）。
        切换账期时工单数不变是<strong className="font-medium">预期行为</strong>，不是故障。
      </Alert>

      {/* ── KPI ─────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {loading && !dash ? (
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[92px] w-full" />)
        ) : (
          <>
            <KpiCard
              label="工单总数"
              value={dash?.work_orders?.total ?? 0}
              unit="单"
              icon={<Receipt className="h-4 w-4" />}
              color="brand"
            />
            <KpiCard
              label="待处理"
              value={dash?.work_orders?.pending ?? 0}
              unit="单"
              icon={<Clock className="h-4 w-4" />}
              color={(dash?.work_orders?.pending ?? 0) > 0 ? "pending" : "verified"}
            />
            <KpiCard
              label="工单金额合计"
              value={yuan(dash?.work_orders?.amount_cents ?? 0)}
              icon={<CircleDollarSign className="h-4 w-4" />}
              color="gold"
            />
            <KpiCard
              label="当前筛选金额"
              value={yuan(shownCents)}
              icon={<FileText className="h-4 w-4" />}
              color="info"
            />
          </>
        )}
      </div>

      <p className="-mt-3 text-caption text-ink-400">
        「工单金额合计」是后端对<strong>全部</strong>工单的统计（不受状态筛选影响）；
        「当前筛选金额」是本页对已筛出 {orders.length} 条工单求和，两者口径不同。
      </p>

      {/* ── 用量额度 ─────────────────────────────────── */}
      <section className="rounded-r3 border border-line bg-surface p-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-body-sm font-semibold text-ink-800">
            用量额度
            {dash?.period && (
              <span className="num ml-2 font-normal text-ink-500">{dash.period}</span>
            )}
          </h2>
        </div>

        {loading && !dash ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-[86px] w-full" />
            ))}
          </div>
        ) : (dash?.quotas ?? []).length === 0 ? (
          /*
            空态必须解释原因。实测：platform（平台管理员默认租户）查不到任何额度记录，
            因为它是共享数据域、没有订阅套餐——不是「系统没数据」。
          */
          <div className="rounded-r2 border border-dashed border-line bg-surface-subtle p-4">
            <p className="flex items-start gap-2 text-body-sm text-ink-600">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-pending-600" />
              <span>
                当前租户在{dash?.period ? ` ${dash.period} ` : " "}账期
                <strong className="font-medium text-ink-800">没有套餐额度记录</strong>。
                <br />
                <span className="text-caption text-ink-500">
                  额度按「租户 + 账期」存储。平台共享域（
                  <code>platform</code>）本身没有订阅套餐，
                  因此默认视角下这里为空；切换到有套餐的业务租户即可看到额度。
                  这不代表系统缺数据。
                </span>
              </span>
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {(dash?.quotas ?? []).map((q) => (
              <div key={q.usage_type} className="rounded-r2 border border-line p-3.5">
                <div className="flex items-baseline justify-between">
                  <span className="text-body-sm font-medium text-ink-800">
                    {USAGE_LABEL[q.usage_type] ?? q.usage_type}
                  </span>
                  <span className="num text-caption text-ink-500">{q.percent}%</span>
                </div>
                <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-surface-subtle">
                  <div
                    className={cn("h-full rounded-full transition-all", quotaTone(q.percent))}
                    style={{ width: `${Math.min(100, Math.max(0, q.percent))}%` }}
                  />
                </div>
                <p className="num mt-2 text-caption text-ink-500">
                  已用 {q.used} / {q.limit}
                  <span className="ml-2 text-ink-400">剩余 {q.remaining}</span>
                </p>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* ── 工单列表 ─────────────────────────────────── */}
      <Card hover={false}>
        <div className="border-b border-line px-4 py-3">
          <FilterBar
            chips={chips}
            onRemove={(id) => {
              if (id === "period") setPeriod("");
              if (id === "status") setStatus("");
            }}
            onClearAll={clearAll}
            resultCount={orders.length}
          >
            <label className="sr-only" htmlFor="bill-period">
              选择账期
            </label>
            <select
              id="bill-period"
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
              className={cn(
                "h-9 rounded-r2 border border-line bg-surface px-2.5 text-body-sm text-ink-800",
                "transition-colors duration-fast focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-500/30",
              )}
            >
              <option value="">当期（本月）</option>
              {periods.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>

            <label className="sr-only" htmlFor="bill-status">
              按工单状态筛选
            </label>
            <select
              id="bill-status"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
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
          </FilterBar>

          <p className="mt-2 text-caption text-ink-400">
            后端 <code>GET /billing/work-orders</code> 返回全量裸数组（无分页），
            仅 <code>status</code> 为服务端参数；工单无时间字段对外暴露，故不显示时间。
          </p>
        </div>

        {loading && orders.length === 0 ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : (
          <DataTable
            columns={columns}
            data={orders}
            rowKey={(r) => String(r.id)}
            emptyMessage={
              status
                ? `没有${STATUS_META[status]?.label ?? ""}状态的工单`
                : "当前租户下暂无工单"
            }
          />
        )}
      </Card>

      {/* ── 工单详情 ─────────────────────────────────── */}
      <Modal
        isOpen={detail !== null}
        onClose={() => setDetail(null)}
        size="md"
        title={detail?.title ?? ""}
        description={detail ? `工单号 ${detail.order_no}` : undefined}
      >
        {detail && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={STATUS_META[detail.status]?.tone ?? "neutral"}>
                {STATUS_META[detail.status]?.label ?? detail.status}
              </Badge>
              {detail.urgent && <Badge variant="danger">加急</Badge>}
              {detail.escalate_to_lawyer && <Badge variant="pending">已转律师</Badge>}
              <span className="num ml-auto text-body-sm font-medium text-ink-900">
                {yuan(detail.price_cents)}
              </span>
            </div>

            <dl className="grid gap-x-6 gap-y-2 text-body-sm sm:grid-cols-2">
              <div>
                <dt className="text-caption text-ink-500">用量类型</dt>
                <dd className="text-ink-800">
                  {USAGE_LABEL[detail.usage_type] ?? detail.usage_type}
                </dd>
              </div>
              <div>
                <dt className="text-caption text-ink-500">所属租户</dt>
                <dd className="num text-ink-800">{detail.tenant_id}</dd>
              </div>
              <div>
                <dt className="text-caption text-ink-500">关联对象</dt>
                <dd className="num text-ink-800">
                  {detail.ref_type ? `${detail.ref_type} #${detail.ref_id ?? "—"}` : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-caption text-ink-500">工单编号</dt>
                <dd className="num text-ink-800">{detail.order_no}</dd>
              </div>
            </dl>

            <div>
              <h4 className="mb-1.5 text-caption text-ink-500">计费明细</h4>
              {noteEntries.length === 0 ? (
                <p className="rounded-r2 border border-line bg-surface-subtle p-3 text-body-sm text-ink-500">
                  该工单没有计费明细记录。
                </p>
              ) : (
                <pre className="overflow-x-auto rounded-r2 border border-line bg-surface-subtle p-3 text-caption leading-relaxed text-ink-700">
                  {JSON.stringify(detail.billing_note, null, 2)}
                </pre>
              )}
            </div>

            <p className="flex items-start gap-1.5 text-caption text-ink-400">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              后端未提供工单详情端点，以上信息均来自列表行（
              <code>WorkOrderOut</code>），因此不包含创建/完成时间。
            </p>
          </div>
        )}
      </Modal>
    </div>
  );
}
