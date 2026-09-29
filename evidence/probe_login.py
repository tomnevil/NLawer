"""探针 4：定位登录失败原因（表单没填进去？还是后端拒绝了？）。

方法：填表后先读回 input.value 确认；再点登录；并抓 /auth/login 的网络响应。
"""
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "backend"))

from cdp import Browser  # noqa: E402

ADMIN = "http://localhost:3002"


async def main() -> int:
    from app.seed.data import DEMO_USERS

    u = next(x for x in DEMO_USERS if x["username"] == "admin")
    user, pwd = u["username"], u["password"]

    async with Browser(headless=True, width=390, height=844) as b:
        await b.apply_device(safe_area={"top": 47, "bottom": 34})
        await b.goto(f"{ADMIN}/login", wait=3)

        # 用 CDP 原生输入事件（比 JS setter 更接近真人，React 一定能收到）
        box_u = await b.cdp.evaluate(
            "(() => { const r = document.getElementById('login-username').getBoundingClientRect();"
            "return {x: r.x + r.width/2, y: r.y + r.height/2}; })()"
        )
        box_p = await b.cdp.evaluate(
            "(() => { const r = document.getElementById('login-password').getBoundingClientRect();"
            "return {x: r.x + r.width/2, y: r.y + r.height/2}; })()"
        )

        async def click(x, y):
            for t in ("mousePressed", "mouseReleased"):
                await b.cdp.send(
                    "Input.dispatchMouseEvent",
                    type=t, x=x, y=y, button="left", clickCount=1,
                )

        await click(box_u["x"], box_u["y"])
        await b.cdp.send("Input.insertText", text=user)
        await click(box_p["x"], box_p["y"])
        await b.cdp.send("Input.insertText", text=pwd)
        await asyncio.sleep(0.5)

        filled = await b.cdp.evaluate(
            "({u: document.getElementById('login-username').value,"
            " p: document.getElementById('login-password').value.length})"
        )
        print("填表回读：", filled)

        # 监听网络
        b.cdp._events.clear()
        await b.cdp.evaluate(
            """(() => { const x=[...document.querySelectorAll('button')].find(z=>z.innerText.trim()==='登录');
                       if(x){x.click();return true} return false })()"""
        )
        await asyncio.sleep(5)

        hits = []
        for e in b.cdp._events:
            m = e.get("method", "")
            p = e.get("params", {})
            if m == "Network.responseReceived":
                url = p.get("response", {}).get("url", "")
                if "/api/" in url:
                    hits.append((p["response"]["status"], url))
            elif m == "Network.loadingFailed":
                hits.append(("FAILED", p.get("errorText")))
        print("网络命中：")
        for s, url in hits[:10]:
            print(f"  {s}  {url[:110]}")
        if not hits:
            print("  （无 /api/ 请求发出）")

        print("当前 URL:", await b.cdp.evaluate("location.href"))
        print("页面文本:", (await b.cdp.evaluate("document.body.innerText"))[:160].replace("\n", " "))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
