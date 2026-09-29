"""P0-7 / P0-11 前端令牌安全 —— 静态不变量校验

不跑打包器，直接对源码做机器可判定的断言。原因：本轮反复验证过一条规律——
「代码评审能通过、静态类型能通过」的改动，仍可能在真实请求路径上失效。
因此把「必须成立的安全不变量」写成断言，每次改动后秒级回归。

覆盖：
  A. localStorage / sessionStorage 不得持久化任何令牌
  B. SDK 中 access 令牌只存内存变量
  C. 所有页面守卫必须用 restoreSession()（页面刷新后内存令牌为空）
  D. 登录必须走 login() 辅助函数（走 Cookie 签发路径）
  E. 登出必须走 logout()（并禁止 401 自动刷新重试）
  F. 请求必须 credentials: "include"
  G. CSRF 头必须回填
  H. 禁止在应用层直接使用 tokenStore

用法：python verify_p0_7_frontend.py
退出码：0 全通过 / 1 有失败
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "frontend"
SDK = ROOT / "packages" / "sdk" / "src" / "index.ts"
UI_PKG = ROOT / "packages" / "ui" / "src"
APPS = ROOT / "apps"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail[:200]))
    mark = "PASS" if ok else "FAIL"
    suffix = f"  -> {detail[:160]}" if detail else ""
    print(f"[{mark}] {name}{suffix}")


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def iter_sources(root: Path, exts=(".ts", ".tsx")):
    if not root.exists():
        return []
    out = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix in exts and "node_modules" not in p.parts:
            out.append(p)
    return sorted(out)


def strip_comments(src: str) -> str:
    """去掉 // 行注释与 /* */ 块注释，避免注释里的示例代码触发误报。"""
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    src = re.sub(r"//[^\n]*", "", src)
    return src


def main() -> int:
    sdk_src = read(SDK)
    check("前置：SDK 源文件可读", bool(sdk_src), str(SDK))
    if not sdk_src:
        return 1

    # 说明性注释里会引用 `localStorage` / `tokenStore.get()` 作为「反面示例」，
    # 断言必须只看真实代码，否则会被自己的文档打败。
    sdk_code = strip_comments(sdk_src)

    app_sources = iter_sources(APPS) + iter_sources(UI_PKG)

    # ─────────── A. 存储层不得持久化令牌 ───────────
    # 只允许主题偏好用 localStorage；出现 token/refresh 关键字即失败
    storage_offenders = []
    for p in app_sources:
        src = strip_comments(read(p))
        for m in re.finditer(r"(localStorage|sessionStorage)\.(setItem|getItem)\s*\(\s*[\"'`]([^\"'`]*)[\"'`]", src):
            key = m.group(3)
            if re.search(r"token|refresh|access|jwt|auth", key, re.I):
                storage_offenders.append(f"{p.relative_to(ROOT)}:{key}")
    check(
        "A.1 无任何令牌写入 localStorage / sessionStorage",
        not storage_offenders,
        f"违规={storage_offenders}" if storage_offenders else "0 处",
    )

    # 反面断言：SDK 里也不能出现 localStorage（主题除外，SDK 不应有）
    check(
        "A.2 SDK 代码中不出现 localStorage（注释除外）",
        "localStorage" not in sdk_code,
        "SDK 应只用内存变量",
    )

    # ─────────── B. access 令牌只存内存变量 ───────────
    check(
        "B.1 SDK 用模块级内存变量保存 access 令牌",
        bool(re.search(r"let\s+_accessToken\s*:\s*string\s*\|\s*null", sdk_src)),
        "期望 `let _accessToken: string | null`",
    )
    check(
        "B.2 刷新令牌不再由前端持有",
        "refreshToken" not in sdk_src.replace("refresh_token", "")
        or "_refreshToken" not in sdk_src,
        "前端不应有 _refreshToken 变量",
    )

    # ─────────── C. 页面守卫必须用 restoreSession ───────────
    # 把所有客户端页面里出现「跳登录」的地方找出来，检查附近是否有 restoreSession
    guard_offenders = []
    guard_files = 0
    for p in app_sources:
        src = read(p)
        if 'router.push("/login")' not in src and "router.replace('/login')" not in src:
            continue
        if "use client" not in src and "useEffect" not in src:
            continue
        # 只看「页面级守卫」：同文件里必须有 useEffect + restoreSession
        if "useEffect" in src:
            guard_files += 1
            if "restoreSession" not in src:
                guard_offenders.append(str(p.relative_to(ROOT)))
    check(
        f"C.1 所有含 useEffect 的页面均使用 restoreSession（共扫 {guard_files} 个页面）",
        not guard_offenders,
        f"缺失={guard_offenders}",
    )

    # 反面断言：不得再有 `if (!tokenStore.get())` 式旧守卫
    # 只扫应用/UI 层——SDK 自身的 tokenStore 实现里出现该表达式是正常的。
    legacy_guard = []
    for p in app_sources:
        src = strip_comments(read(p))
        if re.search(r"if\s*\(\s*!\s*tokenStore\.get\(\)", src):
            legacy_guard.append(str(p.relative_to(ROOT)))
    check(
        "C.2 应用层不存在旧式 `if (!tokenStore.get())` 同步守卫",
        not legacy_guard,
        f"残留={legacy_guard}",
    )

    # C.3 批量改写后遗留的「孤儿 else」会直接导致语法错误。
    # 这是本轮真实踩过的坑：脚本替换了 if/else 前缀却留下 `else xxx();`，
    # 静态 grep 看不出来，只有 tsc 会报 TS1128。此处固化为断言。
    orphan_else = []
    for p in app_sources:
        src = strip_comments(read(p))
        if re.search(r"\n\s*else\s+\w+\(\);\s*\n\s*\}\s*,\s*\[", src) or re.search(
            r"\);\n\s*\}\s*\);\n\s*else\s+\w+\(\);", src
        ):
            orphan_else.append(str(p.relative_to(ROOT)))
    check(
        "C.3 无「孤儿 else」残留（批量改写事故模式）",
        not orphan_else,
        f"残留={orphan_else}",
    )

    # C.4 守卫里 else 调用的函数必须真实存在（防止 `else load()` 但只有 refresh()）
    missing_loader = []
    fn_def_pat = re.compile(
        r"(?:async\s+)?function\s+(\w+)|(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?\("
    )
    for p in app_sources:
        src = strip_comments(read(p))
        m = re.search(
            r"restoreSession\(\)\.then\(\(ok\)\s*=>\s*\{[\s\S]{0,200}?else\s+(\w+)\(\)", src
        )
        if not m:
            continue
        called = m.group(1)
        defined = {a or b for a, b in fn_def_pat.findall(src)}
        if called not in defined:
            missing_loader.append(f"{p.relative_to(ROOT)}: 调用了未定义的 {called}()")
    check(
        "C.4 restoreSession 守卫内调用的加载函数均有定义",
        not missing_loader,
        f"问题={missing_loader}",
    )

    # ─────────── D. 登录必须走 login() ───────────
    check(
        "D.1 SDK 导出 login() 且在登录成功时写入内存令牌",
        bool(re.search(r"export\s+async\s+function\s+login\b", sdk_src))
        and bool(re.search(r"if\s*\(data\?\.access_token\)\s*_accessToken\s*=", sdk_src)),
        "login() 必须设置 _accessToken",
    )

    login_page_offenders = []
    for p in app_sources:
        if p.name != "page.tsx":
            continue
        src = read(p)
        # 登录页特征：文件路径含 login
        if "login" not in str(p).lower().replace("\\", "/"):
            continue
        if "auth/login" in src and "tokenStore.set" in src:
            login_page_offenders.append(str(p.relative_to(ROOT)))
    check(
        "D.2 无登录页仍在做 `auth/login` + `tokenStore.set` 直连",
        not login_page_offenders,
        f"残留={login_page_offenders}",
    )

    # LoginShell（共享组件）也必须走 login()
    shells = [p for p in app_sources if p.name == "LoginShell.tsx"]
    shell_bad = [str(p.relative_to(ROOT)) for p in shells if "tokenStore.set" in read(p)]
    check(
        f"D.3 共享登录组件 LoginShell 已改走 login()（扫描 {len(shells)} 个）",
        bool(shells) and not shell_bad,
        f"残留={shell_bad}" if shell_bad else f"文件数={len(shells)}",
    )

    # ─────────── E. 登出必须走 logout() 且禁用自动重试 ───────────
    check(
        "E.1 SDK 导出 logout()",
        bool(re.search(r"export\s+async\s+function\s+logout\b", sdk_src)),
        "",
    )
    logout_body = ""
    m = re.search(r"export\s+async\s+function\s+logout[\s\S]{0,700}?\n\}", sdk_src)
    if m:
        logout_body = m.group(0)
    check(
        "E.2 logout 请求带 _noRetry（否则 401 会静默刷新、登出失效）",
        "_noRetry: true" in logout_body,
        "缺少 _noRetry 会让登出被自动刷新覆盖",
    )
    check(
        "E.3 logout 在 finally 中清空内存令牌（服务端失败也必须本地清）",
        re.search(r"finally\s*\{[\s\S]{0,120}_accessToken\s*=\s*null", logout_body) is not None,
        "",
    )
    check(
        "E.4 request() 尊重 _noRetry 标记",
        bool(re.search(r"!\s*opts\._noRetry", sdk_src)),
        "401 自动刷新分支必须排除 _noRetry",
    )

    clear_offenders = []
    for p in app_sources:
        src = read(p)
        if re.search(r"\btokenStore\.clear\(\)", src):
            clear_offenders.append(str(p.relative_to(ROOT)))
    check(
        "E.5 应用层不再直接调用 tokenStore.clear()",
        not clear_offenders,
        f"残留={clear_offenders}",
    )

    # ─────────── F. credentials: "include" ───────────
    check(
        "F.1 request() 带 credentials: \"include\"（否则跨域丢 Set-Cookie）",
        bool(re.search(r'credentials:\s*"include"', sdk_src)),
        "",
    )
    include_count = len(re.findall(r'credentials:\s*"include"', sdk_src))
    check(
        "F.2 三条网络路径（refresh / request / streamSSE）均带 include",
        include_count >= 3,
        f"出现 {include_count} 次（期望 >= 3）",
    )

    # ─────────── G. CSRF 头回填 ───────────
    check(
        "G.1 SDK 定义 CSRF Cookie/Header 常量",
        "nlaw_csrf" in sdk_src and "X-CSRF-Token" in sdk_src,
        "",
    )
    check(
        "G.2 refresh 请求显式回填 CSRF 头",
        bool(re.search(r"\[CSRF_HEADER_NAME\]:\s*readCookie\(CSRF_COOKIE_NAME\)", sdk_src)),
        "",
    )
    check(
        "G.3 写方法且无 Bearer 时自动回填 CSRF 头",
        bool(re.search(r"if\s*\(method\s*!==\s*\"GET\"\s*&&\s*!token\)", sdk_src)),
        "",
    )

    # ─────────── H. 应用层禁止直接用 tokenStore ───────────
    ts_offenders = []
    for p in app_sources:
        src = read(p)
        if re.search(r"\btokenStore\b", src):
            ts_offenders.append(str(p.relative_to(ROOT)))
    check(
        "H.1 应用/UI 层不直接引用 tokenStore（统一走 login/logout/authed/restoreSession）",
        not ts_offenders,
        f"残留={ts_offenders}",
    )

    # ─────────── I. streamSSE 也走 Cookie ───────────
    sse = ""
    m = re.search(r"export\s+async\s+function\*\s*streamSSE[\s\S]{0,1200}", sdk_src)
    if m:
        sse = m.group(0)
    check(
        "I.1 streamSSE 默认从内存取令牌（无需调用方传入）",
        bool(re.search(r"opts\.token\s*!==\s*undefined\s*\?\s*opts\.token\s*:\s*_accessToken", sse)),
        "",
    )
    check(
        "I.2 streamSSE 带 credentials: \"include\"",
        'credentials: "include"' in sse,
        "",
    )

    # ═══════════════ 汇总 ═══════════════
    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\n{'=' * 66}")
    print(f"P0-7 / P0-11 前端令牌安全静态校验：{passed} 通过 / {failed} 失败")
    print("=" * 66)
    if failed:
        for name, ok, detail in results:
            if not ok:
                print(f"  [FAIL] {name}  -> {detail}")
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
