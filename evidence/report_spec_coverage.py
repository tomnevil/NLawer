#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""规范覆盖率**报表**（不是门禁）—— 度量「门禁 × 规范小节」的覆盖面。

## 为什么要有它

「下一块该补哪条防线」不能靠猜，也不能靠记忆。此前每轮都是临时写一段
`grep -oE '§[0-9]+(\\.[0-9]+)?'` 求交，于是**同一张矩阵被重复推导、且每次都带同一个缺陷**。

## 它修掉的八处失真（都是实测踩出来的）

> ①–⑤ 记在本节；**⑥ / ⑥b** 记在下一节「零引用 ≠ 没判据」里；
> **⑦** 记在本节末尾 —— 加了它之后，报表才回答得了「**CI 里到底有没有判据**」
> （它们与「零引用分诊」同源：都是**同一个桶里混了两种性质相反的东西**）。

### ① 容器标题被当成小节
`## 00 / ## 01 / ## 03` 是章标题，本身不承载要求，其**子节**才是。
把它们计成「零引用」会**虚增分母**。⇒ 只统计**叶子小节**
（没有任何其它小节以 `s + "."` 为前缀）。

### ② 未点名的门禁不归因
10 个门禁引了 `§x.y` 却没写文档名（`verify_buttons.py: §2.2 §6.1 §8.3` …）。
按「没点名 ⇒ 未归因」处理会让 `design-spec.md` 的真实覆盖被**低估**。
⇒ 按「哪份文档**恰好含有**它引的那些节号」归因：唯一 ⇒ 归过去；
多个 ⇒ 记**歧义**并列候选，**不瞎归**。

### ③ 🚨 「提到」被当成了「判」——**最严重的一处**
`verify_component_refactor.py:9`（**在 docstring 里**）写着
「§8.6 是**功能缺口**不是覆盖率缺口」—— 这句话的**语义恰恰是「本节没判据」**，
而早期仪器把它记成了「§8.6 已被覆盖」。⇒ 矩阵一直在**高估覆盖**，
而且**新写的门禁自己就是污染源**（自指污染）。
⇒ 修法：只在**判据代码体**里提取节号 —— 用 `ast` 剥掉模块 docstring
（⚠️ 不能用 `^\\s*\"\"\"`：文件头常有 shebang，`\\s*` 匹配不到它，
docstring 会**悄悄留下来** —— 实测 v2/v3 数字完全相同就是这个原因），
再剥 `#` 与块注释。**保留字符串字面量里的节号**（判据的报错文案写
`§6.3 要求…` 是**正当覆盖**）。

⇒ 报表同时输出 v2（提到即算）/ v3（判据内）两列，**差值本身就是证据**：
「有多少节号只是被提到过」。

### ④ 🚨 节号可以活在**表格行**里，而不只是标题里

`design-spec.md` 的 `## 07 页面概念图` 一章**没有任何编号子标题**，7 个小节
（7.1 登录页 … 7.7 文书工作台）全部是**表格首列**：

    | 7.3 | 智能问答 | 三栏化 + 引用溯源常驻面板 + 会话历史 | `mockups/03-qa.html` |

⇒ 纯标题扫描**根本不知道它们存在**。这不只是少算覆盖：`verify_component_wiring.py:133`
在**代码体**里引了 `§7.3`，而 §7.3 不在分母里 ⇒ 这条引用被**静默丢弃**，
矩阵连「有个节号没人认领」都不会报。**漏节比算错覆盖更危险。**

⚠️ 判别规则必须要求**点分**（`N.N`）：同一份文档里另有 27 行纯整数首列 ——
`## 00 现状诊断` 的 `| 01 |` 是**问题编号**、`### 8.7` 的 `| 08 |` 是**稿编号**、
`## 10 决策记录` 的 `| 01 |` 是**决策编号**。一并收进来会凭空多出 27 个假小节。

⚠️ **总数不是识别信号**：修完 ④ 后 `design-spec.md` 的叶子小节数**恰好回到**早期
（有缺陷的）仪器给出的 36 —— 但两个 36 是**不同的集合**：早期把章标题 `## 00/01/03`
当小节（虚增），现在收的是 7 张概念图行（真小节）。只看计数会得出「什么都没变」的错误结论。

### ⑤ 🚨 归因到**矩阵外的文档** ⇒ 引用被静默丢弃

`named`（门禁点名了哪份文档）原本**不区分**「矩阵内」与「矩阵外」。
实测：`verify_component_wiring.py:145` 在一行 `#` 注释里提到 `admin-gap-analysis.md`，
于是它的 `§7.3` 被归到 `admin-gap-analysis.md` —— 而该文档**不在 `SPEC_FILES` 里**，
`cov['admin-gap-analysis.md']` 是个**永不打印的键**。⇒ 引用**静默消失**：
报表既不说「§7.3 被覆盖」，也不说「有个节号没人认领」。

同族：`verify_design_tokens.py` 点名 `phase3-implementation.md`（其 §6.4），
这正是 `§2.5` 长期显示零引用、却又能找到「判同一件事」的门禁的原因 ——
**不是引用缺失，是归因被劫持后不可见。**

⇒ 修法：**过滤** `named` 到矩阵内文档，并把矩阵外的名字**列出来**
（`⚠️ 归因到矩阵外文档`），让「这些引用不计入规范覆盖率」这件事**可见**。

🚨 **过度修正的教训**：第一版把 `named` 改成「必须与 `refs` 取自同一段文本」，
理由是「注释里的文档名会劫持代码体引用」。结果 `design-spec.md` 覆盖 **21 → 18** ——
`verify_breakpoints.py` 的 docstring 写着「规范依据（`design-spec.md` §8.2 / §8.4）」，
那是**正当的适用范围声明**，代码体不必重复文档名；改成同段文本后它的
§4.1/§8.2/§9 因**归因歧义**被丢弃。
⇒ **docstring 里的文档名是声明，注释里的文档名才是附带提及** —— 不能一刀切。

### ⑦ 🚨 覆盖率对「这条判据在 CI 里**会不会跑**」无感 ⇒ **高估 CI 内覆盖**

前六处失真都在问「这一节**有没有**判据」。但**有判据 ≠ 会被跑**：
`run_ci_probes.py` 把探针分成三档，CI 只执行 `GATED`（18）+ `GATED_STATIC`（7）
= **25 个**；另外 **14 个 `NOT_GATED`**（要浏览器 / dev server / 生产产物）
**在任何 job 里都不执行**。

📌 **2026-09-26 更新（拍板 §11.4-③）**：那 **14 个已全部接进 CI** ——
新增 `browser-all` job（四端生产构建 + 隔离库 + 真后端），`NOT_GATED` **已清空**。
⚠️ 但**本节的方法论一字未改、仍然成立**：那 14 条**只在主干 / 手动触发**
（≈22–25 min，挂 PR 会拖垮反馈回路）⇒ 对 **PR 而言**它们**依然不跑**。
⇒ 「在 CI 里会不会跑」这个问题，**答案随 job 的触发条件而变** ——
   v4 的 `CI_ELSEWHERE` 记的是「**有一个 job 会跑它**」，**不是**「每个 PR 都会跑它」。

而本报表是 `HERE.glob("verify_*.py")` —— **39 个门禁一视同仁**。
⇒ 一个只被 `verify_runtime_health.py` / `verify_mobile_375.py` 这类探针引用的节，
在报表里显示「已覆盖」，而在 **CI 里那条判据一次都不会跑** ⇒
**报表把「纸面覆盖」当成了「CI 内覆盖」**，缺口被系统性低估。

同族：这是「**提到 vs 判**」（③）的第三次变体 —— 同一个桶里混了两种性质相反的东西：
- ③ 「节号出现在 docstring 里」 vs 「节号出现在**判据代码体**里」
- ⑦ 「判据**存在**」 vs 「判据**在 CI 里会跑**」

⇒ 修法（**纯增量，不改任何既有归因逻辑**）：
1. 从运行器**导入**分档（`import run_ci_probes`，不在这里重抄一份 —— 重抄必然漂移）；
2. 归因循环里多接**一个输出口** `cov4`（走**同一段**代码 ⇒ 与 `v3` 在构造上不可能漂移）；
3. 新增一段「**CI 内零判据**」：`v3` 有、`cov4` 没有的叶子小节；
4. 🚨 **取不到分档时不出 `v4` 数字**（只打印原因）—— 与棘轮的「取不到条数判红」同一条纪律：
   **「取不到」不是「0」**，否则哪天运行器改了名，报表会静默地把 39 个全算成 CI 内。

## ⚠️ 零引用 ≠ 没判据

仪器只负责**缩小候选**。门禁完全可能判了某一节的内容却没写它的节号
（同族：引用号写在**另一份文档**里）。**落点必须读门禁的判据函数再分诊。**

### ⑥ 🚨 「无主引用」被混进「提到而非判」⇒ **归属缺口伪装成口径问题**

只出现在 docstring / 注释里的节号，原先**一律**记「提到而非判」。但这里混着两种东西：

| 档 | 条件 | 性质 |
|---|---|---|
| **提到而非判** | 该节**在矩阵内** | **口径**问题（判据写在代码体之外）⇒ 不是盲区 |
| 🚨 **无主引用** | 该节**不在任何已纳入文档**里 | **归属缺口** ⇒ 一个真实的「引用指向无人认领的节号」 |

后者看起来像「已经解释过了」，于是**没人去修**。实测 6 条：`verify_route_coverage.py`
的 `§3.32`/`§3.33` 与 `verify_service_tenant_param.py` 的 `§3.27`–`§3.29.3` ——
全部出自 **`round15b-fixed-but-unguarded-sweep-2026-09-20.md`**（矩阵外），而这两个门禁
**都没点名那份文档**。⇒ 修法：**点名文档**（成本≈0）。点名后它们转入
「**越界节号已被解释**」档，仍**打印**（可见，不是消失）。

⚠️ 顺带修掉一个**隐藏的硬编码**：`DOCNAME_RE` 原先是手写的 5 个文档名。
⇒ 改成**从磁盘发现**（`DOC_ROOTS` 下的所有 `.md` 词干）。
🚨 **这一步差点造成假降**：第一版把**带扩展名的 `p.name`** 直接拼进正则，而下游是
`m.group(1) + ".md"` ⇒ 归因结果变成 `design-spec.md.md` ⇒ `named` 全空、
`scope_out` 全非空 ⇒ **19 个门禁的引用被整体跳过**，`design-spec.md` 覆盖 **26 → 4**。
**是「改前 / 改后数字对比」逮到它的** —— 若直接信新数字，会凭空「发现」23 个盲区。
⇒ 纪律：**改仪器必须留下改前的快照，改后 `diff` 数字行**；只凭印象看不出假降。
另：`README.md` 这类**太通用的名字**必须忽略（实测 7 个门禁在 docstring 里提过
`evidence/README.md`，收进来会让它们的引用被整体跳过）。

### ⑥b 文档范围必须**显式**：只写「纳入了 3 份」会被读成「仓库里只有 3 份规范」

实测：仓库里有 **33** 份带编号章节的 `.md`，其中 **5 份是产品需求**
（`reference/律小智AI法律助手PRD_v2.0.md` 48 个编号标题 + 三份 `prd-*.md` + 一份 `prd-*-spec`）。
报表此前**从不提它们** ⇒ 读者会默认「3 份 = 全部规范」。
⇒ 现在打印「**文档范围**」段（纳入 / 排除（规则）/ 排除（显式），**每条排除都带理由**），
并加一条**守卫**：扫盘发现**未分类**的规范类文档 ⇒ 🚨 报警
（同 CI 的「新探针未分类 ⇒ 失败」）。
⚠️ **需求类文档一律逐个显式登记**，不用通配规则 —— 「排除一份**需求**」是最需要被看见的决定。

## 退出码

`0` 报表正常产出（**任何覆盖率都不算失败** —— 它度量的是防线，不是产品）·
`1` **仅** `--self-test` 时仪器自身坏掉 ·
`2` 环境问题（`evidence/` 下没有 `verify_*.py`，或点名的规范文件不存在）。

⇒ 因此**不叫** `verify_*.py`：`run_ci_probes.py` 的
「新探针未分类 ⇒ 失败」是按 `glob("verify_*.py")` 发现的，
报表不是门禁，不该被卷进那道检查。
"""

import argparse
import ast
import collections
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
SPEC_DIR = ROOT / "deliverables" / "ui-design"

# 纳入矩阵的规范文档（**有要求**的，不是我们自己的审计稿）
SPEC_FILES = [
    SPEC_DIR / "design-spec.md",
    SPEC_DIR / "im-mobile-nav-spec.md",
    SPEC_DIR / "mobile-feature-integration-spec.md",
]

# ⚠️ 必须捕获**标题文字**（`### 4.4 动效` ⇒ 标题是「动效」）。
# 早先的写法只捕获到节号本身（`group(0)` 止于节号后的空白），
# 于是零引用明细里每行都打印成 `§4.4  4.4` —— 看不出这一节讲什么。
HEAD_RE = re.compile(r"(?m)^(#{2,5})\s+(\d+(?:\.\d+)*)[\s、．.]\s*(.*)$")
# 表格行里的节号（见 docstring ④）。**必须点分**：纯整数首列是编号（问题/稿/决策），不是节号。
ROW_SEC_RE = re.compile(r"(?m)^\|\s*(\d+\.\d+(?:\.\d+)*)\s*\|\s*([^|]*?)\s*\|")
REF_RE = re.compile(r"§\s*(\d+(?:\.\d+)*)")

# ── ⑥ 「点了名的文档」不能是**硬编码 5 个名字** ────────────────────────────
# 早先是 `(design-spec|admin-gap-analysis|…|phase[123]-implementation)\.md` 的手写列表。
# 后果：门禁一旦点名**别的**文档（如 `round15b-*.md`），这个名字**不被识别**
# ⇒ 既不算「点名了矩阵外文档」，也不算「适用范围声明」⇒ 引用落进一个**含混的桶**。
# ⇒ 改成**从磁盘发现**：凡位于规范类根目录下的 `.md` 都算「可点名的文档」。
# ⚠️ 刻意**不含** `evidence/` 与仓库根 `README.md`：门禁在 docstring 里提一句
# `evidence/README.md` 是**引用文档**，不是「适用范围声明」；收进来会把那些门禁的
# 引用**整体跳过** ⇒ 覆盖**假降**（与 ⑤ 的过度修正同族）。
DOC_ROOTS = ("deliverables", "reference", "docs")

#: 太通用的名字（几乎每个目录都有一个）⇒ 出现它们**不算**「点名了某份规范」。
#: 实测：`evidence/README.md` 被 7 个门禁在 docstring 里提到；若把它当成「点名了矩阵外文档」，
#: 那些门禁的引用会被**整体跳过** ⇒ 覆盖**假降**。
DOCNAME_IGNORE = {"README.md"}


def _known_doc_stems() -> list[str]:
    """从磁盘发现「可点名的文档」的**词干**（不含 `.md`）。

    🚨 必须是**词干**：下游是 `m.group(1) + ".md"`。第一版我直接塞了带扩展名的
    `p.name` ⇒ 归因结果变成 `design-spec.md.md` ⇒ `named` 全空、`scope_out` 全非空
    ⇒ **19 个门禁的引用被整体跳过**，`design-spec.md` 覆盖从 26 **假降到 4**。
    ⇒ **是「改前/改后数字对比」逮到它的**：若直接信新数字，会凭空「发现」23 个盲区。
    """
    names: set[str] = set()
    for r in DOC_ROOTS:
        base = ROOT / r
        if base.exists():
            names |= {p.name for p in base.rglob("*.md")}
    return sorted({n[:-3] for n in names - DOCNAME_IGNORE if n.endswith(".md")},
                  key=len, reverse=True)


DOCNAME_RE = re.compile(r"\b(" + "|".join(re.escape(s) for s in _known_doc_stems()) + r")\.md")

# ── ⑥b 文档范围必须**显式**：只写「纳入了 3 份」会被读成「仓库里只有 3 份规范」──
# 实测：仓库里还有 `reference/律小智AI法律助手PRD_v2.0.md`（120 KB / 48 个编号标题）
# 与三份 `prd-*.md`（31/26/20/9 个编号标题）—— 全是**有要求的规范**，却从没出现在报表里。
# ⇒ 把「排除」写成**声明**，并给未分类的文档**报警**（同 CI 的「新探针未分类 ⇒ 失败」）。
#: 排除**规则**：按仓库相对路径前缀匹配。只用来收「记录 / 审计 / 实施 / 计划」这些
#: **不是要求**的族。⚠️ **需求类文档（PRD / spec）一律逐个显式登记**（见 `EXCLUDED_DOCS`），
#: 因为「排除一份**需求**」是最需要被看见的决定，不能藏在通配规则里。
EXCLUDE_RULES: tuple[tuple[str, str], ...] = (
    ("deliverables/product-strategy/round", "轮次记录（记录「做过什么」，不是「要求什么」）"),
    ("deliverables/product-strategy/overview.md", "总览（索引型）"),
    ("deliverables/product-strategy/decisions", "决策记录（裁定结果，索引型）"),
    ("deliverables/product-strategy/competitive-analysis", "竞品分析（非要求）"),
    ("deliverables/product-strategy/incident-postmortem", "事故复盘（记录）"),
    ("deliverables/product-strategy/roadmap-update", "路线图（计划）"),
    ("deliverables/product-strategy/group1-implementation", "实施记录"),
    ("deliverables/product-strategy/backend-cooperation", "给后端的协作清单（不是产品要求）"),
    ("deliverables/product-strategy/remaining-work-inventory", "剩余工作清单（索引型）"),
    ("deliverables/product-strategy/endpoint-authz-gate", "门禁实施记录"),
    ("deliverables/ui-design/phase", "实施记录（做完之后写的）"),
    ("deliverables/ui-design/admin-gap-analysis.md", "审计稿（问题登记表 #1–#63）"),
    ("deliverables/ui-design/page-concept-audit.md", "本轮交付的审计稿"),
    # 2026-09-25：待办 #58 的产出。**不是「要求什么」，是「测了什么」** ——
    # 记录 §1 原则 4「全站等宽」的可静态判性实测（含三选一结论与逐条假红分类）。
    # ⚠️ 它**含 8 个编号标题** ⇒ 不登记就会被 Q14 判成「未分类文档」（实测当场咬合）。
    # 为什么进 `EXCLUDE_RULES` 而不是 `EXCLUDED_DOCS`：那一档留给**需求类**文档
    # （「排除一份*需求*」是最需要被看见的决定）；本文档是实测记录，与 `phase*` 同类。
    ("deliverables/ui-design/mono-coverage-feasibility.md",
     "可行性实测记录（测了什么，不是要求什么）"),
    # 2026-09-25：#72 的规范补充，**已定稿**（并排小目标盒宽 ≥ 40px / 间距 8px）。
    # 要求身份已由 `design-spec.md` §8.3 的**脚注**承担 ⇒ 本文档转为**定稿记录**
    # （记「怎么定的 + 闭式解 + 实测」，不再是「要求什么」）。
    # ⚠️ 含 9 个编号标题 ⇒ 不登记就会被 Q14 判成「未分类文档」（与 mono-coverage 同型咬合）。
    ("deliverables/ui-design/design-spec-addendum-8.3-tap-targets.md",
     "规范补充**定稿记录**（要求已并入 `design-spec.md` §8.3 脚注）"),
    # 2026-09-25：#75 的裁定材料，**已裁定 + 已落地**（收敛 33→0 + 建 `animate-spin-soft`）。
    # 要求身份已由 `design-spec.md` §4.4 的**新增行（加载指示（旋转））+ 补充段**承担
    # ⇒ 本文档转为**定稿记录**（记「怎么定的 + 两处被实测否掉的前提」）。
    # 2026-09-26：另落地一处**门禁侧**后果 —— `verify_reduced_motion.py` 的 **R1c**
    # （内置 `spin` 在 reduce 下仍跑）由「只报不判」**升为判红**（`pending` 通道整体移除），
    # 且 R2 覆盖 / `PROJECT_ANIMS` 补上 `--dur-spin` / `--spin-iter` / `spin-soft`。
    ("deliverables/ui-design/motion-spin-audit.md",
     "动效归属**裁定 + 落地记录**（要求已并入 `design-spec.md` §4.4）"),
    # 2026-09-25：#39 的裁定材料，**已裁定 + 已落地**（含一处实测修正：原推荐 ① 的
    # `AppSwitcher` 手段被实测否掉 —— 它是 `<button>` 下拉，B2a 只认 `a[href]`；
    # 最终改用头部 `<Link href="/chat#new">`）。
    # **不是「要求什么」** —— B2a 的要求本身在 `verify_breakpoints.py` 的 docstring 里。
    ("deliverables/ui-design/im-cases-nav-exit-audit.md",
     "导航出口**裁定 + 落地记录**（要求身份在 `verify_breakpoints.py` 的 B2a）"),
    ("docs/", "运维文档"),
)

#: 🚨 **需求类**文档逐个显式排除 —— 每条都要有理由。
EXCLUDED_DOCS: dict[str, str] = {
    "律小智AI法律助手PRD_v1.0.md": "**已被 v2.0 取代**的历史版本",
    "律小智AI法律助手PRD_v2.0.md":
        "**产品级 PRD**（48 个编号标题）。⚠️ **显式排除，不是遗漏**：本矩阵度量的是 "
        "**UI 设计规范**的覆盖率；PRD 的要求由**后端 pytest 基线**（756 passed）与 "
        "`verify_route_coverage.py` / `verify_endpoint_authz.py` 等**代码级**门禁承担，"
        "它们引的是各自文档的节号。⇒ 要度量 PRD 覆盖率应**另建一张矩阵**（未来工作项）。",
    "prd-contract-review-2026-09-16.md": "合同审查**产品需求**（理由同 PRD_v2.0：产品级，非 UI 设计规范）",
    "prd-notification-realtime-push-2026-09-16.md": "通知实时推送**产品需求**（同上）",
    "prd-notification-readpath-2026-09-16.md": "通知已读路径**产品需求**（同上）",
    "prd-v3-gap-closure-spec-2026-09-12.md": "v3 缺口收口**需求 spec**（同上）",
    # ── 2026-09-22/23 新增（后端 `request-text-limits` 一线的产出）──────────────
    "request-text-limits-gate-2026-09-22.md":
        "**门禁实施记录**（「为什么是这个维度 / 口径 / 判据 / 刻意不做」）—— 记录我们自己"
        "建的探针，不是对产品的**要求**（同类：`endpoint-authz-gate*`）。",
    "request-text-limits-closure-2026-09-22.md":
        "**收口记录**（「两处仪器失真」+「落地取值（18 个字段）」）—— 实施记录，同上。",
    "prd-text-limit-decision-gate-2026-09-23.md":
        "**产品级需求 spec**（含「用户故事 / 成功指标 / 验收标准」，17 个编号标题）。"
        "⚠️ **显式排除，不是遗漏**（理由同 PRD_v2.0：本矩阵度量的是 **UI 设计规范**；"
        "产品级需求由**后端 pytest 基线** + 代码级门禁承担）。"
        "⚠️ 该文档由**并行会话**于 2026-09-23 03:42 新建 ⇒ 若其形态改变"
        "（例如转为 UI 设计规范），**必须改判**并移入 `SPEC_FILES`。",
    # ── 2026-09-23 扫盘发现的 3 份未分类（待裁定 ④ 已裁定：全部「排除（显式）」）──
    "agroup-rulings-decision-memo-2026-09-23.md":
        "**决策纪要（裁定清单）** —— 自陈「类型：决策纪要（裁定清单）」，"
        "「裁定栏留空，由产品负责人填写」⇒ 是**裁定结果**的载体，不是对产品的**要求**"
        "（同类：`decisions*` 规则族）。",
    "design-brief-b1-b2-2026-09-23.md":
        "**设计概念稿（草案）** —— 自陈「类型：设计概念稿（工作流前置，待用户定稿后动工）」，"
        "「状态：⚠️ **草案，待产品/设计定稿**」。"
        "🚨 **这一份是唯一需要真判断的**：它**是** UI 设计规范类（含「3. packages/ui 令牌变更清单」），"
        "本可进 `SPEC_FILES`；但按项目硬约束「设计类改动必须先出规范+概念图，**用户定稿后才动工**」"
        "⇒ **未定稿的草案不进分母**（与 §7.4「被后端契约阻塞 ⇒ 绝不建判据」同族："
        "把未定稿的东西算进分母 ⇒ 每天一条无法修的假红）。"
        "⚠️ **定稿后必须改判并移入 `SPEC_FILES`**（届时那份「令牌变更清单」就成了要求）。",
    "rulings-a3-and-design-openitems-2026-09-23.md":
        "**裁定记录（事实调查 + 决策）** —— 自陈「类型：裁定记录」，"
        "「① 属事实问题，在代码库中查证；② 属决策问题，逐项给结论 + 依据」"
        "⇒ 记录「查证与裁定的结果」，不是要求（同上，属记录族）。",
    # ── 2026-09-24 扫盘发现的 1 份未分类（Q14 逮到的，补齐登记）───────────────
    "loop-closure-verification-2026-09-23.md":
        "**验证记录 + 缺陷修复记录** —— 自陈「性质：**验证记录 + 缺陷修复**，不是新决策」。"
        "记录的是**我们自己**跑闭环时揪出的三个缺陷与修法（`JobSchema` 未暴露 `step_state` / "
        "读回端点从未被 HTTP 请求过 / 413 不走决策门），不是对产品的**要求**"
        "（同族：`rulings-a3-*` / `agroup-rulings-*`，属记录族）。"
        "⚠️ 该文档由**并行会话**于 2026-09-23 新建 ⇒ 若其形态改变"
        "（例如转为 UI 设计规范），**必须改判**并移入 `SPEC_FILES`。",
}
#: 判定「这份 `.md` 像不像规范」的门槛：带编号的标题数。
DOC_HEADING_MIN = 3
HEADNUM_RE = re.compile(r"(?m)^#{2,5}\s+\d+(?:\.\d+)*[\s、．.]")

BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
LINE_COMMENT_RE = re.compile(r"(?<![:\w])#.*$", re.M)


def norm(sec: str) -> str:
    """`07` -> `7`；`6.3` 保留。"""
    return ".".join(str(int(p)) for p in sec.split("."))


def key(sec: str) -> list[int]:
    return [int(x) for x in sec.split(".")]


def sections_from_text(text: str) -> dict[str, str]:
    """标题里的节号 + **表格行首列**的节号（见 docstring ④）。标题优先。

    值 = **标题文字**（不含节号）；标题后无文字时退回节号本身。
    """
    out: dict[str, str] = {}
    # 先收表格行（第二列就是名称），再收标题 —— 让标题（更权威的表述）覆盖同名行。
    for m in ROW_SEC_RE.finditer(text):
        out.setdefault(norm(m.group(1)), m.group(2).strip())
    for m in HEAD_RE.finditer(text):
        sec = norm(m.group(2))
        out[sec] = m.group(3).strip() or sec
    return out


def sections_of(path: pathlib.Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return sections_from_text(path.read_text(encoding="utf-8"))


def leaves(secs: dict[str, str]) -> dict[str, str]:
    keys = list(secs)
    return {s: secs[s] for s in keys
            if not any(o != s and o.startswith(s + ".") for o in keys)}


def body_only(text: str) -> str:
    """剥模块 docstring（用 ast，保住行号）+ 剥注释，只留代码体。"""
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
        first = tree.body[0] if tree.body else None
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            for i in range(first.lineno - 1, min(first.end_lineno, len(lines))):
                lines[i] = ""
    except SyntaxError:
        pass  # 语法坏了也要能出报表，退化为只剥注释
    text = "\n".join(lines)
    return LINE_COMMENT_RE.sub("", BLOCK_COMMENT_RE.sub("", text))


def self_test() -> int:
    """回归臂：钉住「表格行节号」这条规则（docstring ④）。

    ⚠️ 这些臂**必须能被注入反向证明**：把 `ROW_SEC_RE` 的 `\\d+\\.\\d+` 放宽成 `\\d+`，
    Q2/Q3 会红；把点号去掉，Q1 会红 —— 两个方向都覆盖了。
    """
    fails: list[str] = []
    total = 0

    def arm(name: str, got, want) -> None:
        nonlocal total
        total += 1
        if got != want:
            fails.append(f"{name}\n      实际 {got!r}\n      期望 {want!r}")

    # Q1：表格行节号必须被收进来
    syn1 = "| 7.3 | 智能问答 | 三栏化 + 引用溯源常驻面板 + 会话历史 | `mockups/03-qa.html` |\n"
    arm("Q1 表格行节号被收录", sections_from_text(syn1), {"7.3": "智能问答"})

    # Q2：纯整数首列（决策编号）**不得**被当成节号
    syn2 = "| 01 | 主色下沉为墨蓝 | ✓ `#4F46E5` → `#274C93` |\n| 17 | 移动端支持 | ★ 全产品考虑移动端 |\n"
    arm("Q2 决策编号不得入表", sections_from_text(syn2), {})

    # Q3：纯整数首列（稿编号 / 问题编号）同样不得入表
    syn3 = "| 08 | 移动端（客户 / 企业法务）：登录注册 / 问答结果 | `mockups/08-mobile-client.html` |\n"
    arm("Q3 稿编号不得入表", sections_from_text(syn3), {})

    # Q4：标题与表格行同名 ⇒ 标题优先，且值必须是**标题文字**而不是节号
    syn4 = "### 7.8 运营后台·案件管理（概念说明）\n| 7.8 | 运营后台·案件管理 | **新增** | 见下方概念说明 |\n"
    arm("Q4 标题优先且带标题文字", sections_from_text(syn4), {"7.8": "运营后台·案件管理（概念说明）"})

    # Q5：叶子裁剪仍要生效（`7` 有子节 ⇒ 不是叶子；`7.3` 是叶子）
    syn5 = "## 07 页面概念图\n| 7.3 | 智能问答 | 三栏化 | `mockups/03-qa.html` |\n"
    secs = sections_from_text(syn5)
    arm("Q5 叶子裁剪", sorted(leaves(secs)), ["7.3"])

    # Q6：真实文件 —— §7.1–§7.8 必须全部在表里（这正是修 ④ 的理由）
    real = sections_of(SPEC_DIR / "design-spec.md")
    missing = [s for s in ("7.1", "7.2", "7.3", "7.4", "7.5", "7.6", "7.7", "7.8") if s not in real]
    arm("Q6 design-spec 收全 §7.1–7.8", missing, [])
    # Q6b：决策编号 `| 11 |`–`| 17 |`（无对应章）**不得**被当成节号。
    # ⚠️ 别写成 `1`/`2`：`## 01`/`## 02` 经 `norm()` 归一化后**就是**合法节号（Q6c 正面钉住）。
    arm("Q6b 决策编号 11–17 未被误收",
        [s for s in ("11", "12", "13", "14", "15", "16", "17") if s in real], [])
    # Q6c：章标题的归一化必须保留（`## 00 现状诊断` ⇒ `0`）
    arm("Q6c 章标题归一化保留", "0" in real, True)

    # ── ⑥ 的回归臂 ──────────────────────────────────────────────────────
    # Q7 🚨 **DOCNAME_RE 必须捕获「词干」**（下游是 `m.group(1) + ".md"`）。
    # 第一版我塞了带扩展名的 `p.name` ⇒ 归因结果变成 `design-spec.md.md`
    # ⇒ `named` 全空、`scope_out` 全非空 ⇒ 19 个门禁的引用被整体跳过，
    # `design-spec.md` 覆盖从 26 **假降到 4**。**是「改前/改后数字对比」逮到的。**
    m7 = DOCNAME_RE.search("规范依据（`design-spec.md` §4.2）")
    arm("Q7 DOCNAME_RE 捕获词干（拼回 .md 不得重复）",
        (m7.group(1) + ".md") if m7 else None, "design-spec.md")
    # Q8：太通用的名字不算「点名了某份规范」（否则提一句 `evidence/README.md` 就会跳过引用）
    arm("Q8 README.md 不得被当成点名", DOCNAME_RE.search("见 `evidence/README.md`"), None)
    # Q9：**动态发现**必须生效 —— 矩阵外的清扫记录稿也要能被点名
    # （它正是 6 条「无主引用」指向的那份文档）
    arm("Q9 动态发现矩阵外文档名",
        bool(DOCNAME_RE.search("`round15b-fixed-but-unguarded-sweep-2026-09-20.md` §3.32")), True)

    # Q10–Q12：三档分类必须互斥且可证伪
    known = {"4.2"}
    arm("Q10 矩阵内的节 ⇒ mention", classify_ref("4.2", known, set()), "mention")
    arm("Q11 越界但点名了矩阵外文档 ⇒ explained",
        classify_ref("3.32", known, {"round15b.md"}), "explained")
    arm("Q12 越界且无人认领 ⇒ unowned",
        classify_ref("3.32", known, set()), "unowned")

    # Q13–Q14：文档范围守卫
    # ⚠️ `doc_scope()` 的契约是**仓库相对路径**（见其 docstring）—— 首轮我把期望写成
    # 文件名 ⇒ Q13 红。**先分清是「判据错」还是「我的期望值没有出处」**：这里是后者。
    scope = doc_scope()
    arm("Q13 三份矩阵内文档都被判「纳入」",
        sorted(r for r, _n, s in scope if s == "纳入"),
        sorted(p.relative_to(ROOT).as_posix() for p in SPEC_FILES))
    arm("Q14 扫盘无「未分类」文档（新文档必须登记）",
        [r for r, _n, s in scope if s.startswith("🚨")], [])

    # ── ⑦ 的回归臂：CI 内覆盖 ─────────────────────────────────────────────
    ci_names, ci_reason = _ci_gate_names()
    arm("Q15 取到分档且原因为空（空集 + 空原因 = 静默当 0，禁止）",
        (bool(ci_names), ci_reason), (True, ""))
    # 钉住「导入的确实是运行器那份分档」，不是随便一个集合。
    # ⚠️ 2026-09-26：`NOT_GATED` 被 `browser-all` job 清空 ⇒ **旧的负例
    #    （`verify_runtime_health.py` ∉ `ci_names`）当场失效**。
    #    ⇒ 改成**三条**，把边界钉得更死（比原来更强，不是把断言删掉）：
    #      ① `GATED` 的探针 ∈（`verify_im_mobile_nav.py`）；
    #      ② **`CI_ELSEWHERE` 的探针也算「CI 里会跑」**（`verify_runtime_health.py`）
    #         —— 这一条正是 ⑦ 的核心：判据是「会不会跑」，与「在哪个 job 跑」无关；
    #      ③ **非门禁脚本 ∉**（`visual_baseline.py` 在 `NON_GATE_SCRIPTS` 里）
    #         —— 保证这个集合不是「把 evidence/ 全塞进去」。
    arm("Q15b 分档 = CI 会跑的那一档（含 GATED 与 CI_ELSEWHERE，不含非门禁脚本）",
        ("verify_im_mobile_nav.py" in ci_names,
         "verify_runtime_health.py" in ci_names,
         "visual_baseline.py" in ci_names), (True, True, False))
    # Q16：`ci_gap` 是纯函数 —— 只在 `cov3` 有、`cov4` 没有时才算缺口
    arm("Q16 ci_gap 只挑「有判据但 CI 里不跑」的节",
        ci_gap({"1": "a", "2": "b", "3": "c"}, {"1", "2"}, {"1"}), ["2"])
    # Q17 🚨 取不到分档 ⇒ 必须「不给数字」（**不是**当 0）。把返回契约钉死：
    # 将来有人把它改成 `return set(), ""`，报表就会静默地把 39 个全算成 CI 内。
    _missing = object()
    _saved = sys.modules.get("run_ci_probes", _missing)
    sys.modules["run_ci_probes"] = None      # ⇒ `import` 抛 ImportError
    try:
        _names_bad, _reason_bad = _ci_gate_names()
    finally:
        if _saved is _missing:
            sys.modules.pop("run_ci_probes", None)
        else:
            sys.modules["run_ci_probes"] = _saved
    arm("Q17 取不到分档 ⇒ 空集 + 非空原因（「取不到」不是「0」）",
        (_names_bad, bool(_reason_bad)), (set(), True))

    print("=" * 82)
    print(f"自检：{total - len(fails)}/{total} 通过")
    for f in fails:
        print(f"  ✗ {f}")
    print("=" * 82)
    return 1 if fails else 0


def docstring_of(text: str) -> str:
    """模块 docstring 原文（取不到就空串）。

    用来区分**适用范围声明**（docstring 里写「规范依据（`design-spec.md` §8.2）」）
    与**附带提及**（`# 见 admin-gap-analysis.md #25`）。见 docstring ⑤。
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return ""
    if not tree.body:
        return ""
    first = tree.body[0]
    if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)):
        return first.value.value
    return ""


def classify_ref(sec: str, known_secs: set[str], names_out: set[str]) -> str:
    """把「只出现在 docstring / 注释里」的节号分成三档（见 docstring ⑥）。

    | 档 | 条件 | 性质 | 修法 |
    |---|---|---|---|
    | `mention` | 该节**在矩阵内** | **口径**问题（判据写在代码体之外） | 无需修 |
    | `explained` | 越界，但该门禁**点名了矩阵外文档** | 已解释 | 无需修（归因可见） |
    | `unowned` | 越界，且**没人认领** | 🚨 **归属缺口** | 补文档名，或改引用 |

    🚨 抽出成纯函数是为了**可证伪**：`unowned` 与 `explained` 的差别只在一句
    「有没有点名文档」，写在 `build()` 里就测不到。
    """
    if sec in known_secs:
        return "mention"
    if names_out:
        return "explained"
    return "unowned"


def doc_scope() -> list[tuple[str, int, str]]:
    """扫盘列出「像规范」的 `.md`（编号标题数 ≥ `DOC_HEADING_MIN`）并分类。

    ⚠️ 存在的理由：报表只写「纳入 3 份」时，读者会默认「仓库里只有 3 份规范」。
    实测仓库里有 **33** 份带编号章节的 `.md`，其中 5 份是**产品需求**（PRD / spec）。
    ⇒ 把范围**显式打印**，并给**未分类**的文档报警（同 CI 的「新探针未分类 ⇒ 失败」）。

    返回 `(仓库相对路径, 编号标题数, 状态)`；状态 ∈ 纳入 / 排除（规则）/ 排除（显式）/ 🚨 未分类。
    """
    in_matrix = {p.name for p in SPEC_FILES}
    out: list[tuple[str, int, str]] = []
    for root in DOC_ROOTS:
        base = ROOT / root
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.md")):
            rel = p.relative_to(ROOT).as_posix()
            n = len(HEADNUM_RE.findall(p.read_text(encoding="utf-8", errors="replace")))
            if n < DOC_HEADING_MIN:
                continue
            if p.name in in_matrix:
                state = "纳入"
            elif any(rel.startswith(pre) for pre, _ in EXCLUDE_RULES):
                state = "排除（规则）"
            elif p.name in EXCLUDED_DOCS:
                state = "排除（显式）"
            else:
                state = "🚨 未分类"
            out.append((rel, n, state))
    return out


# ── ⑦ CI 内覆盖：判据**存在** ≠ 判据**在 CI 里会跑** ─────────────────────────────
def _ci_gate_names() -> tuple[set[str], str]:
    """CI 里**真的会执行**的门禁名 = `GATED` + `GATED_STATIC` + `CI_ELSEWHERE`（**从运行器导入**）。

    返回 `(集合, 取不到的原因)`。**取不到时集合为空、原因非空串** ——
    调用方必须据此**不输出 v4 数字**，而不是当成 0（理由见模块 docstring ⑦）。

    ⚠️ 2026-09-23：加入 `CI_ELSEWHERE`（「已在 CI、但**不在 `evidence` job**」，
    例如 `frontend` job 里的 `verify_api_base_baked.py`）。
    **判据是「它在 CI 里会不会跑」，与「在哪个 job 跑」无关** —— 漏掉这一档
    会把「已在 CI 里跑」的门禁**误报成「从不执行」**（v4 少算 ⇒ 零判据虚高）。
    """
    sys.path.insert(0, str(HERE))
    try:
        import run_ci_probes as runner
    except Exception as exc:  # noqa: BLE001  环境问题 ⇒ 不给数字，不猜
        return set(), f"取不到运行器分档（{exc!r}）"
    names = ({n for n, _ in runner.GATED} | set(runner.GATED_STATIC)
             | set(getattr(runner, "CI_ELSEWHERE", {})))
    if not names:
        return set(), "运行器分档为空"
    return names, ""


def ci_gap(secs: dict[str, str], cov3: set[str], cov4: set[str]) -> list[str]:
    """**纯函数**：判据存在（∈`cov3`）但 CI 里不会跑（∉`cov4`）⇒ 「CI 内零判据」。"""
    return sorted((s for s in secs if s in cov3 and s not in cov4), key=key)


def build() -> int:
    gates = sorted(HERE.glob("verify_*.py"))
    if not gates:
        print("[ENV] evidence/ 下没有 verify_*.py ⇒ 报表无意义", file=sys.stderr)
        return 2

    docs: dict[str, dict[str, str]] = {}
    missing = [p.name for p in SPEC_FILES if not p.exists()]
    for p in SPEC_FILES:
        docs[p.name] = leaves(sections_of(p))
    if missing:
        print(f"[ENV] 点名的规范文件不存在：{missing} ⇒ 报表不完整", file=sys.stderr)
        return 2

    has: dict[str, set[str]] = collections.defaultdict(set)
    for name, secs in docs.items():
        for s in secs:
            has[s].add(name)

    cov2: dict[str, set[str]] = {n: set() for n in docs}
    cov3: dict[str, set[str]] = {n: set() for n in docs}
    # ⑦ 仅「CI 里真的会跑」的门禁（`GATED` + `GATED_STATIC`）。
    # 🚨 取不到分档 ⇒ `ci_reason` 非空 ⇒ 后面**不输出 v4 数字**（不当 0）。
    ci_names, ci_reason = _ci_gate_names()
    cov4: dict[str, set[str]] = {n: set() for n in docs}
    mention_only: dict[str, set[str]] = collections.defaultdict(set)
    unowned: dict[str, set[str]] = collections.defaultdict(set)
    explained_out: dict[str, set[str]] = collections.defaultdict(set)
    out_of_scope: dict[str, set[str]] = collections.defaultdict(set)
    scope_skipped: list[str] = []
    ambiguous: list[str] = []
    stale: list[str] = []

    for g in gates:
        raw = g.read_text(encoding="utf-8", errors="replace")
        body = body_only(raw)
        # ⚠️ `named` 从 **raw** 取：docstring 里写「规范依据（`design-spec.md` §8.2）」是
        # **正当的适用范围声明**，代码体不必重复文档名。实测若改成「与 refs 同一段文本」，
        # `verify_breakpoints.py` 的 §4.1/§8.2/§9 会因归因歧义被丢掉（覆盖 21→18，**过度修正**）。
        named_all = sorted({m.group(1) + ".md" for m in DOCNAME_RE.finditer(raw)})
        named = [d for d in named_all if d in docs]
        for d in named_all:
            if d not in docs:
                out_of_scope[d].add(g.name)
        # 矩阵外的**适用范围声明**（docstring 里的）⇒ 禁止退化为内容归因。
        scope_out = sorted({m.group(1) + ".md" for m in DOCNAME_RE.finditer(docstring_of(raw))}
                           - set(docs))
        pairs: list[tuple[str, dict[str, set[str]]]] = [(raw, cov2), (body, cov3)]
        if g.name in ci_names:
            # ⑦ 走**同一段归因代码**，只多接一个出口 ⇒ 与 v3 **在构造上**不可能漂移
            pairs.append((body, cov4))
        for text, cov in pairs:
            refs = {norm(m.group(1)) for m in REF_RE.finditer(text)}
            if not refs:
                continue
            if named:
                targets = set(named)
            elif scope_out:
                # 🚨 不许退化：`verify_design_tokens.py` 的 `§6.4` 指的是
                # `phase3-implementation.md` §6.4，退化后会被**误挂**到
                # `im-mobile-nav-spec.md` 的同名 `§6.4` 上（**假绿**）。
                # ⚠️ 身份判据用 `cov is cov3`（**不是** `text is body`）：⑦ 之后
                # **有两个 pass 的 `text` 都是 `body`**，用 text 判会**重复登记**一次。
                if cov is cov3:
                    scope_skipped.append(
                        f"{g.name}: {['§'+s for s in sorted(refs, key=key)]} "
                        f"（只点名矩阵外文档 {scope_out}）")
                continue
            else:
                cand = {d for r in refs for d in has.get(r, ())}
                if len(cand) != 1:
                    if not cand and cov is cov3:
                        stale.append(f"{g.name}: {['§'+s for s in sorted(refs, key=key)]}")
                    elif cov is cov3:
                        ambiguous.append(
                            f"{g.name}: {['§'+s for s in sorted(refs, key=key)]} "
                            f"cand={sorted(cand)}")
                    continue
                targets = cand
            for d in targets:
                cov.setdefault(d, set()).update(refs)
        refs_raw = {norm(m.group(1)) for m in REF_RE.finditer(raw)}
        refs_body = {norm(m.group(1)) for m in REF_RE.finditer(body)}
        known_secs = set(has)
        # 「点名了矩阵外文档」⇒ 它能**解释**自己的越界节号（那些节号属于那份文档）。
        # 🚨 少了这一条，「补文档名 = 解歧义」就成了空话：节号仍不在矩阵内，
        # 于是照样落进 `unowned`，看起来像没修。
        names_out = {d for d in named_all if d not in docs}
        for r in refs_raw - refs_body:
            kind = classify_ref(r, known_secs, names_out)
            if kind == "mention":
                mention_only[g.name].add(r)
            elif kind == "explained":
                explained_out[g.name].add(r)
            else:
                unowned[g.name].add(r)

    print("=" * 82)
    print("规范覆盖率报表（只在**判据代码体**内提取节号；只统计**叶子**小节）")
    print("=" * 82)
    print(f"门禁文件 {len(gates)} 个 · 纳入矩阵的规范 {len(docs)} 份\n")

    # ---- 文档范围（**为什么只有这几份**）----
    scope = doc_scope()
    unclassified = [r for r in scope if r[2].startswith("🚨")]
    print("—— 📚 文档范围（结论只对**纳入**的文档成立）——")
    print(f"     纳入（{len(docs)}）：{sorted(docs)}")
    print(f"     排除（规则 {sum(1 for _r, _n, s in scope if s == '排除（规则）')} 份）：")
    for pre, why in EXCLUDE_RULES:
        print(f"       · {pre}* —— {why}")
    print(f"     排除（显式 {sum(1 for _r, _n, s in scope if s == '排除（显式）')} 份）：")
    for name, why in EXCLUDED_DOCS.items():
        print(f"       · {name} —— {why}")
    if unclassified:
        print(f"     🚨 **扫盘发现 {len(unclassified)} 份未分类的规范类文档**"
              f"（必须登记进 `SPEC_FILES` 或 `EXCLUDED_DOCS`）：")
        for rel, n, _s in unclassified:
            print(f"       · {rel}（{n} 个编号标题）")
        print("       不登记 ⇒ 它们既不在分母里，也不在「已声明排除」里 ⇒ **范围不可证伪**。")
    else:
        print(f"     ✅ 扫盘未发现未分类文档（阈值：编号标题 ≥ {DOC_HEADING_MIN}）")
    print()

    total3 = 0
    total_gap = 0
    for name, secs in docs.items():
        z2 = sorted((s for s in secs if s not in cov2[name]), key=key)
        z3 = sorted((s for s in secs if s not in cov3[name]), key=key)
        total3 += len(z3)
        inflated = sorted(set(z3) - set(z2), key=key)
        z4 = sorted((s for s in secs if s not in cov4[name]), key=key)
        gap = [] if ci_reason else ci_gap(secs, cov3[name], cov4[name])
        total_gap += len(gap)
        print(f"—— {name}：叶子小节 {len(secs)} 个 ——")
        print(f"     v2（提到即算）覆盖 {len(secs)-len(z2)} / 零引用 {len(z2)}")
        print(f"     v3（判据内）  覆盖 {len(secs)-len(z3)} / **零引用 {len(z3)}**")
        if not ci_reason:
            # ⚠️ 「v4 覆盖」必须用 `z4`（`secs - cov4`）算，**不能**用 `len(secs)-len(gap)`：
            # 后者会把「**根本没有判据**」的节也算成「CI 内覆盖」。
            # 第一版就是这么错的 —— **是数字自己暴露的**：design-spec 报 32，而 v3 才 26，
            # 32 > 26 在构造上不可能（`cov4 ⊆ cov3`）。
            print(f"     v4（**CI 内会跑**）覆盖 {len(secs)-len(z4)} / "
                  f"**CI 内零判据 {len(gap)}**")
            if (len(secs) - len(z4)) + len(gap) != len(secs) - len(z3):
                print("     🚨 **仪器自相矛盾**：v4 覆盖 + CI 内零判据 ≠ v3 覆盖 "
                      "⇒ 归因出口接错了（这条不变量就是用来逮第一版那个错的）")
        if inflated:
            print(f"     ⚠️ 仅因「被提到」而虚增覆盖：{['§'+s for s in inflated]}")
        for s in z3:
            print(f"       §{s}  {secs[s][:58]}")
        if gap:
            print(f"     🚨 **CI 内零判据**（有判据，但那条判据在 CI 里**从不执行**）{len(gap)} 个：")
            for s in gap:
                print(f"       §{s}  {secs[s][:52]}")
        print()

    if mention_only:
        print("—— 节号只出现在 docstring / 注释里（**提到而非判**：该节**确实在矩阵内**）——")
        for g, rs in sorted(mention_only.items()):
            print(f"     {g}: {['§'+s for s in sorted(rs, key=key)]}")
        print("     含义：这一节有门禁在**管**，只是判据写在代码体之外 ⇒ 属**口径**问题，不是盲区。")
        print()
    if unowned:
        print("—— 🚨 **无主引用**：节号**不在任何已纳入文档**里（属**归属缺口**，不是口径问题）——")
        for g, rs in sorted(unowned.items()):
            print(f"     {g}: {['§'+s for s in sorted(rs, key=key)]}")
        print("     ⚠️ 它们此前被混进「提到而非判」，于是**看起来像已经解释过了**。")
        print("     两种成因，修法不同：① 节号属于**未纳入矩阵的文档**（多为 `product-strategy/`")
        print("       下的记录稿）⇒ 在门禁里**点名那份文档**（成本≈0，同 ⑤ 的「补文档名 = 解歧义」）；")
        print("       ② 节号**写错了** ⇒ 改引用。")
        print()
    elif explained_out:
        n_exp = sum(len(v) for v in explained_out.values())
        print(f"—— ✅ 越界节号**已被「点名矩阵外文档」解释**（{n_exp} 条，见上一段）——")
        for g, rs in sorted(explained_out.items()):
            print(f"     {g}: {['§'+s for s in sorted(rs, key=key)]}")
        print("     ⇒ 它们**不是**无主引用：节号属于该门禁自己点名的矩阵外文档。")
        print()
    if out_of_scope:
        print("—— ⚠️ 归因到**矩阵外文档**（这些引用**不计入**规范覆盖率）——")
        for d, gs in sorted(out_of_scope.items()):
            print(f"     {d}: {sorted(gs)}")
        print("     说明：矩阵外的文档（我们自己的审计稿 / 实施记录）不是「有要求的规范」，")
        print("           故不纳入分母。点名它们的门禁，其引用的**规范**归属需人工判断。")
        print()
    if scope_skipped:
        print("—— ⚠️ 只点名矩阵外文档 ⇒ **不退化**为内容归因（否则会把节号误挂到同名节上）——")
        for line in scope_skipped:
            print(f"     {line}")
        print()
    if ambiguous:
        print("—— 归因歧义（多份文档含同名节号 ⇒ 不瞎归，需人工分诊）——")
        for line in ambiguous:
            print(f"     {line}")
        print()
    if stale:
        print("—— 引了节号但纳入矩阵的文档都不含它（引用号可能过期，或属于未纳入的文档）——")
        for line in stale:
            print(f"     {line}")
        print()

    print(f"[汇总] 零引用**叶子**小节合计 **{total3}** 个")
    if ci_reason:
        # 🚨 与棘轮的「取不到条数判红」同一条纪律：**「取不到」不是「0」**
        print(f"[汇总] 🚨 v4（CI 内覆盖）**不输出**：{ci_reason}"
              f" —— 取不到分档 ≠ 全部都在 CI 里跑，**不给数字**。")
    else:
        print(f"[汇总] **CI 内零判据**的叶子小节合计 **{total_gap}** 个"
              f"（判据存在、但只在 CI 从不执行的探针里）")
        print(f"[汇总] CI 会执行 {len(ci_names)} 个门禁"
              f"（`GATED` + `GATED_STATIC` + `CI_ELSEWHERE` —— **判据是「在 CI 里会不会跑」，"
              f"与「在哪个 job 跑」无关**）。")
        _never = len(gates) - len(ci_names)
        if _never:
            print(f"       另 {_never} 个**在 CI 里从不执行**"
                  f"（`NOT_GATED`：缺浏览器 / dev server / 生产产物）。")
            print("⚠️ v4 只度量「**在 CI 里**有没有判据」。`NOT_GATED` 的探针在本机跑得通、"
                  "能守住那些节 —— 缺的是**环境前提**，不是判据本身。")
        else:
            # ✅ 2026-09-26：`browser-all` job 落地后 `NOT_GATED` 清空 ⇒ 这一支成为常态。
            #    ⚠️ 仍然要**显式**打出来：「一个都不剩」是**结论**，不是「这一块没写」。
            print("       ✅ **`NOT_GATED` 已清空（0 个）** —— 仓库里每一条门禁"
                  "都至少有一个 job 会跑它（2026-09-26 · 拍板 §11.4-③）。")
            print("⚠️ ⚠️ **但「有个 job 会跑它」≠「每个 PR 都会跑它」**：`browser-all` 只在"
                  "**主干 / 手动**触发 ⇒ 那 14 条对 **PR 而言依然不跑**。"
                  "本报表的 v4 记的是前者。")
    print("⚠️ 零引用 ≠ 没判据：本报表只缩小候选，落点必须读门禁判据函数再分诊。")
    return 0


def ci_gap_only() -> int:
    """**只打印「CI 内零判据」那一块** —— 给 `run_ci_probes.py --list` 的**页脚**调用。

    为什么是这个形态（沿用本项目 `--static-audit` 的既定解法）：
    **只报告不判定、永远 `exit 0`**，并印在「**本来就会在 CI 里跑的那一步**」的页脚里
    ⇒ 缺口**每次 CI 自曝**，且**不需要新增 CI 步骤、不可能让 CI 变红**。

    ⚠️ 不做机器解析：**由报表自己排版、运行器原样回显** ⇒ 没有「格式耦合」。
    ⚠️ `build()` 的 `rc` 原样返回（`2` = 环境问题）⇒ 运行器据此**不出数字**，不猜。
    """
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = build()
    print("—— 📉 CI 内零判据（**判据存在，但那条判据在 CI 里从不执行**）——")
    for ln in buf.getvalue().splitlines():
        if ln.startswith("     v4（") or (
                ln.startswith("[汇总]") and "零引用**叶子**小节合计" not in ln) or (
                ln.startswith("—— ") and "：叶子小节 " in ln):
            print(ln)
    print("     ⇒ 明细 + 待裁定：`evidence/spec_coverage_ci_gap_2026-09-23.txt`")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description="规范覆盖率报表（不是门禁）")
    ap.add_argument("--gates-only", action="store_true",
                    help="只打印每份规范的汇总行，不列零引用明细")
    ap.add_argument("--ci-gap-only", action="store_true",
                    help="只打印「CI 内零判据」块（给运行器页脚用；只报告不判定）")
    ap.add_argument("--self-test", action="store_true",
                    help="只跑仪器自检（钉住「表格行节号」规则），不出报表")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if args.ci_gap_only:
        return ci_gap_only()
    if args.gates_only:
        # 复用同一套逻辑但只输出汇总：简单起见，捕获并过滤
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = build()
        for ln in buf.getvalue().splitlines():
            if ln.startswith(("——", "     v", "[汇总]")):
                print(ln)
        return rc
    return build()


if __name__ == "__main__":
    raise SystemExit(main())
