"""探针 5：摸清「切换视角」浮层与「复核队列」详情弹窗的 DOM。

只读。为正式验证脚本确定选择器。
"""
import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "backend"))

from cdp import Browser  # noqa: E402

ADMIN = "http://localhost:3002"


async def click_text(b, text: str, exact: bool = True) -> bool:
    js = (
        "(() => { const t = %s; const els = [...document.querySelectorAll('button,a')];"
        " const hit = els.find(x => %s); if (!hit) return false;"
        " hit.click(); return true; })()"
        % (json.dumps(text), ("x.innerText.trim() === t" if exact else "x.innerText.includes(t)"))
    )
    return bool(await b.cdp.evaluate(js))


async def login(b, user, pwd):
    await b.goto(f"{ADMIN}/login", wait=3)
    for sel, txt in (("login-username", user), ("login-password", pwd)):
        box = await b.cdp.evaluate(
            "(() => { const r = document.getElementById(%s).getBoundingClientRect();"
            "return {x: r.x + r.width/2, y: r.y + r.height/2}; })()" % json.dumps(sel)
        )
        for t in ("mousePressed", "mouseReleased"):
            await b.cdp.send("Input.dispatchMouseEvent", type=t, x=box["x"], y=box["y"],
                             button="left", clickCount=1)
        await b.cdp.send("Input.insertText", text=txt)
    await asyncio.sleep(0.4)
    await click_text(b, "登录")
    await asyncio.sleep(4)


async def main() -> int:
    from app.seed.data import DEMO_USERS

    u = next(x for x in DEMO_USERS if x["username"] == "admin")
    async with Browser(headless=True, width=390, height=844) as b:
        await b.apply_device(safe_area={"top": 47, "bottom": 34})
        await login(b, u["username"], u["password"])

        print("=== 点「切换视角」 ===")
        print("点击结果:", await click_text(b, "切换视角"))
        await asyncio.sleep(1.5)
        print(
            json.dumps(
                await b.cdp.evaluate(
                    r"""(() => ({
                      dialogs: [...document.querySelectorAll('[role=dialog]')].map(d => ({
                        cls: (d.className||'').slice(0,70),
                        text: (d.innerText||'').replace(/\s+/g,' ').slice(0,300)
                      })),
                      radios: [...document.querySelectorAll('input[type=radio]')].map(r=>({v:r.value,id:r.id})),
                      clickables: [...document.querySelectorAll('button,label,[role=menuitem],[role=option]')]
                        .map(x=>(x.innerText||'').trim()).filter(Boolean).slice(0,20),
                      bodyTail: (document.body.innerText||'').replace(/\s+/g,' ').slice(-400)
                    }))()"""
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        await b.screenshot(pathlib.Path(__file__).parent / "shot_switcher.png")

        print("\n=== 进 /reviews ===")
        await b.goto(f"{ADMIN}/reviews", wait=4)
        print("URL:", await b.cdp.evaluate("location.href"))
        print(
            json.dumps(
                await b.cdp.evaluate(
                    r"""(() => {
                      const rows = [...document.querySelectorAll('tbody tr')];
                      return {
                        rowCount: rows.length,
                        firstRow: rows[0] ? (rows[0].innerText||'').replace(/\s+/g,' ').slice(0,200) : null,
                        rowBtns: rows[0] ? [...rows[0].querySelectorAll('button')].map(x=>(x.innerText||'').trim()) : [],
                        body: (document.body.innerText||'').replace(/\s+/g,' ').slice(0,500)
                      };
                    })()"""
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        await b.screenshot(pathlib.Path(__file__).parent / "shot_reviews_list.png")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
