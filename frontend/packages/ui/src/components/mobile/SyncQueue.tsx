"use client";

import React from "react";
import { cn } from "../../lib/cn";
import { BottomSheet } from "./BottomSheet";
import { useOnlineStatus } from "./OfflineBanner";

export type SyncTaskStatus = "queued" | "syncing" | "failed";

export interface SyncTask {
  id: string;
  /** 展示用描述，如「提交复核：案件分析 #12」 */
  label: string;
  /** 交给 transport 的数据，必须可 JSON 序列化（File 请先转 base64 或走独立上传通道） */
  payload?: unknown;
  /**
   * 任务归属的导航分区 id（与 `AppShellNavItem.id` / `TabBarItem.id` 同名）。
   *
   * 用于把「待同步」琥珀角标显示在**正确的 Tab** 上。用户看到「复核」页签上有
   * 一个琥珀点，才知道待同步的是复核动作；如果只有一个没有落点的全局提示，
   * 他既不知道该不该等，也不知道等一下会同步什么。
   */
  section?: string;
  createdAt: number;
  attempts: number;
  status: SyncTaskStatus;
  error?: string;
}

export interface EnqueueInput {
  /** 幂等键；重复入队同 id 会被忽略，避免弱网重试造成重复提交 */
  id?: string;
  label: string;
  payload?: unknown;
  /** 见 `SyncTask.section` */
  section?: string;
}

export interface SyncQueueValue {
  tasks: SyncTask[];
  pendingCount: number;
  failedCount: number;
  isSyncing: boolean;
  enqueue: (task: EnqueueInput) => string;
  remove: (id: string) => void;
  retry: (id: string) => void;
  clearFailed: () => void;
  flush: () => Promise<void>;
}

export interface SyncQueueProviderProps {
  /** 单条任务的上行通道；抛错即视为失败并计入重试 */
  transport: (task: SyncTask) => Promise<void>;
  storageKey?: string;
  /** 失败多少次后标记为 failed 并停止自动重试，默认 3 */
  maxAttempts?: number;
  /** 恢复在线时自动冲刷，默认开启 */
  autoFlush?: boolean;
  children: React.ReactNode;
}

const SyncQueueContext = React.createContext<SyncQueueValue | null>(null);

let seq = 0;
const nextId = () => `sync-${Date.now().toString(36)}-${seq++}`;

function loadTasks(storageKey: string): SyncTask[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    // 上次会话中断时可能残留 syncing 状态，一律回退为 queued
    return (parsed as SyncTask[])
      .filter((t) => t && typeof t.id === "string")
      .map((t) => ({ ...t, status: t.status === "syncing" ? "queued" : t.status }));
  } catch {
    // 存储损坏时宁可丢队列，也不要让整个应用起不来
    return [];
  }
}

function saveTasks(storageKey: string, tasks: SyncTask[]) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(tasks));
  } catch {
    /* 隐私模式或配额溢出：静默降级为内存队列 */
  }
}

/**
 * 离线同步队列。
 *
 * 移动端弱网下「先记下来、稍后同步」是刚需。这一层的三条硬规则：
 * 1. **幂等**：同 id 任务不重复入队，弱网反复重试不会产生重复提交。
 * 2. **有界重试**：超过 maxAttempts 标记 failed 并停止自动重试，避免
 *    在服务端持续 4xx 时无限打接口。
 * 3. **可持久化**：队列落 localStorage，杀进程重开仍在；读取时把上次
 *    残留的 syncing 状态回退为 queued，防止任务永久卡死。
 */
export const SyncQueueProvider: React.FC<SyncQueueProviderProps> = ({
  transport,
  storageKey = "nlawer.sync-queue",
  maxAttempts = 3,
  autoFlush = true,
  children,
}) => {
  const [tasks, setTasks] = React.useState<SyncTask[]>([]);
  const [isSyncing, setIsSyncing] = React.useState(false);
  const [hydrated, setHydrated] = React.useState(false);
  const online = useOnlineStatus();

  const tasksRef = React.useRef(tasks);
  tasksRef.current = tasks;
  const transportRef = React.useRef(transport);
  transportRef.current = transport;
  const flushingRef = React.useRef(false);

  // 挂载后读取本地队列
  React.useEffect(() => {
    setTasks(loadTasks(storageKey));
    setHydrated(true);
  }, [storageKey]);

  // 变更后落盘（hydrate 之前不写，避免用空数组覆盖已有队列）
  React.useEffect(() => {
    if (hydrated) saveTasks(storageKey, tasks);
  }, [tasks, storageKey, hydrated]);

  const enqueue = React.useCallback((input: EnqueueInput): string => {
    const id = input.id ?? nextId();
    setTasks((prev) => {
      if (prev.some((t) => t.id === id)) return prev;
      const task: SyncTask = {
        id,
        label: input.label,
        payload: input.payload,
        section: input.section,
        createdAt: Date.now(),
        attempts: 0,
        status: "queued",
      };
      return [...prev, task];
    });
    return id;
  }, []);

  const remove = React.useCallback((id: string) => {
    setTasks((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const retry = React.useCallback((id: string) => {
    setTasks((prev) =>
      prev.map((t) => (t.id === id ? { ...t, status: "queued", attempts: 0, error: undefined } : t))
    );
  }, []);

  const clearFailed = React.useCallback(() => {
    setTasks((prev) => prev.filter((t) => t.status !== "failed"));
  }, []);

  const flush = React.useCallback(async () => {
    if (flushingRef.current) return;
    flushingRef.current = true;
    setIsSyncing(true);

    try {
      // 顺序执行：法律场景的写入常有前后依赖，并发会打乱因果
      const queue = tasksRef.current.filter((t) => t.status === "queued");
      for (const task of queue) {
        setTasks((prev) =>
          prev.map((t) => (t.id === task.id ? { ...t, status: "syncing" } : t))
        );
        try {
          await transportRef.current(task);
          setTasks((prev) => prev.filter((t) => t.id !== task.id));
        } catch (err) {
          const attempts = task.attempts + 1;
          const failed = attempts >= maxAttempts;
          setTasks((prev) =>
            prev.map((t) =>
              t.id === task.id
                ? {
                    ...t,
                    attempts,
                    status: failed ? "failed" : "queued",
                    error: err instanceof Error ? err.message : "同步失败",
                  }
                : t
            )
          );
          if (failed) break; // 服务端已不可用，停止本轮避免雪崩
        }
      }
    } finally {
      flushingRef.current = false;
      setIsSyncing(false);
    }
  }, [maxAttempts]);

  // 恢复在线时自动冲刷
  React.useEffect(() => {
    if (!autoFlush || !hydrated || !online) return;
    if (tasks.some((t) => t.status === "queued")) void flush();
  }, [autoFlush, hydrated, online, tasks, flush]);

  const value = React.useMemo<SyncQueueValue>(
    () => ({
      tasks,
      pendingCount: tasks.filter((t) => t.status === "queued" || t.status === "syncing").length,
      failedCount: tasks.filter((t) => t.status === "failed").length,
      isSyncing,
      enqueue,
      remove,
      retry,
      clearFailed,
      flush,
    }),
    [tasks, isSyncing, enqueue, remove, retry, clearFailed, flush]
  );

  return <SyncQueueContext.Provider value={value}>{children}</SyncQueueContext.Provider>;
};

SyncQueueProvider.displayName = "SyncQueueProvider";

export function useSyncQueue(): SyncQueueValue {
  const ctx = React.useContext(SyncQueueContext);
  if (!ctx) throw new Error("useSyncQueue 必须在 <SyncQueueProvider> 内使用");
  return ctx;
}

/**
 * 可选版本：无 Provider 时返回 `null` 而不是抛错。
 *
 * 存在理由是骨架组件面临的两难：`AppLayout` 被四端共用，但**只有需要离线写入
 * 的应用才挂 `SyncQueueProvider`**。若骨架里直接调 `useSyncQueue()`，那么任何
 * 忘记挂 Provider 的应用都会在运行时崩溃——这正是 `useToast` 已经踩过的坑
 * （见技能文档陷阱 20）。
 *
 * 用可选访问器后，骨架可以「有队列就渲染同步 UI，没有就什么都不渲染」，
 * 漏挂 Provider 的后果从「整页崩溃」降级为「少一个功能」。
 */
export function useSyncQueueOptional(): SyncQueueValue | null {
  return React.useContext(SyncQueueContext);
}

export interface SyncQueueBadgeProps {
  onClick?: () => void;
  /** 待同步为 0 时是否仍占位，默认 false */
  showZero?: boolean;
  className?: string;
}

/** 同步状态徽标，通常挂在顶栏或「我的」页。 */
export const SyncQueueBadge: React.FC<SyncQueueBadgeProps> = ({
  onClick,
  showZero = false,
  className,
}) => {
  const { pendingCount, failedCount, isSyncing } = useSyncQueue();
  const total = pendingCount + failedCount;

  if (total === 0 && !showZero) return null;

  const tone = failedCount > 0 ? "danger" : isSyncing ? "info" : "pending";
  const toneCls = {
    danger: "border-danger-500/30 bg-danger-500/10 text-danger-600",
    info: "border-info-500/30 bg-info-500/10 text-info-600",
    pending: "border-pending-500/30 bg-pending-500/10 text-pending-600",
  }[tone];

  const label = failedCount > 0 ? `${failedCount} 条同步失败` : isSyncing ? "同步中…" : `${pendingCount} 条待同步`;

  const Comp = onClick ? "button" : "span";

  return (
    <Comp
      {...(onClick ? { type: "button" as const, onClick } : {})}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-r1 border px-1.5 py-0.5 text-label font-medium",
        onClick && "min-h-tap transition-opacity duration-fast hover:opacity-80",
        toneCls,
        className
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", isSyncing ? "animate-pulse-soft bg-info-500" : "bg-current")} aria-hidden />
      {label}
    </Comp>
  );
};

SyncQueueBadge.displayName = "SyncQueueBadge";

export interface SyncQueueSheetProps {
  isOpen: boolean;
  onClose: () => void;
}

const STATUS_TEXT: Record<SyncTaskStatus, string> = {
  queued: "待同步",
  syncing: "同步中",
  failed: "同步失败",
};

/** 同步队列详情面板（移动端 BottomSheet 形态）。 */
export const SyncQueueSheet: React.FC<SyncQueueSheetProps> = ({ isOpen, onClose }) => {
  const { tasks, pendingCount, failedCount, isSyncing, flush, retry, remove, clearFailed } =
    useSyncQueue();
  const online = useOnlineStatus();

  const formatTime = (ts: number) => {
    const d = new Date(ts);
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };

  return (
    <BottomSheet
      isOpen={isOpen}
      onClose={onClose}
      title="同步队列"
      description={
        tasks.length === 0
          ? "所有记录已同步完成"
          : `${pendingCount} 条待同步${failedCount > 0 ? ` · ${failedCount} 条失败` : ""}`
      }
      heightRatio={0.7}
      footer={
        <div className="flex gap-2">
          {failedCount > 0 && (
            <button
              type="button"
              onClick={clearFailed}
              className="min-h-tap flex-1 rounded-r2 border border-line text-body-sm font-medium text-ink-700 transition-colors duration-fast hover:bg-surface-hover"
            >
              清除失败项
            </button>
          )}
          <button
            type="button"
            onClick={() => void flush()}
            disabled={!online || isSyncing || pendingCount === 0}
            className="min-h-tap flex-1 rounded-r2 bg-brand-600 text-body-sm font-medium text-white transition-colors duration-fast hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isSyncing ? "同步中…" : online ? "立即同步" : "离线中，暂不可同步"}
          </button>
        </div>
      }
    >
      {tasks.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-10">
          <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full border border-verified-500/30 bg-verified-500/10">
            <svg className="h-6 w-6 text-verified-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <p className="text-body-sm text-ink-500">没有待同步的记录</p>
        </div>
      ) : (
        <ul className="divide-y divide-line">
          {tasks.map((task) => (
            <li key={task.id} className="flex items-start gap-3 py-3">
              <span
                aria-hidden
                className={cn(
                  "mt-1.5 h-2 w-2 shrink-0 rounded-full",
                  task.status === "failed"
                    ? "bg-danger-500"
                    : task.status === "syncing"
                      ? "animate-pulse-soft bg-info-500"
                      : "bg-pending-500"
                )}
              />

              <div className="min-w-0 flex-1">
                <p className="truncate text-body-sm text-ink-800">{task.label}</p>
                <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-caption text-ink-500">
                  <span
                    className={cn(
                      task.status === "failed" && "text-danger-600",
                      task.status === "syncing" && "text-info-600"
                    )}
                  >
                    {STATUS_TEXT[task.status]}
                  </span>
                  <span className="num">{formatTime(task.createdAt)}</span>
                  {task.attempts > 0 && <span className="num">已重试 {task.attempts} 次</span>}
                </p>
                {task.error && <p className="mt-1 text-caption text-danger-600">{task.error}</p>}
              </div>

              <div className="flex shrink-0 items-center gap-1">
                {task.status === "failed" && (
                  <button
                    type="button"
                    onClick={() => retry(task.id)}
                    className="min-h-tap rounded-r2 px-2 text-label font-medium text-link transition-colors duration-fast hover:bg-brand-500/10"
                  >
                    重试
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => remove(task.id)}
                  aria-label={`移除 ${task.label}`}
                  className="tap-ghost flex h-8 w-8 items-center justify-center rounded-r2 text-ink-400 transition-colors duration-fast hover:bg-danger-500/10 hover:text-danger-600"
                >
                  <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </BottomSheet>
  );
};

SyncQueueSheet.displayName = "SyncQueueSheet";
