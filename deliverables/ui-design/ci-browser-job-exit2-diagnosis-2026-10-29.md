# CI `browser-all` / `browser-admin` 探针 `exit 2` 根因定论（2026-10-29）

> 结论先行：**不是产品缺陷、不是 Chrome、不是种子/后端**，而是 **`basePath`**——
> committed 树的就绪探针按「无前缀」写，而 `lawyer`/`admin`/`im` 三端自 2026-10-01 起
> 都挂在 `basePath` 下 ⇒ 探 `/` 得 **404** ⇒ 等满 180s ⇒ `return 2`。
> 修复只存在于**未提交的工作树**（`evidence/approutes.py` + `run_browser_job.py` 的 `app_base()`）。

## 1. 现象

`fe9d6ab`（run `40801125564`）两个浏览器 job 都挂在 step 10「Run browser probes」：

| Job | 结果 | 失败步 |
|---|---|---|
| Browser (admin · real assertions) | exit **2** | 10 Run browser probes |
| Browser (all 4 apps · real assertions) | exit **2** | 10 Run browser probes |

`exit 2` 在本仓库语义 = **环境问题**（不是产品坏了）。seed 步修复后已确认不再卡
「Seed build-time env」，即修复有效，问题在其后。

## 2. 逐条排除的假设

| # | 假设 | 判据 | 结论 |
|---|---|---|---|
| 1 | runner 上没有 Chrome | `Evidence (browser self-tests)` job **全绿**；其 step 5 打印 `find_chrome()` 成功、step 6 用 `data:text/html` 合成页**真起过浏览器** | ❌ 排除 |
| 2 | 缺 `websockets`（`cdp.py` 顶层 import） | `backend/requirements.txt:21` = `websockets==13.1`，两 job 都装 | ❌ 排除 |
| 3 | `_seed()` 失败 | 用仓库 `backend/.venv` 在 `fe9d6ab` 干净导出上跑 ⇒ **SEED_OK** | ❌ 排除 |
| 4 | 后端起不来 | 同上：`_start_backend()` + `/api/health/readyz` ⇒ **200** | ❌ 排除 |
| 5 | 登记表自检失败（L686） | `run_browser_job.py --self-test` 在导出上 exit 0 | ❌ 排除 |
| 6 | 缺 `.next/BUILD_ID`（L718） | CI 的 Build 步 success | ❌ 排除 |

## 3. 根因

| | 导出 `fe9d6ab`（= CI 真正跑的） | 工作树（未提交） |
|---|---|---|
| `run_browser_job.py` 就绪 URL | `_wait_http(f"http://localhost:{APP_PORTS[a]}/")` | `_wait_http(ready_url := app_base(a))` |
| `evidence/approutes.py` | **不存在** | 存在（`basePath` 唯一真源） |

`apps/{lawyer,admin,im}/next.config.js` 均配 `basePath`（`/lawyer`·`/admin`·`/im`），
只有 `web` 无前缀。

**本地实测**（起 admin 生产产物 `next start -p 3102`，**不需要 Chrome**）：

```
GET /              -> 404
GET /admin         -> 200
GET /admin/login   -> 200
```

⇒ `_wait_http("http://localhost:3002/")` 永远拿不到 200 ⇒ 等满 `PAGE_TIMEOUT_SEC=180`
⇒ `return 2`。

- `browser-admin`（admin 单端）：卡在 admin 首页 ⇒ exit 2 ✅
- `browser-all`（web→lawyer→admin→im）：`web` 无前缀通过、**`lawyer`** 卡住 ⇒ exit 2 ✅

与 `approutes.py` docstring 记录的事故完全一致：「就绪探针与 15 条门禁探针**全都按无前缀写**
⇒ 每一条 `goto` 都 404 ⇒ `browser-all`/`browser-admin` **自 10-01 起必然 exit 2**，
静默了 9 天」。

## 4. 为什么「增量提交」救不了 CI

1. 工作树里 **27 个 evidence 文件** import `approutes` —— 不止就绪探针，**14 条门禁探针的
   `goto` 也全改了**。只提交「就绪 URL 修复 + `approutes.py`」⇒ 探针仍按无前缀 `goto` ⇒
   仍 404 ⇒ 仍 exit 2。
2. 整批提交 `run_ci_probes.py`（连带 4 个未提交探针）会在 committed 树上新增两条红：

   | 探针 | committed 树 | 工作树 |
   |---|---|---|
   | `verify_no_bom.py` | **6 个 BOM 文件** ⇒ 红 | 0 ⇒ 绿 |
   | `verify_ink400_body.py` | **133 条** ⇒ 红 | 0 ⇒ 绿 |

3. 规模：`git status --short` = **148** 个改动（`evidence/` 占 **62**）。

⇒ committed 树比工作树旧约一个月。**CI 要真跑，只能整批同步工作树；不存在中间态。**

## 5. 两条可行路径

| | A. 整批同步工作树 | B. 本地装 Chromium 直接跑 |
|---|---|---|
| 做法 | `git add -A` + 提交 148 文件 + push | 装 Chromium（Playwright / Chrome for Testing），`CHROME_BIN=<路径>`，然后 `python evidence/run_browser_job.py --job browser-all` |
| 代价 | 破「不提交」约定；一次性大提交；CI 仍可能有 ruff / pip-audit 的既有红 | ~150 MB 下载；本机跑约 4–6 min（四端已构建） |
| 收益 | CI 恢复真实意义（主干每次 push 都验） | **直接拿到 26 页 / 104 组的 T4 真验证**，不碰 git |
| 风险 | 大提交难以 review；若仍有红要连推 | 无（不动仓库） |

`cdp.py` 认 `CHROME_BIN`（`_chrome_candidates()` 第一优先级），因此路径 B 不需要改任何代码。

## 6. 附：本次复现脚本

`%TEMP%\nl_repro_browserjob.py` —— 复用导出里的 `run_browser_job` 的
`_backend_env` / `_seed` / `_start_backend` / `_wait_http`，只把 `ROOT`/`BACKEND`
指到 `fe9d6ab` 导出目录（导出无 `.venv`，用仓库的）。运行方式：

```bash
CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR= \
  backend/.venv/Scripts/python.exe "$TEMP/nl_repro_browserjob.py"
```

---

# 附：路径 B 已执行 —— T4 真验证结果（2026-10-29）

> 结论：**`browser-all` 本机跑通，15/16 通过**；唯一红是 `verify_contrast.py` 的
> **真产品缺陷 8 条**（`web/compliance` 深色筛选芯片 1.95:1），已修复并复验为
> **104 组 / 不合格 0 组 / 缺陷 0 条**。

## B.1 前提纠正：「本地没有 Chrome」是错的

`which google-chrome/chromium/chrome` 全空，但 `cdp.py::_chrome_candidates()` 查的是**固定安装位**：

```
C:\Users\RS\AppData\Local\ms-playwright\chromium-1234\chrome-win64\chrome.exe   ← 第一候选，存在
```

⇒ **无需下载、无需 `CHROME_BIN`**。`which` 只查 PATH，两者不是一回事。

## B.2 执行链与耗时

| 步骤 | 命令 | 结果 |
|---|---|---|
| 浏览器定位 | `cdp.find_chrome()` | 命中 chromium-1234 |
| 产物内联检查 | `verify_api_base_baked.py` | 四端都烘入 `localhost:8001`，EXIT=0 |
| **仪器自检** | `run_ci_probes.py --selftest-for-job browser-selftest` | **4/4 通过**（2m18s，合成页真起浏览器） |
| **真判据** | `run_browser_job.py --job browser-all` | **36m41s**，四端 Ready，**15/16** |

四端就绪 URL 全部带 `basePath`（`:3001/lawyer` · `:3002/admin` · `:3003/im`）——
再次印证第 3 节的根因判断。

## B.3 唯一红：`verify_contrast.py`（rc=1，**产品缺陷**）

```
[T4 渲染层] 测量 104 组；不合格 2 组 / 缺陷 8 条
  ✗ web/compliance @390/dark    4 条
  ✗ web/compliance @1280/dark   4 条
  [T4] 1.95:1 < 4.5（正文 13px/500）：<button> class=`tap rounded-r2 border px-3 py-1.5 text-body-sm …`
       文字=劳动用工 / 商业合同 / 数据隐私 / 广告营销
       fg=rgb(39,76,147)  bg=rgb(26,34,46)
```

其余 15 条全绿（`verify_typography` 236.8s、`verify_runtime_health` 234.0s、
`verify_mobile_375` 256.0s、`verify_adjacent_targets` 142.9s …）。

## B.4 定位与修复

`apps/web/app/(app)/compliance/page.tsx:240` 的**选中态芯片**：

```diff
- ? "border-brand-400 bg-brand-500/10 font-medium text-brand-700"
+ ? "border-brand-400 bg-brand-500/10 font-medium text-link"
```

依据三条，都是**既有规范**而非新设计：

1. 深色档 `--brand-700` 仍是 **#274C93**（= 浅色档值），在 `#1A222E` 上只有 **1.94:1**
   （按 WCAG 公式独立计算 = 1.94，与探针实测 1.95 吻合）。
   深色梯里**唯一能在暗底承载文本的亮蓝是 `--brand-400`**（`tokens.css` 深色段注释已写明）。
2. `tokens.css` L25 的规范原文：「链接与强调文本请使用 **`--link` 角色令牌**，而非 brand-600」。
3. 同族页 `apps/admin/app/(app)/complaints/page.tsx:633` 的**同款芯片**写的就是 `text-link`，
   且 admin 两档在 T4 里全绿 ⇒ 这是**一处漏改**，不是风格选择。

## B.5 复验

- 重建 web 生产产物：`next build` 首次撞已知的 `EPERM .next/trace`，重跑 rc=0
  （`BUILD_ID=tSdW5swLyNDz6zqiTprBu`）。
- 复用**已灌种子**的隔离库单跑 contrast（`%TEMP%\nl_rerun_contrast.py`，7m03s）：

```
[T4 渲染层] 测量 104 组；不合格 0 组 / 缺陷 0 条
0 个 ✗   （web/compliance 深浅两档均 ✓）
```

## B.6 遗留：同形但 T4 覆盖不到的 3 处

| 位置 | 深色底 | 旧写法 | 建议 |
|---|---|---|---|
| `lawyer/app/(app)/cases/[id]/page.tsx:1089`（tab 激活） | 页面底 | ~2.16:1 | `text-link` |
| `lawyer/.../cases/[id]/page.tsx:1423`（序号徽标，`bg-brand-50`） | `#10203A` | ~1.97:1 | `text-link` |
| `packages/ui/src/components/NotificationCenter.tsx:729`（`bg-brand-600/10`） | ≈`#16294A` | ~1.75:1 | `text-link`（**共享包 ⇒ 须拍板**） |

T4 只收**静态列表页**，动态详情页与共享包组件不在覆盖内 ⇒ 这三处需要单独决定是否修。

---

# 附二：动态详情页补测 —— lawyer `/cases/[id]`（同日续）

> 上表里 `lawyer/cases/[id]:1089` / `:1423` 两处已修并**在渲染层验证过**；
> 过程中又抓出 **9 条既存浅色缺陷**（这页从来没有判据），已修掉 7 条，
> 剩 2 条落在**共享包**，需拍板。

## C.1 为什么要单独写脚本

`verify_contrast.py` 的 `pages` 是**静态路径元组**，docstring 明写「详情页要先取 id ⇒ 支持不了动态段」。
于是写了一次性脚本 `%TEMP%\nl_detail_contrast.py`，**完全复用**探针既有机制
（`login()` / `MEASURE_JS` / `judge_render()` / `_premise()`），只把页面换成详情页。

> 细节：列表页是 `onRowClick → router.push('/cases/<id>')`，**没有 `<a href>`** 可抓
> ⇒ 案件 id 从 API 取（`/api/v1/cases?page_size=5`，用 `app.seed.data.DEMO_USERS` 的凭据换 token）。

脚本额外做了一个**指纹查询**：统计「深色下 fg 恰好是 `rgb(39,76,147)`（= 深色 `--brand-700`）」
的文本元素个数 —— 这是「漏改」的直接证据，比只看「0 条缺陷」更强。

## C.2 第一批修复（两处 `text-brand-700` → `text-link`）

| 位置 | 浅色 | 深色 |
|---|---|---|
| `cases/[id]:1089` tab 激活 | 10.67 → **8.26** | 1.94 → **6.67** |
| `cases/[id]:1423` 序号徽标（`bg-brand-50` 上） | 9.58 → **7.42** | 1.97 → **6.79** |

**验证结果**：`@1280/dark` **✓ 0 条缺陷**；深色下 `--brand-700` 残留元素 **0 个**。

## C.3 顺带抓出的 9 条既存浅色缺陷

| 元素 | 旧值 | 来源 |
|---|---|---|
| 时间轴 7 个「未到达」步骤标签 | **2.55:1** | `cases/[id]:1293` `text-caption` + `text-ink-400` |
| `律师已确认` 徽标 | **4.49:1** | `packages/ui/Badge.tsx`（verified） |
| `待复核` 徽标 | **4.16:1** | `packages/ui/Badge.tsx`（pending） |

## C.4 已修：时间轴 `text-ink-400` → `text-ink-500`

`--ink-400` 是**非文本**档（图标 / 占位符），11px 文字用它本身就违反项目自己的
`verify_ink400_body.py` 规则。改 `text-ink-500`：

| | ink-400 | ink-500 | ink-700（已完成档） |
|---|---|---|---|
| 压白底 | 2.55:1 ✗ | **5.47:1 ✓** | 11.32:1 |

层级不变（未到达仍明显弱于已完成）。**复验：浅色 9 条 → 2 条，深色仍 0 条。**

## C.5 剩 2 条：在共享包，需拍板

`packages/ui/src/components/Badge.tsx`：

```ts
verified: "bg-verified-500/15 text-verified-600 border-verified-500/30",
pending:  "bg-pending-500/15  text-pending-600  border-pending-500/30",
```

令牌注释写「文本用色（6.0:1）」——那是**按白底**算的（实测 6.03 ✓）。
但 Badge 把文字压在 **`-500/15` 的带色底**上 ⇒ 掉到 4.49 / 4.16。

| 方案 | verified | pending |
|---|---|---|
| 现状 `-600` | 4.49 ✗ | 4.16 ✗ |
| 改 `-700` | **5.91 ✓** | **5.93 ✓** |

## C.6 🚨 顺带发现一条判据缺口

`verify_ink400_body.py` 在工作树上报「缺陷 **0** 条」，但 `cases/[id]:1292-1293`
确实是 `text-caption` + `text-ink-400` 同现 —— 因为写成了
`cn("text-center text-caption", …, "text-ink-400")`，**跨两个参数**，
而判据的规则是「**同串**」⇒ 漏判。

这是「判据自缺陷：只覆盖一种写法」的又一例（与 MEMORY 里记的两个形状同族）。
**修法**：判据应按**同一个 `cn()` 调用的实参集合**判，而不是按单个字符串。
⚠️ 按仓库纪律，改判据必须**配臂 + 注入反证**（摘掉修复 ⇒ 该臂必红）。
