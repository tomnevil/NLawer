import { ApiError } from "@nlaw/sdk";
import type { BadgeProps } from "@nlaw/ui";

/* ============================================================================
 * im 端的共享契约层
 * ----------------------------------------------------------------------------
 * 【为什么要有这个文件】
 * 4 项 Tab 拆成 4 个路由后，`/`、`/cases`、`/me`、`/chat` 都要读同一批后端契约
 * （案件状态文案、等级配色、分页形状、错误文案）。
 * 若各自维护一份，后端加一个状态时必然有一端渲染成空白 —— 这是本项目
 * 已经踩过的形状（见组件库 `notificationMeta` 的注释）。
 * ⇒ 契约与文案映射**只在这里定义一次**。
 *
 * 【只列真实存在的端点】本文件里的路径全部在 backend 源码中核对过，
 * 且角色收窄是读源码确认的，不是猜的：
 *   GET  /api/v1/cases                      → Role.CLIENT 收窄到 Case.client_user_id
 *   GET  /api/v1/cases/{id}
 *   GET  /api/v1/cases/{id}/events
 *   GET  /api/v1/evidence/cases/{id}
 *   GET  /api/v1/evidence/cases/{id}/missing
 *   GET  /api/v1/conversations              → Role.CLIENT 收窄到 Conversation.client_user_id
 *   GET  /api/v1/notifications/unread-count
 * ========================================================================== */

/* ------------------------------------------------------------------ 形状 */

/** `Page.build` 的形状：`total` 在**顶层**，且可能只是下界。 */
export interface Paged<T> {
  items: T[];
  total: number;
  total_is_lower_bound?: boolean;
}

export interface Conversation {
  id: number;
  tenant_id: string;
  external_user_id: string;
  channel: string;
  status: string;
  bind_lawyer_id?: number | null;
  case_id?: number | null;
  /** 会话引擎写入的轻量上下文：{ last_intent, dispute_type } */
  context?: { dispute_type?: string; last_intent?: string } | null;
  last_message_at?: string | null;
}

export interface CaseDetail {
  id: number;
  case_no: string;
  title: string;
  status: string;
  grade: string;
  dispute_type?: string | null;
  claim_amount?: number | null;
  lawyer_id?: number | null;
  summary?: string | null;
  focus?: string | null;
}

export interface CaseEvent {
  id: number;
  event_type: string;
  title: string;
  description?: string | null;
  occurred_at?: string | null;
  actor_user_id?: number | null;
}

export interface ChecklistItem {
  item: string;
  category: string;
  priority: number;
  description?: string | null;
  missing: boolean;
}

export interface EvidenceItem {
  id: number;
  name: string;
  file_type?: string | null;
  file_size?: number | null;
  status: string;
  category: string;
}

/* -------------------------------------------------------------- 文案映射 */

export const CONV_STATUS: Record<string, { label: string; tone: BadgeProps["variant"] }> = {
  BOT: { label: "AI 接待中", tone: "ai" },
  WAITING_HUMAN: { label: "已转人工 · 待接单", tone: "pending" },
  HUMAN: { label: "律师已接入", tone: "verified" },
  CLOSED: { label: "已结束", tone: "neutral" },
};

export const CASE_STATUS: Record<string, string> = {
  INTAKE: "已收案",
  PENDING_DISPATCH: "待派单",
  DISPATCHED: "已派单",
  ACCEPTED: "已接单",
  IN_REVIEW: "办案中",
  CONFIRMED: "已确认",
  ARCHIVED: "已归档",
  CLOSED: "已结案",
  VOIDED: "已作废",
};

/**
 * 案件状态 → 徽章语义色。
 *
 * ⚠️ 与 `CASE_STATUS` **分开维护**：状态文案是「给客户看的话」，
 * 语义色是「在页面上怎么被扫到」。两者变化节奏不同 ——
 * 改一个字不该顺手改掉配色，反之亦然。
 */
export const CASE_STATUS_TONE: Record<string, BadgeProps["variant"]> = {
  INTAKE: "info",
  PENDING_DISPATCH: "pending",
  DISPATCHED: "pending",
  ACCEPTED: "verified",
  IN_REVIEW: "verified",
  CONFIRMED: "verified",
  ARCHIVED: "neutral",
  CLOSED: "neutral",
  VOIDED: "neutral",
};

/**
 * 「办理中」的取值域。**只在这里定义一次** ——
 * 工作台的待办总览、我的案件的分组都依赖它，
 * 两处各写一遍必然漂移（一处加了 ACCEPTED，另一处没加）。
 */
export const ACTIVE_CASE_STATUSES: readonly string[] = [
  "DISPATCHED",
  "ACCEPTED",
  "IN_REVIEW",
];

export const GRADE_TONE: Record<string, BadgeProps["variant"]> = {
  S: "danger",
  A: "pending",
  B: "info",
  C: "neutral",
};

export const INTENT_LABEL: Record<string, string> = {
  CONSULT: "咨询",
  DOCUMENT: "文书",
  CALCULATION: "计算",
  REVIEW: "审查",
  ENTRUST: "委托",
};

/* ------------------------------------------------------------------ 工具 */

export function errText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "请求失败";
}

export function isCaseActive(status: string): boolean {
  return ACTIVE_CASE_STATUSES.includes(status);
}

export function caseStatusLabel(status: string): string {
  return CASE_STATUS[status] ?? status;
}

/**
 * 时间格式化。后端 `last_message_at` / `occurred_at` 是**无时区的本地 ISO 串**
 * （`datetime.now().isoformat()`），浏览器按本地时区解析，符合预期。
 */
export function fmtTime(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso.replace(" ", "T"));
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  if (d.toDateString() === now.toDateString()) return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}/${d.getDate()}`;
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}

export function fmtDateTime(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso.replace(" ", "T"));
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 金额千分位；后端 `claim_amount` 是 float。 */
export function fmtAmount(n?: number | null): string {
  if (n === null || n === undefined) return "—";
  return `¥ ${n.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`;
}

/** 会话标题：优先案件争议类型（引擎写入 context），否则退化为编号。 */
export function convTitle(c: Conversation): string {
  return c.context?.dispute_type?.trim() || `会话 #${c.id}`;
}

export function convSubtitle(c: Conversation): string {
  const intent = c.context?.last_intent;
  const parts: string[] = [];
  if (intent && INTENT_LABEL[intent]) parts.push(INTENT_LABEL[intent]);
  if (c.case_id) parts.push(`案件 #${c.case_id}`);
  parts.push(`渠道 ${c.channel}`);
  return parts.join(" · ");
}
