"""用本机 Chromium + CDP 渲染手机视口截图（iPhone 12 Pro + 刘海安全区）。

复用 evidence/cdp.py（无需 playwright，直连 CDP）。
运行：backend 下 `python evidence/mobile_preview.py`
"""
import asyncio
import pathlib
import sys

sys.path.insert(0, "C:/Users/RS/Documents/trae_projects/NLawer/evidence")
from cdp import Browser  # noqa: E402

OUT = pathlib.Path("C:/Users/RS/Documents/trae_projects/NLawer/evidence")
# 消费者端(web 3000) 与 IM(3003) 是最移动化的两个端
SHOTS = {
    "mobile_web_3000": "http://localhost:3000",
    "mobile_im_3003": "http://localhost:3003",
}
# iPhone 12 Pro 逻辑分辨率；刘海 47px / 底部 Home 指示条 34px（与渲染级验证一致）
SAFE = {"top": 47, "bottom": 34, "left": 0, "right": 0}


async def main() -> None:
    async with Browser(headless=True, width=390, height=844, device_scale_factor=3) as b:
        await b.freeze_animations()
        sup = await b.apply_device(safe_area=SAFE, mobile=True)
        print("safe-area override:", sup)
        for name, url in SHOTS.items():
            await b.goto(url, wait=2.0)
            await b.settle(extra=3.0)
            p = OUT / f"{name}.png"
            await b.screenshot(p)
            print("saved", name, "->", p)


if __name__ == "__main__":
    asyncio.run(main())
