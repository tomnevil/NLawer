"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, ArrowLeft, Bot, CheckCircle2, FileText, LogOut, MessageSquarePlus, Moon, PanelRight, Scale, Search, Send, Sun, UserRound } from "lucide-react";
import { authed } from "@nlaw/sdk";
import {
  AppSwitcher,
  Badge,
  BottomSheet,
  EmptyState,
  PullToRefresh,
  Skeleton,
  Spinner,
  Timeline,
  buildAppLinks,
  cn,
  displayName,
  roleLabel,
  useAuthGuard,
  useTheme,
  type TimelineItem,
} from "@nlaw/ui";

import {
  CASE_STATUS,
  CONV_STATUS,
  GRADE_TONE,
  convSubtitle,
  convTitle,
  errText,
  fmtAmount,
  fmtDateTime,
  fmtTime,
  type CaseDetail,
  type CaseEvent,
  type ChecklistItem,
  type Conversation,
  type EvidenceItem,
  type Paged,
} from "../../../lib/imApi";

/* ============================================================================
 * 后端契约（读取源码确认，非猜测）
 * ----------------------------------------------------------------------------
 * GET  /api/v1/conversations?page=1&page_size=N   -> Page<ConversationOut>
 * POST /api/v1/conversations                      -> ConversationOut
 * GET  /api/v1/conversations/{id}                 -> ConversationDetail（含 messages）
 * POST /api/v1/conversations/{id}/messages        -> EngineReply
 * GET  /api/v1/cases/{id}                         -> CaseOut
 * GET  /api/v1/cases/{id}/events                  -> CaseEvent[]
 * GET  /api/v1/evidence/cases/{id}                -> EvidenceOut[]
 * GET  /api/v1/evidence/cases/{id}/missing        -> 材料清单（含 missing 标记）
 *
 * 注意 `Page` 的 `total` 在**顶层**而非 `meta` 下，且带 `total_is_lower_bound`。
 * ========================================================================== */

/** 四端是独立进程，切换必须整页跳转；地址走静态 env 访问才会被内联。 */
const APPS = buildAppLinks({
  web: process.env.NEXT_PUBLIC_APP_WEB_URL,
  lawyer: process.env.NEXT_PUBLIC_APP_LAWYER_URL,
  admin: process.env.NEXT_PUBLIC_APP_ADMIN_URL,
  im: process.env.NEXT_PUBLIC_APP_IM_URL,
});

/** 会话引擎产出的结构化卡片（`app/services/conversation_engine.py`）。 */
interface EngineCard {
  kind?: string;
  /* kind === "dispatch" */
  case_no?: string;
  grade?: string;
  mode?: string;
  lawyer_name?: string | null;
  dispute_type?: string;
  /* kind === "consult" */
  sections?: {
    conclusion?: string;
    legal_basis?: string;
    advice?: string;
    risk?: string;
  };
  citations?: { law_name: string; article_no: string; id: number }[];
  /* kind === "consult_report"（律师已确认的正式报告） */
  report_id?: number;
  review_id?: number;
  question?: string;
  signed_at?: string | null;
}

interface ChatMessage {
  id: number;
  conversation_id: number;
  sender: string;
  msg_type: string;
  content?: string | null;
  media_url?: string | null;
  card_payload?: EngineCard | null;
  citation_ids?: number[] | null;
}

interface EngineReply {
  reply: string;
  card?: EngineCard | null;
  status: string;
  dispatch?: { card?: EngineCard; mode?: string; case_id?: number; dispatch_id?: number } | null;
}

/* 案件 / 会话的**类型、状态文案、语义色**已移到 `lib/imApi.ts`。
 * 4 项 Tab 拆成 4 个路由后这些是共享的；各页各写一份，后端加一个状态时
 * 必然有一端渲染成空白。详见该文件顶部说明。 */

/** 引导性提问。是「发给 AI 的话」，不是数据断言，因此可以静态写死。 */
const QUICK_ASKS = ["需要准备哪些证据？", "这类案子大概要多久？", "律师费用怎么算？"];

/* 工具函数（errText / fmtTime / fmtDateTime / fmtAmount / convTitle / convSubtitle）
 * 同样在 `lib/imApi.ts` —— 工作台与我的案件页要用同一套格式化，
 * 两处各写一遍必然在时区或小数位上分叉。 */

/* ============================================================================
 * 页面
 * ========================================================================== */

export default function ImWorkspace() {
  const router = useRouter();
  const { user, logout } = useAuthGuard("/login");
  const { theme, toggle: toggleTheme } = useTheme();

  const [convs, setConvs] = useState<Conversation[]>([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [keyword, setKeyword] = useState("");
  const [creating, setCreating] = useState(false);

  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [msgsLoading, setMsgsLoading] = useState(false);
  const [msgsError, setMsgsError] = useState<string | null>(null);

  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);

  const [caseInfo, setCaseInfo] = useState<CaseDetail | null>(null);
  const [caseEvents, setCaseEvents] = useState<CaseEvent[]>([]);
  const [checklist, setChecklist] = useState<ChecklistItem[]>([]);
  const [evidence, setEvidence] = useState<EvidenceItem[]>([]);
  const [caseLoading, setCaseLoading] = useState(false);

  /** 移动端：列表与对话是两个整屏，靠这个状态切换 */
  const [mobileView, setMobileView] = useState<"list" | "chat">("list");
  /** 窄屏（< 1280px）下案件上下文收进底部抽屉 */
  const [ctxOpen, setCtxOpen] = useState(false);

  const streamRef = useRef<HTMLDivElement>(null);

  const activeConv = useMemo(
    () => convs.find((c) => c.id === activeId) ?? null,
    [convs, activeId]
  );
  const caseId = activeConv?.case_id ?? null;

  /**
   * 承办律师姓名。
   *
   * 后端没有「按 id 查用户」的公开端点，`CaseOut` 只给 `lawyer_id`。
   * 唯一可靠的姓名来源是派单卡片（引擎在派单时已把 `lawyer_name` 写进卡片）。
   * 因此这里只从历史消息里回溯姓名，拿不到就**如实显示律师编号**，
   * 不编造「王振宇」之类的占位姓名。
   */
  const lawyerName = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const card = messages[i].card_payload;
      if (card?.kind === "dispatch" && card.lawyer_name) return card.lawyer_name;
    }
    return null;
  }, [messages]);

  /* ------------------------------------------------------------ 会话列表 */

  const loadConversations = useCallback(async (preferId?: number) => {
    setListError(null);
    try {
      const page = await authed<Paged<Conversation>>("/api/v1/conversations?page=1&page_size=50");
      const items = page.items ?? [];
      setConvs(items);
      setActiveId((prev) => {
        const want = preferId ?? prev;
        if (want && items.some((c) => c.id === want)) return want;
        return items[0]?.id ?? null;
      });
    } catch (e) {
      setListError(errText(e));
    } finally {
      setListLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadConversations();
  }, [loadConversations]);

  /* -------------------------------------------------------------- 消息体 */

  useEffect(() => {
    if (activeId === null) {
      setMessages([]);
      return;
    }
    let alive = true;
    setMsgsLoading(true);
    setMsgsError(null);
    authed<{ messages?: ChatMessage[] }>(`/api/v1/conversations/${activeId}`)
      .then((d) => {
        if (alive) setMessages(d.messages ?? []);
      })
      .catch((e) => {
        if (alive) setMsgsError(errText(e));
      })
      .finally(() => {
        if (alive) setMsgsLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [activeId]);

  /* -------------------------------------------------------- 案件上下文 */

  useEffect(() => {
    if (caseId === null) {
      setCaseInfo(null);
      setCaseEvents([]);
      setChecklist([]);
      setEvidence([]);
      return;
    }
    let alive = true;
    setCaseLoading(true);
    // allSettled：材料清单为空或证据接口异常时，不应连带把案件概要与
    // 时间线一起吞掉——面板要能「部分可用」。
    Promise.allSettled([
      authed<CaseDetail>(`/api/v1/cases/${caseId}`),
      authed<CaseEvent[]>(`/api/v1/cases/${caseId}/events`),
      authed<ChecklistItem[]>(`/api/v1/evidence/cases/${caseId}/missing`),
      authed<EvidenceItem[]>(`/api/v1/evidence/cases/${caseId}`),
    ]).then(([c, ev, ck, ed]) => {
      if (!alive) return;
      setCaseInfo(c.status === "fulfilled" ? c.value : null);
      setCaseEvents(ev.status === "fulfilled" ? ev.value ?? [] : []);
      setChecklist(ck.status === "fulfilled" ? ck.value ?? [] : []);
      setEvidence(ed.status === "fulfilled" ? ed.value ?? [] : []);
      setCaseLoading(false);
    });
    return () => {
      alive = false;
    };
  }, [caseId]);

  /* ----------------------------------------------------------- 自动滚底 */

  useEffect(() => {
    const el = streamRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, sending, activeId]);

  /* --------------------------------------------------------------- 动作 */

  const openConversation = useCallback((id: number) => {
    setActiveId(id);
    setMobileView("chat");
  }, []);

  const createConversation = useCallback(async () => {
    if (creating) return;
    setCreating(true);
    setListError(null);
    try {
      const conv = await authed<Conversation>("/api/v1/conversations", {
        method: "POST",
        body: { external_user_id: user?.username ?? "web" },
      });
      setConvs((prev) => [conv, ...prev]);
      setActiveId(conv.id);
      setMessages([]);
      setMobileView("chat");
    } catch (e) {
      setListError(errText(e));
    } finally {
      setCreating(false);
    }
  }, [creating, user]);

  /**
   * 「向 AI 提问」从工作台跳过来时带 `#new`，落地即新建一个会话。
   *
   * 为什么用 hash 而不是 `useSearchParams`：后者在 App Router 里要求包一层
   * Suspense，否则预渲染阶段会报错；而这里要表达的是一次**动作**，
   * 不是一个可分享、可回退的状态 —— 用 hash 语义更准，读完立刻清掉。
   * 清 hash 用 `replaceState` 而不是 `router.replace`：后者会触发一次路由
   * 跳转并重新挂载本组件，把刚建好的会话状态丢掉。
   */
  useEffect(() => {
    if (typeof window === "undefined" || window.location.hash !== "#new") return;
    window.history.replaceState(null, "", "/chat");
    void createConversation();
  }, [createConversation]);

  const send = useCallback(
    async (textOverride?: string) => {
      const text = (textOverride ?? draft).trim();
      if (!text || sending) return;
      if (activeId === null) return;
      setDraft("");
      setSending(true);

      // 乐观插入用户气泡。`sender` 用后端枚举值，避免本地与服务端两条渲染路径。
      const localId = -Date.now();
      setMessages((m) => [
        ...m,
        {
          id: localId,
          conversation_id: activeId,
          sender: "CLIENT",
          msg_type: "text",
          content: text,
        },
      ]);

      try {
        const res = await authed<EngineReply>(`/api/v1/conversations/${activeId}/messages`, {
          method: "POST",
          body: { text },
        });
        setMessages((m) => [
          ...m,
          {
            id: localId - 1,
            conversation_id: activeId,
            sender: "AI",
            msg_type: "text",
            content: res.reply,
            card_payload: res.card ?? null,
          },
        ]);
        // 引擎可能在本轮把会话推进为「已派单」并绑定案件，因此列表与
        // 案件上下文都要重取。统一重取比按 `res.dispatch` 分支更不容易漏。
        void loadConversations(activeId);
      } catch (e) {
        setMessages((m) => [
          ...m,
          {
            id: localId - 2,
            conversation_id: activeId,
            sender: "SYSTEM",
            msg_type: "text",
            content: `发送失败：${errText(e)}`,
          },
        ]);
      } finally {
        setSending(false);
      }
    },
    [draft, sending, activeId, loadConversations]
  );

  /* -------------------------------------------------------------- 派生 */

  const groups = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    const match = (c: Conversation) =>
      !kw ||
      convTitle(c).toLowerCase().includes(kw) ||
      String(c.id).includes(kw) ||
      (c.context?.dispute_type ?? "").toLowerCase().includes(kw);
    const filtered = convs.filter(match);
    return [
      { key: "open", title: "进行中", items: filtered.filter((c) => c.status !== "CLOSED") },
      { key: "closed", title: "已结束", items: filtered.filter((c) => c.status === "CLOSED") },
    ].filter((g) => g.items.length > 0);
  }, [convs, keyword]);

  const timelineItems: TimelineItem[] = useMemo(
    () =>
      caseEvents.map((e, i) => ({
        id: String(e.id),
        title: e.title,
        description: e.description ?? undefined,
        time: fmtDateTime(e.occurred_at),
        status: i === caseEvents.length - 1 ? "current" : "done",
      })),
    [caseEvents]
  );

  const missingCount = useMemo(() => checklist.filter((c) => c.missing).length, [checklist]);

  const displayUserName = displayName(user) ?? "—";
  const displayUserRole = roleLabel(user?.role) ?? "";

  /* -------------------------------------------------------------- 渲染 */

  return (
    <div className="flex h-full">
      {/* ══════════════════════════ 左：会话列表 ══════════════════════════ */}
      <aside
        className={cn(
          "w-full shrink-0 flex-col border-r border-line bg-surface",
          "lg:flex lg:w-rail",
          mobileView === "list" ? "flex" : "hidden"
        )}
        aria-label="会话列表"
      >
        <header className="shrink-0 border-b border-line px-2 pb-2.5 pt-2">
          <AppSwitcher
            appId="im"
            apps={APPS}
            onChange={(app) => window.location.assign(app.href)}
          />
          <div className="mt-2 flex items-center gap-2">
            <div className="relative min-w-0 flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400" />
              <input
                type="search"
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                placeholder="搜索会话"
                aria-label="搜索会话"
                className={cn(
                  "h-9 w-full rounded-r2 border border-line bg-surface-subtle pl-8 pr-3 text-body-sm text-ink-800",
                  "placeholder:text-ink-400 transition-colors duration-fast",
                  "focus:border-brand-400 focus:bg-surface focus:outline-none focus:ring-2 focus:ring-brand-500/30"
                )}
              />
            </div>
            <button
              type="button"
              onClick={createConversation}
              disabled={creating}
              title="新建咨询"
              aria-label="新建咨询"
              className={cn(
                "tap-ghost flex h-9 w-9 shrink-0 items-center justify-center rounded-r2",
                "bg-solid-brand text-white transition-opacity duration-fast",
                "hover:opacity-90 disabled:opacity-50",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40"
              )}
            >
              {creating ? (
                <Spinner size="sm" label={null} />
              ) : (
                <MessageSquarePlus className="h-4 w-4" />
              )}
            </button>
          </div>
        </header>

        {/* 下拉刷新（§5.1 B.1 落点）：会话由**对方**产生，客户侧只有下拉才能主动取新消息。
            im 是 `h-dvh` 全高应用 ⇒ 本列有确定高度，`PullToRefresh` 的守卫
            （`el.scrollTop > 0 ⇒ 不接管`）由构造成立，不是「碰巧对」。
            原滚动容器的 `p-2` 下移到内层（`PullToRefresh` 的滚动层本身不带内边距）。
            ⚠️ **不给 `/me` 加**：静态设置页没有「新数据」，加了只会得到一个永远不变的手势。 */}
        <PullToRefresh onRefresh={() => loadConversations()} className="min-h-0 flex-1">
          <div className="p-2">
          {listLoading ? (
            <div className="space-y-2 p-1">
              {[0, 1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-16 w-full" />
              ))}
            </div>
          ) : listError ? (
            <div className="p-3">
              <p className="mb-2 flex items-start gap-1.5 text-body-sm text-danger-600">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                {listError}
              </p>
              <button
                type="button"
                onClick={() => void loadConversations()}
                className="text-body-sm text-link hover:text-link-hover"
              >
                重试
              </button>
            </div>
          ) : groups.length === 0 ? (
            <EmptyState
              icon={<Bot className="h-5 w-5" />}
              title={keyword ? "没有匹配的会话" : "还没有会话"}
              description={
                keyword ? "换个关键词试试。" : "描述您遇到的情况，AI 会先给出结构化分析。"
              }
              action={
                !keyword && (
                  <button
                    type="button"
                    onClick={createConversation}
                    className="rounded-r2 bg-solid-brand px-3 py-1.5 text-body-sm font-medium text-white"
                  >
                    开始新咨询
                  </button>
                )
              }
            />
          ) : (
            groups.map((g) => (
              <section key={g.key} className="mb-3 last:mb-0">
                <h2 className="px-2 pb-1 pt-2 text-caption uppercase tracking-wider text-ink-400">
                  {g.title}
                </h2>
                <ul>
                  {g.items.map((c) => {
                    const meta = CONV_STATUS[c.status] ?? {
                      label: c.status,
                      tone: "neutral" as const,
                    };
                    const active = c.id === activeId;
                    return (
                      <li key={c.id}>
                        <button
                          type="button"
                          onClick={() => openConversation(c.id)}
                          aria-current={active ? "true" : undefined}
                          className={cn(
                            "relative flex w-full items-start gap-2.5 rounded-r2 px-2 py-2.5 text-left",
                            "transition-colors duration-fast ease-out",
                            active ? "bg-brand-500/10" : "hover:bg-surface-hover"
                          )}
                        >
                          {/* 选中态左侧 3px 墨蓝指示条（与 AppShell 导航同一语言） */}
                          <span
                            aria-hidden
                            className={cn(
                              "absolute inset-y-1.5 left-0 w-[3px] rounded-full bg-brand-600 transition-opacity duration-fast",
                              active ? "opacity-100" : "opacity-0"
                            )}
                          />
                          <span
                            aria-hidden
                            className={cn(
                              "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-r2 text-caption font-semibold text-white",
                              c.status === "HUMAN" ? "bg-solid-info" : "bg-solid-ai"
                            )}
                          >
                            {c.status === "HUMAN" ? "律" : "AI"}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="flex items-baseline gap-2">
                              <span className="min-w-0 flex-1 truncate text-body-sm font-medium text-ink-900">
                                {convTitle(c)}
                              </span>
                              <span className="num shrink-0 text-caption text-ink-400">
                                {fmtTime(c.last_message_at)}
                              </span>
                            </span>
                            <span className="mt-0.5 block truncate text-caption text-ink-500">
                              {convSubtitle(c)}
                            </span>
                            <span className="mt-1 inline-flex">
                              <Badge variant={meta.tone} size="sm">
                                {meta.label}
                              </Badge>
                            </span>
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))
          )}
          </div>
        </PullToRefresh>

        <footer className="shrink-0 border-t border-line p-2">
          {/* ⚠️ `gap-4`（16px）不是随手挑的：两个按钮盒子都是 32px，
              而 `tap-ghost` 的 48px 热区会**各向外扩 8px** ⇒ 间距必须 ≥ 8 + 8 = 16px，
              否则两个热区重叠、DOM 靠后的那个会**抢走**前一个的重叠区。
              实测：`gap-2.5`（10px）时「主题」按钮热区只有 **42×49**（48 − 6 被抢）。
              规范 §8.3 的「相邻间距 ≥ 8px」只在**盒子本身 ≥ 40px** 时才与 48px 热区相容。
              见 `deliverables/ui-design/admin-gap-analysis.md` #18。 */}
          <div className="flex items-center gap-4 rounded-r2 px-2 py-1.5">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-500/15 text-body-sm font-semibold text-link">
              {displayUserName.charAt(0)}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-body-sm font-medium text-ink-900">
                {displayUserName}
              </span>
              {displayUserRole && (
                <span className="block truncate text-caption text-ink-500">{displayUserRole}</span>
              )}
            </span>
            <button
              type="button"
              onClick={toggleTheme}
              aria-label={theme === "dark" ? "切换到浅色模式" : "切换到深色模式"}
              className="tap-ghost flex h-8 w-8 shrink-0 items-center justify-center rounded-r2 text-ink-500 transition-colors duration-fast hover:bg-surface-hover hover:text-ink-900"
            >
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
            <button
              type="button"
              onClick={() => void logout().finally(() => router.replace("/login"))}
              aria-label="退出登录"
              className="tap-ghost flex h-8 w-8 shrink-0 items-center justify-center rounded-r2 text-ink-500 transition-colors duration-fast hover:bg-surface-hover hover:text-ink-900"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </footer>
      </aside>

      {/* ══════════════════════════ 中：对话 ══════════════════════════ */}
      <section
        className={cn(
          "min-w-0 flex-1 flex-col bg-ink-50",
          mobileView === "chat" ? "flex" : "hidden lg:flex"
        )}
        aria-label="对话"
      >
        <header className="flex h-topbar shrink-0 items-center gap-3 border-b border-line bg-surface px-3 lg:px-5">
          <button
            type="button"
            onClick={() => setMobileView("list")}
            aria-label="返回会话列表"
            className="tap-ghost flex h-9 w-9 shrink-0 items-center justify-center rounded-r2 text-ink-600 transition-colors duration-fast hover:bg-surface-hover lg:hidden"
          >
            <ArrowLeft className="h-5 w-5" />
          </button>

          <span
            aria-hidden
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-r2 bg-solid-ai text-white"
          >
            <Bot className="h-4 w-4" />
          </span>

          <div className="min-w-0 flex-1">
            <h1 className="truncate text-body font-semibold text-ink-900">
              {activeConv ? convTitle(activeConv) : "律小智 AI 助手"}
            </h1>
            <p className="flex items-center gap-1.5 truncate text-caption text-ink-500">
              {activeConv ? (
                <>
                  <span
                    aria-hidden
                    className={cn(
                      "h-1.5 w-1.5 shrink-0 rounded-full",
                      activeConv.status === "CLOSED" ? "bg-ink-400" : "bg-verified-500"
                    )}
                  />
                  {CONV_STATUS[activeConv.status]?.label ?? activeConv.status}
                  {activeConv.case_id && <span className="num">· 案件 #{activeConv.case_id}</span>}
                </>
              ) : (
                "选择左侧会话，或开始新咨询"
              )}
            </p>
          </div>

          {activeConv && (
            <button
              type="button"
              onClick={() => setCtxOpen(true)}
              className={cn(
                "flex h-8 shrink-0 items-center gap-1.5 rounded-r2 border border-line px-2.5",
                "text-body-sm text-ink-600 transition-colors duration-fast hover:bg-surface-hover",
                "xl:hidden"
              )}
            >
              <PanelRight className="h-4 w-4" />
              案件
            </button>
          )}
        </header>

        <div ref={streamRef} className="scroll-thin min-h-0 flex-1 overflow-y-auto px-4 py-5 lg:px-6">
          {msgsLoading && messages.length === 0 ? (
            <div className="mx-auto max-w-2xl space-y-3">
              <Skeleton className="h-16 w-3/4" />
              <Skeleton className="ml-auto h-12 w-1/2" />
              <Skeleton className="h-24 w-4/5" />
            </div>
          ) : msgsError ? (
            <div className="mx-auto max-w-2xl">
              <p className="flex items-start gap-2 rounded-r3 border border-danger-500/30 bg-danger-500/10 p-3 text-body-sm text-danger-600">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                {msgsError}
              </p>
            </div>
          ) : messages.length === 0 ? (
            <div className="mx-auto max-w-2xl">
              <div className="rounded-r3 border border-line bg-surface p-5 shadow-s1">
                <p className="text-body text-ink-700">
                  您好，我是律小智 AI 法律助手。请描述您遇到的情况，我会给出结构化的法律分析；
                  如涉及复杂纠纷，可一键委托执业律师。
                </p>
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                {QUICK_ASKS.map((q) => (
                  <button
                    key={q}
                    type="button"
                    onClick={() => void send(q)}
                    className={cn(
                      "rounded-full border border-brand-200 bg-brand-50 px-3.5 py-1.5 text-body-sm font-medium text-link",
                      "transition-colors duration-fast hover:bg-brand-600 hover:text-white"
                    )}
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="mx-auto max-w-2xl space-y-4">
              {messages.map((m) => (
                <MessageRow key={m.id} msg={m} />
              ))}
              {sending && (
                <div className="flex items-end gap-2">
                  <span
                    aria-hidden
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-r2 bg-solid-ai text-white"
                  >
                    <Bot className="h-3.5 w-3.5" />
                  </span>
                  <div className="rounded-r3 rounded-bl-r1 border border-line bg-surface px-4 py-3">
                    <span className="flex items-center gap-1" aria-label="AI 正在生成回复">
                      <i className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-ink-300" />
                      <i
                        className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-ink-300"
                        style={{ animationDelay: "0.2s" }}
                      />
                      <i
                        className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-ink-300"
                        style={{ animationDelay: "0.4s" }}
                      />
                    </span>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="shrink-0 border-t border-line bg-surface px-3 py-3 lg:px-6">
          <div className="mx-auto max-w-2xl">
            <div className="flex items-end gap-2 rounded-r4 border border-line bg-surface-subtle p-2 transition-colors duration-fast focus-within:border-brand-400 focus-within:bg-surface focus-within:ring-2 focus-within:ring-brand-500/25">
              <label htmlFor="im-composer" className="sr-only">
                输入消息
              </label>
              <textarea
                id="im-composer"
                rows={1}
                value={draft}
                disabled={activeId === null}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send();
                  }
                }}
                placeholder={
                  activeId === null
                    ? "请先选择或新建一个会话"
                    : "描述您的情况，Enter 发送，Shift+Enter 换行…"
                }
                className={cn(
                  "scroll-thin max-h-40 min-h-9 flex-1 resize-none bg-transparent px-2 py-1.5",
                  "text-body-sm text-ink-900 outline-none placeholder:text-ink-400 disabled:cursor-not-allowed"
                )}
              />
              <button
                type="button"
                onClick={() => void send()}
                disabled={sending || !draft.trim() || activeId === null}
                className={cn(
                  "flex h-9 shrink-0 items-center gap-1.5 rounded-r2 px-3.5 text-body-sm font-semibold text-white",
                  "bg-solid-brand transition-opacity duration-fast hover:opacity-90",
                  "disabled:cursor-not-allowed disabled:opacity-40",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40"
                )}
              >
                <Send className="h-4 w-4" />
                发送
              </button>
            </div>
            <p className="mt-2 text-center text-caption text-ink-400">
              AI 生成内容仅供参考，不构成正式法律意见。涉及重大权益请委托执业律师。
            </p>
          </div>
        </div>
      </section>

      {/* ══════════════════ 右：案件上下文（≥1280 常驻） ══════════════════ */}
      <aside
        className="hidden w-panel shrink-0 flex-col border-l border-line bg-surface xl:flex"
        aria-label="案件上下文"
      >
        <CaseContext
          conv={activeConv}
          caseInfo={caseInfo}
          timelineItems={timelineItems}
          checklist={checklist}
          missingCount={missingCount}
          evidence={evidence}
          lawyerName={lawyerName}
          loading={caseLoading}
        />
      </aside>

      {/* 窄屏：同一份上下文收进底部抽屉 */}
      <BottomSheet
        isOpen={ctxOpen}
        onClose={() => setCtxOpen(false)}
        title="案件上下文"
        heightRatio={0.8}
      >
        <CaseContext
          conv={activeConv}
          caseInfo={caseInfo}
          timelineItems={timelineItems}
          checklist={checklist}
          missingCount={missingCount}
          evidence={evidence}
          lawyerName={lawyerName}
          loading={caseLoading}
          embedded
        />
      </BottomSheet>
    </div>
  );
}

/* ============================================================================
 * 子组件
 * ========================================================================== */

/**
 * 单条消息。
 *
 * 责任边界：AI 产出的内容一律带 `AI` 标记与紫色标识，不用「看起来像定稿」的
 * 中性样式——这是本产品的底线（规范第 06 节），客户必须一眼看出哪段不是
 * 律师写的。
 */
function MessageRow({ msg }: { msg: ChatMessage }) {
  const isMine = msg.sender === "CLIENT";
  const isSystem = msg.sender === "SYSTEM";
  const card = msg.card_payload ?? null;

  if (isSystem) {
    return (
      <div className="flex justify-center">
        <p className="rounded-full bg-ink-100 px-3 py-1 text-caption text-ink-600">{msg.content}</p>
      </div>
    );
  }

  return (
    <div className={cn("flex items-end gap-2", isMine && "flex-row-reverse")}>
      <span
        aria-hidden
        className={cn(
          "flex h-7 w-7 shrink-0 items-center justify-center rounded-r2",
          isMine ? "bg-ink-200 text-ink-600" : "bg-solid-ai text-white"
        )}
      >
        {isMine ? <UserRound className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
      </span>

      <div className={cn("min-w-0 max-w-[85%]", isMine && "flex flex-col items-end")}>
        <div
          className={cn(
            "whitespace-pre-wrap rounded-r3 px-3.5 py-2.5 text-body-sm",
            isMine
              ? "rounded-br-r1 bg-solid-brand text-white"
              : "rounded-bl-r1 border border-line bg-surface text-ink-700"
          )}
        >
          {msg.content}
        </div>

        {!isMine && card && <CardBlock card={card} />}
      </div>
    </div>
  );
}

/** 会话引擎的结构化卡片：派单卡 / 咨询四段式卡。 */
function CardBlock({ card }: { card: EngineCard }) {
  if (card.kind === "dispatch") {
    return (
      <article className="mt-2 w-full overflow-hidden rounded-r3 border border-line bg-surface shadow-s1">
        <header className="flex items-center gap-2 border-b border-line bg-surface-subtle px-3 py-2">
          <Scale className="h-3.5 w-3.5 shrink-0 text-pending-500" />
          <span className="flex-1 text-body-sm font-medium text-ink-800">案件评估结果</span>
          <Badge variant="pending" size="sm">
            已派单
          </Badge>
        </header>
        <div className="p-3">
          <div className="flex items-start gap-3">
            <span
              aria-hidden
              className={cn(
                "flex h-9 w-9 shrink-0 items-center justify-center rounded-r2 text-h4 font-semibold text-white",
                card.grade === "S" || card.grade === "A" ? "bg-solid-danger" : "bg-solid-pending"
              )}
            >
              {card.grade ?? "?"}
            </span>
            <div className="min-w-0">
              <p className="text-body-sm font-medium text-ink-900">
                {card.dispute_type ?? "案件"} · 案件等级 {card.grade ?? "—"}
              </p>
              <p className="mt-0.5 text-caption text-ink-500">
                {card.lawyer_name
                  ? `已指派 ${card.lawyer_name} 律师`
                  : "已进入派单池，等待律师接单"}
              </p>
            </div>
          </div>

          <dl className="mt-3 grid grid-cols-2 gap-2 border-t border-line pt-3">
            <div>
              <dt className="text-caption text-ink-400">案件编号</dt>
              <dd className="num text-body-sm text-ink-800">{card.case_no ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-caption text-ink-400">派单方式</dt>
              <dd className="text-body-sm text-ink-800">
                {card.mode === "DESIGNATED"
                  ? "指定律师"
                  : card.mode === "POOL"
                    ? "律师抢单"
                    : "系统派单"}
              </dd>
            </div>
          </dl>
        </div>
      </article>
    );
  }

  if (card.kind === "consult" && card.sections) {
    const { conclusion, legal_basis: legal, advice, risk } = card.sections;
    return (
      <article className="mt-2 w-full overflow-hidden rounded-r3 border border-ai-500/30 bg-ai-500/[0.06]">
        <header className="flex items-center gap-2 border-b border-ai-500/20 px-3 py-2">
          <Badge variant="ai" size="sm">
            AI 生成
          </Badge>
          <span className="text-caption text-ink-500">结构化分析 · 未经律师确认</span>
        </header>
        <div className="space-y-3 p-3">
          {conclusion && <Section title="结论" body={conclusion} />}
          {legal && <Section title="法律依据" body={legal} mono />}
          {advice && <Section title="行动建议" body={advice} />}
          {risk && <Section title="风险提示" body={risk} />}
          {card.citations && card.citations.length > 0 && (
            <div className="border-t border-ai-500/20 pt-2.5">
              <p className="mb-1.5 text-caption text-ink-500">引用依据（{card.citations.length}）</p>
              <ul className="flex flex-wrap gap-1.5">
                {card.citations.map((c) => (
                  <li key={c.id}>
                    <Badge variant="neutral" size="sm">
                      《{c.law_name}》{c.article_no}
                    </Badge>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </article>
    );
  }

  /* 律师已确认的正式咨询报告：带署名 + 时间戳，与客户可见的 AI 草稿在视觉上
     明确区分（绿/verified 色系 + 「律师已确认」徽标），呼应责任边界规范。 */
  if (card.kind === "consult_report" && card.sections) {
    const { conclusion, legal_basis: legal, advice, risk } = card.sections;
    return (
      <article className="mt-2 w-full overflow-hidden rounded-r3 border border-verified-500/40 bg-verified-500/[0.06]">
        <header className="flex items-center gap-2 border-b border-verified-500/30 px-3 py-2">
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-verified-500" />
          <Badge variant="verified" size="sm">
            律师已确认
          </Badge>
          <span className="text-caption text-ink-500">正式咨询报告</span>
          {card.lawyer_name && (
            <span className="ml-auto truncate text-caption text-ink-500">执业律师 {card.lawyer_name}</span>
          )}
        </header>
        <div className="space-y-3 p-3">
          {conclusion && <Section title="结论" body={conclusion} />}
          {legal && <Section title="法律依据" body={legal} mono />}
          {advice && <Section title="行动建议" body={advice} />}
          {risk && <Section title="风险提示" body={risk} />}
          {card.citations && card.citations.length > 0 && (
            <div className="border-t border-verified-500/30 pt-2.5">
              <p className="mb-1.5 text-caption text-ink-500">引用依据（{card.citations.length}）</p>
              <ul className="flex flex-wrap gap-1.5">
                {card.citations.map((c) => (
                  <li key={c.id}>
                    <Badge variant="neutral" size="sm">
                      《{c.law_name}》{c.article_no}
                    </Badge>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {card.signed_at && (
            <p className="border-t border-verified-500/30 pt-2 text-caption text-ink-400">
              由执业律师 {card.lawyer_name ?? "本所律师"} 于 {card.signed_at} 确认出具
            </p>
          )}
        </div>
      </article>
    );
  }

  return null;
}

function Section({ title, body, mono }: { title: string; body: string; mono?: boolean }) {
  return (
    <div>
      <p className="text-caption font-medium text-ink-500">{title}</p>
      <p
        className={cn(
          "mt-0.5 whitespace-pre-wrap text-body-sm text-ink-700",
          // 法条原文用衬线体 + 1.85 行高，与正文拉开质感差异（规范第 03 节）
          mono && "font-serif leading-[1.85]"
        )}
      >
        {body}
      </p>
    </div>
  );
}

/**
 * 案件上下文面板（桌面右栏与移动端抽屉共用同一份实现）。
 *
 * 【刻意不做的部分】概念图 06 的右栏还有「费用预估」（代理费 / 受理费 / 风险代理）。
 * 后端没有任何费用估算端点，`CaseOut` 也没有费用字段——写死一组金额等于对客户
 * 承诺一个平台无法背书的数字。法律产品的报价失实比缺一块信息严重得多，
 * 因此这里整块省略，而不是填占位值。
 */
function CaseContext({
  conv,
  caseInfo,
  timelineItems,
  checklist,
  missingCount,
  evidence,
  lawyerName,
  loading,
  embedded = false,
}: {
  conv: Conversation | null;
  caseInfo: CaseDetail | null;
  timelineItems: TimelineItem[];
  checklist: ChecklistItem[];
  missingCount: number;
  evidence: EvidenceItem[];
  lawyerName: string | null;
  loading: boolean;
  embedded?: boolean;
}) {
  const body = (
    <>
      {!conv ? (
        <EmptyState
          icon={<FileText className="h-5 w-5" />}
          title="未选择会话"
          description="选择左侧会话后，这里会显示关联案件的进度与材料。"
        />
      ) : conv.case_id === null || conv.case_id === undefined ? (
        <EmptyState
          icon={<FileText className="h-5 w-5" />}
          title="尚未生成案件"
          description="当您描述的情况涉及复杂纠纷（需要律师介入）时，AI 会自动立案并派单，届时这里会显示案件进度。"
        />
      ) : loading && !caseInfo ? (
        <div className="space-y-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      ) : (
        <div className="space-y-5">
          {/* ── 案件概要 ── */}
          {caseInfo && (
            <section>
              <h3 className="mb-2 text-caption uppercase tracking-wider text-ink-400">案件概要</h3>
              <div className="rounded-r3 border border-line bg-surface-subtle p-3">
                <p className="num text-body-sm font-medium text-ink-900">{caseInfo.case_no}</p>
                <p className="mt-0.5 text-caption text-ink-500">{caseInfo.title}</p>
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <Badge variant={GRADE_TONE[caseInfo.grade] ?? "neutral"} size="sm">
                    {caseInfo.grade} 级
                  </Badge>
                  <Badge variant="neutral" size="sm">
                    {CASE_STATUS[caseInfo.status] ?? caseInfo.status}
                  </Badge>
                  {caseInfo.dispute_type && (
                    <Badge variant="neutral" size="sm">
                      {caseInfo.dispute_type}
                    </Badge>
                  )}
                </div>
                <dl className="mt-3 space-y-1.5 border-t border-line pt-2.5">
                  <div className="flex justify-between gap-2">
                    <dt className="text-caption text-ink-500">争议标的</dt>
                    <dd className="num text-body-sm text-ink-800">
                      {fmtAmount(caseInfo.claim_amount)}
                    </dd>
                  </div>
                  {caseInfo.focus && (
                    <div className="flex justify-between gap-2">
                      <dt className="shrink-0 text-caption text-ink-500">争议焦点</dt>
                      <dd className="text-right text-body-sm text-ink-800">{caseInfo.focus}</dd>
                    </div>
                  )}
                </dl>
              </div>
            </section>
          )}

          {/* ── 承办律师 ── */}
          <section>
            <h3 className="mb-2 text-caption uppercase tracking-wider text-ink-400">承办律师</h3>
            {caseInfo?.lawyer_id ? (
              <div className="flex items-center gap-3 rounded-r3 border border-line bg-surface-subtle p-3">
                <span
                  aria-hidden
                  className="flex h-10 w-10 shrink-0 items-center justify-center rounded-r2 bg-solid-info text-body font-semibold text-white"
                >
                  {lawyerName ? lawyerName.charAt(0) : "律"}
                </span>
                <div className="min-w-0">
                  {/* 拿不到姓名时如实显示编号，不编造姓名 */}
                  <p className="truncate text-body-sm font-medium text-ink-900">
                    {lawyerName ?? `律师编号 #${caseInfo.lawyer_id}`}
                  </p>
                  <p className="mt-0.5 text-caption text-ink-500">承办律师 · 可通过本会话沟通</p>
                </div>
              </div>
            ) : (
              <p className="rounded-r3 border border-pending-500/30 bg-pending-500/10 p-3 text-body-sm text-pending-600">
                尚未有律师接单。派单池已推送，接单后此处会显示承办律师。
              </p>
            )}
          </section>

          {/* ── 案件进度 ── */}
          <section>
            <h3 className="mb-2 text-caption uppercase tracking-wider text-ink-400">案件进度</h3>
            {timelineItems.length > 0 ? (
              <Timeline items={timelineItems} />
            ) : (
              <p className="rounded-r3 border border-line bg-surface-subtle p-3 text-body-sm text-ink-500">
                暂无流转记录。
              </p>
            )}
          </section>

          {/* ── 材料清单 ── */}
          <section>
            <div className="mb-2 flex items-baseline justify-between gap-2">
              <h3 className="text-caption uppercase tracking-wider text-ink-400">材料清单</h3>
              {checklist.length > 0 && (
                <span className="num text-caption text-ink-500">
                  缺 {missingCount} / 共 {checklist.length}
                </span>
              )}
            </div>

            {checklist.length === 0 && evidence.length === 0 ? (
              <p className="rounded-r3 border border-line bg-surface-subtle p-3 text-body-sm text-ink-500">
                暂无可核对的材料清单。
              </p>
            ) : (
              <ul className="space-y-1.5">
                {checklist.map((c) => (
                  <li key={`${c.category}-${c.item}`} className="flex items-start gap-2">
                    {c.missing ? (
                      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger-500" />
                    ) : (
                      <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-verified-500" />
                    )}
                    <span className="min-w-0 flex-1">
                      <span
                        className={cn(
                          "block text-body-sm",
                          c.missing ? "text-danger-600" : "text-ink-700"
                        )}
                      >
                        {c.missing ? `缺：${c.item}` : c.item}
                      </span>
                      {c.description && (
                        <span className="mt-0.5 block text-caption text-ink-400">
                          {c.description}
                        </span>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            )}

            {evidence.length > 0 && (
              <div className="mt-3 border-t border-line pt-3">
                <p className="mb-1.5 text-caption text-ink-500">已上传材料（{evidence.length}）</p>
                <ul className="space-y-1">
                  {evidence.map((e) => (
                    <li key={e.id} className="flex items-center gap-2">
                      <FileText className="h-3.5 w-3.5 shrink-0 text-ink-400" />
                      <span className="min-w-0 flex-1 truncate text-body-sm text-ink-700">
                        {e.name}
                      </span>
                      <span className="shrink-0 text-caption text-ink-400">{e.category}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        </div>
      )}
    </>
  );

  if (embedded) return body;

  return (
    <>
      <header className="flex h-topbar shrink-0 items-center border-b border-line px-4">
        <h2 className="text-body font-semibold text-ink-900">案件上下文</h2>
        {conv?.case_id && <span className="num ml-2 text-caption text-ink-500">#{conv.case_id}</span>}
      </header>
      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto p-4">{body}</div>
    </>
  );
}
