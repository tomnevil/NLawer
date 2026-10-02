"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "next/navigation";
import { BookOpen, RotateCcw, Send, ShieldAlert, Sparkles } from "lucide-react";
import { streamSSE } from "@nlaw/sdk";
import {
  Badge,
  BottomSheet,
  CitationChip,
  CitationPanel,
  EmptyState,
  ProvenanceBadge,
  ProvenanceLegend,
  Skeleton,
  cn,
  type Citation as PanelCitation,
  type CitationStatus,
} from "@nlaw/ui";

/* ============================================================================
 * 后端契约（读取 `app/api/v1/qa.py` 与 `app/services/qa_service.py` 确认）
 * ----------------------------------------------------------------------------
 * POST /api/v1/qa/stream  -> SSE，按 `data: {json}\n\n` 逐包：
 *   {type:"delta",   content}                      流式正文片段
 *   {type:"done",    sections, citations, disclaimer}
 *   {type:"blocked", reason, action}               内容安全拦截（停止传输）
 *   {type:"error",   message}                      流中异常
 *
 * citations[] 三种来源，字段并不同构：
 *   LAW       { id, type, title: "法名+条号", excerpt, effective_date, timeliness_warning }
 *   CASE      { id, type, title, excerpt, court, judgment_date }
 *   KNOWLEDGE { id, type, title, excerpt }
 * ========================================================================== */

interface BackendCitation {
  id: number;
  type: string;
  title: string;
  excerpt: string;
  effective_date?: string | null;
  timeliness_warning?: string | null;
  court?: string | null;
  judgment_date?: string | null;
}

interface Sections {
  conclusion?: string;
  legal_basis?: string;
  advice?: string;
  risk?: string;
}

interface Turn {
  key: number;
  role: "user" | "ai";
  /** 用户提问原文 */
  question?: string;
  /** 流式累积正文 */
  text?: string;
  streaming?: boolean;
  /** 生成进度（后端 status 事件），如「正在为您分析…」 */
  status?: string;
  /** 是否四段式结构化；false 表示简单回复（如寒暄），前端渲染单段纯文本 */
  structured?: boolean;
  sections?: Sections;
  citations?: BackendCitation[];
  disclaimer?: string;
  /** 内容安全拦截原因 */
  blocked?: string;
  /** 传输错误 */
  error?: string;
}

const SECTION_LABELS: [string, keyof Sections][] = [
  ["结论", "conclusion"],
  ["法律依据", "legal_basis"],
  ["行动建议", "advice"],
  ["风险提示", "risk"],
];

const TYPE_LABEL: Record<string, string> = {
  LAW: "法律条文",
  CASE: "类案判例",
  KNOWLEDGE: "企业知识",
};

/** 把 `中华人民共和国劳动合同法第八十五条` 拆成法名与条号。 */
function splitLawTitle(raw: string): { name: string; article?: string } {
  const m = raw.match(/^(.*?)(第[零〇一二三四五六七八九十百千0-9]+条(?:之[零〇一二三四五六七八九十]+)?.*)$/);
  if (!m || !m[1]) return { name: raw };
  return { name: m[1], article: m[2] };
}

/** 归一化用于「正文法名 → 引用条目」的匹配。 */
function normalizeLawName(s: string): string {
  return s.replace(/[《》\s]/g, "");
}

/**
 * 后端引用 → `CitationPanel` 条目。
 *
 * 时效状态只在法条上成立：类案判例与内部知识没有「生效 / 废止」概念，
 * 早期把它们一律标成「现行有效」是对律师的误导，现映射为「参考资料」。
 */
function toPanelCitations(list: BackendCitation[]): PanelCitation[] {
  return list.map((c, i) => {
    const isLaw = c.type === "LAW";
    const { name, article } = isLaw ? splitLawTitle(c.title) : { name: c.title, article: undefined };
    let status: CitationStatus = "reference";
    if (isLaw) {
      status = (c.timeliness_warning ?? "").includes("废止") ? "repealed" : "in-force";
    }
    const source = isLaw
      ? undefined
      : c.type === "CASE"
        ? [c.court, c.judgment_date].filter(Boolean).join(" · ") || "类案检索"
        : "企业知识库";

    return {
      id: String(c.id),
      index: i + 1,
      title: isLaw && !name.startsWith("《") ? `《${name}》` : name,
      article,
      source,
      effectiveDate: c.effective_date ?? undefined,
      status,
      excerpt: c.excerpt,
    };
  });
}

/** 正文里的《法名》渲染成「文本 + 引用序号」，点击联动右侧面板。 */
function renderWithChips(
  text: string,
  citations: BackendCitation[],
  activeId: string | null,
  onCite: (id: string) => void
): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /《([^》]+)》/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;

  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));

    const inner = normalizeLawName(m[1]);
    const hit = citations.findIndex((c) => normalizeLawName(c.title).includes(inner));
    out.push(<span key={`law-${k}`}>《{m[1]}》</span>);

    if (hit >= 0) {
      const c = citations[hit];
      out.push(
        <CitationChip
          key={`chip-${k}`}
          index={hit + 1}
          active={activeId === String(c.id)}
          title={c.title}
          onClick={() => onCite(String(c.id))}
        />
      );
    }
    last = m.index + m[0].length;
    k += 1;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/* ============================================================================
 * 页面
 * ========================================================================== */

/**
 * `useSearchParams` 会把整个页面拖入动态渲染，因此把真正用到它的组件
 * 包在 Suspense 里——这样 `/qa` 仍可静态预渲染，首屏不受影响。
 */
export default function QAPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-3">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      }
    >
      <QAWorkspace />
    </Suspense>
  );
}

function QAWorkspace() {
  const searchParams = useSearchParams();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [activeCitationId, setActiveCitationId] = useState<string | null>(null);
  const [citeOpen, setCiteOpen] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const keyRef = useRef(0);
  const prefilled = useRef(false);

  const nextKey = () => {
    keyRef.current += 1;
    return keyRef.current;
  };

  /* ------------------------------------------------------------ 提问 */

  const ask = useCallback(async (question: string) => {
    const q = question.trim();
    if (!q) return;

    setTurns((t) => [
      ...t,
      { key: nextKey(), role: "user", question: q },
      // AI 轮次也带上原始问题，便于失败时「重新生成」
      { key: nextKey(), role: "ai", streaming: true, text: "", question: q },
    ]);
    setStreaming(true);
    setActiveCitationId(null);

    let acc = "";
    try {
      for await (const ev of streamSSE("/api/v1/qa/stream", { question: q })) {
        if (ev?.type === "delta") {
          acc += String(ev.content ?? "");
          setTurns((t) => {
            const copy = [...t];
            copy[copy.length - 1] = { key: copy[copy.length - 1].key, role: "ai", streaming: true, text: acc };
            return copy;
          });
        } else if (ev?.type === "done") {
          setTurns((t) => {
            const copy = [...t];
            copy[copy.length - 1] = {
              key: copy[copy.length - 1].key,
              role: "ai",
              sections: ev.sections as Sections,
              structured: ev.structured !== false,
              citations: (ev.citations ?? []) as BackendCitation[],
              disclaimer: ev.disclaimer as string | undefined,
            };
            return copy;
          });
        } else if (ev?.type === "status") {
          // 进度提示：模型耗时较长时，让用户看到「在干活」而不是空转
          setTurns((t) => {
            const copy = [...t];
            const last = copy[copy.length - 1];
            if (last.role !== "ai") return copy;
            copy[copy.length - 1] = { ...last, streaming: true, status: String(ev.message ?? "") };
            return copy;
          });
        } else if (ev?.type === "blocked") {
          // 内容安全「停止传输」：明确告知用户已拦截，而不是留一个空回复
          setTurns((t) => {
            const copy = [...t];
            copy[copy.length - 1] = {
              key: copy[copy.length - 1].key,
              role: "ai",
              blocked: String(ev.reason ?? "该内容未通过合规校验，已停止生成。"),
            };
            return copy;
          });
        } else if (ev?.type === "error") {
          // 服务端技术异常不上屏，统一给一句人话；原始信息留到控制台便于定位
          console.error("[qa] 服务端流错误", ev.message);
          setTurns((t) => {
            const copy = [...t];
            const last = copy[copy.length - 1];
            copy[copy.length - 1] = { key: last.key, role: "ai", question: last.question, error: "服务暂时不可用，请稍后重试。" };
            return copy;
          });
        }
      }
    } catch (e) {
      // 网络中断 / 连接被代理断开：不把 "network error" 之类原始报文甩给用户
      console.error("[qa] 流式请求失败", e);
      const msg = e instanceof Error && e.name === "AbortError" ? "请求已取消。" : "网络连接中断，请检查网络后重试。";
      setTurns((t) => {
        const copy = [...t];
        const last = copy[copy.length - 1];
        copy[copy.length - 1] = { key: last.key, role: "ai", question: last.question, error: msg };
        return copy;
      });
    } finally {
      setStreaming(false);
    }
  }, []);

  // 顶栏全局搜索跳过来时带 `?q=`，直接当作一次提问。
  // 用 ref 保证只消费一次，否则任何 re-render 都会重复提问。
  useEffect(() => {
    if (prefilled.current) return;
    const q = searchParams.get("q");
    if (!q?.trim()) return;
    prefilled.current = true;
    void ask(q);
  }, [searchParams, ask]);

  const submit = useCallback(() => {
    const q = input.trim();
    if (!q || streaming) return;
    setInput("");
    void ask(q);
  }, [input, streaming, ask]);

  /* ---------------------------------------------------------- 自动滚底 */

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [turns]);

  /* -------------------------------------------------------------- 派生 */

  /** 面板只展示最后一轮 AI 回答的引用——这才是用户当下正在核对的内容。 */
  const latestCitations = useMemo(() => {
    for (let i = turns.length - 1; i >= 0; i -= 1) {
      const t = turns[i];
      if (t.role === "ai" && t.citations) return t.citations;
    }
    return [];
  }, [turns]);

  const panelCitations = useMemo(() => toPanelCitations(latestCitations), [latestCitations]);

  const handleCite = useCallback((id: string) => setActiveCitationId(id), []);

  const latestHasContent = turns.some((t) => t.role === "ai" && (t.citations?.length ?? 0) > 0);

  /* -------------------------------------------------------------- 渲染 */

  return (
    <div className="flex h-[calc(100dvh-var(--topbar-h)-2rem)] gap-0 overflow-hidden rounded-r3 border border-line bg-surface lg:h-[calc(100dvh-var(--topbar-h)-3rem)]">
      {/* ══════════════════ 主栏：对话 ══════════════════ */}
      <section className="flex min-w-0 flex-1 flex-col" aria-label="智能问答">
        <header className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 border-b border-line px-4 py-3 lg:px-5">
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 shrink-0 text-ai-500" />
            <h1 className="text-body font-semibold text-ink-900">智能问答</h1>
            <Badge variant="neutral" size="sm">
              AI 对话
            </Badge>
          </div>

          {/* 三态图例常驻：用户在任何位置都能解读颜色含义（规范第 05 节） */}
          <ProvenanceLegend variant="compact" className="ml-auto" />

          <button
            type="button"
            onClick={() => setCiteOpen(true)}
            disabled={panelCitations.length === 0}
            className={cn(
              "flex h-8 shrink-0 items-center gap-1.5 rounded-r2 border border-line px-2.5",
              "text-body-sm text-ink-600 transition-colors duration-fast hover:bg-surface-hover",
              "disabled:cursor-not-allowed disabled:opacity-40",
              "lg:hidden"
            )}
          >
            <BookOpen className="h-4 w-4" />
            依据
            {panelCitations.length > 0 && <span className="num">({panelCitations.length})</span>}
          </button>
        </header>

        <div ref={scrollRef} className="scroll-thin min-h-0 flex-1 overflow-y-auto px-4 py-5 lg:px-6">
          {turns.length === 0 ? (
            <div className="mx-auto max-w-reading pt-10">
              <EmptyState
                icon={<Sparkles className="h-5 w-5" />}
                title="输入法律问题，AI 即时作答"
                description="我会用自然语言为您分析，并在右侧列出引用的法条与类案，可逐条核对原文。"
              />
            </div>
          ) : (
            <div className="mx-auto max-w-reading space-y-6">
              {turns.map((t) =>
                t.role === "user" ? (
                  <div key={t.key} className="flex justify-end">
                    <p className="max-w-[85%] whitespace-pre-wrap rounded-r3 rounded-br-r1 bg-solid-brand px-4 py-2.5 text-body-sm text-white">
                      {t.question}
                    </p>
                  </div>
                ) : (
                  <AiTurn
                    key={t.key}
                    turn={t}
                    activeCitationId={activeCitationId}
                    onCite={handleCite}
                    onRetry={() => {
                      if (t.question) void ask(t.question);
                    }}
                  />
                )
              )}
            </div>
          )}
        </div>

        <div className="shrink-0 border-t border-line px-4 py-3 lg:px-6">
          <div className="mx-auto flex max-w-reading items-end gap-2 rounded-r4 border border-line bg-surface-subtle p-2 transition-colors duration-fast focus-within:border-brand-400 focus-within:bg-surface focus-within:ring-2 focus-within:ring-brand-500/25">
            <label htmlFor="qa-input" className="sr-only">
              输入法律问题
            </label>
            <input
              id="qa-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
              placeholder="例如：公司拖欠工资三个月怎么办？"
              className="h-9 min-w-0 flex-1 bg-transparent px-2 text-body-sm text-ink-900 outline-none placeholder:text-ink-400"
            />
            <button
              type="button"
              onClick={submit}
              disabled={streaming || !input.trim()}
              className={cn(
                "flex h-9 shrink-0 items-center gap-1.5 rounded-r2 px-3.5 text-body-sm font-semibold text-white",
                "bg-solid-brand transition-opacity duration-fast hover:opacity-90",
                "disabled:cursor-not-allowed disabled:opacity-40",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40"
              )}
            >
              <Send className="h-4 w-4" />
              {streaming ? "生成中" : "发送"}
            </button>
          </div>
          <p className="mx-auto mt-2 max-w-reading text-center text-caption text-ink-400">
            AI 生成内容仅供参考，不构成正式法律意见。引用法条请以官方文本为准。
          </p>
        </div>
      </section>

      {/* ══════════════ 右栏：引用溯源（≥1024 常驻 380px） ══════════════ */}
      <CitationPanel
        citations={panelCitations}
        activeId={activeCitationId ?? undefined}
        onSelect={(c) => setActiveCitationId(c.id)}
        emptyText={
          latestHasContent
            ? "本次回答未引用法条或文件。"
            : "提问后，这里会列出本次回答引用的全部法条与类案。"
        }
        className="hidden lg:flex"
      />

      {/* 窄屏：同一份引用收进底部抽屉，不再用弹窗遮挡正文 */}
      <BottomSheet
        isOpen={citeOpen}
        onClose={() => setCiteOpen(false)}
        title="引用溯源"
        description={panelCitations.length > 0 ? `共 ${panelCitations.length} 条依据` : "暂无依据"}
        heightRatio={0.85}
      >
        <CitationPanel
          citations={panelCitations}
          activeId={activeCitationId ?? undefined}
          onSelect={(c) => setActiveCitationId(c.id)}
          variant="inline"
          className="border-0"
        />
      </BottomSheet>
    </div>
  );
}

/* ============================================================================
 * 单轮 AI 回答
 * ========================================================================== */

function AiTurn({
  turn,
  activeCitationId,
  onCite,
  onRetry,
}: {
  turn: Turn;
  activeCitationId: string | null;
  onCite: (id: string) => void;
  onRetry: () => void;
}) {
  /* 内容安全拦截 */
  if (turn.blocked) {
    return (
      <div className="flex items-start gap-2.5 rounded-r3 border border-danger-500/30 bg-danger-500/10 p-4">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-danger-500" />
        <div>
          <p className="text-body-sm font-medium text-danger-600">该提问未通过合规校验</p>
          <p className="mt-1 text-body-sm text-ink-600">{turn.blocked}</p>
        </div>
      </div>
    );
  }

  /* 传输错误 */
  if (turn.error) {
    return (
      <div className="rounded-r3 border border-danger-500/30 bg-danger-500/10 p-4">
        <p className="text-body-sm font-medium text-danger-600">生成中断</p>
        <p className="mt-1 text-body-sm text-ink-600">{turn.error}</p>
        {turn.question && (
          <button
            type="button"
            onClick={onRetry}
            className="mt-3 inline-flex h-8 items-center gap-1.5 rounded-r2 border border-danger-500/40 bg-surface px-3 text-body-sm font-medium text-danger-600 transition-colors duration-fast hover:bg-danger-500/10"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            重新生成
          </button>
        )}
      </div>
    );
  }

  /* 流式生成中 */
  if (turn.streaming) {
    return (
      <div className="ai-content rounded-r3 border border-ai-500/25 bg-ai-500/[0.05] p-4">
        <div className="mb-2 flex items-center gap-2">
          <ProvenanceBadge state="ai" size="sm" />
          <span className="text-caption text-ink-500">
            {turn.text ? "正在生成…" : turn.status || "正在生成…"}
          </span>
        </div>
        <p className="whitespace-pre-wrap font-serif text-body leading-[1.85] text-ink-700">
          {turn.text}
          <span className="ml-0.5 inline-block h-4 w-0.5 animate-pulse-soft bg-ai-500 align-middle" />
        </p>
      </div>
    );
  }

  const citations = turn.citations ?? [];

  /* 简单回复（如寒暄）：单段纯文本，不渲染四段式骨架 */
  if (turn.structured === false) {
    return (
      <article className="rounded-r3 border border-line bg-surface shadow-s1">
        <header className="flex items-center gap-2 border-b border-line bg-ai-500/[0.05] px-4 py-2.5">
          <ProvenanceBadge state="ai" size="sm" />
          <span className="text-caption text-ink-500">AI 助手 · 未经律师确认</span>
        </header>
        <div className="space-y-4 p-4">
          <p className="whitespace-pre-wrap text-body leading-[1.75] text-ink-700">
            {turn.sections?.conclusion}
          </p>
          {turn.disclaimer && (
            <p className="border-t border-line pt-3 text-caption text-ink-400">{turn.disclaimer}</p>
          )}
        </div>
      </article>
    );
  }

  return (
    <article className="rounded-r3 border border-line bg-surface shadow-s1">
      {/* 责任边界：AI 产出的内容必须显式标注，客户要能一眼看出这不是律师写的 */}
      <header className="flex flex-wrap items-center gap-x-2 gap-y-1.5 border-b border-line bg-ai-500/[0.05] px-4 py-2.5">
        <ProvenanceBadge state="ai" size="sm" />
        <span className="text-caption text-ink-500">四段式结构化输出 · 未经律师确认</span>
        {citations.length > 0 && (
          <span className="ml-auto flex items-center gap-1 text-caption text-ink-500">
            <BookOpen className="h-3.5 w-3.5" />
            引用 <span className="num">{citations.length}</span> 条
          </span>
        )}
      </header>

      <div className="space-y-4 p-4">
        {SECTION_LABELS.map(([label, key]) => {
          const body = turn.sections?.[key];
          if (!body) return null;
          // 结论段里的《法名》挂引用序号；法律依据段整段用衬线体，是「原文」质感
          const isConclusion = key === "conclusion";
          const isLegal = key === "legal_basis";
          return (
            <section key={key}>
              <h2 className="mb-1.5 text-label font-semibold text-ink-800">{label}</h2>
              <p
                className={cn(
                  "whitespace-pre-wrap text-body text-ink-700",
                  isLegal ? "font-serif leading-[1.85]" : "leading-[1.75]"
                )}
              >
                {isConclusion ? renderWithChips(body, citations, activeCitationId, onCite) : body}
              </p>
            </section>
          );
        })}

        {/* 引用摘要：正文之外再给一次可点击的入口，右侧面板同步定位 */}
        {citations.length > 0 && (
          <div className="border-t border-line pt-3">
            <p className="mb-2 flex items-center gap-1.5 text-label font-medium text-ink-600">
              <BookOpen className="h-3.5 w-3.5" />
              引用溯源
            </p>
            <ul className="flex flex-wrap gap-1.5">
              {citations.map((c, i) => {
                const active = activeCitationId === String(c.id);
                return (
                  <li key={c.id}>
                    <button
                      type="button"
                      onClick={() => onCite(String(c.id))}
                      className={cn(
                        "flex items-center gap-1.5 rounded-r2 border px-2 py-1 text-caption transition-colors duration-fast",
                        active
                          ? "border-brand-500 bg-brand-500/10 text-link"
                          : "border-line bg-surface-subtle text-ink-600 hover:border-brand-400 hover:text-link"
                      )}
                    >
                      <span className="num font-medium text-gold-600">{i + 1}</span>
                      <span className="max-w-[16rem] truncate">{c.title}</span>
                      <span className="shrink-0 text-ink-400">{TYPE_LABEL[c.type] ?? c.type}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        )}

        {turn.disclaimer && (
          <p className="border-t border-line pt-3 text-caption text-ink-400">{turn.disclaimer}</p>
        )}
      </div>
    </article>
  );
}
