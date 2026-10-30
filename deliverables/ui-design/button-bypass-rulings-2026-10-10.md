# 按钮绕过 `Button` · 余下 5 处裁定材料（2026-10-10）

> 出处：门禁 `evidence/verify_buttons.py` **B4**（§6.1「移动端统一 48px」）。
> 状态：**已收敛 6 处（无需拍板）** · **余 5 处待拍板（Q1–Q3）**。

---

## 一、B4 判据与冻结值演变

B4 = 原生 `<button>` / `<a>` 上硬编码 `h-7/h-8/h-9/h-10`（28/32/36/40px）**且带文字** ⇒ 移动端到不了 48px。
（带 `min-h-tap` 的按「**有补偿**」放行 —— 那是项目自己的机制；纯图标按钮归 `verify_tap_targets.py`。）

| 时间 | B4 判红条数 | 说明 |
|---|---|---|
| 09-22 | **10** | 冻结值 |
| 09-27 | **11** | 🚨 **既有漂移**：冻结后从未复评（非 git 仓库 ⇒ `ci.yml` 不触发）⇒ 棘轮要防的「欠账悄悄变大」真发生了 |
| 10-10 | **5** | 本轮把 6 处「刷新」收敛；棘轮同步收紧 **10 → 5** |

---

## 二、✅ 已收敛的 6 处（不需拍板 · 依据是**既有惯例**）

`lawyer/{archives,cases,dispatches,page,reviews}` + `web/knowledge` 各有一个**完全相同**的「刷新」按钮：

```jsx
// 改前
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

// 改后（= admin 6 个页面早已采用的写法，逐字一致）
<Button variant="outline" type="button" onClick={() => void load()} disabled={loading}>
  {loading ? <Spinner size="sm" label={null} /> : <RefreshCw className="h-4 w-4" />}
  刷新
</Button>
```

**为什么这一组可以直接改**：`admin` 的 `audit` / `billing` / `cases` / `complaints` / `compliance` / `dispatches`
**6 个页面**已经把同一个按钮写成 `Button variant="outline"`（默认 `md` = 34px，移动端 `min-h-tap` 48px）
⇒ 属**拉回既有惯例**，不含任何新决策。连带：`lawyer/cases`、`lawyer/page` 补 `Button` import；
`archives`、`dispatches` 的 `cn` 成为未使用 ⇒ 一并删 import。

---

## 三、⚠️ 余下 5 处：为什么**不能**机械替换

### A. `im/chat`「案件」与 `web/qa`「依据」（`h-8`）

```jsx
<button type="button" onClick={…} className={cn(
  "flex h-8 shrink-0 items-center gap-1.5 rounded-r2 border border-line px-2.5",
  "text-body-sm text-ink-600 transition-colors duration-fast hover:bg-surface-hover",
  "xl:hidden"          /* 案件 */   |   "lg:hidden"   /* 依据 */
)}>
  <PanelRight className="h-4 w-4" /> 案件      /* | <BookOpen/> 依据 */
</button>
```

**卡点**：`Button` 的两个候选尺寸**都不是逐字等价**——

| | 原生 | `Button size="sm"` | `Button size="md"` |
|---|---|---|---|
| 高度 | **32px** (`h-8`) | 28px (`h-7`) −4 | 34px (`h-[34px]`) +2 |
| 横向内边距 | `px-2.5` | `px-3` | `px-4` |
| 文字档 | `text-body-sm`(13px) | `text-label`(12px) | `text-body-sm`(13px) |
| 边框色 | `border-line` | `border-line-strong` | `border-line-strong` |

⇒ **文字档位指向 `md`，「紧凑」意图指向 `sm`** —— 两个信号冲突，故须拍板。

> **推荐 `size="sm"`**：这两处是 **`xl:hidden` / `lg:hidden` 的窄屏专属入口**（与同排的图标按钮并排），
> 同高更协调；且 `Button sm` 的 `h-7 + text-label` 搭配在仓库里已被大量使用
> （`admin` 4 处 / `lawyer` 1 处 / 预览页 3 处）。
> **代价**：高度 −4px、边框色 `line` → `line-strong`、文字 13 → 12px。

### B. `im/chat`「发送」与 `web/qa`「发送 / 生成中」（`h-9`）—— **卡在令牌上**

```jsx
<button type="button" onClick={…} disabled={…} className={cn(
  "flex h-9 shrink-0 items-center gap-1.5 rounded-r2 px-3.5 text-body-sm font-semibold text-white",
  "bg-solid-brand transition-opacity duration-fast hover:opacity-90",
  "disabled:cursor-not-allowed disabled:opacity-40",
  "focus-visible:outline-none focus-visible:shadow-focus"
)}>
  <Send className="h-4 w-4" /> {streaming ? "生成中" : "发送"}
</button>
```

🚨 **`--solid-brand` 不是随手写的，它有文档**（`packages/ui/src/tokens.css:172-178`）：

> 「**恒定实底** —— 用于承载白色前景的图标块、进度条、实心徽章。这些颜色**不在 `.dark` 中翻转**：
> 无论浅色还是深色模式，白字/白图标都需要 ≥ 4.5:1 的对比度，因此必须保持深色。
> 注意与语义色阶的区别：**`--brand-600` 会在深色模式下上抬到 `#3A63B0`，而 `--solid-brand` 恒为 `#274C93`**。」

而 `Button` 的 `primary` 变体用的是 **`bg-brand-600`** ⇒ 换过去会**换掉令牌**，并**改变深色模式下的白字对比**。
`bg-solid-brand` 全仓 **8+ 处**在用（含 im 的消息气泡、链接 chip）⇒ 这是**刻意的模式**，不是遗漏。

| 选项 | 做法 | 代价 |
|---|---|---|
| **B1（推荐）** | `Button` 新增 **`solid`** 变体（`bg-solid-brand text-white hover:opacity-90`），两处改用它 | **动共享包**：须同步改规范 §6.1 的「5 变体」与 `verify_buttons.py` 的 `SPEC_VARIANTS = 5` |
| **B2** | 直接用 `variant="primary"` | 零共享包改动，但**深色模式白字对比下降**（`#3A63B0` 上白字 ≈ 4.0:1 **< 4.5:1**） |

**另需核**：§6.1「每屏最多一个 `primary`」—— `im/chat` 与 `web/qa` 各只有这一个主按钮 ⇒ **不冲突**。

### C. `web/qa`「重新生成」（`h-8`）—— **没有对应变体**

```jsx
<button type="button" onClick={onRetry} className="mt-3 inline-flex h-8 items-center gap-1.5 rounded-r2 border border-danger-500/40 bg-surface px-3 text-body-sm font-medium text-danger-600 …">
  <RotateCcw className="h-3.5 w-3.5" /> 重新生成
</button>
```

**卡点**：这是**危险语义的描边按钮**，而 `Button` **没有** `danger` 描边变体 ——
`danger` 是**实底红**（`bg-danger-500 text-white`），换过去视觉重量会从「次要重试」跳到「破坏性主操作」。

| 选项 | 做法 | 代价 |
|---|---|---|
| **C1（推荐）** | `Button` 新增 **`danger-outline`** 变体（`bg-surface text-danger-600 border-danger-500/40 hover:bg-danger-500/10`） | **动共享包 + 规范**（同 B1） |
| **C2** | 用 `variant="outline"`（墨蓝描边） | 零共享包改动，但**丢掉危险语义** |
| **C3** | 保留原生 `<button>` + 加 `min-h-tap` | ⚠️ **不推荐**：B4 的豁免本意是「桌面紧凑、移动端靠 min-height 撑开」；这里是**完全可以用 `Button` 表达**的常规按钮 ⇒ 用豁免等于把欠账藏起来 |

---

## 四、影响面

| 处 | 端 / 路由 | 断点 | 影响桌面？ |
|---|---|---|---|
| 案件 | `im` `/chat` | `xl:hidden`（< 1280） | 否 |
| 依据 | `web` `/qa` | `lg:hidden`（< 1024） | 否 |
| 发送 | `im` `/chat` | 全断点 | **是** |
| 发送 / 生成中 | `web` `/qa` | 全断点 | **是** |
| 重新生成 | `web` `/qa` | 全断点 | **是** |

---

## 五、验收判据（拍板后照此执行）

1. `verify_buttons.py --source-only` 的 **B4 判红条数：5 → 0**（若选 C3，则该条转为「有补偿」不计入判红）。
2. 棘轮 `STATIC_GATES["verify_buttons.py"]` 的 `count_max` 同步收紧到 **0**；
   🚨 **`RATCHET_SELFTEST` 的 R1/R2/R3 夹具绑在同一个冻结值上**（本轮已实测：只改冻结值不改夹具 ⇒ R1/R3 **同时假红**）
   ⇒ 三行须整体跟着改。
3. 若选 **B1 / C1**（新增变体）：须同步 **规范 §6.1** 的变体清单 + `verify_buttons.py` 的 `SPEC_VARIANTS`，
   并按「**设计系统属共享包 ⇒ 改它须拍板**」另出一份裁定材料（含概念图）。
4. 渲染层：`browser-all`（`verify_buttons` 的渲染半）量热区，确认移动端 **48px**。

---

## 六、待拍板清单（已拍板 · 2026-10-11）

- ✅ **Q1**：`案件` / `依据` → **`Button size="sm"`**（28px，材料推荐）。
- ✅ **Q2**：`发送` ×2 → **B2**（改用 `variant="primary"`，零共享包改动）。
  🚨 **材料里的对比度数据有误，已修正**：本节 B 选项原写「B2 深色模式白字 ≈ **4.0:1 < 4.5:1**」，
  **实测 `#3A63B0` 上白字 = 5.85:1`**（`tokens.css:311` 自己的注释也写「5.4:1」）⇒ 两者**都 ≥ 4.5:1**，
  B2 的代价被高估 ⇒ 用户据此改选 B2。
  **代价（改选后仍存在）**：令牌由**恒定的** `--solid-brand`（`#274C93`，8.26:1）换成**随主题翻转的**
  `--brand-600` ⇒ 深色模式下按钮底色由 `#274C93` 抬到 `#3A63B0`（浅色模式两者同值）。
- ✅ **Q3**：`重新生成` → **C1**（新增 `danger-outline` 变体，动共享包 + 规范）。
  ⇒ 已同步：`Button.tsx`（新增变体）· `verify_buttons.py::COMPAT_VARIANTS`（登记）·
  `admin-gap-analysis.md` §6.1 的「扩展变体」段。

---

## 七、已做

### 10-10（6 处「刷新」· 不需回）

- 6 处「刷新」→ `Button variant="outline"`（照抄 admin 既有写法）；连带补/删 2 处 import。
- 验证：`pnpm typecheck`（packages + 四端）**全绿** · B4 判红 **11 → 5** · 棘轮冻结值 **10 → 5** ·
  `run_ci_probes --self-test` 通过 · `--static-audit` **0 个与冻结值不一致**。

### 10-11（余下 5 处按 Q1/Q2/Q3 收口）

- **改动**：`im/chat` 案件·发送 · `web/qa` 依据·发送/生成中·重新生成 → 全部走 `Button`
  （分别 `outline/sm` · 默认 `primary` ×2 · `danger-outline`）；两端补 `Button` import。
- **新增共享包能力**：`Button` 的 `danger-outline` 变体（Q3=C1）。
- **验证**：B4 判红 **5 → 0** · 棘轮 `rc_max/count_max` **1/5 → 0/0**（含 R1–R3 夹具同步）·
  `verify_buttons.py --self-test` **53/53** · `run_ci_probes --self-test` 通过 ·
  `--static-audit` **0 个与冻结值不一致** · `tsc`（packages + 四端）**全绿** ·
  `run_ci_probes` 完整套件 **exit 0**。
