// 律小智 UI 组件库 v2「墨与纸」。
// 设计规范：deliverables/ui-design/design-spec.md
// 设计令牌：./tokens.css（唯一事实来源，深色模式由 CSS 变量驱动）
export { Button } from "./components/Button";
export type { ButtonProps } from "./components/Button";
export {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from "./components/Card";
export type { CardProps } from "./components/Card";
export { Input } from "./components/Input";
export type { InputProps } from "./components/Input";
export { Badge } from "./components/Badge";
export type { BadgeProps } from "./components/Badge";
export { Modal } from "./components/Modal";
export type { ModalProps } from "./components/Modal";
// `Sidebar` / `Header`（v1 骨架）已于阶段三删除：
// 四端业务页与 components-preview 全仓零引用，被 AppShell 取代；
// 且 `Header` 的 `h-topbar` + `paddingTop: safe-top` 组合在刘海屏上会挤压内容。
// 需要壳层请用 AppShell / AppLayout。
export { KpiCard } from "./components/KpiCard";
export type { KpiCardProps } from "./components/KpiCard";
export { Alert } from "./components/Alert";
export type { AlertProps } from "./components/Alert";
export { ToastProvider, useToast } from "./components/Toast";
export type { ToastProps } from "./components/Toast";
export { Spinner } from "./components/Spinner";
export type { SpinnerProps } from "./components/Spinner";
export { EmptyState } from "./components/EmptyState";
export type { EmptyStateProps } from "./components/EmptyState";
export { Skeleton } from "./components/Skeleton";
export type { SkeletonProps } from "./components/Skeleton";
export { Table } from "./components/Table";
export type { TableProps, TableColumn } from "./components/Table";

// 统一骨架（桌面侧栏 + 移动 Tab Bar 双形态）
export { AppShell, AppShellLogoMark, AppShellChevronRight } from "./components/AppShell";
export type {
  AppShellProps,
  AppShellNavItem,
  AppShellNavGroup,
  AppShellApp,
  AppShellUser,
  AppShellNotifications,
} from "./components/AppShell";
export { TabBar } from "./components/TabBar";
export type { TabBarProps, TabBarItem } from "./components/TabBar";
export { AppLayout } from "./components/AppLayout";
export type { AppLayoutProps } from "./components/AppLayout";
// 独立版应用切换器：供不使用 AppShell 侧栏的满屏布局（IM 三栏）复用
export { AppSwitcher } from "./components/AppSwitcher";
export type { AppSwitcherProps } from "./components/AppSwitcher";

// ── 通知中心（P0-15：通知读路径）────────────────────────────────────
export {
  NotificationCenter,
  formatRelativeTime,
  formatBadge,
  // 类型元数据统一从组件库导出：各端不要各自维护 type→图标/颜色 映射，
  // 否则后端新增类型时必然有某一端渲染成空白（详见组件内注释）。
  notificationMeta,
} from "./components/NotificationCenter";
export type {
  NotificationCenterProps,
  NotificationItem,
  NotificationUnread,
  NotificationTypeMeta,
} from "./components/NotificationCenter";

// ── 责任边界三态（AI 生成 / 律师已确认 / 待复核）────────────────────────
export { ProvenanceBadge } from "./components/ProvenanceBadge";
export type { ProvenanceBadgeProps } from "./components/ProvenanceBadge";
export { ProvenanceBlock } from "./components/ProvenanceBlock";
export type { ProvenanceBlockProps } from "./components/ProvenanceBlock";
export { ProvenanceLegend } from "./components/ProvenanceLegend";
export type { ProvenanceLegendProps } from "./components/ProvenanceLegend";

// ── 引用溯源 ────────────────────────────────────────────────────────
export { CitationChip } from "./components/CitationChip";
export type { CitationChipProps } from "./components/CitationChip";
export { CitationPanel } from "./components/CitationPanel";
export type { CitationPanelProps, Citation, CitationStatus } from "./components/CitationPanel";

// ── 数据与导航 ──────────────────────────────────────────────────────
export { SegmentedControl } from "./components/SegmentedControl";
export type { SegmentedControlProps, SegmentedControlOption } from "./components/SegmentedControl";
export { Pagination } from "./components/Pagination";
export type { PaginationProps } from "./components/Pagination";
export { FilterBar } from "./components/FilterBar";
export type { FilterBarProps, FilterChip } from "./components/FilterBar";
export { Timeline } from "./components/Timeline";
export type { TimelineProps, TimelineItem, TimelineStatus } from "./components/Timeline";
export { Drawer } from "./components/Drawer";
export type { DrawerProps } from "./components/Drawer";
export { DataTable } from "./components/DataTable";
export type {
  DataTableProps,
  DataTableColumn,
  DataTableDensity,
  DataTableMobileMode,
  MobileFieldRole,
  SortDirection,
} from "./components/DataTable";

// ── B1/B2：超限决策门 + 长任务进度（2026-09-23）─────────────────────
// 设计依据：prd-text-limit-decision-gate-2026-09-23.md
//          design-brief-b1-b2-2026-09-23.md
// 令牌：**零新增色值**，只用 tokens.css 里的 7 个语义别名（§3）。
export { CountBadge } from "./components/CountBadge";
export type { CountBadgeProps } from "./components/CountBadge";
export { GateBanner } from "./components/GateBanner";
export type { GateBannerProps } from "./components/GateBanner";
export {
  StageProgress,
  jobStage,
  JOB_STAGE_LABELS,
} from "./components/StageProgress";
export type { JobStage, StageProgressProps, JobLike } from "./components/StageProgress";
export { JobRow, jobTypeLabel } from "./components/JobRow";
export type { JobRowProps } from "./components/JobRow";
export { TaskCenterDrawer } from "./components/TaskCenterDrawer";
export type { TaskCenterDrawerProps } from "./components/TaskCenterDrawer";
export type { TaskCenterJob } from "./components/JobRow";
export { ContentTooLargeGate } from "./components/ContentTooLargeGate";
export type { ContentTooLargeGateProps } from "./components/ContentTooLargeGate";

// ── 移动端组件族 ────────────────────────────────────────────────────
export { BottomSheet } from "./components/mobile/BottomSheet";
export type { BottomSheetProps } from "./components/mobile/BottomSheet";
export { CollapseGroup, CollapsePanel } from "./components/mobile/CollapsePanel";
export type { CollapseGroupProps, CollapsePanelProps } from "./components/mobile/CollapsePanel";
export { PullToRefresh } from "./components/mobile/PullToRefresh";
export type { PullToRefreshProps } from "./components/mobile/PullToRefresh";
export { InfiniteList } from "./components/mobile/InfiniteList";
export type { InfiniteListProps } from "./components/mobile/InfiniteList";
export { CameraCapture } from "./components/mobile/CameraCapture";
export type { CameraCaptureProps, CapturedFile } from "./components/mobile/CameraCapture";
export { OfflineBanner, useOnlineStatus } from "./components/mobile/OfflineBanner";
export type { OfflineBannerProps } from "./components/mobile/OfflineBanner";
export {
  SyncQueueProvider,
  SyncQueueBadge,
  SyncQueueSheet,
  useSyncQueue,
  // 可选访问器：骨架组件用（无 Provider 时返回 null 而不抛错）
  useSyncQueueOptional,
} from "./components/mobile/SyncQueue";
export type {
  SyncQueueProviderProps,
  SyncQueueBadgeProps,
  SyncQueueSheetProps,
  SyncQueueValue,
  SyncTask,
  SyncTaskStatus,
  EnqueueInput,
} from "./components/mobile/SyncQueue";
export { MobileActionBar } from "./components/mobile/MobileActionBar";
export type { MobileActionBarProps } from "./components/mobile/MobileActionBar";

export { cn } from "./lib/cn";
export { useSession, ROLE_LABELS, roleLabel, displayName } from "./hooks/useSession";
export type { SessionUser, UseSessionResult } from "./hooks/useSession";
export { useAuthGuard } from "./hooks/useAuthGuard";
export type { UseAuthGuardResult } from "./hooks/useAuthGuard";
export { APP_IDS, APP_META, buildAppLinks, otherAppLinks } from "./lib/apps";
export type { AppId, AppLink, AppUrlOverrides } from "./lib/apps";
export { ThemeProvider, ThemeScript, useTheme } from "./theme/ThemeProvider";
export {
  palette,
  paletteDark,
  semanticColors,
  caseGradeColors,
  provenanceStates,
  themeColors,
} from "./theme/colors";
export type { Palette, ColorScale, NeutralScale, ProvenanceState } from "./theme/colors";
export { LoginShell, DEFAULT_DEMO_ACCOUNTS } from "./components/LoginShell";
export type { LoginShellProps, DemoAccount, LoginMode } from "./components/LoginShell";
