# 令牌档位专项 · 裁定材料（2026-10-29）

> **类型**：裁定材料 → **裁定 + 施工记录**
> **状态**：✅ **已定稿并施工**（2026-10-29 16:20，用户以 `cont` 采纳推荐 **①A + ②A**）。
> ✅ **附带发现（§4 通知面板）也已按方向① 建判据并进 CI**（2026-10-29 17:5x，用户选 `1`，见 §11.5）。
> 本文件**不是规范** —— 要求身份仍在 `design-spec.md` §2.1/§2.3 与 `verify_contrast.py` 的 T4；
> 这里记的是**为什么这么改**与**改了什么**（§9、§10、§11.5）。
> **目标**：让决定在 **3 分钟内**能做完 —— 所以先给结论，再给证据。

---

## 1. 要拍的两件事（先看这张）

| # | 位置 | 现状 | 建议 | 影响面 |
|---|---|---|---|---|
| **①** | `Badge` 的 `verified` / `pending` 两变体 | 浅色 **4.49 / 4.16**（AA 门槛 4.5） | `-600` → **`-700`**（→ 5.91 / 5.93） | 共享包，全站徽章 |
| **②** | 深色档把 `--brand-600/700` 当**文本色**用 | 深色 **1.98 ~ 3.05** | 换成角色令牌 **`--link`**（→ 6.83 / 7.43） | 共享包 3 处 + `DataTable` 1 处 + 四端 2 处 |

两件事**根因不同**，必须分开拍：

- ① 是**浅色档**：令牌注释自己写着「文本用色（6.0:1）」，但那个 6.0 是**压在白底上**标的；
  徽章的真实底色是 `verified-500/15` 染色 ⇒ 掉到 4.49。
- ② 是**深色档**：`--brand-600` 在深色是 `#3A63B0`，注释明写「**主按钮填充**（可承载白字 5.4:1）」
  —— 它是**填充色**，不是文本色。深色档唯一能承载文本的亮蓝是 `--brand-400` 与角色令牌 `--link`。

---

## 2. 事实①：徽章文字压在**染色底**上不够

`Badge.tsx:42-43` 写的是 `bg-verified-500/15 text-verified-600`（`pending` 同形）。
**期望值一律从 `tokens.css` 现读现算**，不写死 hex。

| 徽章实际所在底色 | `verified-600` | `verified-700` | `pending-600` | `pending-700` |
|---|---|---|---|---|
| `surface-card` `#FFFFFF` | 5.02 ✓ | 6.61 ✓ | 4.66 ✓ | 6.64 ✓ |
| `surface-page` `#F7F8F9` | 4.74 ✓ | 6.25 ✓ | **4.40 ✗** | 6.28 ✓ |
| `surface-subtle` `#EFF1F3` | **4.49 ✗** | 5.91 ✓ | **4.16 ✗** | 5.93 ✓ |
| 深色 `surface-card` `#14181D` | 6.56 ✓ | 8.24 ✓ | 6.58 ✓ | 8.58 ✓ |

**模型已被实测反向验证**：渲染探针在浅色量到 **4.49 / 4.16**，本表在 `surface-subtle` 底上
算出 **4.49 / 4.16**、`-700` 档 **5.91 / 5.93** —— **逐位一致**。
⇒ 徽章的真实底色是 `surface-subtle`（表头/列表底），不是白卡。

**换档是四格单调改善**：浅色 +1.59 / +1.98，深色 +1.68 / +2.00。
深色档 `verified-700 = #82D6B8`、`pending-700 = #E8C480` 比 `-600` **更亮**，在暗底上只会更好。

---

## 3. 事实②：深色档的 `--brand-600/700` 是填充色

| 文本色（深色档） | on `surface-card` | on `surface-page` | on `surface-subtle` |
|---|---|---|---|
| `--brand-500` `#4E77C4` | 4.04 △ | 4.38 △ | 3.75 △ |
| `--brand-600` `#3A63B0` | **3.05 ✗** | 3.31 △ | **2.83 ✗** |
| `--brand-700` `#274C93` | **2.16 ✗** | 2.34 ✗ | **2.00 ✗** |
| `--link` `#8AA8E0`（角色令牌） | **7.43 ✓** | 7.83 ✓ | 6.83 ✓ |

AA 门槛 4.5（WCAG 1.4.3，正文 <18.66px 粗体 / <24px）。
⇒ 深色档里 `text-brand-600` / `text-brand-700` **当文本用一律不合格**，无一例外。

---

## 4. 🚨 附带发现：通知面板**从未被任何渲染探针量过**

`NotificationCenter.tsx:835` 是 `{open && !isMobile && (…)}` —— 面板**默认关闭**，
里面的 `header / listBody / footer`（含 729 / 738 / 793 三处）**只有在点开铃铛后才会挂载**。

`verify_contrast.py` 的 T4 是「打开页面 → 量 DOM」，**不点铃铛** ⇒ 这三处**结构上量不到**。
所以上一轮 T4 在 `lawyer/notifications`（10-14 已纳入 26 页）报 0 缺陷，
**不等于通知面板是干净的** —— 面板根本不在被量的 DOM 里。

⚠️ 这正是项目里那条硬提醒的又一例：**「0 条」≠「全站 0 条」，先问「触发过/采到了/答的是这个问题吗」。**

---

## 5. 同族全量清单（源码层实测，非推测）

判据：深色档 `text-brand-600/700` 无 `dark:` 覆盖，且**是文字**（非图标/勾选标记）。

### 5.1 需修 —— 文本，6 处

| # | 位置 | 字号 | 深色对比度 | 探针覆盖 |
|---|---|---|---|---|
| 1 | `packages/ui/…/NotificationCenter.tsx:729` 未读角标 | `caption` | **1.98**（渲染实测 ≈1.75） | ❌ 面板关闭态 |
| 2 | `packages/ui/…/NotificationCenter.tsx:738` 「全部已读」 | `body-sm` | **3.05** | ❌ 同上 |
| 3 | `packages/ui/…/NotificationCenter.tsx:793` 「查看全部」 | `body-sm` | **3.05** | ❌ 同上 |
| 4 | `packages/ui/…/DataTable.tsx:328` 「已选 N 项」 | `num font-medium` | **3.05** | ⚠️ 需勾选后才渲染 |
| 5 | `apps/lawyer/…/cases/[id]/page.tsx:1211`（含 `hover:text-brand-700`） | `caption` | **3.05** / hover **2.16** | ❌ 详情页不在 T4 页表 |
| 6 | `apps/lawyer/…/notifications/page.tsx:255` | `body-sm` | **3.05** | ⚠️ 页面在 T4 页表内，但**本条未被报**（见 §4） |

### 5.2 非文本，**不在本次范围**（3:1 阈值，`brand-600` 在 card 上 3.05 勉强过、在 subtle 上 2.83 不过）

`DataTable.tsx:406 / 435 / 504 / 596`（复选框勾）· `DataTable.tsx:111`（排序箭头）·
`NotificationCenter.tsx:150`（通知类型图标 tone）· `apps/im/app/(app)/page.tsx:313`（图标）。

⇒ 属 **WCAG 1.4.11** 的另一条线，建议**另开一单**，别混进这次拍板。

---

## 6. 选项与代价

### ① 徽章

| 选项 | 结果 | 代价 |
|---|---|---|
| **A. `-600` → `-700`**（推荐） | 4.49→5.91 · 4.16→5.93，**两模式四底色全单调改善** | 浅色徽章文字更深一点（视觉更「重」）；但 4.49 本就低于 AA，这是**修缺陷**不是换风格 |
| B. 改染色底（`/15` → `/10` 或换底色） | 也能过 | 徽章失去语义染色 ⇒ 三态视觉区分只剩边框；且「语义色 + `/15`」是 §2.4 双通道编码的同族写法，改它会把视觉语言撕开一道口子 |
| C. 不动 | —— | 不可行：4.49 < 4.5，是**不合格**，不是「差不多」 |

### ② 深色 `brand` 当文本

| 选项 | 结果 | 代价 |
|---|---|---|
| **A. 换角色令牌 `--link`**（推荐） | 深色 6.83 / 7.43；浅色 7.04（原 9.09） | 浅色下强调色由 `#1E3C77`/`#274C93` 统一成 `#274C93` ⇒ 角标与按钮的**层次差消失**（9.09→7.04 略淡）。**已有 2 处落地先例**：`web/compliance:240`、`lawyer/cases/[id]:1089/1423` |
| B. `text-brand-700 dark:text-link`（仅深色覆盖） | 浅色保持 9.09，深色 6.83 | 每处写两遍；「深色档要记得加 `dark:`」是**靠人记的约定** —— 本项目已证明这类约定会漂移（7 个 BOM 文件静默失明、`browser-all` 静默 exit 2 达 9 天）。且源码判据**判不出「忘了加 `dark:`」**，除非再写一条判据 |
| C. 改深色 `--brand-600/700` 提亮 | 一处改、全仓受益，最省事 | **不可行**：`--brand-600` 在深色是**主按钮填充**（注释「可承载白字 5.4:1」）⇒ 提亮会让白字掉到 4.5 以下，**直接拆掉主按钮**；`--brand-700` 是主按钮 hover 填充，同理 |
| D. 新增 `--brand-text`（浅 `#274C93` / 深 `#8AA8E0`） | 语义最准 | `--link` 的定义就是「**链接与强调文本**」（`tokens.css:134`）⇒ 新增等于造**第二个同义令牌**，设计令牌判据里会出现两条同值定义，属重复 |

**推荐：①A + ②A。** 理由：两案都**不新增令牌、不改填充色、不靠人记约定**，
且都是「用规范里已有的、角色语义正确的那个令牌」——①用同色阶的更深一档，②用角色令牌 `--link`。

---

## 7. 落地清单（定稿后一次做完）

```diff
# ① 徽章（1 文件 2 行）
- verified: "bg-verified-500/15 text-verified-600 border-verified-500/30",
+ verified: "bg-verified-500/15 text-verified-700 border-verified-500/30",
- pending:  "bg-pending-500/15  text-pending-600  border-pending-500/30",
+ pending:  "bg-pending-500/15  text-pending-700  border-pending-500/30",

# ② 深色 brand 当文本（6 处，均为 text-brand-600|700 → text-link）
NotificationCenter.tsx:729 / 738 / 793 · DataTable.tsx:328
lawyer/cases/[id]:1211（含 hover:text-brand-700 → hover:text-link-hover）
lawyer/notifications:255
```

⚠️ `hover:text-brand-700` 必须一并换成 **`hover:text-link-hover`**（深色 `#B6C9EE`），
否则 hover 态仍是 2.16 —— 比默认态更差。

---

## 8. 复现（不靠记忆，跑这两条）

```bash
# 闭式验算（令牌值从 tokens.css 现读；含「换底色复现实测」那一段）
python %TEMP%\nl_shared_pkg_contrast.py

# 同族全量扫描（源码层，排除 node_modules/.next）
python %TEMP%\nl_brand_fill_as_text.py
```

⚠️ 这两个脚本是**一次性验算工具**，没有登记进 `evidence/`（不构成门禁）。
若本材料定稿后要**长期防回归**，需另建判据 —— 但那属于「② 的判据缺口」，
见 §4：**渲染级判据结构上覆盖不到关闭态面板**，得先解决「怎么触发面板」这个前提。

---

## 9. 施工记录（2026-10-29 16:20 起）

### 9.1 定稿时补到的两条**加强证据**（定稿前没有，落地时才查到）

1. **6 处改动里 4 处浅色「零变化」**：浅色档 `--brand-600` 与 `--link` **同为 `#274C93`**
   （`tokens.css:49` / `:134`），`--brand-700` 与 `--link-hover` **同为 `#1E3C77`**（`:50` / `:135`）。
   ⇒ `text-brand-600 → text-link`、`hover:text-brand-700 → hover:text-link-hover` 在浅色下
   **像素级不变**，只修深色。**唯一有浅色变化的是 §5.1 第 1 行角标**（`brand-700` 9.09 → `link` 7.04）。
2. **`-700` 不是新档位，`-600` 才是异类**：全仓已有 5 处 `text-verified-700` / `text-pending-700`，
   其中 `admin/complaints:372`、`mobile/OfflineBanner:80/91` 就是
   `bg-*-500/10 text-*-700` 的**同形写法**。⇒ 徽章的 `-600` 是全仓唯一的例外，
   本次改动是**收敛到既有惯例**，不是引入新样式。

### 9.2 改动清单（8 行 + 4 处判据镜像）

| 文件 | 改动 |
|---|---|
| `packages/ui/src/components/Badge.tsx:42` | `text-verified-600` → **`text-verified-700`** |
| `packages/ui/src/components/Badge.tsx:44` | `text-pending-600` → **`text-pending-700`** |
| `packages/ui/src/components/NotificationCenter.tsx:729` | `text-brand-700` → **`text-link`** |
| `packages/ui/src/components/NotificationCenter.tsx:738` | `text-brand-600` → **`text-link`** |
| `packages/ui/src/components/NotificationCenter.tsx:793` | `text-brand-600` → **`text-link`** |
| `packages/ui/src/components/DataTable.tsx:328` | `text-brand-600` → **`text-link`** |
| `apps/lawyer/…/cases/[id]/page.tsx:1211` | `text-brand-600 hover:text-brand-700` → **`text-link hover:text-link-hover`** |
| `apps/lawyer/…/notifications/page.tsx:255` | `text-brand-600` → **`text-link`** |

🚨 **判据镜像必须同步**（`BADGE_VARIANT_FG_TOKEN` 是从 `Badge.tsx` **手抄**的，
不同步 ⇒ G4 当场判「文字色 ≠ 声明的令牌」—— 这是记忆里踩过 3 次的坑）：

- `verify_grade_badges.py`：`BADGE_VARIANT_FG_TOKEN` 的 `verified`/`pending` → `-700`；
  `TOKEN_SNAPSHOT` 加 `verified-700` / `pending-700`（否则 G4 报「令牌读不到」）；
  自检夹具 `LIGHT_TOKENS` / `DARK_TOKENS` 补两档（否则未来加臂会假红）。

### 9.3 验证

| 项 | 结果 |
|---|---|
| `verify_grade_badges.py --self-test` | **36/36 通过** |
| `verify_grade_badges.py --source-only`（G2） | ✓ 单一来源 · 四等级映射未漂移 |
| `verify_ink400_body.py`（全仓 147 文件） | **0 条**（R1/R2/R3 全跑） |
| `ruff check evidence/` | All checks passed |

⚠️ **未验证（诚实标注）**：
- ~~**G1/G3/G4 的渲染半边**需要四端 `next start` + 后端 + 种子库，本轮没起服务 ⇒ 只跑了源码层。~~
  ✅ **2026-10-29 17:00–17:22 已补跑，见 §10**（四端重建 + 隔离库 + 真后端，163.1s）。
- **§4 的通知面板** —— ✅ **2026-10-29 17:36–17:39 已补跑，见 §11**（点开铃铛 + 造未读数据）。
  ✅ **判据也已建**（`verify_notification_panel.py` + 独立 job `browser-ncpanel`，`JOB_RC=0`，见 §11.5）。

---

## 11. 通知面板：三处首次真被量到

### 11.1 怎么触发的

面板是 `{open && !isMobile && (…)}` ⇒ 点 `button[aria-haspopup="dialog"]` 才挂载。
脚本复用 `verify_contrast` 的 `login` / `MEASURE_JS` / `_premise` / `judge_render`，
只在测量前点一下铃铛（`%TEMP%\nl_nc_panel_contrast.py`，只起 lawyer 一端）。

### 11.2 🚨 前两次跑都是**假的绿** —— 两个测量陷阱

| # | 陷阱 | 现象 | 为什么会骗人 |
|---|---|---|---|
| **1** | **面板是空态** | 面板挂载了、判了「0 缺陷」，但角标（729）与「全部已读」（738）**根本不在 DOM 里** | 种子库对律师**不产生任何通知** ⇒ `unread.total=0` ⇒ 角标不渲染；`unreadItems=0` ⇒ 按钮不渲染。**「0 条」= 没量到，不是没缺陷** |
| **2** | **按钮文字是直接文本节点** | 「全部已读」取不到（`null`） | 选择器只认 `children.length===0` 的**叶子元素**，而该按钮同级有 `<CheckIcon>` 子元素 ⇒ 被跳过。**改成按 `button.textContent` 找**才拿到 |

修法：脚本里 `seed_unread()` 直接往 `notifications` 表插 3 条 `is_read=0`（`tenant_id='firm_hlw'`），
按钮改用 `querySelectorAll('button')` 按 `textContent` 匹配。

### 11.3 结果：三处全部 ✓

| 位置 | 浅色 | 深色 | 判定 |
|---|---|---|---|
| 未读角标 `:729`（`caption` 11px/500） | `rgb(39,76,147)` = `--link` | `rgb(138,168,224)` = `--link` | ✓ |
| 全部已读 `:738`（`body-sm` 13px） | 同上 | 同上 | ✓ |
| 查看全部通知 `:793`（`body-sm` 13px） | 同上 | 同上 | ✓ |

- **深色 `--brand-700` 残留指纹：0 个**（两档都是 0）
- **面板打开态整页判定**：49 个文本元素 · **缺陷 0** · 区间内 0（浅/深各一次）

⇒ §5.1 的 6 处**全部**在渲染层确认修好（前 5 处由 §10 的 G4 覆盖，第 6 处由本节覆盖）。

### 11.4 三个方向与裁定结果

要接就得回答「**怎么稳定触发**」，而 §11.2 的第 1 条说明：**不造未读数据 ⇒ 判据永远量到空**。
这已经不是「加一行 goto」的问题了，需要判据自己承担「造业务数据」的副作用 —— 而
`run_browser_job` 的种子是**共享**的（四端 26 页跑同一份库），在探针里写库会污染后续探针。

| 方向 | 代价 | 副作用 |
|---|---|---|
| **① 探针自带独立库**（自己造未读，不碰共享库） | 成本 ≈ 一条新 job（复制一套生命周期） | **无** |
| ② 给产品种子加「律师有 3 条未读」 | 最省事（改 `seed/business.py`） | 改动**产品种子**，且会推翻所有依赖「未读为 0」的既有断言（`NotificationCenter` 红点逻辑就是按 0 判的） |
| ③ 不建渲染判据，改在源码层拦 | 便宜 | 见下方⚠️，**只认字面量**会漏同形状写法 |

✅ **裁定（2026-10-29，用户选 `1` = 方向①）：按方向① 落地，建独立 job `browser-ncpanel`。**
理由：只有它不碰产品种子、也不碰共享库；②的副作用会波及别的探针，③已被 §10.2 证明会漏
（上一轮按令牌名扫 `text-brand-*`，漏掉了 `CitationPanel` / `KpiCard` / `ProvenanceBadge`
里**同形状**的 `bg-*-500/15 + text-*-600`）。

⚠️ ③ 仍然值得单独做，但**不是**拿来替代渲染判据：源码层只能证伪「写法」，渲染层才能证伪
「行为」；两边的盲区不一样（§10.2 就是源码层按令牌名扫漏掉同形状写法的实例）。

### 11.5 施工：一条新 job（`browser-ncpanel`）

| 文件 | 改动 |
|---|---|
| `evidence/verify_notification_panel.py` | **新建**。判据 **P1** 面板挂载 / **P2** 三处**都必须渲染**（拦空态假绿）/ **P3** 文字色 == 页面现读 `--link` / **P4** 面板打开态整页 `judge_render` 0 条 / **P5** 深色 `--brand-700` 残留指纹 = 0。自检 **13 臂**（5 输入 + 7 判定 + 1 注入反证 `I1`） |
| `evidence/run_browser_job.py` | `JOB_SPECS["browser-ncpanel"]`：`apps=("lawyer",)` · `probe_args=--app lawyer` · 超时 **420s**；另给 `browser-all` 补 `verify_grade_badges.py`（§10.6） |
| `evidence/run_ci_probes.py` | 5 处注册：`SELFTEST_ONLY` / `CI_JOB_PROBES["browser-ncpanel"]` / `JOB_ENTRYPOINTS["browser-ncpanel"]` / `CI_ELSEWHERE`（`@job:browser-ncpanel`）/ `_syn_ok` 合成 YAML |
| `.github/workflows/ci.yml` | 新 job（10 个 step，照 `browser-admin` 抄，`if: github.event_name != 'pull_request'`） |

**P3 与 P4 刻意不合并**：P3 判「**用的是角色令牌**」（语义：链接/强调文本必须走 `--link`），
P4 判「**比值够不够**」。只留 P4 会漏「值碰巧相同但令牌用错」；只留 P3 会漏「令牌本身不够亮」。

**验收**：`run_browser_job.py --job browser-ncpanel` ⇒ **`JOB_RC=0`（1/1 通过，1m23s）** ·
探针自检 13/13 · `run_ci_probes --self-test` 30 个 ✓ · `run_browser_job --self-test` rc=0 ·
`ruff check evidence/` 过 · `report_spec_coverage` 的 **CI 执行 51 → 52 条**（新探针进了一条 job）·
**全量 `run_ci_probes.py` ⇒ `RUN_ALL_RC=0`**（阶段 2 24/24 · 阶段 3 8/8，2m34s）。

🚨 收尾时全量跑才发现 `RUN_ALL_RC=1`：`report_spec_coverage` 的 **Q14 扫盘**逮到 **3 份既存未分类
文档**（`ruling-p2-pending-2026-10-17` / `ruling-cjk-gap-2026-10-17` / `ci-browser-job-exit2-diagnosis-2026-10-29`）。
**`--self-test` 当时仍是 30 个 ✓** —— 它只跑合成夹具、**不读仓库**。⇒ 已在 `EXCLUDE_RULES` 按「记录族」
登记（要求身份都在别处），复跑 `run_all` 转绿。这条经验比本次改动本身更值钱：**收尾必须跑全量**。

### 11.6 🚨 落地时踩到的三个坑（都长得像「产品有缺陷」）

| # | 现象 | 真因 | 修法 |
|---|---|---|---|
| **1** | 第一版起 **admin**，P1 全红：点了铃铛面板不挂载 | admin 首页 `/` 上唯一的 `[aria-haspopup="dialog"]` 是**别的按钮**（无 `aria-label`），点了 `aria-expanded` 仍 false。**「AppShell 在四端都渲染」≠「四端首页都有铃铛」** | 改起 **lawyer**（`aria-label="通知"`），并同步改 `ci.yml` 的 `apps/lawyer/.env.local` 与 `pnpm --filter app-lawyer build` |
| **2** | 面板明明挂载了，判据却一直说没打开 | **`aria-expanded` 不能当判据**：它是 React **重渲染后**才更新的，`b.click()` 之后**立刻**读必然还是旧值。白红了两轮 | 删掉 `aria-expanded` 判定，只留 `if not opened["clicked"]` 与 `elif not p.get("mounted")`；`aria-expanded` **降级为诊断信息**写进报错文案 |
| **3** | 第一次跑就「0 缺陷」——**假绿** | 面板空态（§11.2 陷阱 1） | 探针自带 `seed_unread()` 造 3 条 `is_read=0`；并把 **P2「三处都必须渲染」** 直接定为判据 —— 空态从此**必然**转红 |

另有两个**登记表**的坑（都是设计意图，不是 bug）：

- 只加 `CI_JOB_PROBES` 不加 `CI_ELSEWHERE` ⇒ `run_all()` 红（判据 5，自检 S3/H0 两臂转红）；
- 只加 `CI_JOB_PROBES` 不加 `JOB_ENTRYPOINTS` / `ci.yml` job 块 ⇒ 自检 S3 红（判据 6）。
⇒ **两张表都得改**，漏一张就红 —— 这正是「判据互相兜住」想要的效果。

### 11.7 边界与遗留

- 只测**桌面**面板（`!isMobile`）；移动端走 BottomSheet，是另一套 DOM，**未覆盖**。
- 只测三处强调文本的**令牌一致性** + 面板打开态的整页比值；不逐条测「每条通知」的语义。
- 证不了「没有任何别的地方用 brand 当文本色」—— 那条要么靠方向③ 的源码判据，要么靠 T4 扩面。
- `report_spec_coverage` 把本探针的 `§2.1` 记为**归因歧义**（`design-spec.md` /
  `im-mobile-nav-spec.md` / `mobile-feature-integration-spec.md` 三份都有 `§2.1`）⇒ 只报不分诊，
  与既有那批同类，不影响判定。


---

## 10. 渲染半边首次真跑：抓出**同族 8 处**并接进 CI

### 10.1 为什么必须跑

`verify_grade_badges.py` 在 CI 里只以 `--source-only` 登记（`StaticGate`，`frontend` job 跑），
它的 `APPS` 覆盖**四端 26 页**、要无头 Chrome + 真后端 + 四端生产产物 ⇒ **G1/G3/G4 从没进过任何 job**。
而我这次改的 `-600 → -700` **正是 G4 要核对的令牌** —— 不跑就等于改了个没人看的东西。

跑法：四端 `next build`（本轮 admin/im 各重试一次，EPERM 老问题）+ 复用
`run_browser_job` 的完整生命周期（`_seed` / `_start_backend` / `_start_frontend` / `_wait_http`
/ `_run_probe`），只把探针清单换成一条 —— 与 job 里跑出来的**同源**。

### 10.2 第一次跑：G4 **63 通过 / 5 失败**（rc=1）

失败的 5 个徽章，DOM 上的文字色是 `rgb(143,95,20)` / `rgb(22,112,85)` = **`-600`**，
而 `Badge.tsx` 已声明 `-700`。查下去发现**不是我的改动没生效**，而是：

> `CitationPanel.tsx:54-55`、`KpiCard.tsx:32-33 / :79`、`ProvenanceBadge.tsx:21-22`
> 把**和 Badge 一模一样的** `bg-*-500/15 + text-*-600` 写在了自己的组件里。

⇒ G4 按「底色 class ⇒ 变体」反查，于是把它们算成 `Badge` 的变体来核对 —— **判定是对的**，
只是文案把出处写成「Badge.tsx 声明的」（**措辞瑕疵**，同 `reviews.py:42` 那类，不影响判定）。
⇒ **真正的事实是：§5.1 那份清单漏了这 3 个组件** —— 我上一轮只按 `text-brand-*` 扫，
没按「`/15` 染色底 + 同色 `-600` 文字」这个**形状**扫。

### 10.3 全色族验算（`%TEMP%\nl_tone_family.py`）

| 色族 | 浅/`surface-subtle` | 浅/`surface-page` | 深/`surface-card` | 结论 |
|---|---|---|---|---|
| `verified` | **4.49 ✗** | 4.74 ✓ | 6.56 ✓ | 取 `-700`（5.91） |
| `pending` | **4.16 ✗** | **4.40 ✗** | 6.58 ✓ | 取 `-700`（5.93） |
| `gold` | **3.64 ✗** | **3.83 ✗** | 7.25 ✓ | 取 `-700`（5.31） |
| `danger` | 4.88 ✓ | 5.18 ✓ | 5.42 ✓ | **不动** |
| `info` | 5.08 ✓ | 5.34 ✓ | 6.87 ✓ | **不动** |
| `ai` | 5.24 ✓ | 5.54 ✓ | 6.27 ✓ | **不动** |
| `brand` | 5.93 ✓ | 6.29 ✓ | **2.60 ✗** | 已统一走 `text-link` |

⇒ **只有 verified / pending / gold 三个色族需要提档**；「统一成 -700」或「统一成 -600」
两个方向都有反例。`gold` 是本轮**新发现**（`Badge` 早就用 `gold-700` 了，`KpiCard` 没用）。

### 10.4 追加的 8 处改动

| 文件 | 改动 |
|---|---|
| `CitationPanel.tsx:54 / :55` | `text-verified-600` → `-700` · `text-pending-600` → `-700` |
| `KpiCard.tsx:32 / :33` | `text: "text-verified-600"` → `-700` · `pending` 同 |
| `KpiCard.tsx:37` | `text-gold-600` → **`-700`**（3.64 ✗ → 5.31 ✓） |
| `KpiCard.tsx:79` | `bg-verified-500/15 text-verified-600` → `-700` |
| `ProvenanceBadge.tsx:21 / :22` | `verified` / `pending` → `-700` |

三处都加了同一段注释说明「为什么危险色不动」—— 否则下一个人会「统一一下」把 danger 改坏。

### 10.5 复跑：**G4 68 通过 / 0 失败 / 63 不判**（rc=0）

`G1 23/23` · `G3 23/23` · 通过数 63 → **68**（正好是那 5 个转绿）· 26 页 · 131 个 Badge。
唯一残留是 `im /cases/@first-case` 的一条 **ENV**（占位符解析失败，**不算缺陷**）。

### 10.6 接进 CI（把「修完没人守」这个病治掉）

| 登记处 | 改动 |
|---|---|
| `run_ci_probes.CI_JOB_PROBES["browser-all"]` | 加 `verify_grade_badges.py`（无参） |
| `run_ci_probes.CI_ELSEWHERE` | 加同名条目，带 `@job:browser-all` |
| `run_browser_job.JOB_SPECS["browser-all"]` | `probe_args` 加 `()`；`probe_timeouts` 给 **600s** |

🚨 **踩到判据 5**：只加 `CI_JOB_PROBES` 不加 `CI_ELSEWHERE` ⇒ `run_all()` 直接红
（「两张表不一致：列在某个 job 里但没登记进 `CI_ELSEWHERE`」），自检 S3/H0 两臂当场转红。
⇒ 这正是**登记表互相兜住**的设计意图：**两张表都得改**，漏一张就红。

验证：`run_ci_probes --self-test` **全绿** · `run_browser_job --self-test` rc=0 ·
`ruff check evidence/` 过 · `report_spec_coverage` 仍是 **CI 执行 51 条**（同一探针，只是多进一个 job）。


