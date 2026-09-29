"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { ApiError, authed, http } from "@nlaw/sdk";
import {
  Alert,
  Button,
  JobRow,
  Spinner,
  useToast,
  type TaskCenterJob,
} from "@nlaw/ui";

/**
 * 任务中心（B2）· 律师端
 *
 * ## 为什么直接复用 `GET /api/v1/jobs`
 *
 * 后端已有该端点：含分页、`?status=` 过滤、**按 `tenant_id` 隔离**，
 * 且自动对接 `recover_stale_jobs` 僵尸回收。前端**不自建任务存储**——
 * 自建必然与后端状态漂移（僵尸任务前端看不到、后端其实已回收）。
 *
 * ## 为什么只在「有进行中任务」时轮询
 *
 * 全部终态后继续轮询是无谓请求；而进行中时若不轮询，用户就得手动刷新，
 * 这正是「不确定的等待」最折磨人的地方。
 */

const POLL_MS = 5000;
const ACTIVE = ["pending", "running", "retrying"];

export default function LawyerJobsPage() {
  const { addToast } = useToast();
  const [jobs, setJobs] = useState<TaskCenterJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (silent = false) => {
    try {
      if (!silent) setLoading(true);
      const page = await http.get<{ items: TaskCenterJob[] }>("/api/v1/jobs", {
        query: { page_size: 50 },
      });
      setJobs(page?.items ?? []);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const running = jobs.some((j) => ACTIVE.includes(String(j.status).toLowerCase()));

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void load(true), POLL_MS);
    return () => clearInterval(timer);
  }, [running, load]);

  const retry = useCallback(
    async (id: number) => {
      try {
        await authed(`/api/v1/jobs/${id}/retry`, { method: "POST", body: {} });
        await load(true);
      } catch (e) {
        addToast({
          type: "error",
          title: "重试失败",
          message: e instanceof ApiError ? e.message : "请求失败",
        });
      }
    },
    [load, addToast]
  );

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-h2 text-ink-900">任务中心</h1>
          <p className="mt-1 text-body-sm text-ink-500">
            案件分析、证据解析、合规扫描等后台任务的进度与结果
          </p>
        </div>
        <Button
          variant="secondary"
          leftIcon={<RefreshCw className="h-3.5 w-3.5" />}
          onClick={() => void load()}
          disabled={loading}
        >
          刷新
        </Button>
      </header>

      {loading && jobs.length === 0 && (
        <div className="flex items-center justify-center gap-2 py-12 text-body-sm text-ink-500">
          <Spinner size="md" />
          加载中…
        </div>
      )}

      {!loading && error && (
        <Alert variant="error" title="加载失败">
          {error}
        </Alert>
      )}

      {!loading && !error && jobs.length === 0 && (
        <p className="rounded-r3 border border-line bg-surface p-8 text-center text-body-sm text-ink-500">
          暂无后台任务
        </p>
      )}

      {jobs.length > 0 && (
        <div className="flex flex-col gap-2">
          {jobs.map((job) => (
            <JobRow key={job.id} job={job} onRetry={retry} />
          ))}
        </div>
      )}
    </div>
  );
}
