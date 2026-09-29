"use client";

import React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { MessagesSquare, FileText, ScanLine, ShieldCheck, Database, BarChart3, ArrowRight } from "lucide-react";
import { ApiError, authed } from "@nlaw/sdk";
import { Badge, Button, Card, EmptyState, KpiCard, Skeleton, displayName, useSession } from "@nlaw/ui";

/* ────────────────────────── 后端契约 ────────────────────────── */

/** `app/core/pagination.py` 的 `Page`：`total` 在顶层，不在 `meta` 里。 */
interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
  /** `total` 是否为下界（匹配数超过 COUNT_CAP 时只数到上限） */
  total_is_lower_bound?: boolean;
}

interface Conversation {
  id: number;
  channel: string;
  status: string;
  case_id?: number | null;
  last_message_at?: string | null;
}

interface Quota {
  usage_type: string;
  used: number;
  limit: number;
  remaining: number;
  percent: number;
}

interface Dashboard {
  period: string;
  quotas: Quota[];
  work_orders: { total: number; pending: number; amount_cents: number };
}

interface Scan {
  id: number;
  title: string;
  status: string;
  overall_risk: string;
  is_external: boolean;
}

/* ────────────────────────── 展示映射 ────────────────────────── */

const USAGE_LABEL: Record<string, string> = {
  QA: "智能问答",
  DOCUMENT: "文书生成",
  CONTRACT_REVIEW: "合同审查",
  COMPLIANCE_SCAN: "合规扫描",
};

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

const ENTRY = [
  { href: "/qa", icon: MessagesSquare, title: "智能问答", desc: "四段式回答 · 引用溯源" },
  { href: "/documents", icon: FileText, title: "文书工作台", desc: "模板库 · 多轮变量收集" },
  { href: "/contract-review", icon: ScanLine, title: "合同审查", desc: "逐条定位 · 一键采纳修订" },
  { href: "/compliance", icon: ShieldCheck, title: "合规扫描", desc: "四维体检 · 风险分级" },
  { href: "/knowledge", icon: Database, title: "企业知识库", desc: "合同库 · 制度库" },
  { href: "/billing", icon: BarChart3, title: "用量与计费", desc: "实时看板 · 超量转工单" },
];

/** 把下界计数渲染成「200+」，避免把上限当精确值展示。 */
function countText(n: number, isLowerBound?: boolean): string {
  return isLowerBound ? `${n}+` : String(n);
}

function greeting(): string {
  const h = new Date().getHours();
  if (h < 6) return "凌晨好";
  if (h < 12) return "上午好";
  if (h < 14) return "中午好";
  if (h < 18) return "下午好";
  return "晚上好";
}

export default function WorkbenchHome() {
  const router = useRouter();
  const { user } = useSession();
  const [conversations, setConversations] = React.useState<Paged<Conversation> | null>(null);
  const [dashboard, setDashboard] = React.useState<Dashboard | null>(null);
  const [scans, setScans] = React.useState<Scan[] | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    let alive = true;

    (async () => {
      try {
        // 三个请求互不依赖，并行发出；任一失败不拖垮整页
        const [conv, dash, scanList] = await Promise.allSettled([
          authed<Paged<Conversation>>("/api/v1/conversations?page_size=6"),
          authed<Dashboard>("/api/v1/billing/dashboard"),
          authed<Scan[]>("/api/v1/compliance/scans"),
        ]);
        if (!alive) return;

        if (conv.status === "fulfilled") setConversations(conv.value);
        if (dash.status === "fulfilled") setDashboard(dash.value);
        if (scanList.status === "fulfilled") setScans(scanList.value);

        const rejected = [conv, dash, scanList].find((r) => r.status === "rejected") as
          | PromiseRejectedResult
          | undefined;
        if (rejected) {
          const reason = rejected.reason;
          if (reason instanceof ApiError && reason.status === 401) {
            router.replace("/login");
            return;
          }
          setError("部分数据加载失败，稍后重试");
        }
      } finally {
        if (alive) setLoading(false);
      }
    })();

    return () => {
      alive = false;
    };
  }, [router]);

  const pendingScans = (scans ?? []).filter(
    (s) => s.overall_risk === "HIGH" || s.overall_risk === "CRITICAL"
  );
  const activeConversations = (conversations?.items ?? []).filter((c) => c.status === "ACTIVE");
  const totalRemaining = (dashboard?.quotas ?? []).reduce((sum, q) => sum + q.remaining, 0);

  return (
    <div className="space-y-6">
      {/* ── 问候 ─────────────────────────────────────── */}
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-h1 text-ink-900">
            {greeting()}
            {displayName(user) ? `，${displayName(user)}` : ""}
          </h1>
          <p className="mt-1 text-body-sm text-ink-500">
            {loading
              ? "正在加载工作台…"
              : pendingScans.length > 0
                ? `有 ${pendingScans.length} 项高风险合规发现待处理`
                : "暂无待处理的高风险事项"}
          </p>
        </div>
        <Button variant="primary" onClick={() => router.push("/qa")}>
          发起咨询
        </Button>
      </header>

      {error && (
        <div
          role="alert"
          className="rounded-r2 border border-pending-500/30 bg-pending-500/10 px-3 py-2 text-body-sm text-pending-600"
        >
          {error}
        </div>
      )}

      {/* ── KPI ──────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {loading ? (
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[92px] w-full" />)
        ) : (
          <>
            <KpiCard
              label="累计咨询"
              value={countText(conversations?.total ?? 0, conversations?.total_is_lower_bound)}
              unit="次"
              icon={<MessagesSquare className="h-4 w-4" />}
              color="brand"
            />
            <KpiCard
              label="进行中会话"
              value={activeConversations.length}
              unit="个"
              icon={<MessagesSquare className="h-4 w-4" />}
              color="info"
            />
            <KpiCard
              label="高风险合规发现"
              value={pendingScans.length}
              unit="项"
              icon={<ShieldCheck className="h-4 w-4" />}
              color={pendingScans.length > 0 ? "danger" : "verified"}
            />
            <KpiCard
              label="本月剩余额度"
              value={totalRemaining}
              unit="次"
              icon={<BarChart3 className="h-4 w-4" />}
              color="gold"
            />
          </>
        )}
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        {/* ── 最近会话 ─────────────────────────────── */}
        <Card className="lg:col-span-2" hover={false}>
          <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
            <h2 className="text-h4 text-ink-900">最近咨询</h2>
            <Link
              href="/qa"
              className="inline-flex items-center gap-1 text-label text-link transition-colors duration-fast hover:text-link-hover"
            >
              全部 <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>

          {loading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : (conversations?.items.length ?? 0) === 0 ? (
            <EmptyState
              title="还没有咨询记录"
              description="从一次提问开始，AI 会给出一份带引用溯源的初步分析。"
              action={
                <Button variant="primary" size="sm" onClick={() => router.push("/qa")}>
                  发起首次咨询
                </Button>
              }
            />
          ) : (
            <ul className="divide-y divide-line">
              {conversations!.items.map((c) => (
                <li key={c.id}>
                  <Link
                    href="/qa"
                    className="flex items-center gap-3 px-4 py-3 transition-colors duration-fast hover:bg-surface-hover"
                  >
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-r2 bg-brand-500/10 text-link">
                      <MessagesSquare className="h-4 w-4" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-body-sm font-medium text-ink-800">
                        会话 #{c.id}
                        {c.case_id ? ` · 关联案件 #${c.case_id}` : ""}
                      </span>
                      <span className="num block truncate text-caption text-ink-500">
                        {c.last_message_at ?? "暂无消息"}
                      </span>
                    </span>
                    <Badge variant={c.status === "ACTIVE" ? "primary" : "neutral"}>
                      {c.status === "ACTIVE" ? "进行中" : c.status}
                    </Badge>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {/* ── 右栏：额度 + 快捷入口 ────────────────── */}
        <div className="space-y-4">
          <Card hover={false}>
            <div className="flex items-baseline justify-between gap-2 border-b border-line px-4 py-3">
              <h2 className="text-h4 text-ink-900">本月用量</h2>
              {dashboard?.period && <span className="num text-caption text-ink-400">{dashboard.period}</span>}
            </div>

            {loading ? (
              <div className="space-y-3 p-4">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-8 w-full" />
                ))}
              </div>
            ) : (dashboard?.quotas.length ?? 0) === 0 ? (
              <p className="px-4 py-6 text-center text-body-sm text-ink-500">本期暂无用量记录</p>
            ) : (
              <ul className="space-y-3 p-4">
                {dashboard!.quotas.map((q) => {
                  // 超额时 percent 会 >100，进度条需夹到 100 否则会溢出容器
                  const barWidth = Math.min(100, q.percent);
                  const over = q.percent >= 100;
                  return (
                    <li key={q.usage_type}>
                      <div className="mb-1 flex items-baseline justify-between gap-2 text-caption">
                        <span className="text-ink-600">{USAGE_LABEL[q.usage_type] ?? q.usage_type}</span>
                        <span className="num text-ink-500">
                          <span className={over ? "font-medium text-danger-600" : "text-ink-800"}>
                            {q.used}
                          </span>
                          {" / "}
                          {q.limit}
                        </span>
                      </div>
                      <div
                        className="h-1.5 overflow-hidden rounded-full bg-surface-subtle"
                        role="progressbar"
                        aria-valuenow={q.used}
                        aria-valuemin={0}
                        aria-valuemax={q.limit}
                        aria-label={`${USAGE_LABEL[q.usage_type] ?? q.usage_type} 用量`}
                      >
                        <div
                          className={
                            over ? "h-full rounded-full bg-danger-500" : "h-full rounded-full bg-brand-600"
                          }
                          style={{ width: `${barWidth}%` }}
                        />
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}

            {(dashboard?.work_orders.pending ?? 0) > 0 && (
              <div className="border-t border-line px-4 py-3">
                <p className="text-caption text-pending-600">
                  超量转工单 <span className="num font-medium">{dashboard!.work_orders.pending}</span> 笔待确认
                </p>
              </div>
            )}
          </Card>

          <Card hover={false}>
            <h2 className="border-b border-line px-4 py-3 text-h4 text-ink-900">快捷入口</h2>
            <ul className="divide-y divide-line">
              {ENTRY.map((e) => (
                <li key={e.href}>
                  <Link
                    href={e.href}
                    className="flex items-center gap-3 px-4 py-2.5 transition-colors duration-fast hover:bg-surface-hover"
                  >
                    <e.icon className="h-4 w-4 shrink-0 text-ink-400" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-body-sm text-ink-800">{e.title}</span>
                      <span className="block truncate text-caption text-ink-500">{e.desc}</span>
                    </span>
                    <ArrowRight className="h-3.5 w-3.5 shrink-0 text-ink-300" />
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>

      {/* ── 高风险发现 ───────────────────────────────── */}
      {!loading && pendingScans.length > 0 && (
        <Card hover={false}>
          <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
            <h2 className="text-h4 text-ink-900">需处理的合规发现</h2>
            <Link
              href="/compliance"
              className="inline-flex items-center gap-1 text-label text-link transition-colors duration-fast hover:text-link-hover"
            >
              全部扫描 <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>
          <ul className="divide-y divide-line">
            {pendingScans.slice(0, 5).map((s) => (
              <li key={s.id} className="flex items-center gap-3 px-4 py-3">
                <ShieldCheck className="h-4 w-4 shrink-0 text-danger-500" />
                <span className="min-w-0 flex-1 truncate text-body-sm text-ink-800">{s.title}</span>
                <Badge variant={RISK_TONE[s.overall_risk] ?? "neutral"}>
                  {RISK_LABEL[s.overall_risk] ?? s.overall_risk}
                </Badge>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <p className="text-center text-caption text-ink-400">
        本内容由 AI 生成，仅供参考，不构成正式法律意见。
      </p>
    </div>
  );
}
