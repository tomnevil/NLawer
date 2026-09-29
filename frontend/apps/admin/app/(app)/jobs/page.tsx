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
 * 任务中心（B2）· 管理端
 *
 * 与律师端同一套实现：任务表本身就是租户级共享视图，
 * **不需要**、也不应该为管理端另做一个列表接口（多一个接口就多一处漂移）。
 *
 * ⚠️ `GET /api/v1/jobs` 已按 `ctx.tenant_id` 过滤，跨租户列出即为数据泄露；
 * 这一层由后端保证，前端不要试图「补全」或缓存其它租户的数据。
 */

const POLL_MS = 5000;
const ACTIVE = ["pending", "running", "retrying"];

export default function AdminJobsPage() {
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
            知识库导入、合规扫描等后台任务的进度与结果
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
