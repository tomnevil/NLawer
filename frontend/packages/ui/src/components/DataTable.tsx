"use client";

import React from "react";
import { cn } from "../lib/cn";
import { Spinner } from "./Spinner";
import { SegmentedControl } from "./SegmentedControl";

export type DataTableDensity = "compact" | "default" | "comfortable";
export type DataTableMobileMode = "auto" | "table" | "card" | "scroll";
export type MobileFieldRole = "primary" | "status" | "normal" | "hidden";
export type SortDirection = "asc" | "desc";

export interface DataTableColumn<T> {
  key: string;
  header: string;
  /** 移动端卡片里的字段名，缺省沿用 header */
  mobileHeader?: string;
  width?: string;
  align?: "left" | "center" | "right";
  /** 案号 / 金额 / 日期 → 等宽字体 + tabular-nums（移动端 11–12px） */
  numeric?: boolean;
  sortable?: boolean;
  /**
   * 移动端卡片中的角色：
   * - primary：卡片标题（取第一个 primary，缺省取第一列）
   * - status：标题右侧徽标
   * - normal：进 2 列栅格
   * - hidden：移动端不展示（进详情页）
   */
  mobile?: MobileFieldRole;
  render?: (value: any, row: T, index: number) => React.ReactNode;
  /** 列设置中默认隐藏 */
  defaultHidden?: boolean;
  /** 列设置中不可隐藏 */
  pinned?: boolean;
}

export interface DataTableProps<T> {
  columns: DataTableColumn<T>[];
  data: T[];
  /** 稳定行标识；缺失时退化为下标 */
  rowKey?: (row: T, index: number) => string;

  // 排序（传 onSortChange 即为受控）
  sortKey?: string | null;
  sortDir?: SortDirection | null;
  onSortChange?: (key: string | null, dir: SortDirection | null) => void;

  // 行选择
  selectable?: boolean;
  selectedKeys?: string[];
  onSelectionChange?: (keys: string[]) => void;
  /** 选中后的批量操作区；传 clear 可一键取消选择 */
  bulkActions?: (selected: T[], clear: () => void) => React.ReactNode;

  // 列设置 / 密度
  columnSettings?: boolean;
  density?: DataTableDensity;
  defaultDensity?: DataTableDensity;
  onDensityChange?: (density: DataTableDensity) => void;

  // 移动端
  mobileMode?: DataTableMobileMode;
  /** 卡片底部的行级操作 */
  rowActions?: (row: T) => React.ReactNode;

  onRowClick?: (row: T) => void;
  loading?: boolean;
  emptyMessage?: string;
  /** 无障碍表格标题 */
  caption?: string;
  className?: string;
}

const DENSITY_CELL: Record<DataTableDensity, string> = {
  compact: "px-3 py-1.5",
  default: "px-4 py-2.5",
  comfortable: "px-4 py-3.5",
};

const DENSITY_CARD: Record<DataTableDensity, string> = {
  compact: "p-2.5",
  default: "p-3.5",
  comfortable: "p-5",
};

const DENSITY_LABEL: Record<DataTableDensity, string> = {
  compact: "紧凑",
  default: "标准",
  comfortable: "宽松",
};

/** 自动判定移动端形态：列少行少直接沿用表格，否则转卡片。 */
function resolveMobileMode(
  requested: DataTableMobileMode,
  columnCount: number,
  rowCount: number
): Exclude<DataTableMobileMode, "auto"> {
  if (requested !== "auto") return requested;
  // 3 列以内且 8 行以内，表格在手机上依然读得动，保留表格以省一次认知转换
  if (columnCount <= 3 && rowCount <= 8) return "table";
  // 4–6 列转卡片；>6 列同样转卡片，但只保留 3 个关键字段
  return "card";
}

function SortGlyph({ dir }: { dir: SortDirection | null }) {
  return (
    <svg
      className={cn(
        "h-3 w-3 shrink-0 transition-colors duration-fast",
        dir ? "text-brand-600" : "text-ink-300"
      )}
      viewBox="0 0 12 12"
      fill="none"
      aria-hidden
    >
      <path
        d="M6 2.5 3.5 5.2h5L6 2.5Z"
        fill="currentColor"
        opacity={dir === "desc" ? 0.3 : 1}
      />
      <path
        d="M6 9.5 8.5 6.8h-5L6 9.5Z"
        fill="currentColor"
        opacity={dir === "asc" ? 0.3 : 1}
      />
    </svg>
  );
}

/** 点击外部关闭的内部 hook（列设置气泡用）。 */
function useDismiss<T extends HTMLElement>(onDismiss: () => void) {
  const ref = React.useRef<T>(null);
  React.useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onDismiss();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onDismiss();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [onDismiss]);
  return ref;
}

/**
 * 数据表格（v2「墨与纸」）。
 *
 * 承担案件列表、证据清单、文书列表等所有高密度数据视图。核心取舍：
 * 1. **移动端自动降级**。列多行多时横向表格在手机上不可用，因此按列数自动
 *    在「表格 / 卡片 / 横向滚动」之间切换；案号、金额、日期始终保持等宽。
 * 2. **排序受控优先**。传 onSortChange 走受控（服务端排序），否则本地排序。
 * 3. **选择态与批量操作一体**。选中即出现批量操作条，避免「先选后找按钮」。
 * 4. **列设置与密度是用户级偏好**，不随筛选重置，由父级持久化。
 */
export function DataTable<T extends Record<string, any>>({
  columns,
  data,
  rowKey,
  sortKey,
  sortDir,
  onSortChange,
  selectable = false,
  selectedKeys,
  onSelectionChange,
  bulkActions,
  columnSettings = false,
  density,
  defaultDensity = "default",
  onDensityChange,
  mobileMode = "auto",
  rowActions,
  onRowClick,
  loading = false,
  emptyMessage = "暂无数据",
  caption,
  className,
}: DataTableProps<T>) {
  // ---- 密度（受控 / 非受控）----
  const [innerDensity, setInnerDensity] = React.useState<DataTableDensity>(defaultDensity);
  const currentDensity = density ?? innerDensity;
  const setDensity = (d: DataTableDensity) => {
    setInnerDensity(d);
    onDensityChange?.(d);
  };

  // ---- 排序（受控 / 非受控）----
  const [innerSort, setInnerSort] = React.useState<{ key: string | null; dir: SortDirection | null }>({
    key: null,
    dir: null,
  });
  const controlled = onSortChange !== undefined;
  const activeSortKey = controlled ? sortKey ?? null : innerSort.key;
  const activeSortDir = controlled ? sortDir ?? null : innerSort.dir;

  const handleSort = (key: string) => {
    // 三态循环：升序 → 降序 → 取消
    let nextDir: SortDirection | null = "asc";
    if (activeSortKey === key) nextDir = activeSortDir === "asc" ? "desc" : null;
    const nextKey = nextDir ? key : null;

    if (controlled) onSortChange?.(nextKey, nextDir);
    else setInnerSort({ key: nextKey, dir: nextDir });
  };

  // ---- 列设置 ----
  const [hiddenKeys, setHiddenKeys] = React.useState<string[]>(() =>
    columns.filter((c) => c.defaultHidden).map((c) => c.key)
  );
  const [settingsOpen, setSettingsOpen] = React.useState(false);
  const settingsRef = useDismiss<HTMLDivElement>(() => setSettingsOpen(false));

  const visibleColumns = React.useMemo(
    () => columns.filter((c) => !hiddenKeys.includes(c.key)),
    [columns, hiddenKeys]
  );

  // ---- 行选择 ----
  const [innerSelected, setInnerSelected] = React.useState<string[]>([]);
  const selected = selectedKeys ?? innerSelected;
  const selectedSet = React.useMemo(() => new Set(selected), [selected]);
  const keyOf = React.useCallback(
    (row: T, index: number) => (rowKey ? rowKey(row, index) : String(index)),
    [rowKey]
  );

  const commitSelection = (keys: string[]) => {
    if (selectedKeys === undefined) setInnerSelected(keys);
    onSelectionChange?.(keys);
  };

  const allSelected = data.length > 0 && data.every((row, i) => selectedSet.has(keyOf(row, i)));
  const someSelected = selected.some((k) => data.some((row, i) => keyOf(row, i) === k));

  const headCheckboxRef = React.useRef<HTMLInputElement>(null);
  React.useEffect(() => {
    if (headCheckboxRef.current) {
      headCheckboxRef.current.indeterminate = someSelected && !allSelected;
    }
  }, [someSelected, allSelected]);

  const toggleAll = () => {
    if (allSelected) commitSelection([]);
    else commitSelection(data.map((row, i) => keyOf(row, i)));
  };

  const toggleRow = (key: string) => {
    if (selectedSet.has(key)) commitSelection(selected.filter((k) => k !== key));
    else commitSelection([...selected, key]);
  };

  const selectedRows = React.useMemo(
    () => data.filter((row, i) => selectedSet.has(keyOf(row, i))),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data, selectedSet, rowKey]
  );

  // ---- 排序后的数据 ----
  const sortedData = React.useMemo(() => {
    if (controlled || !activeSortKey || !activeSortDir) return data;
    const col = columns.find((c) => c.key === activeSortKey);
    const factor = activeSortDir === "asc" ? 1 : -1;
    return [...data].sort((a, b) => {
      const av = a[activeSortKey];
      const bv = b[activeSortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      // 等宽列（案号、金额、日期）按数值/字符串自然比较
      if (col?.numeric && !isNaN(Number(av)) && !isNaN(Number(bv))) {
        return (Number(av) - Number(bv)) * factor;
      }
      return String(av).localeCompare(String(bv), "zh-Hans-CN") * factor;
    });
  }, [data, columns, activeSortKey, activeSortDir, controlled]);

  // ---- 移动端形态 ----
  const effectiveMobile = resolveMobileMode(mobileMode, visibleColumns.length, data.length);
  const cardMode = effectiveMobile === "card";
  const scrollMode = effectiveMobile === "scroll";

  // 卡片模式：>6 列时只保留 3 个关键字段，其余进详情页
  const cardFields = React.useMemo(() => {
    const pool = visibleColumns.filter((c) => (c.mobile ?? "normal") !== "hidden");
    const primary = pool.find((c) => c.mobile === "primary") ?? pool[0];
    const status = pool.find((c) => c.mobile === "status");
    const rest = pool.filter((c) => c !== primary && c !== status);

    if (visibleColumns.length <= 6) return { primary, status, rest };
    // 列过多：仅保留 primary + status + 1 个字段，避免卡片变成第二张表格
    return { primary, status, rest: rest.slice(0, status ? 1 : 2) };
  }, [visibleColumns]);

  // ---- 空态 / 加载态 ----
  if (loading) {
    return (
      <div className={cn("flex items-center justify-center py-14", className)}>
        <Spinner size="md" />
      </div>
    );
  }

  if (data.length === 0) {
    return (
      <div className={cn("flex flex-col items-center justify-center py-14", className)}>
        <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-r3 border border-line bg-surface-subtle">
          <svg className="h-6 w-6 text-ink-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h10" />
          </svg>
        </div>
        <p className="text-body-sm text-ink-500">{emptyMessage}</p>
      </div>
    );
  }

  const cellCls = DENSITY_CELL[currentDensity];

  const toolbar = (selectable && selected.length > 0) || columnSettings || onDensityChange ? (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-line px-4 py-2.5">
      {selectable && selected.length > 0 ? (
        <>
          <span className="text-body-sm text-ink-700">
            已选 <span className="num font-medium text-brand-600">{selected.length}</span> 项
          </span>
          <button
            type="button"
            onClick={() => commitSelection([])}
            className="text-label text-ink-500 underline-offset-2 transition-colors duration-fast hover:text-ink-900 hover:underline"
          >
            取消选择
          </button>
          {bulkActions && (
            <div className="flex flex-wrap items-center gap-2">{bulkActions(selectedRows, () => commitSelection([]))}</div>
          )}
        </>
      ) : (
        <span className="text-caption text-ink-500">
          共 <span className="num text-ink-700">{data.length}</span> 条
        </span>
      )}

      <div className="ml-auto flex items-center gap-2">
        {onDensityChange && (
          <div className="hidden sm:block">
            <SegmentedControl
              size="sm"
              value={currentDensity}
              onChange={(v) => setDensity(v as DataTableDensity)}
              ariaLabel="表格密度"
              options={(["compact", "default", "comfortable"] as DataTableDensity[]).map((d) => ({
                value: d,
                label: DENSITY_LABEL[d],
              }))}
            />
          </div>
        )}

        {columnSettings && (
          <div className="relative" ref={settingsRef}>
            <button
              type="button"
              onClick={() => setSettingsOpen((v) => !v)}
              aria-expanded={settingsOpen}
              aria-haspopup="true"
              className={cn(
                "tap-ghost inline-flex h-8 min-h-tap sm:min-h-0 items-center gap-1.5 rounded-r2 border border-line px-2.5",
                "text-label text-ink-600 transition-colors duration-fast hover:bg-surface-hover hover:text-ink-900"
              )}
            >
              <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 4.5h6M9 12h6M9 19.5h6" />
              </svg>
              列设置
            </button>

            {settingsOpen && (
              <div
                role="menu"
                className="absolute right-0 z-modal mt-1 w-52 animate-fade-in rounded-r3 border border-line bg-surface p-1.5 shadow-s2"
              >
                {columns.map((col) => {
                  const checked = !hiddenKeys.includes(col.key);
                  return (
                    <label
                      key={col.key}
                      className={cn(
                        "flex items-center gap-2.5 rounded-r2 px-2 py-1.5 text-body-sm text-ink-700",
                        col.pinned ? "cursor-not-allowed opacity-60" : "cursor-pointer hover:bg-surface-hover"
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={col.pinned}
                        onChange={() => {
                          if (col.pinned) return;
                          setHiddenKeys((prev) =>
                            checked ? [...prev, col.key] : prev.filter((k) => k !== col.key)
                          );
                        }}
                        className="h-3.5 w-3.5 rounded-[3px] border-line text-brand-600 focus:ring-2 focus:ring-brand-500/30"
                      />
                      {col.header}
                      {col.pinned && <span className="ml-auto text-caption text-ink-400">固定</span>}
                    </label>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  ) : null;

  const tableEl = (
    <div className={cn("scroll-thin overflow-x-auto", scrollMode && "relative")}>
      <table className="w-full border-collapse">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr className="bg-surface-subtle">
            {selectable && (
              <th scope="col" className={cn("w-10 border-b border-line text-center", cellCls)}>
                <input
                  ref={headCheckboxRef}
                  type="checkbox"
                  checked={allSelected}
                  onChange={toggleAll}
                  aria-label="全选"
                  className="h-3.5 w-3.5 rounded-[3px] border-line text-brand-600 focus:ring-2 focus:ring-brand-500/30"
                />
              </th>
            )}

            {visibleColumns.map((col, colIndex) => {
              const isActive = activeSortKey === col.key;
              const dir = isActive ? activeSortDir : null;
              const frozen = scrollMode && colIndex === 0;

              return (
                <th
                  key={col.key}
                  scope="col"
                  style={col.width ? { width: col.width } : undefined}
                  aria-sort={isActive ? (dir === "asc" ? "ascending" : "descending") : "none"}
                  className={cn(
                    "border-b border-line text-label font-medium text-ink-500",
                    cellCls,
                    col.align === "center" && "text-center",
                    col.align === "right" && "text-right",
                    !col.align && "text-left",
                    frozen && "sticky left-0 z-10 border-r border-line bg-surface-subtle"
                  )}
                >
                  {col.sortable ? (
                    <button
                      type="button"
                      onClick={() => handleSort(col.key)}
                      className={cn(
                        "inline-flex items-center gap-1 transition-colors duration-fast hover:text-ink-900",
                        col.align === "right" && "flex-row-reverse",
                        isActive && "text-ink-900"
                      )}
                    >
                      {col.header}
                      <SortGlyph dir={dir} />
                    </button>
                  ) : (
                    col.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>

        <tbody>
          {sortedData.map((row, rowIndex) => {
            const key = keyOf(row, rowIndex);
            const isSelected = selectedSet.has(key);

            return (
              <tr
                key={key}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                className={cn(
                  "border-b border-line transition-colors duration-fast",
                  isSelected ? "bg-brand-500/[0.07]" : "hover:bg-brand-500/5",
                  onRowClick && "cursor-pointer"
                )}
              >
                {selectable && (
                  <td className={cn("text-center", cellCls)} onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => toggleRow(key)}
                      aria-label={`选择第 ${rowIndex + 1} 行`}
                      className="h-3.5 w-3.5 rounded-[3px] border-line text-brand-600 focus:ring-2 focus:ring-brand-500/30"
                    />
                  </td>
                )}

                {visibleColumns.map((col, colIndex) => {
                  const frozen = scrollMode && colIndex === 0;
                  return (
                    <td
                      key={col.key}
                      className={cn(
                        "text-body-sm text-ink-700",
                        cellCls,
                        col.numeric && "num",
                        col.align === "center" && "text-center",
                        col.align === "right" && "text-right",
                        frozen && "sticky left-0 z-10 border-r border-line bg-surface"
                      )}
                    >
                      {col.render ? col.render(row[col.key], row, rowIndex) : row[col.key]}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>

      {/* 横向滚动提示：仅在滚动模式下于窄屏出现，避免用户不知道右侧还有列 */}
      {scrollMode && (
        <>
          <div
            aria-hidden
            className="pointer-events-none absolute inset-y-0 right-0 w-8 sm:hidden"
            style={{
              background:
                "linear-gradient(to right, rgb(var(--surface-card) / 0), rgb(var(--surface-card) / 0.9))",
            }}
          />
          <p className="border-t border-line bg-surface-subtle px-4 py-1.5 text-caption text-ink-500 sm:hidden">
            ← 左右滑动查看全部列
          </p>
        </>
      )}
    </div>
  );

  // 卡片模式：桌面仍是表格，窄屏换成卡片列表
  if (!cardMode) {
    return (
      <div className={cn("overflow-hidden rounded-r3 border border-line bg-surface", className)}>
        {toolbar}
        {tableEl}
      </div>
    );
  }

  const cardCellCls = DENSITY_CARD[currentDensity];
  const { primary, status, rest } = cardFields;

  return (
    <div className={cn("overflow-hidden rounded-r3 border border-line bg-surface", className)}>
      {toolbar}

      {/* 桌面：表格 */}
      <div className="hidden sm:block">{tableEl}</div>

      {/* 窄屏：卡片列表 */}
      <ul className="divide-y divide-line sm:hidden">
        {sortedData.map((row, rowIndex) => {
          const key = keyOf(row, rowIndex);
          const isSelected = selectedSet.has(key);

          return (
            <li
              key={key}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={cn(
                cardCellCls,
                isSelected && "bg-brand-500/[0.07]",
                onRowClick && "active:bg-surface-hover"
              )}
            >
              <div className="flex items-start gap-2.5">
                {selectable && (
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => toggleRow(key)}
                    onClick={(e) => e.stopPropagation()}
                    aria-label={`选择第 ${rowIndex + 1} 行`}
                    className="mt-0.5 h-4 w-4 shrink-0 rounded-[3px] border-line text-brand-600"
                  />
                )}

                <div className="min-w-0 flex-1">
                  {/* 标题行：主标识 + 状态徽标 */}
                  <div className="flex items-start justify-between gap-2">
                    <span className="min-w-0 flex-1 truncate text-body font-medium text-ink-900">
                      {primary
                        ? primary.render
                          ? primary.render(row[primary.key], row, rowIndex)
                          : row[primary.key]
                        : `第 ${rowIndex + 1} 行`}
                    </span>
                    {status && (
                      <span className="shrink-0">
                        {status.render ? status.render(row[status.key], row, rowIndex) : row[status.key]}
                      </span>
                    )}
                  </div>

                  {/* 其余字段：两列栅格 */}
                  {rest.length > 0 && (
                    <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1.5">
                      {rest.map((col) => (
                        <div key={col.key} className="min-w-0">
                          <dt className="text-caption text-ink-500">
                            {col.mobileHeader ?? col.header}
                          </dt>
                          <dd
                            className={cn(
                              "truncate text-body-sm text-ink-800",
                              col.numeric && "num text-[11px] leading-5"
                            )}
                          >
                            {col.render ? col.render(row[col.key], row, rowIndex) : row[col.key]}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  )}

                  {rowActions && (
                    <div
                      className="mt-2.5 flex flex-wrap items-center gap-2 border-t border-line pt-2.5"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {rowActions(row)}
                    </div>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

DataTable.displayName = "DataTable";
