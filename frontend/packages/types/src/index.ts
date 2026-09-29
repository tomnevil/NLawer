/** 与后端 schemas 对齐的 DTO 类型（随模块推进持续扩充）。 */

export type Role =
  | "PLATFORM_ADMIN"
  | "FIRM_ADMIN"
  | "LAWYER"
  | "ASSISTANT"
  | "CLIENT"
  | "ENTERPRISE_ADMIN"
  | "ENTERPRISE_USER";

export type TenantType = "PLATFORM" | "LAW_FIRM" | "ENTERPRISE";

export interface UserBrief {
  id: number;
  username: string;
  full_name: string | null;
  role: Role;
  tenant_id: string;
  tenant_name?: string | null;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: UserBrief;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface ApiResponse<T> {
  success: boolean;
  data: T | null;
  error?: { code: string; message: string; details?: unknown } | null;
}

export interface PageMeta {
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface Paged<T> {
  items: T[];
  meta: PageMeta;
}

/** 统一响应 —— 接口即时返回 job_id，前端轮询进度。 */
export interface JobRef {
  job_id: number;
  status: string;
  progress: number;
  message?: string | null;
}

// ---- 产品线 A 枚举（与后端 models/enums 对齐）----
export type IntentType = "CONSULT" | "DOCUMENT" | "CALCULATION" | "REVIEW" | "ENTRUST";
export type CaseGrade = "S" | "A" | "B" | "C";
export type CaseStatus =
  | "INTAKE"
  | "PENDING_DISPATCH"
  | "DISPATCHED"
  | "ACCEPTED"
  | "IN_REVIEW"
  | "CONFIRMED"
  | "ARCHIVED"
  | "CLOSED"
  | "VOIDED";
export type DispatchMode = "DESIGNATED" | "AUTO" | "POOL";
export type DispatchStatus = "PENDING" | "ACCEPTED" | "REJECTED" | "EXPIRED";
export type ConversationStatus = "BOT" | "WAITING_HUMAN" | "HUMAN" | "CLOSED";
export type ReviewStatus =
  | "draft"
  | "lawyer_editing"
  | "pending_confirm"
  | "confirmed"
  | "archived"
  | "voided";
export type ReviewLevel = "L1" | "L2" | "L3";

// ---- 产品线 B 枚举 ----
export type ComplianceDimension =
  | "LABOR"
  | "COMMERCIAL"
  | "DATA_PRIVACY"
  | "ADVERTISING";
export type RiskLevel = "HIGH" | "MEDIUM" | "LOW" | "NONE";
export type UsageType = "QA" | "DOCUMENT" | "CONTRACT_REVIEW" | "COMPLIANCE_SCAN";
