"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, ShieldCheck } from "lucide-react";
import { ApiError, authed } from "@nlaw/sdk";
import {
  Badge,
  Button,
  EmptyState,
  Skeleton,
  cn,
  useToast,
  type BadgeProps,
} from "@nlaw/ui";

/* ============================================================================
 * 四维合规扫描
 * ----------------------------------------------------------------------------
 * GET  /api/v1/compliance/scans        -> ComplianceScanOut[]（裸数组）
 * POST /api/v1/compliance/scans        {title, dimensions, input_summary} -> 扫描
 * GET  /api/v1/compliance/scans/{id}   -> ComplianceScanOut + findings[]
 *
 * ⚠️ 本轮修复：`POST /compliance/scans` 在旧实现里用 `authed(path, { body })`
 *     调用，而 `authed` 默认 method 为 `GET`。浏览器对「GET + body」直接抛
 *     `TypeError`，因此「开始扫描」按钮点了没有任何反应，也不会有网络请求。
 *
 * ⚠️ 契约事实：`dimension_scores` 是 `{维度: {score, risk, findings}}`，
 *     其中 `findings` 是该维度命中的规则条数——旧实现没用到它，本页补上。
 * ========================================================================== */

interface DimScore {
  score?: number;
  risk?: string;
  findings?: number;
}

interface Scan {
  id: number;
  title: string;
  status: string;
  dimensions?: string[] | null;
  scope?: string | null;
  input_summary?: string | null;
  overall_risk: string;
  dimension_scores?: Record<string, DimScore> | null;
  report_summary?: string | null;
  is_external?: boolean;
  review_status?: string | null;
  findings?: Finding[];
}

interface Finding {
  id: number;
  dimension: string;
  title: string;
  description?: string | null;
  risk_level: string;
  suggestion?: string | null;
}

const DIMS = [
  { key: "LABOR", label: "劳动用工" },
  { key: "COMMERCIAL", label: "商业合同" },
  { key: "DATA_PRIVACY", label: "数据隐私" },
  { key: "ADVERTISING", label: "广告营销" },
] as const;

const DIM_LABEL: Record<string, string> = Object.fromEntries(
  DIMS.map((d) => [d.key, d.label])
);

const RISK_LABEL: Record<string, string> = {
  HIGH: "高风险",
  MEDIUM: "中风险",
  LOW: "低风险",
};

const RISK_TONE: Record<string, BadgeProps["variant"]> = {
  HIGH: "danger",
  MEDIUM: "pending",
  LOW: "verified",
};

/** 分数色阶：<60 红 / <85 琥珀 / 其余绿。与后端 overall_risk 阈值一致。 */
function scoreCls(score?: number): string {
  if (score === undefined) return "text-ink-400";
  if (score < 60) return "text-danger-600";
  if (score < 85) return "text-pending-600";
  return "text-verified-600";
}

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

export default function CompliancePage() {
  const { addToast } = useToast();

  const [scans, setScans] = useState<Scan[]>([]);
  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");
  const [sel, setSel] = useState<string[]>(DIMS.map((d) => d.key));
  const [detail, setDetail] = useState<Scan | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setScans((await authed<Scan[]>("/api/v1/compliance/scans")) ?? []);
    } catch (e) {
      setError(errText(e));
      setScans([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const open = useCallback(
    async (id: number) => {
      try {
        setDetail(await authed<Scan>(`/api/v1/compliance/scans/${id}`));
      } catch (e) {
        addToast({ type: "error", title: "无法打开扫描详情", message: errText(e) });
      }
    },
    [addToast]
  );

  const run = useCallback(async () => {
    if (!title.trim()) {
      addToast({ type: "warning", title: "请填写扫描标题" });
      return;
    }
    if (sel.length === 0) {
      addToast({ type: "warning", title: "请至少选择一个合规维度" });
      return;
    }
    if (!summary.trim()) {
      addToast({
        type: "warning",
        title: "请填写自查材料摘要",
        message: "评分基于摘要文本的关键词命中，摘要为空则四维均为满分。",
      });
      return;
    }

    setBusy(true);
    try {
      // 显式 POST：SDK 虽已按「带 body 即 POST」兜底，但契约写在调用点更清楚
      const s = await authed<Scan>("/api/v1/compliance/scans", {
        method: "POST",
        body: { title: title.trim(), dimensions: sel, input_summary: summary },
      });
      addToast({ type: "success", title: "扫描已完成" });
      await refresh();
      await open(s.id);
    } catch (e) {
      addToast({ type: "error", title: "扫描失败", message: errText(e) });
    } finally {
      setBusy(false);
    }
  }, [title, sel, summary, addToast, refresh, open]);

  const toggleDim = (key: string) =>
    setSel((s) => (s.includes(key) ? s.filter((x) => x !== key) : [...s, key]));

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-h2 text-ink-900">四维合规扫描</h1>
        <p className="mt-1 text-body-sm text-ink-500">
          劳动用工 · 商业合同 · 数据隐私 · 广告营销 · 命中关键词按权重扣分
        </p>
      </header>

      {/* ── 发起扫描 ───────────────────────────────────────────── */}
      <section className="rounded-r3 border border-line bg-surface p-5">
        <h2 className="text-body-sm font-semibold text-ink-800">发起自查</h2>

        <div className="mt-3 space-y-3">
          <div>
            <label htmlFor="scan-title" className="mb-1 block text-body-sm text-ink-700">
              扫描标题
            </label>
            <input
              id="scan-title"
              className={FIELD_CLS}
              placeholder="如：2026Q3 合规自查"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>

          <div>
            <label htmlFor="scan-summary" className="mb-1 block text-body-sm text-ink-700">
              自查材料摘要
            </label>
            <textarea
              id="scan-summary"
              className={cn(FIELD_CLS, "h-24 resize-y")}
              placeholder="把要自查的情况概述成一段文字，命中风险关键词即扣分。"
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
            />
            <p className="mt-1 text-caption text-ink-500">
              评分依据是这段文本的关键词命中，摘要写不全就会得到偏高的分数。
            </p>
          </div>

          <fieldset>
            <legend className="mb-1.5 text-body-sm text-ink-700">扫描维度</legend>
            <div className="flex flex-wrap gap-2">
              {DIMS.map((d) => {
                const on = sel.includes(d.key);
                return (
                  <button
                    key={d.key}
                    type="button"
                    aria-pressed={on}
                    onClick={() => toggleDim(d.key)}
                    className={cn(
                      "tap rounded-r2 border px-3 py-1.5 text-body-sm transition-colors duration-fast",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30",
                      on
                        ? "border-brand-400 bg-brand-500/10 font-medium text-brand-700"
                        : "border-line text-ink-500 hover:bg-surface-hover"
                    )}
                  >
                    {d.label}
                  </button>
                );
              })}
            </div>
          </fieldset>
        </div>

        <Button
          className="mt-4"
          variant="primary"
          isLoading={busy}
          leftIcon={<ShieldCheck className="h-3.5 w-3.5" />}
          onClick={() => void run()}
        >
          开始扫描
        </Button>
      </section>

      {/* ── 扫描详情 ───────────────────────────────────────────── */}
      {detail && (
        <section className="rounded-r3 border border-line bg-surface">
          <header className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
            <h2 className="text-body-sm font-semibold text-ink-800">{detail.title}</h2>
            <Badge variant={RISK_TONE[detail.overall_risk] ?? "neutral"}>
              整体 {RISK_LABEL[detail.overall_risk] ?? detail.overall_risk}
            </Badge>
            {detail.is_external && <Badge variant="pending">对外报告 · 需 L2 复核</Badge>}
          </header>

          <div className="p-4">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {DIMS.map((d) => {
                const sc = detail.dimension_scores?.[d.key];
                return (
                  <div key={d.key} className="rounded-r2 border border-line p-3 text-center">
                    <p className="text-caption text-ink-500">{d.label}</p>
                    <p className={cn("num mt-1 text-h2 font-semibold", scoreCls(sc?.score))}>
                      {sc?.score ?? "—"}
                    </p>
                    <p className="mt-0.5 text-caption text-ink-500">
                      {sc?.risk ? RISK_LABEL[sc.risk] ?? sc.risk : "未扫描"}
                      {sc?.findings ? ` · 命中 ${sc.findings}` : ""}
                    </p>
                  </div>
                );
              })}
            </div>

            {detail.report_summary && (
              <p className="mt-4 whitespace-pre-line rounded-r2 bg-surface-subtle p-3 text-body-sm text-ink-700">
                {detail.report_summary}
              </p>
            )}

            <h3 className="mt-5 text-body-sm font-semibold text-ink-800">
              发现项（{(detail.findings ?? []).length}）
            </h3>
            {(detail.findings ?? []).length === 0 ? (
              <p className="mt-2 text-body-sm text-verified-600">
                四个维度均未命中风险规则。
              </p>
            ) : (
              <ul className="mt-2 space-y-2.5">
                {(detail.findings ?? []).map((f) => (
                  <li key={f.id} className="rounded-r2 border border-line p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant={RISK_TONE[f.risk_level] ?? "neutral"} size="sm">
                        {RISK_LABEL[f.risk_level] ?? f.risk_level}
                      </Badge>
                      <span className="text-body-sm font-medium text-ink-800">{f.title}</span>
                      <span className="ml-auto text-caption text-ink-400">
                        {DIM_LABEL[f.dimension] ?? f.dimension}
                      </span>
                    </div>
                    {f.description && (
                      <p className="mt-1 text-caption text-ink-500">{f.description}</p>
                    )}
                    {f.suggestion && (
                      <p className="mt-1.5 text-body-sm text-ink-600">
                        <span className="text-ink-500">整改建议：</span>
                        {f.suggestion}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      )}

      {/* ── 历史扫描 ───────────────────────────────────────────── */}
      <section className="space-y-2">
        <h2 className="text-body-sm font-semibold text-ink-600">历史扫描</h2>

        {error ? (
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
        ) : loading && scans.length === 0 ? (
          <div className="space-y-2">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : scans.length === 0 ? (
          <EmptyState
            icon={<ShieldCheck className="h-5 w-5" />}
            title="还没有扫描记录"
            description="填写上方的标题与自查摘要，选择维度后即可开始第一次扫描。"
          />
        ) : (
          <ul className="overflow-hidden rounded-r3 border border-line bg-surface">
            {scans.map((s) => (
              <li key={s.id} className="border-b border-line last:border-b-0">
                <button
                  type="button"
                  onClick={() => void open(s.id)}
                  className={cn(
                    "flex w-full items-center gap-3 px-4 py-3 text-left",
                    "transition-colors duration-fast hover:bg-surface-hover",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500/30"
                  )}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-body-sm font-medium text-ink-800">
                      {s.title}
                    </span>
                    <span className="num mt-0.5 block text-caption text-ink-500">
                      {s.dimensions?.length ?? 0} 个维度
                      {s.input_summary ? ` · ${s.input_summary.slice(0, 40)}` : ""}
                    </span>
                  </span>
                  <Badge variant={RISK_TONE[s.overall_risk] ?? "neutral"} size="sm">
                    {RISK_LABEL[s.overall_risk] ?? s.overall_risk}
                  </Badge>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
