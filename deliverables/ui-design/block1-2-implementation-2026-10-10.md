# 块 1 + 块 2 第 20 项 · 施工记录（2026-10-10）

> 授权：用户「全部按建议推进」。范围 = `design-audit.md` 的**块 1（共享包 3 项）** + **块 2 第 20 项**。
> 本文是**复核用**：先看「可见变化」，再看「门禁改动」，最后是验证口径。

---

## 一、⚠️ 请先复核：三处**可见变化**

| # | 位置 | 改前 | 改后 | 影响面 |
|---|---|---|---|---|
| 1 | **案件等级徽章** | `S→danger`(朱砂) · `A→pending`(琥珀) · `B→info`(蓝) · `C→neutral`(灰底) | `S→gold`(金) · `A→danger`(朱砂) · `B→brand`(墨蓝) · `C→ink`(ink-400 底) | admin / lawyer / im **三个端的等级徽章** |
| 2 | **焦点环** | `2px` + `brand-500` @ **30/40/25/20%**（4 种） | `0 0 0 3px rgba(54,96,176,.22)`（= `--brand-500` @ 22%） | **全站**（48 处 / 29 文件，含 `packages/ui` ⇒ 四端） |
| 3 | **登录页品牌标题** | `40px` · 行高 1.25 · 字距 −0.025em · **字重继承(400)** | `38px` · 行高 1.25 · 字距 −0.01em · **字重 600** | `LoginShell` 品牌区 |

**为什么改**：#1 是 **§2.4「案件等级」** 的既有规范（规范给的是**颜色**，代码用了另一套 ⇒ 门禁 `verify_grade_badges.py` **G1 0/25 全不符**；
`Badge.tsx` 的 `gold` 变体注释自己就写着「S 级案件」，组件预览页也把 S 级**正确展示成金色** ⇒ 属「组件层有正确答案、调用方用了另一个」）。
#2 是 **§4.3 末行**明文「焦点环统一：`0 0 0 3px rgba(54,96,176,.22)`」。
#3 是 `text-[40px]` **不在** §03 的 17 档阶梯里（最近档 `display` = 38px）⇒ 行高/字重/字距全部脱离档位。

**顺带清掉的死代码**：`Button.tsx` / `LoginShell.tsx` 共 2 处 `focus-visible:ring-offset-*` ——
Tailwind 的 `ring-offset-*` 只给 `--tw-ring-offset-width` 赋值、由 `ring-*` 消费，换成 `shadow-focus` 后即失效。

---

## 二、块 1 三项明细

### 第 23 项 · `text-[10px]` → `text-caption`
6 处全在 `packages/ui`（`CitationChip:39` `CitationPanel:92` `NotificationCenter:826` `SegmentedControl:112`
`TabBar:73` `mobile/CameraCapture:221`）。**保留原有 `leading-*`**（只改字号，不动行高，避免容器位移）。
另把预览页 3 处刻度标签（`{step}`/`{r}`/`{s}`）也一致化（探针本不计缺陷，但留着会被当范例抄）。

### 第 22 项 · `Badge` 补 `brand`/`ink` + `GRADE_TONE` 收归单一来源
- **新增唯一来源** `packages/ui/src/lib/gradeTone.ts`；`index.ts` 导出；im 的 `lib/imApi.ts` 改为
  `export { GRADE_TONE } from "@nlaw/ui"`（**转发不算定义**，定义点 **6 份 → 1 份**）。
- 5 个 app 页删本地 `const GRADE_TONE` 改 import；`dispatches/page.tsx` 的 `BadgeProps` 随之成为未使用 ⇒ **连带删 import**。
- `Badge.tsx` 新增两个变体：
  - `brand` = `bg-brand-600/15 text-brand-600 border-brand-600/30`（§2.4 的 B 级 = `--brand-600` 墨蓝，**与 `primary` 的 brand-500 不是同一档**）
  - `ink` = `bg-ink-400/15 text-ink-600 border-ink-400/30`（**底色**取 ink-400 满足 §2.4；**文字**取 ink-600 ——
    绕开 `tokens.css` 对 `--ink-400` 的「占位符/图标/装饰，**禁用于正文**」标注，即 G5 那条**只报不判**的规范 tension）
  - `normalize()` **不再把 `ink` 吞成 `neutral`**；预览页唯一一处 `variant="ink"`（「必填」）改为 `variant="neutral"`
    —— 它原本就被吞成 neutral ⇒ **零外观漂移**。
- ⚠️ **字母通道（G3 25/25 ✓）未动** —— 它本来就是达标的干净对照。

### 第 26 项 · `LoginShell` 品牌标题 → `text-display`
`h1` 由 `font-serif text-[40px] leading-[1.25] tracking-tight` 改为 `font-serif text-display`
（`display` 档 = 38px / lh 1.25 / fw 600 / ls −0.01em）。**保留 `font-serif`**（字族，非字号档）。

### 第 20 项 · 48 处焦点环 → `focus*:shadow-focus`
7 种形态全部收敛（`focus:ring-2 ring-brand-500/30`×26 · `focus-visible:`×12 · `/40`×4 · `focus-within:`×2 ·
含 `ring-inset`×2 · `/20`×1 · `danger-500/30`×1）。
- `danger-500/30`（危险态）也一并统一：**规范未声明例外**，门禁把它列为「规范缺口（只报不判）」；
  若保留，F3 会因为「环宽集合只剩 `ring-2`=2px」而**稳定判红**。
- 手法：**可审计脚本**（干跑逐处打印 before/after → `--apply` → 用完即删），共 48 处 / 29 文件。

---

## 三、🚨 本轮最重要的产出：**两个门禁自身缺口**（都会「把合规报成没测成」）

> 共同形状：**判据只覆盖「旧写法」⇒ 一旦把实现改成规范要求的新写法，判据就反向误报。**

### 缺口 ① `verify_font_scale.py` —— 清零后棘轮**取不到数**
它**只在有缺陷时**才印 `✗ 源码层缺陷 N 条`；干净路径走 `✓ 未发现 T1 缺陷`。
而运行器的 `declared` 棘轮用正则 `缺陷 (\d+) 条` 取数 ⇒ **失配 ⇒ `count_from()` 返回 `None` ⇒ 判红「取不到条数」**
（**坑 50 同形**：宁可红，不许静默）。⇒ 第 23 项清零后，这条棘轮已处于「**永久取不到数**」状态。
**修**：改为**无条件**打印 `── 源码层：缺陷 N 条 ──`，与 `verify_buttons.py` / `verify_focus_ring.py` 的既有约定对齐。

### 缺口 ② `verify_focus_ring.py` —— 两处
- **F5 的正则认不出 `focus:shadow-focus`**：原正则 `(?:^|[\s"'`])shadow-focus(?:[\s"'`]|$)` 要求前后是
  空白/引号，而**规范用法带变体前缀**（前缀那个 `:` 不在字符类里）⇒ 按规范改完 48 处，它**仍报「调用点 0 处」**。
  **修**：提为**单一常量** `SHADOW_FOCUS_RE`（消掉 `self_test` 里的第二份副本 —— 两处各写一份，改一处忘一处就会「自检绿、实测红」）
  并允许任意变体前缀。
- **`cov` 只数 `focus:ring-*`**：全站改用令牌后 `cov == 0` ⇒ `summary_exit` 判 **exit 2「覆盖率 0 / 环境问题」**
  ⇒ **把「完全合规」报成「没测成」**。**修**：`cov += focus_shadow`（令牌调用点本身就是覆盖率）。
- 另修一句自相矛盾的提示文案（F3 的「覆盖率 0」与汇总的「覆盖率 48」并存）。

**两个缺口都补了回归臂**：F5 的 `U5b`（`focus-visible:shadow-focus`）/ `U5c`（`focus:shadow-focus`）/
`U5d`（阴性对照）/ `U8`（**只用令牌、无 `ring-*` ⇒ 绿且 `cov > 0`**）。

---

## 四、棘轮收紧（`run_ci_probes.py` · `STATIC_GATES`）

| 探针 | 旧（09-22 实测） | 新（10-10 实测） |
|---|---|---|
| `verify_font_scale.py` | rc 1 / 条数 **6** | **0 / 0** |
| `verify_grade_badges.py` | rc 1 / 条数 **5** | **0 / 0** |
| `verify_focus_ring.py` | rc 1 / 条数 **3** | **0 / 0** |

🚨 **棘轮自检夹具同时绑 `rc_max` 与 `count_max`**：R8/R9 用的就是 `grade_badges`，两个上限一改，
夹具里 `rc=1` 立刻「超限」⇒ **R9 假红**。⇒ 两臂的 rc 一并给 0（**只让条数变化**），并写进注释：
**改冻结值必须同步这两行**。

---

## 五、验证口径（本轮）

| 项 | 结果 |
|---|---|
| `pnpm typecheck`（packages + 四端 `tsc`） | **两轮全绿**（改 `packages/ui` 后、改 48 处焦点环后各一轮） |
| `ruff check evidence/` | **All checks passed** |
| `verify_grade_badges.py --self-test` | **36/36** |
| `verify_focus_ring.py --self-test` | **62/62**（新增 4 臂） |
| `run_ci_probes.py --self-test` | **通过**（棘轮 9 臂 + 接线 2 臂 + 登记表 5 臂 + `ci.yml` 4 臂 + 退出码 5 臂） |
| `run_ci_probes.py --static-audit` | 6 个静态门禁 **1 红**；**其余 5 个与新冻结值完全一致** |
| `verify_grade_badges.py --source-only` | 定义点 **1** 处 · 四级全部「与 §2.4 同前缀 ✓」· **✗ 0 条** |
| `verify_focus_ring.py --source-only` | 覆盖率 **48** · `shadow-focus` 命中 **48** · **缺陷 0 条** · `exit 0` |

### ⚠️ 两条**未覆盖**，必须说清
1. **渲染层没跑**：`grade_badges` 的 **G1/G3/G4**（等级徽章底色/字母/文字色）与 `focus_ring` 的 **F6+**
   都需要浏览器 ⇒ 属 `browser-all`（≈22–25 min）。**本轮结论只到源码层。**
2. **`verify_buttons.py` 冻结 10 → 实测 11**（静态审计判红）：11 处「带文字」的 `<button>` 硬编码 `h-8`/`h-9`，
   **全部落在我本轮未触碰的文件**（`im/chat`、`lawyer/{page,reviews}`、`web/{knowledge,qa}`）⇒ **既有漂移**，非本轮引入。
   冻结值测于 09-22 之后未复评（**非 git 仓库 ⇒ `ci.yml` 从不触发**，全量回归 ~20 min 也少跑）
   ⇒ **棘轮要防的「欠账悄悄变大」真实发生了**。属**第 21 项**（任务 #99）。

---

## 六、余下

| 项 | 内容 | 任务 |
|---|---|---|
| 块 2 第 21 项 | 11 处硬编码 `h-8/h-9` → `Button` size（逐处判 variant，保留 `onClick`/`disabled`/`type`/`aria`） | **#99** |
| 块 3 第 9 项 | 产品页改接 `ProvenanceBlock`（已建已导出，产品页 0 调用 ⇒ **是接线不是新建**） | **#98** |
