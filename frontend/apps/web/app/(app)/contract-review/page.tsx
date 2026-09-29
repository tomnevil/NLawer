"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Inbox,
  Info,
  RotateCcw,
  ScanLine,
  Upload,
} from "lucide-react";
import { ApiError, authed, http, isContentTooLarge, onContentTooLarge } from "@nlaw/sdk";
import {
  Alert,
  Badge,
  Button,
  CountBadge,
  GateBanner,
  Spinner,
  StageProgress,
  TaskCenterDrawer,
  cn,
  jobStage,
  useToast,
  type TaskCenterJob,
} from "@nlaw/ui";
import {
  DEFAULT_DISCLAIMER,
  billingSpec,
  type ContractReviewResult,
} from "./types";
import { ResultView } from "./ResultView";

/* ============================================================================
 * 合同审查（P0-16 / CR-13）
 * ----------------------------------------------------------------------------
 * POST /api/v1/documents/contract-review   {title?, source_text, evidence_id?}
 *   -> { id, title, source, status, analysis_status, overall_risk, coverage,
 *        disclaimer, summary, findings[], usage? }
 *
 * 长合同（>20k）走异步：
 * POST /api/v1/documents/contract-review/async  -> 202 { job_id }
 * GET  /api/v1/jobs/{id}                        -> 进度（step_state 含 contract_review_id）
 * GET  /api/v1/documents/contract-review/{id}   -> 取回结论
 *
 * ## 本页的三条实现约束（都有踩过的坑在背后）
 *
 * 1. **提交的原文必须逐字保留**。审查结果的 `char_start/char_end` 是相对
 *    `source_text` 的字符下标，任何 trim / 规范化都会让高亮整体错位。
 *    因此这里把「提交时用的文本」单独存进 `submitted`，与 textarea 的实时
 *    内容解耦——用户在结果页点「修改原文」时，改动不会污染已提交的区间。
 *
 * 2. **失败与降级必须各有各的界面**。`status=failed` 时**不渲染任何风险结论**
 *    （连 findings 都不读）；请求本身失败时给可重试的错误页。两者都不得白屏。
 *
 * 3. **本轮不做导出 Word**（CR-05 已降级）。此处不放导出按钮，也不放
 *    「即将上线」占位——承诺一个不存在的能力比不做更糟。
 *
 * ## 413 决策门（B1）
 *
 * 超限后后端返回 413（带 `field` / `max_length` / `guidance`），SDK 会把它
 * 派发成全局事件（见 `onContentTooLarge`）。本页订阅后渲染 `GateBanner`：
 * **绝不清空 `draft`**（超限即丢内容是信任崩塌的头号来源）。
 * ========================================================================== */

const FIELD_CLS = cn(
  "w-full rounded-r2 border border-line bg-surface px-3 py-2 text-body-sm text-ink-800",
  "placeholder:text-ink-400",
  "transition-colors duration-fast focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-500/30"
);

/**
 * 同步端点 `source_text` 的上限（字符）。
 *
 * ⚠️ 这只是**首屏的默认值**：权威值来自后端 413 错误体的 `max_length`
 * （见下方 `onContentTooLarge` 订阅）。后端调整上限时这里会自动跟上，
 * 不需要前后端同步改一次常量。
 */
const DEFAULT_SOURCE_TEXT_MAX = 20000;

/** 轮询异步任务的间隔。 */
const POLL_MS = 3000;

function errText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "请求失败";
}

type Stage = "input" | "reviewing" | "done";

interface GateState {
  field?: string | null;
  maxLength?: number | null;
  guidance?: string | null;
}

export default function ContractReviewPage() {
  const { addToast } = useToast();
  const fileRef = useRef<HTMLInputElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const [stage, setStage] = useState<Stage>("input");
  const [title, setTitle] = useState("");
  /** textarea 的实时内容（用户可编辑） */
  const [draft, setDraft] = useState("");
  /** 本次请求实际提交的内容——结果页的所有切片都基于它 */
  const [submitted, setSubmitted] = useState<{ title: string; text: string } | null>(null);
  const [result, setResult] = useState<ContractReviewResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);

  /* ---- B1 决策门 + B2 任务中心 ---- */
  const [maxLength, setMaxLength] = useState(DEFAULT_SOURCE_TEXT_MAX);
  const [gate, setGate] = useState<GateState | null>(null);
  /** 异步任务：id 决定轮询，实体用于渲染进度 */
  const [jobId, setJobId] = useState<number | null>(null);
  const [job, setJob] = useState<TaskCenterJob | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [jobs, setJobs] = useState<TaskCenterJob[]>([]);

  // 审查是耗时操作：给出「已用时」，避免用户以为页面卡死
  useEffect(() => {
    if (stage !== "reviewing") return;
    const timer = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(timer);
  }, [stage]);

  /**
   * 订阅全局 413。
   *
   * 只处理本页相关的路径：其它页面触发的超限不该在这里弹决策门。
   * `max_length` 以**后端返回为准**回写，使计数徽标与真实上限始终一致。
   */
  useEffect(() => {
    return onContentTooLarge((e) => {
      if (!e.path.includes("/contract-review")) return;
      if (e.detail.max_length) setMaxLength(e.detail.max_length);
      setGate({
        field: e.detail.field ?? null,
        maxLength: e.detail.max_length ?? null,
        guidance: e.detail.guidance ?? null,
      });
    });
  }, []);

  const fetchJobs = useCallback(async () => {
    try {
      const page = await http.get<{ items: TaskCenterJob[] }>("/api/v1/jobs", {
        query: { page_size: 20 },
      });
      setJobs(page?.items ?? []);
    } catch {
      // 任务中心是**辅助入口**：取数失败不能打断主流程，静默即可
    }
  }, []);

  /** 长合同走异步：拿到 job_id 后交给轮询。 */
  const submitAsync = useCallback(
    async (text: string, reviewTitle: string) => {
      try {
        const data = await authed<{ job_id: number }>(
          "/api/v1/documents/contract-review/async",
          {
            method: "POST",
            body: reviewTitle ? { title: reviewTitle, source_text: text } : { source_text: text },
          }
        );
        if (!data?.job_id) throw new Error("服务端未返回 job_id");
        setSubmitted({ title: reviewTitle, text });
        setResult(null);
        setError(null);
        setElapsed(0);
        setGate(null);
        setJobId(data.job_id);
        setStage("reviewing");
        addToast({
          type: "info",
          title: "已转为后台任务",
          message: "可在任务中心查看进度，完成后会自动打开结论。",
        });
      } catch (e) {
        setError(errText(e));
        setStage("done");
      }
    },
    [addToast]
  );

  /**
   * 轮询异步任务。
   *
   * 依赖只有 `jobId`——把整个 `job` 放进依赖会让它每次轮询都重新订阅，
   * 进而把计时器重置（表现为进度永不推进）。
   */
  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;

    const tick = async () => {
      try {
        const cur = await http.get<TaskCenterJob>(`/api/v1/jobs/${jobId}`);
        if (cancelled || !cur) return;
        setJob(cur);

        const st = String(cur.status ?? "").toLowerCase();
        if (st === "completed") {
          const cid = (cur as unknown as { step_state?: { contract_review_id?: number } })
            .step_state?.contract_review_id;
          if (!cid) {
            if (!cancelled) {
              setError("任务已完成但未产出审查记录");
              setStage("done");
              setJobId(null);
            }
            return;
          }
          const data = await authed<ContractReviewResult>(
            `/api/v1/documents/contract-review/${cid}`
          );
          if (cancelled) return;
          setResult(data);
          setStage("done");
          setJobId(null);
        } else if (st === "failed") {
          if (cancelled) return;
          setError(cur.error_message || "后台审查失败");
          setStage("done");
          setJobId(null);
        }
      } catch {
        // 单次轮询失败保持等待：任务已落库，下一轮会重试
      }
    };

    void tick();
    const timer = setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [jobId]);

  const run = useCallback(async (reviewTitle: string, text: string) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setSubmitted({ title: reviewTitle, text });
    setResult(null);
    setError(null);
    setElapsed(0);
    setStage("reviewing");

    try {
      const data = await authed<ContractReviewResult>("/api/v1/documents/contract-review", {
        method: "POST",
        // 不传 title 时让后端决定默认标题（避免前端编造标题）
        body: reviewTitle ? { title: reviewTitle, source_text: text } : { source_text: text },
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      if (!data || typeof data !== "object") {
        // 响应体没有 data：这是接口契约违约，不能当成「无风险的审查结果」
        throw new Error("服务端未返回审查结果（响应缺少 data 字段）");
      }
      setResult(data);
      setStage("done");
    } catch (e) {
      if (controller.signal.aborted) return;
      // 413 **不是「审查失败」**：它是「这条路走不通，换一条」。
      // 早期实现把它一并塞进 `setStage("done")` ⇒ 直接渲染失败结果页，
      // 输入表单连同其中的 GateBanner 一起被卸载 ⇒ 决策门永远看不见，
      // 用户只拿到「重试 / 返回修改原文」两个必然再次失败的出口
      // （2026-09-24 真实浏览器端到端实测发现；静态门禁与 API 冒烟全绿照漏）。
      // 正确行为：留在输入态、原文保留，由 GateBanner 给出「转异步」出口。
      if (isContentTooLarge(e)) {
        setStage("input");
        return;
      }
      setError(errText(e));
      setStage("done");
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  }, []);

  const submit = useCallback(() => {
    // 刻意不 trim：前后空白属于原文，裁剪会改变字符下标
    if (!draft.trim()) {
      addToast({ type: "warning", title: "请先粘贴合同原文", message: "输入框还是空的。" });
      return;
    }
    void run(title.trim(), draft);
  }, [draft, title, run, addToast]);

  const retry = useCallback(() => {
    if (!submitted) return;
    void run(submitted.title, submitted.text);
  }, [submitted, run]);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStage("input");
  }, []);

  const backToInput = useCallback(() => {
    setStage("input");
    setResult(null);
    setError(null);
  }, []);

  const pickFile = useCallback(
    async (file: File | undefined) => {
      if (!file) return;
      if (!/\.(txt|md|markdown|text)$/i.test(file.name)) {
        addToast({
          type: "warning",
          title: "暂不支持该文件类型",
          message: "当前只支持 .txt / .md 纯文本；PDF 与 Word 需先自行转成文本再粘贴。",
        });
        return;
      }
      try {
        const text = await file.text();
        const name = file.name.replace(/\.[^.]+$/, "");
        // 超过在线上限的文件**不塞进 textarea**（塞进去也只会再次被 413），
        // 直接转异步——这正是 413 的 `guidance=upload_or_async` 指向的路。
        if (text.length > maxLength) {
          addToast({
            type: "info",
            title: "文件超过在线上限",
            message: `共 ${text.length} 字，已转为后台任务处理。`,
          });
          void submitAsync(text, title.trim() || name);
          return;
        }
        setDraft(text);
        if (!title.trim()) setTitle(name);
        addToast({ type: "success", title: "已读入文件", message: `${file.name} · ${text.length} 字` });
      } catch (e) {
        addToast({ type: "error", title: "读取文件失败", message: errText(e) });
      }
    },
    [title, maxLength, addToast, submitAsync]
  );

  const taskCenter = (
    <TaskCenterDrawer
      isOpen={drawerOpen}
      onClose={() => setDrawerOpen(false)}
      jobs={jobs}
      onRefresh={() => void fetchJobs()}
      onRetry={async (id) => {
        try {
          await authed(`/api/v1/jobs/${id}/retry`, { method: "POST", body: {} });
          await fetchJobs();
        } catch (e) {
          addToast({ type: "error", title: "重试失败", message: errText(e) });
        }
      }}
    />
  );

  /* ------------------------------------------------------------ 输入态 */

  if (stage === "input") {
    return (
      <div className="space-y-4">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-h2 text-ink-900">合同审查</h1>
            <p className="mt-1 text-body-sm text-ink-500">
              粘贴合同原文 → 逐条定位风险 → 一键采纳生成修订稿
            </p>
          </div>
          <Button
            variant="secondary"
            leftIcon={<Inbox className="h-3.5 w-3.5" />}
            onClick={() => {
              void fetchJobs();
              setDrawerOpen(true);
            }}
          >
            任务中心
          </Button>
        </header>

        <Alert variant="info" title="提交前请知悉" icon={<Info className="h-5 w-5" />}>
          <ul className="list-disc space-y-1 pl-4">
            <li>审查结果为 <strong className="font-medium">AI 辅助意见，不构成法律意见</strong>，定稿前请逐条核对。</li>
            <li>按次计费；<strong className="font-medium">仅当本次由 AI 模型完整分析成功时才计费</strong>，降级或失败不计费。</li>
            <li>结果页会标注本次结果的来源与覆盖度，请以标注为准判断可信程度。</li>
          </ul>
        </Alert>

        <section className="space-y-4 rounded-r3 border border-line bg-surface p-4 sm:p-5">
          <div>
            <label htmlFor="cr-title" className="mb-1 block text-body-sm text-ink-700">
              合同名称（选填）
            </label>
            <input
              id="cr-title"
              className={FIELD_CLS}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="如：设备采购合同（不填则由系统命名）"
            />
          </div>

          <div>
            <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
              <label htmlFor="cr-text" className="text-body-sm text-ink-700">
                合同原文<span className="ml-0.5 text-danger-600">*</span>
              </label>
              <div className="flex items-center gap-2">
                <CountBadge value={draft.length} max={maxLength} />
                <input
                  ref={fileRef}
                  type="file"
                  accept=".txt,.md,.markdown,text/plain"
                  className="hidden"
                  onChange={(e) => {
                    void pickFile(e.target.files?.[0]);
                    // 允许重复选择同一个文件
                    e.target.value = "";
                  }}
                />
                <Button
                  size="sm"
                  variant="secondary"
                  leftIcon={<Upload className="h-3.5 w-3.5" />}
                  onClick={() => fileRef.current?.click()}
                >
                  读取文本文件
                </Button>
              </div>
            </div>
            <textarea
              id="cr-text"
              className={cn(FIELD_CLS, "min-h-[16rem] font-serif leading-[1.9]")}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="在此粘贴合同全文。请尽量保留原文的条款分段与编号——审查结果会按字符位置定位到原文，改动排版不影响定位，但请勿删改正文内容。"
              spellCheck={false}
            />
            <p className="mt-1 text-caption text-ink-500">
              支持 .txt / .md 文本文件；PDF 与 Word 需先自行转成文本。原文仅用于本次审查，不会因换行或空格调整而影响定位。
            </p>
          </div>

          {/* 413 决策门：**非阻断**横幅。draft 全程保留，绝不清空。 */}
          {gate && (
            <GateBanner
              text={draft}
              field={gate.field}
              maxLength={gate.maxLength}
              guidance={gate.guidance}
              fileName={title.trim() || undefined}
              onSaveAsTxt={async (file) => {
                void submitAsync(await file.text(), title.trim());
              }}
              onPickFile={() => fileRef.current?.click()}
              onDismiss={() => setGate(null)}
            />
          )}

          <div className="flex flex-wrap items-center gap-2">
            <Button leftIcon={<ScanLine className="h-3.5 w-3.5" />} onClick={submit}>
              开始审查
            </Button>
            {draft && (
              <Button
                variant="ghost"
                leftIcon={<RotateCcw className="h-3.5 w-3.5" />}
                onClick={() => {
                  setDraft("");
                  setTitle("");
                }}
              >
                清空
              </Button>
            )}
          </div>
        </section>

        <p className="text-caption leading-relaxed text-ink-500">{DEFAULT_DISCLAIMER}</p>
        {taskCenter}
      </div>
    );
  }

  /* ------------------------------------------------------------ 加载态 */

  if (stage === "reviewing") {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 text-center">
        <Spinner size="lg" />
        <div>
          <h1 className="text-h3 text-ink-900">正在审查合同</h1>
          {/* 异步任务给出**阶段 + 百分比**：不确定的等待比长等待更痛苦 */}
          {job ? (
            <div className="mx-auto mt-2 w-full max-w-sm text-left">
              <StageProgress stage={jobStage(job)} progress={job.progress ?? 0} />
            </div>
          ) : (
            <p className="num mt-1 text-body-sm text-ink-500">
              已用时 {elapsed} 秒 · 正在逐条通读，长合同需要更久
            </p>
          )}
        </div>
        <Button variant="secondary" onClick={cancel}>
          取消并返回
        </Button>
        <p className="max-w-md text-caption leading-relaxed text-ink-400">
          {job
            ? "已转为后台任务，离开页面也不会丢失；可在任务中心查看进度。"
            : "审查期间请勿关闭页面；取消只会终止本次等待。"}
        </p>
      </div>
    );
  }

  /* -------------------------------------------------- 请求失败 / 审查失败 */

  const sourceText = submitted?.text ?? "";
  const failedByStatus = result?.status === "failed";

  if (error || failedByStatus) {
    // 计费口径以接口返回为准，不在这里推断（failed 亦可能有 usage 字段）
    const billing = billingSpec(result?.usage);
    /*
     * `error_message` 只在 status=failed 时存在（键不存在 = 不适用）。
     * 服务端刻意只给人话文案、不暴露环境变量与内部地址，因此这里原样展示、
     * 不做任何加工或翻译。请求本身失败（非 failed 状态）时没有这个字段，
     * 退回展示 ApiError 的 message。
     */
    const failureReason = (result?.error_message ?? "").trim();

    return (
      <div className="space-y-4">
        <header>
          <h1 className="text-h2 text-ink-900">{submitted?.title || "合同审查"}</h1>
        </header>

        <section className="rounded-r3 border border-danger-500/30 bg-danger-500/10 p-5">
          <h2 className="flex items-start gap-2 text-h4 text-danger-600">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
            本次审查未能完成，未产出任何审查结论
          </h2>
          <p className="mt-2 text-body-sm text-ink-700">
            {failedByStatus
              ? failureReason || "分析过程失败，因此不会给出任何风险判断。请稍后重试。"
              : `请求失败：${error}`}
          </p>
          <div className="mt-2 space-y-1">
            <p className="flex flex-wrap items-center gap-2 text-body-sm text-ink-700">
              <Badge variant={billing.charged ? "neutral" : "verified"} size="sm">
                {billing.charged ? "已计费" : "未计费"}
              </Badge>
              <span>{billing.text}</span>
              {billing.escalated && (
                <Badge variant="pending" size="sm">
                  已转人工工单
                </Badge>
              )}
            </p>
            {billing.note && (
              <p className="text-caption leading-relaxed text-pending-600">{billing.note}</p>
            )}
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button onClick={retry} disabled={!submitted}>
              重试
            </Button>
            <Button variant="ghost" onClick={backToInput}>
              返回修改原文
            </Button>
          </div>
        </section>

        <footer className="rounded-r3 border border-line bg-surface-subtle p-4">
          <p className="flex items-start gap-2 text-body-sm text-ink-600">
            <Info className="mt-0.5 h-4 w-4 shrink-0 text-ink-400" aria-hidden />
            <span>{result?.disclaimer?.trim() || DEFAULT_DISCLAIMER}</span>
          </p>
        </footer>
      </div>
    );
  }

  /* ------------------------------------------------------------ 结果态 */

  if (!result) {
    // 理论上不可达：done 且无 error、无 result 说明状态机被改坏了。
    // 给一个可恢复的界面，而不是空白页。
    return (
      <div className="space-y-4">
        <Alert variant="error" title="页面状态异常" icon={<AlertTriangle className="h-5 w-5" />}>
          未能取得审查结果。
        </Alert>
        <Button onClick={backToInput}>返回重新提交</Button>
      </div>
    );
  }

  return (
    <ResultView
      result={result}
      sourceText={sourceText}
      onRetry={retry}
      onReset={backToInput}
    />
  );
}
