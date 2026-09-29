"""探测：admin（运营后台）到底有没有**角色门禁**，以及四端登录后的落地页长什么样。

**为什么要单独探一次**：项目记忆里写的是「四端登录**不做**角色门禁，数据隔离在后端」。
但 `probe_app_login.py` 的实测输出显示 admin 端在 6 个账号里，
除 `admin` 外的 5 个都出现了错误横幅（`权限不足` / `角色无权访问`）——
**实测与记忆冲突，必须重新测清楚**，不能拿旧结论盖过去。

本脚本回答三个问题：
1. 非 `admin` 角色登录 admin 端后，**停在哪个 URL**？（有跳转 = 前端门禁；无跳转 = 只弹错）
2. 页面上**实际可见的文字**是什么？（区分「前端守卫拦下」与「后端 403 被渲染成横幅」）
3. 顺带把四端登录后的落地页截图存到 `evidence/preview_<app>.png`，供人工核对。

退出码：0 = 探测完成（本脚本只做**表征**，不判定产品对错）；
        2 = 环境问题（某个端起不来 / 登录失败）。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "backend"))

from cdp import Browser  # noqa: E402

# 每个端「应该用哪个账号测」——与 probe_app_login.py 实测出的可用账号一致。
APPS = {
    "web": (3000, "ent_admin"),
    "lawyer": (3001, "lawyer_wang"),
    "admin": (3002, "admin"),
    "im": (3003, "client"),
}

VISIBLE_TEXT_JS = """(() => {
  const t = document.body ? document.body.innerText : '';
  return t.replace(/\\n{2,}/g, '\\n').trim().slice(0, 400);
})()"""

BANNER_JS = """(() => {
  const sels = ['[role=alert]', '.text-danger-600', '.text-red-600', '.text-amber-600'];
  const out = [];
  for (const s of sels) {
    for (const e of document.querySelectorAll(s)) {
      const t = (e.innerText || '').trim();
      if (t) out.push(t.slice(0, 60));
    }
  }
  return [...new Set(out)];
})()"""


async def login(b: Browser, base: str, username: str) -> dict:
    from app.seed.data import DEMO_USERS

    u = next((x for x in DEMO_USERS if x["username"] == username), None)
    if not u:
        raise RuntimeError(f"种子数据里没有账号 {username}")

    await b.goto(f"{base}/login", wait=1.0)
    await b.cdp.evaluate(
        "(() => { try { localStorage.clear(); sessionStorage.clear(); } catch(e){} })()"
    )
    await b.goto(f"{base}/login", wait=2.0)
    await b.type_into("#login-username", u["username"])
    await b.type_into("#login-password", u["password"])
    await asyncio.sleep(0.3)
    await b.click_text("登录")
    await asyncio.sleep(3.5)
    return {"username": username}


async def main() -> int:
    async with Browser(headless=True, width=1280, height=900, device_scale_factor=1) as b:
        await b.apply_device(mobile=False)

        # ---------- 第一段：四端落地页截图（用「该端的正确账号」）----------
        print("=== 四端登录后落地页 ===")
        for app, (port, user) in APPS.items():
            base = f"http://localhost:{port}"
            await login(b, base, user)
            href = await b.cdp.evaluate("location.href") or ""
            head = await b.cdp.evaluate(
                "(() => { const h = document.querySelector('h1,h2');"
                " return h ? h.innerText.trim().slice(0,40) : ''; })()"
            )
            banners = await b.cdp.evaluate(BANNER_JS) or []
            shot = HERE / f"preview_{app}.png"
            await b.screenshot(shot)
            print(
                f"  {app:<7} :{port}  user={user:<12} href={href.replace(base,'')!r:<14}"
                f" h={head!r:<20} 横幅={banners}"
            )

        # ---------- 第二段：admin 的角色门禁表征 ----------
        print("\n=== admin(:3002) 角色门禁表征：拿非 admin 账号登 ===")
        base = "http://localhost:3002"
        for user in ("client", "lawyer_wang", "admin"):
            await login(b, base, user)
            href = await b.cdp.evaluate("location.href") or ""
            banners = await b.cdp.evaluate(BANNER_JS) or []
            text = await b.cdp.evaluate(VISIBLE_TEXT_JS) or ""
            print(f"\n  --- 账号 {user} ---")
            print(f"  最终 URL : {href.replace(base, '')}")
            print(f"  错误横幅 : {banners}")
            print("  可见正文 :")
            for line in text.splitlines()[:12]:
                print(f"    | {line}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
