"""探测：四个端各自「用哪个演示账号能登录成功、落在哪一页」。

**为什么要实测而不是读代码**：登录页源码里列着演示账号（含明文口令），
读取会被敏感内容门禁拦下；而且「源码里列了」不等于「这个端接受这个角色」——
四端的角色门禁可能在后端 `auth/login` 或前端 `useAuthGuard` 里，
只有真的登一次才知道。

用法：python evidence/probe_app_login.py
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "backend"))

from cdp import Browser  # noqa: E402

APPS = {"web": 3000, "lawyer": 3001, "admin": 3002, "im": 3003}
CANDIDATES = ("ent_admin", "client", "lawyer_wang", "firm_admin", "admin", "assistant")


async def try_login(b: Browser, base: str, username: str) -> dict:
    """返回 {ok, href, heading, error}。"""
    from app.seed.data import DEMO_USERS

    u = next((x for x in DEMO_USERS if x["username"] == username), None)
    if not u:
        return {"ok": False, "note": "种子无此账号"}

    # 清掉上一轮的会话，避免「上一次登录成功」被误判成本次成功
    await b.goto(f"{base}/login", wait=1.0)
    await b.cdp.evaluate("(() => { try { localStorage.clear(); sessionStorage.clear(); } catch(e){} })()")
    await b.goto(f"{base}/login", wait=2.0)

    await b.type_into("#login-username", u["username"])
    await b.type_into("#login-password", u["password"])
    await asyncio.sleep(0.3)
    await b.click_text("登录")
    await asyncio.sleep(3.5)

    href = await b.cdp.evaluate("location.href") or ""
    heading = await b.cdp.evaluate(
        "(() => { const h = document.querySelector('h1,h2'); return h ? h.innerText.trim().slice(0,40) : ''; })()"
    )
    err = await b.cdp.evaluate(
        "(() => { const e = document.querySelector('[role=alert],.text-danger-600,.text-red-600');"
        " return e ? e.innerText.trim().slice(0,60) : ''; })()"
    )
    return {"ok": "/login" not in href, "href": href.replace(base, ""), "heading": heading, "error": err}


async def main() -> int:
    async with Browser(headless=True, width=1280, height=900, device_scale_factor=1) as b:
        await b.apply_device(mobile=False)
        for app, port in APPS.items():
            base = f"http://localhost:{port}"
            print(f"\n=== {app} ({base}) ===")
            # 不 break：要的是**完整矩阵**（哪个角色被这个端接受），
            # 不是「第一个能进的账号」——只报第一个会把授权问题盖住。
            for uname in CANDIDATES:
                r = await try_login(b, base, uname)
                flag = "OK  " if r["ok"] else "fail"
                print(f"  [{flag}] {uname:<12} href={r.get('href','')!r:<18} h={r.get('heading','')!r:<24} err={r.get('error','')!r}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
