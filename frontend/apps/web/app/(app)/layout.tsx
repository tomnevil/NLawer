"use client";

import { useRouter } from "next/navigation";
import {
  AppLayout,
  buildAppLinks,
  type AppShellNavGroup,
  type TabBarItem,
} from "@nlaw/ui";
import {
  LayoutDashboard,
  MessagesSquare,
  FileText,
  ScanLine,
  ShieldCheck,
  Database,
  BarChart3,
} from "lucide-react";

/* 四端地址走静态的 process.env 访问，Next 才会在客户端包里内联替换 */
const APPS = buildAppLinks({
  web: process.env.NEXT_PUBLIC_APP_WEB_URL,
  lawyer: process.env.NEXT_PUBLIC_APP_LAWYER_URL,
  admin: process.env.NEXT_PUBLIC_APP_ADMIN_URL,
  im: process.env.NEXT_PUBLIC_APP_IM_URL,
});

const ICON = "h-4 w-4";

const NAV: AppShellNavGroup[] = [
  {
    title: "工作台",
    items: [
      { id: "home", label: "首页", href: "/", icon: <LayoutDashboard className={ICON} /> },
      { id: "qa", label: "智能问答", href: "/qa", icon: <MessagesSquare className={ICON} /> },
    ],
  },
  {
    title: "文书与合规",
    items: [
      { id: "documents", label: "文书工作台", href: "/documents", icon: <FileText className={ICON} /> },
      { id: "contract-review", label: "合同审查", href: "/contract-review", icon: <ScanLine className={ICON} /> },
      { id: "compliance", label: "合规扫描", href: "/compliance", icon: <ShieldCheck className={ICON} /> },
    ],
  },
  {
    title: "资料与账务",
    items: [
      { id: "knowledge", label: "企业知识库", href: "/knowledge", icon: <Database className={ICON} /> },
      { id: "billing", label: "用量与计费", href: "/billing", icon: <BarChart3 className={ICON} /> },
    ],
  },
];

/** 移动端底部 Tab：最多 5 项，按使用频次排序 */
const TABS: TabBarItem[] = [
  { id: "home", label: "首页", href: "/" },
  { id: "qa", label: "问答", href: "/qa" },
  { id: "documents", label: "文书", href: "/documents" },
  { id: "compliance", label: "合规", href: "/compliance" },
  { id: "billing", label: "计费", href: "/billing" },
];

const SECTION_LABELS: Record<string, string> = {
  home: "工作台",
  qa: "智能问答",
  documents: "文书工作台",
  "contract-review": "合同审查",
  compliance: "合规扫描",
  knowledge: "企业知识库",
  billing: "用量与计费",
};

export default function WebAppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();

  return (
    <AppLayout
      appId="web"
      apps={APPS}
      nav={NAV}
      tabs={TABS}
      sectionLabels={SECTION_LABELS}
      contentWidth="workbench"
      searchPlaceholder="搜索法条、文书、历史问答…"
      // 顶栏搜索直接转成一次提问，比跳一个「搜索页」更符合本产品的用法
      onSearch={(kw) => kw.trim() && router.push(`/qa?q=${encodeURIComponent(kw.trim())}`)}
    >
      {children}
    </AppLayout>
  );
}
