"""探针 2：连上真实在跑的 admin（3002），摸清登录页与骨架的 DOM。

只读，不改任何东西。目的是拿到可靠的选择器，避免在正式脚本里瞎猜。
"""
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from cdp import Browser  # noqa: E402

ADMIN = "http://localhost:3002"


async def main() -> int:
    async with Browser(headless=True, width=390, height=844) as b:
        r = await b.apply_device(safe_area={"top": 47, "bottom": 34})
        print(f"安全区模拟：{r}\n")

        await b.goto(f"{ADMIN}/login", wait=2.5)
        print("URL:", await b.cdp.evaluate("location.href"))

        info = await b.cdp.evaluate(
            """(() => {
              const inputs = [...document.querySelectorAll('input')].map(i => ({
                name: i.name, type: i.type, id: i.id, placeholder: i.placeholder,
                aria: i.getAttribute('aria-label')
              }));
              const btns = [...document.querySelectorAll('button')].map(x => (x.innerText||'').trim()).filter(Boolean);
              const safeTop = document.querySelector('.safe-top');
              const safeBottom = document.querySelector('.safe-bottom');
              const cs = el => el ? {
                padTop: getComputedStyle(el).paddingTop,
                padBottom: getComputedStyle(el).paddingBottom,
                cls: el.className.slice(0, 90)
              } : null;
              const root = getComputedStyle(document.documentElement);
              return {
                viewport: (document.querySelector('meta[name=viewport]')||{}).content,
                inputs, btns,
                safeTop: cs(safeTop), safeBottom: cs(safeBottom),
                varSafeTop: root.getPropertyValue('--safe-top').trim(),
                varSafeBottom: root.getPropertyValue('--safe-bottom').trim(),
                varTopbarTotal: root.getPropertyValue('--topbar-total').trim(),
                bodyText: (document.body.innerText||'').replace(/\\s+/g,' ').slice(0, 260)
              };
            })()"""
        )
        import json

        print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
