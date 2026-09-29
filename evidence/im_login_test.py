"""真实浏览器登录 IM(:3003)：捕获登录请求实际打到的 URL 与状态码。

目的：证明「3003 预览但登不了」的根因（SDK 把 API_BASE 内联成旧的 :8000，
而不是 .env.local 里的 :8001）在重启 IM 后已修复。

复用 evidence/cdp.py。
注意：绝不打印 token，只报告 请求 URL / 状态码 / 登录后路径。
"""
import asyncio
import json
import sys
import time

sys.path.insert(0, "C:/Users/RS/Documents/trae_projects/NLawer/evidence")
from cdp import Browser  # noqa: E402

URL = "http://localhost:3003/login"
USER, PASS = "lawyer_wang", "Lawyer@12345"
LOGIN_PATH = "/api/v1/auth/login"


async def wait_input(b: Browser, sel: str, timeout: float = 12) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if await b.cdp.evaluate(f"!!document.querySelector({json.dumps(sel)})"):
            return True
        await asyncio.sleep(0.5)
    return False


async def main() -> None:
    async with Browser(headless=True, width=1280, height=900, device_scale_factor=1) as b:
        await b.apply_device(mobile=False)  # 桌面视口
        await b.freeze_animations()
        await b.goto(URL, wait=2.0)

        # 等登录表单水合（Next dev 首屏可能慢）
        has_pw = await wait_input(b, "input[type=password]")
        await b.settle(extra=2.0)
        print("密码框存在:", has_pw)

        # 清掉历史事件，只关注本次登录
        b.drain_events()

        # 用户名框坐标
        ub = await b.cdp.evaluate(
            "(() => {const all=[...document.querySelectorAll('input')]"
            ".filter(i=>i.type!=='hidden'&&i.type!=='password');"
            "const u=all.find(i=>/user|account|手机|邮箱|name/i.test(i.name+i.id+i.placeholder))||all[0];"
            "if(!u)return null;const r=u.getBoundingClientRect();"
            "return {x:r.x+r.width/2,y:r.y+r.height/2};})()"
        )
        if ub:
            for t in ("mousePressed", "mouseReleased"):
                await b.cdp.send("Input.dispatchMouseEvent", type=t, x=ub["x"], y=ub["y"], button="left", clickCount=1)
            await b.cdp.send("Input.insertText", text=USER)

        # 密码框坐标
        pb = await b.cdp.evaluate(
            "(() => {const p=document.querySelector('input[type=password]');if(!p)return null;"
            "const r=p.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()"
        )
        if pb:
            for t in ("mousePressed", "mouseReleased"):
                await b.cdp.send("Input.dispatchMouseEvent", type=t, x=pb["x"], y=pb["y"], button="left", clickCount=1)
            await b.cdp.send("Input.insertText", text=PASS)

        # 提交
        submitted = await b.cdp.evaluate(
            "(() => {const b=document.querySelector('button[type=submit]');if(b){b.click();return 'submit-btn';}"
            " const t=[...document.querySelectorAll('button,a')].find(x=>/登录|login|sign in/i.test(x.innerText));"
            " if(t){t.click();return 'text:'+t.innerText;} return 'none';})()"
        )
        print("提交方式:", submitted)

        await asyncio.sleep(5)

        # 扫描本次登录的网络事件
        reqs, resps, fails = [], [], []
        for ev in b.drain_events():
            m = ev.get("method")
            if m == "Network.requestWillBeSent":
                u = ev["params"]["request"]["url"]
                if LOGIN_PATH in u:
                    reqs.append(u)
            elif m == "Network.responseReceived":
                r = ev["params"]["response"]
                if LOGIN_PATH in r.get("url", ""):
                    resps.append({"url": r["url"], "status": r["status"]})
            elif m == "Network.loadingFailed":
                fails.append(ev["params"].get("errorText"))

        final = await b.cdp.evaluate(
            "({path: location.pathname, body: document.body.innerText.slice(0,200)})"
        )
        print("登录请求 URL:", json.dumps(reqs, ensure_ascii=False))
        print("登录响应:", json.dumps(resps, ensure_ascii=False))
        print("加载失败:", json.dumps(fails, ensure_ascii=False))
        print("登录后路径/页文:", json.dumps(final, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
