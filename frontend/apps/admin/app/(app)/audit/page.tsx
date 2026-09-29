"use client";

import React from "react";
import { AlertTriangle, Archive, CheckCircle2, Database, RefreshCw, ShieldCheck, Trash2 } from "lucide-react";
import { ApiError, authed } from "@nlaw/sdk";
import { Alert, Badge, Button, Card, Input, KpiCard, Skeleton, Spinner, Timeline } from "@nlaw/ui";

/* ────────────────────────── 后端契约 ────────────────────────── */

interface RetentionStats {
  total: number;
  expired: number;
  retained_days: number;
  baseline_days: number;
  compliant: boolean;
  cutoff: string;
  oldest: string | null;
  newest: string | null;
}

interface RetentionPolicy {
  baseline_days: number;
  effective_days: number;
  basis: string;
  policy: string[];
}

interface ArchiveResult {
  path: string;
  rows: number;
  sha256: string;
  bytes: number;
  cutoff: string;
}

interface PurgeResult {
  dry_run?: boolean;
  would_delete?: number;
  deleted?: number;
  cutoff?: string;
  retained_days?: number;
  note?: string;
  batches?: number;
  tombstone_id?: number;
}

interface ArchiveThenPurgeResult {
  archived: ArchiveResult | null;
  purged: PurgeResult | null;
  note: string;
}

/* ────────────────────────── 工具 ────────────────────────── */

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

export default function AuditRetentionPage() {
  const [stats, setStats] = React.useState<RetentionStats | null>(null);
  const [policy, setPolicy] = React.useState<RetentionPolicy | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");

  // 归档
  const [archiving, setArchiving] = React.useState(false);
  const [archiveResult, setArchiveResult] = React.useState<ArchiveResult | null>(null);

  // 归档并清理（两段式：先 dry-run 拿到真实行数，再要求手输该行数才能执行）
  const [previewing, setPreviewing] = React.useState(false);
  const [purging, setPurging] = React.useState(false);
  const [dryRun, setDryRun] = React.useState<PurgeResult | null>(null);
  const [purgeResult, setPurgeResult] = React.useState<ArchiveThenPurgeResult | null>(null);
  const [confirmText, setConfirmText] = React.useState("");
  const [confirmError, setConfirmError] = React.useState("");

  const load = React.useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [s, p] = await Promise.all([
        authed<RetentionStats>("/api/v1/audit/retention/stats"),
        authed<RetentionPolicy>("/api/v1/audit/retention/policy"),
      ]);
      setStats(s);
      setPolicy(p);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return; // 外壳负责跳转
      setError(e instanceof ApiError ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  /** 归档：只导出不删除，因此不需要二次确认 */
  const doArchive = async () => {
    setArchiving(true);
    setError("");
    try {
      const r = await authed<ArchiveResult>("/api/v1/audit/retention/archive", {
        method: "POST",
        body: {},
      });
      setArchiveResult(r);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "归档失败");
    } finally {
      setArchiving(false);
    }
  };

  /** 第一步：dry-run，拿到「会删除多少行」这个真实数字 */
  const doPreview = async () => {
    setPreviewing(true);
    setError("");
    setConfirmText("");
    setConfirmError("");
    try {
      const r = await authed<ArchiveThenPurgeResult>("/api/v1/audit/retention/archive-then-purge", {
        method: "POST",
        body: { confirm: false },
      });
      setPurgeResult(r);
      setDryRun(r.purged ?? null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "预演失败");
    } finally {
      setPreviewing(false);
    }
  };

  /**
   * 第二步：真正执行。
   *
   * 要求用户**手动输入待删除行数**才放行——这是不可逆操作，
   * 必须有一个「你真的看懂了影响面」的动作门槛，而不是点两下确认框。
   */
  const doPurge = async () => {
    const expected = dryRun?.would_delete ?? 0;
    if (confirmText.trim() !== String(expected)) {
      setConfirmError(`请输入待清理的行数：${expected}`);
      return;
    }
    setPurging(true);
    setError("");
    try {
      const r = await authed<ArchiveThenPurgeResult>("/api/v1/audit/retention/archive-then-purge", {
        method: "POST",
        body: { confirm: true },
      });
      setPurgeResult(r);
      setDryRun(null);
      setConfirmText("");
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "清理失败");
    } finally {
      setPurging(false);
    }
  };

  const expiring = stats?.expired ?? 0;
  const clean = expiring === 0;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-h1 text-ink-900">审计保留期</h1>
          <p className="mt-1 text-body-sm text-ink-500">
            等保 2.0（三级）要求审计记录留存不少于 6 个月。保留期内不得删除，到期前必须先归档。
          </p>
        </div>
        <Button variant="outline" onClick={() => void load()} disabled={loading}>
          {loading ? <Spinner size="sm" label={null} /> : <RefreshCw className="h-4 w-4" />}
          刷新
        </Button>
      </header>

      {error && (
        <div role="alert" className="rounded-r2 border border-danger-500/30 bg-danger-500/10 px-3 py-2 text-body-sm text-danger-600">
          {error}
        </div>
      )}

      {/* ── 状态 ─────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {loading ? (
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[92px] w-full" />)
        ) : (
          <>
            <KpiCard
              label="审计记录总量"
              value={stats?.total ?? 0}
              unit="条"
              icon={<Database className="h-4 w-4" />}
              color="brand"
            />
            <KpiCard
              label="超出保留期"
              value={expiring}
              unit="条"
              icon={<AlertTriangle className="h-4 w-4" />}
              color={clean ? "verified" : "pending"}
            />
            <KpiCard
              label="生效保留天数"
              value={stats?.retained_days ?? 0}
              unit="天"
              icon={<ShieldCheck className="h-4 w-4" />}
              color="info"
            />
            <KpiCard
              label="合规基线"
              value={stats?.baseline_days ?? 0}
              unit="天"
              icon={<ShieldCheck className="h-4 w-4" />}
              color="gold"
            />
          </>
        )}
      </div>

      {!loading && (
        <Alert variant={clean ? "success" : "warning"} title={clean ? "合规状态正常" : "存在超出保留期的记录"}>
          {clean
            ? "当前没有超出保留期的审计记录，无需归档或清理。"
            : `有 ${expiring} 条记录已超出保留期。请先归档并核对 sha256，确认无误后再执行清理。`}
        </Alert>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        {/* ── 保留时间轴 ───────────────────────────── */}
        <Card className="lg:col-span-2" hover={false}>
          <h2 className="border-b border-line px-4 py-3 text-h4 text-ink-900">保留时间轴</h2>
          {loading ? (
            <div className="space-y-3 p-4">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : (
            <div className="p-4">
              <Timeline
                items={[
                  {
                    id: "oldest",
                    title: "最早记录",
                    time: fmtDate(stats?.oldest ?? null),
                    description: "保留期从这条开始计算",
                    status: expiring > 0 ? "done" : "current",
                  },
                  {
                    id: "cutoff",
                    title: "保留期截点",
                    time: fmtDate(stats?.cutoff ?? null),
                    description: `此时间之前的记录已超出 ${stats?.retained_days ?? 0} 天保留期`,
                    status: expiring > 0 ? "rejected" : "pending",
                  },
                  {
                    id: "newest",
                    title: "最新记录",
                    time: fmtDate(stats?.newest ?? null),
                    status: "pending",
                  },
                ]}
              />
            </div>
          )}
        </Card>

        {/* ── 策略说明 ─────────────────────────────── */}
        <Card hover={false}>
          <h2 className="border-b border-line px-4 py-3 text-h4 text-ink-900">清理策略</h2>
          {loading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-5 w-full" />
              ))}
            </div>
          ) : (
            <>
              {policy && (
                <p className="border-b border-line bg-surface-subtle px-4 py-2.5 text-caption text-ink-500">
                  {policy.basis}
                </p>
              )}
              <ul className="space-y-2 p-4">
                {(policy?.policy ?? []).map((line, i) => (
                  <li key={i} className="flex gap-2 text-body-sm text-ink-700">
                    <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-verified-500" />
                    <span>{line}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </Card>
      </div>

      {/* ── 归档 ─────────────────────────────────────── */}
      <Card hover={false}>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
          <div>
            <h2 className="text-h4 text-ink-900">归档过期日志</h2>
            <p className="mt-0.5 text-caption text-ink-500">
              导出为 gzip JSONL 并返回 sha256 校验值。<strong>归档本身不删除任何数据。</strong>
            </p>
          </div>
          <Button variant="secondary" onClick={() => void doArchive()} disabled={archiving || clean}>
            {archiving ? <Spinner size="sm" label={null} /> : <Archive className="h-4 w-4" />}
            归档
          </Button>
        </div>

        {archiveResult && (
          <dl className="grid gap-x-6 gap-y-2 p-4 sm:grid-cols-2">
            <div>
              <dt className="text-caption text-ink-500">归档行数</dt>
              <dd className="num text-body-sm text-ink-900">{archiveResult.rows}</dd>
            </div>
            <div>
              <dt className="text-caption text-ink-500">产物大小</dt>
              <dd className="num text-body-sm text-ink-900">{fmtBytes(archiveResult.bytes)}</dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="text-caption text-ink-500">产物路径</dt>
              <dd className="num break-all text-body-sm text-ink-700">{archiveResult.path}</dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="text-caption text-ink-500">sha256（请人工核对后再清理）</dt>
              <dd className="num break-all rounded-r1 border border-line bg-surface-subtle px-2 py-1.5 text-caption text-ink-700">
                {archiveResult.sha256}
              </dd>
            </div>
          </dl>
        )}

        {!archiveResult && !loading && (
          <p className="px-4 py-6 text-center text-body-sm text-ink-500">
            {clean ? "没有需要归档的记录" : "点击「归档」导出超出保留期的日志"}
          </p>
        )}
      </Card>

      {/* ── 清理（不可逆）───────────────────────────── */}
      <Card hover={false} className="border-danger-500/30">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-danger-500/30 px-4 py-3">
          <div>
            <h2 className="flex items-center gap-2 text-h4 text-ink-900">
              <Trash2 className="h-4 w-4 text-danger-500" />
              归档并清理
            </h2>
            <p className="mt-0.5 text-caption text-ink-500">
              先归档并校验行数，一致才清理；不一致则中止（宁可保留，不可丢失）。
            </p>
          </div>
          <Button variant="outline" onClick={() => void doPreview()} disabled={previewing || clean}>
            {previewing ? <Spinner size="sm" label={null} /> : null}
            预演（不删除）
          </Button>
        </div>

        <div className="space-y-4 p-4">
          {/* 预演结果 */}
          {dryRun && dryRun.would_delete !== undefined && (
            <div className="rounded-r2 border border-pending-500/30 bg-pending-500/10 p-3.5">
              <p className="text-body-sm font-medium text-pending-700">
                预演结果：将删除 <span className="num">{dryRun.would_delete}</span> 条超出保留期的记录
              </p>
              <p className="mt-1 text-caption text-pending-600">
                截点 {fmtDate(dryRun.cutoff ?? null)} · 保留 {dryRun.retained_days ?? 0} 天 · {dryRun.note}
              </p>
            </div>
          )}

          {/* 二次确认：必须手输行数 */}
          {dryRun && (dryRun.would_delete ?? 0) > 0 && (
            <div className="space-y-2 rounded-r2 border border-line bg-surface-subtle p-3.5">
              <p className="text-body-sm text-ink-700">
                此操作<strong className="text-danger-600">不可逆</strong>。请输入待清理的行数
                <span className="num mx-1 rounded-r1 bg-danger-500/15 px-1.5 font-medium text-danger-600">
                  {dryRun.would_delete}
                </span>
                以确认你已核对影响面。
              </p>
              <div className="flex flex-wrap items-end gap-2">
                <div className="w-40">
                  <Input
                    value={confirmText}
                    onChange={(e) => {
                      setConfirmText(e.target.value);
                      setConfirmError("");
                    }}
                    placeholder={String(dryRun.would_delete)}
                    error={confirmError}
                    inputMode="numeric"
                    aria-label="输入待清理行数以确认"
                  />
                </div>
                <Button variant="danger" onClick={() => void doPurge()} disabled={purging}>
                  {purging ? <Spinner size="sm" label={null} /> : <Trash2 className="h-4 w-4" />}
                  确认清理
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => {
                    setDryRun(null);
                    setConfirmText("");
                    setConfirmError("");
                  }}
                  disabled={purging}
                >
                  取消
                </Button>
              </div>
            </div>
          )}

          {/* 执行结果 */}
          {purgeResult && !dryRun && (
            <div
              className={`rounded-r2 border p-3.5 ${
                purgeResult.purged?.deleted !== undefined
                  ? "border-verified-500/30 bg-verified-500/10"
                  : "border-line bg-surface-subtle"
              }`}
            >
              <p className="text-body-sm font-medium text-ink-800">{purgeResult.note}</p>
              {purgeResult.archived && (
                <p className="num mt-1 text-caption text-ink-600">
                  已归档 {purgeResult.archived.rows} 行 · {fmtBytes(purgeResult.archived.bytes)} · sha256{" "}
                  {purgeResult.archived.sha256.slice(0, 16)}…
                </p>
              )}
              {purgeResult.purged?.deleted !== undefined && (
                <p className="num mt-1 text-caption text-verified-700">
                  已删除 {purgeResult.purged.deleted} 行
                  {purgeResult.purged.tombstone_id
                    ? ` · 清理动作已写入审计 #${purgeResult.purged.tombstone_id}`
                    : ""}
                </p>
              )}
            </div>
          )}

          {!dryRun && !purgeResult && (
            <p className="text-body-sm text-ink-500">
              {clean
                ? "没有超出保留期的记录，无需清理。"
                : "点击「预演」查看将被删除的行数，预演不会删除任何数据。"}
            </p>
          )}

          <p className="flex items-start gap-1.5 text-caption text-ink-400">
            <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            每次清理都会写入一条 AUDIT_RETENTION_PURGE 审计记录——审计的删除本身也必须被审计。
          </p>
        </div>
      </Card>
    </div>
  );
}
