"use client";

import React from "react";
import { AlertTriangle, RefreshCw, ShieldCheck } from "lucide-react";
import { ApiError, authed, tenantScope } from "@nlaw/sdk";
import {
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
  FilterBar,
  KpiCard,
  Modal,
  Pagination,
  Skeleton,
  Spinner,
  cn,
  type BadgeProps,
  type DataTableColumn,
  type FilterChip,
} from "@nlaw/ui";

/* ────────────────────────── 后端契约 ────────────────────────── */

/** `app/schemas/compliance.py::ComplianceScanOut`。 */
interface Scan {
  id: number;
  title: string;
  status: string;
  dimensions?: string[] | null;
  scope?: string | null;
  input_summary?: string | null;
  overall_risk: string;
  dimension_scores?: Record<string, DimScore> | null;
  report_summary?: string | null;
  is_external: boolean;
  review_status?: string | null;
}

interface DimScore {
  score?: number;
  risk?: string;
  /** 该维度命中的规则条数（每条命中对应一行 `ComplianceFinding`）。 */
  findings?: number;
}

interface Finding {
  id: number;
  dimension: string;
  title: string;
  description?: string | null;
  risk_level: string;
  suggestion?: string | null;
}

interface ScanDetail extends Scan {
  findings?: Finding[];
}

/** `ReviewOut`，仅用于取 `total`。 */
interface Paged<T> {
  items: T[];
  total: number;
}

/* ────────────────────────── 常量映射 ────────────────────────── */

/** `ComplianceDimension`。顺序即后端 `_DIMENSION_RULES` 的定义顺序。 */
const DIMS = [
  { key: "LABOR", label: "劳动用工" },
  { key: "COMMERCIAL", label: "商业合同" },
  { key: "DATA_PRIVACY", label: "数据隐私" },
  { key: "ADVERTISING", label: "广告营销" },
] as const;

const DIM_LABEL: Record<string, string> = Object.fromEntries(DIMS.map((d) => [d.key, d.label]));

/** `RiskLevel`。`NONE` 是模型默认值（尚未扫描）。 */
const RISK_META: Record<string, { label: string; tone: BadgeProps["variant"] }> = {
  HIGH: { label: "高风险", tone: "danger" },
  MEDIUM: { label: "中风险", tone: "pending" },
  LOW: { label: "低风险", tone: "verified" },
  NONE: { label: "未评级", tone: "neutral" },
};

/** `ScanStatus`。 */
const STATUS_LABEL: Record<string, string> = {
  PENDING: "排队中",
  RUNNING: "扫描中",
  COMPLETED: "已完成",
  FAILED: "失败",
};

/**
 * 分数色阶，与后端阈值**逐字对齐**：
 * `_risk = HIGH if score < 60 else (MEDIUM if score < 85 else LOW)`。
 * 注意 85 本身属于 LOW（后端用的是严格小于），此处不可写成 `<= 85`。
 */
function scoreTone(score?: number): string {
  if (score === undefined) return "text-ink-400";
  if (score < 60) return "text-danger-600";
  if (score < 85) return "text-pending-600";
  return "text-verified-600";
}

/** 一条扫描的命中项总数：由各维度 `findings` 求和（后端每条命中建一行 finding）。 */
function hitCount(s: Scan): number {
  return Object.values(s.dimension_scores ?? {}).reduce((n, d) => n + (d?.findings ?? 0), 0);
}

const PAGE_SIZE = 20;

/* ────────────────────────── 页面 ────────────────────────── */

/**
 * 运营后台·合规扫描监控（**只读**）。
 *
 * ## 为什么是「监控台」而不是又一个「发起扫描」
 *
 * `apps/web/app/(app)/compliance/page.tsx` 已经是一个完整可用的合规扫描页：
 * 发起自查、看四维得分、看发现项与整改建议、看历史。**那才是这个能力的主场。**
 * 在 admin 重做一遍会造出「同一件事两个入口」，且平台运营方本来也不是
 * 「发起自查」的责任主体（自查是客户/律所自己的动作）。
 *
 * 运营方真正需要的是**监督视角**：这个租户的合规风险分布如何、
 * 哪些报告是要对外交付的、那些标记了「需复核」的到底有没有进入复核流程。
 *
 * ## ⚠️ 三条必须如实告知的能力边界（都会直接影响结论可信度）
 *
 * 1. **这是关键词规则引擎，不是 AI 语义分析。**
 *    `compliance_service.py::_DIMENSION_RULES` 是一张固定关键词表，
 *    `run()` 里做的是 `r["kw"] in text` 的子串匹配。
 *    ⇒ **摘要写不全就会得到偏高的分数；未命中关键词 ≠ 合规。**
 *
 * 2. **广告营销维度含一个单字关键词 `最`**，`最近`/`最终`/`最好` 都会命中。
 *    实测（中性文本 `最近公司组织了一次团建活动`）该维度 100 → 85 分并产生
 *    一条「宣传用语含绝对化用语」发现项。**该维度几乎必然产生发现项。**
 *    后端已冻结，本轮仅登记，不修改。
 *
 * 3. **`review_status` 只是一个「要求级别」标签，不产生复核任务。**
 *    `run()` 里写的是 `scan.review_status = highest_level(hits).value`
 *    （对外报告命中 `EXTERNAL_COMPLIANCE_REPORT` → `L2`，否则 `L1`），
 *    但**合规链路从不调用 `ReviewService`**，`ReviewTargetType.COMPLIANCE_REPORT`
 *    在全仓**零引用**。⇒ 标了「需 L2 复核」，却没有任何任务、指派或追踪。
 *    本页用一条**实时断言**把这件事显示出来（见 `orphanReviews`），
 *    而不是写死在文案里——后端一旦补上，这个数字会自己变。
 */
export default function AdminCompliancePage() {
  const [scans, setScans] = React.useState<Scan[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");

  /** 复核队列中 `target_type=COMPLIANCE_REPORT` 的任务数（用于实时验证边界 3）。 */
  const [orphanReviews, setOrphanReviews] = React.useState<number | null>(null);

  // 客户端筛选：`GET /compliance/scans` 返回**裸数组**，无分页、无筛选参数
  const [risk, setRisk] = React.useState("");
  const [external, setExternal] = React.useState("");
  const [keyword, setKeyword] = React.useState("");
  const [page, setPage] = React.useState(1);

  const [detail, setDetail] = React.useState<ScanDetail | null>(null);
  const [detailLoading, setDetailLoading] = React.useState(false);
  const [detailError, setDetailError] = React.useState("");

  const [scope, setScope] = React.useState<string | null>(null);
  React.useEffect(() => setScope(tenantScope.get()), []);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [list, cr] = await Promise.all([
        authed<Scan[]>("/api/v1/compliance/scans"),
        // 这条查询本身就是「边界 3」的证据：合规报告复核任务是否存在
        authed<Paged<unknown>>("/api/v1/reviews", {
          query: { page: 1, page_size: 1, target_type: "COMPLIANCE_REPORT" },
        }),
      ]);
      setScans(Array.isArray(list) ? list : []);
      setOrphanReviews(cr?.total ?? 0);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return;
      setError(e instanceof ApiError ? e.message : "加载失败");
      setScans([]);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  const openDetail = async (row: Scan) => {
    setDetail(row);
    setDetailError("");
    setDetailLoading(true);
    try {
      setDetail(await authed<ScanDetail>(`/api/v1/compliance/scans/${row.id}`));
    } catch (e) {
      setDetailError(e instanceof ApiError ? e.message : "详情加载失败");
    } finally {
      setDetailLoading(false);
    }
  };

  const resetTo = (fn: () => void) => {
    fn();
    setPage(1);
  };

  /* ── 客户端筛选 ─────────────────────────────────── */
  const filtered = React.useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    return scans.filter((s) => {
      if (risk && s.overall_risk !== risk) return false;
      if (external === "yes" && !s.is_external) return false;
      if (external === "no" && s.is_external) return false;
      if (kw) {
        const hay = `${s.title} ${s.scope ?? ""} ${s.input_summary ?? ""}`.toLowerCase();
        if (!hay.includes(kw)) return false;
      }
      return true;
    });
  }, [scans, risk, external, keyword]);

  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const paged = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  const chips: FilterChip[] = [];
  if (risk) chips.push({ id: "risk", label: "整体风险", value: RISK_META[risk]?.label ?? risk });
  if (external)
    chips.push({ id: "external", label: "用途", value: external === "yes" ? "对外报告" : "内部使用" });
  if (keyword.trim()) chips.push({ id: "keyword", label: "关键词", value: keyword.trim() });

  const clearAll = () =>
    resetTo(() => {
      setRisk("");
      setExternal("");
      setKeyword("");
    });

  /* ── KPI ───────────────────────────────────────── */
  const kpi = React.useMemo(() => {
    const high = scans.filter((s) => s.overall_risk === "HIGH").length;
    const ext = scans.filter((s) => s.is_external).length;
    const needL2 = scans.filter((s) => s.review_status === "L2").length;
    const hits = scans.reduce((n, s) => n + hitCount(s), 0);
    return { total: scans.length, high, ext, needL2, hits };
  }, [scans]);

  /**
   * 标记了需 L2 复核、但复核队列里查不到对应任务的条数。
   *
   * 用「相减」而不是「队列为 0 就算全部」：后端若只给部分报告建了任务，
   * 相减仍能给出正确的缺口数，而「等于 0 才判定」会把部分落实误报成全部落实。
   */
  const unbacked = Math.max(0, kpi.needL2 - (orphanReviews ?? 0));

  const columns: DataTableColumn<Scan>[] = React.useMemo(
    () => [
      {
        key: "title",
        header: "扫描任务",
        mobile: "primary",
        // 详情入口挂 primary 列：DataTable 卡片模式下会切掉末尾的操作列
        render: (_v, row) => (
          <button
            type="button"
            onClick={() => void openDetail(row)}
            className="max-w-[22rem] truncate text-left font-medium text-link underline-offset-2 hover:underline"
            title={row.title}
          >
            {row.title}
          </button>
        ),
      },
      {
        key: "overall_risk",
        header: "整体风险",
        width: "104px",
        mobile: "status",
        render: (_v, row) => (
          <Badge variant={RISK_META[row.overall_risk]?.tone ?? "neutral"}>
            {RISK_META[row.overall_risk]?.label ?? row.overall_risk}
          </Badge>
        ),
      },
      {
        key: "hits",
        header: "命中项",
        numeric: true,
        width: "88px",
        align: "center",
        render: (_v, row) => {
          const n = hitCount(row);
          return n > 0 ? (
            <span className="num font-medium text-ink-800">{n}</span>
          ) : (
            <span className="text-ink-400">0</span>
          );
        },
      },
      {
        key: "dimensions",
        header: "维度",
        numeric: true,
        width: "72px",
        align: "center",
        render: (_v, row) => <span className="num">{row.dimensions?.length ?? 0}</span>,
      },
      {
        key: "is_external",
        header: "用途",
        width: "116px",
        render: (_v, row) =>
          row.is_external ? (
            <Badge variant="pending">对外报告</Badge>
          ) : (
            <span className="text-caption text-ink-500">内部使用</span>
          ),
      },
      {
        // 注意语义：这是**要求的复核级别**，不是「已复核」。后端只写标签不建任务。
        key: "review_status",
        header: "要求复核",
        width: "104px",
        align: "center",
        render: (_v, row) => {
          const lv = row.review_status;
          if (!lv || lv === "L1") return <span className="text-caption text-ink-400">L1（无需）</span>;
          return <Badge variant="info">{lv}</Badge>;
        },
      },
      {
        key: "status",
        header: "执行状态",
        width: "96px",
        render: (_v, row) => (
          <span className="text-caption text-ink-600">
            {STATUS_LABEL[row.status] ?? row.status}
          </span>
        ),
      },
      {
        key: "_actions",
        header: "操作",
        width: "88px",
        align: "right",
        render: (_v, row) => (
          <Button variant="outline" size="sm" onClick={() => void openDetail(row)}>
            详情
          </Button>
        ),
      },
    ],
    [],
  );

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-h1 text-ink-900">合规扫描监控</h1>
          <p className="mt-1 text-body-sm text-ink-500">
            四维合规扫描的风险分布与对外报告复核情况。数据范围跟随顶部「租户视角」。
          </p>
        </div>
        <Button variant="outline" onClick={() => void load()} disabled={loading}>
          {loading ? <Spinner size="sm" label={null} /> : <RefreshCw className="h-4 w-4" />}
          刷新
        </Button>
      </header>

      {error && (
        <div
          role="alert"
          className="rounded-r2 border border-danger-500/30 bg-danger-500/10 px-3 py-2 text-body-sm text-danger-600"
        >
          {error}
        </div>
      )}

      {/* 只读声明 */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-r2 border border-line bg-surface-subtle px-3.5 py-2.5">
        <Badge variant="neutral">只读监控</Badge>
        <Badge variant={scope ? "primary" : "neutral"}>
          {scope ? `租户 ${scope}` : "账号自身租户"}
        </Badge>
        <p className="min-w-0 flex-1 text-caption text-ink-500">
          发起合规自查属客户端动作（在客户端应用的「合规扫描」页完成）。
          本页只做监督：风险分布、对外报告、复核标记落实情况，
          <strong className="font-medium text-ink-600">不提供发起入口</strong>。
        </p>
      </div>

      {/*
        能力边界告知。这三条直接决定「这个页面的数字能不能当结论用」，
        因此放在 KPI 之上而不是折叠起来。
      */}
      <Alert variant="warning" title="本页数字由关键词规则引擎产出，不是 AI 语义分析">
        <ul className="list-disc space-y-1 pl-4">
          <li>
            后端按<strong className="font-medium">固定关键词表</strong>匹配摘要文本（
            <code>input_summary</code> / <code>scope</code>），命中即扣分。
            <strong className="font-medium">
              摘要写不全就会得到偏高的分数；未命中关键词不等于合规。
            </strong>
          </li>
          <li>
            广告营销维度含<strong className="font-medium">单字</strong>关键词「最」，
            「最近」「最终」等普通词都会命中。实测中性文本
            「最近公司组织了一次团建活动」该维度即 100 → 85 分并产生一条
            「绝对化用语」发现项，
            <strong className="font-medium">该维度几乎必然产生发现项</strong>。
          </li>
          <li>
            「要求复核」列是<strong className="font-medium">要求级别标签，不是复核状态</strong>
            ：后端只写 <code>review_status</code>，从不创建复核任务（见下方核对结果）。
          </li>
        </ul>
      </Alert>

      {/* ── 复核标记落实情况（实时断言，非写死文案）──────── */}
      {!loading && kpi.needL2 > 0 && (
        <Alert
          variant={unbacked > 0 ? "error" : "info"}
          title={
            unbacked > 0
              ? `${unbacked} 份对外报告标记了需 L2 复核，但复核队列中没有对应任务`
              : `对外报告的 L2 复核标记已能在复核队列中查到对应任务`
          }
        >
          {unbacked > 0 ? (
            <>
              本租户有 <strong className="font-medium">{kpi.needL2}</strong> 份对外报告被标记
              「需 L2 复核」，而复核队列中 <code>target_type=COMPLIANCE_REPORT</code> 的任务数为{" "}
              <strong className="num font-medium">{orphanReviews ?? 0}</strong>。
              也就是说这个标记
              <strong className="font-medium">目前不产生任何复核流程</strong>
              ——没有任务、没有指派、没有人知道要复核。
              <br />
              后端已冻结，本轮仅登记不改动：合规链路从不调用 <code>ReviewService</code>，
              <code>ReviewTargetType.COMPLIANCE_REPORT</code> 全仓零引用。
            </>
          ) : (
            <>
              实时核对通过：标记了 L2 的对外报告（{kpi.needL2} 份），
              在复核队列中能查到 <strong className="num font-medium">{orphanReviews ?? 0}</strong>{" "}
              条对应任务。
            </>
          )}
        </Alert>
      )}

      {/* ── KPI ─────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {loading && scans.length === 0 ? (
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[92px] w-full" />)
        ) : (
          <>
            <KpiCard
              label="扫描任务"
              value={kpi.total}
              unit="次"
              icon={<ShieldCheck className="h-4 w-4" />}
              color="brand"
            />
            <KpiCard
              label="高风险"
              value={kpi.high}
              unit="次"
              icon={<AlertTriangle className="h-4 w-4" />}
              color={kpi.high > 0 ? "danger" : "verified"}
            />
            <KpiCard
              label="对外报告"
              value={kpi.ext}
              unit="份"
              icon={<ShieldCheck className="h-4 w-4" />}
              color={kpi.ext > 0 ? "pending" : "verified"}
            />
            <KpiCard
              label="累计命中项"
              value={kpi.hits}
              unit="项"
              icon={<AlertTriangle className="h-4 w-4" />}
              color="info"
            />
          </>
        )}
      </div>

      {/* ── 列表 ────────────────────────────────────── */}
      <Card hover={false}>
        <div className="border-b border-line px-4 py-3">
          <FilterBar
            chips={chips}
            onRemove={(id) =>
              resetTo(() => {
                if (id === "risk") setRisk("");
                if (id === "external") setExternal("");
                if (id === "keyword") setKeyword("");
              })
            }
            onClearAll={clearAll}
            resultCount={filtered.length}
          >
            <label className="sr-only" htmlFor="scan-risk">
              按整体风险筛选
            </label>
            <select
              id="scan-risk"
              value={risk}
              onChange={(e) => resetTo(() => setRisk(e.target.value))}
              className={cn(
                "h-9 rounded-r2 border border-line bg-surface px-2.5 text-body-sm text-ink-800",
                "transition-colors duration-fast focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-500/30",
              )}
            >
              <option value="">全部风险</option>
              {Object.entries(RISK_META).map(([k, v]) => (
                <option key={k} value={k}>
                  {v.label}
                </option>
              ))}
            </select>

            <label className="sr-only" htmlFor="scan-external">
              按用途筛选
            </label>
            <select
              id="scan-external"
              value={external}
              onChange={(e) => resetTo(() => setExternal(e.target.value))}
              className={cn(
                "h-9 rounded-r2 border border-line bg-surface px-2.5 text-body-sm text-ink-800",
                "transition-colors duration-fast focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-500/30",
              )}
            >
              <option value="">全部用途</option>
              <option value="yes">对外报告</option>
              <option value="no">内部使用</option>
            </select>

            <label className="sr-only" htmlFor="scan-keyword">
              按标题或摘要搜索
            </label>
            <input
              id="scan-keyword"
              type="search"
              value={keyword}
              onChange={(e) => resetTo(() => setKeyword(e.target.value))}
              placeholder="搜索标题或摘要…"
              className={cn(
                "h-9 w-full rounded-r2 border border-line bg-surface px-2.5 text-body-sm text-ink-800 sm:w-56",
                "placeholder:text-ink-400",
                "transition-colors duration-fast focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-500/30",
              )}
            />
          </FilterBar>

          {/* 裸数组端点的事实必须说出来，否则「共 N 条」会被误读成全量 */}
          <p className="mt-2 text-caption text-ink-400">
            后端 <code>GET /compliance/scans</code> 返回<strong>全量裸数组</strong>
            （无分页、无筛选参数），筛选与分页均在本页本地完成。
          </p>
        </div>

        {loading && scans.length === 0 ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : (
          <DataTable
            columns={columns}
            data={paged}
            rowKey={(r) => String(r.id)}
            emptyMessage={
              chips.length > 0
                ? "没有符合当前筛选条件的扫描任务"
                : "当前租户下暂无合规扫描记录"
            }
          />
        )}

        {filtered.length > PAGE_SIZE && (
          <div className="border-t border-line px-4 py-3">
            <Pagination
              page={page}
              pageSize={PAGE_SIZE}
              total={filtered.length}
              onPageChange={setPage}
            />
          </div>
        )}
      </Card>

      {/* ── 详情 ────────────────────────────────────── */}
      <Modal
        isOpen={detail !== null}
        onClose={() => setDetail(null)}
        size="lg"
        title={detail?.title ?? ""}
        description={
          detail
            ? `${RISK_META[detail.overall_risk]?.label ?? detail.overall_risk} · ${
                detail.is_external ? "对外报告" : "内部使用"
              } · ${STATUS_LABEL[detail.status] ?? detail.status}`
            : undefined
        }
      >
        {detail && (
          <div className="space-y-4">
            {detailError && (
              <p role="alert" className="text-body-sm text-danger-600">
                {detailError}
              </p>
            )}

            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={RISK_META[detail.overall_risk]?.tone ?? "neutral"}>
                整体 {RISK_META[detail.overall_risk]?.label ?? detail.overall_risk}
              </Badge>
              {detail.is_external && <Badge variant="pending">对外报告</Badge>}
              {detail.review_status && detail.review_status !== "L1" && (
                <Badge variant="info">要求 {detail.review_status} 复核</Badge>
              )}
              <span className="num ml-auto text-caption text-ink-400">扫描 #{detail.id}</span>
            </div>

            {/* 四维得分 */}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {DIMS.map((d) => {
                const sc = detail.dimension_scores?.[d.key];
                const scanned = (detail.dimensions ?? []).includes(d.key);
                return (
                  <div key={d.key} className="rounded-r2 border border-line p-3 text-center">
                    <p className="text-caption text-ink-500">{d.label}</p>
                    <p className={cn("num mt-1 text-h2 font-semibold", scoreTone(sc?.score))}>
                      {sc?.score ?? "—"}
                    </p>
                    <p className="mt-0.5 text-caption text-ink-500">
                      {!scanned
                        ? "未纳入本次扫描"
                        : sc?.risk
                          ? (RISK_META[sc.risk]?.label ?? sc.risk)
                          : "未评级"}
                      {sc?.findings ? ` · 命中 ${sc.findings}` : ""}
                    </p>
                  </div>
                );
              })}
            </div>

            {detail.scope && (
              <div>
                <h4 className="text-caption text-ink-500">扫描范围</h4>
                <p className="mt-1 text-body-sm text-ink-700">{detail.scope}</p>
              </div>
            )}

            {detail.input_summary && (
              <div>
                <h4 className="text-caption text-ink-500">自查材料摘要</h4>
                <p className="mt-1 whitespace-pre-line rounded-r2 border border-line bg-surface-subtle p-3 text-body-sm text-ink-700">
                  {detail.input_summary}
                </p>
              </div>
            )}

            {detail.report_summary && (
              <div>
                <h4 className="text-caption text-ink-500">报告摘要</h4>
                <p className="mt-1 whitespace-pre-line rounded-r2 bg-surface-subtle p-3 text-body-sm text-ink-700">
                  {detail.report_summary}
                </p>
              </div>
            )}

            {/* 发现项 */}
            <div>
              <h4 className="mb-2 text-caption text-ink-500">
                发现项（{(detail.findings ?? []).length} 项）
              </h4>
              {detailLoading ? (
                <div className="space-y-2">
                  {Array.from({ length: 3 }).map((_, i) => (
                    <Skeleton key={i} className="h-12 w-full" />
                  ))}
                </div>
              ) : (detail.findings ?? []).length === 0 ? (
                <p className="rounded-r2 border border-line bg-surface-subtle p-3 text-body-sm text-ink-500">
                  未命中任何关键词规则。
                  <span className="text-ink-400">
                    {" "}
                    注意：这不等于合规——规则表只覆盖有限关键词。
                  </span>
                </p>
              ) : (
                <ul className="space-y-2.5">
                  {(detail.findings ?? []).map((f) => (
                    <li key={f.id} className="rounded-r2 border border-line p-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant={RISK_META[f.risk_level]?.tone ?? "neutral"} size="sm">
                          {RISK_META[f.risk_level]?.label ?? f.risk_level}
                        </Badge>
                        <span className="text-body-sm font-medium text-ink-800">{f.title}</span>
                        <span className="ml-auto text-caption text-ink-400">
                          {DIM_LABEL[f.dimension] ?? f.dimension}
                        </span>
                      </div>
                      {f.description && (
                        <p className="mt-1 text-caption text-ink-500">{f.description}</p>
                      )}
                      {f.suggestion && (
                        <p className="mt-1.5 text-body-sm text-ink-600">
                          <span className="text-ink-500">整改建议：</span>
                          {f.suggestion}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <p className="flex items-start gap-1.5 text-caption text-ink-400">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              本页为只读监控台，不提供发起扫描或修改结论的入口。
              （扫描任务无创建/完成时间字段对外暴露，故不显示时间。）
            </p>
          </div>
        )}
      </Modal>
    </div>
  );
}
