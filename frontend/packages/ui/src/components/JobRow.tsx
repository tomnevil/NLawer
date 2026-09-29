"use client";

import React from "react";
import { cn } from "../lib/cn";
import { StageProgress, jobStage } from "./StageProgress";

/**
 * 任务中心里的一行。**字段名与 `GET /api/v1/jobs` 的响应一一对应**（后端 `JobSchema`）。
 *
 * ⚠️ 刻意直接用后端字段名（snake_case）而不是前端另造一套 camelCase DTO：
 * 多一层映射就多一处「后端改了、前端没跟」的漂移风险，而且这层映射
 * 不产生任何业务价值。
 */
export interface TaskCenterJob {
  id: number;
  job_type: string;
  status: string;
  progress: number;
  step_name?: string | null;
  error_message?: string | null;
  retry_count?: number;
  created_at?: string | null;
}

export interface JobRowProps {
  job: TaskCenterJob;
  /** 仅在 `status === failed` 时由抽屉传入；其余状态不渲染重试钮。 */
  onRetry?: (id: number) => void;
  onOpen?: (job: TaskCenterJob) => void;
  className?: string;
}

const JOB_TYPE_LABELS: Record<string, string> = {
  case_analysis: "案件分析",
  evidence_parse: "证据解析",
  compliance_scan: "合规扫描",
  document_gen: "文书生成",
  contract_review: "合同审查",
};

export function jobTypeLabel(jobType: string): string {
  return JOB_TYPE_LABELS[jobType] ?? jobType;
}

export const JobRow: React.FC<JobRowProps> = ({ job, onRetry, onOpen, className }) => {
  const stage = jobStage(job);
  const failed = stage === "FAILED";

  return (
    <div
      className={cn(
        "rounded-r2 border border-line bg-surface p-3 transition-colors duration-fast",
        onOpen && "cursor-pointer hover:bg-surface-hover",
        className
      )}
      onClick={onOpen ? () => onOpen(job) : undefined}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="truncate text-body-sm font-medium text-ink-800">
          {jobTypeLabel(job.job_type)}
        </span>
        <span className="shrink-0 text-caption text-ink-400">#{job.id}</span>
      </div>

      <StageProgress stage={stage} progress={job.progress ?? 0} className="mt-2" />

      {failed && (
        <div className="mt-2 flex items-center justify-between gap-2">
          <p className="min-w-0 flex-1 truncate text-caption text-[rgb(var(--danger-600))]">
            {job.error_message || "任务失败"}
          </p>
          {onRetry && (
            <button
              type="button"
              onClick={(e) => {
                // 行本身可点（onOpen），重试钮不能把点击冒泡成「打开」
                e.stopPropagation();
                onRetry(job.id);
              }}
              className="shrink-0 rounded-r1 border border-line px-2 py-1 text-caption font-medium text-ink-700 transition-colors duration-fast hover:bg-surface-hover"
            >
              重试
            </button>
          )}
        </div>
      )}
    </div>
  );
};

JobRow.displayName = "JobRow";
