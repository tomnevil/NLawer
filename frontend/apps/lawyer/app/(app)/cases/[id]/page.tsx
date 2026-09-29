"use client";

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  AlertTriangle,
  Archive,
  BookOpen,
  Camera,
  ChevronLeft,
  ClipboardCheck,
  Download,
  FileText,
  FolderOpen,
  Gavel,
  History,
  Lightbulb,
  ListChecks,
  RefreshCw,
  Scale,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import { ApiError, authed, upload } from "@nlaw/sdk";
import {
  Badge,
  Button,
  CameraCapture,
  CitationChip,
  CollapseGroup,
  CollapsePanel,
  EmptyState,
  MobileActionBar,
  ProvenanceBadge,
  ProvenanceLegend,
  Skeleton,
  Spinner,
  Timeline,
  cn,
  useSession,
  useSyncQueueOptional,
  useToast,
  type BadgeProps,
  type CapturedFile,
  type ProvenanceState,
  type TimelineItem,
} from "@nlaw/ui";

import {
  SYNC_SECTION,
  isOfflineFailure,
  type LawyerSyncPayload,
} from "../../../../lib/syncTransport";

/* ============================================================================
 * 律师案件详情页（概念图 05）
 * ----------------------------------------------------------------------------
 * 后端契约（均为 GET，页面加载不产生任何写操作）：
 *   GET /api/v1/cases/{id}                        -> CaseOut
 *   GET /api/v1/analyses/case/{id}                -> CaseAnalysisOut（404 = 尚未生成）
 *   GET /api/v1/evidence/cases/{id}               -> EvidenceOut[]
 *   GET /api/v1/evidence/cases/{id}/missing       -> [{item,category,priority,description,missing}]
 *   GET /api/v1/cases/{id}/events                 -> CaseEvent[]
 *   GET /api/v1/reviews?target_type=CASE_ANALYSIS -> Page<ReviewOut>
 *   GET /api/v1/reviews/{id}/records              -> ReviewRecordOut[]
 *   GET /api/v1/archives/cases/{id}               -> ArchiveOut（404 = 未归档）
 *
 * 三个「概念图有、后端没有」的取舍，已在下方逐处标注：
 *   ① 分段级确认态 —— 后端只有 analysis 级 `confirmed_by`/`ai_generated`
 *   ② 转他人 / 导出卷宗 —— 无对应端点
 *   ③ 阶段条时间戳 —— CaseEvent 无法可靠映射到阶段，改用「案件动态」标签页呈现真实事件
 * ========================================================================== */

interface CaseDetail {
  id: number;
  case_no: string;
  title: string;
  status: string;
  intent?: string | null;
  grade: string;
  dispute_type?: string | null;
  party_a?: string | null;
  party_b?: string | null;
  focus?: string | null;
  claim_amount?: number | null;
  urgency?: number;
  complexity?: number;
  require_formal_opinion?: boolean;
  summary?: string | null;
  lawyer_id?: number | null;
  client_user_id?: number | null;
}

/* 六段式的真实字段形状（见 `app/services/case_copilot.py` 的写入处）。
 * 注意 `app/models/analysis.py` 的列注释写的是 `[{law, article, …}]`，
 * 与实际写入的 `law_name` / `article_no` 不一致——以**写入处**为准，
 * 按注释取键会渲染出 `undefined`。 */
interface RelatedLaw {
  law_name?: string;
  article_no?: string;
  excerpt?: string;
  citation_id?: number;
}
interface SimilarCase {
  case_no?: string;
  title?: string;
  court?: string;
  holding?: string;
  citation_id?: number;
}
interface Suggestion {
  path?: string;
  pros?: string;
  cons?: string;
}
interface MissingInfo {
  item?: string;
  reason?: string;
  priority?: number;
}

interface CaseAnalysis {
  id: number;
  case_id: number;
  version: number;
  summary?: string | null;
  legal_analysis?: string | null;
  related_laws?: RelatedLaw[] | null;
  similar_cases?: SimilarCase[] | null;
  suggestions?: Suggestion[] | null;
  missing_info?: MissingInfo[] | null;
  status: string;
  required_level: string;
  forced_hits?: unknown[] | null;
  ai_generated?: boolean;
  iteration_notes?: string[] | null;
  confirmed_by?: number | null;
  confirmed_at?: string | null;
}

interface EvidenceItem {
  id: number;
  name: string;
  file_path: string;
  file_type?: string | null;
  file_size?: number | null;
  status: string;
  category: string;
  category_confidence?: number | null;
  legality_risks?: unknown[] | null;
  event_date?: string | null;
  parse_error?: string | null;
}

interface ChecklistItem {
  item: string;
  category: string;
  priority: number;
  description?: string | null;
  missing: boolean;
}

interface ReviewRow {
  id: number;
  target_type: string;
  target_id: number;
  case_id?: number | null;
  status: string;
  required_level: string;
  satisfied_level?: string | null;
  is_forced?: boolean;
  forced_hits?: unknown[] | null;
  assignee_id?: number | null;
  decided_by?: number | null;
  decision?: string | null;
  comment?: string | null;
}

interface ReviewRecord {
  id: number;
  action: string;
  actor_id?: number | null;
  actor_role?: string | null;
  level?: string | null;
  from_status?: string | null;
  to_status?: string | null;
  comment?: string | null;
}

interface CaseEvent {
  id: number;
  event_type: string;
  title: string;
  description?: string | null;
  occurred_at?: string | null;
  actor_user_id?: number | null;
}

interface ArchiveInfo {
  id: number;
  archive_no: string;
  title: string;
  current_version: number;
  retention_years: number;
  hearing_pack_path?: string | null;
  archived_at?: string | null;
}

/* ---------------------------------------------------------------- 文案映射 */

/** 后端 `CaseStatus` 全量九态。缺项会在界面上漏出裸英文枚举值。 */
const CASE_STATUS_LABEL: Record<string, string> = {
  INTAKE: "已收案",
  PENDING_DISPATCH: "待派单",
  DISPATCHED: "已派单",
  ACCEPTED: "已接单",
  IN_REVIEW: "复核中",
  CONFIRMED: "已定稿",
  ARCHIVED: "已归档",
  CLOSED: "已结案",
  VOIDED: "已作废",
};

const CASE_STATUS_TONE: Record<string, BadgeProps["variant"]> = {
  INTAKE: "neutral",
  PENDING_DISPATCH: "pending",
  DISPATCHED: "pending",
  ACCEPTED: "verified",
  IN_REVIEW: "primary",
  CONFIRMED: "verified",
  ARCHIVED: "neutral",
  CLOSED: "neutral",
  VOIDED: "danger",
};

const GRADE_TONE: Record<string, BadgeProps["variant"]> = {
  S: "danger",
  A: "pending",
  B: "info",
  C: "neutral",
};

/** `ReviewStatus` 六态（小写，见 `app/models/enums.py`）。 */
const REVIEW_STATUS_LABEL: Record<string, string> = {
  draft: "AI 初稿",
  lawyer_editing: "律师修改中",
  pending_confirm: "待复核确认",
  confirmed: "已定稿",
  archived: "已归档",
  voided: "已作废",
};

const REVIEW_ACTION_LABEL: Record<string, string> = {
  CREATE: "创建复核任务",
  EDIT: "律师修改",
  SUBMIT: "提交复核",
  APPROVE: "复核通过",
  APPROVE_PARTIAL: "部分级别通过",
  REQUEST_REVISION: "退回修改",
  REJECT: "复核未通过",
  ARCHIVE: "归档",
  VOID: "作废",
};

const LEVEL_LABEL: Record<string, string> = {
  L1: "L1 · AI 自检",
  L2: "L2 · 律师复核",
  L3: "L3 · 合伙人终审",
};

const EVIDENCE_STATUS_LABEL: Record<string, string> = {
  UPLOADED: "待解析",
  PARSING: "解析中",
  PARSED: "已解析",
  FAILED: "解析失败",
};

const EVIDENCE_CATEGORY_LABEL: Record<string, string> = {
  CONTRACT: "合同类",
  PAYMENT: "支付凭证类",
  COMMUNICATION: "沟通记录类",
  IDENTITY: "身份证明类",
  OFFICIAL: "公文书类",
  OTHER: "其他",
};

/** 阶段条只画**线性主路径**；CLOSED / VOIDED 是终态分支，单独标识。 */
const STAGE_FLOW: { id: string; label: string }[] = [
  { id: "INTAKE", label: "已收案" },
  { id: "PENDING_DISPATCH", label: "待派单" },
  { id: "DISPATCHED", label: "已派单" },
  { id: "ACCEPTED", label: "已接单" },
  { id: "IN_REVIEW", label: "复核中" },
  { id: "CONFIRMED", label: "已定稿" },
  { id: "ARCHIVED", label: "已归档" },
];

type SectionKey =
  | "summary"
  | "legal_analysis"
  | "related_laws"
  | "similar_cases"
  | "suggestions"
  | "missing_info";

const SECTIONS: {
  key: SectionKey;
  n: number;
  title: string;
  hint: string;
  icon: ReactNode;
}[] = [
  {
    key: "summary",
    n: 1,
    title: "案件摘要",
    hint: "当事人 · 纠纷类型 · 争议焦点 · 标的额",
    icon: <FileText className="h-4 w-4" />,
  },
  {
    key: "legal_analysis",
    n: 2,
    title: "法律分析",
    hint: "法律关系与请求权基础",
    icon: <Scale className="h-4 w-4" />,
  },
  {
    key: "related_laws",
    n: 3,
    title: "相关法条",
    hint: "检索命中的法条原文",
    icon: <BookOpen className="h-4 w-4" />,
  },
  {
    key: "similar_cases",
    n: 4,
    title: "类案参考",
    hint: "相似判例与裁判要旨",
    icon: <Gavel className="h-4 w-4" />,
  },
  {
    key: "suggestions",
    n: 5,
    title: "初步建议",
    hint: "可行路径与利弊权衡",
    icon: <Lightbulb className="h-4 w-4" />,
  },
  {
    key: "missing_info",
    n: 6,
    title: "待补充信息",
    hint: "按优先级排列的补充清单",
    icon: <ListChecks className="h-4 w-4" />,
  },
];

/* ------------------------------------------------------------------ 工具 */

function errText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "请求失败";
}

function isNotFound(e: unknown): boolean {
  return e instanceof ApiError && e.status === 404;
}

function fmtAmount(n?: number | null): string {
  if (n === null || n === undefined) return "—";
  return `¥${n.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}`;
}

function fmtDateTime(raw?: string | null): string {
  if (!raw) return "—";
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function fmtSize(bytes?: number | null): string {
  if (!bytes || bytes <= 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

const PRIORITY_LABEL: Record<number, string> = { 1: "关键", 2: "重要", 3: "补充" };

/**
 * 分析级责任边界态。
 *
 * ⚠️ 概念图 05 画的是**逐段**确认（每段各有「律师已确认 · 王振宇 09-15」）。
 * 后端不支持：`CaseAnalysis` 只有分析级的 `confirmed_by` / `confirmed_at` /
 * `ai_generated`，没有分段确认字段，`CaseAnalysisVersion` 也无法反推「哪一段被确认」。
 * 因此这里只给**分析级**结论，并把徽章放在面板头部与右侧说明卡，
 * 不下沉到每一段——给六段都挂同一个「律师已确认」，视觉上像分段确认，
 * 实际上是假的，对律师是误导。
 */
function analysisProvenance(a: CaseAnalysis | null): ProvenanceState {
  if (!a) return "pending";
  if (a.confirmed_by) return "verified";
  if (a.status === "pending_confirm" || a.status === "confirmed" || a.status === "archived") {
    return "pending";
  }
  // ai_generated === false 表示律师完全手写，责任主体是人，归入已确认侧
  return a.ai_generated === false ? "verified" : "ai";
}

/** 证据核验等级：解析失败 / 合法性风险 → 红；未解析完成 → 黄；其余 → 绿。 */
function evidenceFlag(e: EvidenceItem): "ok" | "warn" | "risk" {
  if (e.status === "FAILED" || e.parse_error) return "risk";
  if ((e.legality_risks ?? []).length > 0) return "risk";
  if (e.status !== "PARSED") return "warn";
  return "ok";
}

const FLAG_CLS: Record<"ok" | "warn" | "risk", string> = {
  ok: "bg-verified-500",
  warn: "bg-pending-500",
  risk: "bg-danger-500",
};

const FLAG_LABEL: Record<"ok" | "warn" | "risk", string> = {
  ok: "已核验",
  warn: "待核验",
  risk: "有问题",
};

/* ============================================================================
 * 页面
 * ========================================================================== */

type TabKey = "analysis" | "evidence" | "review" | "events" | "archive";

export default function CaseDetailPage() {
  const routeParams = useParams<{ id: string }>();
  const caseId = Number(routeParams?.id);
  const router = useRouter();
  const { user } = useSession();
  const { addToast } = useToast();

  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [analysis, setAnalysis] = useState<CaseAnalysis | null>(null);
  const [evidence, setEvidence] = useState<EvidenceItem[]>([]);
  const [checklist, setChecklist] = useState<ChecklistItem[]>([]);
  const [events, setEvents] = useState<CaseEvent[]>([]);
  const [review, setReview] = useState<ReviewRow | null>(null);
  const [records, setRecords] = useState<ReviewRecord[]>([]);
  const [archive, setArchive] = useState<ArchiveInfo | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [tab, setTab] = useState<TabKey>("analysis");
  const [activeCite, setActiveCite] = useState<number | null>(null);

  /* ------------------------------------------------------------ 数据加载 */

  /**
   * 案件本体：失败即整页不可用，单独 try 以便区分「案件不存在」与「子资源失败」。
   */
  const loadCase = useCallback(async () => {
    const c = await authed<CaseDetail>(`/api/v1/cases/${caseId}`);
    setDetail(c);
    return c;
  }, [caseId]);

  /**
   * 子资源一律 `allSettled`。
   *
   * 为什么：分析可能尚未生成（404）、卷宗可能尚未归档（404）、复核任务可能
   * 尚未创建（空列表）——这些**都是正常业务状态，不是错误**。用 `all` 会让
   * 一个 404 把整页打成错误态，律师连案件标题都看不到。
   */
  const loadSubResources = useCallback(async () => {
    const [a, ev, miss, eo, rv, ar] = await Promise.allSettled([
      authed<CaseAnalysis>(`/api/v1/analyses/case/${caseId}`),
      authed<EvidenceItem[]>(`/api/v1/evidence/cases/${caseId}`),
      authed<ChecklistItem[]>(`/api/v1/evidence/cases/${caseId}/missing`),
      authed<CaseEvent[]>(`/api/v1/cases/${caseId}/events`),
      authed<{ items: ReviewRow[] }>(
        "/api/v1/reviews?target_type=CASE_ANALYSIS&page=1&page_size=50"
      ),
      authed<ArchiveInfo>(`/api/v1/archives/cases/${caseId}`),
    ]);

    const aVal = a.status === "fulfilled" ? a.value : null;
    setAnalysis(aVal);
    setEvidence(ev.status === "fulfilled" ? ev.value ?? [] : []);
    setChecklist(miss.status === "fulfilled" ? miss.value ?? [] : []);
    setEvents(eo.status === "fulfilled" ? eo.value ?? [] : []);
    setArchive(ar.status === "fulfilled" ? ar.value : null);

    /* 复核任务：后端 `/reviews` 只支持按 target_type 过滤，没有 target_id 参数，
     * 因此在客户端按 `target_id === analysis.id` 收敛。
     * 这里刻意**不**调 `POST /reviews/ensure`——旧实现在页面加载时就建复核任务，
     * 导致「看一眼详情」也会产生一条复核记录。写操作必须由按钮触发。 */
    let matched: ReviewRow | null = null;
    if (rv.status === "fulfilled" && aVal) {
      matched =
        (rv.value?.items ?? []).find(
          (x) => x.target_type === "CASE_ANALYSIS" && x.target_id === aVal.id
        ) ?? null;
    }
    setReview(matched);

    if (matched) {
      try {
        setRecords(await authed<ReviewRecord[]>(`/api/v1/reviews/${matched.id}/records`));
      } catch {
        setRecords([]);
      }
    } else {
      setRecords([]);
    }
  }, [caseId]);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      await loadCase();
      await loadSubResources();
    } catch (e) {
      setError(errText(e));
      setDetail(null);
    } finally {
      setLoading(false);
    }
  }, [loadCase, loadSubResources]);

  useEffect(() => {
    if (!Number.isFinite(caseId)) {
      setError("案件编号不合法");
      setLoading(false);
      return;
    }
    void loadAll();
  }, [caseId, loadAll]);

  /* ------------------------------------------------------------ 写操作 */

  /**
   * 离线队列（可选）。Provider 由 `app/(app)/layout.tsx` 经 `AppLayout` 的
   * `sync` 属性挂上；若未挂，这里拿到 `null`，写操作退化为原来的「失败即报错」。
   */
  const syncQueue = useSyncQueueOptional();

  /**
   * 统一包裹：置忙、抛错转 toast、成功后重载对应数据。
   *
   * 传了 `queue` 之后，**网络不可达**时改为把动作放进离线队列而不是丢掉：
   * 律师在法院走廊、看守所会见室断网时点「提交复核」，得到的不该是
   * 「操作失败」，而该是「已保存到本机，恢复后自动同步」。
   *
   * 判据是「网络不可达」而**不是**「任何失败」：服务端明确返回的 4xx/5xx
   * 属于业务错误，重放同样的请求只会得到同样的结果——入队毫无意义，
   * 还会在同步队列里堆一批永远失败的任务，把真问题淹掉。
   */
  const run = useCallback(
    async (
      key: string,
      fn: () => Promise<void>,
      okMsg: string,
      queue?: { label: string; section: string; payload: LawyerSyncPayload }
    ) => {
      setBusy(key);
      try {
        await fn();
        addToast({ type: "success", title: okMsg });
        await loadAll();
      } catch (e) {
        if (queue && syncQueue && isOfflineFailure(e)) {
          syncQueue.enqueue({
            label: queue.label,
            section: queue.section,
            payload: queue.payload,
          });
          addToast({
            type: "info",
            title: "已保存到本机",
            message: "当前网络不可用，恢复后会自动同步",
          });
        } else {
          addToast({ type: "error", title: "操作失败", message: errText(e) });
        }
      } finally {
        setBusy(null);
      }
    },
    [addToast, loadAll, syncQueue]
  );

  /* 生成分析**故意不入队**：它会新建一个后台生成任务，重放可能产生重复任务，
   * 而它本身是异步作业、用户重新点一次成本极低。
   * 同理，案件级归档与开庭材料包导出也不入队（幂等性未确认）。
   * 完整判断见 `lib/syncTransport.ts` 的说明。 */
  const generateAnalysis = () =>
    run(
      "generate",
      async () => {
        await authed(`/api/v1/analyses/case/${caseId}/generate`, { method: "POST" });
      },
      "已提交生成任务，稍后刷新查看"
    );

  const submitReview = () =>
    run(
      "submit",
      async () => {
        if (!analysis) throw new Error("尚未生成分析");
        // required_level 取自分析本身，不再硬编码 L3
        const r = review
          ? review
          : await authed<ReviewRow>("/api/v1/reviews/ensure", {
              method: "POST",
              query: {
                target_type: "CASE_ANALYSIS",
                target_id: analysis.id,
                case_id: caseId,
                required_level: analysis.required_level,
              },
            });
        await authed(`/api/v1/reviews/${r.id}/submit`, {
          method: "POST",
          body: { comment: "提交复核" },
        });
      },
      "已提交复核",
      // 离线时 `ensure` 同样失败、拿不到 review id，所以把「先建任务再提交」
      // 所需的信息一并带上，由 transport 在恢复网络后补做 ensure
      analysis
        ? {
            label: `提交复核：${detail?.case_no ?? `案件 #${caseId}`}`,
            section: SYNC_SECTION.reviews,
            payload: {
              op: "review.submit",
              reviewId: review?.id,
              ensure: {
                targetType: "CASE_ANALYSIS",
                targetId: analysis.id,
                caseId,
                requiredLevel: analysis.required_level,
              },
              comment: "提交复核",
            },
          }
        : undefined
    );

  const decideReview = (decision: "APPROVED" | "REVISION_REQUESTED") =>
    run(
      decision === "APPROVED" ? "approve" : "revision",
      async () => {
        if (!review) throw new Error("复核任务不存在");
        await authed(`/api/v1/reviews/${review.id}/decide`, {
          method: "POST",
          body: {
            decision,
            comment: decision === "APPROVED" ? "复核通过" : "退回修改",
          },
        });
      },
      decision === "APPROVED" ? "已出具复核结论" : "已退回修改",
      review
        ? {
            label: `出复核结论：${detail?.case_no ?? `案件 #${caseId}`}`,
            section: SYNC_SECTION.reviews,
            payload: {
              op: "review.decide",
              reviewId: review.id,
              decision,
              comment: decision === "APPROVED" ? "复核通过" : "退回修改",
            },
          }
        : undefined
    );

  const archiveReview = () =>
    run(
      "archive",
      async () => {
        if (!review) throw new Error("复核任务不存在");
        await authed(`/api/v1/reviews/${review.id}/archive`, { method: "POST" });
      },
      "已归档",
      review
        ? {
            label: `归档复核：${detail?.case_no ?? `案件 #${caseId}`}`,
            section: SYNC_SECTION.reviews,
            payload: { op: "review.archive", reviewId: review.id },
          }
        : undefined
    );

  const archiveCase = () =>
    run(
      "archiveCase",
      async () => {
        await authed(`/api/v1/archives/cases/${caseId}`, { method: "POST" });
      },
      "案件已归档"
    );

  const exportHearingPack = () =>
    run(
      "hearingPack",
      async () => {
        await authed(`/api/v1/archives/cases/${caseId}/hearing-pack`, { method: "POST" });
      },
      "开庭材料包已导出"
    );

  /* -------------------------------------------------------------- 派生值 */

  const provenance = analysisProvenance(analysis);
  const laws = analysis?.related_laws ?? [];
  const missingChecklist = useMemo(
    () => checklist.filter((c) => c.missing),
    [checklist]
  );
  const riskEvidence = useMemo(
    () => evidence.filter((e) => evidenceFlag(e) !== "ok"),
    [evidence]
  );

  const stageIndex = useMemo(
    () => STAGE_FLOW.findIndex((s) => s.id === detail?.status),
    [detail?.status]
  );

  const recordTimeline: TimelineItem[] = useMemo(
    () =>
      records.map((r) => ({
        id: String(r.id),
        title: REVIEW_ACTION_LABEL[r.action] ?? r.action,
        description: [
          r.from_status && r.to_status && r.from_status !== r.to_status
            ? `${REVIEW_STATUS_LABEL[r.from_status] ?? r.from_status} → ${
                REVIEW_STATUS_LABEL[r.to_status] ?? r.to_status
              }`
            : null,
          r.level ? LEVEL_LABEL[r.level] ?? r.level : null,
          r.comment || null,
        ]
          .filter(Boolean)
          .join(" · "),
        actor: r.actor_id ? `操作人 #${r.actor_id}` : undefined,
        status:
          r.action === "REJECT"
            ? "rejected"
            : r.action === "REQUEST_REVISION"
              ? "current"
              : "done",
      })),
    [records]
  );

  const eventTimeline: TimelineItem[] = useMemo(
    () =>
      events.map((e) => ({
        id: String(e.id),
        title: e.title,
        description: e.description ?? undefined,
        time: fmtDateTime(e.occurred_at),
        actor: e.actor_user_id ? `#${e.actor_user_id}` : undefined,
        status: "done",
      })),
    [events]
  );

  /* ---------------------------------------------------------------- 渲染 */

  if (loading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-32 w-full" />
        <div className="grid gap-4 lg:grid-cols-[1fr_336px]">
          <Skeleton className="h-96 w-full" />
          <div className="space-y-4">
            <Skeleton className="h-40 w-full" />
            <Skeleton className="h-52 w-full" />
          </div>
        </div>
      </div>
    );
  }

  if (error || !detail) {
    return (
      <EmptyState
        icon={<AlertTriangle className="h-5 w-5" />}
        title="无法打开案件"
        description={error ?? "案件不存在，或你没有查看权限。"}
        action={
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => router.push("/cases")}
              className="rounded-r2 border border-line px-3 py-1.5 text-body-sm text-ink-700"
            >
              返回列表
            </button>
            <button
              type="button"
              onClick={() => void loadAll()}
              className="rounded-r2 bg-solid-brand px-3 py-1.5 text-body-sm font-medium text-white"
            >
              重试
            </button>
          </div>
        }
      />
    );
  }

  const isMine = detail.lawyer_id != null && detail.lawyer_id === user?.id;

  const tabs: { key: TabKey; label: string; count?: number }[] = [
    { key: "analysis", label: "六段式分析" },
    { key: "evidence", label: "证据材料", count: evidence.length },
    { key: "review", label: "复核记录", count: records.length },
    { key: "events", label: "案件动态", count: events.length },
    { key: "archive", label: "归档卷宗" },
  ];

  /* ── 移动端底部操作条的内容（规范 08 节）────────────────────────────
   * 只放**当前页签**真正可用的主操作。把五个按钮一股脑搬到底部，
   * 它就退化成第二个工具栏，拇指区里反而没有「最重要的那一个」。
   *
   * 桌面端不做这件事——同一批动作已经在右上角按钮组里，
   * 底部再压一条会形成两个「主操作区」，层级反而模糊。 */
  const mobileBar = (() => {
    if (tab === "review" && review) {
      if (canDecide(review.status)) {
        return {
          hint: `待你出结论 · 要求 ${LEVEL_LABEL[review.required_level] ?? review.required_level}`,
          actions: (
            <>
              <Button
                size="sm"
                variant="outline"
                disabled={busy !== null}
                onClick={() => decideReview("REVISION_REQUESTED")}
              >
                退回修改
              </Button>
              <Button
                size="sm"
                variant="verify"
                isLoading={busy === "approve"}
                onClick={() => decideReview("APPROVED")}
              >
                复核通过
              </Button>
            </>
          ),
        };
      }
      if (canArchive(review.status)) {
        return {
          hint: "结论已出，可归档",
          actions: (
            <Button size="sm" variant="primary" isLoading={busy === "archive"} onClick={archiveReview}>
              归档复核
            </Button>
          ),
        };
      }
      return null;
    }

    if (tab === "analysis") {
      if (!analysis) {
        return {
          hint: "尚未生成 AI 分析",
          actions: (
            <Button
              size="sm"
              variant="secondary"
              leftIcon={<Sparkles className="h-3.5 w-3.5" />}
              isLoading={busy === "generate"}
              onClick={generateAnalysis}
            >
              生成 AI 分析
            </Button>
          ),
        };
      }
      if (review && canSubmit(review.status)) {
        return {
          hint: "分析已生成，待提交复核",
          actions: (
            <Button size="sm" variant="verify" isLoading={busy === "submit"} onClick={submitReview}>
              提交复核
            </Button>
          ),
        };
      }
      return null;
    }

    if (tab === "archive") {
      return {
        hint: archive ? `卷宗号 ${archive.archive_no}` : "尚未归档",
        actions: archive ? (
          <Button
            size="sm"
            variant="outline"
            leftIcon={<Download className="h-3.5 w-3.5" />}
            isLoading={busy === "hearingPack"}
            onClick={exportHearingPack}
          >
            导出开庭材料包
          </Button>
        ) : (
          <Button
            size="sm"
            variant="primary"
            isLoading={busy === "archiveCase"}
            onClick={archiveCase}
          >
            归档案件
          </Button>
        ),
      };
    }

    // 证据材料 / 案件动态两个页签没有「一个决定性动作」，就不占这块位置
    return null;
  })();

  return (
    <div className="space-y-4">
      {/* ── 返回 + 操作 ─────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <button
          type="button"
          onClick={() => router.push("/cases")}
          className="flex items-center gap-1 text-body-sm text-ink-500 transition-colors duration-fast hover:text-ink-900"
        >
          <ChevronLeft className="h-4 w-4" />
          我的案件
        </button>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            leftIcon={loading ? <Spinner className="h-3.5 w-3.5" label={null} /> : <RefreshCw className="h-3.5 w-3.5" />}
            onClick={() => void loadAll()}
            disabled={loading}
          >
            刷新
          </Button>
          {/*
           * 概念图 05 顶栏还有「转他人」与「导出卷宗」。
           * 后端没有案件转派端点，也没有卷宗导出端点——宁可不放，
           * 也不放一个点了报 404 的按钮。开庭材料包是真实存在的导出能力。
           */}
          <Button
            size="sm"
            variant="outline"
            leftIcon={<Download className="h-3.5 w-3.5" />}
            isLoading={busy === "hearingPack"}
            onClick={exportHearingPack}
          >
            导出开庭材料包
          </Button>
          {!analysis && (
            <Button
              size="sm"
              variant="secondary"
              leftIcon={<Sparkles className="h-3.5 w-3.5" />}
              isLoading={busy === "generate"}
              onClick={generateAnalysis}
            >
              生成 AI 分析
            </Button>
          )}
          {analysis && review && canSubmit(review.status) && (
            <Button
              size="sm"
              variant="verify"
              leftIcon={<ClipboardCheck className="h-3.5 w-3.5" />}
              isLoading={busy === "submit"}
              onClick={submitReview}
            >
              提交复核
            </Button>
          )}
        </div>
      </div>

      {/* ── 案件头 ─────────────────────────────────────────────── */}
      <section className="rounded-r3 border border-line bg-surface p-5">
        <div className="flex items-start gap-4">
          <div
            className={cn(
              "grid h-11 w-11 shrink-0 place-items-center rounded-r3 text-h3 font-semibold text-white",
              detail.grade === "S" && "bg-solid-danger",
              detail.grade === "A" && "bg-solid-pending",
              detail.grade === "B" && "bg-solid-brand",
              detail.grade === "C" && "bg-ink-400"
            )}
            title={`案件等级 ${detail.grade}`}
          >
            {detail.grade}
          </div>

          <div className="min-w-0 flex-1">
            <h1 className="text-h2 text-ink-900">{detail.title}</h1>

            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-body-sm text-ink-500">
              <span className="num text-ink-600">{detail.case_no}</span>
              <Sep />
              <span className="num">标的额 {fmtAmount(detail.claim_amount)}</span>
              <Sep />
              <span>承办：{isMine ? "我" : detail.lawyer_id ? `律师 #${detail.lawyer_id}` : "未指派"}</span>
              {detail.party_a && (
                <>
                  <Sep />
                  <span>委托方：{detail.party_a}</span>
                </>
              )}
              {detail.party_b && (
                <>
                  <Sep />
                  <span>相对方：{detail.party_b}</span>
                </>
              )}
              {detail.dispute_type && (
                <>
                  <Sep />
                  <span>{detail.dispute_type}</span>
                </>
              )}
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Badge variant={CASE_STATUS_TONE[detail.status] ?? "neutral"}>
                {CASE_STATUS_LABEL[detail.status] ?? detail.status}
              </Badge>
              {analysis && (
                <ProvenanceBadge
                  state={provenance}
                  by={analysis.confirmed_by ? `#${analysis.confirmed_by}` : undefined}
                  at={analysis.confirmed_at ? fmtDateTime(analysis.confirmed_at) : undefined}
                />
              )}
              {review && (
                <Badge variant={review.is_forced ? "danger" : "outline"}>
                  复核 {REVIEW_STATUS_LABEL[review.status] ?? review.status} ·{" "}
                  {review.required_level}
                  {review.is_forced ? " · 强制" : ""}
                </Badge>
              )}
              {missingChecklist.length > 0 && (
                <Badge variant="pending">
                  <AlertTriangle className="mr-1 inline h-3 w-3" />
                  {missingChecklist.length} 项材料缺口
                </Badge>
              )}
              {riskEvidence.length > 0 && (
                <Badge variant="danger">
                  <ShieldAlert className="mr-1 inline h-3 w-3" />
                  {riskEvidence.length} 份材料待核验
                </Badge>
              )}
              {detail.require_formal_opinion && <Badge variant="info">需正式法律意见书</Badge>}
            </div>
          </div>
        </div>

        {/* 阶段条：只反映 CaseStatus 真实取值，不编造时间 */}
        <StageBar status={detail.status} index={stageIndex} />
      </section>

      {/* ── 主体两栏 ───────────────────────────────────────────── */}
      <div className="grid gap-4 lg:grid-cols-[1fr_336px]">
        {/* 左：主内容 */}
        <div className="min-w-0">
          <div className="mb-3 flex gap-1 overflow-x-auto border-b border-line">
            {tabs.map((t) => (
              <button
                key={t.key}
                type="button"
                onClick={() => setTab(t.key)}
                className={cn(
                  "relative whitespace-nowrap px-3 pb-2.5 pt-1 text-body-sm font-medium",
                  "transition-colors duration-fast",
                  tab === t.key ? "text-brand-700" : "text-ink-500 hover:text-ink-900"
                )}
              >
                {t.label}
                {t.count !== undefined && (
                  <span className="num ml-1 text-caption text-ink-400">{t.count}</span>
                )}
                {tab === t.key && (
                  <span className="absolute inset-x-3 -bottom-px h-0.5 rounded-t bg-brand-600" />
                )}
              </button>
            ))}
          </div>

          {tab === "analysis" && (
            <AnalysisTab
              analysis={analysis}
              provenance={provenance}
              laws={laws}
              activeCite={activeCite}
              onCite={setActiveCite}
              busy={busy === "generate"}
              onGenerate={generateAnalysis}
            />
          )}
          {tab === "evidence" && (
            <EvidenceTab
              caseId={caseId}
              evidence={evidence}
              checklist={checklist}
              onRefresh={() => void loadAll()}
            />
          )}
          {tab === "review" && (
            <ReviewTab
              analysis={analysis}
              review={review}
              timeline={recordTimeline}
              busy={busy}
              onSubmit={submitReview}
              onDecide={decideReview}
              onArchive={archiveReview}
            />
          )}
          {tab === "events" && <EventsTab timeline={eventTimeline} />}
          {tab === "archive" && (
            <ArchiveTab
              archive={archive}
              busy={busy}
              onArchive={archiveCase}
              onExport={exportHearingPack}
            />
          )}
        </div>

        {/* 右：侧栏 */}
        <aside className="space-y-3">
          <RailCard title="责任边界" icon={<Sparkles className="h-3.5 w-3.5" />}>
            <div className="px-4 py-3">
              <ProvenanceLegend />
              <p className="mt-3 border-t border-line pt-3 text-caption leading-relaxed text-ink-500">
                当前分析为
                <span className="mx-1 font-medium text-ink-700">
                  {provenance === "ai"
                    ? "AI 生成、尚未经律师确认"
                    : provenance === "verified"
                      ? "律师已确认"
                      : "待复核"}
                </span>
                。
                {/* 如实说明粒度，避免律师误以为每段已分别确认 */}
                后端按「整份分析」记录确认状态，不支持逐段确认，故本页不对单段标注确认人。
              </p>
            </div>
          </RailCard>

          <RailCard title="案件信息" icon={<FileText className="h-3.5 w-3.5" />}>
            <KV k="案件等级" v={`${detail.grade} 级`} />
            <KV k="当前阶段" v={CASE_STATUS_LABEL[detail.status] ?? detail.status} />
            <KV k="案由" v={detail.dispute_type || "—"} />
            <KV k="争议焦点" v={detail.focus || "—"} />
            <KV k="标的额" v={fmtAmount(detail.claim_amount)} mono />
            <KV k="紧急度" v={detail.urgency != null ? `${detail.urgency} / 5` : "—"} />
            <KV k="复杂度" v={detail.complexity != null ? `${detail.complexity} / 5` : "—"} />
            <KV k="分析版本" v={analysis ? `v${analysis.version}` : "—"} mono />
          </RailCard>

          {review && (
            <RailCard title="复核流转" icon={<ClipboardCheck className="h-3.5 w-3.5" />}>
              <div className="px-4 py-3">
                <div className="mb-3 space-y-1 text-body-sm">
                  <div className="flex items-center justify-between">
                    <span className="text-ink-500">要求级别</span>
                    <span className="text-ink-800">{review.required_level}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-ink-500">已满足级别</span>
                    <span className="text-ink-800">{review.satisfied_level ?? "—"}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-ink-500">当前状态</span>
                    <span className="text-ink-800">
                      {REVIEW_STATUS_LABEL[review.status] ?? review.status}
                    </span>
                  </div>
                </div>
                <Timeline items={recordTimeline} />
              </div>
            </RailCard>
          )}

          <RailCard
            title="证据材料"
            icon={<FolderOpen className="h-3.5 w-3.5" />}
            action={
              evidence.length > 0 ? (
                <button
                  type="button"
                  onClick={() => setTab("evidence")}
                  className="text-caption text-brand-600 hover:text-brand-700"
                >
                  全部 {evidence.length} 份 →
                </button>
              ) : null
            }
          >
            {evidence.length === 0 ? (
              <p className="px-4 py-6 text-center text-body-sm text-ink-500">尚未上传材料</p>
            ) : (
              evidence.slice(0, 6).map((e) => <EvidenceRow key={e.id} item={e} compact />)
            )}
          </RailCard>
        </aside>
      </div>

      {/* 移动端常驻底部操作条：桌面端同一批动作在右上角按钮组里 */}
      {mobileBar && (
        <MobileActionBar hint={mobileBar.hint}>{mobileBar.actions}</MobileActionBar>
      )}
    </div>
  );
}

/* ============================================================================
 * 复核状态机镜像（与 `app/workflows/review_fsm.py` 的 ALLOWED_TRANSITIONS 对齐）
 * ----------------------------------------------------------------------------
 * 在前端复刻白名单，是为了**在点击前**就禁用非法动作，而不是让律师点下去
 * 再收到一个 `REVIEW_TRANSITION_DENIED`。后端仍是唯一权威，这里只是 UI 约束。
 * ========================================================================== */

function canSubmit(status: string): boolean {
  return status === "draft" || status === "lawyer_editing";
}
function canDecide(status: string): boolean {
  return status === "pending_confirm";
}
function canArchive(status: string): boolean {
  return status === "confirmed";
}

/* ============================================================================
 * 子组件
 * ========================================================================== */

const Sep = () => <span className="text-ink-300">·</span>;

function StageBar({ status, index }: { status: string; index: number }) {
  const terminal = status === "CLOSED" || status === "VOIDED";
  const voided = status === "VOIDED";
  // CLOSED 表示走完全流程后结案，故 7 段全部点亮
  const effective = status === "CLOSED" ? STAGE_FLOW.length - 1 : index;

  return (
    <div className="mt-5">
      <ol className="flex">
        {STAGE_FLOW.map((s, i) => {
          const done = !terminal && effective > i;
          const current = !terminal && effective === i;
          return (
            <li key={s.id} className="relative flex-1 pt-4">
              <span
                aria-hidden
                className={cn(
                  "absolute left-0 right-0 top-[5px] h-0.5",
                  i === 0 && "left-1/2",
                  i === STAGE_FLOW.length - 1 && "right-1/2",
                  done ? "bg-verified-500" : "bg-line"
                )}
              />
              <span
                aria-hidden
                className={cn(
                  "absolute left-1/2 top-0 z-10 h-3 w-3 -translate-x-1/2 rounded-full border-2",
                  done && "border-verified-500 bg-verified-500",
                  current && "border-pending-500 bg-surface ring-4 ring-pending-500/20",
                  !done && !current && "border-ink-300 bg-surface"
                )}
              />
              <div
                className={cn(
                  "text-center text-caption",
                  done || current ? "font-medium text-ink-700" : "text-ink-400"
                )}
              >
                {s.label}
              </div>
            </li>
          );
        })}
      </ol>

      {terminal && (
        <p
          className={cn(
            "mt-3 rounded-r2 border px-3 py-2 text-body-sm",
            voided
              ? "border-danger-500/30 bg-danger-500/10 text-danger-600"
              : "border-line bg-surface-subtle text-ink-600"
          )}
        >
          {voided
            ? "该案件已作废，不再进入正常流转。"
            : "该案件已结案，流程已结束。"}
        </p>
      )}
    </div>
  );
}

function RailCard({
  title,
  icon,
  action,
  children,
}: {
  title: string;
  icon?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-r3 border border-line bg-surface">
      <header className="flex items-center gap-2 border-b border-line px-4 py-2.5">
        {icon && <span className="text-ink-500">{icon}</span>}
        <h2 className="text-body-sm font-semibold text-ink-800">{title}</h2>
        {action && <span className="ml-auto">{action}</span>}
      </header>
      {children}
    </section>
  );
}

function KV({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex border-b border-line px-4 py-2 text-body-sm last:border-b-0">
      <span className="w-[76px] shrink-0 text-ink-500">{k}</span>
      <span className={cn("min-w-0 flex-1 break-words text-ink-800", mono && "num")}>{v}</span>
    </div>
  );
}

/* ------------------------------------------------------------ 六段式分析 */

function AnalysisTab({
  analysis,
  provenance,
  laws,
  activeCite,
  onCite,
  busy,
  onGenerate,
}: {
  analysis: CaseAnalysis | null;
  provenance: ProvenanceState;
  laws: RelatedLaw[];
  activeCite: number | null;
  onCite: (n: number | null) => void;
  busy: boolean;
  onGenerate: () => void;
}) {
  if (!analysis) {
    return (
      <EmptyState
        icon={<Sparkles className="h-5 w-5" />}
        title="尚未生成六段式分析"
        description="AI 会基于案件基本信息与已上传材料，生成摘要、法律分析、法条、类案、建议与待补清单六段内容。"
        action={
          <Button variant="primary" isLoading={busy} onClick={onGenerate}>
            生成 AI 分析
          </Button>
        }
      />
    );
  }

  return (
    <div className="space-y-3">
      {/* 责任边界横幅：分析级，不是分段级 */}
      <div className="flex items-start gap-2.5 rounded-r3 border border-dashed border-ai-500/30 bg-ai-500/10 px-3.5 py-2.5">
        <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-ai-600" aria-hidden />
        <p className="text-body-sm text-ai-600">
          以下六段内容由
          <b className="mx-1">AI 生成</b>
          （版本 v{analysis.version}），律师可逐段核对后提交复核。
          {analysis.iteration_notes && analysis.iteration_notes.length > 0 && (
            <span className="ml-1">
              已按律师批注迭代 {analysis.iteration_notes.length} 次。
            </span>
          )}
          {analysis.forced_hits && analysis.forced_hits.length > 0 && (
            <span className="ml-1 font-medium">
              命中 {analysis.forced_hits.length} 项强制复核场景，要求 {analysis.required_level} 复核。
            </span>
          )}
        </p>
      </div>

      <CollapseGroup
        accordion={false}
        className="overflow-hidden rounded-r3 border border-line bg-surface"
      >
        {SECTIONS.map((s) => (
          <CollapsePanel
            key={s.key}
            id={s.key}
            defaultOpen
            icon={s.icon}
            title={
              <span className="flex items-center gap-2.5">
                <span className="num grid h-5 w-5 shrink-0 place-items-center rounded-r1 border border-brand-100 bg-brand-50 text-caption font-semibold text-brand-700">
                  {s.n}
                </span>
                <span>{s.title}</span>
              </span>
            }
            subtitle={s.hint}
            badge={<SectionBadge k={s.key} analysis={analysis} laws={laws} />}
          >
            <SectionBody
              k={s.key}
              analysis={analysis}
              laws={laws}
              activeCite={activeCite}
              onCite={onCite}
            />
          </CollapsePanel>
        ))}
      </CollapseGroup>

      {provenance === "ai" && (
        <p className="text-caption leading-relaxed text-ink-500">
          提示：本页不对单段标注「律师已确认」——后端按整份分析记录确认状态，
          逐段标注会制造「每段都已核对」的假象。
        </p>
      )}
    </div>
  );
}

/** 段头右侧的事实性徽标（条数 / 缺口），不是确认态。 */
function SectionBadge({
  k,
  analysis,
  laws,
}: {
  k: SectionKey;
  analysis: CaseAnalysis;
  laws: RelatedLaw[];
}) {
  if (k === "related_laws") {
    return laws.length > 0 ? <Badge variant="outline">{laws.length} 条</Badge> : null;
  }
  if (k === "similar_cases") {
    const n = analysis.similar_cases?.length ?? 0;
    return n > 0 ? <Badge variant="outline">{n} 例</Badge> : null;
  }
  if (k === "suggestions") {
    const n = analysis.suggestions?.length ?? 0;
    return n > 0 ? <Badge variant="outline">{n} 条路径</Badge> : null;
  }
  if (k === "missing_info") {
    const n = analysis.missing_info?.length ?? 0;
    return n > 0 ? <Badge variant="pending">{n} 项待补</Badge> : null;
  }
  return null;
}

function SectionBody({
  k,
  analysis,
  laws,
  activeCite,
  onCite,
}: {
  k: SectionKey;
  analysis: CaseAnalysis;
  laws: RelatedLaw[];
  activeCite: number | null;
  onCite: (n: number | null) => void;
}) {
  switch (k) {
    case "summary":
    case "legal_analysis": {
      const raw = (k === "summary" ? analysis.summary : analysis.legal_analysis) ?? "";
      if (!raw.trim()) return <Placeholder />;
      return (
        <div className="space-y-2.5 text-body leading-[1.85] text-ink-700">
          {raw.split("\n").filter(Boolean).map((line, i) => (
            <p key={i}>
              {k === "legal_analysis"
                ? renderLawRefs(line, laws, activeCite, onCite)
                : line}
            </p>
          ))}
        </div>
      );
    }

    case "related_laws": {
      if (laws.length === 0) return <Placeholder />;
      return (
        <ol className="space-y-3">
          {laws.map((l, i) => (
            <li key={l.citation_id ?? i}>
              <div className="flex items-start gap-2">
                <CitationChip
                  index={i + 1}
                  active={activeCite === i + 1}
                  onClick={() => onCite(activeCite === i + 1 ? null : i + 1)}
                  title={`${l.law_name ?? ""}${l.article_no ?? ""}`}
                />
                <div className="min-w-0 flex-1">
                  <p className="text-body-sm font-medium text-ink-800">
                    《{l.law_name ?? "—"}
                    {l.article_no ?? ""}》
                  </p>
                  {l.excerpt && (
                    <blockquote className="mt-1.5 border-l-[3px] border-gold-400 bg-surface-subtle px-3.5 py-2.5 font-serif text-body-sm leading-[1.95] text-ink-700">
                      {l.excerpt}
                    </blockquote>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ol>
      );
    }

    case "similar_cases": {
      const list = analysis.similar_cases ?? [];
      if (list.length === 0) return <Placeholder />;
      return (
        <ul className="space-y-2.5">
          {list.map((c, i) => (
            <li key={c.citation_id ?? i} className="rounded-r2 border border-line bg-surface-subtle p-3">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="num text-caption text-ink-500">{c.case_no ?? "—"}</span>
                {c.court && <span className="text-caption text-ink-400">{c.court}</span>}
              </div>
              <p className="mt-0.5 text-body-sm font-medium text-ink-800">{c.title ?? "—"}</p>
              {c.holding && (
                <p className="mt-1 text-body-sm text-ink-600">{c.holding}</p>
              )}
            </li>
          ))}
        </ul>
      );
    }

    case "suggestions": {
      const list = analysis.suggestions ?? [];
      if (list.length === 0) return <Placeholder />;
      return (
        <ol className="space-y-2.5">
          {list.map((s, i) => (
            <li key={i} className="rounded-r2 border border-line p-3">
              <p className="text-body-sm font-medium text-ink-900">
                {i + 1}. {s.path ?? "—"}
              </p>
              <dl className="mt-2 grid gap-1.5 sm:grid-cols-2">
                <div className="rounded-r1 bg-verified-500/10 px-2.5 py-1.5">
                  <dt className="text-caption font-medium text-verified-600">有利</dt>
                  <dd className="text-body-sm text-ink-700">{s.pros || "—"}</dd>
                </div>
                <div className="rounded-r1 bg-pending-500/10 px-2.5 py-1.5">
                  <dt className="text-caption font-medium text-pending-600">顾虑</dt>
                  <dd className="text-body-sm text-ink-700">{s.cons || "—"}</dd>
                </div>
              </dl>
            </li>
          ))}
        </ol>
      );
    }

    case "missing_info": {
      const list = analysis.missing_info ?? [];
      if (list.length === 0) return <Placeholder />;
      return (
        <ul className="space-y-2">
          {list.map((m, i) => (
            <li key={i} className="flex items-start gap-2.5">
              <Badge variant={(m.priority ?? 3) <= 1 ? "danger" : (m.priority ?? 3) === 2 ? "pending" : "neutral"}>
                {PRIORITY_LABEL[m.priority ?? 3] ?? "补充"}
              </Badge>
              <div className="min-w-0 flex-1">
                <p className="text-body-sm font-medium text-ink-800">{m.item ?? "—"}</p>
                {m.reason && <p className="text-caption text-ink-500">{m.reason}</p>}
              </div>
            </li>
          ))}
        </ul>
      );
    }
  }
}

function Placeholder() {
  return <p className="text-body-sm text-ink-400">该段暂无内容。</p>;
}

/**
 * 把正文里的《法名+条号》替换为「原文 + 引用序号」。
 *
 * 序号与「相关法条」段一一对应，点击后高亮右侧面板/本段条目。
 * 匹配失败（法条未被检索命中）时只保留原文，不生成指向错误条目的序号。
 */
function renderLawRefs(
  text: string,
  laws: RelatedLaw[],
  activeCite: number | null,
  onCite: (n: number | null) => void
): ReactNode {
  const re = /《([^》]+)》/g;
  const out: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  let key = 0;

  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const inner = m[1];
    const norm = inner.replace(/\s/g, "");
    const idx = laws.findIndex(
      (l) => `${l.law_name ?? ""}${l.article_no ?? ""}`.replace(/\s/g, "") === norm
    );
    out.push(<span key={`q${key++}`}>《{inner}》</span>);
    if (idx >= 0) {
      out.push(
        <CitationChip
          key={`c${key++}`}
          index={idx + 1}
          active={activeCite === idx + 1}
          onClick={() => onCite(activeCite === idx + 1 ? null : idx + 1)}
          title={inner}
        />
      );
    }
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/* ------------------------------------------------------------ 证据材料 */

function EvidenceRow({ item, compact }: { item: EvidenceItem; compact?: boolean }) {
  const flag = evidenceFlag(item);
  const risks = (item.legality_risks ?? []).length;

  return (
    <div
      className={cn(
        "flex items-center gap-2.5 border-b border-line last:border-b-0",
        compact ? "px-4 py-2.5" : "px-4 py-3"
      )}
    >
      <span className="grid h-7 w-7 shrink-0 place-items-center rounded-r2 bg-ink-100">
        <FileText className="h-3.5 w-3.5 text-ink-600" aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-body-sm font-medium text-ink-800">{item.name}</p>
        <p className="mt-0.5 truncate text-caption text-ink-500">
          {[
            EVIDENCE_CATEGORY_LABEL[item.category] ?? item.category,
            EVIDENCE_STATUS_LABEL[item.status] ?? item.status,
            item.file_type,
            fmtSize(item.file_size),
            risks > 0 ? `合法性风险 ${risks} 项` : null,
            item.event_date,
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </div>
      <span
        className={cn("h-2 w-2 shrink-0 rounded-full", FLAG_CLS[flag])}
        title={FLAG_LABEL[flag]}
        aria-label={FLAG_LABEL[flag]}
      />
    </div>
  );
}

function EvidenceTab({
  caseId,
  evidence,
  checklist,
  onRefresh,
}: {
  caseId: number;
  evidence: EvidenceItem[];
  checklist: ChecklistItem[];
  onRefresh: () => void;
}) {
  const { addToast } = useToast();
  const [captureOpen, setCaptureOpen] = useState(false);
  const [captured, setCaptured] = useState<CapturedFile[]>([]);
  const [uploading, setUploading] = useState(false);

  const missing = checklist.filter((c) => c.missing);
  const present = checklist.filter((c) => !c.missing);

  /**
   * 上传。**逐张发**，不是把多张塞进一个请求：
   * 后端 `POST /evidence/cases/{id}` 的 `file: UploadFile = File(...)` 是**单值**字段
   * （`backend/app/api/v1/evidence.py:85`）—— 同一个字段名 append 多次会让 FastAPI 校验失败。
   * 逐张还让「第 3 张挂了」不牵连前 2 张：失败的逐条报出来，**不静默丢**。
   */
  const doUpload = useCallback(async () => {
    if (captured.length === 0 || uploading) return;
    setUploading(true);
    let done = 0;
    const failed: string[] = [];
    for (const f of captured) {
      try {
        await upload<{ job_id?: number }>(`/api/v1/evidence/cases/${caseId}`, f.file);
        done += 1;
      } catch (e) {
        failed.push(`${f.file.name}：${errText(e)}`);
      }
    }
    setUploading(false);
    if (done > 0) {
      setCaptured([]);
      setCaptureOpen(false);
      addToast({
        type: "success",
        title: `已上传 ${done} 张材料`,
        message: "后端正在解析，稍后刷新即可看到归类结果",
      });
      onRefresh();
    }
    if (failed.length > 0) {
      addToast({
        type: "error",
        title: `${failed.length} 张上传失败`,
        message: failed.slice(0, 3).join("；") + (failed.length > 3 ? " …" : ""),
      });
    }
  }, [captured, uploading, caseId, addToast, onRefresh]);

  /* 采集区：**空态分支里也要有** —— 新案件恰恰最需要上传，
     若只在「已有材料」分支里放按钮，新案就永远传不上第一份材料。 */
  const capturePanel = (
    <section className="rounded-r3 border border-line bg-surface p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 text-body-sm font-semibold text-ink-800">
            <Camera className="h-4 w-4 shrink-0 text-ink-500" aria-hidden />
            拍照上传证据
          </h2>
          <p className="mt-0.5 text-caption text-ink-500">
            {captureOpen
              ? "上传后自动解析、归类，并与案件清单比对出缺口"
              : "现场拍卷宗：先在本地压缩，再逐张上传"}
          </p>
        </div>
        <Button
          variant="outline"
          className="shrink-0"
          disabled={uploading}
          onClick={() => setCaptureOpen((v) => !v)}
        >
          {captureOpen ? "收起" : "拍照 / 选图"}
        </Button>
      </div>

      {captureOpen && (
        <div className="mt-3">
          <CameraCapture
            value={captured}
            onChange={setCaptured}
            disabled={uploading}
            hint="支持拍照与相册多选；单张原图上限 20MB，超出会提示"
            onError={(m) => addToast({ type: "warning", title: m })}
          />
          <div className="mt-3 flex justify-end gap-2">
            <Button
              variant="outline"
              disabled={uploading || captured.length === 0}
              onClick={() => setCaptured([])}
            >
              清空
            </Button>
            <Button
              variant="primary"
              isLoading={uploading}
              disabled={uploading || captured.length === 0}
              onClick={() => void doUpload()}
            >
              上传 {captured.length} 张
            </Button>
          </div>
        </div>
      )}
    </section>
  );

  if (evidence.length === 0 && checklist.length === 0) {
    return (
      <div className="space-y-3">
        {capturePanel}
        <EmptyState
          icon={<FolderOpen className="h-5 w-5" />}
          title="尚未上传证据材料"
          description="材料上传后会自动解析、归类，并与案件清单比对出缺口。"
          action={
            <Button variant="outline" leftIcon={<RefreshCw className="h-3.5 w-3.5" />} onClick={onRefresh}>
              刷新
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {capturePanel}

      {missing.length > 0 && (
        <section className="rounded-r3 border border-pending-500/30 bg-pending-500/10 p-4">
          <h2 className="flex items-center gap-2 text-body-sm font-semibold text-pending-600">
            <AlertTriangle className="h-4 w-4" aria-hidden />
            材料缺口（{missing.length}）
          </h2>
          <ul className="mt-2.5 space-y-1.5">
            {missing.map((c, i) => (
              <li key={i} className="flex items-start gap-2 text-body-sm text-ink-700">
                <Badge
                  variant={c.priority <= 1 ? "danger" : c.priority === 2 ? "pending" : "neutral"}
                >
                  {PRIORITY_LABEL[c.priority] ?? "补充"}
                </Badge>
                <span className="min-w-0 flex-1">
                  {c.item}
                  {c.description && (
                    <span className="ml-1 text-caption text-ink-500">（{c.description}）</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="overflow-hidden rounded-r3 border border-line bg-surface">
        <header className="flex items-center gap-2 border-b border-line px-4 py-2.5">
          <h2 className="text-body-sm font-semibold text-ink-800">
            已上传材料（{evidence.length}）
          </h2>
          <span className="ml-auto text-caption text-ink-500">
            <span className="mr-2 inline-flex items-center gap-1">
              <i className={cn("h-2 w-2 rounded-full", FLAG_CLS.ok)} /> 已核验
            </span>
            <span className="mr-2 inline-flex items-center gap-1">
              <i className={cn("h-2 w-2 rounded-full", FLAG_CLS.warn)} /> 待核验
            </span>
            <span className="inline-flex items-center gap-1">
              <i className={cn("h-2 w-2 rounded-full", FLAG_CLS.risk)} /> 有问题
            </span>
          </span>
        </header>
        {evidence.length === 0 ? (
          <p className="px-4 py-6 text-center text-body-sm text-ink-500">尚无材料</p>
        ) : (
          evidence.map((e) => <EvidenceRow key={e.id} item={e} />)
        )}
      </section>

      {present.length > 0 && (
        <section className="overflow-hidden rounded-r3 border border-line bg-surface">
          <header className="border-b border-line px-4 py-2.5">
            <h2 className="text-body-sm font-semibold text-ink-800">
              清单已齐项（{present.length}）
            </h2>
          </header>
          <ul className="divide-y divide-line">
            {present.map((c, i) => (
              <li key={i} className="flex items-center gap-2 px-4 py-2 text-body-sm text-ink-600">
                <span className="h-1.5 w-1.5 rounded-full bg-verified-500" aria-hidden />
                {c.item}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

/* ------------------------------------------------------------ 复核记录 */

function ReviewTab({
  analysis,
  review,
  timeline,
  busy,
  onSubmit,
  onDecide,
  onArchive,
}: {
  analysis: CaseAnalysis | null;
  review: ReviewRow | null;
  timeline: TimelineItem[];
  busy: string | null;
  onSubmit: () => void;
  onDecide: (d: "APPROVED" | "REVISION_REQUESTED") => void;
  onArchive: () => void;
}) {
  if (!analysis) {
    return (
      <EmptyState
        icon={<ClipboardCheck className="h-5 w-5" />}
        title="尚无可复核的内容"
        description="需要先生成六段式分析，才能提交复核。"
      />
    );
  }

  if (!review) {
    return (
      <div className="space-y-3">
        <section className="rounded-r3 border border-line bg-surface p-4">
          <h2 className="text-body-sm font-semibold text-ink-800">尚未创建复核任务</h2>
          <p className="mt-1 text-body-sm text-ink-600">
            分析要求复核级别为 <b>{analysis.required_level}</b>
            {analysis.forced_hits && analysis.forced_hits.length > 0
              ? `（命中 ${analysis.forced_hits.length} 项强制复核场景）`
              : ""}
            。提交后，复核人会在此处留下流转记录。
          </p>
          <Button
            className="mt-3"
            variant="verify"
            isLoading={busy === "submit"}
            onClick={onSubmit}
          >
            创建并提交复核
          </Button>
        </section>
      </div>
    );
  }

  const canApprove = canDecide(review.status);
  const canRevise = canDecide(review.status);
  const canDoArchive = canArchive(review.status);
  const canDoSubmit = canSubmit(review.status);
  const terminal = review.status === "archived" || review.status === "voided";

  return (
    <div className="space-y-3">
      <section className="rounded-r3 border border-line bg-surface p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-body-sm font-semibold text-ink-800">
              复核状态：{REVIEW_STATUS_LABEL[review.status] ?? review.status}
            </h2>
            <p className="mt-1 text-caption text-ink-500">
              要求 {review.required_level} · 已满足 {review.satisfied_level ?? "—"}
              {review.is_forced ? " · 强制复核" : ""}
            </p>
          </div>
          <Badge variant={review.status === "confirmed" || review.status === "archived" ? "verified" : "pending"}>
            {review.status === "confirmed" || review.status === "archived" ? "已定稿" : "未定稿"}
          </Badge>
        </div>

        {/*
         * 按钮严格镜像后端状态机白名单，非法动作直接不渲染——
         * 律师不应该通过「点一下看报什么错」来学习状态机。
         */}
        <div className="mt-3 flex flex-wrap gap-2">
          {canDoSubmit && (
            <Button size="sm" variant="verify" isLoading={busy === "submit"} onClick={onSubmit}>
              提交复核
            </Button>
          )}
          {canApprove && (
            <Button
              size="sm"
              variant="primary"
              isLoading={busy === "approve"}
              onClick={() => onDecide("APPROVED")}
            >
              复核通过
            </Button>
          )}
          {canRevise && (
            <Button
              size="sm"
              variant="outline"
              isLoading={busy === "revision"}
              onClick={() => onDecide("REVISION_REQUESTED")}
            >
              退回修改
            </Button>
          )}
          {canDoArchive && (
            <Button
              size="sm"
              variant="secondary"
              leftIcon={<Archive className="h-3.5 w-3.5" />}
              isLoading={busy === "archive"}
              onClick={onArchive}
            >
              定稿归档
            </Button>
          )}
        </div>

        {terminal && (
          <p className="mt-3 border-t border-line pt-3 text-caption text-ink-500">
            该复核任务已进入终态（{REVIEW_STATUS_LABEL[review.status]}），不可再流转。
          </p>
        )}

        {/* 级别不足时如实说明为什么「复核通过」不会直接定稿 */}
        {review.status === "pending_confirm" &&
          review.satisfied_level &&
          review.satisfied_level !== review.required_level && (
            <p className="mt-3 rounded-r2 border border-pending-500/30 bg-pending-500/10 px-3 py-2 text-caption leading-relaxed text-pending-600">
              已满足 {review.satisfied_level}，但本案要求 {review.required_level}。
              继续通过需由具备 {review.required_level} 权限的复核人操作，否则任务会停留在「待复核确认」。
            </p>
          )}
      </section>

      <section className="overflow-hidden rounded-r3 border border-line bg-surface">
        <header className="border-b border-line px-4 py-2.5">
          <h2 className="text-body-sm font-semibold text-ink-800">
            流转留痕（{timeline.length}）
          </h2>
        </header>
        <div className="px-4 py-3">
          <Timeline items={timeline} />
        </div>
      </section>
    </div>
  );
}

/* ------------------------------------------------------------ 案件动态 */

function EventsTab({ timeline }: { timeline: TimelineItem[] }) {
  if (timeline.length === 0) {
    return (
      <EmptyState
        icon={<History className="h-5 w-5" />}
        title="暂无案件动态"
        description="案件状态变更、派单、材料上传等动作会自动记录在这里。"
      />
    );
  }
  return (
    <section className="overflow-hidden rounded-r3 border border-line bg-surface">
      <header className="border-b border-line px-4 py-2.5">
        <h2 className="text-body-sm font-semibold text-ink-800">
          案件动态（{timeline.length}）
        </h2>
      </header>
      <div className="px-4 py-3">
        <Timeline items={timeline} />
      </div>
    </section>
  );
}

/* ------------------------------------------------------------ 归档卷宗 */

function ArchiveTab({
  archive,
  busy,
  onArchive,
  onExport,
}: {
  archive: ArchiveInfo | null;
  busy: string | null;
  onArchive: () => void;
  onExport: () => void;
}) {
  return (
    <div className="space-y-3">
      <section className="rounded-r3 border border-line bg-surface p-4">
        <h2 className="text-body-sm font-semibold text-ink-800">案件卷宗</h2>
        {archive ? (
          <dl className="mt-2 space-y-1 text-body-sm text-ink-700">
            <div className="flex gap-2">
              <dt className="w-20 shrink-0 text-ink-500">卷宗号</dt>
              <dd className="num">{archive.archive_no}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="w-20 shrink-0 text-ink-500">版本</dt>
              <dd className="num">v{archive.current_version}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="w-20 shrink-0 text-ink-500">保存期限</dt>
              <dd className="num">{archive.retention_years} 年</dd>
            </div>
            <div className="flex gap-2">
              <dt className="w-20 shrink-0 text-ink-500">归档时间</dt>
              <dd className="num">{fmtDateTime(archive.archived_at)}</dd>
            </div>
          </dl>
        ) : (
          <p className="mt-2 text-body-sm text-ink-500">
            尚未归档。归档前必须先完成复核并定稿——「未确认不可定稿、未定稿不可归档」是后端硬约束。
          </p>
        )}
        <Button
          className="mt-3"
          size="sm"
          variant="secondary"
          isLoading={busy === "archiveCase"}
          disabled={Boolean(archive)}
          onClick={onArchive}
        >
          {archive ? "已归档" : "归档案件"}
        </Button>
      </section>

      <section className="rounded-r3 border border-line bg-surface p-4">
        <h2 className="text-body-sm font-semibold text-ink-800">开庭材料包</h2>
        <p className="mt-2 text-body-sm text-ink-500">
          {archive?.hearing_pack_path
            ? `已导出：${archive.hearing_pack_path}`
            : "一键导出卷宗目录、起诉状、证据清单、质证提纲与庭审要点。"}
        </p>
        <Button
          className="mt-3"
          size="sm"
          variant="outline"
          leftIcon={<Download className="h-3.5 w-3.5" />}
          isLoading={busy === "hearingPack"}
          onClick={onExport}
        >
          导出开庭材料包
        </Button>
      </section>
    </div>
  );
}
