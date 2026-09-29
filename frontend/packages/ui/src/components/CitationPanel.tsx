"use client";

import React, { useEffect, useRef } from "react";
import { cn } from "../lib/cn";

/**
 * 引用时效状态。颜色不可独立承载语义，因此始终配文字。
 *
 * `reference` 用于类案与内部知识这类**不具备「生效 / 废止」语义**的来源。
 * 早期只有前三种状态，导致类案被强行标成「现行有效」——对律师是明确的
 * 错误信息（判决书不「有效」，只有是否具参考价值）。
 */
export type CitationStatus = "in-force" | "amended" | "repealed" | "reference";

export interface Citation {
  id: string;
  /** 引用序号，与正文中的 CitationChip 对应 */
  index: number;
  /** 法条名 / 文件名 */
  title: string;
  /** 第 X 条 / 第 X 款 */
  article?: string;
  /** 法律名 / 文件来源 */
  source?: string;
  /** 生效日期（已格式化字符串） */
  effectiveDate?: string;
  /** 时效状态，默认 `in-force` */
  status?: CitationStatus;
  /** 原文摘录（以衬线体呈现） */
  excerpt: string;
  /** 需要在摘录中高亮的片段 */
  highlight?: string;
  /** 原文链接 */
  url?: string;
}

export interface CitationPanelProps {
  citations: Citation[];
  /** 当前选中的引用 id，会自动滚动到可视区域 */
  activeId?: string;
  onSelect?: (citation: Citation) => void;
  /** 传了才显示关闭按钮（移动端页内展开时用） */
  onClose?: () => void;
  /**
   * `panel`  —— 桌面常驻右侧面板，固定 380px；
   * `inline` —— 移动端页内展开，占满宽度。
   */
  variant?: "panel" | "inline";
  emptyText?: string;
  className?: string;
}

const STATUS_META: Record<CitationStatus, { label: string; cls: string }> = {
  "in-force": { label: "现行有效", cls: "bg-verified-500/15 text-verified-600 border-verified-500/30" },
  amended: { label: "已修订", cls: "bg-pending-500/15 text-pending-600 border-pending-500/30" },
  repealed: { label: "已废止", cls: "bg-danger-500/15 text-danger-600 border-danger-500/30" },
  // 类案 / 内部知识：不适用时效判定，用中性色明确「这是参考而非依据」
  reference: { label: "参考资料", cls: "bg-surface-subtle text-ink-600 border-line" },
};

/** 把摘录中的高亮片段包进 <mark>。 */
function renderExcerpt(excerpt: string, highlight?: string): React.ReactNode {
  if (!highlight) return excerpt;
  const at = excerpt.indexOf(highlight);
  if (at === -1) return excerpt;
  return (
    <>
      {excerpt.slice(0, at)}
      <mark className="rounded-[2px] bg-gold-500/20 px-0.5 text-ink-900">{highlight}</mark>
      {excerpt.slice(at + highlight.length)}
    </>
  );
}

const CitationCard: React.FC<{
  citation: Citation;
  active: boolean;
  onSelect?: (c: Citation) => void;
}> = ({ citation, active, onSelect }) => {
  const status = STATUS_META[citation.status ?? "in-force"];

  return (
    <article
      className={cn(
        "border-b border-line px-4 py-4 transition-colors duration-base ease-out",
        active ? "bg-brand-500/5" : "hover:bg-surface-hover/60"
      )}
    >
      <header className="mb-2 flex items-start gap-2">
        <span
          className={cn(
            "num mt-0.5 flex h-[18px] min-w-[18px] shrink-0 items-center justify-center rounded-r1 px-1 text-caption font-medium leading-none",
            active ? "bg-gold-600 text-white" : "bg-gold-500/15 text-gold-700"
          )}
        >
          {citation.index}
        </span>
        <div className="min-w-0 flex-1">
          <h4 className="text-body-sm font-medium text-ink-900">
            {citation.title}
            {citation.article && <span className="text-ink-600"> · {citation.article}</span>}
          </h4>
          {citation.source && <p className="mt-0.5 text-caption text-ink-500">{citation.source}</p>}
        </div>
        <span className={cn("shrink-0 rounded-r1 border px-1.5 py-0.5 text-caption", status.cls)}>
          {status.label}
        </span>
      </header>

      {/* 法条原文用衬线体 + 1.85 行高，这是最重要的差异化手段 */}
      <blockquote className="legal-text border-l-2 border-line-strong pl-3 text-ink-700">
        {renderExcerpt(citation.excerpt, citation.highlight)}
      </blockquote>

      <footer className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-caption text-ink-500">
        {citation.effectiveDate && (
          <span>
            生效日期 <span className="num text-ink-600">{citation.effectiveDate}</span>
          </span>
        )}
        {citation.url && (
          <a
            href={citation.url}
            target="_blank"
            rel="noreferrer"
            className="text-link transition-colors duration-fast hover:text-link-hover"
          >
            查看原文 ↗
          </a>
        )}
        {onSelect && (
          <button
            type="button"
            onClick={() => onSelect(citation)}
            className="text-link transition-colors duration-fast hover:text-link-hover"
          >
            定位正文
          </button>
        )}
      </footer>
    </article>
  );
};

/**
 * 引用溯源面板（规范第 07 节 / 决策 04）。
 *
 * 由旧版的「弹窗抽屉遮挡正文」改为**桌面右侧常驻面板 380px**——
 * 核心场景是「边读结论边核对法条」，任何遮挡正文的交互都会破坏它。
 * 移动端改为页内展开（`variant="inline"`），不强行套用桌面布局。
 */
export const CitationPanel: React.FC<CitationPanelProps> = ({
  citations,
  activeId,
  onSelect,
  onClose,
  variant = "panel",
  emptyText = "本次回答未引用法条或文件。",
  className,
}) => {
  const activeRef = useRef<HTMLDivElement>(null);

  // 选中引用变化时滚动到可视区域
  useEffect(() => {
    if (!activeId) return;
    activeRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [activeId]);

  const isPanel = variant === "panel";

  return (
    <aside
      className={cn(
        "flex flex-col bg-surface",
        isPanel ? "w-citation shrink-0 border-l border-line" : "w-full rounded-r3 border border-line",
        className
      )}
      aria-label="引用溯源"
    >
      <header
        className={cn(
          "flex shrink-0 items-center justify-between gap-2 border-b border-line px-4",
          isPanel ? "h-topbar" : "py-3"
        )}
      >
        <div className="min-w-0">
          <h3 className="text-h4 font-semibold text-ink-900">引用溯源</h3>
          <p className="text-caption text-ink-500">
            {citations.length > 0 ? `共 ${citations.length} 条依据` : "暂无依据"}
          </p>
        </div>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="收起引用面板"
            className="tap-ghost flex h-8 w-8 shrink-0 items-center justify-center rounded-r1 text-ink-400 transition-colors duration-fast hover:bg-surface-hover hover:text-ink-900"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        )}
      </header>

      <div className="scroll-thin flex-1 overflow-y-auto">
        {citations.length === 0 ? (
          <p className="px-4 py-8 text-center text-body-sm text-ink-500">{emptyText}</p>
        ) : (
          citations.map((c) => (
            <div key={c.id} ref={c.id === activeId ? activeRef : undefined}>
              <CitationCard citation={c} active={c.id === activeId} onSelect={onSelect} />
            </div>
          ))
        )}
      </div>
    </aside>
  );
};

CitationPanel.displayName = "CitationPanel";
