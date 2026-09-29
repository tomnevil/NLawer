"use client";

import React from "react";
import { Hand, Inbox, RefreshCw, Send, UserCheck } from "lucide-react";
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
  cn,
  type BadgeProps,
  type DataTableColumn,
  type FilterChip,
} from "@nlaw/ui";

/* ────────────────────────── 后端契约 ────────────────────────── */

/** `app/schemas/case.py::DispatchOut`。注意：无时间字段，故不显示时间。 */
interface Dispatch {
  id: number;
  case_id: number;
  lawyer_id?: number | null;
  mode: string;
  status: string;
  score?: number | null;
  reason?: string | null;
}

interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
  total_is_lower_bound?: boolean;
}

/* ────────────────────────── 常量映射 ────────────────────────── */

/** `DispatchMode`（PRD 5.2 三种派单方式）。 */
const MODE_META: Record<string, { label: string; tone: BadgeProps["variant"] }> = {
  DESIGNATED: { label: "客户指定", tone: "primary" },
  AUTO: { label: "系统自动", tone: "info" },
  POOL: { label: "律师抢单", tone: "pending" },
};

/** `DispatchStatus`。 */
const STATUS_META: Record<string, { label: string; tone: BadgeProps["variant"] }> = {
  PENDING: { label: "待接单", tone: "pending" },
  ACCEPTED: { label: "已接单", tone: "verified" },
  REJECTED: { label: "已拒绝", tone: "danger" },
  EXPIRED: { label: "已过期", tone: "neutral" },
};

const MODE_OPTIONS = Object.keys(MODE_META);
const STATUS_OPTIONS = Object.keys(STATUS_META);

const PAGE_SIZE = 20;

function scoreTone(score?: number | null): string {
  if (score === undefined || score === null) return "text-ink-400";
  if (score >= 0.8) return "text-verified-600";
  if (score >= 0.5) return "text-pending-600";
  return "text-danger-600";
}

/* ────────────────────────── 页面 ────────────────────────── */

/**
 * 运营后台·派单记录（**只读**）。
 *
 * ## 🚨 为什么这一项叫「派单记录」而不是概念图里的「派单规则」
 *
 * 概念图 `mockups/04-admin-cockpit.html` 把这一项写作「派单规则」，
 * 但后端在 `app/api/v1/` 下**没有任何 `DispatchRule` 端点**（读、写都没有）。
 *
 * 更值得注意的是——**规则引擎是活的，只是看不见也配不了**：
 *
 * - 模型 `DispatchRule`（`app/models/case.py:112`）字段完整：
 *   `conditions` JSON（案由 / 等级 / 执业年限）、`strategy`、
 *   `candidate_lawyer_ids` 白名单、`priority`、`enabled`。
 * - 工作流 `app/workflows/dispatch_rules.py` 的 `resolve_strategy()` 在
 *   **每一次 AUTO 派单时都会被调用**（`DispatchService` → `load_rules` → `pick_rule`）。
 * - **但**：`load_rules` 查不到任何规则行（种子未造 `DispatchRule`），
 *   于是 `resolve_strategy` 一律返回兜底值 `"SPECIALTY_MATCH"`。
 *
 * ⇒ 结论：**「派单规则配置」在后端冻结期间做不了（无端点）；
 * 且当前所有 AUTO 派单实际都走同一套兜底策略**。这是产品问题，不是前端可补的。
 * 本页只做「派单记录」的监督，并在页面上把这个结论写出来。
 *
 * ## 为什么只读
 *
 * `accept` / `grab` 是**律师接单**的动作，会改变案件责任人并触发案件分析任务。
 * 平台运营方在后台替律师接单没有合理场景，且 §08.1 把运营后台限定为只读。
 */
export default function AdminDispatchesPage() {
  const [rows, setRows] = React.useState<Dispatch[]>([]);
  const [total, setTotal] = React.useState(0);
  const [pages, setPages] = React.useState(1);
  const [lowerBound, setLowerBound] = React.useState(false);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [counts, setCounts] = React.useState<Record<string, number> | null>(null);

  // 两者都是**服务端**参数（实测确认）：?status= / ?mode=
  const [status, setStatus] = React.useState("");
  const [mode, setMode] = React.useState("");
  const [page, setPage] = React.useState(1);

  const [detail, setDetail] = React.useState<Dispatch | null>(null);
  const [scope, setScope] = React.useState<string | null>(null);
  React.useEffect(() => setScope(tenantScope.get()), []);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [list, pending, accepted, pool] = await Promise.all([
        authed<Paged<Dispatch>>("/api/v1/dispatches", {
          query: { page, page_size: PAGE_SIZE, status: status || undefined, mode: mode || undefined },
        }),
        authed<Paged<Dispatch>>("/api/v1/dispatches", {
          query: { page: 1, page_size: 1, status: "PENDING" },
        }),
        authed<Paged<Dispatch>>("/api/v1/dispatches", {
          query: { page: 1, page_size: 1, status: "ACCEPTED" },
        }),
        // 抢单池：`/dispatches/pool` 的口径是「待接 且 (未指定律师 或 POOL 模式)」
        authed<Paged<Dispatch>>("/api/v1/dispatches/pool", { query: { page: 1, page_size: 1 } }),
      ]);
      setRows(list?.items ?? []);
      setTotal(list?.total ?? 0);
      setPages(list?.pages ?? 1);
      setLowerBound(Boolean(list?.total_is_lower_bound));
      setCounts({
        pending: pending?.total ?? 0,
        accepted: accepted?.total ?? 0,
        pool: pool?.total ?? 0,
      });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return;
      setError(e instanceof ApiError ? e.message : "加载失败");
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [page, status, mode]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const resetTo = (fn: () => void) => {
    fn();
    setPage(1);
  };

  const chips: FilterChip[] = [];
  if (status) chips.push({ id: "status", label: "状态", value: STATUS_META[status]?.label ?? status });
  if (mode) chips.push({ id: "mode", label: "方式", value: MODE_META[mode]?.label ?? mode });

  const clearAll = () =>
    resetTo(() => {
      setStatus("");
      setMode("");
    });

  const columns: DataTableColumn<Dispatch>[] = React.useMemo(
    () => [
      {
        key: "id",
        header: "派单号",
        numeric: true,
        width: "96px",
        mobile: "primary",
        // 详情入口挂 primary 列：DataTable 卡片模式会切掉末尾的操作列
        render: (_v, row) => (
          <button
            type="button"
            onClick={() => setDetail(row)}
            className="num font-medium text-link underline-offset-2 hover:underline"
          >
            #{row.id}
          </button>
        ),
      },
      {
        key: "case_id",
        header: "案件",
        numeric: true,
        width: "88px",
        render: (_v, row) => <span className="num">#{row.case_id}</span>,
      },
      {
        key: "lawyer_id",
        header: "承办律师",
        numeric: true,
        width: "104px",
        render: (_v, row) =>
          row.lawyer_id ? (
            <span className="num">#{row.lawyer_id}</span>
          ) : (
            <span className="text-caption text-ink-400">待分配</span>
          ),
      },
      {
        key: "mode",
        header: "派单方式",
        width: "116px",
        render: (_v, row) => (
          <Badge variant={MODE_META[row.mode]?.tone ?? "neutral"}>
            {MODE_META[row.mode]?.label ?? row.mode}
          </Badge>
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
        key: "score",
        header: "匹配度",
        numeric: true,
        width: "88px",
        align: "right",
        render: (_v, row) =>
          row.score === undefined || row.score === null ? (
            <span className="text-ink-400">—</span>
          ) : (
            <span className={cn("num", scoreTone(row.score))}>{row.score.toFixed(2)}</span>
          ),
      },
    ],
    [],
  );

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-h1 text-ink-900">派单记录</h1>
          <p className="mt-1 text-body-sm text-ink-500">
            案件派单与接单情况。数据范围跟随顶部「租户视角」。
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
          接单 / 抢单是<strong className="font-medium text-ink-600">律师侧动作</strong>
          （会改变案件责任人并触发案件分析），本页不提供入口。
        </p>
      </div>

      {/*
        这一条是本次审计最值得说的发现，必须放在显眼位置：
        概念图里的「派单规则」目前是「能跑、但看不见也配不了」的状态。
      */}
      <Alert variant="warning" title="「派单规则配置」当前不可用：规则引擎在跑，但无端点也无规则数据">
        <ul className="list-disc space-y-1 pl-4">
          <li>
            <code>DispatchRule</code> 模型字段完整（条件 JSON / 策略 / 候选白名单 /
            优先级 / 启用开关），<code>app/workflows/dispatch_rules.py</code> 的
            <code>resolve_strategy()</code> 在<strong className="font-medium">每一次 AUTO 派单时都会被调用</strong>。
          </li>
          <li>
            但 <code>app/api/v1/</code> 下<strong className="font-medium">没有任何规则端点</strong>
            （读、写都没有），因此规则无法查看也无法配置。
          </li>
          <li>
            且种子未造任何规则行 ⇒ <code>load_rules()</code> 返回空 ⇒
            所有 AUTO 派单<strong className="font-medium">实际都走兜底策略 SPECIALTY_MATCH</strong>。
          </li>
        </ul>
        <p className="mt-2 text-caption text-ink-500">
          后端已冻结，本轮只登记不改动。本页因此只做「派单记录」的监督，
          菜单项也按实际能力命名为「派单记录」而非「派单规则」。
        </p>
      </Alert>

      {/* ── KPI ─────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {loading && !counts ? (
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[92px] w-full" />)
        ) : (
          <>
            <KpiCard
              label="派单总数"
              value={total}
              unit="单"
              icon={<Send className="h-4 w-4" />}
              color="brand"
            />
            <KpiCard
              label="待接单"
              value={counts?.pending ?? 0}
              unit="单"
              icon={<Inbox className="h-4 w-4" />}
              color={(counts?.pending ?? 0) > 0 ? "pending" : "verified"}
            />
            <KpiCard
              label="已接单"
              value={counts?.accepted ?? 0}
              unit="单"
              icon={<UserCheck className="h-4 w-4" />}
              color="verified"
            />
            <KpiCard
              label="抢单池"
              value={counts?.pool ?? 0}
              unit="单"
              icon={<Hand className="h-4 w-4" />}
              color={(counts?.pool ?? 0) > 0 ? "pending" : "verified"}
            />
          </>
        )}
      </div>

      <p className="-mt-3 text-caption text-ink-400">
        「抢单池」来自 <code>GET /dispatches/pool</code>，口径是
        <strong className="font-normal">「待接单 且（未指定律师 或 POOL 模式）」</strong>
        ，与「待接单」不是同一集合，两者数字不同是正常的。
      </p>

      {/* ── 列表 ────────────────────────────────────── */}
      <Card hover={false}>
        <div className="border-b border-line px-4 py-3">
          <FilterBar
            chips={chips}
            onRemove={(id) =>
              resetTo(() => {
                if (id === "status") setStatus("");
                if (id === "mode") setMode("");
              })
            }
            onClearAll={clearAll}
            resultCount={total}
          >
            <label className="sr-only" htmlFor="disp-status">
              按派单状态筛选
            </label>
            <select
              id="disp-status"
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

            <label className="sr-only" htmlFor="disp-mode">
              按派单方式筛选
            </label>
            <select
              id="disp-mode"
              value={mode}
              onChange={(e) => resetTo(() => setMode(e.target.value))}
              className={cn(
                "h-9 rounded-r2 border border-line bg-surface px-2.5 text-body-sm text-ink-800",
                "transition-colors duration-fast focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-500/30",
              )}
            >
              <option value="">全部方式</option>
              {MODE_OPTIONS.map((m) => (
                <option key={m} value={m}>
                  {MODE_META[m].label}
                </option>
              ))}
            </select>
          </FilterBar>

          <p className="mt-2 text-caption text-ink-400">
            <code>status</code> 与 <code>mode</code> 均为<strong className="font-normal">服务端</strong>
            参数；派单无时间字段对外暴露，故不显示时间。
          </p>
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
              chips.length > 0
                ? "没有符合当前筛选条件的派单记录"
                : "当前租户下暂无派单记录（派单是租户侧业务动作，平台共享域本身不会有）"
            }
          />
        )}

        {total > 0 && (
          <div className="border-t border-line px-4 py-3">
            {lowerBound && (
              <p className="mb-2 text-caption text-ink-400">
                匹配派单超过计数上限，总数显示为 <span className="num">{total}+</span>（下界）。
              </p>
            )}
            <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
          </div>
        )}
      </Card>

      {/* ── 详情 ────────────────────────────────────── */}
      <Modal
        isOpen={detail !== null}
        onClose={() => setDetail(null)}
        size="md"
        title={detail ? `派单 #${detail.id}` : ""}
        description={detail ? `案件 #${detail.case_id}` : undefined}
      >
        {detail && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={STATUS_META[detail.status]?.tone ?? "neutral"}>
                {STATUS_META[detail.status]?.label ?? detail.status}
              </Badge>
              <Badge variant={MODE_META[detail.mode]?.tone ?? "neutral"}>
                {MODE_META[detail.mode]?.label ?? detail.mode}
              </Badge>
              {detail.score !== undefined && detail.score !== null && (
                <span className={cn("num ml-auto text-body-sm font-medium", scoreTone(detail.score))}>
                  匹配度 {detail.score.toFixed(2)}
                </span>
              )}
            </div>

            <dl className="grid gap-x-6 gap-y-2 text-body-sm sm:grid-cols-2">
              <div>
                <dt className="text-caption text-ink-500">关联案件</dt>
                <dd className="num text-ink-800">#{detail.case_id}</dd>
              </div>
              <div>
                <dt className="text-caption text-ink-500">承办律师</dt>
                <dd className="num text-ink-800">
                  {detail.lawyer_id ? `#${detail.lawyer_id}` : "待分配"}
                </dd>
              </div>
            </dl>

            <div>
              <h4 className="mb-1.5 text-caption text-ink-500">派单理由</h4>
              <p className="rounded-r2 border border-line bg-surface-subtle p-3 text-body-sm text-ink-700">
                {detail.reason || "（无）"}
              </p>
              {/*
                实测发现（verify_dispatches.py F.1）：reason 从不反映该条派单的实际依据——
                AUTO 与 DESIGNATED 是同一句「演示数据：按专业领域匹配」，POOL 则为 NULL。
                指定派单本不该有「按专业领域匹配」的理由——这是演示数据的瑕疵，
                不是产品缺陷，但它会让人误读匹配度与理由的因果关系。
                因此在指定派单上显式标注，避免用户把它当成系统真实输出。
              */}
              {detail.mode === "DESIGNATED" && detail.reason && (
                <p className="mt-1.5 text-caption text-pending-600">
                  注意：这是「客户指定」派单，但理由文字描述的是自动匹配。
                  当前数据的「派单理由」只有这一句固定文案（抢单池派单则为空），
                  不代表真实派单逻辑。
                </p>
              )}
            </div>

            <p className="text-caption text-ink-400">
              后端无派单详情端点，以上信息来自列表行（<code>DispatchOut</code>），
              因此不包含创建/接单时间。
            </p>
          </div>
        )}
      </Modal>
    </div>
  );
}
