# 裁定材料 · #75：33 处 `animate-spin` 的动效归属

> **状态：✅ 已裁定（2026-09-25）· 已落地 · 已实测闭合（2026-09-26）。**
> 裁定：走 **③**（先收敛 → 再建令牌）· 收敛范围 **(b)**（连 `packages/ui` 收到只剩 `Spinner.tsx`）·
> 次序 **先收敛后建令牌**。
> 🚨 **但 §3 表格里「reduce 下切透明度脉冲」那一行被实测否掉了**：它写「**可复用** `--dur-pulse`」，
> 而 `--dur-pulse` 在 reduce 块里**已被归零为 `0ms`**（`tokens.css:394`）⇒ 复用它会得到
> **静止**，不是脉冲。二次裁定：**reduce 下停成静止的弧**（与骨架屏同口径）。详见 §9。
> **2026-09-26 补充**：运行时实测（四端 · 真后端 · 隔离库）**rc=0**，
> `测量 24 组（加载态 8 组）；不合格 0 组`；并把 `verify_reduced_motion.py` 的
> **R1c 由「只报不判」升为判红**（见 **§9.7** —— 那次「留待拍板」就是本裁定）。
> 数据口径与 `evidence/verify_motion_tokens.py` 的 **R7** 完全一致
> （同一 `SKIP_DIRS`，已排除 `.next` 构建产物）⇒ 收敛前的 **33 处**对得上。

---

## 1. 结论（先说）

原来摆在你面前的是二选一（① 建令牌 / ② 明文豁免）。**本轮实测后我认为两个都不该直接选**，理由是：

1. **33 处全是真·加载指示**，没有一处是装饰性动效 ⇒ 「该不该管」的问题**不存在**，
   真正的问题是「**用什么形态**」。
2. **32/33 处在「loading 分支 + a11y」上都齐**（`role="status"` / `aria-hidden` / `aria-label`）⇒
   产品侧对加载态的处理**质量不低**，不是「随手加的动画」。
3. 🚨 **但共享原语被绕过了**：`packages/ui` 的 `<Spinner>` 全仓**只有 4 处真实引用**，
   而 **25 个页面在手搓 `animate-spin`** ⇒ 这才是更值得先动的地方。

**推荐路线（新增的第三条）**：
**③ 先收敛（25 处页面手搓 → 0）→ 再给共享原语建 `--dur-spin`，且 reduced-motion 形态是「透明度脉冲」而非归零**
⇒ 之后把 **A4 扩到 `animate-spin`** 时，**上线当天不会红**。

---

## 2. 33 处的真实构成

| 归属 | 处数 | 明细 |
|---|---|---|
| **共享包 `packages/ui`**（= 加载**原语**） | **8** | `Spinner.tsx:27`（根）· `Button.tsx:80` · `Table.tsx:50` · `DataTable.tsx:302` · `AppLayout.tsx:197` · `mobile/InfiniteList.tsx:106` · `mobile/PullToRefresh.tsx:106` · `mobile/CameraCapture.tsx:296` |
| **页面**（= 手搓，**绕过了原语**） | **25** | admin **13** · lawyer **9** · im **2** · web **1** |

页面侧 25 处的**两种写法**（各占一类）：

- **15 处** `<Loader2 className="h-4 w-4 animate-spin" />`（lucide 图标，三元里当 loading 分支）
- **9 处** `<RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />`（刷新按钮，loading 时才转）
- **1 处** `im/app/(app)/layout.tsx:55` 手写 `<svg className="h-6 w-6 animate-spin text-brand-500">`

⚠️ 注意这 25 处**完全重复**了共享原语已有的能力 —— `Spinner.tsx` 本身就带 `sizeStyles` / `colorStyles`。
**「原语存在但被绕过」**是比「动效没令牌化」更靠前的根因。

---

## 3. 关键设计判断：加载指示**该不该**响应 reduced-motion？

这一条决定了 ① 和 ② 谁对，所以必须先把事实摆清（**不能靠直觉**）。

**规范侧（严格合规）**：
- WCAG **2.3.3「Animation from Interactions」** 属 **AAA** 级，且**自带例外**：
  「*unless the animation is essential to the functionality or the information being conveyed*」。
- ⇒ 严格按条文，**旋转型加载指示很可能落进「必要」例外** ⇒ **不算违规**。
  **这就是选项 ② 的立论基础，它并非无理。**

**实践侧（通行做法）**：
- 但无障碍实践界的通行做法是：**保留**加载指示器（删掉它会伤害可用性 —— 用户会以为界面卡死），
  同时**把「旋转」这个形态换成「透明度脉冲」**。
- 依据方向：`prefers-reduced-motion` 的医学指向是**前庭失调 / 眩晕 / 恶心**，而**持续旋转**是最典型的触发形态之一；
  参见 W3C 技术 **C39**（*Using prefers-reduced-motion to Prevent Motion*）与 MDN 的 `prefers-reduced-motion`。
- ⚠️ **被替换的是「旋转」，不是「指示器」** —— 「必要」的是**指示器**，不是**旋转这个形态**。

**⇒ 由此得出本项目的形态选择（而不是「管不管」的选择）**：

| 形态 | reduce 下的表现 | 评价 |
|---|---|---|
| 现状（内置 `animate-spin`） | **照转不停** | ❌ 完全无视偏好 |
| 朴素令牌 `--dur-spin: 0ms` | **静止的弧** | ⚠️ 可接受（仍是指示器），但比现状更弱地传达了「正在加载」 |
| **`animate-spin-soft`：reduce 下切「透明度脉冲」** | **呼吸式明暗** | ✅ **通行做法**；且**与本项目已有的 `--dur-pulse` + `animate-pulse-soft` 天然衔接**（可直接复用该形态） |

---

## 4. 三个选项（含本轮新增的 ③）

| # | 选项 | 满足 reduced-motion | 代价 | 风险 |
|---|---|---|---|---|
| ① | 建 `--dur-spin` + `animate-spin-soft`（**归零**） | 部分（静止弧） | 中 | 33 处要逐处换类；**加载感变弱** |
| ② | §4.4 明文豁免「功能型加载指示」 | ❌ 不满足 | **零** | ① 与「实践侧通行做法」相反；② **口子一开，`animate-pulse` 也会来援引它**（两者同为「功能型」）⇒ A4 的边界被架空 |
| **③** | **先收敛 25 处手搓 → 再用 `--dur-spin`，reduce 形态 = 透明度脉冲**（复用 `--dur-pulse`） | ✅ | 中（含一次收敛） | 收敛要动 25 个页面文件；但**这 25 处本来就在重复原语**，属**技术债**而非新增成本 |

**⚠️ ② 的致命处**：`animate-pulse`（骨架屏）和 `animate-spin`（加载指示）**在「功能型」这个维度上是同一类**。
一旦以「功能型豁免」放过 spin，**A4 就不能再拦 pulse** ⇒ 本日刚补的 A4 会被同一套论证反向拆掉。
**要么两者都归零，要么两者都不归零 —— 不能只豁免其中一个。**

---

## 5. 推荐 ③ —— 为什么

1. **它让 A4 可以合法扩张**。收敛后 `animate-spin` 只剩在 `packages/ui` 的加载原语里（8 处 →
   收敛后可再降到 **1–2 处**，即 `Spinner.tsx` 内部），判据可以写成
   **「`animate-spin` 只允许出现在 `packages/ui` 的加载原语内；页面一律走 `<Spinner>` / `Button loading`」**
   ⇒ 边界**清晰、可判、零假红**。
2. **它同时还掉一笔真实技术债**：25 个页面各写一遍 spinner = **同一件事 25 份实现**，
   改样式要改 25 处。收敛的收益**独立于动效问题**成立。
3. **它复用已有令牌**：`--dur-pulse` / `--pulse-iter` / `animate-pulse-soft` 都已存在
   （`tokens.css:389-397`），reduce 形态**不必新造**。

---

## 6. 🚨 顺带发现的两个问题（独立于 #75，建议单列）

### (a) `tokens.css:388` 的注释是**过度声明**

原文：

```
/* 降低动效偏好：令牌归零即可让全部动画与过渡停下，无需 !important */
```

**「全部」不成立** —— Tailwind **内置** `animate-pulse` / `animate-bounce` / `animate-ping` / `animate-spin`
**根本不读 `--dur-*`**。这正是 #41 与 A4 的根因。
⇒ 建议把注释改成「**经令牌的**动画与过渡会停下；内置动画工具类不受影响（见 A4）」。

### (b) `--dur-pulse` 的注释与「场景名」口径

`--dur-pulse: 1.6s` 注释写着「骨架屏**与**打字光标的循环周期」⇒ 它是**合并令牌**，
改它会连带改骨架屏（这就是 R1「打字光标规范 1.2s vs 实现 1.6s」不能直接改值的原因）。
若本轮要为 spin 新增令牌，**注意别再制造第二个合并令牌**。

---

## 7. ✅ 裁示结果（2026-09-25）

| # | 问题 | 裁示 |
|---|---|---|
| 1 | 走 ① / ② / ③？ | **③**（先收敛 → 再建令牌） |
| 2 | 收敛范围取哪一档？ | **(b)** —— 连 `packages/ui` 内部也收敛到只剩 `Spinner.tsx` |
| 3 | reduce 下的形态？ | **①先选「透明度脉冲」**（按本文推荐）⇒ 🚨 **实测否掉，改判「归零静止」**（见 §9.2） |
| 4 | 收敛与建令牌的次序？ | **先收敛后建令牌**（判据上线当天不红） |

**顺带两项（§6）的处理**：
- (a) `tokens.css:388` 的过度声明注释 ⇒ **已改正**（见 §9.4）。
- (b) 「别再制造第二个合并令牌」⇒ **已遵守**：`--dur-spin` 是**独立**令牌，**不**与 `--dur-pulse` 合并。

---

## 8. 附：本材料的可复现口径

- 33 处：`backend/.venv/Scripts/python.exe evidence/verify_motion_tokens.py` → R7 行（`exit 0`）。
- 分布与 a11y 判定：临时脚本按 **同一 `SKIP_DIRS`** 扫 `frontend/**/*.tsx`，对每处取
  **向上 25 行 + 向下 3 行**的窗口，匹配 loading 词表（`load|pending|submitting|busy|…`）
  与 a11y 词表（`role="status"|aria-busy|aria-label|sr-only|aria-live`）。
- `<Spinner>` 引用数：`grep -rn "<Spinner" frontend --include=*.tsx`（排除 `node_modules`）⇒ **5 行**，
  其中 1 行是 `Spinner.tsx` 自身定义 ⇒ **真实引用 4 处**。

---

## 9. 落地记录（2026-09-25）

### 9.1 第一步：收敛（33 → 1）

用脚本做**逐条断言命中数**的替换 —— 33 处里绝大多数是**逐字相同**的字符串，
手改的风险是**漏改且不报错**（此时 A4 还没接 `spin`，漏了不会有任何提示）。

| 原始写法 | 处数 | 收敛为 | a11y 取法 |
|---|---|---|---|
| 手写 `<svg … role="status" aria-label="加载中">`（`Table` / `DataTable`） | 2 | `<Spinner size="md" />` | 默认 label |
| 手写 `<svg … role="status" aria-label="正在恢复会话">`（`AppLayout` / im 外壳） | 2 | `<Spinner size="md" label="正在恢复会话" />` | **保留原 label** |
| 手写 `<svg … aria-hidden>`（`Button` / `InfiniteList` / `PullToRefresh` / `CameraCapture`） | 4 | `<Spinner … label={null} />` | 装饰 |
| `<Loader2 className="h-4 w-4 animate-spin" />` | 14 | `<Spinner size="sm" label={null} />` | 装饰 |
| `<Loader2 className="h-5 w-5 animate-spin" />` | 1 | `<Spinner className="h-5 w-5" label={null} />` | 装饰 |
| `<RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />`（刷新按钮） | 7 | `{loading ? <Spinner size="sm" label={null} /> : <RefreshCw className="h-4 w-4" />}` | 装饰 |
| 同上 `h-3.5`，在 `leftIcon=` **属性位** | 1 | `leftIcon={loading ? … : <RefreshCw className="h-3.5 w-3.5" />}` | 装饰 |
| `<RefreshCw className="h-4 w-4 animate-spin" />`（im 新建咨询） | 1 | `<Spinner size="sm" label={null} />` | 装饰 |

**a11y 语义逐处照搬，不统一取默认值**：收敛前 33 处里有的写了 `role="status" aria-label`、
有的写了 `aria-hidden`、有的什么都没写（lucide 图标）。一律取默认 `label="加载中"`
会让「紧邻『加载中…』文字」这类位置**被播报两次**。
⇒ `Spinner` 因此新增 `label?: string | null`（`null` = 纯装饰，渲染 `aria-hidden`），
并把 props 扩到 `React.ComponentPropsWithoutRef<"svg">`（`Button` 那处要传 `aria-hidden`）。

**收敛形态取「loading 时换图标」**（7 处刷新按钮）：与仓内主流一致
（`{loading ? <Loader2/> : <RefreshCw/>}` 本来就是最常见的写法），
而不是保留 `<RefreshCw>` 原地加 `animate-spin` —— 后者**仍是手搓**，达不到收敛目的。

### 9.2 第二步的岔口：reduce 形态**被实测否掉**

本文 §3 的表格推荐「reduce 下切**透明度脉冲**」，理由写的是
「与本项目已有的 `--dur-pulse` + `animate-pulse-soft` 天然衔接（**可直接复用该形态**）」。

🚨 **这条理由不成立**：`tokens.css` 的 reduce 块里写着 **`--dur-pulse: 0ms`** / `--pulse-iter: 1`
⇒ 「复用 `--dur-pulse`」得到的是**静止**，不是脉冲。要实现真正的脉冲，必须：

1. 新增一个**不归零**的 reduce 令牌 + 新 keyframe；
2. **改 `verify_reduced_motion.py` 的 R1d**（它明文要求「reduce 档下**项目自定义动画不得仍在跑**」）；
3. 在 §4.4 写明这是**项目唯一一条「reduce 下仍在动」的动画**（依据 W3C C39）。

⇒ 二次裁定为 **归零静止**：reduce 下 `--dur-spin: 0ms` / `--spin-iter: 1` ⇒ **静止的弧**。
与骨架屏同一口径（**要么都归零、要么都不归零**），**零额外改动**；
`verify_reduced_motion.py` 的 **R1c**（原把「内置 spin 在 reduce 下仍跑」列为「留待拍板」）随之消解。

**保留的判断**：**归零的是「旋转」，不是「指示器」** —— 静止的弧仍是可见的加载指示，
只是不再转。停掉指示器才会让用户以为界面卡死。

### 9.3 令牌与工具类

| 层 | 改动 |
|---|---|
| `tokens.css` `:root` | 新增 `--dur-spin: 1s`（**独立**令牌，**不**与 `--dur-pulse` 合并）+ `--spin-iter: infinite` |
| `tokens.css` reduce 块 | 新增 `--dur-spin: 0ms` + `--spin-iter: 1` |
| `tailwind.preset.ts` | 新增 keyframes `spin-soft` + `animation["spin-soft"] = "spin-soft var(--dur-spin) linear var(--spin-iter)"` |
| `Spinner.tsx` | `animate-spin` → **`animate-spin-soft`** |

⚠️ **关键帧名必须字面出现在 animation 值里**：Tailwind 靠 `value.includes(name)` 决定要不要
输出 `@keyframes`。写成 `var(--spin-name)` 这种「运行时切名字」的写法会让**关键帧根本不产出**
（静态读源码完全看不出来）⇒ 这也是「脉冲方案」在实现层被否的原因之一。

### 9.4 门禁：A4 扩到 `spin`

- `BUILTIN_ANIM_RE`：`\banimate-(?:pulse|bounce|ping)(?![\w-])` → **加 `spin`**。
- **R7 从「只报不判」改为「存档项」**（判据已并入 A4），但**不删** ——
  A4 只能说「有就红」，说不出「33 → 0 这件事有没有被回退」；R7 是**正面计数**。
- 自检新增 **2 臂**：`Q18b`（裸旋转类必须红）+ `Q19b`（`animate-spin-soft` 不得红）。
  ⚠️ `Q19b` 比 `Q19` 更要紧：`animate-spin-soft` 是本次**新造**的合法类名，
  右边界写错会让 A4 **把我们的修复判成缺陷**（自伤）⇒ 自检 **25 → 27**。
- §4.4 补一行 `| 加载指示（旋转） | 1s 循环 | ease-linear |`，并把 `--dur-spin` 的行注释
  写成**逐字等于该场景名** ⇒ A1 的配对从 **3 行 → 4 行**（新令牌**真正被判到**，
  而不是「加了一行说明、其实没人管」）。
  ⚠️ 缓动写 `ease-linear`（**不是**裸 `linear`）：A2 的正则只认
  `ease-(out|in|in-out|linear)` 与 `cubic-bezier(...)`，裸 `linear` 会走
  `A2:缓动无法解析` **判红**。

### 9.5 🚨 顺带踩到的同族坑：**判据的文本扫描会把「描述规则的文字」也当规则对象**

A4 按**源码文本**扫（`iter_sources()` 不做注释剥离）。把 `spin` 纳入 A4 后，
我在 `Spinner.tsx` / `tailwind.preset.ts` 的注释里**写出了那个被禁类名**做说明
⇒ **注释自己把这两份文件判红**。

这与坑 71（「写**关于转义**的文档，文档自己中了转义的坑」）**同型**：
**文档/注释一旦提到规则对象，就会成为规则的对象。**

**处理**：源码注释里**把类名拆开写**（`animate-` + `spin`），并写明**为什么拆开**。
⚠️ 仓内既有约定本来就是如此 —— 实测已禁的 `pulse` / `bounce` / `ping` 在
`frontend/**` 的注释里**也 0 命中**。新加入者必须遵守同一条，否则 A4 上线即红。

### 9.6 实测闭合

| 项 | 结果 |
|---|---|
| `animate-spin` 精确命中（右边界 `(?![\w-])`） | **33 → 0** |
| `pnpm typecheck` | `EXIT=0`（25 个改动文件） |
| `pnpm build`（四端） | `EXIT=0`（四端 `BUILD_ID` 全部刷新） |
| `verify_motion_tokens.py --self-test` | **27/27** |
| `verify_motion_tokens.py`（真判据） | `exit 0` · A1 配对 **4 行** · A4 绿 · R7 **0 处** |
| `verify_reduced_motion.py`（**四端**真判据 · 临时编排） | **rc=0** · `测量 24 组（加载态 8 组）；不合格 0 组` · 197.5s |
| `verify_breakpoints.py` / `verify_adjacent_targets.py`（四端复跑） | **rc=0** / **rc=0**（#39 · #72 保持闭合） |
| **完整门禁** `run_ci_probes.py`（2026-09-26） | **`EXIT=0`** · 阶段 1 **27 条全绿**（16 门禁内 + 9 仅自检 + 2 仪器与仓库一致）· 阶段 2 **21/21** · 阶段 3 **6/6**（棘轮，3m3s） |

**运行时这一行才是关键**：源码级判据（A4 / R7）只能证明「源码里没有内置旋转类」，
**证不了「reduce 档下真的停住了」**。而 `reduce` 档下四端 **24 组全部 `reduce=0`**、
其中**加载态 8 组全绿** —— 加载态正是 `<Spinner>`（`spin-soft`）与骨架屏（`pulse-soft`）
**唯一会出现**的地方。

> **与改动前的对照**（同一门禁、同一采样方式）：改动前加载态抓到 **im 骨架屏 2 组红**
> + **R1c 4 处「只报不判」**（lawyer 刷新按钮 / admin 全屏 spinner）。
> 现在两者都消失 —— 不是「判据放宽了」，而是那两类元素**已经不存在**（都换成了经令牌的实现）。
> 存证：`evidence/four_app_rm_2026-09-26.txt`（本次全量日志）。

### 9.7 门禁侧的连带落地：`R1c` 由「只报不判」**升为判红**

`verify_reduced_motion.py` 的 **R1c** 原本把「内置 `spin` 在 reduce 下仍跑」列为
**只报不判、留待拍板**（理由：加载指示是否属「必须归零的动效」是设计取舍）。
**#75 就是那次拍板** ⇒ 理由消失，判据必须跟上：

| 改动 | 为什么 |
|---|---|
| **R1c → 判红** | 拍板后「设计取舍」不再成立。⚠️ 它禁的是**内置旋转类**这个**实现**，**不是**「不许有加载指示」——`<Spinner>` 在 reduce 下要**停在原地**当指示器 |
| **`pending`（只报不判）通道整体移除** | R1c 是唯一的 pending 项 ⇒ 通道会变成**永远为空**。留一个永远为空的「待拍板」通道，就是留一个**零判据的口子**（让人以为「这里报出来的有人管」） |
| **`REDUCE_VAR_NAMES` 补 `--dur-spin` / `--spin-iter`** | R2 是**运行时**断言。新令牌不登记 ⇒ 它的归零**无人断言、且不报错**（A1 只做静态比对） |
| **`PROJECT_ANIMS` 补 `spin-soft`** | 漏了它，令牌失效时会落到兜底文案「仍有动画 `spin-soft`」，**归因信息丢了**（读的人看不出是令牌机制坏了） |
| 自检 **25 → 28**（浏览器臂 10 → 13） | 补 **Q5b**（`spin-soft` 归零后**不得**判红 · **阴性对照防自伤**）/ **Q5c**（令牌失效时必须报红 · 证明新动画真的进了 `PROJECT_ANIMS`）/ **Q10b**（新令牌**真的进了 R2 覆盖**） |

⚠️ **A4（静态）与 R1c（运行时）不是重复**，是两条独立判据：
A4 扫 `frontend/**` **源码文本**（连注释一起扫）⇒ 第三方 CSS、扫描范围外的文件它看不见；
R1c 量**真页面的计算值** ⇒ 只在注释里出现的类名它看不见。**两者互补**。

🚨 **本次自己犯的错（已改）**：写「移除 pending 收尾块」那一步时，Edit 的
**新旧文本方向写反了**，把块**又插入了一遍**（文件里出现两份 `if pending_all:`）。
⇒ 教训：**删除类 Edit 必须回头复核「改完还有没有这个词」**（`grep -c` 一下），
不能只看工具回了 `Successfully edited`。
