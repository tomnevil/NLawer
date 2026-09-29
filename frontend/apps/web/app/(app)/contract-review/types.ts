/* ============================================================================
 * 合同审查（P0-16 / CR-13 + CR-04）前端契约
 * ----------------------------------------------------------------------------
 * 字段名与后端冻结契约（`POST /api/v1/documents/contract-review`）一一对应，
 * **不得改动**。本文件只放类型与语义映射，不含渲染逻辑。
 *
 * 为什么把语义映射集中在这里而不是写进 JSX：
 *   结果页有 4 个「渲染错就是产品事故」的判定——来源(source) / 审查状态(status) /
 *   分析完成度(analysis_status) / 依据类型(basis_type)。它们散落在 JSX 里时，
 *   后续任何一次改动都可能漏掉一个分支，而漏掉的分支方向恰好都是
 *   「把降级产出渲染成 AI 分析结果」。集中一处后，新增分支必须在这里显式落笔。
 * ========================================================================== */

import type { BadgeProps } from "@nlaw/ui";

export type ReviewSource = "llm" | "rule" | "mock";
export type ReviewStatus = "success" | "degraded" | "failed";
export type AnalysisStatus = "complete_no_risk" | "prescreen_only" | "risk_found";
export type BasisType = "statute" | "experience" | "manual";

export interface ReviewCitation {
  law_name?: string | null;
  article_no?: string | null;
  content?: string | null;
}

export interface ReviewFinding {
  clause_index?: number | null;
  clause_no?: string | null;
  /** 原文精确字符区间；后端保证 original === source_text[char_start:char_end] */
  char_start?: number | null;
  char_end?: number | null;
  original?: string | null;
  dimension?: string | null;
  risk_level?: string | null;
  /** 后果说明。非空才有决策价值（用户研究结论） */
  consequence?: string | null;
  /** 可直接采纳的改写文本。**只有它才能作为 redline 替换原文** */
  suggestion_text?: string | null;
  basis_type?: string | null;
  citation?: ReviewCitation | null;
  /* ---- 旧键，仅兼容用；不得作为 redline 采纳来源 ---- */
  clause?: string | null;
  issue?: string | null;
  suggestion?: string | null;
}

export interface ReviewCoverage {
  total_clauses?: number | null;
  reviewed_clauses?: number | null;
  /** 0–1 */
  reviewed_ratio?: number | null;
}

export interface ReviewUsage {
  /** 单位：分。缺省或 0 都表示本次未计费 */
  charged?: number | null;
  /**
   * 是否**超出套餐额度、已转人工工单**。
   *
   * 为 true 时钱**照样要付**（转成等额计费工单），`charged` 仍是全额。
   * 不区分这一支的后果是：用户看到「已计费 ¥99.00」会以为是从套餐额度里扣的，
   * 而实际是额外产生的工单费用——这是 PRD Q2 点名的「计价不对等」隐患，
   * 属于必须说清的商业口径，不是文案美化。
   */
  exceeded?: boolean | null;
  /** 转工单后的工单号（exceeded 为 true 时存在） */
  work_order_id?: number | null;
}

export interface ContractReviewResult {
  id?: number | null;
  title?: string | null;
  source?: string | null;
  status?: string | null;
  analysis_status?: string | null;
  overall_risk?: string | null;
  coverage?: ReviewCoverage | null;
  disclaimer?: string | null;
  summary?: string | null;
  /**
   * 失败原因。**仅在 `status=failed` 时出现**（与 `usage` 同一约定：
   * 键不存在表示「不适用」）。服务端给的是不含环境变量与内部地址的人话文案，
   * 因此这里原样展示、不做任何加工。
   */
  error_message?: string | null;
  findings?: ReviewFinding[] | null;
  /** 键不存在 ⇒ 本次未计费（产品的诚实性承诺） */
  usage?: ReviewUsage | null;
}

/* ------------------------------------------------------------------ 免责声明 */

/**
 * 契约要求后端返回 `disclaimer`。若后端漏传，**不能因此不显示免责声明**——
 * 「没带免责声明」不是「没有免责声明」。此处用 PRD 固定文案兜底。
 */
export const DEFAULT_DISCLAIMER = "本结果为 AI 辅助意见，不构成法律意见。";

/* --------------------------------------------------------------- 风险等级 */

export const RISK_LABEL: Record<string, string> = {
  HIGH: "高风险",
  MEDIUM: "中风险",
  LOW: "低风险",
  NONE: "未发现风险",
};

export const RISK_TONE: Record<string, BadgeProps["variant"]> = {
  HIGH: "danger",
  MEDIUM: "pending",
  LOW: "info",
  NONE: "verified",
};

/** 排序权重：HIGH → MEDIUM → LOW；未知等级排最后 */
const RISK_ORDER: Record<string, number> = { HIGH: 0, MEDIUM: 1, LOW: 2 };

export interface IndexedFinding {
  finding: ReviewFinding;
  /** 在原始 findings 数组中的下标——采纳状态、区间映射都以它为键 */
  index: number;
}

/** 按风险等级降序（HIGH 在前）稳定排序，同级保持后端原始顺序。 */
export function sortFindings(findings: ReviewFinding[]): IndexedFinding[] {
  return findings
    .map((finding, index) => ({ finding, index }))
    .sort((a, b) => {
      const ra = RISK_ORDER[a.finding.risk_level ?? ""] ?? 99;
      const rb = RISK_ORDER[b.finding.risk_level ?? ""] ?? 99;
      return ra - rb || a.index - b.index;
    });
}

/* ------------------------------------------------------------------- 来源 */

export interface BannerSpec {
  /** 与来源/状态对应的三态；用于 ProvenanceBadge */
  provenance: "ai" | "verified" | "pending";
  tone: "info" | "warning" | "error";
  title: string;
  detail: string;
}

/**
 * 来源横幅。**`source` 缺失时也必须出横幅**——缺字段不是「AI 产出」，
 * 而是「无法确认来源」，两者对用户的意义完全不同。
 */
export function sourceBanner(source: string | null | undefined): BannerSpec {
  switch (source) {
    case "llm":
      return {
        provenance: "ai",
        tone: "info",
        title: "来源：AI 模型分析",
        detail: "本次结果由 AI 模型逐条通读合同后产出。",
      };
    case "rule":
      return {
        provenance: "pending",
        tone: "warning",
        title: "本次结果由规则引擎产出，未使用模型",
        detail:
          "规则引擎只按关键词命中，未逐条通读合同；结论可能严重不完整，请勿据此判断合同是否安全。",
      };
    case "mock":
      return {
        provenance: "pending",
        tone: "warning",
        title: "本次为测试/演示结果，未使用真实模型",
        detail: "该结果由测试替身产出，不具备任何审查参考价值。",
      };
    default:
      return {
        provenance: "pending",
        tone: "warning",
        title: "本次未提供结果来源标注",
        detail: "无法确认该结果由模型还是规则引擎产出，请勿视为 AI 分析结果。",
      };
  }
}

/* --------------------------------------------------------------- 审查状态 */

/**
 * 审查状态横幅。
 *
 * **刻意不在这里断言计费结果**——「降级/失败不计费」是计费策略，其事实依据
 * 只能是接口返回的 `usage`。若横幅自行宣称「本次不产生费用」，而 `usage` 又
 * 显示已计费，页面就会自相矛盾；计费口径一律由 `billingSpec` 统一呈现。
 */
export function statusBanner(status: string | null | undefined): BannerSpec | null {
  switch (status) {
    case "success":
      return null;
    case "degraded":
      return {
        provenance: "pending",
        tone: "warning",
        title: "本次审查已降级",
        detail: "分析过程未能完整完成，结论可能不完整。建议重试；是否计费见本页「计费」一栏。",
      };
    case "failed":
      return {
        provenance: "pending",
        tone: "error",
        title: "本次审查失败",
        detail: "未产出任何审查结论。可重试；是否计费见本页「计费」一栏。",
      };
    default:
      return {
        provenance: "pending",
        tone: "warning",
        title: "本次未提供审查状态标注",
        detail: "无法确认本次分析是否完整完成，请谨慎采信。",
      };
  }
}

/* ---------------------------------------------------------- 分析完成度 */

export interface AnalysisSpec {
  /** 是否可以正当地给出「未发现风险」这类正面结论 */
  positive: boolean;
  tone: "verified" | "warning" | "danger";
  text: string;
}

/**
 * 分析完成度。
 *
 * **`prescreen_only` 绝不可渲染成「合同安全」**（用户研究判定「零发现即放行」
 * 为阻断级缺陷）。未知取值同样按警示处理：不知道完成度时，唯一安全的表述
 * 是「不知道」。
 */
export function analysisSpec(
  analysisStatus: string | null | undefined,
  findingCount: number,
): AnalysisSpec {
  switch (analysisStatus) {
    case "complete_no_risk":
      return {
        positive: true,
        tone: "verified",
        text: "已完成逐条通读，未发现风险条款。",
      };
    case "risk_found":
      return {
        positive: false,
        tone: "danger",
        text: `已完成逐条通读，发现 ${findingCount} 条风险条款。`,
      };
    case "prescreen_only":
      return {
        positive: false,
        tone: "warning",
        text:
          "本次仅完成规则预筛，未命中规则库不等于合同安全，建议开启完整 AI 审查。",
      };
    default:
      return {
        positive: false,
        tone: "warning",
        text: "本次未提供分析完成度标注，请勿据此认定合同安全。",
      };
  }
}

/* --------------------------------------------------------------- 覆盖度 */

export interface CoverageSpec {
  tone: "neutral" | "warning";
  text: string;
}

/**
 * 覆盖度。`reviewed_ratio < 1` 必须显示（契约硬要求），且必须说清
 * 「结论只覆盖了已审阅部分」。契约缺覆盖度时同样要显示「未提供」，
 * 而不是静默按 100% 处理。
 */
export function coverageSpec(coverage: ReviewCoverage | null | undefined): CoverageSpec {
  const total = coverage?.total_clauses;
  const reviewed = coverage?.reviewed_clauses;
  const ratio = coverage?.reviewed_ratio;

  if (!coverage || (total == null && reviewed == null && ratio == null)) {
    return { tone: "warning", text: "本次未提供覆盖度信息，无法确认是否已审阅全部条款。" };
  }

  const percent =
    typeof ratio === "number" && Number.isFinite(ratio)
      ? `${Math.round(Math.max(0, Math.min(1, ratio)) * 100)}%`
      : null;

  const counts =
    typeof total === "number" && typeof reviewed === "number"
      ? `已审阅 ${reviewed}/${total} 条`
      : typeof reviewed === "number"
        ? `已审阅 ${reviewed} 条`
        : null;

  const head = [counts, percent ? `覆盖率 ${percent}` : null].filter(Boolean).join(" · ");
  const partial = typeof ratio === "number" ? ratio < 1 : true;

  if (!head) return { tone: "warning", text: "本次未提供覆盖度信息，无法确认是否已审阅全部条款。" };

  return partial
    ? { tone: "warning", text: `${head}——未覆盖全部条款，以下结论仅基于已审阅部分。` }
    : { tone: "neutral", text: head };
}

/* --------------------------------------------------------------- 计费 */

export interface BillingSpec {
  charged: boolean;
  /** 是否超出套餐额度、已转人工工单（钱照样要付，但性质完全不同） */
  escalated: boolean;
  text: string;
  /** 转工单时给出「这不是套餐内扣减」的解释；其余情况为 null */
  note: string | null;
}

/**
 * 计费口径。**`usage` 键不存在 / `charged` 为 0 ⇒ 本次未计费**（必须明示）。
 *
 * 注意区分两支「已计费」：
 *   - 套餐内扣减：`exceeded=false`
 *   - 额度耗尽转人工工单：`exceeded=true`，`charged` 仍是**全额**
 * 两者对用户的钱包含义不同，UI 必须分开表述——把后者渲染成前者，
 * 等于让用户以为这次是套餐内的免费额度在消化。
 */
export function billingSpec(usage: ReviewUsage | null | undefined): BillingSpec {
  const charged = typeof usage?.charged === "number" ? usage.charged : 0;
  if (charged <= 0) {
    return { charged: false, escalated: false, text: "本次未计费", note: null };
  }

  const amount = `¥${(charged / 100).toFixed(2)}`;
  if (usage?.exceeded) {
    // 工单号对需要跟进/申诉的用户有实际价值，有就带上
    const order =
      typeof usage.work_order_id === "number" ? `（工单号 #${usage.work_order_id}）` : "";
    return {
      charged: true,
      escalated: true,
      text: `本次已计费 ${amount}（已超出套餐额度）`,
      note: `套餐额度已用尽，本次已转为人工工单${order}处理，费用按 ${amount} 另行计收，不从套餐额度内扣减。`,
    };
  }

  return { charged: true, escalated: false, text: `本次已计费 ${amount}`, note: null };
}

/* --------------------------------------------------------------- 依据 */

export interface BasisSpec {
  /** 可核验的法条依据；`null` 表示「不得渲染法条区块」 */
  citation: ReviewCitation | null;
  label: string;
  tone: BadgeProps["variant"];
  /** 依据类型标注本身缺失或异常时的警示文案 */
  warning?: string;
}

/** 判定 citation 是否真的可以展示（三个字段全空视为无依据，不得渲染空区块）。 */
function usableCitation(citation: ReviewCitation | null | undefined): ReviewCitation | null {
  if (!citation) return null;
  const hasAny = Boolean(
    (citation.law_name ?? "").trim() ||
      (citation.article_no ?? "").trim() ||
      (citation.content ?? "").trim(),
  );
  return hasAny ? citation : null;
}

/**
 * 依据分层。**绝不可把 `experience` 渲染成有法条依据的样子**——
 * 用户研究结论：律师不排斥经验判断，排斥的是把经验伪装成法条。
 */
export function basisSpec(finding: ReviewFinding): BasisSpec {
  const citation = usableCitation(finding.citation);
  switch (finding.basis_type) {
    case "statute":
      // 标注为法条却拿不出可核验条文：如实说明，而不是留一个空法条区块
      if (!citation) {
        return {
          citation: null,
          label: "法条依据缺失",
          tone: "warning",
          warning: "本条标注为法条依据，但未返回可核验的条文内容，请自行核对。",
        };
      }
      return { citation, label: "法条依据", tone: "primary" };
    case "experience":
      return { citation: null, label: "经验判断", tone: "neutral" };
    case "manual":
      return { citation: null, label: "需人工确认", tone: "pending" };
    default:
      return {
        citation,
        label: "依据类型未标注",
        tone: "neutral",
        warning: "本条未提供依据类型标注，无法确认是否有法条支撑。",
      };
  }
}

/** 依据对应的说明文案。`statute` 有 citation 时不显示说明（直接显示条文）。 */
export function basisNote(spec: BasisSpec): string | null {
  if (spec.warning) return spec.warning;
  if (spec.citation) return null;
  if (spec.label === "经验判断") return "本条为经验判断，暂无明确法条依据。";
  if (spec.label === "需人工确认") return "本条需人工确认后才能定稿。";
  return null;
}

/* --------------------------------------------------------------- 建议文本 */

export interface SuggestionSpec {
  /** 可直接采纳的改写文本；为空表示不可采纳（不渲染采纳按钮） */
  adoptable: string | null;
  /** 旧键里的方向性短语，仅作参考展示，**不得作为 redline** */
  direction: string | null;
}

/**
 * 只有 `suggestion_text` 才可作为 redline。旧键 `suggestion` 是
 * 「明确违约金计算方式」这类方向性短语，把它替换进原文等于**破坏合同原文**，
 * 因此只展示、不采纳。
 */
export function suggestionSpec(finding: ReviewFinding): SuggestionSpec {
  const adoptable = (finding.suggestion_text ?? "").trim() || null;
  const direction = (finding.suggestion ?? "").trim() || null;
  return { adoptable, direction: adoptable ? null : direction };
}

/* ------------------------------------------------------------------ 杂项 */

/** 风险条目的标题：优先维度，其次旧键 issue。 */
export function findingTitle(finding: ReviewFinding): string {
  return (finding.dimension ?? "").trim() || (finding.issue ?? "").trim() || "未标注风险维度";
}

/** 条款编号：优先 clause_no，其次由 clause_index 推导。 */
export function findingClauseLabel(finding: ReviewFinding): string | null {
  const no = (finding.clause_no ?? "").trim();
  if (no) return no;
  if (typeof finding.clause_index === "number") return `第 ${finding.clause_index} 段`;
  return null;
}

/** 原文片段：优先逐字的 original，其次旧键 clause。 */
export function findingOriginal(finding: ReviewFinding): string {
  return (finding.original ?? finding.clause ?? "").trim();
}
