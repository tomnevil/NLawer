#!/usr/bin/env python
"""门禁：**令牌不得落到任何可被 JS 读到的持久化位置**（P0-7 前端面）。

## 为什么需要这条门禁

**前端目前没有任何测试运行器** —— 无 vitest / jest、根 `package.json` 没有
`test` 脚本、全仓 0 个 `*.test.*`。唯一的静态门禁是 `tsc --noEmit` 与
`next build`，而这两者**证明不了任何运行期行为**（本项目已反复验证：
`NEXT_PUBLIC_*` 缺失、`viewport-fit=cover` 缺失、`CameraCapture` 0 接入，
三者都是 `tsc` + `build` 全绿）。

P0-7 的修复是一个**三方契约**：

| 方 | 职责 |
|---|---|
| 后端 | refresh 下发为 `HttpOnly` Cookie（`app/core/auth_cookies.py`） |
| SDK | access 只存**模块内存变量**，refresh 不落任何 JS 可读处 |
| 四端 | **不得**绕过 SDK 自行把令牌写进 `localStorage` |

后端那一半本轮已补 6 条 pytest 判据（`tests/test_auth_cookie_transport.py`）。
**本文件补的是前端这一半**——它此前**一条判据都没有**。

## 判据

| 编号 | 断言 | 为什么 |
|------|------|--------|
| F1 | SDK 里**不得**出现 `localStorage/sessionStorage.setItem` 且键名含 token 语义 | access 一旦持久化，XSS 可长期接管 |
| F2 | SDK 里 refresh 不得被赋给任何 `Storage` | 长效凭据绝不能进 JS 可读处 |
| F3 | SDK 必须有内存变量承载 access（且有 `clear()`） | 证明「内存方案」真的落地，不是空口 |
| F4 | SDK 的 `fetch` 必须带 `credentials: "include"` | 缺了它跨域 Cookie 被丢弃 ⇒ refresh **静默失效**（表现为「一会儿就掉线」） |
| F5 | 401 必须触发自动刷新后重试（P0-7 原文「无自动刷新」） | 且 `logout`/`login` 必须 `_noRetry` |
| F6 | 四端 `apps/*` 不得自行持久化令牌 | SDK 做对、**某个页面绕过**同样前功尽弃 |

## 退出码

- `0` 通过
- `1` 发现缺陷
- `2` 环境问题（仓库结构不对 / 自检失败）

用法：

    python evidence/verify_token_storage.py
    python evidence/verify_token_storage.py --self-test
    python evidence/verify_token_storage.py --verbose
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent
FRONTEND = HERE.parent / "frontend"
SDK = FRONTEND / "packages" / "sdk" / "src" / "index.ts"
APPS = ("web", "lawyer", "admin", "im")

#: 键名里出现这些词即视为「令牌语义」
TOKEN_WORDS = ("token", "jwt", "access", "refresh", "auth", "session")
#: 允许的持久化键（**不是凭据**）
ALLOWED_PERSISTED = ("tenant", "theme", "draft", "scroll", "sync", "locale", "sidebar")

_STORAGE_WRITE = re.compile(
    r"(localStorage|sessionStorage)\s*\.\s*setItem\s*\(", re.IGNORECASE
)
_STORAGE_ANY = re.compile(r"(localStorage|sessionStorage)", re.IGNORECASE)


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _strip_comments(src: str) -> str:
    """去掉 `//` 与 `/* */` 注释——否则文档里写的反例会被当成实现。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    src = re.sub(r"//[^\n]*", "", src)
    return src


def _key_written(call_src: str) -> str:
    """粗略取出 setItem 的第一参数文本，用于判断是否令牌语义。"""
    m = re.search(r"setItem\s*\(\s*([^,)]+)", call_src)
    return m.group(1) if m else ""


# --------------------------------------------------------------------- 判据
def f1_f2_sdk_storage(src: str) -> list[str]:
    """F1/F2：SDK 不得把令牌写进任何 Web Storage。"""
    code = _strip_comments(src)
    defects = []
    for m in _STORAGE_WRITE.finditer(code):
        seg = code[max(0, m.start() - 200) : m.end() + 200]
        key = _key_written(seg).lower()
        if any(w in key for w in TOKEN_WORDS):
            defects.append(
                f"F1：SDK 把令牌语义的键写入了 Web Storage —— `{key.strip()}`"
            )
        elif not any(w in key for w in ALLOWED_PERSISTED) and key.strip():
            defects.append(
                f"F1：SDK 写入了未登记的持久化键 `{key.strip()}` —— 请确认它不是凭据"
            )
    # F2：refresh 与 Storage 出现在同一行
    for i, line in enumerate(code.splitlines(), 1):
        if "refresh" in line.lower() and _STORAGE_ANY.search(line):
            defects.append(f"F2：第 {i} 行 refresh 与 Storage 同时出现 —— {line.strip()[:90]}")
    return defects


def f3_memory_token_store(src: str) -> list[str]:
    """F3：access 必须由模块级内存变量承载，且可清空。"""
    code = _strip_comments(src)
    if not re.search(r"let\s+_accessToken\s*(:\s*string\s*\|\s*null)?\s*=", code):
        return ["F3：SDK 里找不到模块级内存变量 `_accessToken`（内存方案未落地）"]
    if "clear:" not in code and "clear()" not in code:
        return ["F3：`tokenStore` 必须提供 `clear()`，否则登出无法真正清除凭据"]
    return []


def f4_credentials_include(src: str) -> list[str]:
    """F4：**每一处** `fetch` 都必须带 `credentials: "include"`。

    ⚠️ 不能写成「全文至少有一处 include」——实测 SDK 里有 **3 处** `fetch`，
    那种写法在其中任意一处被删掉时**仍然全绿**（自检就是这么暴露的）。
    少带一处的那次请求就收不到 Set-Cookie，表现为「登录成功但一会儿就掉线」。
    """
    code = _strip_comments(src)
    defects = []
    calls = list(re.finditer(r"\bfetch\s*\(", code))
    if not calls:
        return ["F4：SDK 里找不到 `fetch(` 调用（判据前提不成立）"]
    for i, m in enumerate(calls, 1):
        # 🚨 窗口必须在**下一处 `fetch(`** 处截断，不能固定 500 字符。
        # 实测踩到（2026-09-20）：自检把第一处 `credentials: "include"` 换成
        # `"omit"` 后 F4 **仍然绿** —— 因为 500 字符的窗口一路跨到了第二处
        # `fetch`，把它的 `include` 借来用了。⇒ 前一处缺凭据会被后一处掩盖，
        # 「每一处都要带」这条判据在调用密集时**静默失效**。
        end = calls[i].start() if i < len(calls) else len(code)
        window = code[m.start() : min(m.start() + 500, end)]
        if not re.search(r'credentials\s*:\s*"include"', window):
            line = code[: m.start()].count("\n") + 1
            defects.append(
                f"F4：第 {line} 行第 {i} 处 `fetch` 缺少 `credentials: \"include\"` —— "
                "跨域场景下浏览器会丢弃 Set-Cookie，refresh 静默失效"
            )
    return defects


def f5_auto_refresh_and_noretry(src: str) -> list[str]:
    """F5：401 自动刷新；且 logout/login 必须 `_noRetry`。"""
    code = _strip_comments(src)
    defects = []
    if not re.search(r"status\s*===\s*401", code):
        return ["F5：SDK 里没有 401 处理分支（P0-7 原文：无自动刷新）"]
    if "refreshAccessToken" not in code:
        defects.append("F5：401 分支没有调用 `refreshAccessToken()`")
    for fn in ("logout", "login"):
        m = re.search(rf"async function {fn}\b", code)
        if not m:
            continue
        body = code[m.start() : m.start() + 1200]
        if "_noRetry" not in body:
            defects.append(
                f"F5：`{fn}()` 缺 `_noRetry` —— 令牌过期时会先 401→刷新→重新签发，"
                f"导致「{'登出形同虚设' if fn == 'logout' else '登录带上旧凭据'}」"
            )
    return defects


def _f6_scan_text(app: str, rel: str, code: str) -> list[str]:
    defects = []
    for m in _STORAGE_WRITE.finditer(code):
        seg = code[max(0, m.start() - 200) : m.end() + 200]
        key = _key_written(seg).lower()
        if any(w in key for w in TOKEN_WORDS):
            defects.append(
                f"F6：{app} 端 `{rel}` 把令牌写入了 Web Storage（`{key.strip()}`）"
                "—— 绕过了 SDK 的内存方案"
            )
    return defects


def f6_apps_do_not_persist_tokens(
    synthetic: list[tuple[str, str, str]] | None = None,
) -> list[str]:
    """F6：四端不得绕过 SDK 自行持久化令牌。

    `synthetic` 形如 `[(app, relpath, text), ...]`，**仅自检用**——
    否则 F6 只能扫真实文件，永远无法证明自己有检出能力。
    """
    if synthetic is not None:
        out: list[str] = []
        for app, rel, text in synthetic:
            out += _f6_scan_text(app, rel, _strip_comments(text))
        return out

    defects = []
    for app in APPS:
        root = FRONTEND / "apps" / app
        if not root.exists():
            continue
        for path in list(root.rglob("*.ts")) + list(root.rglob("*.tsx")):
            if "node_modules" in path.parts or ".next" in path.parts:
                continue
            defects += _f6_scan_text(app, str(path.relative_to(FRONTEND)), _read(path))
    return defects


# ------------------------------------------------------------------- 自检
# ---------------------------------------------------------------------------
# 合成「合规 SDK」夹具。
#
# 🚨 这里**必须**是字面量，**不能** `_read(SDK)` 读真实文件。
# 实测踩过（2026-09-20）：原先 `good = _read(SDK)`，于是在真实 SDK 里注入一行
# `localStorage.setItem("access_token", ...)` 后，**自检**自己挂了（exit 2），
# 运行器据此报「工具坏了，先修探针」——而实际是**产品坏了**。
# 自检一旦依赖真实仓库状态，「自检失败 = 工具坏了」这个语义就失效了，
# 它会把产品缺陷**误诊**成工具缺陷，让人去改一个根本没坏的探针。
# ⇒ 自检必须与产品**完全解耦**：干净样本是夹具，产品好不好由阶段 2 实测去判。
# ---------------------------------------------------------------------------
CLEAN_SDK = """// 合成夹具：一份「合规 SDK」的最小形态，**与真实仓库无关**。
"use client";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

let _accessToken: string | null = null;

export const tokenStore = {
  get: () => _accessToken,
  set: (t: string | null) => { _accessToken = t; },
  clear: () => { _accessToken = null; },
};

async function refreshAccessToken(): Promise<boolean> {
  const res = await fetch(`${API_BASE}/api/v1/auth/refresh`, {
    method: "POST",
    credentials: "include",
  });
  return res.ok;
}

export async function request(path: string, init?: RequestInit) {
  const res = await fetch(`${API_BASE}${path}`, { ...init, credentials: "include" });
  if (res.status === 401 && !init?._noRetry) {
    if (await refreshAccessToken()) {
      return request(path, { ...init, _noRetry: true });
    }
  }
  return res;
}

export async function login(username: string, password: string) {
  return request("/api/v1/auth/login", {
    method: "POST",
    credentials: "include",
    body: JSON.stringify({ username, password }),
    _noRetry: true,
  });
}

export async function logout() {
  tokenStore.clear();
  return request("/api/v1/auth/logout", {
    method: "POST",
    credentials: "include",
    _noRetry: true,
  });
}
"""


def self_test() -> int:
    """把「坏 SDK」喂进去，确认每条判据都会红（否则它是个只会绿的摆设）。

    干净与肮脏**都来自夹具**，不读真实文件 —— 见 `CLEAN_SDK` 上方的说明。
    """
    good = CLEAN_SDK
    print("  自检：注入历史缺陷，确认各判据转红\n")
    checks = []

    cases = {
        "F1": ('localStorage.setItem("nlaw_token", access);', f1_f2_sdk_storage),
        "F2": ('sessionStorage.setItem("nlaw_rt", refresh);', f1_f2_sdk_storage),
    }
    for name, (inject, fn) in cases.items():
        dirty = good.replace(
            "export const API_BASE =", inject + "\nexport const API_BASE =", 1
        )
        checks.append((name, bool(fn(dirty))))

    # F4 必须**替换**而非追加：SDK 里本来就有 `credentials: "include"`，
    # 追加一个 `"omit"` 只会让判据继续命中旧值 ⇒ 自检假绿。
    dirty = good.replace('credentials: "include"', 'credentials: "omit"', 1)
    assert dirty != good, "F4 自检前提不成立：SDK 里没有可替换的 credentials 字面量"
    checks.append(("F4", bool(f4_credentials_include(dirty))))

    # F3：删掉内存变量
    dirty = re.sub(r"let _accessToken[^;]*;", "", good, count=1)
    checks.append(("F3", bool(f3_memory_token_store(dirty))))

    # F5：去掉 401 分支
    dirty = good.replace("res.status === 401", "res.status === 402", 1)
    checks.append(("F5", bool(f5_auto_refresh_and_noretry(dirty))))

    # F6：合成一个「某端自己写了令牌」的文件
    checks.append(
        (
            "F6",
            bool(
                f6_apps_do_not_persist_tokens(
                    [("web", "apps/web/x.tsx", 'localStorage.setItem("token", t);')]
                )
            ),
        )
    )

    all_ok = True
    for name, turned_red in checks:
        flag = "✓ 转红" if turned_red else "✗ 仍绿（判据无效）"
        print(f"    {name}  {flag}")
        all_ok = all_ok and turned_red

    # 反向对照：干净样本必须全绿
    clean_defects = (
        f1_f2_sdk_storage(good)
        + f3_memory_token_store(good)
        + f4_credentials_include(good)
        + f5_auto_refresh_and_noretry(good)
    )
    print(f"\n    干净样本缺陷数：{len(clean_defects)}（必须为 0）")
    all_ok = all_ok and not clean_defects

    print("\n  自检" + ("通过" if all_ok else "失败"))
    return 0 if all_ok else 2


# -------------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if not FRONTEND.exists() or not SDK.exists():
        print(f"EXIT=2  环境问题：找不到 {SDK}")
        return 2

    src = _read(SDK)
    defects: list[str] = []
    defects += f1_f2_sdk_storage(src)
    defects += f3_memory_token_store(src)
    defects += f4_credentials_include(src)
    defects += f5_auto_refresh_and_noretry(src)
    defects += f6_apps_do_not_persist_tokens()

    print("令牌存储门禁（P0-7 前端面）\n")
    print(f"  SDK      {SDK.relative_to(HERE.parent)}")
    print(f"  四端     {', '.join(APPS)}\n")
    for fid in ("F1/F2", "F3", "F4", "F5", "F6"):
        hit = [d for d in defects if d.startswith(fid.split("/")[0])]
        print(f"  {fid:<6} {'✗ ' + str(len(hit)) + ' 条' if hit else '✓ 通过'}")

    if defects:
        print("\n  ✗ 发现缺陷：")
        for d in defects:
            print(f"      · {d}")
        print(f"\nEXIT=1  产品缺陷 {len(defects)} 条")
        return 1

    print("\n  ✓ access 仅存内存；refresh 不落 JS 可读处；Cookie 会被携带；"
          "401 自动刷新且登出不重试")
    print("\nEXIT=0  通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
