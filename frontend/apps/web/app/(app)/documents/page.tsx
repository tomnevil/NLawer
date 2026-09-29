"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowLeft, CheckCircle2, FileText, Sparkles } from "lucide-react";
import { ApiError, authed } from "@nlaw/sdk";
import {
  Badge,
  Button,
  EmptyState,
  ProvenanceBadge,
  Skeleton,
  cn,
  useToast,
  type BadgeProps,
} from "@nlaw/ui";

/* ============================================================================
 * 文书工作台
 * ----------------------------------------------------------------------------
 * GET  /api/v1/documents/templates       -> TemplateOut[]
 * POST /api/v1/documents/start           {template_id}       -> DocumentOut
 * POST /api/v1/documents/{id}/collect    {variables}         -> DocumentOut
 * POST /api/v1/documents/{id}/render     (无 body)           -> DocumentOut
 *
 * ⚠️ 本轮修复的 P0 缺陷：三个写端点在后端都是 **POST**，而旧实现全部用
 *     `authed(path, { body })` 调用——`authed` 的 method 默认值是 `GET`，
 *     浏览器对「GET + body」会直接抛 `TypeError`，请求根本发不出去。
 *     于是「选模板 → 填变量 → 生成文书」三步**每一步都失败**，整个文书
 *     工作台不可用（且表现为「点了没反应」，不是可见的错误提示）。
 *
 *     修法有两层：
 *       1. `packages/sdk` 改为「带 body 即 POST」，从机制上杜绝漏写 method；
 *       2. 本页对无 body 的 `render` 显式声明 `method: "POST"`。
 * ========================================================================== */

interface TemplateVariable {
  key: string;
  label?: string;
  required?: boolean;
}

interface Tpl {
  id: number;
  code: string;
  name: string;
  lifecycle: string;
  description?: string | null;
  variables?: TemplateVariable[] | null;
  is_high_risk: boolean;
  reviewed_by_lawyer: boolean;
  usage_count: number;
}

interface RiskFinding {
  clause?: string;
  risk_level?: string;
  issue?: string;
  suggestion?: string;
}

interface Doc {
  id: number;
  title: string;
  content?: string | null;
  status: string;
  variables?: Record<string, string> | null;
  missing_variables?: { key: string; label?: string }[] | null;
  risk_findings?: RiskFinding[] | null;
  overall_risk: string;
  review_status: string;
  required_level: string;
}

/** 文书生命周期阶段（后端 `lifecycle` 字段）。 */
const LIFECYCLE_LABEL: Record<string, string> = {
  PRE_SIGN: "签约前",
  SIGNING: "签约中",
  PERFORMANCE: "履行中",
  DISPUTE: "争议期",
  TERMINATION: "终止期",
};

const DOC_STATUS_LABEL: Record<string, string> = {
  COLLECTING: "收集中",
  DRAFT: "草稿",
  GENERATED: "已生成",
  IN_REVIEW: "复核中",
  CONFIRMED: "已确认",
  EXPORTED: "已导出",
  VOIDED: "已作废",
};

const RISK_LABEL: Record<string, string> = {
  HIGH: "高风险",
  MEDIUM: "中风险",
  LOW: "低风险",
  NONE: "未发现风险",
};

const RISK_TONE: Record<string, BadgeProps["variant"]> = {
  HIGH: "danger",
  MEDIUM: "pending",
  LOW: "info",
  NONE: "verified",
};

const LEVEL_LABEL: Record<string, string> = {
  L1: "L1 · AI 自检",
  L2: "L2 · 律师复核",
  L3: "L3 · 合伙人终审",
};

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

export default function DocumentsPage() {
  const { addToast } = useToast();

  const [step, setStep] = useState<"list" | "collect" | "result">("list");
  const [tpls, setTpls] = useState<Tpl[]>([]);
  const [tpl, setTpl] = useState<Tpl | null>(null);
  const [doc, setDoc] = useState<Doc | null>(null);
  const [vals, setVals] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadTemplates = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setTpls((await authed<Tpl[]>("/api/v1/documents/templates")) ?? []);
    } catch (e) {
      setError(errText(e));
      setTpls([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadTemplates();
  }, [loadTemplates]);

  const start = useCallback(
    async (t: Tpl) => {
      setBusy("start");
      try {
        // 带 body 会被 SDK 判为 POST；这里仍显式写出，让契约在调用点可见
        const d = await authed<Doc>("/api/v1/documents/start", {
          method: "POST",
          body: { template_id: t.id },
        });
        setTpl(t);
        setDoc(d);
        setVals({});
        setStep("collect");
      } catch (e) {
        addToast({ type: "error", title: "无法开始该模板", message: errText(e) });
      } finally {
        setBusy(null);
      }
    },
    [addToast]
  );

  const collectAndRender = useCallback(async () => {
    if (!doc || !tpl) return;

    // 前端先拦一道必填，避免把「缺变量」这种可本地判断的错误推给后端
    const missing = (tpl.variables ?? [])
      .filter((v) => v.required && !(vals[v.key] ?? "").trim())
      .map((v) => v.label ?? v.key);
    if (missing.length > 0) {
      addToast({
        type: "warning",
        title: "还有必填项未填写",
        message: missing.join("、"),
      });
      return;
    }

    setBusy("render");
    try {
      const collected = await authed<Doc>(`/api/v1/documents/${doc.id}/collect`, {
        method: "POST",
        body: { variables: vals },
      });
      // render 无请求体，必须显式声明 POST——否则会退化为 GET
      const rendered = await authed<Doc>(`/api/v1/documents/${collected.id}/render`, {
        method: "POST",
      });
      setDoc(rendered);
      setStep("result");
      addToast({ type: "success", title: "文书已生成" });
    } catch (e) {
      addToast({ type: "error", title: "生成失败", message: errText(e) });
    } finally {
      setBusy(null);
    }
  }, [doc, tpl, vals, addToast]);

  const grouped = useMemo(() => {
    const m = new Map<string, Tpl[]>();
    for (const t of tpls) {
      const k = t.lifecycle || "OTHER";
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(t);
    }
    return Array.from(m.entries());
  }, [tpls]);

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-h2 text-ink-900">文书工作台</h1>
          <p className="mt-1 text-body-sm text-ink-500">
            模板库 · 变量收集 · 生成后自动扫描风险条款
          </p>
        </div>
        {step !== "list" && (
          <Button
            size="sm"
            variant="ghost"
            leftIcon={<ArrowLeft className="h-3.5 w-3.5" />}
            onClick={() => {
              setStep("list");
              setDoc(null);
              setTpl(null);
            }}
          >
            返回模板库
          </Button>
        )}
      </header>

      {/* ── 第一步：模板库 ─────────────────────────────────────── */}
      {step === "list" && (
        <>
          {error ? (
            <div className="rounded-r3 border border-danger-500/30 bg-danger-500/10 p-4">
              <p className="flex items-start gap-2 text-body-sm text-danger-600">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                {error}
              </p>
              <button
                type="button"
                onClick={() => void loadTemplates()}
                className="mt-2 text-body-sm text-link hover:text-link-hover"
              >
                重试
              </button>
            </div>
          ) : loading ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {[0, 1, 2, 3, 4, 5].map((i) => (
                <Skeleton key={i} className="h-28 w-full" />
              ))}
            </div>
          ) : tpls.length === 0 ? (
            <EmptyState
              icon={<FileText className="h-5 w-5" />}
              title="模板库为空"
              description="当前租户还没有可用的文书模板。"
            />
          ) : (
            grouped.map(([lifecycle, list]) => (
              <section key={lifecycle} className="space-y-2">
                <h2 className="text-body-sm font-semibold text-ink-600">
                  {LIFECYCLE_LABEL[lifecycle] ?? lifecycle}
                </h2>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {list.map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      disabled={busy === "start"}
                      onClick={() => void start(t)}
                      className={cn(
                        "flex flex-col rounded-r3 border border-line bg-surface p-4 text-left",
                        "transition-colors duration-fast hover:border-brand-300 hover:bg-surface-hover",
                        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30",
                        "disabled:opacity-60"
                      )}
                    >
                      <span className="flex items-start gap-2">
                        <FileText className="mt-0.5 h-4 w-4 shrink-0 text-ink-400" aria-hidden />
                        <span className="min-w-0 flex-1">
                          <span className="block font-medium text-ink-900">{t.name}</span>
                          <span className="num mt-0.5 block text-caption text-ink-500">
                            {t.code}
                          </span>
                        </span>
                      </span>

                      {t.description && (
                        <span className="mt-2 line-clamp-2 text-caption leading-relaxed text-ink-600">
                          {t.description}
                        </span>
                      )}

                      <span className="mt-2.5 flex flex-wrap items-center gap-1.5">
                        {t.is_high_risk && <Badge variant="danger" size="sm">高风险文书</Badge>}
                        {t.reviewed_by_lawyer ? (
                          <Badge variant="verified" size="sm">律师已审</Badge>
                        ) : (
                          <Badge variant="pending" size="sm">待律师审</Badge>
                        )}
                        {t.usage_count > 0 && (
                          <span className="num ml-auto text-caption text-ink-400">
                            用过 {t.usage_count} 次
                          </span>
                        )}
                      </span>
                    </button>
                  ))}
                </div>
              </section>
            ))
          )}
        </>
      )}

      {/* ── 第二步：变量收集 ───────────────────────────────────── */}
      {step === "collect" && tpl && doc && (
        <section className="rounded-r3 border border-line bg-surface p-5">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-h3 text-ink-900">{tpl.name}</h2>
            {tpl.is_high_risk && <Badge variant="danger">高风险文书</Badge>}
          </div>
          <p className="mt-1 text-body-sm text-ink-500">
            带 <span className="text-danger-600">*</span> 的为必填项，填齐后才能生成正文。
          </p>

          <div className="mt-4 space-y-3">
            {(tpl.variables ?? []).length === 0 ? (
              <p className="text-body-sm text-ink-500">该模板无需填写变量，可直接生成。</p>
            ) : (
              (tpl.variables ?? []).map((v) => {
                const isMissing = Boolean(v.required) && !(vals[v.key] ?? "").trim();
                return (
                  <div key={v.key}>
                    <label
                      htmlFor={`var-${v.key}`}
                      className="mb-1 block text-body-sm text-ink-700"
                    >
                      {v.label ?? v.key}
                      {v.required && <span className="ml-0.5 text-danger-600">*</span>}
                    </label>
                    <input
                      id={`var-${v.key}`}
                      className={cn(FIELD_CLS, isMissing && "border-danger-500/40")}
                      value={vals[v.key] ?? ""}
                      onChange={(e) => setVals((s) => ({ ...s, [v.key]: e.target.value }))}
                    />
                  </div>
                );
              })
            )}
          </div>

          <div className="mt-5 flex flex-wrap gap-2">
            <Button
              variant="primary"
              isLoading={busy === "render"}
              leftIcon={<Sparkles className="h-3.5 w-3.5" />}
              onClick={() => void collectAndRender()}
            >
              生成文书
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setStep("list");
                setDoc(null);
              }}
            >
              取消
            </Button>
          </div>
        </section>
      )}

      {/* ── 第三步：生成结果 ───────────────────────────────────── */}
      {step === "result" && doc && (
        <div className="space-y-3">
          <section className="overflow-hidden rounded-r3 border border-line bg-surface">
            <header className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
              <h2 className="text-body-sm font-semibold text-ink-800">{doc.title}</h2>
              <ProvenanceBadge state="ai" />
              <span className="ml-auto flex flex-wrap items-center gap-1.5">
                <Badge variant="outline">
                  {DOC_STATUS_LABEL[doc.status] ?? doc.status}
                </Badge>
                <Badge variant="neutral">{LEVEL_LABEL[doc.required_level] ?? doc.required_level}</Badge>
              </span>
            </header>

            {/*
             * 文书正文用衬线字（Noto Serif SC）。
             * 规范第 03 节把衬线体限定给「法条原文与文书正文」——
             * 这两类内容会被打印、会进卷宗，衬线在纸面上的可读性明显更好。
             */}
            <pre className="max-h-[28rem] overflow-auto whitespace-pre-wrap px-4 py-4 font-serif text-body leading-[1.95] text-ink-800">
              {doc.content || "（正文为空）"}
            </pre>
          </section>

          <section className="rounded-r3 border border-line bg-surface">
            <header className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
              <h2 className="text-body-sm font-semibold text-ink-800">风险扫描</h2>
              <Badge variant={RISK_TONE[doc.overall_risk] ?? "neutral"}>
                {RISK_LABEL[doc.overall_risk] ?? doc.overall_risk}
              </Badge>
              {(doc.risk_findings ?? []).length > 0 && (
                <span className="num ml-auto text-caption text-ink-500">
                  命中 {(doc.risk_findings ?? []).length} 条
                </span>
              )}
            </header>

            <div className="p-4">
              {(doc.risk_findings ?? []).length === 0 ? (
                <p className="flex items-center gap-1.5 text-body-sm text-verified-600">
                  <CheckCircle2 className="h-4 w-4" aria-hidden />
                  未发现明显风险条款
                </p>
              ) : (
                <ul className="space-y-2.5">
                  {(doc.risk_findings ?? []).map((f, i) => (
                    <li key={i} className="rounded-r2 border border-line p-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant={RISK_TONE[f.risk_level ?? ""] ?? "neutral"} size="sm">
                          {RISK_LABEL[f.risk_level ?? ""] ?? f.risk_level ?? "—"}
                        </Badge>
                        <span className="text-body-sm font-medium text-ink-800">
                          {f.issue ?? "—"}
                        </span>
                      </div>
                      {f.suggestion && (
                        <p className="mt-1.5 text-body-sm text-ink-600">
                          <span className="text-ink-500">修改建议：</span>
                          {f.suggestion}
                        </p>
                      )}
                      {f.clause && (
                        <p className="mt-1.5 truncate font-serif text-caption text-ink-400" title={f.clause}>
                          …{f.clause}…
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>

          <p className="text-caption leading-relaxed text-ink-500">
            生成结果由 AI 产出，复核级别 {LEVEL_LABEL[doc.required_level] ?? doc.required_level}
            {doc.review_status ? `（当前复核状态 ${doc.review_status}）` : ""}。
            定稿前请逐条核对风险建议。
          </p>
        </div>
      )}
    </div>
  );
}
