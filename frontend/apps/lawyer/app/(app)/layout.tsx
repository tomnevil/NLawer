"use client";

import {
  AppLayout,
  buildAppLinks,
  ContentTooLargeGate,
  type AppShellNavGroup,
  type TabBarItem,
} from "@nlaw/ui";
import {
  LayoutDashboard,
  Inbox,
  FolderOpen,
  ClipboardCheck,
  Archive,
  Bell,
  ListChecks,
} from "lucide-react";

import { notificationHref } from "../../lib/notificationRoutes";
import { lawyerSyncTransport } from "../../lib/syncTransport";

const APPS = buildAppLinks({
  web: process.env.NEXT_PUBLIC_APP_WEB_URL,
  lawyer: process.env.NEXT_PUBLIC_APP_LAWYER_URL,
  admin: process.env.NEXT_PUBLIC_APP_ADMIN_URL,
  im: process.env.NEXT_PUBLIC_APP_IM_URL,
});

const ICON = "h-4 w-4";

const NAV: AppShellNavGroup[] = [
  {
    title: "办案",
    items: [
      { id: "home", label: "工作台", href: "/", icon: <LayoutDashboard className={ICON} /> },
      { id: "dispatches", label: "派单池", href: "/dispatches", icon: <Inbox className={ICON} /> },
      { id: "cases", label: "我的案件", href: "/cases", icon: <FolderOpen className={ICON} /> },
      { id: "reviews", label: "复核队列", href: "/reviews", icon: <ClipboardCheck className={ICON} /> },
    ],
  },
  {
    title: "卷宗",
    items: [
      { id: "archives", label: "归档", href: "/archives", icon: <Archive className={ICON} /> },
      { id: "notifications", label: "通知", href: "/notifications", icon: <Bell className={ICON} /> },
    ],
  },
  {
    title: "后台",
    items: [
      // 任务中心（B2）：案件分析 / 证据解析 / 合规扫描都是后台任务，
      // 此前**没有任何可见入口**——进度看不见、失败了也没人知道。
      { id: "jobs", label: "任务中心", href: "/jobs", icon: <ListChecks className={ICON} /> },
    ],
  },
];

/**
 * 移动端底部 Tab：律师端高频动作是「接单 → 办案 → 送审 → 看通知」。
 *
 * 这里的 `id` 同时是同步任务 `section` 的取值域（见 `lib/syncTransport.ts`
 * 的 `SYNC_SECTION`）——**改 id 要同步改那张表**，否则「待同步」琥珀角标
 * 会挂到一个不存在的页签上，而且是静默失效（没有报错，只是角标不见了）。
 *
 * ⚠️ 因此新增「任务中心」**只加侧栏、不加 Tab**：加 Tab 就必须同步改
 * `SYNC_SECTION`，而任务中心没有离线写入需求，不值得为它动那张表。
 */
const TABS: TabBarItem[] = [
  { id: "home", label: "工作台", href: "/" },
  { id: "dispatches", label: "派单", href: "/dispatches" },
  { id: "cases", label: "案件", href: "/cases" },
  { id: "reviews", label: "复核", href: "/reviews" },
  { id: "notifications", label: "通知", href: "/notifications" },
];

const SECTION_LABELS: Record<string, string> = {
  home: "工作台",
  dispatches: "派单池",
  cases: "我的案件",
  reviews: "复核队列",
  archives: "归档",
  notifications: "通知",
};

/**
 * 律师端应用外壳。
 *
 * `sync` 开启离线写入通道。开启的依据是这一端的业务特性，而不是「四端统一」：
 * 律师要在法院、看守所会见室、地下车库这些**没有信号**的地方办案，
 * 而「提交复核 / 出复核结论 / 归档」都是有时间窗口的一次性动作——
 * 断网时只弹一个「操作失败」然后丢掉，是这一端最不能接受的失败方式。
 *
 * `storageKey` 按应用分开：四端各自独立进程、同一个浏览器下 localStorage
 * 是共享的，用同一个 key 会让企业端的待同步任务出现在律师端的队列里。
 */
export default function LawyerAppLayout({ children }: { children: React.ReactNode }) {
  return (
    <AppLayout
      appId="lawyer"
      apps={APPS}
      nav={NAV}
      tabs={TABS}
      sectionLabels={SECTION_LABELS}
      contentWidth="workbench"
      searchPlaceholder="搜索案件、当事人、案号…"
      notifications={{ href: notificationHref }}
      sync={{ transport: lawyerSyncTransport, storageKey: "nlawer.lawyer.sync-queue" }}
    >
      {/* B1 端级兜底：保证 413 至少被明确告知一次，不会「点了没反应」。
          本端若有长文本表单，仍应单独接 `GateBanner`（本组件是兜底不是替代）。 */}
      <ContentTooLargeGate />
      {children}
    </AppLayout>
  );
}
