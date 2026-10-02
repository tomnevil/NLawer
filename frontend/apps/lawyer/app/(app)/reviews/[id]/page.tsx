"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  AlertTriangle,
  ChevronLeft,
  ClipboardCheck,
  Gavel,
  MessageSquareText,
  ShieldCheck,
} from "lucide-react";
import { ApiError, authed } from "@nlaw/sdk";
import {
  Badge,
  Button,
  CollapseGroup,
  CollapsePanel,
  EmptyState,
  ProvenanceBadge,
  Skeleton,
  Spinner,
  cn,
} from "@nlaw/ui";

/* ============================================================================
 * 律师端 · 复核详情（咨询报告类）
 * ----------------------------------------------------------------------------
 * GET /api/v1/reviews/{id}             -> ReviewOut + consult_report（四段式 + 引用）
 * GET /api/v1/reviews/{id}/transcript  -> 关联会话原文（点击「查看完整聊天记录」按需加载）
 *
 * 可见性：后端已按复核门禁约束——仅承办人 / 调度资格者可看；客户不可访问。
 * 聊天记录含客户 PII，故严格受同一门禁保护，不在列表/详情默认吐出，需显式展开。
 * ========================================================================== */

interface Citation {
  id?: string;
  title?: string;
  source?: string;
  url?: string;
  type?: string;
}

interface TranscriptMsg {
  id: number;
  sender: "CLIENT" | "AI" | "LAWYER" | string;
  msg_type: string;
  content: string | null;
  card_payload: Record<string, unknown> | null;
  citation_ids: string[] | null;
  created_at: string | null;
}

interface ConsultReport {
  id: number;
  status: string;
  question: string | null;
  answer: string | null;
  draft_sections: Record<string, string> | null;
  citations: Citation[] | null;
  review_id: number | null;
  lawyer_id: number | null;
  conversation_id: number | null;
  final_report: string | null;
  signed_by: number | null;
  signed_at: string | null;
  lawyer_name: string | null;
}

interface ReviewDetail {
  id: number;
  target_type: string;
  target_id: number;
  status: string;
  required_level: string;
  assignee_id?: number | null;
  decision?: string | null;
  comment?: string | null;
  consult_report?: ConsultReport;
}

const SECTIONS: { key: string; label: string; icon: typeof Gavel }[] = [
  { key: "conclusion", label: "结论", icon: Gavel },
  { key: "legal_basis", label: "法律依据", icon: ShieldCheck },
  { key: "advice", label: "建议", icon: ClipboardCheck },
  { key: "risk", label: "风险提示", icon: AlertTriangle },
];

const STATUS_LABEL: Record<string, string> = {
  draft: "AI 初稿",
  lawyer_editing: "律师修改中",
  pending_confirm: "待复核确认",
  confirmed: "已定稿",
  archived: "已归档",
  voided: "已作废",
};

const STATUS_TONE: Record<string, "neutral" | "pending" | "primary" | "verified" | "danger"> = {
  draft: "neutral",
  lawyer_editing: "pending",
  pending_confirm: "primary",
  confirmed: "verified",
  archived: "neutral",
  voided: "danger",
};

function errText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "请求失败";
}

function TranscriptBubble({ m }: { m: TranscriptMsg }) {
  const tone =
    m.sender === "CLIENT"
      ? "border-line bg-surface"
      : m.sender === "LAWYER"
        ? "border-brand-500/30 bg-brand-500/5"
        : "border-line bg-surface-hover";
  const who = m.sender === "CLIENT" ? "客户" : m.sender === "LAWYER" ? "律师" : "AI";
  const isCard = m.msg_type === "CARD" && m.card_payload;
  return (
    <div className={cn("rounded-r3 border p-3", tone)}>
      <div className="mb-1 flex items-center gap-2 text-caption text-ink-500">
        <span className="font-medium text-ink-700">{who}</span>
        {m.created_at && <span className="num">{m.created_at.replace("T", " ").slice(0, 16)}</span>}
      </div>
      {isCard ? (
        <div className="text-body-sm text-ink-800">
          <MessageSquareText className="mr-1 inline h-3.5 w-3.5 text-brand-500" />
          {(m.card_payload?.summary as string) || (m.card_payload?.kind as string) || "卡片消息"}
          {Boolean(m.card_payload?.lawyer_name) && (
            <span className="ml-1 text-caption text-ink-500">· {String(m.card_payload?.lawyer_name)}</span>
          )}
        </div>
      ) : (
        <p className="whitespace-pre-wrap text-body-sm text-ink-800">{m.content || "（空）"}</p>
      )}
    </div>
  );
}

export default function ReviewDetailPage() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const id = Number(params?.id);

  const [data, setData] = useState<ReviewDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [transcript, setTranscript] = useState<TranscriptMsg[] | null>(null);
  const [transcriptLoading, setTranscriptLoading] = useState(false);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);
  const [transcriptOpen, setTranscriptOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const d = await authed<ReviewDetail>(`/api/v1/reviews/${id}`);
      setData(d);
    } catch (e) {
      setError(errText(e));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    if (Number.isFinite(id)) void load();
  }, [id, load]);

  const loadTranscript = useCallback(async () => {
    if (transcript !== null) return; // 已加载过，避免重复拉取
    setTranscriptLoading(true);
    setTranscriptError(null);
    try {
      const list = await authed<TranscriptMsg[]>(`/api/v1/reviews/${id}/transcript`);
      setTranscript(list);
    } catch (e) {
      setTranscriptError(errText(e));
    } finally {
      setTranscriptLoading(false);
    }
  }, [id, transcript]);

  const onToggleTranscript = useCallback(
    (open: boolean) => {
      setTranscriptOpen(open);
      if (open) void loadTranscript();
    },
    [loadTranscript],
  );

  const cr = data?.consult_report;

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <header className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => router.back()}
          className="flex h-9 w-9 items-center justify-center rounded-r2 border border-line text-ink-600 transition-colors duration-fast hover:bg-surface-hover"
          aria-label="返回"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
        <div className="min-w-0">
          <h1 className="text-h2 text-ink-900">咨询复核</h1>
          <p className="mt-0.5 num text-caption text-ink-500">复核任务 #{id}</p>
        </div>
        {data && (
          <Badge variant={STATUS_TONE[data.status] ?? "neutral"} size="sm" className="ml-auto">
            {STATUS_LABEL[data.status] ?? data.status}
          </Badge>
        )}
      </header>

      {error ? (
        <div className="rounded-r3 border border-danger-500/30 bg-danger-500/10 p-4">
          <p className="flex items-start gap-2 text-body-sm text-danger-600">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            {error}
          </p>
          <button
            type="button"
            onClick={() => void load()}
            className="mt-2 text-body-sm text-link hover:text-link-hover"
          >
            重试
          </button>
        </div>
      ) : loading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      ) : !cr ? (
        <EmptyState
          icon={<ClipboardCheck className="h-5 w-5" />}
          title="该复核非咨询报告类"
          description="案件分析 / 文书类复核请在对应案件页查看。"
        />
      ) : (
        <div className="space-y-4">
          {cr.status === "confirmed" && cr.lawyer_name && (
            <div className="flex items-center gap-2 rounded-r3 border border-verified-500/30 bg-verified-500/5 px-3 py-2 text-body-sm text-verified-700">
              <ShieldCheck className="h-4 w-4 shrink-0" />
              已由执业律师 <span className="font-medium">{cr.lawyer_name}</span> 确认出具
              {cr.signed_at && (
                <span className="num text-caption text-ink-500">· {cr.signed_at.replace("T", " ").slice(0, 16)}</span>
              )}
            </div>
          )}

          <section className="rounded-r3 border border-line bg-surface p-4">
            <h2 className="text-body-sm font-semibold text-ink-700">客户问题</h2>
            <p className="mt-1.5 whitespace-pre-wrap text-body-sm text-ink-900">{cr.question || "（无）"}</p>
          </section>

          <section className="space-y-3">
            <div className="flex items-center gap-2 text-caption text-ink-500">
              <ProvenanceBadge state="ai" size="sm" />
              AI 归纳草稿（四段式）· 客户侧不可见
            </div>
            {SECTIONS.map((s) => {
              const text = cr.draft_sections?.[s.key];
              if (!text) return null;
              const Icon = s.icon;
              return (
                <div key={s.key} className="rounded-r3 border border-line bg-surface p-4">
                  <h3 className="flex items-center gap-1.5 text-body-sm font-semibold text-ink-700">
                    <Icon className="h-4 w-4 text-brand-500" />
                    {s.label}
                  </h3>
                  <p className="mt-1.5 whitespace-pre-wrap text-body-sm text-ink-800">{text}</p>
                </div>
              );
            })}
          </section>

          {cr.citations && cr.citations.length > 0 && (
            <section className="rounded-r3 border border-line bg-surface p-4">
              <h2 className="text-body-sm font-semibold text-ink-700">引用依据</h2>
              <ul className="mt-2 space-y-1.5">
                {cr.citations.map((c, i) => (
                  <li key={c.id ?? i} className="text-body-sm text-ink-800">
                    <span className="num text-caption text-ink-500">[{i + 1}]</span> {c.title || c.id || "未命名依据"}
                    {c.source && <span className="ml-1 text-caption text-ink-500">· {c.source}</span>}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <CollapseGroup>
            <CollapsePanel
              title="查看完整聊天记录"
              icon={<MessageSquareText className="h-4 w-4" />}
              defaultOpen={false}
              onToggle={onToggleTranscript}
            >
              {transcriptLoading ? (
                <div className="flex items-center gap-2 py-2 text-body-sm text-ink-500">
                  <Spinner size="sm" label={null} /> 加载中…
                </div>
              ) : transcriptError ? (
                <p className="py-2 text-body-sm text-danger-600">{transcriptError}</p>
              ) : transcript && transcript.length > 0 ? (
                <div className="space-y-2 py-1">
                  {transcript.map((m) => (
                    <TranscriptBubble key={m.id} m={m} />
                  ))}
                </div>
              ) : (
                <p className="py-2 text-body-sm text-ink-500">该咨询暂无关联会话记录。</p>
              )}
            </CollapsePanel>
          </CollapseGroup>

          {data && (data.decision || data.comment) && (
            <section className="rounded-r3 border border-line bg-surface p-4">
              <h2 className="text-body-sm font-semibold text-ink-700">复核结论</h2>
              <p className="mt-1.5 text-body-sm text-ink-800">
                {data.decision ? `结论：${data.decision}` : ""}
                {data.comment ? ` · ${data.comment}` : ""}
              </p>
            </section>
          )}
        </div>
      )}
    </div>
  );
}
