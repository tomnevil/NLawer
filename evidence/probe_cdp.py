"""探针：确认本机能否用 CDP 驱动 Chromium，以及安全区模拟是否可用。

先实测，再决定「渲染级验证」能不能做——不要沿用「需非 Windows」的旧结论。
"""
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from cdp import Browser, find_chrome  # noqa: E402


async def main() -> int:
    print(f"浏览器：{find_chrome()}")
    async with Browser(headless=True) as b:
        print(f"版本：{b.version.get('Browser')}  protocol={b.version.get('Protocol-Version')}")
        r = await b.apply_device(safe_area={"top": 47, "bottom": 34, "left": 0, "right": 0})
        print(f"安全区模拟：{r}")

        await b.goto("data:text/html,<meta name=viewport content='width=device-width,initial-scale=1,viewport-fit=cover'><div id=t style='padding-top:env(safe-area-inset-top);padding-bottom:env(safe-area-inset-bottom)'>x</div>", wait=0.6)
        top = await b.cdp.evaluate("getComputedStyle(document.getElementById('t')).paddingTop")
        bot = await b.cdp.evaluate("getComputedStyle(document.getElementById('t')).paddingBottom")
        print(f"env(safe-area-inset-top)  → 计算 padding-top = {top}")
        print(f"env(safe-area-inset-bottom) → 计算 padding-bottom = {bot}")

        # 对照组：清掉安全区
        await b.apply_device(safe_area={"top": 0, "bottom": 0})
        top0 = await b.cdp.evaluate("getComputedStyle(document.getElementById('t')).paddingTop")
        print(f"对照组（insets=0）        → 计算 padding-top = {top0}")

        ok = r.get("supported") and top == "47px" and bot == "34px" and top0 == "0px"
        print("\n结论：" + ("✅ 渲染级安全区验证**可行**" if ok else "❌ 不可行，需换手段"))
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
