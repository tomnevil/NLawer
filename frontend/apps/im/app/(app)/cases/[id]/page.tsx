"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  FileText,
  MessageSquare,
} from "lucide-react";
import { authed } from "@nlaw/sdk";
import { Badge, EmptyState, Skeleton, Timeline, type TimelineItem } from "@nlaw/ui";

import {
  CASE_STATUS_TONE,
  GRADE_TONE,
  caseStatusLabel,
  errText,
  fmtAmount,
  fmtDateTime,
  type CaseDetail,
  type CaseEvent,
  type ChecklistItem,
  type EvidenceItem,
} from "../../../../lib/imApi";

/* ============================================================================
 * 案件详情（客户端 · `/cases/[id]`）
 * ----------------------------------------------------------------------------
 * 四个端点并行取，用 `Promise.allSettled` 而不是 `all`：
 * 材料清单或证据接口异常时**不应连带吞掉**案件概要与时间线 —— 面板要能「部分可用」。
 * 这个策略与 `chat/page.tsx` 的案件上下文面板一致。
 *
 * 【承办律师只显示编号】
 * `CaseOut` 只给 `lawyer_id`，后端没有「按 id 查用户」的公开端点。
 * ⇒ **如实显示律师编号，不编造姓名**（与 chat 页同一取舍）。
 * 真实姓名只在会话的派单卡片里有，那里才显示。
 * ========================================================================== */

export default function ImCaseDetailPage() {
  const params = useParams<{ id: string }>();
  const caseId = params?.id;

  const [info, setInfo] = useState<CaseDetail | null>(null);
  const [events, setEvents] = useState<CaseEvent[]>([]);
  const [checklist, setChecklist] = useState<ChecklistItem[]>([]);
  const [evidence, setEvidence] = useState<EvidenceItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!caseId) return;
    let alive = true;
    setLoading(true);
    setError(null);

    Promise.allSettled([
      authed<CaseDetail>(`/api/v1/cases/${caseId}`),
      authed<CaseEvent[]>(`/api/v1/cases/${caseId}/events`),
      authed<ChecklistItem[]>(`/api/v1/evidence/cases/${caseId}/missing`),
      authed<EvidenceItem[]>(`/api/v1/evidence/cases/${caseId}`),
    ]).then(([c, ev, ck, ed]) => {
      if (!alive) return;
      if (c.status === "rejected") {
        // 案件主体拿不到 ⇒ 这一页没有意义，如实报错而不是渲染一个空壳
        setError(errText(c.reason));
      } else {
        setInfo(c.value);
      }
      setEvents(ev.status === "fulfilled" ? ev.value ?? [] : []);
      setChecklist(ck.status === "fulfilled" ? ck.value ?? [] : []);
      setEvidence(ed.status === "fulfilled" ? ed.value ?? [] : []);
      setLoading(false);
    });

    return () => {
      alive = false;
    };
  }, [caseId]);

  const timelineItems: TimelineItem[] = useMemo(
    () =>
      events.map((e, i) => ({
        id: String(e.id),
        title: e.title,
        description: e.description ?? undefined,
        time: fmtDateTime(e.occurred_at),
        status: i === events.length - 1 ? "current" : "done",
      })),
    [events]
  );

  const missing = useMemo(() => checklist.filter((c) => c.missing), [checklist]);
  const have = useMemo(() => checklist.filter((c) => !c.missing), [checklist]);

  if (loading) {
    return (
      <div className="scroll-thin h-full overflow-y-auto">
        <div className="mx-auto w-full max-w-[560px] space-y-3 px-4 pt-5 lg:max-w-[720px] lg:px-6">
          <Skeleton className="h-8 w-40" />
          <Skeleton className="h-28 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      </div>
    );
  }

  if (error || !info) {
    return (
      <div className="scroll-thin h-full overflow-y-auto">
        <div className="mx-auto w-full max-w-[560px] px-4 pt-5 lg:max-w-[720px] lg:px-6">
          <BackLink />
          <p className="mt-4 rounded-r3 border border-danger-500/30 bg-danger-500/10 p-3 text-body-sm text-danger-600">
            {error ?? "案件不存在"}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="scroll-thin h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-[560px] px-4 pb-6 lg:max-w-[720px] lg:px-6">
        <div className="pt-4">
          <BackLink />
        </div>

        <header className="mt-3">
          <h1 className="text-h4 leading-snug text-ink-900">{info.title}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Badge variant={CASE_STATUS_TONE[info.status] ?? "neutral"} dot>
              {caseStatusLabel(info.status)}
            </Badge>
            {info.grade && <Badge variant={GRADE_TONE[info.grade] ?? "neutral"}>等级 {info.grade}</Badge>}
            <span className="num text-caption text-ink-400">{info.case_no}</span>
          </div>
        </header>

        {/* ─────────────────────── 案件概要 ─────────────────────── */}
        <section className="mt-4 rounded-r3 border border-line bg-surface p-4">
          <h2 className="text-caption font-medium uppercase tracking-wider text-ink-400">案件概要</h2>
          <dl className="mt-2 divide-y divide-line">
            <Row label="争议类型" value={info.dispute_type || "—"} />
            <Row label="标的额" value={fmtAmount(info.claim_amount)} mono />
            <Row
              label="承办律师"
              // 拿不到姓名就显示编号，不编造（见文件头说明）
              value={info.lawyer_id ? `律师 #${info.lawyer_id}` : "尚未指派"}
            />
          </dl>
          {info.summary && (
            <p className="mt-3 border-t border-line pt-3 text-body-sm text-ink-700">
              {info.summary}
            </p>
          )}
        </section>

        {/* ─────────────────────── 争议焦点 ─────────────────────── */}
        {info.focus && (
          <section className="mt-3 rounded-r3 border border-line bg-surface-subtle p-4">
            <h2 className="text-caption font-medium uppercase tracking-wider text-ink-400">争议焦点</h2>
            <p className="mt-1.5 text-body-sm text-ink-800">{info.focus}</p>
          </section>
        )}

        {/* ─────────────────────── 案件进度 ─────────────────────── */}
        <section className="mt-5">
          <h2 className="px-0.5 text-body font-medium text-ink-900">案件进度</h2>
          <div className="mt-3">
            {timelineItems.length === 0 ? (
              <p className="rounded-r3 border border-line bg-surface-subtle p-3 text-body-sm text-ink-500">
                暂无进度记录。案件有新进展时会在这里按时间倒序出现。
              </p>
            ) : (
              <Timeline items={timelineItems} />
            )}
          </div>
        </section>

        {/* ─────────────────────── 材料清单 ─────────────────────── */}
        <section className="mt-5">
          <div className="flex items-baseline gap-2 px-0.5">
            <h2 className="text-body font-medium text-ink-900">材料清单</h2>
            {missing.length > 0 && (
              <span className="num text-caption text-danger-600">{missing.length} 项待补</span>
            )}
          </div>

          <div className="mt-2">
            {checklist.length === 0 ? (
              <p className="rounded-r3 border border-line bg-surface-subtle p-3 text-body-sm text-ink-500">
                暂未生成材料清单。案件进入办案流程后会按案由自动列出所需材料。
              </p>
            ) : (
              <ul className="overflow-hidden rounded-r3 border border-line bg-surface">
                {checklist.map((c) => (
                  <li
                    key={`${c.category}-${c.item}`}
                    className="flex min-h-tap items-start gap-2.5 border-b border-line px-4 py-3 last:border-b-0"
                  >
                    {c.missing ? (
                      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-danger-500" />
                    ) : (
                      <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-verified-500" />
                    )}
                    <span className="min-w-0 flex-1">
                      <span
                        className={
                          "block text-body-sm " + (c.missing ? "text-ink-800" : "text-ink-500 line-through")
                        }
                      >
                        {c.item}
                      </span>
                      <span className="mt-0.5 block text-caption text-ink-400">
                        {c.category}
                        {c.description ? ` · ${c.description}` : ""}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* 已上传材料：与清单分开，因为「有清单」和「有文件」是两件事 */}
          {evidence.length > 0 && (
            <>
              <h3 className="mt-4 px-0.5 text-caption font-medium uppercase tracking-wider text-ink-400">
                已上传材料（{evidence.length}）
              </h3>
              <ul className="mt-2 space-y-1.5">
                {evidence.map((e) => (
                  <li
                    key={e.id}
                    className="flex items-center gap-2 rounded-r2 border border-line bg-surface px-3 py-2.5"
                  >
                    <FileText className="h-4 w-4 shrink-0 text-ink-400" />
                    <span className="min-w-0 flex-1 truncate text-body-sm text-ink-700">{e.name}</span>
                    <span className="shrink-0 text-caption text-ink-400">{e.category}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>

        {/* ─────────────────────── 下一步 ─────────────────────── */}
        <Link
          href="/chat"
          className="mt-5 flex min-h-tap items-center justify-center gap-2 rounded-r3 bg-solid-brand px-4 text-body font-medium text-white transition-opacity duration-fast hover:opacity-90"
        >
          <MessageSquare className="h-4 w-4" />
          在会话中沟通
        </Link>
      </div>
    </div>
  );
}

function BackLink() {
  return (
    <Link
      href="/cases"
      className="-ml-2 inline-flex min-h-tap items-center gap-0.5 rounded-r2 px-2 text-body-sm text-ink-600 transition-colors duration-fast hover:text-ink-900"
    >
      <ChevronLeft className="h-4 w-4" />
      我的案件
    </Link>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-2.5">
      <dt className="shrink-0 text-caption text-ink-500">{label}</dt>
      <dd className={"text-right text-body-sm text-ink-800 " + (mono ? "num" : "")}>{value}</dd>
    </div>
  );
}
