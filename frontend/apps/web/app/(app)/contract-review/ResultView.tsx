"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronDown,
  Coins,
  Copy,
  Crosshair,
  Info,
  RotateCcw,
  ScanLine,
} from "lucide-react";
import {
  Alert,
  Badge,
  Button,
  ProvenanceBadge,
  SegmentedControl,
  cn,
  useToast,
  type BadgeProps,
  type SegmentedControlOption,
} from "@nlaw/ui";
import {
  DEFAULT_DISCLAIMER,
  RISK_LABEL,
  RISK_TONE,
  analysisSpec,
  basisNote,
  basisSpec,
  billingSpec,
  coverageSpec,
  findingClauseLabel,
  findingOriginal,
  findingTitle,
  sortFindings,
  sourceBanner,
  statusBanner,
  suggestionSpec,
  type ContractReviewResult,
  type ReviewFinding,
} from "./types";
import { buildHighlights, buildRedline } from "./redline";

/* ============================================================================
 * 合同审查结果页（CR-13 + CR-04）
 * ----------------------------------------------------------------------------
 * 本页承载四条**不可妥协**的产品承诺，任何改动都不得削弱：
 *
 *   1. **来源必须可辨**：`source=rule/mock` 或字段缺失时出横幅，绝不把降级产出
 *      渲染成 AI 分析结果；
 *   2. **覆盖度与完成度必须可见**：`prescreen_only` 不得渲染成「合同安全」；
 *   3. **免责声明固定展示**（失败态也要展示）；
 *   4. **计费必须如实**：`usage` 缺省或 0 ⇒ 明示「本次未计费」。
 *
 * 交互上对齐竞品最低门槛：点击风险项 → 原文自动滚动并高亮（逐字区间）；
 * 每条建议可一键采纳生成修订稿，且原始文本始终保留以便对比与撤销。
 * ========================================================================== */

export interface ResultViewProps {
  result: ContractReviewResult;
  /** 提交审查时使用的原文——逐字，未经任何加工（切片依赖它的长度与下标） */
  sourceText: string;
  /** 用同一份原文重新发起审查 */
  onRetry: () => void;
  /** 回到输入态修改原文 */
  onReset: () => void;
}

/** 风险等级 → 原文高亮底色。颜色不单独承载语义，清单里有文字等级。 */
const HIGHLIGHT_BG: Record<string, string> = {
  HIGH: "bg-danger-500/25",
  MEDIUM: "bg-pending-500/30",
  LOW: "bg-info-500/25",
};

const PANE_OPTIONS: SegmentedControlOption<"list" | "text">[] = [
  { value: "list", label: "风险清单" },
  { value: "text", label: "合同原文" },
];

const REDLINE_OPTIONS: SegmentedControlOption<"diff" | "full">[] = [
  { value: "diff", label: "逐条对比" },
  { value: "full", label: "修订后全文" },
];

function errText(e: unknown): string {
  return e instanceof Error ? e.message : "复制失败";
}

/* ------------------------------------------------------------ 单条风险卡片 */

interface FindingCardProps {
  finding: ReviewFinding;
  adopted: boolean;
  active: boolean;
  citationOpen: boolean;
  onLocate: () => void;
  onToggleAdopt: () => void;
  onToggleCitation: () => void;
}

function FindingCard({
  finding,
  adopted,
  active,
  citationOpen,
  onLocate,
  onToggleAdopt,
  onToggleCitation,
}: FindingCardProps) {
  const level = (finding.risk_level ?? "").trim();
  const title = findingTitle(finding);
  const clauseLabel = findingClauseLabel(finding);
  const original = findingOriginal(finding);
  const consequence = (finding.consequence ?? "").trim();
  const basis = basisSpec(finding);
  const note = basisNote(basis);
  const suggestion = suggestionSpec(finding);

  const citationTitle = basis.citation
    ? `${basis.citation.law_name ?? ""} ${basis.citation.article_no ?? ""}`.trim()
    : "";

  return (
    <li
      className={cn(
        "rounded-r2 border p-3 transition-colors duration-fast",
        adopted ? "border-verified-500/40 bg-verified-500/5" : "border-line bg-surface",
        active && "border-brand-400 ring-1 ring-brand-500/30"
      )}
    >
      {/* 整块头部可点 = 定位到原文对应区间（竞品最低门槛要求） */}
      <button
        type="button"
        onClick={onLocate}
        className={cn(
          "flex w-full items-start gap-2 rounded-r1 text-left",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30"
        )}
      >
        <span className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
          <Badge variant={RISK_TONE[level] ?? "neutral"} size="sm">
            {RISK_LABEL[level] ?? level ?? "未标注等级"}
          </Badge>
          <span className="text-body-sm font-medium text-ink-900">{title}</span>
          {clauseLabel && <span className="num text-caption text-ink-500">{clauseLabel}</span>}
          {adopted && (
            <Badge variant="verified" size="sm">
              已采纳
            </Badge>
          )}
        </span>
        <span className="flex shrink-0 items-center gap-1 text-caption text-link">
          <Crosshair className="h-3.5 w-3.5" aria-hidden />
          定位原文
        </span>
      </button>

      {/* 后果说明：瑞思判定「仅标中风险」无决策价值，必须显示 */}
      {consequence ? (
        <p className="mt-2 flex items-start gap-1.5 text-body-sm text-ink-700">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-pending-500" aria-hidden />
          <span>
            <span className="text-ink-500">后果：</span>
            {consequence}
          </span>
        </p>
      ) : (
        <p className="mt-2 text-caption text-pending-600">本条未提供后果说明，请自行判断影响。</p>
      )}

      {original && (
        <p className="mt-2 line-clamp-3 rounded-r2 bg-surface-subtle px-2.5 py-2 font-serif text-caption leading-relaxed text-ink-600">
          {original}
        </p>
      )}

      {/* 依据：statute 展示可核验条文，experience 明示「无法条依据」 */}
      <div className="mt-2.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant={basis.tone} size="sm">
            {basis.label}
          </Badge>
          {basis.citation && (
            <button
              type="button"
              onClick={onToggleCitation}
              aria-expanded={citationOpen}
              className="inline-flex items-center gap-1 text-caption text-link transition-colors duration-fast hover:text-link-hover"
            >
              {citationTitle}
              <ChevronDown
                className={cn("h-3 w-3 transition-transform duration-fast", citationOpen && "rotate-180")}
                aria-hidden
              />
            </button>
          )}
        </div>

        {note && <p className="mt-1 text-caption leading-relaxed text-ink-500">{note}</p>}

        {basis.citation && citationOpen && (
          <div className="mt-1.5 rounded-r2 border border-gold-400/40 bg-gold-500/5 px-2.5 py-2">
            {citationTitle && (
              <p className="num text-caption font-medium text-gold-700">{citationTitle}</p>
            )}
            {basis.citation.content && (
              <p className="legal-text mt-1 text-ink-700">{basis.citation.content}</p>
            )}
          </div>
        )}
      </div>

      {/* 修订建议 + 一键采纳（CR-04） */}
      {suggestion.adoptable ? (
        <div className="mt-2.5 rounded-r2 border border-brand-400/40 bg-brand-500/5 px-2.5 py-2">
          <p className="text-caption text-ink-500">修改建议（可直接采纳）</p>
          <p className="legal-text mt-1 text-ink-800">{suggestion.adoptable}</p>
        </div>
      ) : suggestion.direction ? (
        <div className="mt-2.5 rounded-r2 border border-line bg-surface-subtle px-2.5 py-2">
          <p className="text-caption text-ink-500">修改方向（非可直接采纳的改写文本）</p>
          <p className="mt-1 text-body-sm text-ink-700">{suggestion.direction}</p>
        </div>
      ) : (
        <p className="mt-2.5 text-caption text-ink-500">本条未提供修改建议。</p>
      )}

      {suggestion.adoptable ? (
        <div className="mt-2.5">
          <Button
            size="sm"
            variant={adopted ? "verify" : "secondary"}
            leftIcon={
              adopted ? <CheckCircle2 className="h-3.5 w-3.5" /> : <Check className="h-3.5 w-3.5" />
            }
            onClick={onToggleAdopt}
          >
            {adopted ? "已采纳 · 点击撤销" : "一键采纳"}
          </Button>
        </div>
      ) : (
        <p className="mt-2.5 text-caption text-ink-400">
          本条未返回可直接采纳的改写文本，无法一键采纳。
        </p>
      )}
    </li>
  );
}

/* -------------------------------------------------------------- 结果主视图 */

export function ResultView({ result, sourceText, onRetry, onReset }: ResultViewProps) {
  const { addToast } = useToast();

  const findings = useMemo(() => result.findings ?? [], [result.findings]);
  const sorted = useMemo(() => sortFindings(findings), [findings]);

  const [adopted, setAdopted] = useState<ReadonlySet<number>>(() => new Set<number>());
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const [pane, setPane] = useState<"list" | "text">("list");
  const [redlineMode, setRedlineMode] = useState<"diff" | "full">("diff");
  const [openCitations, setOpenCitations] = useState<ReadonlySet<number>>(() => new Set<number>());

  const markRefs = useRef(new Map<number, HTMLElement | null>());
  const textPanelRef = useRef<HTMLElement | null>(null);
  const [pendingLocate, setPendingLocate] = useState<number | null>(null);

  const highlights = useMemo(() => buildHighlights(sourceText, findings), [sourceText, findings]);
  const redline = useMemo(() => buildRedline(sourceText, findings, adopted), [sourceText, findings, adopted]);

  const adoptableIndices = useMemo(
    () => findings.map((f, i) => (suggestionSpec(f).adoptable ? i : -1)).filter((i) => i >= 0),
    [findings],
  );

  const source = sourceBanner(result.source);
  const status = statusBanner(result.status);
  const analysis = analysisSpec(result.analysis_status, findings.length);
  const coverage = coverageSpec(result.coverage);
  const billing = billingSpec(result.usage);
  const disclaimer = (result.disclaimer ?? "").trim() || DEFAULT_DISCLAIMER;

  const overallRisk = (result.overall_risk ?? "").trim();
  const overall = useMemo(() => {
    if (!overallRisk) return null;
    if (overallRisk === "NONE") {
      // 「零发现即放行」是阻断级缺陷：只有完成度确认时才允许正面结论
      return analysis.positive
        ? { text: "未发现风险", tone: "verified" as BadgeProps["variant"] }
        : { text: "未命中规则库（不等于合同安全）", tone: "pending" as BadgeProps["variant"] };
    }
    return {
      text: RISK_LABEL[overallRisk] ?? overallRisk,
      tone: (RISK_TONE[overallRisk] ?? "neutral") as BadgeProps["variant"],
    };
  }, [overallRisk, analysis.positive]);

  /* -------------------------------------------------------------- 交互 */

  const locate = useCallback((index: number) => {
    setActiveIndex(index);
    // 移动端清单与原文分屏显示：先切到原文，再滚动（由 pendingLocate 驱动）
    setPane("text");
    setPendingLocate(index);
  }, []);

  useEffect(() => {
    if (pendingLocate === null) return;
    const el = markRefs.current.get(pendingLocate);
    if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
    else textPanelRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
    setPendingLocate(null);
  }, [pendingLocate, pane]);

  const toggleAdopt = useCallback((index: number) => {
    setAdopted((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }, []);

  const toggleCitation = useCallback((index: number) => {
    setOpenCitations((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }, []);

  const copyRedline = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(redline.plain);
      addToast({ type: "success", title: "修订后全文已复制" });
    } catch (e) {
      addToast({
        type: "error",
        title: "复制失败",
        message: `${errText(e)}——请在下方手动选择文本复制。`,
      });
    }
  }, [redline.plain, addToast]);

  /* -------------------------------------------------------------- 渲染 */

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="flex flex-wrap items-center gap-2 text-h2 text-ink-900">
            <span className="min-w-0 break-words">{result.title || "合同审查"}</span>
            {overall && <Badge variant={overall.tone}>{overall.text}</Badge>}
          </h1>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-body-sm text-ink-500">
            <span className="num">原文 {sourceText.length} 字</span>
            <span aria-hidden>·</span>
            <span className="num">风险 {findings.length} 条</span>
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="secondary" leftIcon={<RotateCcw className="h-3.5 w-3.5" />} onClick={onRetry}>
            重新审查
          </Button>
          <Button size="sm" variant="ghost" onClick={onReset}>
            修改原文
          </Button>
        </div>
      </header>

      {/* ── 必须显示的横幅：来源 / 降级 / 完成度 / 覆盖度 ───────────── */}
      <div className="space-y-2">
        <Alert
          variant={source.tone}
          title={source.title}
          icon={<ScanLine className="h-5 w-5" />}
        >
          <span className="flex flex-wrap items-center gap-2">
            <ProvenanceBadge state={source.provenance} size="sm" />
            <span>{source.detail}</span>
          </span>
        </Alert>

        {status && (
          <Alert variant={status.tone} title={status.title} icon={<AlertTriangle className="h-5 w-5" />}>
            {status.detail}
          </Alert>
        )}

        {/*
         * 「结论强度」横幅只在**无法给出正面结论**时出现：
         *   prescreen_only → 必须显式声明「未命中规则库不等于合同安全」
         *   完成度未标注   → 必须显式声明「不得据此认定合同安全」
         * risk_found 不在此列——风险条目本身就摆在下方的清单里，再顶一条横幅
         * 只会稀释真正需要被看见的那两条警示。
         */}
        {!analysis.positive && result.analysis_status !== "risk_found" && (
          <Alert variant="warning" title="结论强度" icon={<Info className="h-5 w-5" />}>
            {analysis.text}
          </Alert>
        )}

        {coverage.tone === "warning" && (
          <Alert variant="warning" title="覆盖度" icon={<Info className="h-5 w-5" />}>
            {coverage.text}
          </Alert>
        )}

        {/*
         * 超量转工单必须单独出横幅：这是**费用性质**的变化（套餐内扣减 →
         * 额外计费工单），只放在元信息条的小字里会被用户忽略，
         * 而 PRD Q2 已把它列为需向用户交代的商业口径问题。
         */}
        {billing.escalated && (
          <Alert variant="warning" title="本次已超出套餐额度" icon={<Coins className="h-5 w-5" />}>
            {billing.note}
          </Alert>
        )}
      </div>

      {/* ── 来源 / 完成度 / 覆盖度 / 计费 元信息条 ───────────────── */}
      <dl className="grid grid-cols-1 gap-x-4 gap-y-3 rounded-r3 border border-line bg-surface p-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="min-w-0">
          <dt className="text-caption text-ink-500">结果来源</dt>
          <dd className="mt-1 flex flex-wrap items-center gap-1.5">
            <ProvenanceBadge state={source.provenance} size="sm" />
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-caption text-ink-500">分析完成度</dt>
          <dd
            className={cn(
              "mt-1 text-body-sm",
              analysis.tone === "verified"
                ? "text-verified-600"
                : analysis.tone === "danger"
                  ? "text-danger-600"
                  : "text-pending-600"
            )}
          >
            {analysis.text}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-caption text-ink-500">覆盖度</dt>
          <dd
            className={cn(
              "mt-1 text-body-sm",
              coverage.tone === "warning" ? "text-pending-600" : "text-ink-700"
            )}
          >
            {coverage.text}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-caption text-ink-500">计费</dt>
          <dd className="mt-1 space-y-1">
            <span className="flex flex-wrap items-center gap-1.5 text-body-sm text-ink-800">
              <Coins className="h-3.5 w-3.5 shrink-0 text-ink-400" aria-hidden />
              <span className={cn(!billing.charged && "font-medium")}>{billing.text}</span>
              {billing.escalated && (
                <Badge variant="pending" size="sm">
                  已转人工工单
                </Badge>
              )}
            </span>
            {billing.note && (
              <span className="block text-caption leading-relaxed text-pending-600">{billing.note}</span>
            )}
          </dd>
        </div>
      </dl>

      {result.summary && (
        <section className="rounded-r3 border border-line bg-surface p-4">
          <h2 className="text-body-sm font-semibold text-ink-800">审查摘要</h2>
          <p className="mt-1.5 whitespace-pre-wrap text-body text-ink-700">
            {result.summary}
          </p>
        </section>
      )}

      {/* ── 移动端分屏切换（<1024px）────────────────────────────── */}
      <div className="lg:hidden">
        <SegmentedControl
          fullWidth
          value={pane}
          onChange={setPane}
          options={PANE_OPTIONS}
          ariaLabel="切换风险清单与合同原文"
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px] lg:items-start">
        {/* ── 合同原文（逐字切片 + 定位高亮）────────────────────── */}
        <section
          ref={textPanelRef}
          className={cn(
            "rounded-r3 border border-line bg-surface",
            pane === "text" ? "block" : "hidden",
            "lg:block"
          )}
        >
          <header className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
            <h2 className="text-body-sm font-semibold text-ink-800">合同原文</h2>
            <Badge variant="neutral" size="sm">
              高亮 {highlights.segments.filter((s) => s.findingIndex !== null).length} 处
            </Badge>
            <span className="ml-auto flex items-center gap-1">
              {activeIndex !== null && (
                <Button size="sm" variant="ghost" onClick={() => setActiveIndex(null)}>
                  取消定位
                </Button>
              )}
              {/* 移动端原文与清单互斥显示，给一个明确的返回入口，避免只能靠顶部切换 */}
              <Button
                size="sm"
                variant="ghost"
                className="lg:hidden"
                onClick={() => setPane("list")}
              >
                返回风险清单
              </Button>
            </span>
          </header>

          {highlights.dropped.length > 0 && (
            <p className="border-b border-line px-4 py-2 text-caption text-pending-600">
              有 {highlights.dropped.length} 条风险的原文区间与其他条目重叠，未在原文中高亮，请以风险清单为准。
            </p>
          )}

          <div className="legal-text scroll-thin max-h-[60vh] overflow-auto whitespace-pre-wrap px-4 py-4 text-ink-800 lg:max-h-[70vh]">
            {highlights.segments.map((seg, i) => {
              const findingIndex = seg.findingIndex;
              if (findingIndex === null) return <span key={i}>{seg.text}</span>;
              return (
                <mark
                  key={i}
                  ref={(el) => {
                    markRefs.current.set(findingIndex, el);
                  }}
                  onClick={() => setActiveIndex(findingIndex)}
                  title="点击可在风险清单中定位该条"
                  className={cn(
                    "cursor-pointer rounded-r1 px-0.5 text-inherit transition-colors duration-fast",
                    HIGHLIGHT_BG[findings[findingIndex]?.risk_level ?? ""] ?? "bg-pending-500/25",
                    activeIndex === findingIndex && "ring-2 ring-brand-500/70"
                  )}
                >
                  {seg.text}
                </mark>
              );
            })}
          </div>
        </section>

        {/* ── 风险清单 ─────────────────────────────────────────── */}
        <aside className={cn("space-y-3", pane === "list" ? "block" : "hidden", "lg:block")}>
          <section className="rounded-r3 border border-line bg-surface">
            <header className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
              <h2 className="text-body-sm font-semibold text-ink-800">风险清单</h2>
              <ProvenanceBadge state={source.provenance} size="sm" />
              <span className="num ml-auto text-caption text-ink-500">{findings.length} 条</span>
            </header>

            <div className="p-3">
              {findings.length === 0 ? (
                analysis.positive ? (
                  <p className="flex items-center gap-1.5 px-1 py-2 text-body-sm text-verified-600">
                    <CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden />
                    已完成逐条通读，未发现风险条款。
                  </p>
                ) : (
                  <p className="flex items-start gap-1.5 px-1 py-2 text-body-sm text-pending-600">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
                    本次未产出风险条目——这并不等于合同安全，请结合上方完成度说明判断。
                  </p>
                )
              ) : (
                <ul className="space-y-2.5">
                  {sorted.map(({ finding, index }) => (
                    <FindingCard
                      key={index}
                      finding={finding}
                      adopted={adopted.has(index)}
                      active={activeIndex === index}
                      citationOpen={openCitations.has(index)}
                      onLocate={() => locate(index)}
                      onToggleAdopt={() => toggleAdopt(index)}
                      onToggleCitation={() => toggleCitation(index)}
                    />
                  ))}
                </ul>
              )}
            </div>
          </section>
        </aside>
      </div>

      {/* ── 修订稿（CR-04：一键采纳 → redline，原文始终保留）──────── */}
      <section className="rounded-r3 border border-line bg-surface">
        <header className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
          <h2 className="text-body-sm font-semibold text-ink-800">修订稿</h2>
          <ProvenanceBadge state={source.provenance} size="sm" />
          <span className="num ml-auto text-caption text-ink-500">
            已采纳 {redline.applied.length} / {adoptableIndices.length} 条
          </span>
        </header>

        <div className="space-y-3 p-4">
          {adoptableIndices.length === 0 ? (
            <p className="text-body-sm text-ink-500">本次未返回可直接采纳的改写文本，无法生成修订稿。</p>
          ) : adopted.size === 0 ? (
            <p className="text-body-sm text-ink-500">
              尚未采纳任何建议。在风险清单中点击「一键采纳」，这里会生成修订后的全文；原始文本始终保留，可随时撤销。
            </p>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <SegmentedControl
                  size="sm"
                  value={redlineMode}
                  onChange={setRedlineMode}
                  options={REDLINE_OPTIONS}
                  ariaLabel="修订稿视图切换"
                />
                <span className="ml-auto flex flex-wrap items-center gap-2">
                  {adoptableIndices.length > adopted.size && (
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => setAdopted(new Set(adoptableIndices))}
                    >
                      全部采纳
                    </Button>
                  )}
                  <Button
                    size="sm"
                    variant="ghost"
                    leftIcon={<RotateCcw className="h-3.5 w-3.5" />}
                    onClick={() => setAdopted(new Set<number>())}
                  >
                    撤销全部采纳
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    leftIcon={<Copy className="h-3.5 w-3.5" />}
                    onClick={() => void copyRedline()}
                    disabled={redline.applied.length === 0}
                  >
                    复制修订后全文
                  </Button>
                </span>
              </div>

              {redline.conflicts.length > 0 && (
                <p className="flex items-start gap-1.5 text-caption leading-relaxed text-pending-600">
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                  有 {redline.conflicts.length} 条已勾选的建议未能替换原文（原文区间无效、与其他条目重叠，或未提供改写文本），请人工处理。
                </p>
              )}

              {redline.applied.length === 0 ? (
                <p className="text-body-sm text-pending-600">
                  已勾选的建议均未能替换原文，修订稿与原文一致，请按上方提示人工处理。
                </p>
              ) : redlineMode === "full" ? (
                <div className="legal-text surface-paper scroll-thin max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-r2 border border-line px-4 py-4 lg:max-h-[70vh]">
                  {redline.segments.map((seg, i) =>
                    seg.findingIndex === null ? (
                      <span key={i}>{seg.text}</span>
                    ) : (
                      <mark key={i} className="rounded-r1 bg-verified-500/25 px-0.5 text-inherit">
                        {seg.text}
                      </mark>
                    )
                  )}
                </div>
              ) : (
                <ul className="space-y-3">
                  {redline.applied.map((i) => {
                    const finding = findings[i];
                    const clauseLabel = findingClauseLabel(finding);
                    return (
                      <li key={i} className="rounded-r2 border border-line p-3">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <Badge variant={RISK_TONE[finding.risk_level ?? ""] ?? "neutral"} size="sm">
                            {RISK_LABEL[finding.risk_level ?? ""] ?? finding.risk_level ?? "未标注等级"}
                          </Badge>
                          <span className="text-body-sm font-medium text-ink-800">
                            {findingTitle(finding)}
                          </span>
                          {clauseLabel && (
                            <span className="num text-caption text-ink-500">{clauseLabel}</span>
                          )}
                        </div>
                        <p className="mt-2 rounded-r2 bg-danger-500/5 px-2.5 py-2 font-serif text-caption leading-relaxed text-ink-600">
                          <span className="mb-0.5 block text-caption font-sans text-ink-500">
                            原文（已替换）
                          </span>
                          <span className="line-through decoration-danger-400">
                            {findingOriginal(finding) || "（原文片段缺失）"}
                          </span>
                        </p>
                        <p className="mt-1.5 rounded-r2 bg-verified-500/5 px-2.5 py-2">
                          <span className="mb-0.5 block text-caption text-ink-500">修订后</span>
                          <span className="legal-text text-ink-800">
                            {suggestionSpec(finding).adoptable}
                          </span>
                        </p>
                      </li>
                    );
                  })}
                </ul>
              )}
            </>
          )}
        </div>
      </section>

      {/* ── 免责声明（CR-06，固定展示）──────────────────────────── */}
      <footer className="rounded-r3 border border-line bg-surface-subtle p-4">
        <p className="flex items-start gap-2 text-body-sm text-ink-600">
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-ink-400" aria-hidden />
          <span>{disclaimer}</span>
        </p>
      </footer>
    </div>
  );
}

export default ResultView;
