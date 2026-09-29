#!/usr/bin/env python
"""引用一致性门禁：规范引用**不许写行号**，`§x.y「锚点」` 的锚点必须**真在该节**。

## 为什么要这条判据

`design-spec.md:NNN` 式的引用**会腐烂**：往规范**中间插内容** ⇒ 其后行号整体 +N。
实测（2026-09-26）：全仓 `design-spec.md:NNN` 式引用 **100 处**，一次全量审计发现
**活文件里 39 处已失效**（规范那一行现在是**空行**或**表格分隔线**）；
累积漂移已达 **+52**（§8.3）乃至 **+72**（§8.5）。
⇒ 根治：引用改成 **「节号 + 锚点文本」**（`§8.3「相邻可点元素间距 ≥ 8px」`）——
**节号与锚点文本都不随行数漂移**。

## 五条判据（3 判红 + 3 只报）

| 判据 | 内容 | 档 |
|---|---|---|
| **G1** | 活文件里不得出现 `design-spec.md:NNN`（带前缀的行号引用） | **判红** |
| **G2** | 同一行里不得**既提 `design-spec` 又写裸 `` `:NNN` ``**（点名后写简称那一类） | **判红** |
| **G3** | `§x.y「锚点」` 的锚点**在已登记文档里存在**，但**不在 §x.y 里** ⇒ 引错了节 | **判红** |
| **G3s** | 锚点在已登记文档里**根本找不到** ⇒ 可能引的是**未登记文档**，也可能引文不够精确 | **只报** |
| **G4** | `§x.y` 的节号不在任何已登记文档里 | **只报** |
| **G5** | 裸 `` `:NNN` `` 且同行既无文档名也无别的文件名 | **只报** |

### 🚨 为什么 G3 判红要加「在已登记文档里存在」这个前置

**本仓的 `§x.y` 不一定是 design-spec 的节号** —— `§3.32` / `§2.29.3` 是**后端文档**的，
`§5.1「✅ 定稿」` 是**某份设计草稿自己**的（`design-spec.md` 压根没有 5.1），
`§7「已知遗留」` 是 `admin-gap-analysis.md` 自己的 §7。
⇒ 若把「锚点不在 §x.y 里」一律判红，会**稳定假红**（实测 41 条里过半是这一类）。
⇒ 判红的**充分条件**：我们**知道**这句话住在哪一节，而它**不住在你引的那一节**。
   这时消息能直接说「实际出现在 X §a.b」，可操作。
⇒ 剩下的（找不到）降为 G3s **只报**：**宁可少判，不可错判**。

### 🚨 归一化：锚点比的是「引用」，不是「排版」

比对前剥掉 `**` / 反引号 / **HTML 标签**，并**忽略全部空白**。
依据：`design-spec.html`（规范的 HTML 版）里同一句写作
`§4.2「断点 640 / 1024 / 1280 / <strong>1600</strong>」`——`<strong>` 是排版不是内容；
表格里 `/` 两侧有无空格同理。
锚点里的 `：` `·` `…` 视为**分段符**：每段都要在该节里。
（`§8.6「离线期间的拍照…全部进队列」` 是**省略式引用**，`…` 就是作者标的省略处。）

### 🚨 管不到的（**已声明的边界**，不是漏掉）

- **模板化锚点**（含 `{…}`）：`evidence/*.py` 里会写 `§4.4「{sc}」` 这种**格式化占位**，
  运行期才成型 ⇒ 静态不可验 ⇒ 落 G3s 只报。
- **同行既无文档名也无 `§` 的裸 `` `:NNN` ``**：**天生有歧义** ——
  `` `billing.py` 的 `:72` `` 是后端文件的，表格体里孤零零一个 `` `:195` `` 才是规范的。
  **光看这一行分不出来** ⇒ G5 只报（判了必然假红）。
- **门禁自身**（`verify_spec_refs.py`）与 `evidence/*.txt`、`evidence/*.log`、每日日志
  `.workbuddy-ai/memory/20*.md` **不扫**：前三类是**历史存证**，每日日志是 **append-only**；
  门禁自身是因为 `SELFTEST` 里的夹具字符串**故意**长得像坏引用（由 `--self-test` 负责验）。

## 退出码

- `0` 通过
- `1` 有必判违规（G1 / G2 / G3）
- `2` 环境问题（规范读不到、节解析为空、自检失败 ⇒ **工具坏了，不是产品坏了**）

用法：

    python evidence/verify_spec_refs.py
    python evidence/verify_spec_refs.py --self-test     # 合成夹具自检（判定函数对不对）
    python evidence/verify_spec_refs.py --inject        # 真实规范上的**注入反证**（它会不会红）
    python evidence/verify_spec_refs.py --report-only   # 只打印，不判红

## 🚨 Why `--inject` 不能省

`--self-test` 用的是**合成夹具**（`SPEC_FIXTURE`，一份 4 节的小文档），它证明的是
「**判定函数**会红」；`--inject` 用的是**真实规范**，证明的是「**在这个仓库上**会红」。
少了后者，「判据写错了、而夹具恰好按同样的方式写错」这种**同向错误**永远看不出来 ——
与本项目「门控改恒放行 ⇒ 一条没红 = 此前从未执行」是同一条纪律。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

#: 已登记的规范文档（`§x.y「锚点」` 的锚点在这几份里找）。
SPEC_DOCS: tuple[str, ...] = (
    "deliverables/ui-design/design-spec.md",
    "deliverables/ui-design/im-mobile-nav-spec.md",
    "deliverables/ui-design/mobile-feature-integration-spec.md",
)

SKIP_DIR = {".git", "node_modules", ".next", "dist", "__pycache__", ".venv", "_tmp_tests", "storage"}
SKIP_SUFFIX = {".pyc", ".db", ".png", ".gz", ".lock", ".tsbuildinfo", ".bak"}
DAILY = re.compile(r"^\.workbuddy-ai/memory/20\d\d-\d\d-\d\d\.md$")
#: 本文件自带合成夹具 ⇒ 自扫必然假红（夹具由 `--self-test` 负责验）。
SELF_REL = "evidence/verify_spec_refs.py"

RX_LINE_REF = re.compile(r"design-spec\.md:(\d+)")
RX_BARE = re.compile(r"`:(\d+)`")
RX_SEC_ANCHOR = re.compile(r"§(\d+(?:\.\d+)*)「([^」]+)」")
RX_SEC = re.compile(r"§(\d+(?:\.\d+)*)")

HEAD = re.compile(r"^(#{2,5})\s+([0-9]+(?:\.[0-9]+)*)\s*[、．.]?\s*(.*)$")
ROW = re.compile(r"^\|\s*([0-9]+(?:\.[0-9]+)+)\s*\|")


# ------------------------------------------------------------------ 文本归一化

def norm(no: str) -> str:
    """`01` → `1`、`7.10` → `7.10`（只去前导零，不重排）。"""
    if not re.fullmatch(r"\d+(?:\.\d+)*", no):
        return no
    return ".".join(str(int(x)) for x in no.split("."))


def _strip_md(s: str) -> str:
    """剥掉**排版**标记：粗体、反引号、**良构** HTML 标签。

    🚨 标签必须要求 `<` 后紧跟字母（或 `/` + 字母），且中间不许再出现 `<` / `>`。
    踩过：写成 `<[^>]+>` 时，§8.2 表里的 `< 640px` 那个裸 `<` 会一路吃到**下一个 `>`**
    （下一行的引用块标记 `>`）⇒ **整张表被删光** ⇒ `§8.2「单列」` 被误判成「锚点找不到」。
    ⇒ 判据本身把正文吃掉，比它要抓的缺陷更危险。自检 Q18 钉住这一点。
    """
    s = s.replace("**", "").replace("`", "")
    return re.sub(r"</?[A-Za-z][^<>]*>", "", s)


def _squash(s: str) -> str:
    """剥排版标记 + **去掉全部空白** ⇒ 只比「引用」本身。"""
    return re.sub(r"\s+", "", _strip_md(s))


def _drop_refs(s: str) -> str:
    """把 `§x.y「…」` 形式的**引用**摘掉：锚点只许命中**正文**，不许命中别人的引用。

    🚨 不摘的话会**自证成立**：`§8.1「im 桌面视为放大适配」` 曾在 §8.2 里被判「找到」——
    而 §8.2 那句话本身就是**引用 §8.1 的那条引用**（`依据 §8.1「…」+ …`）。
    ⇒ 「找到」必须意味着「正文里有这句话」。
    """
    return RX_SEC_ANCHOR.sub(" ", s)


def anchor_parts(anchor: str) -> list[str]:
    """锚点切成若干段（`：` `·` `…` `...` 是分段符），每段都要在该节里。

    分段是**受控放宽**：`§8.6「离线期间的拍照…全部进队列」` 用 `…` 明示省略，
    要求整串字面命中会把它误判成失效。

    🚨 返回的段**必须已经 `_squash()`**（与节文本同口径）。踩过：只在**长度检查**里
    squash、返回的却是原文 ⇒ 带空格的段去比已压缩的节文本 ⇒ **永远不中**
    （自检 Q2「干净对照」臂正是这么红的，而 Q16 无空格版却通过 —— 这个不对称就是线索）。
    """
    parts: list[str] = []
    for p in re.split(r"[：·…]|\.\.\.", _strip_md(anchor)):
        s = _squash(p)
        if len(s) >= 2:
            parts.append(s)
    return parts


def _parts_in(parts: list[str], squashed: str) -> bool:
    return all(p in squashed for p in parts)


# ------------------------------------------------------------------ 规范解析

def parse_sections(lines: list[str]) -> dict[str, tuple[int, int]]:
    """节号 → (起始行, 结束行)（1-based，含端点）。标题 ∪ §07 那种「表格行当节」。"""
    marks: list[tuple[int, str]] = []
    for i, ln in enumerate(lines, 1):
        m, r = HEAD.match(ln), ROW.match(ln)
        if m:
            marks.append((i, norm(m.group(2))))
        elif r:
            marks.append((i, norm(r.group(1))))
    out: dict[str, tuple[int, int]] = {}
    for k, (start, no) in enumerate(marks):
        end = marks[k + 1][0] - 1 if k + 1 < len(marks) else len(lines)
        out.setdefault(no, (start, end))
    return out


#: (节号 → [(文档, 压缩文本)], [(文档, 节号, 压缩文本)])
Index = tuple[dict[str, list[tuple[str, str]]], list[tuple[str, str, str]]]


def build_index(docs: dict[str, list[str]]) -> Index:
    by_no: dict[str, list[tuple[str, str]]] = {}
    all_secs: list[tuple[str, str, str]] = []
    for doc, lines in docs.items():
        for no, (a, b) in parse_sections(lines).items():
            sq = _squash(_drop_refs("\n".join(lines[a - 1:b])))
            by_no.setdefault(no, []).append((doc, sq))
            all_secs.append((doc, no, sq))
    return by_no, all_secs


def load_specs() -> tuple[dict[str, list[str]], Index]:
    docs: dict[str, list[str]] = {}
    for rel in SPEC_DOCS:
        p = ROOT / rel
        if not p.exists():
            continue
        docs[rel] = p.read_text(encoding="utf-8", errors="replace").split("\n")
    return docs, build_index(docs)


def iter_live_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR and not d.startswith(".next")]
        for fn in filenames:
            p = pathlib.Path(dirpath) / fn
            rel = p.relative_to(ROOT).as_posix()
            if p.suffix.lower() in SKIP_SUFFIX:
                continue
            if rel == SELF_REL:
                continue  # 自带合成夹具（`SELFTEST`）⇒ 自扫必然假红，由 `--self-test` 验
            if rel.startswith("evidence/") and p.suffix.lower() in {".txt", ".log"}:
                continue  # 历史存证（运行快照 / 裁定记录）按纪律不动
            if DAILY.match(rel):
                continue  # 每日日志是 append-only 的历史，不重写
            yield rel, p


# ------------------------------------------------------------------ 扫描

def scan_text(rel: str, text: str, spec_lines: int, index: Index,
              ) -> tuple[list[str], list[str], list[str], list[str]]:
    """返回 (判红, 只报G3s, 只报G4, 只报G5)。

    抽成纯函数 ⇒ 自检能用**合成夹具**覆盖各臂，不需要真实仓库。
    """
    by_no, all_secs = index
    hard: list[str] = []
    s3: list[str] = []
    s4: list[str] = []
    s5: list[str] = []
    for i, line in enumerate(text.split("\n"), 1):
        has_doc = "design-spec" in line
        for m in RX_LINE_REF.finditer(line):
            hard.append(f"G1 {rel}:{i}  行号引用 `design-spec.md:{m.group(1)}`"
                        f"（改成 `§x.y「锚点」`）")
        if has_doc:
            for m in RX_BARE.finditer(line):
                if int(m.group(1)) <= spec_lines:
                    hard.append(f"G2 {rel}:{i}  同行点了文档名又写裸行号 `:{m.group(1)}`"
                                f"（改成 `§x.y「锚点」`）")

        # 🚨 先收「带锚点」那批的区间：它们由 G3 家族**独占**判定。
        # 否则 G4 会去匹配同一个 `§` ⇒ 一处引用被两个判据**各报一次**（自检 Q5 踩到过）。
        # ⚠️ 别用负向前瞻 `(?!「)` 来排 —— `\d+` 会**回溯**：`§9.9「` 匹配不成 `9.9` 就退成 `9`，
        #    前瞻对着 `.` 反而通过 ⇒ 报出「`§9` 不存在」这种**假 G4**（自检 Q10 就是这么红的）。
        # ⇒ 按**位置**排除才精确（`§x.y「锚点」` 的起点就是 `§` 所在位置）。
        anchor_spans = [(m.start(), m.end()) for m in RX_SEC_ANCHOR.finditer(line)]
        for m in RX_SEC_ANCHOR.finditer(line):
            no, anchor = norm(m.group(1)), m.group(2)
            if "{" in anchor or "}" in anchor:
                s3.append(f"G3s {rel}:{i}  `§{no}「{anchor}」` —— 锚点含模板占位符 ⇒ 静态不可验")
                continue
            parts = anchor_parts(anchor)
            if not parts:
                s3.append(f"G3s {rel}:{i}  `§{no}「{anchor}」` —— 锚点太短 / 只有分隔符 ⇒ 不可验")
                continue
            if any(_parts_in(parts, sq) for _, sq in by_no.get(no, [])):
                continue  # ✅ 锚点真在该节
            if no not in by_no:
                s4.append(f"G4 {rel}:{i}  `§{no}「{anchor}」` —— 节号不在任何已登记文档里")
                continue
            # 🚨 判红要求「锚点出现在**所引节号所在的那份文档**的别的节里」——
            # 同文档 ⇒ 作者手里拿的就是这份文档（否则锚点不会落在那份文档里）⇒ 能确定引错了节。
            # 跨文档找不到就**只报**：`§5.1「✅ 定稿」` 指的是某份**未登记**草稿自己的 §5.1，
            # 而 `5.1` 这个号恰好被 `im-mobile-nav-spec` 占着 ⇒ 判红会稳定假红（实测 17 条里 16 条是这类）。
            owners = {d for d, _ in by_no[no]}
            elsewhere = [(d, n2) for d, n2, sq in all_secs
                         if d in owners and n2 != no and _parts_in(parts, sq)]
            if elsewhere:
                where = " · ".join(f"{d} §{n2}" for d, n2 in elsewhere[:3])
                hard.append(f"G3 {rel}:{i}  `§{no}「{anchor}」` —— 锚点**不在 §{no}**，"
                            f"实际出现在 {where}")
            else:
                s3.append(f"G3s {rel}:{i}  `§{no}「{anchor}」` —— 锚点在同号文档里找不到"
                          f"（可能是未登记文档的引用，或引文不够精确）")

        for m in RX_SEC.finditer(line):
            if any(a <= m.start() < b for a, b in anchor_spans):
                continue
            no = norm(m.group(1))
            if no not in by_no:
                s4.append(f"G4 {rel}:{i}  `§{no}` 不在任何已登记文档的节号里")
        # G5：裸行号，N 在规范行数内，且同行没有别的文件名
        for m in RX_BARE.finditer(line):
            if int(m.group(1)) > spec_lines:
                continue
            if has_doc or re.search(r"§\d", line):
                continue
            if re.search(r"[\w./-]+\.(?:py|tsx?|jsx?|json|css|yml|html|md|cjs)\b", line):
                continue
            s5.append(f"G5 {rel}:{i}  `:{m.group(1)}`（同行既无文档名也无别的文件名）"
                      f" —— 若指 design-spec 请改成 `§x.y「锚点」`")
    return hard, s3, s4, s5


# ------------------------------------------------------------------ 自检

SPEC_FIXTURE = "\n".join([
    "## 01 原则",                                  # 1
    "",                                            # 2
    "### 1.1 子节",                                 # 3
    "- 相邻可点元素间距 ≥ 8px，避免误触",              # 4
    "",                                            # 5
    "| 7.1 | 登录页 | 品牌叙事 |",                    # 6
    "",                                            # 7
    "### 1.2 断点",                                 # 8
    "",                                            # 9
    "| `compact` | < 640px | 单列 |",               # 10 ← 裸 `<`，后文有引用块 `>`（Q18 用）
    "",                                            # 11
    "> 引用：见 §1.1「相邻可点元素间距 ≥ 8px」",        # 12 ← 引用了 §1.1（Q19 用）
])

#: (臂名, 说明, 夹具行, 规范行数, 期望 (判红, G3s, G4, G5))
SELFTEST: tuple[tuple[str, str, str, int, tuple[int, int, int, int]], ...] = (
    ("Q1", "带前缀行号 ⇒ G1 判红",
     "见 `design-spec.md:12`", 20, (1, 0, 0, 0)),
    ("Q2", "干净对照：只有 `§x.y「锚点」` ⇒ 全 0",
     "出处：`§1.1「相邻可点元素间距 ≥ 8px」`", 20, (0, 0, 0, 0)),
    ("Q3", "同行点文档名 + 裸行号 ⇒ G2 判红",
     "规范 `design-spec.md` 的 `:4` 写着…", 20, (1, 0, 0, 0)),
    ("Q4", "锚点在已登记文档里**找不到** ⇒ **只报** G3s（可能是别的文档的引用）",
     "出处：`§1.1「这句话规范里没有」`", 20, (0, 1, 0, 0)),
    ("Q5", "节号不在任何已登记文档里 ⇒ **只报** G4（**不判红**）",
     "出处：`§9.9「随便什么」`", 20, (0, 0, 1, 0)),
    ("Q6", "裸行号但同行有**别的文件名** ⇒ 全部不判（G5 也不列）",
     "`billing.py` 的 `:4` 有守卫", 20, (0, 0, 0, 0)),
    ("Q7", "裸行号同行无文档名无别的文件名 ⇒ **只报** G5，不判红",
     "刻度见 `:4`（表格体）", 20, (0, 0, 0, 1)),
    ("Q8", "**超出规范行数**的裸行号（端口 / 别的文件）⇒ 不判不报",
     "后端在 `:8001`、`:3003`", 20, (0, 0, 0, 0)),
    ("Q9", "`§x.y` 无锚点且节号不存在 ⇒ **只报** G4，不判红",
     "见 `§3.32`", 20, (0, 0, 1, 0)),
    ("Q10", "表格行当节的锚点也算成立（`§7.1「品牌叙事」`）",
     "`§7.1「品牌叙事」`", 20, (0, 0, 0, 0)),
    # 🚨 Q12 是 Q9 的**反方向**：区间排除若写宽了（把无锚点的也吞掉）⇒ G4 会**漏报**，
    # 而 Q9 只证明「该报的报了」。两个方向都要有臂，否则「改一个匹配规则」只验了一半。
    ("Q12", "`§x.y` 无锚点且节号**存在** ⇒ 不判不报（G4 不许漏报）",
     "见 `§1.1`", 20, (0, 0, 0, 0)),
    ("Q13", "锚点**在同一文档的别的节**里 ⇒ G3 **判红**（并指出实际所在节）",
     "见 `§1.1「品牌叙事」`", 20, (1, 0, 0, 0)),
    ("Q14", "`…` 省略式锚点 ⇒ 按段切分，各段都在即通过",
     "`§1.1「相邻可点元素间距…避免误触」`", 20, (0, 0, 0, 0)),
    ("Q15", "锚点含 `{}` 模板占位符 ⇒ **只报** G3s（静态不可验）",
     "`§1.1「{x}间距」`", 20, (0, 1, 0, 0)),
    ("Q16", "空白不敏感 ⇒ 通过（表格里 `/` 两侧空格不该算内容）",
     "`§1.1「相邻可点元素间距≥8px」`", 20, (0, 0, 0, 0)),
    ("Q17", "HTML 标签剥掉后再比 ⇒ 通过（`design-spec.html` 那类）",
     "`§1.1「相邻可点元素间距 ≥ <strong>8px</strong>」`", 20, (0, 0, 0, 0)),
    # 🚨 Q18/Q19 钉的是**判据自己的**两个归一化陷阱 —— 它们各自都让门禁**误报**过。
    ("Q18", "节里有裸 `<`（`< 640px`）⇒ **不许**把正文当 HTML 标签吃掉（§1.2「单列」仍在）",
     "`§1.2「单列」`", 20, (0, 0, 0, 0)),
    ("Q19", "锚点只能命中**正文**：§1.2 里那句是**引用 §1.1**，不算「§1.2 有这句话」⇒ 判红",
     "`§1.2「相邻可点元素间距 ≥ 8px」`", 20, (1, 0, 0, 0)),
)


def _fixture_index() -> Index:
    return build_index({"fixture.md": SPEC_FIXTURE.split("\n")})


def run_self_test() -> int:
    print("自检（合成夹具，与产品无关）—— 各臂分开报：")
    index = _fixture_index()
    bad: list[str] = []
    for key, desc, text, spec_n, expect in SELFTEST:
        hard, s3, s4, s5 = scan_text("apps/x/page.tsx", text, spec_n, index)
        got = (len(hard), len(s3), len(s4), len(s5))
        ok = got == expect
        print(f"  [{key}] {desc}")
        print(f"        期望 判红{expect[0]}/G3s{expect[1]}/G4{expect[2]}/G5{expect[3]}   "
              f"实测 判红{got[0]}/G3s{got[1]}/G4{got[2]}/G5{got[3]}  {'✓' if ok else '✗'}")
        for h in hard:
            print(f"          · {h}")
        if not ok:
            bad.append(f"[{key}] 期望 {expect} 实测 {got}")

    # 前提锁：节解析为空 ⇒ 必须 exit 2（否则「解析不到」会被读成「没有引用」= 假绿）
    empty = parse_sections(["", "没有标题"])
    if empty:
        bad.append("[Q11] 空文档竟解析出节 ⇒ 前提锁失效")
    print("  [Q11] 规范解析不到节 ⇒ 前提锁必须拦住（exit 2）")
    print(f"        期望 空字典  实测 {'空字典' if not empty else empty}  "
          f"{'✓' if not empty else '✗'}")

    if bad:
        print(f"\n自检失败 {len(bad)} 臂 ⇒ **工具坏了，不是产品坏了**（exit 2）")
        for b in bad:
            print(f"  - {b}")
        return 2
    print("\n自检通过。")
    return 0


# ------------------------------------------------------------------ 注入反证

#: (臂名, 说明, **注入的那一整行**, 期望判红条数)
#: 🚨 注入**全部在内存**（`scan_text` 是纯函数，不落任何文件）—— 与本项目其它门禁同纪律。
#: 为什么它**不能**被自检替代：自检用的是合成夹具（一份 4 节的小文档），
#: 证明的是「判定函数会红」；注入用的是**真实规范**，证明的是「**在这个仓库上**会红」。
#: 少了后者，「判据写错了但夹具恰好也错了」这类**同向错误**永远看不出来。
INJECT_CASES: tuple[tuple[str, str, str, int], ...] = (
    ("I0", "对照：注入**正确**的 `§8.3「相邻可点元素间距 ≥ 8px」` ⇒ 必须 **0 判红**",
     "出处：`§8.3「相邻可点元素间距 ≥ 8px」`", 0),
    ("I1", "注入行号引用 `design-spec.md:425` ⇒ G1 **必须判红**",
     "出处：`design-spec.md:425`", 1),
    ("I2", "注入「点文档名 + 裸行号」 ⇒ G2 **必须判红**",
     "规范 `design-spec.md` 的 `:425` 写着触控间距", 1),
    # 🚨 I3 是 G3 唯一的**真实仓库**反证臂：锚点在 design-spec §8.3，却写成 §4.2
    # ⇒ 同号文档（两个号都在 design-spec）+ 锚点在别的节 ⇒ 必须判红，且消息要指出实际所在节。
    ("I3", "锚点真在同号文档、但**不在所引的节** ⇒ G3 **必须判红**（并指出实际所在节）",
     "出处：`§4.2「相邻可点元素间距 ≥ 8px」`", 1),
    ("I4", "节号不在任何已登记文档 ⇒ **只报不判红**（跨文档归因不定的那一类，不许误伤）",
     "出处：`§9.9「随便什么」`", 0),
    ("I5", "回归：`§8.2「单列」` 必须 **0 判红** —— 裸 `<`（`< 640px`）不许把正文当标签吃掉",
     "出处：`§8.2「单列」`", 0),
    # I6 是 I3 的**反方向**：锚点根本不在任何一节 ⇒ 不许判红。
    # 两个方向都要有臂，否则「同号文档」这条不等式只验了一半。
    ("I6", "锚点在已登记文档里**找不到**（可能是别的文档的引用）⇒ **只报** G3s，不判红",
     "出处：`§8.6「离线可读（预缓存）」`", 0),
)

#: 注入臂用到的锚点 —— ⚠️ **运行时现读校验**（`_anchor_live`）。硬编码的锚点会在规范改措辞后
#: **静默失效**：那时 I3 从「必须红」变成「找不到 ⇒ 只报」⇒ 注入臂**假的绿**。
#: 与其假装它永远有效，不如**前提没了就 exit 2**（与本项目 A0 类守卫同一条纪律）。
INJECT_ANCHORS: tuple[tuple[str, str], ...] = (
    ("8.3", "相邻可点元素间距 ≥ 8px"),   # I0 / I3 用
    ("8.2", "单列"),                   # I5 用（§8.2 那张表里有裸 `<`，见 Q18 那个坑）
)


def _anchor_live(lines: list[str], no: str, needle: str) -> bool:
    """锚点是否**逐字**还活在 §no 里（注入臂的前提锁）。"""
    a, b = parse_sections(lines)[no]
    return _squash(needle) in _squash(_drop_refs("\n".join(lines[a - 1:b])))


def run_injection() -> int:
    print("注入反证（**内存**注入，不碰任何产品文件）—— 各臂分开报：")
    docs, index = load_specs()
    if not docs:
        print("[env] 一份已登记规范文档都读不到 ⇒ 不判", file=sys.stderr)
        return 2
    spec_lines = len(docs[SPEC_DOCS[0]])
    ds_lines = docs[SPEC_DOCS[0]]
    gone = [f"§{no}「{n}」" for no, n in INJECT_ANCHORS if not _anchor_live(ds_lines, no, n)]
    if gone:
        print(f"[env] 注入臂的前提不在了：{' · '.join(gone)} 在各自节里已逐字找不到",
              file=sys.stderr)
        print("      ⇒ **更新 `INJECT_ANCHORS` / `INJECT_CASES`**，别让它静默变绿。",
              file=sys.stderr)
        return 2

    bad: list[str] = []
    for key, desc, text, want in INJECT_CASES:
        hard, s3, s4, s5 = scan_text("__inject__/x.md", text, spec_lines, index)
        got = len(hard)
        ok = got == want
        print(f"  [{key}] {desc}")
        print(f"        期望 判红{want}  实测 判红{got}  {'✓' if ok else '✗'}"
              f"（G3s {len(s3)} / G4 {len(s4)} / G5 {len(s5)}）")
        for h in hard:
            print(f"          · {h}")
        if not ok:
            bad.append(f"[{key}] 期望 判红{want} 实测 判红{got}")
    if bad:
        print(f"\n注入反证失败 {len(bad)} 臂 ⇒ **判据在这个仓库上没咬合**（exit 2）")
        for b in bad:
            print(f"  - {b}")
        return 2
    print(f"\n注入反证 {len(INJECT_CASES)}/{len(INJECT_CASES)} 通过。"
          f"（⚠️ 只证明「**会红**」，不证明「覆盖全」—— 它仍然拦不住「引对了格式但指错了节」）")
    return 0


# ------------------------------------------------------------------ main

def _group_g4(items: list[str]) -> list[str]:
    """G4 按节号聚合。

    511 行逐条打印**没人看也没法看**。聚合后**出现次数少的**才可能是「打错的节号」：
    `§3.13 × 40` 是后端文档的编号体系（正常），`§8.9 × 1` 才需要人看一眼。
    """
    groups: dict[str, list[str]] = {}
    for x in items:
        m_no = re.search(r"`(§[\d.]+)`", x)
        m_loc = re.search(r"^G4 (\S+:\d+)", x)
        groups.setdefault(m_no.group(1) if m_no else "?", []).append(
            m_loc.group(1) if m_loc else "?")
    out: list[str] = []
    for key, locs in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        out.append(f"  {key} ×{len(locs)}   " + " · ".join(locs[:3]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="引用一致性门禁：禁止行号引用 + 锚点必须真在该节")
    ap.add_argument("--self-test", action="store_true", help="合成夹具自检（证明判定函数对）")
    ap.add_argument("--inject", action="store_true", help="**真实规范**上的注入反证（证明它在这个仓库上会红）")
    ap.add_argument("--report-only", action="store_true", help="只打印，不判红")
    args = ap.parse_args()

    if args.self_test:
        return run_self_test()
    if args.inject:
        return run_injection()

    docs, index = load_specs()
    if not docs:
        print("[env] 一份已登记规范文档都读不到 ⇒ 不判产品", file=sys.stderr)
        return 2
    spec_rel = SPEC_DOCS[0]
    by_no, _ = index
    if spec_rel not in docs or not by_no:
        print(f"[env] {spec_rel} 解析不出任何节号 ⇒ 前提不成立，不判产品", file=sys.stderr)
        return 2
    spec_lines = len(docs[spec_rel])
    print(f"规范 {spec_rel}：{spec_lines} 行 / {len(by_no)} 个节号")
    print(f"已登记文档 {len(docs)} 份：" + " · ".join(docs))

    hard: list[str] = []
    s3: list[str] = []
    s4: list[str] = []
    s5: list[str] = []
    n_files = 0
    for rel, p in iter_live_files():
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        n_files += 1
        h, a, b, c = scan_text(rel, text, spec_lines, index)
        hard += h
        s3 += a
        s4 += b
        s5 += c

    print(f"扫描 {n_files} 个活文件（排除门禁自身 / `evidence/*.txt|*.log` / 每日日志）")
    print(f"合计：判红 **{len(hard)}** · G3s {len(s3)} · G4 {len(s4)} · G5 {len(s5)}\n")

    for title, items in (
        ("G3s 只报：锚点在同号文档里找不到（可能是未登记文档的引用，或引文不够精确）", s3),
        ("G5 只报：疑似 design-spec 的裸行号（**分不出来**，需人工看）", s5),
    ):
        if items:
            print(f"【{title} —— {len(items)} 处】")
            for x in items:
                print(f"  {x}")
            print()

    if s4:
        groups = _group_g4(s4)
        print(f"【G4 只报：`§x.y` 的节号不在任何已登记文档里 —— "
              f"{len(s4)} 处 / {len(groups)} 个不同节号】")
        print("   （多数是**别的文档**的编号体系，属正常；**出现次数少的**才可能是打错的号）")
        for line in groups:
            print(line)
        print()

    if hard and not args.report_only:
        print(f"发现 **{len(hard)} 条**引用一致性违规：")
        for x in hard:
            print(f"  ✗ {x}")
        print("\n提示：`design-spec.md:NNN` 会随规范增删而漂移 ⇒ 一律改成 `§x.y「锚点」`；")
        print("      `§x.y「锚点」` 的锚点必须**真的在该节**（否则改规范时它悄悄失效）。")
        return 1

    print("G1 / G2 / G3 全部通过：无行号引用，且每个能定位的 `§x.y「锚点」` 的锚点都真在该节。")
    print("⚠️ 本判据**只能证伪不能证真**：G3s / G4 / G5 三档是**只报**，需人工看；")
    print("   它拦不住「引对了格式但引的节号本来就不该这么写」。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
