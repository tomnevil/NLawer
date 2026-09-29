"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Database, Lock, Plus, RefreshCw, Trash2 } from "lucide-react";
import { ApiError, authed } from "@nlaw/sdk";
import {
  Badge,
  Button,
  EmptyState,
  Modal,
  Pagination,
  Skeleton,
  Spinner,
  cn,
  useToast,
  type BadgeProps,
} from "@nlaw/ui";

/* ============================================================================
 * 企业知识库
 * ----------------------------------------------------------------------------
 * GET    /api/v1/knowledge/docs?page&page_size&doc_type&keyword -> Page<KnowledgeDocOut>
 * POST   /api/v1/knowledge/docs  {title, doc_type, content}     -> 新增
 * DELETE /api/v1/knowledge/docs/{id}                            -> 删除
 *
 * ⚠️ 本轮修复：`POST /knowledge/docs` 在旧实现里用 `authed(path, { body })` 调用，
 *     method 默认 `GET`，浏览器对「GET + body」直接抛 `TypeError`——
 *     「保存」按钮点了没有任何反应，文档从未被创建。
 *
 * ⚠️ 另修两处：旧实现无分页（`page_size=50` 写死，超过 50 份的文档不可达）；
 *     `doc_type` 直接把 `POLICY` / `QA_HISTORY` 这样的裸值渲染给用户。
 *     删除是不可逆操作，旧实现点一下图标即删，现改为二次确认弹窗。
 * ========================================================================== */

interface KnowledgeDoc {
  id: number;
  title: string;
  doc_type: string;
  content?: string | null;
  source_ref?: string | null;
  tags?: string | null;
  chunk_count: number;
  reviewed_by_lawyer: boolean;
}

interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_is_lower_bound?: boolean;
}

/** `doc_type` 是自由字符串，取值见 `app/models/knowledge.py` 的注释。 */
const TYPE_LABEL: Record<string, string> = {
  CONTRACT: "合同库",
  POLICY: "制度库",
  QA_HISTORY: "历史咨询",
  PREFERENCE: "偏好设置",
  REGULATION: "法规订阅",
};

const TYPE_OPTIONS = Object.keys(TYPE_LABEL);

const TYPE_TONE: Record<string, BadgeProps["variant"]> = {
  CONTRACT: "info",
  POLICY: "primary",
  QA_HISTORY: "neutral",
  PREFERENCE: "neutral",
  REGULATION: "gold",
};

function errText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "请求失败";
}

const FIELD_CLS = cn(
  "w-full rounded-r2 border border-line bg-surface px-3 py-2 text-body-sm text-ink-800",
  "placeholder:text-ink-400",
  "transition-colors duration-fast focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-500/30"
);

export default function KnowledgePage() {
  const { addToast } = useToast();

  const [docs, setDocs] = useState<KnowledgeDoc[]>([]);
  const [total, setTotal] = useState(0);
  const [isLowerBound, setIsLowerBound] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [typeFilter, setTypeFilter] = useState("");
  const [keyword, setKeyword] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [title, setTitle] = useState("");
  const [docType, setDocType] = useState("POLICY");
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);

  const [pendingDelete, setPendingDelete] = useState<KnowledgeDoc | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const qs = new URLSearchParams({
        page: String(page),
        page_size: String(pageSize),
      });
      if (typeFilter) qs.set("doc_type", typeFilter);
      if (keyword.trim()) qs.set("keyword", keyword.trim());

      const p = await authed<Paged<KnowledgeDoc>>(`/api/v1/knowledge/docs?${qs.toString()}`);
      setDocs(p.items ?? []);
      setTotal(p.total ?? 0);
      setIsLowerBound(Boolean(p.total_is_lower_bound));
    } catch (e) {
      setError(errText(e));
      setDocs([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, typeFilter, keyword]);

  useEffect(() => {
    void load();
  }, [load]);

  const add = useCallback(async () => {
    if (!title.trim()) {
      addToast({ type: "warning", title: "请填写文档标题" });
      return;
    }
    if (!content.trim()) {
      addToast({ type: "warning", title: "请填写文档内容" });
      return;
    }
    setSaving(true);
    try {
      await authed("/api/v1/knowledge/docs", {
        method: "POST",
        body: { title: title.trim(), doc_type: docType, content },
      });
      setTitle("");
      setContent("");
      setShowForm(false);
      addToast({ type: "success", title: "文档已入库", message: "已切块并加入检索索引" });
      setPage(1);
      await load();
    } catch (e) {
      addToast({ type: "error", title: "保存失败", message: errText(e) });
    } finally {
      setSaving(false);
    }
  }, [title, docType, content, addToast, load]);

  const confirmDelete = useCallback(async () => {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      await authed(`/api/v1/knowledge/docs/${pendingDelete.id}`, { method: "DELETE" });
      addToast({ type: "success", title: "文档已删除" });
      setPendingDelete(null);
      await load();
    } catch (e) {
      addToast({ type: "error", title: "删除失败", message: errText(e) });
    } finally {
      setDeleting(false);
    }
  }, [pendingDelete, addToast, load]);

  const countText = isLowerBound ? `${total}+ 份` : `${total} 份`;

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-h2 text-ink-900">
            <Database className="h-5 w-5 text-ink-400" aria-hidden />
            企业知识库
            <Badge variant="neutral" size="sm">
              <Lock className="mr-1 inline h-3 w-3" aria-hidden />
              租户隔离
            </Badge>
          </h1>
          <p className="mt-1 text-body-sm text-ink-500">
            合同库 / 制度库 / 历史咨询 / 偏好设置，仅本企业可见 · 入库后自动切块参与检索
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            className={cn(
              "flex h-9 items-center gap-1.5 rounded-r2 border border-line px-3",
              "text-body-sm text-ink-600 transition-colors duration-fast hover:bg-surface-hover",
              "disabled:opacity-50"
            )}
          >
            {loading ? <Spinner size="sm" label={null} /> : <RefreshCw className="h-4 w-4" />}
            刷新
          </button>
          <Button
            variant={showForm ? "outline" : "primary"}
            leftIcon={<Plus className="h-3.5 w-3.5" />}
            onClick={() => setShowForm((v) => !v)}
          >
            {showForm ? "收起" : "新增文档"}
          </Button>
        </div>
      </header>

      {/* ── 新增表单 ───────────────────────────────────────────── */}
      {showForm && (
        <section className="rounded-r3 border border-line bg-surface p-5">
          <h2 className="text-body-sm font-semibold text-ink-800">新增知识文档</h2>
          <div className="mt-3 space-y-3">
            <div>
              <label htmlFor="kb-title" className="mb-1 block text-body-sm text-ink-700">
                文档标题
              </label>
              <input
                id="kb-title"
                className={FIELD_CLS}
                placeholder="如：员工手册（2026 版）"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </div>
            <div>
              <label htmlFor="kb-type" className="mb-1 block text-body-sm text-ink-700">
                内容类型
              </label>
              <select
                id="kb-type"
                className={FIELD_CLS}
                value={docType}
                onChange={(e) => setDocType(e.target.value)}
              >
                {TYPE_OPTIONS.map((t) => (
                  <option key={t} value={t}>
                    {TYPE_LABEL[t]}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="kb-content" className="mb-1 block text-body-sm text-ink-700">
                文档内容
              </label>
              <textarea
                id="kb-content"
                className={cn(FIELD_CLS, "h-32 resize-y")}
                placeholder="粘贴制度、合同范本或历史咨询全文。"
                value={content}
                onChange={(e) => setContent(e.target.value)}
              />
              <p className="mt-1 text-caption text-ink-500">
                内容会被切块并向量化，供问答检索引用。请勿粘贴含个人敏感信息的材料。
              </p>
            </div>
          </div>
          <div className="mt-4 flex gap-2">
            <Button variant="primary" isLoading={saving} onClick={() => void add()}>
              保存
            </Button>
            <Button variant="ghost" onClick={() => setShowForm(false)}>
              取消
            </Button>
          </div>
        </section>
      )}

      {/* ── 筛选 ───────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-2">
        <label className="sr-only" htmlFor="kb-type-filter">
          按内容类型筛选
        </label>
        <select
          id="kb-type-filter"
          value={typeFilter}
          onChange={(e) => {
            setTypeFilter(e.target.value);
            setPage(1);
          }}
          className={cn(FIELD_CLS, "h-9 w-auto py-0")}
        >
          <option value="">全部类型</option>
          {TYPE_OPTIONS.map((t) => (
            <option key={t} value={t}>
              {TYPE_LABEL[t]}
            </option>
          ))}
        </select>

        <label className="sr-only" htmlFor="kb-keyword">
          按关键词搜索
        </label>
        <input
          id="kb-keyword"
          type="search"
          value={keyword}
          onChange={(e) => {
            setKeyword(e.target.value);
            setPage(1);
          }}
          placeholder="搜索标题或内容…"
          className={cn(FIELD_CLS, "h-9 w-52 py-0")}
        />

        <span className="num ml-auto text-body-sm text-ink-500">共 {countText}</span>
      </div>

      {/* ── 列表 ───────────────────────────────────────────────── */}
      {error ? (
        <div className="rounded-r3 border border-danger-500/30 bg-danger-500/10 p-4">
          <p className="flex items-start gap-2 text-body-sm text-danger-600">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            {error}
          </p>
          <button
            type="button"
            onClick={() => void load()}
            className="mt-2 text-body-sm text-link hover:text-link-hover"
          >
            重试
          </button>
        </div>
      ) : loading && docs.length === 0 ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : docs.length === 0 ? (
        <EmptyState
          icon={<Database className="h-5 w-5" />}
          title={keyword || typeFilter ? "没有符合条件的文档" : "知识库还是空的"}
          description={
            keyword || typeFilter
              ? "试试撤下筛选条件。"
              : "把制度、合同范本、历史咨询导入进来，问答与文书生成会引用这些内容。"
          }
          action={
            keyword || typeFilter ? (
              <button
                type="button"
                onClick={() => {
                  setKeyword("");
                  setTypeFilter("");
                  setPage(1);
                }}
                className="rounded-r2 border border-line px-3 py-1.5 text-body-sm text-ink-700"
              >
                清空筛选
              </button>
            ) : (
              <Button variant="primary" onClick={() => setShowForm(true)}>
                新增文档
              </Button>
            )
          }
        />
      ) : (
        <>
          <ul className="overflow-hidden rounded-r3 border border-line bg-surface">
            {docs.map((d) => (
              <li
                key={d.id}
                className="flex items-start gap-3 border-b border-line px-4 py-3 last:border-b-0"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-body-sm font-medium text-ink-900">{d.title}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    <Badge variant={TYPE_TONE[d.doc_type] ?? "neutral"} size="sm">
                      {TYPE_LABEL[d.doc_type] ?? d.doc_type}
                    </Badge>
                    <span className="num text-caption text-ink-500">
                      切块 {d.chunk_count}
                    </span>
                    {d.reviewed_by_lawyer ? (
                      <Badge variant="verified" size="sm">律师已审</Badge>
                    ) : (
                      <Badge variant="pending" size="sm">待律师审</Badge>
                    )}
                    {d.tags && (
                      <span className="truncate text-caption text-ink-400">{d.tags}</span>
                    )}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setPendingDelete(d)}
                  aria-label={`删除《${d.title}》`}
                  className={cn(
                    "tap-ghost flex h-9 w-9 shrink-0 items-center justify-center rounded-r2",
                    "text-ink-400 transition-colors duration-fast",
                    "hover:bg-danger-500/10 hover:text-danger-600",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger-500/30"
                  )}
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </li>
            ))}
          </ul>

          <div className="flex justify-end">
            <Pagination
              page={page}
              pageSize={pageSize}
              total={total}
              onPageChange={setPage}
              onPageSizeChange={(n) => {
                setPageSize(n);
                setPage(1);
              }}
            />
          </div>
        </>
      )}

      {/* ── 删除二次确认 ───────────────────────────────────────── */}
      <Modal
        isOpen={pendingDelete !== null}
        onClose={() => setPendingDelete(null)}
        title="删除知识文档"
        description="删除后该文档的切块会一并从检索索引中移除，且不可恢复。"
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setPendingDelete(null)}>
              取消
            </Button>
            <Button variant="danger" isLoading={deleting} onClick={() => void confirmDelete()}>
              确认删除
            </Button>
          </>
        }
      >
        <p className="text-body-sm text-ink-700">
          即将删除：
          <span className="font-medium text-ink-900">{pendingDelete?.title}</span>
          <span className="num ml-1.5 text-caption text-ink-500">
            （{pendingDelete?.chunk_count ?? 0} 个切块）
          </span>
        </p>
      </Modal>
    </div>
  );
}
