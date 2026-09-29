"use client";

import React from "react";
import { cn } from "../lib/cn";

/**
 * 长任务阶段。与后端 `JobStatus` 不完全同构：
 * `JobStatus` 是**执行态**（pending/running/completed/failed/retrying），
 * 而这里是**给用户看的业务阶段**（已接收→解析→审查→出报告）。
 *
 * 二者靠 `fromJob()` 映射：不要让后端返回的状态码直接进 UI 文案——
 * 那样新增 job 类型时必然有一端显示成原始英文枚举。
 */
export type JobStage = "RECEIVED" | "PARSING" | "REVIEWING" | "REPORTING" | "SUCCESS" | "FAILED" | "RETRYING";

export const JOB_STAGE_LABELS: Record<JobStage, string> = {
  RECEIVED: "已接收",
  PARSING: "解析切片",
  REVIEWING: "审查中",
  REPORTING: "生成报告",
  SUCCESS: "已完成",
  FAILED: "失败",
  RETRYING: "重试中",
};

/** 终态：不再显示 ETA。 */
const TERMINAL: JobStage[] = ["SUCCESS", "FAILED"];

export interface JobLike {
  status?: string | null;
  step_name?: string | null;
  progress?: number | null;
}

/**
 * 从后端 Job 推导展示阶段。
 *
 * 映射表写在**组件库**里而不是各端各写一份：否则后端新增一个 `step_name`
 * 时，必然有某一端把它渲染成空白或原始英文（与 `notificationMeta` 同一纪律）。
 */
export function jobStage(job: JobLike): JobStage {
  const s = (job.status || "").toLowerCase();
  if (s === "completed") return "SUCCESS";
  if (s === "failed") return "FAILED";
  if (s === "retrying") return "RETRYING";

  const step = (job.step_name || "").toLowerCase();
  if (step.includes("pars")) return "PARSING";
  if (step.includes("review") || step.includes("scan")) return "REVIEWING";
  if (step.includes("report") || step.includes("gener")) return "REPORTING";
  return "RECEIVED";
}

export interface StageProgressProps {
  stage: JobStage;
  /** 0–100。 */
  progress: number;
  /** 预计剩余秒数；终态忽略。 */
  etaSeconds?: number | null;
  className?: string;
}

function formatEta(sec: number): string {
  if (sec < 60) return `约 ${Math.max(1, Math.round(sec))} 秒`;
  const m = Math.round(sec / 60);
  if (m < 60) return `约 ${m} 分钟`;
  return `约 ${(m / 60).toFixed(1)} 小时`;
}

/**
 * 阶段化进度条：阶段标签 + 百分比 + ETA。
 *
 * ## 为什么必须有「阶段」而不只是一个百分比条
 *
 * 用户研究（瑞思）：**不确定的等待比长时间的等待更痛苦**。百分比只回答
 * 「走了多远」，阶段才回答「现在在干什么、还要几步」——后者才是止住
 * 「以为卡死 ⇒ 重复提交 / 离开」的关键。
 */
export const StageProgress: React.FC<StageProgressProps> = ({
  stage,
  progress,
  etaSeconds,
  className,
}) => {
  const pct = Math.max(0, Math.min(100, Math.round(progress || 0)));
  const done = TERMINAL.includes(stage);
  const eta = !done && etaSeconds && etaSeconds > 0 ? formatEta(etaSeconds) : null;

  return (
    <div className={cn("w-full", className)}>
      <div className="flex items-baseline justify-between gap-2 text-caption">
        <span
          className={cn(
            "font-medium",
            stage === "FAILED" ? "text-[rgb(var(--danger-600))]" : "text-ink-600"
          )}
        >
          {JOB_STAGE_LABELS[stage]}
        </span>
        <span className="tabular-nums text-ink-500">{pct}%</span>
      </div>

      <div
        className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-[rgb(var(--progress-track))]"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${JOB_STAGE_LABELS[stage]} ${pct}%`}
      >
        <div
          className={cn(
            "h-full rounded-full transition-[width] duration-slow ease-out",
            stage === "FAILED"
              ? "bg-[rgb(var(--danger-500))]"
              : done
                ? "bg-[rgb(var(--verified-500))]"
                : "bg-[rgb(var(--progress-bar))]"
          )}
          style={{ width: `${pct}%` }}
        />
      </div>

      {eta && <p className="mt-1 text-caption text-ink-500">预计还需 {eta}</p>}
    </div>
  );
};

StageProgress.displayName = "StageProgress";
