"use client";

import React from "react";
import { cn } from "../lib/cn";
import { Drawer } from "./Drawer";
import { JobRow, type TaskCenterJob } from "./JobRow";
import { Spinner } from "./Spinner";

export type { TaskCenterJob } from "./JobRow";

export interface TaskCenterDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  /** 由调用方从 `GET /api/v1/jobs` 取回并传入。本组件**不自己发请求**。 */
  jobs: TaskCenterJob[];
  loading?: boolean;
  error?: string | null;
  onRetry?: (id: number) => void;
  onRefresh?: () => void;
  onOpen?: (job: TaskCenterJob) => void;
  className?: string;
}

/**
 * 任务中心抽屉（B2）。
 *
 * ## 为什么本组件**不自己发请求**
 *
 * 组件库不应持有数据获取逻辑：四端的 SDK 配置、轮询节奏、租户视角各不相同，
 * 内置 fetch 会把这些差异焊死在共享包里。这里只负责渲染——
 * 调用方（各端）用 `http.get("/api/v1/jobs")` 取数后传进来。
 *
 * ## 为什么必须复用 `GET /api/v1/jobs` 而不是新建任务列表接口
 *
 * 后端已有该端点（含分页、`?status=` 过滤、按 `tenant_id` 隔离），
 * 且它自动对接 `recover_stale_jobs` 僵尸回收。前端**不自建任务存储**——
 * 自建必然与后端状态漂移（僵尸任务前端看不到、后端已回收）。
 *
 * ⚠️ 调用方必须按自己的租户取数：任务表里存着 `input_payload`
 * （含合同原文等），跨租户列出即为数据泄露。
 */
export const TaskCenterDrawer: React.FC<TaskCenterDrawerProps> = ({
  isOpen,
  onClose,
  jobs,
  loading = false,
  error = null,
  onRetry,
  onRefresh,
  onOpen,
  className,
}) => {
  const running = jobs.filter((j) => ["pending", "running", "retrying"].includes(String(j.status).toLowerCase()));

  return (
    <Drawer
      isOpen={isOpen}
      onClose={onClose}
      title="任务中心"
      description={running.length > 0 ? `${running.length} 个进行中` : undefined}
      side="right"
      width="md"
    >
      <div className={cn("flex flex-col gap-3", className)}>
        {onRefresh && (
          <div className="flex items-center justify-end">
            <button
              type="button"
              onClick={onRefresh}
              className="rounded-r1 border border-line px-2 py-1 text-caption text-ink-600 transition-colors duration-fast hover:bg-surface-hover"
            >
              刷新
            </button>
          </div>
        )}

        {loading && (
          <div className="flex items-center justify-center gap-2 py-8 text-body-sm text-ink-500">
            <Spinner size="sm" />
            加载中…
          </div>
        )}

        {!loading && error && (
          <p className="rounded-r2 border border-line bg-surface p-3 text-body-sm text-[rgb(var(--danger-600))]">
            {error}
          </p>
        )}

        {!loading && !error && jobs.length === 0 && (
          <p className="py-8 text-center text-body-sm text-ink-500">暂无任务</p>
        )}

        {!loading && !error && jobs.length > 0 && (
          <div className="flex flex-col gap-2">
            {jobs.map((job) => (
              <JobRow key={job.id} job={job} onRetry={onRetry} onOpen={onOpen} />
            ))}
          </div>
        )}
      </div>
    </Drawer>
  );
};

TaskCenterDrawer.displayName = "TaskCenterDrawer";
