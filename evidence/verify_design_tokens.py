"""设计令牌迁移「全量验证」门禁（任务 #19）。

## 为什么要有这条

`bridge.css`（阶段一的兼容层）已经删掉了，但**「删掉了」不等于「迁移完成了」**：
只要还有一处 `!important`、一个旧色系工具类、一条 `.dark` 覆盖，
就说明有地方没迁到令牌上——而这些**全部躲得过 `tsc` 与 `next build`**。

原先这些检查是**手跑一遍、把数字抄进文档**（`phase3-implementation.md` §6.4）。
手跑的检查不是防线：下次改完没人会重跑，数字会静静腐烂。
⇒ 把它做成**可重跑、且证明过会红**的门禁。

## 判据的出处

全部来自 `deliverables/ui-design/phase3-implementation.md` §6.4 的验收表
（那是**删 `bridge.css` 那一刻**的口径，本节照搬，不自己发明期望值）：

| 检查 | 期望 |
|---|---|
| `!important`（四端产物 CSS） | **0** |
| 旧色系残留（源码 `apps` + `packages`） | **0**（15 个旧色系全族） |
| 旧色系残留（产物 CSS） | **0** |
| `.bg-white`（产物 CSS） | **0** |
| `.dark` 工具类覆盖（产物 CSS） | **0 条**（`.dark{}` 只剩变量块） |
| 被删文件 / 悬空引用 | `bridge.css`、v1 `Header`/`Sidebar` **0 处真实引用** |
| `viewport-fit=cover`（产物 HTML） | **存在**（§6.7：缺了它整节安全区规范是死代码） |
| `safe-left` / `safe-right`（产物 CSS） | **存在** |

> 后两条断言的是「**存在**」而不是某个具体条数。
> §6.4 抄下来的 `web 22 / lawyer 16 / admin 8 / im 6` 是**快照**，会随内容变动；
> 而「`viewport-fit=cover` 在不在」才是**不变量**——§6.7 的 P0 正是它缺席。

## 两种产物，两套结论（退出码语义）

产物级检查需要**生产构建**。若 `.next` 里没有预渲染 HTML（dev 构建就是这样），
那不是「产物有缺陷」，而是**「没东西可测」** ⇒ 记 `2`（环境问题），**不当作通过**。
`--artifacts` 可以指向任意构建目录（含归档产物），用于在没有 prod 构建时验证检查本身。

## 退出码

`0` 通过 · `1` 产品缺陷 · `2` 环境问题（产物缺失 / 无法读取）

用法：
    python evidence/verify_design_tokens.py                    # 源码 + 四端产物
    python evidence/verify_design_tokens.py --source-only
    python evidence/verify_design_tokens.py --artifacts <构建目录> --label im-prod
    python evidence/verify_design_tokens.py --self-test        # 证明每条检查都会红
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent
FRONTEND = HERE.parent / "frontend"

# §6.4 列的 15 个旧色系全族
OLD_PALETTE = (
    "slate", "indigo", "cyan", "teal", "emerald", "amber", "red", "rose",
    "violet", "orange", "yellow", "sky", "lime", "fuchsia", "pink",
)
# Tailwind 工具类前缀（`bg-red-600` / `text-slate-500` …）
UTIL_PREFIX = (
    "bg|text|border|ring|from|to|via|fill|stroke|divide|placeholder|shadow"
    "|outline|accent|caret|decoration"
)
RE_OLD_PALETTE = re.compile(
    r"\b(?:" + UTIL_PREFIX + r")-(?:" + "|".join(OLD_PALETTE) + r")-\d{2,3}\b"
)
RE_IMPORTANT = re.compile(r"!important")
# ⚠️ **必须与 §6.4 的口径逐字一致：`.bg-white{`（带花括号 = 不透明那个）。**
#    第一版写成 `\.bg-white\b`，于是把 `.bg-white\/15`（**半透明**）也算了进去，
#    在归档的真实产物上报出「`.bg-white` 1 处」—— 而 §6.4 记的是 **0**。
#    实测核对：`.bg-white{` 精确匹配确实是 **0**，§6.4 没错，**是我的判据比出处更宽**。
#    半透明的 `bg-white/15` 在源码里有 **2 处真实使用**（`im/me` 头像底、`im/` 骨架微光），
#    是**有意**的叠加色，不是迁移漏网 ⇒ 单独作「观察」报出，**不判缺陷**。
RE_BG_WHITE = re.compile(r"\.bg-white\{")
RE_BG_WHITE_ALPHA = re.compile(r"\.bg-white\\/\d+")
RE_DARK_TOKEN = re.compile(r"\.dark(?![-\w])")
RE_VIEWPORT_FIT = re.compile(r"viewport-fit\s*=\s*cover", re.I)
RE_SAFE_X = re.compile(r"safe-(?:left|right)")

# 源码扫描要跳过的目录（每条都要能说出理由）
SOURCE_SKIP_DIRS = {
    "node_modules": "第三方依赖",
    "_prev_build": "归档的旧产物，不是源码",
    "components-preview": "组件预览页；**原 §6.4 口径即排除**，此处照搬并显式列出（排除本身就是需要被看见的动作）",
}
SOURCE_EXTS = {".ts", ".tsx", ".js", ".jsx", ".css"}

DELETED_FILES = (
    "packages/ui/src/bridge.css",
    "packages/ui/src/components/Header.tsx",
    "packages/ui/src/components/Sidebar.tsx",
)


# ────────────────────────── 纯函数检查器（可被 --self-test 直接喂字符串）──────────────────────────

def find_old_palette(text: str) -> list[str]:
    return sorted({m.group(0) for m in RE_OLD_PALETTE.finditer(text)})


def find_important(text: str) -> list[str]:
    return ["!important"] * len(RE_IMPORTANT.findall(text))


def find_bg_white(text: str) -> list[str]:
    """§6.4 口径：**不透明**的 `.bg-white{`（半透明的 `bg-white/15` 不在其中）。"""
    return sorted({m.group(0) for m in RE_BG_WHITE.finditer(text)})


def find_bg_white_alpha(text: str) -> list[str]:
    """**观察项**（不判缺陷）：半透明白叠加 `.bg-white/15` —— 绕过了令牌，但可能是有意的。"""
    return sorted({m.group(0) for m in RE_BG_WHITE_ALPHA.finditer(text)})


def find_dark_override(text: str) -> list[str]:
    """找 `.dark` 的**工具类覆盖**（`.dark .x{}` / `:is(.dark *)` / `.dark\\:x`）。

    合法的只有 `.dark{--var:…}` 那一个**变量块** ⇒ 后面紧跟 `{` 的算合法，其余全算违规。
    """
    out = []
    for m in RE_DARK_TOKEN.finditer(text):
        rest = text[m.end():m.end() + 40].lstrip()
        if not rest.startswith("{"):
            out.append((".dark" + rest[:24]).strip())
    return out


def has_viewport_fit(text: str) -> bool:
    return bool(RE_VIEWPORT_FIT.search(text))


def has_safe_x(text: str) -> bool:
    return bool(RE_SAFE_X.search(text))


# ────────────────────────── 收集文件 ──────────────────────────

def walk_source(root: pathlib.Path) -> list[pathlib.Path]:
    """列出 `root` 下的源码文件（`SOURCE_EXTS`，跳过 `SOURCE_SKIP_DIRS` 与 `.next*`）。

    🚨 **必须「下降前剪枝」，不能「走完再过滤」。**
    原实现是 `root.rglob("*")` + 事后过滤 ⇒ 它照样**走完四份 `node_modules`**
    （四端各一份，实测 **>100s**，`timeout 100` 下 rc=124 被直接杀掉，
    在 `run_ci_probes.py` 里表现为「探针挂死」而不是「探针报红」）。
    这是 `evidence/README.md` 坑 6 的同一族：**遍历的代价在下降时就已经付掉了**，
    过滤发生在下降之后，所以过滤不省任何时间。

    这里改成 `os.walk` + `dirnames[:] = [...]`，在**下降之前**剪掉
    `SOURCE_SKIP_DIRS` 与 `.next*`。

    ⚠️ **判据本身一个字都没动**：下面每个候选文件走的还是原实现那两条
    `any(part in SOURCE_SKIP_DIRS …)` / `any(part.startswith(".next") …)` 谓词。
    剪枝与谓词是**严格冗余**的 —— 被剪掉的目录名要么 ∈ `SOURCE_SKIP_DIRS`、
    要么以 `.next` 开头，两种情况下 `rel.parts` 都必然命中同一条谓词，
    所以「剪枝」**只能删掉本来就会被丢掉的文件** ⇒ 结果集**可证不变**。
    （保留谓词还有一层好处：万一 `os.walk` 的 `dirnames` 分类与
    `rglob` 不同，谓词仍然兜底。）

    唯一可能变化的是**顺序**（`rglob` 是下降与产出交错的深度优先，
    `os.walk` 是「先本层文件、再下降子目录」）—— 因此
    调用方打印的 `files[:3]` 示例顺序可能不同，**计数与集合不变**。
    """
    out: list[pathlib.Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # 🚨 就地剪枝：必须**原地赋值**给 dirnames，os.walk 才会真的不下降
        dirnames[:] = [
            d for d in dirnames
            if d not in SOURCE_SKIP_DIRS and not d.startswith(".next")
        ]
        for name in filenames:
            p = pathlib.Path(dirpath) / name
            # ⚠️ `is_file()` 不能省：`os.walk` 把**断链符号链接 / 特殊文件**
            #    也归进 filenames（它们不是目录），原实现用 is_file() 把它们挡掉了。
            #    少了这一条，一个断链的 `foo.ts` 会被算进源码 ⇒ 读不到 ⇒ **假 EXIT=2**。
            if not p.is_file() or p.suffix not in SOURCE_EXTS:
                continue
            rel = p.relative_to(root)
            if any(part in SOURCE_SKIP_DIRS for part in rel.parts):
                continue
            # `.next*` 一律跳过（构建产物不是源码）
            if any(part.startswith(".next") for part in rel.parts):
                continue
            out.append(p)
    return out


def collect_artifacts(d: pathlib.Path) -> dict:
    return {
        "css": sorted(d.glob("static/css/**/*.css")),
        "html": sorted(d.glob("server/app/**/*.html")),
    }


def is_prod_build(art: dict) -> bool:
    """有没有**预渲染 HTML** 是「prod vs dev」最可靠的判据。

    dev 构建的 `.next` 里 `server/app/**/*.html` 是 0 个（实测），
    `static/css` 只有 1 个占位文件 ⇒ 产物级检查在那上面跑毫无意义。
    """
    return len(art["html"]) > 0


# ────────────────────────── 自检 ──────────────────────────

CLEAN_CSS = """
:root{--ink-50:247 248 249;--tap:48px}
.dark{--ink-50:11 14 18}
.safe-bottom{padding-bottom:var(--safe-bottom)}
.pb-\\[var\\(--actionbar-bottom\\)\\]{padding-bottom:calc(56px + 0px)}
"""
DIRTY_CSS = """
:root{--ink-50:247 248 249}
.dark .bg-red-500{color:#f00}
.text-slate-700{color:#333!important}
.bg-white{background:#fff}
.dark\\:bg-indigo-600{background:#4f46e5}
"""


def self_test() -> int:
    """证明每条检查**都会红**，且**对照组干净**（两件事分开报）。"""
    print("── 自检：检出能力（Q1）与对照组干净（Q2）分开报 ──\n")
    fails: list[str] = []

    # Q1：每条检查都必须能在「脏样本」上报出来
    #
    # ⚠️ 期望值要**数准**：第一版这里写 `.dark 工具类覆盖` ≥3，
    #    而 DIRTY_CSS 里其实只有 **2** 条（`.dark .bg-red-500{}` 与 `.dark\:bg-indigo-600{}`）
    #    ⇒ 自检直接红。**自检抓到的第一个错是我自己的期望值**，不是检查器。
    #    这与本项目「断言之前先证明期望值的出处」是同一条纪律。
    q1 = [
        ("!important", find_important(DIRTY_CSS), 1),
        ("旧色系（源码/产物通用）", find_old_palette(DIRTY_CSS), 3),
        (".bg-white", find_bg_white(DIRTY_CSS), 1),
        (".dark 工具类覆盖", find_dark_override(DIRTY_CSS), 2),
    ]
    for name, got, want_at_least in q1:
        ok = len(got) >= want_at_least
        print(f"  Q1 {name:<22} 脏样本检出 {len(got)} 处 {'✓' if ok else '**✗ 检不出**'}  {got[:4]}")
        if not ok:
            fails.append(f"Q1 检不出：{name}")

    # `viewport-fit` / `safe-x` 是「存在性」判据，脏样本 = 缺席
    for name, fn, sample in (
        ("viewport-fit=cover", has_viewport_fit, "<html>no viewport here</html>"),
        ("safe-left/right", has_safe_x, ".pb-4{padding-bottom:1rem}"),
    ):
        got = fn(sample)
        ok = got is False
        print(f"  Q1 {name:<22} 缺席样本判为「不存在」 {'✓' if ok else '**✗ 检不出**'}")
        if not ok:
            fails.append(f"Q1 检不出：{name}")

    # Q2：干净样本必须一条都不报（否则就是「一条只会绿的防线」的反面：只会红）
    print()
    q2 = [
        ("!important", find_important(CLEAN_CSS)),
        ("旧色系", find_old_palette(CLEAN_CSS)),
        (".bg-white", find_bg_white(CLEAN_CSS)),
        (".dark 工具类覆盖", find_dark_override(CLEAN_CSS)),
    ]
    for name, got in q2:
        ok = len(got) == 0
        print(f"  Q2 {name:<22} 干净样本命中 {len(got)} 处 {'✓' if ok else '**✗ 误报**'}  {got[:4]}")
        if not ok:
            fails.append(f"Q2 误报：{name}")

    # ⚠️ **这条对照专门用来挡住「判据比出处更宽」**：
    #    半透明的 `bg-white/15` **不在** §6.4 的口径里，绝不能被算作 `.bg-white`。
    #    第一版 `\.bg-white\b` 会把它算进去 ⇒ 在真实产物上误报 1 条。
    got = find_bg_white(".bg-white\\/15{background-color:rgb(255 255 255/.15)}")
    ok = len(got) == 0
    print(f"  Q2 {'bg-white/15 不算违规':<22} 命中 {len(got)} 处 {'✓' if ok else '**✗ 判据过宽**'}  {got}")
    if not ok:
        fails.append("Q2 判据过宽：把半透明的 bg-white/15 当成了 .bg-white{")

    for name, fn, sample in (
        ("viewport-fit=cover", has_viewport_fit, '<meta name="viewport" content="width=device-width, viewport-fit=cover">'),
        ("safe-left/right", has_safe_x, ".pl-\\[calc\\(0\\.75rem_\\+_var\\(--safe-left\\)\\)\\]{padding-left:calc(0.75rem + var(--safe-left))}"),
    ):
        got = fn(sample)
        ok = got is True
        print(f"  Q2 {name:<22} 真实样本判为「存在」 {'✓' if ok else '**✗ 误判为不存在**'}")
        if not ok:
            fails.append(f"Q2 误判：{name}")

    print()
    if fails:
        print("  ✗ 自检失败：")
        for f in fails:
            print(f"      · {f}")
        print("\nSELFTEST_EXIT=1")
        return 1
    print("  ✓ 每条检查都证明过会红（Q1），且干净样本不误报（Q2）")
    print("\nSELFTEST_EXIT=0")
    return 0


# ────────────────────────── 主流程 ──────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-only", action="store_true", help="只跑源码级检查")
    ap.add_argument("--artifacts", help="对指定构建目录跑产物级检查（默认四端 apps/*/.next）")
    ap.add_argument("--label", default="", help="--artifacts 的显示名")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    print("── 设计令牌迁移「全量验证」（判据出处：phase3-implementation.md §6.4）──\n")

    defects: list[str] = []
    env: list[str] = []

    # ── 源码级 ──
    src = walk_source(FRONTEND)
    print(f"[源码级] 扫描 {len(src)} 个文件（apps + packages）")
    print("         已排除：" + "、".join(f"{k}（{v}）" for k, v in SOURCE_SKIP_DIRS.items()))
    hits: dict[str, list[str]] = {}
    for p in src:
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            env.append(f"读不到 {p}: {e}")
            continue
        for tok in find_old_palette(t):
            hits.setdefault(tok, []).append(str(p.relative_to(FRONTEND)))
    if hits:
        for tok, files in sorted(hits.items()):
            print(f"         ✗ 旧色系 {tok}  {len(files)} 处：{files[:3]}")
            defects.append(f"源码残留旧色系工具类 {tok}（{len(files)} 处，如 {files[0]}）")
    else:
        print("         ✓ 旧色系残留 0 命中")

    # 观察项：半透明白叠加。**不判缺陷**，但也不能静默
    alpha_hits: dict[str, list[str]] = {}
    for p in src:
        t = p.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"\bbg-white/\d+", t):
            alpha_hits.setdefault(m.group(0), []).append(str(p.relative_to(FRONTEND)))
    if alpha_hits:
        print("         观察（不判缺陷）：源码里有用到半透明白叠加 ——")
        for tok, files in sorted(alpha_hits.items()):
            print(f"           {tok}  {len(files)} 处：{files}")
        print("           ⇒ §6.4 的口径是**不透明**的 `.bg-white{`，不含这种；"
              "列出来是为了它不被误以为「这里没有 bg-white」")

    for rel in DELETED_FILES:
        p = FRONTEND / rel
        if p.exists():
            print(f"         ✗ 已删文件仍在：{rel}")
            defects.append(f"阶段三已删除的 {rel} 又出现了")
    bad_import = []
    for p in src:
        t = p.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"(?:import|require|@import)[^\n]*bridge\.css", t):
            bad_import.append(str(p.relative_to(FRONTEND)))
    pkg = FRONTEND / "packages/ui/package.json"
    if pkg.exists() and "bridge" in pkg.read_text(encoding="utf-8", errors="ignore"):
        bad_import.append("packages/ui/package.json（exports 仍指向 bridge.css）")
    if bad_import:
        print(f"         ✗ bridge.css 悬空引用：{bad_import}")
        defects.append(f"bridge.css 仍有真实引用：{bad_import}")
    else:
        print("         ✓ 被删文件不存在、bridge.css 0 处真实引用")
    print()

    # ── 产物级 ──
    if args.source_only:
        print("[产物级] --source-only，跳过")
        targets = []
    elif args.artifacts:
        d = pathlib.Path(args.artifacts)
        targets = [(args.label or d.name, d)] if d.is_dir() else []
        if not targets:
            print(f"[产物级] ✗ 目录不存在：{args.artifacts}")
            env.append(f"产物目录不存在：{args.artifacts}")
    else:
        targets = [(a, FRONTEND / f"apps/{a}/.next") for a in ("web", "lawyer", "admin", "im")]

    for label, d in targets:
        art = collect_artifacts(d)
        if not is_prod_build(art):
            print(f"[产物级] {label:<24} ⚠ 不是生产构建（预渲染 HTML 0 个，css {len(art['css'])} 个）")
            print("           ⇒ **没东西可测**，不当作通过。需要先 `next build`。")
            env.append(f"{label} 无生产产物")
            continue

        t_css = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in art["css"])
        t_html = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in art["html"])
        n_imp = len(find_important(t_css))
        n_pal = find_old_palette(t_css)
        n_bg = find_bg_white(t_css)
        n_bg_a = find_bg_white_alpha(t_css)
        n_dark = find_dark_override(t_css)
        vp = has_viewport_fit(t_html)
        sx = has_safe_x(t_css)
        ok = (n_imp == 0 and not n_pal and not n_bg and not n_dark and vp and sx)
        print(f"[产物级] {label:<24} css {len(art['css'])} / html {len(art['html'])}"
              f"  ⇒ {'✓' if ok else '✗'}")
        print(f"         !important={n_imp}  旧色系={len(n_pal)}{n_pal[:3] if n_pal else ''}"
              f"  .bg-white{{={len(n_bg)}  .dark覆盖={len(n_dark)}{n_dark[:2] if n_dark else ''}")
        print(f"         viewport-fit=cover={'在 ✓' if vp else '**缺失 ✗**'}"
              f"  safe-left/right={'在 ✓' if sx else '**缺失 ✗**'}")
        if n_bg_a:
            # 观察项：不判缺陷，但不能静默 —— 否则下次有人会以为「这里没有 bg-white」
            print(f"         观察（不判缺陷）：半透明白叠加 {n_bg_a} ——"
                  f" 绕过令牌，但源码里是有意使用的叠加色")
        if n_imp:
            defects.append(f"{label} 产物 CSS 仍有 {n_imp} 处 !important")
        if n_pal:
            defects.append(f"{label} 产物 CSS 残留旧色系：{n_pal[:5]}")
        if n_bg:
            defects.append(f"{label} 产物 CSS 仍有 {n_bg} 处 .bg-white")
        if n_dark:
            defects.append(f"{label} 产物 CSS 有 {len(n_dark)} 条 .dark 工具类覆盖")
        if not vp:
            defects.append(f"{label} 产物 HTML 缺 viewport-fit=cover（§6.7：缺了整节安全区规范是死代码）")
        if not sx:
            defects.append(f"{label} 产物 CSS 缺 safe-left/right")
    print()

    print("── 汇总 ──")
    if env:
        print(f"  ⚠ 环境问题：{env}")
    if defects:
        print("  ✗ 发现缺陷：")
        for d in defects:
            print(f"      · {d}")
        print(f"\nEXIT=1  产品缺陷 {len(defects)} 条")
        return 1
    if env:
        print("\nEXIT=2  有产物未能测量（需要先 next build），不当作通过")
        return 2
    print("  ✓ 旧色系 0 残留；无 !important；无 .dark 覆盖；安全区链路在产物里")
    print("\nEXIT=0  通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
