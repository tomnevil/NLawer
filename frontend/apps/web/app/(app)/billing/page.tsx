"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, BarChart3, Receipt, TrendingUp } from "lucide-react";
import { ApiError, authed } from "@nlaw/sdk";
import {
  Badge,
  Button,
  EmptyState,
  KpiCard,
  Skeleton,
  cn,
  useToast,
  type BadgeProps,
} from "@nlaw/ui";

/* ============================================================================
 * 用量与计费
 * ----------------------------------------------------------------------------
 * GET  /api/v1/billing/dashboard       -> {period, quotas[], work_orders{total,pending,amount_cents}}
 * GET  /api/v1/billing/work-orders     -> WorkOrderOut[]（裸数组）
 * POST /api/v1/billing/project-revenue {四个分项（分）} -> {breakdown, weights, total_cents, total_yuan}
 *
 * ⚠️ 本轮修复：`POST /billing/project-revenue` 在旧实现里用
 *     `authed(path, { body })` 调用，method 默认 `GET`，浏览器对
 *     「GET + body」直接抛 `TypeError`——「预测」按钮点了没有任何反应。
 *
 * ⚠️ 另修两处旧样式残留：进度条用了「靛蓝 -> 青蓝」双色渐变
 *     （设计系统 v2 已废弃渐变，改用令牌色块）；`KpiCard` 用了旧色名
 *     `amber` / `emerald`（现改用语义名 `pending` / `verified`）。
 * ========================================================================== */

interface Quota {
  usage_type: string;
  used: number;
  limit: number;
  remaining?: number;
  percent: number;
}

interface Dashboard {
  period?: string;
  quotas: Quota[];
  work_orders: { total: number; pending: number; amount_cents: number };
}

interface WorkOrder {
  id: number;
  order_no: string;
  usage_type: string;
  title: string;
  status: string;
  urgent: boolean;
  price_cents: number;
  escalate_to_lawyer?: boolean;
  billing_note?: Record<string, unknown> | null;
}

interface Revenue {
  breakdown: Record<string, number>;
  total_cents: number;
  total_yuan: number;
}

/** `UsageType` 四态。缺项会漏出裸英文枚举值。 */
const USAGE_LABEL: Record<string, string> = {
  QA: "智能问答",
  DOCUMENT: "文书生成",
  CONTRACT_REVIEW: "合同审查",
  COMPLIANCE_SCAN: "合规扫描",
};

/** `WorkOrderStatus` 四态。 */
const WO_STATUS_LABEL: Record<string, string> = {
  PENDING: "待处理",
  PROCESSING: "处理中",
  COMPLETED: "已完成",
  CANCELLED: "已取消",
};

const WO_STATUS_TONE: Record<string, BadgeProps["variant"]> = {
  PENDING: "pending",
  PROCESSING: "primary",
  COMPLETED: "verified",
  CANCELLED: "neutral",
};

/** 收入分项的显示名。 */
const BREAKDOWN_LABEL: Record<string, string> = {
  subscription: "订阅",
  case_service: "案件服务",
  work_order: "工单",
  value_added: "增值",
};

const yuan = (cents: number) => `¥${(cents / 100).toLocaleString("zh-CN", { maximumFractionDigits: 0 })}`;

function errText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "请求失败";
}

const FIELD_CLS = cn(
  "w-full rounded-r2 border border-line bg-surface px-3 py-2 text-body-sm text-ink-800",
  "placeholder:text-ink-400",
  "transition-colors duration-fast focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-500/30"
);

export default function BillingPage() {
  const { addToast } = useToast();

  const [dash, setDash] = useState<Dashboard | null>(null);
  const [orders, setOrders] = useState<WorkOrder[]>([]);
  const [rev, setRev] = useState<Revenue | null>(null);
  const [inputs, setInputs] = useState({
    subscription_cents: 128000,
    case_service_cents: 36000,
    work_order_cents: 19800,
    value_added_cents: 9000,
  });
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [d, o] = await Promise.allSettled([
        authed<Dashboard>("/api/v1/billing/dashboard"),
        authed<WorkOrder[]>("/api/v1/billing/work-orders"),
      ]);
      if (d.status === "fulfilled") setDash(d.value);
      if (o.status === "fulfilled") setOrders(o.value ?? []);
      // 两个请求都失败才算页面级错误；单边失败保留另一边可用
      if (d.status === "rejected" && o.status === "rejected") {
        setError(errText(d.reason));
      } else if (d.status === "rejected") {
        setError(`用量看板加载失败：${errText(d.reason)}`);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const project = useCallback(async () => {
    setBusy(true);
    try {
      setRev(
        await authed<Revenue>("/api/v1/billing/project-revenue", {
          method: "POST",
          body: inputs,
        })
      );
    } catch (e) {
      addToast({ type: "error", title: "预测失败", message: errText(e) });
    } finally {
      setBusy(false);
    }
  }, [inputs, addToast]);

  const totalUsed = useMemo(
    () => (dash?.quotas ?? []).reduce((a, q) => a + (q.used ?? 0), 0),
    [dash]
  );
  const overQuota = useMemo(
    () => (dash?.quotas ?? []).filter((q) => (q.percent ?? 0) >= 100),
    [dash]
  );
  const nearQuota = useMemo(
    () => (dash?.quotas ?? []).filter((q) => (q.percent ?? 0) >= 80 && (q.percent ?? 0) < 100),
    [dash]
  );

  const PROJECT_FIELDS: { key: keyof typeof inputs; label: string }[] = [
    { key: "subscription_cents", label: "订阅（分）" },
    { key: "case_service_cents", label: "案件服务（分）" },
    { key: "work_order_cents", label: "工单（分）" },
    { key: "value_added_cents", label: "增值（分）" },
  ];

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-h2 text-ink-900">用量与计费</h1>
        <p className="mt-1 text-body-sm text-ink-500">
          {dash?.period ? `${dash.period} 账期 · ` : ""}实时扣减 · 超量自动转工单
        </p>
      </header>

      {error && (
        <div className="rounded-r3 border border-danger-500/30 bg-danger-500/10 p-4">
          <p className="flex items-start gap-2 text-body-sm text-danger-600">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            {error}
          </p>
          <button
            type="button"
            onClick={() => void refresh()}
            className="mt-2 text-body-sm text-link hover:text-link-hover"
          >
            重试
          </button>
        </div>
      )}

      {loading && !dash ? (
        <div className="grid gap-3 sm:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-3">
          <KpiCard
            label="本月已用量"
            value={totalUsed}
            unit="次"
            color="brand"
            icon={<BarChart3 className="h-4 w-4" />}
          />
          <KpiCard
            label="工单总数"
            value={dash?.work_orders.total ?? 0}
            unit="单"
            color={overQuota.length > 0 ? "danger" : "pending"}
            icon={<Receipt className="h-4 w-4" />}
          />
          <KpiCard
            label="工单金额"
            value={yuan(dash?.work_orders.amount_cents ?? 0)}
            color="gold"
            icon={<TrendingUp className="h-4 w-4" />}
          />
        </div>
      )}

      {/* 超量提示：只在实际越界或接近越界时出现，不做常驻装饰 */}
      {(overQuota.length > 0 || nearQuota.length > 0) && (
        <div
          className={cn(
            "rounded-r3 border p-3.5 text-body-sm",
            overQuota.length > 0
              ? "border-danger-500/30 bg-danger-500/10 text-danger-600"
              : "border-pending-500/30 bg-pending-500/10 text-pending-600"
          )}
        >
          <p className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            <span>
              {overQuota.length > 0 && (
                <>
                  已超额度：
                  {overQuota.map((q) => USAGE_LABEL[q.usage_type] ?? q.usage_type).join("、")}
                  ，超出部分将自动转为工单计费。
                </>
              )}
              {overQuota.length === 0 && nearQuota.length > 0 && (
                <>
                  接近额度：
                  {nearQuota.map((q) => USAGE_LABEL[q.usage_type] ?? q.usage_type).join("、")}
                  ，建议关注剩余可用量。
                </>
              )}
            </span>
          </p>
        </div>
      )}

      {/* ── 用量看板 ───────────────────────────────────────────── */}
      <section className="rounded-r3 border border-line bg-surface p-5">
        <h2 className="text-body-sm font-semibold text-ink-800">用量看板</h2>
        {(dash?.quotas ?? []).length === 0 ? (
          <p className="mt-3 text-body-sm text-ink-500">
            本账期还没有用量记录。使用问答、文书、合同审查或合规扫描后会在此累计。
          </p>
        ) : (
          <ul className="mt-3 space-y-3">
            {(dash?.quotas ?? []).map((q) => {
              const pct = q.percent ?? 0;
              const barCls =
                pct >= 100 ? "bg-solid-danger" : pct >= 80 ? "bg-solid-pending" : "bg-solid-brand";
              return (
                <li key={q.usage_type}>
                  <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2 text-body-sm">
                    <span className="text-ink-700">
                      {USAGE_LABEL[q.usage_type] ?? q.usage_type}
                    </span>
                    <span className="num text-ink-500">
                      {q.used} / {q.limit}
                      <span className="ml-1.5">{pct}%</span>
                    </span>
                  </div>
                  <div
                    className="h-2 overflow-hidden rounded-full bg-surface-subtle"
                    role="progressbar"
                    aria-valuenow={Math.min(pct, 100)}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label={`${USAGE_LABEL[q.usage_type] ?? q.usage_type} 用量`}
                  >
                    <div
                      className={cn("h-full rounded-full transition-[width] duration-base ease-soft", barCls)}
                      style={{ width: `${Math.min(pct, 100)}%` }}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {/* ── 工单 ───────────────────────────────────────────────── */}
      <section className="overflow-hidden rounded-r3 border border-line bg-surface">
        <header className="flex items-center gap-2 border-b border-line px-4 py-3">
          <h2 className="text-body-sm font-semibold text-ink-800">工单</h2>
          {orders.length > 0 && (
            <span className="num ml-auto text-caption text-ink-500">共 {orders.length} 单</span>
          )}
        </header>
        {orders.length === 0 ? (
          <p className="px-4 py-6 text-center text-body-sm text-ink-500">
            暂无工单（用量未超套餐额度时不会转工单）
          </p>
        ) : (
          <ul className="divide-y divide-line">
            {orders.map((o) => (
              <li key={o.id} className="flex flex-wrap items-center gap-2 px-4 py-3">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-body-sm font-medium text-ink-800">
                    {o.title}
                    <span className="num ml-1.5 text-caption font-normal text-ink-400">
                      #{o.order_no}
                    </span>
                  </span>
                  <span className="mt-0.5 block text-caption text-ink-500">
                    {USAGE_LABEL[o.usage_type] ?? o.usage_type}
                  </span>
                </span>
                {o.urgent && <Badge variant="danger" size="sm">加急</Badge>}
                {o.escalate_to_lawyer && <Badge variant="info" size="sm">已转律师</Badge>}
                <span className="num text-body-sm text-ink-700">{yuan(o.price_cents)}</span>
                <Badge variant={WO_STATUS_TONE[o.status] ?? "neutral"} size="sm">
                  {WO_STATUS_LABEL[o.status] ?? o.status}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* ── 收入预测 ───────────────────────────────────────────── */}
      <section className="rounded-r3 border border-line bg-surface p-5">
        <h2 className="flex items-center gap-2 text-body-sm font-semibold text-ink-800">
          <TrendingUp className="h-4 w-4 text-ink-400" aria-hidden />
          收入预测（分项系数法）
        </h2>
        <p className="mt-1 text-caption text-ink-500">
          输入各分项金额（单位：分），按固定系数折算预测总收入。
        </p>

        <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {PROJECT_FIELDS.map((f) => (
            <div key={f.key}>
              <label htmlFor={`rev-${f.key}`} className="mb-1 block text-caption text-ink-500">
                {f.label}
              </label>
              <input
                id={`rev-${f.key}`}
                type="number"
                min={0}
                step={100}
                className={FIELD_CLS}
                value={inputs[f.key]}
                onChange={(e) =>
                  setInputs((s) => ({ ...s, [f.key]: Number(e.target.value) || 0 }))
                }
              />
            </div>
          ))}
        </div>

        <Button className="mt-3" variant="primary" isLoading={busy} onClick={() => void project()}>
          预测
        </Button>

        {rev && (
          <div className="mt-3 rounded-r2 bg-surface-subtle p-3.5">
            <dl className="flex flex-wrap gap-x-4 gap-y-1 text-body-sm">
              {Object.entries(rev.breakdown).map(([k, v]) => (
                <div key={k} className="flex items-baseline gap-1.5">
                  <dt className="text-ink-500">{BREAKDOWN_LABEL[k] ?? k}</dt>
                  <dd className="num text-ink-700">{yuan(v)}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-2 border-t border-line pt-2 text-body-sm text-ink-600">
              预测总收入
              <span className="num ml-2 text-h3 font-semibold text-ink-900">
                ¥{rev.total_yuan.toFixed(2)}
              </span>
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
