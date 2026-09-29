# 阶段一实施记录 · 设计基建

> 2026-09-16 · 对应规范 `design-spec.md` 第 09 节「阶段一 · 设计基建（P0）」
> 状态：**已完成并通过验收**

---

## 一、验收结论

| 验收项 | 命令 | 结果 |
|---|---|---|
| 四端构建 | `next build` × 4 | ✓ web 10 页 / admin 5 页 / im 5 页 / lawyer 9 页，全部退出码 0 |
| 前端安全门禁 | `pnpm audit --audit-level high` | ✓ `No known vulnerabilities found`，退出码 0 |
| 后端静态门禁 | `ruff check backend` | ✓ `All checks passed!`，退出码 0 |
| 跨包类型检查 | `tsc -p tsconfig.packages.json --noEmit` | ✓ 退出码 0 |
| **深色模式零 `!important`** | 编译产物断言 | ✓ **产物 CSS 中 `!important` 计数 = 0** |
| 深色模式仅变量驱动 | 编译产物断言 | ✓ `.dark{}` 块含 89 条 CSS 变量、**0 条工具类覆盖** |
| 新语义类可用 | 编译产物断言 | ✓ 24 个语义类（`bg-surface-subtle` / `w-sidebar` / `z-tabbar` / `text-body-sm` …）全部生成 |

web 端产物 CSS 体积 45,080 字节。

---

## 二、改动清单

### 新增（4 个源码文件 + 本文档）

| 文件 | 作用 |
|---|---|
| `packages/ui/src/tokens.css` | **设计令牌唯一事实来源**。中性阶 ink、品牌阶 brand、六个语义阶（ai/verified/pending/danger/gold/info）、角色令牌、恒定实底、圆角阴影、字体三栈、动效、布局层级、移动端令牌。`:root` 与 `.dark` 两套值 |
| `packages/ui/src/bridge.css` | **存量兼容层**（过渡件）。把「一个类名承载两种语义」的工具类重新指向变量，并压平多色渐变 |
| `packages/ui/src/components/AppShell.tsx` | **四端统一骨架**。桌面 240px 墨色侧栏（可整栏收起）+ 56px 顶栏 + 应用切换器 + 三档内容宽度 + 移动抽屉 + 移动 Tab Bar 双形态 |
| `packages/ui/src/components/TabBar.tsx` | 移动端底部导航。≤5 项，48px 触控目标，红/琥珀双色角标，安全区适配 |

### 重写（19 个文件）

| 分组 | 文件 |
|---|---|
| 令牌链路 | `tailwind.preset.ts`、`theme/colors.ts`、`styles.css`、`index.ts`、`package.json` |
| 骨架 | `components/Sidebar.tsx`、`components/Header.tsx` |
| 组件（去 `dark:` 硬编码） | `Button` `Badge` `Card` `Input` `KpiCard` `Table` `Modal` `Toast` `Alert` `Skeleton` `Spinner` `EmptyState` |
| 主题 | `theme/ThemeProvider.tsx` |
| 四端入口 | `apps/{web,lawyer,admin,im}/app/globals.css` |

### 微调（2 个文件）

- `apps/web/app/knowledge/page.tsx`：1 处 `text-slate-300` → `text-slate-400`（前者在深色模式下会变暗至不可见）
- `LoginShell.tsx`：3 处类名（品牌区文案改 `text-white/75`、版权行 `text-white/55`、Logo 改 `text-link`）

---

## 三、四个关键设计决策

### 1. 为什么令牌用 RGB 三元组而不是 hex

```css
--ink-50: 247 248 249;   /* #F7F8F9 */
```
配合 Tailwind 的 `rgb(var(--ink-50) / <alpha-value>)`，`bg-brand-600/10` 这类透明度修饰符才能生效。
若直接用 hex，`/10` 会被静默忽略——而存量代码里有 30 余处 `bg-indigo-500/25`、`border-slate-200/60` 之类的写法，忽略透明度会造成大面积视觉错误。

hex 值写在行尾注释里，与规范表格一一对应，便于比对。
纯 CSS 场景写 `rgb(var(--ink-50))`。

### 2. 为什么中性阶在深色模式下「镜像翻转」

数字表示**层级强度**而非固定明度：`--ink-900` 恒定代表「最高强调文本」，
浅色模式下是近黑 `#14181D`，深色模式下是近白 `#F2F4F6`。

这样做的收益是：存量代码里 227 处 `bg-slate-50` / `text-slate-900` / `border-slate-200`
**一行都不用改**，就自动获得了正确的深色模式表现。这正是「零 `!important`」得以成立的原因。

唯一例外是 `--ink-950`，它恒定代表「最深的墨」（`#0B0E12`），用作深色模式页面底色。

### 3. 为什么 `bridge.css` 用 `html ` 前缀

`html .bg-white` 的特异性是 (0,1,1)，高于 Tailwind 工具类的 (0,1,0)，
因此**不依赖样式表顺序**即可稳定生效。

这一点很关键：本项目的 PostCSS 只配了 `tailwindcss` + `autoprefixer`，**没有 `postcss-import`**，
`@import` 是由 Next.js 的 css-loader 解析的。也就是说 Tailwind 先展开 `@tailwind utilities`，
之后 css-loader 才把 `tokens.css` / `bridge.css` / `styles.css` 内联进来——内联位置由打包器决定，
不保证在工具类之后。靠 `html ` 前缀提特异性，两种内联顺序下结果都一致。

同时，`hover:` / `focus:` 变体的特异性是 (0,2,0)，仍能正常覆盖本层，交互态不受影响。

### 4. 为什么要一组「恒定实底」令牌

```css
--solid-brand: 39 76 147;  /* #274C93 恒为深色，深色模式下不翻转 */
```

承载白色前景的图标块、进度条、实心徽章，在两种模式下都需要 ≥4.5:1 的对比度，
因此**必须保持深色**。这与 `--brand-600` 不同——后者在深色模式下会上抬到 `#3A63B0`
（因为它还要兼作深色背景上的描边与按钮填充）。

---

## 四、两个「静默失效」陷阱及处理

### 1. 色系重映射的边界

`tailwind.preset.ts` 把存量色系名整体指向新令牌，但有三个类名**不能**重映射，
因为同一个词承担了两种语义，已单独处理：

| 类名 | 冲突 | 处理 |
|---|---|---|
| `bg-white` | 需要随主题变暗（卡片表面），但 `text-white` 必须恒为白（侧栏、深色渐变、主按钮） | `bridge.css` 按前缀区分：`bg-white*` 映射到 `--surface-card`，`text-white` 保持字面白 |
| `text-indigo-*` | 作为链接需随主题变亮，作为品牌填充需保持墨蓝 | `bridge.css` 映射到 `--link` / `--link-hover` / `--link-muted`；`bg-` / `border-` 走品牌阶 |
| `text-indigo-200/300` | 出现在墨色侧栏上，语义是「深色底上的次要前景」，必须恒为浅色 | `bridge.css` 映射到 `--sidebar-fg-muted` |

### 2. 多色渐变会让白字失去对比度

存量页面有 5 组 `bg-gradient-to-r` 图标块渐变，例如 `from-cyan-600 to-teal-500`。
若按色系重映射，深色模式下渐变尾端会变成 `#5C9ED6`（亮青），
而上面的图标是白色 → 对比度跌到 2.7:1。

处理方式：用**复合选择器**精确匹配这 5 组渐变（同时要求两个类名，特异性 0,2,1），
压平为单色实底：

```css
html .from-indigo-600.to-cyan-500 { /* → 平铺 --solid-brand */ }
html .from-violet-600.to-indigo-500 { /* → 平铺 --solid-ai */ }
html .from-cyan-600.to-teal-500 { /* → 平铺 --solid-info */ }
html .from-amber-500.to-orange-500 { /* → 平铺 --solid-gold */ }
html .from-emerald-600.to-teal-500 { /* → 平铺 --solid-verified */ }
```

复合选择器不会误伤其他 `from-*` 用法——深色 hero 的
`from-indigo-950 via-indigo-900 to-indigo-800` 完好保留。
顺带实现了规范第 02.5 节「撤下渐变」的要求。

---

## 五、修复的功能性缺陷

| 缺陷 | 状态 |
|---|---|
| 04 · `theme/colors.ts` 键名与色值完全错位（`emerald` 实为靛蓝、`teal` 为青、`cyan` 为紫，导致 `KpiCard color="emerald"` 渲染出蓝色） | **已修复**。改为导出 `palette` / `paletteDark` / `semanticColors` / `provenanceStates`，`KpiCard` 的 `color` 改为语义色调（`brand` / `verified` / `pending` / `danger` / `info` / `ai` / `gold`），旧名作为兼容别名保留但已指向正确色系 |
| 05 · 深色模式是补丁（20 余条 `!important` 硬覆盖） | **已修复**。产物 CSS 中 `!important` 计数为 0，`.dark` 块只剩 89 条变量 |
| 03 · 品牌色失去层级（靛蓝→青蓝渐变铺满） | **已收敛**。5 组图标块渐变压平为单色实底，登录左栏改墨蓝渐变，主按钮去渐变改实底 |
| 09 · 未考虑移动端 | **已起步**。`AppShell` 内置移动抽屉 + `TabBar`，令牌层已有 `--tap: 48px` 与安全区变量 |

---

## 六、已知遗留（不属阶段一范围）

1. **`bridge.css` 是过渡件**，阶段三页面重构完成后应删除。判断标准：全站不再出现 `bg-white` / `text-indigo-*` / 5 组渐变类名。
2. **Inter 字体尚未加载**。字体栈已写好，但未接入 `next/font`；中文回退系统字体，西文暂时回退 `system-ui`。属规范第 10.2 节准备清单，建议与 Noto Serif SC 子集化一并处理。
3. **`bg-white` 卡片落在深色 hero 上**：web 与 lawyer 首页仍是「深色渐变底 + 白卡片门户」，深色模式下卡片变暗后与底色区分度下降。该页在阶段三会被工作台取代，暂不处理。
4. **`CitationDrawer` 仍是遮挡正文的弹窗**（缺陷 07），阶段三随问答页三栏化一并改造。
5. **`AppShell` 尚未接入任何页面**。它已通过类型检查与构建，但没有页面使用；阶段三逐一接入。
6. **`apps/im` 的「640px 固定卡片」布局未动**，属阶段三。
7. **未做真机截图比对**。规范第 10.2 节要求 375 / 390 / 430px 三档真机验收，当前环境无浏览器自动化能力，需人工验收。

---

## 七、下一步

按规范第 09 节，进入**阶段二 · 组件层**：

- 改造已完成 14 个组件中的 12 个（`LoginShell` 仅做了配色迁移，其 2×2 演示账号 + 角色分段切换待做）
- 新增 P0 组件：`DataTable`、`CitationPanel`、`ProvenanceBadge`、`Timeline`
- 新增 P1 组件：`SegmentedControl`、`Drawer`、`FilterBar`、`Pagination`
- 新增移动端组件：`BottomSheet`、`CollapsePanel`、`PullToRefresh`、`InfiniteList`、`CameraCapture`、`OfflineBanner`、`SyncQueue`
- 建立 `components-preview` 页面用于逐一验收视觉

推进范围仍按决策 16：**先做 web 端试点**。
