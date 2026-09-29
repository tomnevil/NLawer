"""探针：四端在 390px（`compact` 断点）下的**导航形态**实测。

起因：有人在手机宽度下看 admin（运营后台），发现菜单在**侧边抽屉**里，
问「设计稿明明是底部导航」。而 `08-mobile-client` / `09-mobile-lawyer`
是**客户端 / 律师端**的稿子，不是运营后台的。

设计规范 `design-spec.md` §8.1 / §8.2 / §8.4 三处都写了：
**运营后台移动形态 = AppBar + 只读标识，无 Tab Bar**。

本探针把「代码里怎么写的」变成「390px 下真的渲染了什么」：
  - 有没有 `nav[aria-label="主导航"]`（= 底部 Tab Bar 的 DOM 特征）
  - 它在不在视口底部（`position:fixed` + `bottom:0`）
  - 有没有只读标识、有没有汉堡/抽屉入口

用法：python evidence/probe_mobile_nav.py
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "backend"))

from cdp import Browser  # noqa: E402

APPS = {
    "web": (3000, "ent_admin"),
    "lawyer": (3001, "lawyer_wang"),
    "admin": (3002, "admin"),
    "im": (3003, "client"),
}

# 一次算完，避免多轮往返
PROBE_JS = """(() => {
  const nav = document.querySelector('nav[aria-label="主导航"]');
  let box = null, fixedBottom = false;
  if (nav) {
    const r = nav.getBoundingClientRect();
    const cs = getComputedStyle(nav);
    box = [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)];
    fixedBottom = cs.position === 'fixed' && Math.abs(r.bottom - innerHeight) < 2;
  }
  const fixedBars = [...document.querySelectorAll('body *')].filter(el => {
    const cs = getComputedStyle(el);
    if (cs.position !== 'fixed') return false;
    const r = el.getBoundingClientRect();
    return r.height > 40 && r.width > innerWidth * 0.7 && r.bottom > innerHeight - 4;
  }).map(el => el.tagName.toLowerCase() + '.' + (el.className || '').toString().split(' ')[0]);

  const text = document.body.innerText || '';
  return {
    vw: innerWidth, vh: innerHeight,
    hasMainNav: !!nav,
    navBox: box,
    navFixedBottom: fixedBottom,
    bottomFixedBars: [...new Set(fixedBars)],
    hasReadonlyBadge: text.includes('只读'),
    hasHamburger: !!document.querySelector('[aria-label*="菜单"], [aria-label*="导航"], button.lg\\\\:hidden'),
    navItems: nav ? [...nav.querySelectorAll('a,button')].map(a => (a.innerText||'').trim()).filter(Boolean) : [],
  };
})()"""


async def login(b: Browser, base: str, username: str) -> bool:
    from app.seed.data import DEMO_USERS

    u = next(x for x in DEMO_USERS if x["username"] == username)
    await b.goto(f"{base}/login", wait=1.5)
    await b.type_into("#login-username", u["username"])
    await b.type_into("#login-password", u["password"])
    await asyncio.sleep(0.4)
    await b.click_text("登录")
    await asyncio.sleep(4.0)
    return "/login" not in (await b.cdp.evaluate("location.href") or "")


async def main() -> int:
    print("── 390×844（compact 断点）下的导航形态实测 ──\n")
    for app, (port, user) in APPS.items():
        base = f"http://localhost:{port}"
        async with Browser(headless=True, width=390, height=844, device_scale_factor=3) as b:
            await b.apply_device(mobile=True)
            if not await login(b, base, user):
                print(f"{app:<7} :{port}  登录失败（环境问题）")
                continue
            await b.goto(f"{base}/", wait=1.5)
            await b.settle(extra=2.0)
            r = await b.cdp.evaluate(PROBE_JS)

        verdict = "底部 Tab Bar" if (r["hasMainNav"] and r["navFixedBottom"]) else "无底部 Tab Bar"
        print(f"{app:<7} :{port}  视口 {r['vw']}x{r['vh']}  →  {verdict}")
        print(f"        nav[aria-label=主导航]: {r['hasMainNav']}  贴底固定: {r['navFixedBottom']}  box={r['navBox']}")
        print(f"        Tab 项: {r['navItems']}")
        print(f"        贴底固定条: {r['bottomFixedBars']}")
        print(f"        只读标识: {r['hasReadonlyBadge']}   汉堡/抽屉入口: {r['hasHamburger']}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
