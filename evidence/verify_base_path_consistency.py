#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""`basePath` 一致性门禁 —— 前端 `next.config.js` 与 `approutes.APP_BASE_PATH` 必须逐端一致。

## 为什么要有这一条（2026-10-10 事故）

`apps/{lawyer,admin,im}/next.config.js` 自 **2026-10-01 14:42** 起配了 `basePath`
（为 `https://bot.tomneil.asia/<app>` 的 nginx 前缀转发），应用整体挂在**前缀**下；
而 `run_browser_job.py` 的就绪探针与 15 条门禁探针**当时全都按无前缀写**
⇒ `browser-all` / `browser-admin` 自那时起**必然 exit 2**，且**静默了 9 天**
（本仓**非 git 仓库** ⇒ `ci.yml` 永不触发；这两个 job 又只在 main / 手动跑）。

⇒ 根因**不是**「谁写错了」，而是**「跨端配置」与「证据层」之间没有任何判据**：
   配置改了、探针没跟，**没有一条门禁会红**。
   这与 README 坑 74 同族（「有个 job 会跑它」≠「每个 PR 都会跑它」）。

## 两条判据

| 编号 | 判什么 | 口径 |
|---|---|---|
| **C1** | 四端 `next.config.js` 的 `basePath` **必须等于** `approutes.APP_BASE_PATH` | 逐端比对，不一致 ⇒ 判红 |
| **C2** | `evidence/verify_*.py` **不得**直接拼「端口 + 路径」的前端 URL —— 必须走 `approutes` | 源码正则，命中 ⇒ 判红 |

## ⚠️ 本判据**只能证伪，不能证真**

- **C1** 只证明「两张表一致」。它**证明不了**前缀本身是**对的**
  （两处一起写成 `/wrong` 它照样绿）—— 那要靠 `browser-all` 真跑。
- **C2 拦的是写法，不是行为**：它挡的是「**绕过唯一真源**」的写法。
  绕过之后**还能**用别的写法拼出错误的 URL（例如把前缀写进局部变量），
  本判据**看不见**。与 `verify_component_wiring.count_equivalent` 同族。
- 🚨 **别在本文件（或任何 `verify_*.py`）的注释/文档里写出 C2 正则能命中的字面量**
  —— 本文件**也是** `verify_*.py`，C2 会扫到它自己 ⇒ **假红**。
  示例一律写成 `<端口>/…`（这正是 README 里那条「别在注释里写带尖括号的示例」的同族纪律）。

## 跑法

    python evidence/verify_base_path_consistency.py            # 真跑
    python evidence/verify_base_path_consistency.py --source-only   # 同义（免浏览器半边）
    python evidence/verify_base_path_consistency.py --self-test     # 只跑自检臂（纯函数）
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
APPS = ROOT / "frontend" / "apps"

sys.path.insert(0, str(HERE))

from approutes import APP_BASE_PATH  # noqa: E402

#: 从 `next.config.js` 里抠出 `basePath: "…"`。**没有这个键 ⇒ 无前缀**（Next 默认）。
BASE_PATH_RE = re.compile(r"""basePath\s*:\s*["']([^"']*)["']""")

#: 🚨 **C2 的正则**。它匹配「**手写前端 URL**」的**两种**写法：
#:
#:   ① **字面量端口**：`localhost:` + 四端端口之一（`3000`–`3003`）；
#:   ② **插值端口**：`localhost:` 后**紧跟**一个插值花括号（`f` 字符串里拼端口那种）。
#:
#: 🚨 **为什么必须覆盖 ②**（2026-10-10 实测发现的**判据自缺陷**）：第一版只写了 ①，
#:   于是 7 个 `verify_*.py` 里「`localhost:` + 插值花括号」那种写法**全部逃过** ——
#:   它们照样绕过 `approutes`、照样会在 `basePath` 变更时静默失灵，而判据**报绿**。
#:   「拦的是写法」这条口径要求它**按写法拦全**，不能只拦恰好写成字面量的那种。
#:
#: ⚠️ 本行**自己不会**被匹配：`300` 后面紧跟的是 `[`（字符类），`:` 后面是 `(`（分组）
#:   —— 都不是 `0`–`3` 或 `{`。所以本文件可以安全地**定义**它。
#: 🚨 但**不要**在别处（尤其注释/文档里）写出能被它命中的字面量：本文件**也是**
#:   `verify_*.py`，会被自己扫到 ⇒ **假红**。示例一律写 `<端口>`。
BYPASS_RE = re.compile(r"localhost:(?:300[0-3]|\{)")


# ---------------------------------------------------------------------------
# C1：`next.config.js` ↔ `approutes.APP_BASE_PATH`
# ---------------------------------------------------------------------------
def read_base_path(app: str) -> str | None:
    """读某端 `next.config.js` 的 `basePath`。

    返回 `""` = 该文件**没有** `basePath` 键（Next 默认无前缀）；
    返回 `None` = **文件不存在**（当环境问题，不当缺陷）。
    """
    f = APPS / app / "next.config.js"
    if not f.exists():
        return None
    m = BASE_PATH_RE.search(f.read_text(encoding="utf-8"))
    return m.group(1) if m else ""


def check_c1() -> list[str]:
    """四端逐端比对。返回**缺陷列表**（空 = 绿）。"""
    out: list[str] = []
    for app in sorted(APP_BASE_PATH):
        got = read_base_path(app)
        if got is None:
            out.append(f"[C1] `frontend/apps/{app}/next.config.js` **不存在**（环境问题，不是缺陷）")
            continue
        want = APP_BASE_PATH[app]
        if got != want:
            out.append(
                f"[C1] `apps/{app}`：`next.config.js` 的 basePath={got!r} "
                f"≠ `approutes.APP_BASE_PATH[{app!r}]`={want!r}"
                " —— 两者必须**同时**改（改哪边都行，但不能只改一边）"
            )
    return out


# ---------------------------------------------------------------------------
# C2：门禁探针不得绕过 `approutes` 手拼前端 URL
# ---------------------------------------------------------------------------
def scan_bypass(path: pathlib.Path) -> list[str]:
    """扫**单个文件**里的「手拼前端 URL」写法。返回 `["<文件>:<行> …"]`。"""
    out: list[str] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if BYPASS_RE.search(line):
            out.append(
                f"[C2] `{path.name}:{i}` 直接手拼了「端口 + 路径」的前端 URL"
                " ⇒ 必须走 `approutes`（`app_base` / `app_url` / `base_for_port`）"
            )
    return out


def check_c2() -> list[str]:
    """扫 `evidence/verify_*.py`（含本文件）。返回**缺陷列表**（空 = 绿）。"""
    out: list[str] = []
    for f in sorted(HERE.glob("verify_*.py")):
        out.extend(scan_bypass(f))
    return out


# ---------------------------------------------------------------------------
# 自检：合成夹具 + **注入反证**
# ---------------------------------------------------------------------------
def self_test() -> int:
    """纯函数自检（不启浏览器、不连服务）。返回 0/1。

    🚨 **每条判据都配一条注入反证**：把「修复」摘掉 ⇒ 该臂**必须红**。
       否则这条判据就是**零判据**（README 里那条纪律）。
    """
    print("── 自检：`verify_base_path_consistency.py`（纯函数）──")
    arms: list[tuple[str, bool, str]] = []

    # ── S1：`BASE_PATH_RE` 能抠出值；**没有该键** ⇒ 空串 ──
    #   ⚠️ 夹具里的字面量刻意写成 `basePath` 的**普通形态**，不含 C2 关心的东西。
    probe_js = 'const c = { basePath: "/demo-app", reactStrictMode: true };'
    m = BASE_PATH_RE.search(probe_js)
    arms.append((
        "S1 `BASE_PATH_RE` 抠出 basePath",
        bool(m) and m.group(1) == "/demo-app",
        f"got={m.group(1) if m else None!r}",
    ))
    arms.append((
        "S1b 没有 `basePath` 键 ⇒ `read_base_path` 回空串",
        read_base_path("web") == "",
        f"web basePath={read_base_path('web')!r}",
    ))

    # ── S2：**注入反证** —— 把 `APP_BASE_PATH` 改坏一个 ⇒ C1 必须报出那一条 ──
    real_admin = APP_BASE_PATH["admin"]
    try:
        APP_BASE_PATH["admin"] = "/__injected_wrong__"
        c1_bad = check_c1()
        hit = any("apps/admin" in d for d in c1_bad)
    finally:
        APP_BASE_PATH["admin"] = real_admin
    arms.append(("S2 注入反证：改坏 `admin` 的前缀 ⇒ C1 必红", hit,
                 f"缺陷 {len(c1_bad)} 条"))
    # ⚠️ 只跑一次：下面 S2b / S4 **共用**这次结果 —— `check_c1()` 要读四份
    #    `next.config.js`，`check_c2()` 要扫全仓 `verify_*.py`；重复调用会把
    #    本探针的自检推过 `SELFTEST_ONLY` 的「≤2s」门槛。
    c1_final = check_c1()
    arms.append(("S2b 还原后 C1 恢复全绿", c1_final == [], f"缺陷 {len(c1_final)} 条"))

    # ── S3：**注入反证** —— 合成一个「手拼前端 URL」的文件 ⇒ C2 必须命中 ──
    #   🚨 这里**不能**直接写字面量（本文件也是 `verify_*.py`，会被 C2 扫到自己 ⇒ 假红）
    #      ⇒ 用字符串拼接把字面量**在运行时**造出来。
    #   ⚠️ 两种写法都要注入：第一版只注入了字面量那种，于是**插值那种漏网了整整一轮**
    #      （判据自缺陷：口径说「拦写法」，实现只拦了其中一种写法）。
    bad_literal = "x = " + '"' + "http://localhost:" + "300" + "1" + "/cases" + '"'
    bad_fstring = "y = f" + '"' + "http://localhost:" + "{port}" + "/cases" + '"'
    # 🚨 夹具**必须**写在 `evidence/` **之外**（用 `tempfile`）：
    #    `run_ci_probes` 的**判据 0b** 会扫 `evidence/*.py`，临时文件落在那里会被判成
    #    「**未分类的 .py**」⇒ **并发跑**自检时会把别人的门禁搅红。
    #    2026-10-10 实测踩到：本文件的自检与 `run_ci_probes --self-test` 同时跑 ⇒
    #    后者的 **S3 / H0 两臂假红**（期望 exit=0、实测 1），排查花了很久。
    with tempfile.TemporaryDirectory(prefix="basepath-fixture-") as td:
        tmp = pathlib.Path(td) / "fixture.py"
        tmp.write_text(bad_literal + "\n" + bad_fstring + "\n", encoding="utf-8")
        hits = scan_bypass(tmp)
    # 断言**两行各自**被命中（只数总数的话，「两行都命中了同一行」也会骗过这一臂）。
    got_lines = sorted(int(h.split(":")[1].split("`")[0]) for h in hits)
    arms.append((
        "S3 注入反证：字面量 / f-string 插值 **两种**写法 ⇒ C2 各命中一次",
        got_lines == [1, 2], f"命中行={got_lines}",
    ))

    # ── S4：当前仓库状态（C1 复用上面的 `c1_final`，避免重复全仓扫描）──
    c2 = check_c2()
    arms.append(("S4 当前仓库 C1 全绿（四端一致）", c1_final == [], f"缺陷 {len(c1_final)} 条"))
    arms.append(("S4b 当前仓库 C2 全绿（无绕过写法）", c2 == [], f"缺陷 {len(c2)} 条"))

    for name, ok, detail in arms:
        print(f"  [{'✓' if ok else '✗'}] {name}    （{detail}）")
    bad = [n for n, ok, _ in arms if not ok]
    if bad:
        print(f"\n❌ 自检失败 {len(bad)} 条：{', '.join(bad)}")
        return 1
    print(f"\n✅ 自检通过（{len(arms)} 臂）")
    return 0


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="`basePath` 一致性门禁")
    ap.add_argument("--source-only", action="store_true",
                    help="免浏览器半边（本探针本就纯静态，此开关是同义别名）")
    ap.add_argument("--self-test", action="store_true", help="只跑自检臂（纯函数）")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    defects = check_c1() + check_c2()

    print("── 源码层：`basePath` 一致性 ──")
    for app in sorted(APP_BASE_PATH):
        got = read_base_path(app)
        shown = "（无 basePath 键）" if got == "" else f"{got!r}"
        print(f"  {app:<7} next.config.js → {shown:<20} approutes → {APP_BASE_PATH[app]!r}")
    # ⚠️ **无条件**打印条数：只印在「有缺陷」分支里的话，清零后棘轮取不到数 ⇒ 判红
    #    （`verify_font_scale.py` 踩过，坑 50 同形）。
    print(f"── 源码层：缺陷 {len(defects)} 条 ──")
    for d in defects:
        print(f"  ✗ {d}")

    if defects:
        print("\nEXIT=1  产品缺陷")
        return 1
    print("\nEXIT=0  通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
