"use client";

import {
  AppLayout,
  Badge,
  buildAppLinks,
  ContentTooLargeGate,
  type AppShellNavGroup,
} from "@nlaw/ui";
import {
  ClipboardCheck,
  FolderOpen,
  LayoutDashboard,
  ListChecks,
  MessageSquareWarning,
  Receipt,
  ScrollText,
  Send,
  ShieldCheck,
} from "lucide-react";
import { TenantScopeBar } from "./_components/TenantScopeBar";

const APPS = buildAppLinks({
  web: process.env.NEXT_PUBLIC_APP_WEB_URL,
  lawyer: process.env.NEXT_PUBLIC_APP_LAWYER_URL,
  admin: process.env.NEXT_PUBLIC_APP_ADMIN_URL,
  im: process.env.NEXT_PUBLIC_APP_IM_URL,
});

const ICON = "h-4 w-4";

/**
 * 侧栏只挂**已经真实存在**的页面。
 *
 * 这条纪律不是洁癖：侧栏里出现点了 404 的条目，比侧栏短更糟——
 * 前者会让用户认为「系统坏了」，后者只说明「还没做到」。
 *
 * 规范 `design-spec.md` §07 的概念图（`mockups/04-admin-cockpit.html`）
 * 画的是四组十项，但那十项里有八项目前**没有对应页面**。
 * 概念图是目标形态，侧栏是当前状态，两者不一致是正常的，
 * 把目标形态提前挂到侧栏上才是错的。
 *
 * 各项的可实现性（后端已冻结，只能按既有端点做）：
 * - 运营驾驶舱 ✅ / 案件管理 ✅ / 复核队列 ✅ / 合规扫描 ✅ / 投诉举报 ✅ /
 *   审计保留期 ✅ / 计费与工单 ✅ / 派单记录 ✅
 * - 任务中心 ✅（B2，复用 `GET /api/v1/jobs`；合规扫描与知识库导入都在这里看进度）
 * - 内容审核 —— `ModerationRecord` 有模型与服务，但**无任何读端点**，做不了
 * - **派单规则配置** —— `DispatchRule` 有模型且引擎在跑，但**无任何端点**，做不了
 *   （本页因此只做「派单记录」，菜单项按实际能力命名）
 * - 租户管理 —— **无 `/tenants` 端点**，做不了
 * - 系统设置 —— 无端点，做不了
 */
const NAV: AppShellNavGroup[] = [
  {
    title: "平台治理",
    items: [
      { id: "home", label: "运营驾驶舱", href: "/", icon: <LayoutDashboard className={ICON} /> },
      { id: "cases", label: "案件管理", href: "/cases", icon: <FolderOpen className={ICON} /> },
      { id: "dispatches", label: "派单记录", href: "/dispatches", icon: <Send className={ICON} /> },
      {
        id: "reviews",
        label: "复核队列",
        href: "/reviews",
        icon: <ClipboardCheck className={ICON} />,
      },
      {
        id: "compliance",
        label: "合规扫描",
        href: "/compliance",
        icon: <ShieldCheck className={ICON} />,
      },
      {
        id: "complaints",
        label: "投诉举报",
        href: "/complaints",
        icon: <MessageSquareWarning className={ICON} />,
      },
      { id: "audit", label: "审计保留期", href: "/audit", icon: <ScrollText className={ICON} /> },
      { id: "jobs", label: "任务中心", href: "/jobs", icon: <ListChecks className={ICON} /> },
    ],
  },
  {
    title: "商业",
    items: [
      { id: "billing", label: "计费与工单", href: "/billing", icon: <Receipt className={ICON} /> },
    ],
  },
];

/*
 * 刻意**不传 `tabs`**。
 *
 * 规范 §08.4 对运营后台的移动形态有明确约定：「AppBar + 只读标识，无 Tab Bar」。
 * 依据是 §08.1 把 admin 的移动能力限定为「只读查看：驾驶舱核心指标、告警、案件查询；
 * 批量操作、规则配置不移动化」——底部 Tab 表达的是「这几个是日常平级入口」，
 * 而运营后台在手机上并没有这种平级日常动线，导航走抽屉即可。
 *
 * 早期实现传了两个 Tab，等于把一个桌面后台的导航模型套到了手机上。
 */
const SECTION_LABELS: Record<string, string> = {
  home: "运营驾驶舱",
  cases: "案件管理",
  dispatches: "派单记录",
  reviews: "复核队列",
  compliance: "合规扫描",
  complaints: "投诉举报",
  audit: "审计保留期",
  billing: "计费与工单",
  jobs: "任务中心",
};

export default function AdminAppLayout({ children }: { children: React.ReactNode }) {
  return (
    <AppLayout
      appId="admin"
      // P2-9 跨端串号防线：本端只接受平台管理员会话，
      // 其余角色（共享 Cookie 恢复出他端用户）强制服务端登出。
      allowedRoles={["PLATFORM_ADMIN"]}
      apps={APPS}
      nav={NAV}
      sectionLabels={SECTION_LABELS}
      contentWidth="wide"
      searchPlaceholder="搜索租户、案件、审计记录…"
      actions={<Badge variant="neutral">只读</Badge>}
    >
      {/* 租户视角切换条：放在内容区顶部而非顶栏，是为了让它对三个页面都生效，
          且不侵占 AppShell 的通用布局（其它三端没有这个概念）。 */}
      <div className="mb-5">
        <TenantScopeBar />
      </div>
      {/* B1 端级兜底：保证 413 至少被明确告知一次，不会「点了没反应」。
          本端若有长文本表单，仍应单独接 `GateBanner`（本组件是兜底不是替代）。 */}
      <ContentTooLargeGate />
      {children}
    </AppLayout>
  );
}
