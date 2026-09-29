"use client";

import React from "react";
import Link from "next/link";
import {
  AppShell,
  type AppShellNavGroup,
  type AppShellApp,
  Button,
  Badge,
  KpiCard,
  Alert,
  ProvenanceBadge,
  ProvenanceBlock,
  ProvenanceLegend,
  CitationChip,
  CitationPanel,
  type Citation,
  SegmentedControl,
  Pagination,
  FilterBar,
  type FilterChip,
  Timeline,
  type TimelineItem,
  Drawer,
  DataTable,
  type DataTableColumn,
  BottomSheet,
  CollapseGroup,
  CollapsePanel,
  PullToRefresh,
  InfiniteList,
  CameraCapture,
  type CapturedFile,
  OfflineBanner,
  SyncQueueProvider,
  SyncQueueBadge,
  SyncQueueSheet,
  useTheme,
} from "@nlaw/ui";

/* ────────────────────────────── 演示数据 ────────────────────────────── */

const APPS: AppShellApp[] = [
  { id: "web", label: "法务助手", href: "/", description: "个人与企业法务" },
  { id: "lawyer", label: "律所工作台", href: "/", description: "案件协作与办案" },
  { id: "admin", label: "运营后台", href: "/", description: "平台治理" },
  { id: "im", label: "即时沟通", href: "/", description: "律师与客户会话" },
];

const NAV: AppShellNavGroup[] = [
  {
    title: "阶段一 · 设计基建",
    items: [
      { id: "tokens", label: "设计令牌" },
      { id: "shell", label: "统一骨架" },
    ],
  },
  {
    title: "阶段二 · 组件层",
    items: [
      { id: "provenance", label: "责任边界三态", badge: 3 },
      { id: "citation", label: "引用溯源" },
      { id: "datatable", label: "数据表格", badge: 2, badgeTone: "pending" },
      { id: "nav", label: "导航与分页" },
      { id: "timeline", label: "时间线" },
      { id: "overlay", label: "抽屉与弹层" },
      { id: "mobile", label: "移动端组件族", badge: "新" },
    ],
  },
];

interface CaseRow extends Record<string, any> {
  id: string;
  caseNo: string;
  title: string;
  grade: string;
  stage: string;
  owner: string;
  amount: number;
  deadline: string;
  status: string;
}

const CASES: CaseRow[] = [
  { id: "c1", caseNo: "(2026)京0105民初1234号", title: "张某诉某科技公司劳动争议", grade: "S", stage: "一审举证", owner: "王律师", amount: 486000, deadline: "2026-09-24", status: "进行中" },
  { id: "c2", caseNo: "(2026)京0105民初1188号", title: "李某诉某置业公司房屋买卖合同纠纷", grade: "A", stage: "庭前调解", owner: "王律师", amount: 1280000, deadline: "2026-09-28", status: "待复核" },
  { id: "c3", caseNo: "(2026)沪0115民初2077号", title: "某贸易公司诉某物流公司运输合同纠纷", grade: "B", stage: "一审开庭", owner: "陈律师", amount: 320000, deadline: "2026-10-09", status: "进行中" },
  { id: "c4", caseNo: "(2026)粤0304民初3391号", title: "赵某与某餐饮公司股权转让纠纷", grade: "A", stage: "二审", owner: "刘律师", amount: 2450000, deadline: "2026-10-15", status: "进行中" },
  { id: "c5", caseNo: "(2026)京0108民初4412号", title: "某文化传媒公司著作权侵权纠纷", grade: "B", stage: "证据交换", owner: "陈律师", amount: 156000, deadline: "2026-09-30", status: "待复核" },
  { id: "c6", caseNo: "(2026)苏0508民初5520号", title: "某制造企业买卖合同货款纠纷", grade: "C", stage: "一审举证", owner: "刘律师", amount: 88000, deadline: "2026-10-20", status: "进行中" },
  { id: "c7", caseNo: "(2026)京0105民初6603号", title: "孙某诉某保险公司人身损害赔偿", grade: "A", stage: "一审开庭", owner: "王律师", amount: 675000, deadline: "2026-10-06", status: "已结案" },
  { id: "c8", caseNo: "(2026)浙0106民初7714号", title: "某网络公司侵害商标权纠纷", grade: "B", stage: "庭前调解", owner: "陈律师", amount: 210000, deadline: "2026-10-12", status: "进行中" },
  { id: "c9", caseNo: "(2026)京0108民初8825号", title: "某教育公司服务合同纠纷", grade: "C", stage: "一审举证", owner: "刘律师", amount: 64000, deadline: "2026-10-25", status: "待复核" },
];

const STATUS_TONE: Record<string, "primary" | "pending" | "verified" | "neutral"> = {
  进行中: "primary",
  待复核: "pending",
  已结案: "verified",
};

const CASE_COLUMNS: DataTableColumn<CaseRow>[] = [
  {
    key: "title",
    header: "案件名称",
    mobile: "primary",
    sortable: true,
    pinned: true,
    render: (v: string) => <span className="font-medium text-ink-900">{v}</span>,
  },
  {
    key: "status",
    header: "状态",
    mobile: "status",
    width: "96px",
    render: (v: string) => <Badge variant={STATUS_TONE[v] ?? "neutral"}>{v}</Badge>,
  },
  { key: "caseNo", header: "案号", mobileHeader: "案号", numeric: true, width: "200px" },
  {
    key: "grade",
    header: "等级",
    width: "72px",
    align: "center",
    sortable: true,
    render: (v: string) => (
      <span className={`num text-label font-medium ${v === "S" ? "text-gold-600" : "text-ink-600"}`}>{v}</span>
    ),
  },
  { key: "stage", header: "阶段", width: "110px", sortable: true },
  { key: "owner", header: "主办律师", width: "104px", sortable: true },
  {
    key: "amount",
    header: "标的额",
    numeric: true,
    align: "right",
    sortable: true,
    render: (v: number) => `¥${v.toLocaleString("zh-CN")}`,
  },
  { key: "deadline", header: "期限", numeric: true, width: "112px", sortable: true },
];

const CITATIONS: Citation[] = [
  {
    id: "cit1",
    index: 1,
    title: "《中华人民共和国劳动合同法》",
    article: "第三十八条",
    source: "全国人民代表大会常务委员会",
    effectiveDate: "2013-07-01",
    status: "in-force",
    excerpt:
      "用人单位有下列情形之一的，劳动者可以解除劳动合同：（一）未按照劳动合同约定提供劳动保护或者劳动条件的；（二）未及时足额支付劳动报酬的；（三）未依法为劳动者缴纳社会保险费的；……",
    highlight: "未依法为劳动者缴纳社会保险费的",
    url: "#",
  },
  {
    id: "cit2",
    index: 2,
    title: "《中华人民共和国劳动合同法》",
    article: "第四十六条",
    source: "全国人民代表大会常务委员会",
    effectiveDate: "2013-07-01",
    status: "in-force",
    excerpt:
      "有下列情形之一的，用人单位应当向劳动者支付经济补偿：（一）劳动者依照本法第三十八条规定解除劳动合同的；（二）用人单位依照本法第三十六条规定向劳动者提出解除劳动合同并与劳动者协商一致解除劳动合同的；……",
    highlight: "劳动者依照本法第三十八条规定解除劳动合同的",
    url: "#",
  },
  {
    id: "cit3",
    index: 3,
    title: "《最高人民法院关于审理劳动争议案件适用法律问题的解释（一）》",
    article: "第四十四条",
    source: "最高人民法院",
    effectiveDate: "2021-01-01",
    status: "amended",
    excerpt:
      "用人单位与劳动者协商一致解除劳动合同后，劳动者以用人单位未依法支付经济补偿为由请求用人单位支付的，人民法院应予支持。",
    url: "#",
  },
];

const TIMELINE: TimelineItem[] = [
  { id: "t1", title: "客户提交委托", description: "张某 · 劳动争议", time: "09-12 10:24", actor: "助理 李", status: "done" },
  { id: "t2", title: "AI 生成案件分析", description: "识别 3 项争议焦点，引用法条 3 条", time: "09-12 10:26", actor: "模型 claude", status: "done" },
  { id: "t3", title: "律师复核并修订", description: "补充社保缴纳记录作为关键证据", time: "09-12 15:03", actor: "王律师", status: "done" },
  { id: "t4", title: "待合伙人确认", description: "标的额超过 30 万，需二级复核", time: "—", status: "current" },
  { id: "t5", title: "立案材料提交", time: "—", status: "pending" },
];

const STAGE_OPTIONS = [
  { value: "一审举证", label: "一审举证" },
  { value: "庭前调解", label: "庭前调解" },
  { value: "一审开庭", label: "一审开庭" },
  { value: "二审", label: "二审" },
  { value: "已结案", label: "已结案" },
];

/* ────────────────────────────── 页面骨架 ────────────────────────────── */

function Section({
  id,
  title,
  desc,
  children,
}: {
  id: string;
  title: string;
  desc?: string;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-20 border-t border-line pt-8 first:border-t-0 first:pt-0">
      <h2 className="text-h3 text-ink-900">{title}</h2>
      {desc && <p className="mt-1.5 max-w-3xl text-body-sm text-ink-500">{desc}</p>}
      <div className="mt-5 space-y-5">{children}</div>
    </section>
  );
}

function Panel({
  title,
  hint,
  children,
  className,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`rounded-r3 border border-line bg-surface p-4 ${className ?? ""}`}>
      <div className="mb-3 flex flex-wrap items-baseline gap-x-2">
        <h3 className="text-label font-medium text-ink-800">{title}</h3>
        {hint && <span className="text-caption text-ink-400">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

function SyncQueueDemo() {
  const [open, setOpen] = React.useState(false);
  const [offline, setOffline] = React.useState(false);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <SyncQueueBadge onClick={() => setOpen(true)} showZero />
        <Button size="sm" variant="outline" onClick={() => setOffline((v) => !v)}>
          {offline ? "恢复在线" : "模拟离线"}
        </Button>
      </div>
      <OfflineBanner pendingCount={offline ? 0 : 2} onSync={() => setOpen(true)} />
      <SyncQueueSheet isOpen={open} onClose={() => setOpen(false)} />
    </div>
  );
}

function InfiniteListDemo() {
  const all = React.useMemo(() => Array.from({ length: 24 }, (_, i) => `证据材料 ${i + 1}`), []);
  const [count, setCount] = React.useState(6);
  const [loading, setLoading] = React.useState(false);

  const loadMore = () => {
    setLoading(true);
    setTimeout(() => {
      setCount((c) => Math.min(all.length, c + 6));
      setLoading(false);
    }, 600);
  };

  return (
    <div className="h-72 overflow-hidden rounded-r2 border border-line">
      <PullToRefresh
        onRefresh={() =>
          new Promise<void>((resolve) =>
            setTimeout(() => {
              setCount(6);
              resolve();
            }, 800)
          )
        }
      >
        <InfiniteList
          items={all.slice(0, count)}
          rowKey={(item) => item}
          renderItem={(item) => <div className="px-4 py-3 text-body-sm text-ink-700">{item}</div>}
          hasMore={count < all.length}
          loading={loading}
          onLoadMore={loadMore}
          endMessage="全部证据已加载"
        />
      </PullToRefresh>
    </div>
  );
}

function CameraDemo() {
  const [files, setFiles] = React.useState<CapturedFile[]>([]);
  const [msg, setMsg] = React.useState("");
  return (
    <div className="space-y-3">
      <CameraCapture
        value={files}
        onChange={setFiles}
        hint="支持拍照或从相册选择，长边压缩至 1600px 后上传"
        onError={setMsg}
      />
      {msg && <p className="text-caption text-pending-600">{msg}</p>}
      {files.length > 0 && (
        <p className="num text-caption text-ink-500">
          合计 {(files.reduce((s, f) => s + f.size, 0) / 1024 / 1024).toFixed(2)} MB
        </p>
      )}
    </div>
  );
}

function DataTableDemo() {
  const [sortKey, setSortKey] = React.useState<string | null>("deadline");
  const [sortDir, setSortDir] = React.useState<"asc" | "desc" | null>("asc");
  const [selected, setSelected] = React.useState<string[]>([]);
  const [density, setDensity] = React.useState<"compact" | "default" | "comfortable">("default");
  const [chips, setChips] = React.useState<FilterChip[]>([
    { id: "grade", label: "案件等级", value: "A" },
    { id: "owner", label: "主办律师", value: "王律师" },
  ]);
  const [page, setPage] = React.useState(1);
  const [pageSize, setPageSize] = React.useState(5);

  const filtered = React.useMemo(
    () =>
      CASES.filter((c) => {
        const grade = chips.find((x) => x.id === "grade");
        const owner = chips.find((x) => x.id === "owner");
        return (!grade || c.grade === grade.value) && (!owner || c.owner === owner.value);
      }),
    [chips]
  );

  const paged = filtered.slice((page - 1) * pageSize, page * pageSize);

  return (
    <div className="space-y-3">
      <FilterBar
        chips={chips}
        onRemove={(id) => setChips((prev) => prev.filter((c) => c.id !== id))}
        onClearAll={() => setChips([])}
        resultCount={filtered.length}
        actions={
          <Button size="sm" variant="outline">
            导出
          </Button>
        }
      />

      <DataTable
        columns={CASE_COLUMNS}
        data={paged}
        rowKey={(row) => row.id}
        sortKey={sortKey}
        sortDir={sortDir}
        onSortChange={(k, d) => {
          setSortKey(k);
          setSortDir(d);
        }}
        selectable
        selectedKeys={selected}
        onSelectionChange={setSelected}
        columnSettings
        density={density}
        onDensityChange={setDensity}
        bulkActions={(rows, clear) => (
          <>
            <Button size="sm" variant="verify" onClick={clear}>
              批量确认 {rows.length} 件
            </Button>
            <Button size="sm" variant="outline" onClick={clear}>
              批量分派
            </Button>
          </>
        )}
        rowActions={() => (
          <>
            <Button size="sm" variant="ghost">
              查看
            </Button>
            <Button size="sm" variant="ghost">
              流转
            </Button>
          </>
        )}
        caption="案件列表"
      />

      <Pagination
        page={page}
        pageSize={pageSize}
        total={filtered.length}
        onPageChange={setPage}
        onPageSizeChange={(n) => {
          setPageSize(n);
          setPage(1);
        }}
      />
    </div>
  );
}

/* ────────────────────────────── 主页面 ────────────────────────────── */

export default function ComponentsPreviewPage() {
  const { theme, toggle } = useTheme();
  const [activeId, setActiveId] = React.useState("tokens");
  const [drawerOpen, setDrawerOpen] = React.useState(false);
  const [sheetOpen, setSheetOpen] = React.useState(false);
  const [activeCitation, setActiveCitation] = React.useState("cit1");
  const [stage, setStage] = React.useState("一审举证");

  // 滚动联动侧栏高亮
  React.useEffect(() => {
    const ids = NAV.flatMap((g) => g.items.map((i) => i.id));
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (visible) setActiveId(visible.target.id);
      },
      { rootMargin: "-72px 0px -70% 0px", threshold: 0 }
    );
    ids.forEach((id) => {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, []);

  const scrollTo = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
    setActiveId(id);
  };

  return (
    <SyncQueueProvider
      transport={async () => {
        await new Promise((r) => setTimeout(r, 500));
      }}
    >
      <AppShell
        nav={NAV}
        activeId={activeId}
        onNavigate={(item) => scrollTo(item.id)}
        appId="web"
        apps={APPS}
        breadcrumb={
          <span className="text-ink-500">
            设计系统 <span className="mx-1 text-ink-300">/</span>
            <span className="text-ink-900">组件预览</span>
          </span>
        }
        searchPlaceholder="搜索组件…"
        contentWidth="workbench"
        user={{ name: "王律师", role: "执业律师" }}
        // P0-15 通知中心：不传则不渲染铃铛（骨架层默认不发起网络轮询）。
        // 此页未登录时轮询会静默失败并保持空角标——正好演示空态。
        notifications={{}}
        actions={
          <Button size="sm" variant="ghost" onClick={toggle}>
            {theme === "dark" ? "浅色" : "深色"}
          </Button>
        }
        tabs={[
          { id: "tokens", label: "令牌" },
          { id: "provenance", label: "三态" },
          { id: "datatable", label: "表格" },
          { id: "mobile", label: "移动端", badge: 7 },
        ]}
        onTabSelect={(t) => scrollTo(t.id)}
      >
        <div className="space-y-10">
          <header>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-h1 text-ink-900">「墨与纸」组件预览</h1>
              <Badge variant="primary">v2 · 阶段二</Badge>
            </div>
            <p className="mt-2 max-w-3xl text-body text-ink-500">
              本页同时承担两个职责：一是把阶段二新增的全部组件摆在真实语境里自查，
              二是作为统一骨架（AppShell）的集成验证——你现在看到的侧栏、顶栏与移动端
              Tab Bar 就是最终形态。
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              <Link href="/components-preview/login">
                <Button size="sm" variant="outline">
                  查看登录页
                </Button>
              </Link>
              <Button size="sm" variant="ghost" onClick={() => scrollTo("mobile")}>
                跳到移动端组件
              </Button>
            </div>
          </header>

          {/* ── 1. 设计令牌 ─────────────────────────────────── */}
          <Section
            id="tokens"
            title="设计令牌"
            desc="所有颜色以 RGB 三元组形式定义在 tokens.css，经 Tailwind preset 映射为语义色名。深色模式只覆盖变量值，不产生任何 !important。"
          >
            <Panel title="色彩系统" hint="鼠标悬停可看色值">
              <div className="space-y-4">
                {(
                  [
                    ["ink", "中性 · 墨", "页面底色、文本、分隔线"],
                    ["brand", "品牌 · 靛蓝墨", "主按钮、激活导航、链接"],
                    ["ai", "AI 生成", "仅用于责任边界，禁止作装饰色"],
                    ["verified", "律师已确认", "三态之绿"],
                    ["pending", "待复核", "三态之琥珀"],
                    ["gold", "香槟金", "全局面积 ≤3%，仅点睛"],
                  ] as const
                ).map(([key, name, note]) => (
                  <div key={key}>
                    <div className="mb-1.5 flex flex-wrap items-baseline gap-x-2">
                      <span className="text-label font-medium text-ink-800">{name}</span>
                      <span className="text-caption text-ink-400">{note}</span>
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {[50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950].map((step) => (
                        <div key={step} className="text-center">
                          {/* 色板用内联 CSS 变量：Tailwind JIT 无法扫描拼接出来的类名 */}
                          <div
                            className="h-9 w-14 rounded-r1 border border-line"
                            style={{ backgroundColor: `rgb(var(--${key}-${step}))` }}
                            title={`--${key}-${step}`}
                          />
                          <span className="num text-caption text-ink-400">{step}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </Panel>

            <div className="grid gap-4 sm:grid-cols-3">
              <Panel title="圆角" hint="4 / 6 / 8 / 12">
                <div className="flex items-end gap-2">
                  {["r1", "r2", "r3", "r4"].map((r) => (
                    <div key={r} className="text-center">
                      <div
                        className="h-12 w-12 border border-line bg-surface-subtle"
                        style={{ borderRadius: `var(--${r})` }}
                      />
                      <span className="text-caption text-ink-400">{r}</span>
                    </div>
                  ))}
                </div>
              </Panel>
              <Panel title="阴影" hint="三级，优先用 1px 边框">
                <div className="flex items-end gap-2">
                  {["s1", "s2", "s3"].map((s) => (
                    <div key={s} className="text-center">
                      <div
                        className="h-12 w-12 rounded-r2 bg-surface"
                        style={{ boxShadow: `var(--${s})` }}
                      />
                      <span className="text-caption text-ink-400">{s}</span>
                    </div>
                  ))}
                </div>
              </Panel>
              <Panel title="字体" hint="三栈分立">
                <div className="space-y-1.5">
                  <p className="text-body-sm text-ink-800">Inter + 系统中文字 · UI 正文</p>
                  <p className="legal-text text-ink-800">Noto Serif SC · 法条原文与文书正文</p>
                  <p className="num text-body-sm text-ink-800">(2026)京0105民初1234号 ¥486,000</p>
                </div>
              </Panel>
            </div>
          </Section>

          {/* ── 2. 责任边界三态 ─────────────────────────────── */}
          <Section
            id="provenance"
            title="责任边界三态"
            desc="任何法律内容必须且只能属于 AI 生成 / 律师已确认 / 待复核 之一。颜色不可独立承载语义——色盲用户与黑白打印必须仍能分辨，因此颜色 + 文字 + 图标三重编码。"
          >
            <Panel title="徽章" hint="三种状态 × 两种尺寸">
              <div className="flex flex-wrap items-center gap-3">
                <ProvenanceBadge state="ai" by="模型 claude-sonnet" />
                <ProvenanceBadge state="verified" by="王律师" at="09-16 14:20" />
                <ProvenanceBadge state="pending" />
                <ProvenanceBadge state="ai" size="sm" />
                <ProvenanceBadge state="verified" size="sm" />
                <ProvenanceBadge state="pending" size="sm" />
              </div>
            </Panel>

            <Panel title="内容块" hint="AI 生成用紫色虚线，律师已确认用绿色左实线">
              <div className="space-y-3">
                <ProvenanceBlock state="ai" by="模型 claude-sonnet" at="09-16 14:18">
                  <p className="text-body-sm text-ink-700">
                    根据《劳动合同法》第三十八条，用人单位未依法为劳动者缴纳社会保险费的，
                    劳动者可以解除劳动合同，并依第四十六条主张经济补偿。本案社保缴纳记录
                    存在 2024 年 3 月至 8 月的断缴，构成上述情形。
                  </p>
                </ProvenanceBlock>

                <ProvenanceBlock state="verified" by="王律师" at="09-16 14:25">
                  <p className="text-body-sm text-ink-700">
                    已核对社保中心出具的缴费明细，断缴期间为 2024 年 3 月至 8 月，
                    与当事人陈述一致。该结论可直接用于仲裁申请书。
                  </p>
                </ProvenanceBlock>

                <ProvenanceBlock state="pending">
                  <p className="text-body-sm text-ink-700">
                    经济补偿的具体计算基数尚待确认（前 12 个月平均工资是否包含年终奖），
                    需律师复核后定稿。
                  </p>
                </ProvenanceBlock>
              </div>
            </Panel>

            <Panel title="全局图例" hint="出现在页脚与导出文书末尾">
              <ProvenanceLegend />
              <div className="mt-3">
                <ProvenanceLegend variant="compact" />
              </div>
            </Panel>
          </Section>

          {/* ── 3. 引用溯源 ─────────────────────────────────── */}
          <Section
            id="citation"
            title="引用溯源"
            desc="由弹窗改为右侧 380px 常驻面板：读法条原文时不该丢掉正文位置。移动端降级为页内展开。"
          >
            <div className="overflow-hidden rounded-r3 border border-line bg-surface">
              <div className="flex">
                <article className="min-w-0 flex-1 p-5 sm:p-6">
                  <h3 className="text-h4 text-ink-900">争议焦点二：社保断缴是否构成被迫解除事由</h3>
                  <p className="mt-3 text-body text-ink-700">
                    用人单位未依法为劳动者缴纳社会保险费的，劳动者可以解除劳动合同
                    <CitationChip
                      index={1}
                      active={activeCitation === "cit1"}
                      onClick={() => setActiveCitation("cit1")}
                    />
                    ，并要求用人单位支付经济补偿
                    <CitationChip
                      index={2}
                      active={activeCitation === "cit2"}
                      onClick={() => setActiveCitation("cit2")}
                    />
                    。本案中，2024 年 3 月至 8 月期间用人单位未缴纳社保，事实清楚、证据充分。
                  </p>
                  <p className="mt-3 text-body text-ink-700">
                    关于协商一致解除后仍可主张经济补偿的问题，可参照司法解释的相关规定
                    <CitationChip
                      index={3}
                      active={activeCitation === "cit3"}
                      onClick={() => setActiveCitation("cit3")}
                    />
                    。
                  </p>

                  <div className="mt-5 rounded-r2 border border-line bg-surface-subtle p-3.5">
                    <p className="legal-text text-ink-800">
                      「用人单位与劳动者协商一致解除劳动合同后，劳动者以用人单位未依法支付
                      经济补偿为由请求用人单位支付的，人民法院应予支持。」
                    </p>
                    <p className="mt-2 text-caption text-ink-500">
                      —— 法条原文以衬线体呈现，与界面文字明确区分
                    </p>
                  </div>
                </article>

                {/* 桌面常驻引用面板 */}
                <CitationPanel
                  className="hidden lg:flex"
                  citations={CITATIONS}
                  activeId={activeCitation}
                  onSelect={(c) => setActiveCitation(c.id)}
                />
              </div>

              {/* 移动端页内展开 */}
              <div className="border-t border-line lg:hidden">
                <CitationPanel variant="inline" citations={CITATIONS} activeId={activeCitation} />
              </div>
            </div>
          </Section>

          {/* ── 4. 数据表格 ─────────────────────────────────── */}
          <Section
            id="datatable"
            title="数据表格"
            desc="排序、筛选回显、列设置、三档密度、行选择与批量操作。移动端按列数自动降级：≤3 列保留表格，4–6 列转卡片（两列栅格），>6 列只保留 3 个关键字段。案号、金额、日期始终等宽。"
          >
            <div className="grid gap-3 sm:grid-cols-4">
              <KpiCard label="在办案件" value={CASES.length} color="brand" />
              <KpiCard label="待复核" value={3} color="pending" />
              <KpiCard label="本月结案" value={1} color="verified" />
              <KpiCard label="标的额合计" value={572.2} unit="万" color="gold" />
            </div>

            <DataTableDemo />

            <Alert variant="info" title="移动端降级规则">
              把浏览器窗口缩到 375px 以下，或直接用手机打开本页，即可看到上表自动变为卡片列表：
              案件名称作为标题、状态徽标置于右侧、其余字段进入两列栅格。
            </Alert>
          </Section>

          {/* ── 5. 导航与分页 ───────────────────────────────── */}
          <Section id="nav" title="导航与分页" desc="分段控制、筛选条、分页器。">
            <div className="grid gap-4 lg:grid-cols-2">
              <Panel title="分段控制器" hint="方向键可切换">
                <div className="space-y-3">
                  <SegmentedControl value={stage} onChange={setStage} options={STAGE_OPTIONS} ariaLabel="案件阶段" />
                  <SegmentedControl
                    size="sm"
                    value="all"
                    onChange={() => {}}
                    ariaLabel="范围"
                    options={[
                      { value: "all", label: "全部", badge: 42 },
                      { value: "mine", label: "我负责的", badge: 8 },
                      { value: "review", label: "待复核", badge: 3 },
                    ]}
                  />
                  <SegmentedControl
                    fullWidth
                    value="week"
                    onChange={() => {}}
                    ariaLabel="时间范围"
                    options={[
                      { value: "day", label: "今日" },
                      { value: "week", label: "本周" },
                      { value: "month", label: "本月" },
                    ]}
                  />
                </div>
              </Panel>

              <Panel title="分页器" hint="页数过多自动折叠">
                <div className="space-y-3">
                  <PaginationDemo />
                  <PaginationDemo total={238} />
                </div>
              </Panel>
            </div>

            <Panel title="按钮与徽标">
              <div className="flex flex-wrap items-center gap-2">
                <Button variant="primary">主操作</Button>
                <Button variant="verify">确认无误</Button>
                <Button variant="secondary">次要</Button>
                <Button variant="outline">描边</Button>
                <Button variant="ghost">幽灵</Button>
                <Button variant="danger">退回</Button>
                <Button variant="accent">AI 生成</Button>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Badge variant="primary">进行中</Badge>
                <Badge variant="pending">待复核</Badge>
                <Badge variant="verified">已确认</Badge>
                <Badge variant="danger">已退回</Badge>
                <Badge variant="ai">AI 生成</Badge>
                <Badge variant="gold">S 级</Badge>
                <Badge variant="neutral">已归档</Badge>
              </div>
            </Panel>
          </Section>

          {/* ── 6. 时间线 ───────────────────────────────────── */}
          <Section id="timeline" title="时间线" desc="复核流转、证据时间线。节点状态用颜色 + 形状双通道表达。">
            <div className="grid gap-4 lg:grid-cols-2">
              <Panel title="案件流转" hint="含 AI 生成与人工复核节点">
                <Timeline items={TIMELINE} />
              </Panel>
              <Panel title="证据时间线" hint="含已退回节点">
                <Timeline
                  items={[
                    { id: "e1", title: "劳动合同签订", time: "2023-04-01", status: "done" },
                    { id: "e2", title: "工资流水（银行导出）", description: "2023-04 至 2026-08，共 41 期", time: "2026-09-12", status: "done" },
                    { id: "e3", title: "社保缴费明细", description: "发现 2024-03 至 2024-08 断缴", time: "2026-09-13", status: "done" },
                    { id: "e4", title: "微信聊天记录截图", description: "清晰度不足，需重新取证", time: "2026-09-14", status: "rejected" },
                    { id: "e5", title: "补充取证", time: "—", status: "pending" },
                  ]}
                />
              </Panel>
            </div>
          </Section>

          {/* ── 7. 抽屉与弹层 ───────────────────────────────── */}
          <Section id="overlay" title="抽屉与弹层" desc="桌面右侧抽屉 + 移动端底部抽屉，两者语义不同：抽屉用于「补充查看」，底部抽屉用于「主操作」。">
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={() => setDrawerOpen(true)}>
                打开右侧抽屉
              </Button>
              <Button variant="outline" onClick={() => setSheetOpen(true)}>
                打开底部抽屉
              </Button>
            </div>

            <Drawer
              isOpen={drawerOpen}
              onClose={() => setDrawerOpen(false)}
              title="案件流转记录"
              description="张某诉某科技公司劳动争议 · (2026)京0105民初1234号"
              width="md"
              footer={
                <div className="flex gap-2">
                  <Button variant="outline" className="flex-1" onClick={() => setDrawerOpen(false)}>
                    关闭
                  </Button>
                  <Button variant="primary" className="flex-1" onClick={() => setDrawerOpen(false)}>
                    导出记录
                  </Button>
                </div>
              }
            >
              <Timeline items={TIMELINE} />
            </Drawer>

            <BottomSheet
              isOpen={sheetOpen}
              onClose={() => setSheetOpen(false)}
              title="分派案件"
              description="选择主办律师后案件将进入其待办"
              heightRatio={0.55}
              footer={
                <div className="flex gap-2">
                  <Button variant="outline" className="flex-1" onClick={() => setSheetOpen(false)}>
                    取消
                  </Button>
                  <Button variant="primary" className="flex-1" onClick={() => setSheetOpen(false)}>
                    确认分派
                  </Button>
                </div>
              }
            >
              <div className="space-y-2">
                {["王律师 · 在办 8 件", "陈律师 · 在办 5 件", "刘律师 · 在办 11 件"].map((s) => (
                  <button
                    key={s}
                    type="button"
                    className="flex w-full min-h-tap items-center justify-between rounded-r2 border border-line px-3 text-left text-body-sm text-ink-700 transition-colors duration-fast active:bg-surface-hover"
                  >
                    {s}
                    <span className="text-ink-400">›</span>
                  </button>
                ))}
              </div>
            </BottomSheet>
          </Section>

          {/* ── 8. 移动端组件族 ─────────────────────────────── */}
          <Section
            id="mobile"
            title="移动端组件族"
            desc="弱网、单手、碎片时间是移动端办案的真实约束。以下七个组件分别对应拍照取证、离线续写、长列表、折叠表单、下拉刷新与同步队列。"
          >
            <div className="grid gap-4 lg:grid-cols-2">
              <Panel title="折叠面板" hint="手风琴模式，同时只展开一个">
                <CollapseGroup className="rounded-r2 border border-line">
                  <CollapsePanel title="当事人信息" defaultOpen badge={<Badge variant="ink">必填</Badge>}>
                    <div className="space-y-2 text-body-sm text-ink-700">
                      <p>张某 · 男 · 1988 年生</p>
                      <p className="num">身份证 1101**********1234</p>
                      <p className="num">手机 138****5678</p>
                    </div>
                  </CollapsePanel>
                  <CollapsePanel title="争议标的" subtitle="金额与计算方式">
                    <p className="text-body-sm text-ink-700">经济补偿 ¥486,000（前 12 个月平均工资 × 8 年）</p>
                  </CollapsePanel>
                  <CollapsePanel title="管辖与送达" subtitle="法院、送达地址">
                    <p className="text-body-sm text-ink-700">北京市朝阳区人民法院 · 电子送达</p>
                  </CollapsePanel>
                </CollapseGroup>
              </Panel>

              <Panel title="拍照取证" hint="前端压缩后上传，弱网也能传完">
                <CameraDemo />
              </Panel>

              <Panel title="长列表 + 下拉刷新" hint="滚动到底自动加载，也可下拉重置">
                <InfiniteListDemo />
              </Panel>

              <Panel title="离线与同步队列" hint="幂等入队、有界重试、本地持久化">
                <SyncQueueDemo />
              </Panel>
            </div>
          </Section>

          <footer className="border-t border-line pt-6 text-caption text-ink-400">
            律小智 · 设计系统 v2「墨与纸」 · 阶段二组件预览 · 本页内容均为演示数据
          </footer>
        </div>
      </AppShell>
    </SyncQueueProvider>
  );
}

function PaginationDemo({ total = 42 }: { total?: number }) {
  const [page, setPage] = React.useState(1);
  const [pageSize, setPageSize] = React.useState(10);
  return (
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
  );
}
