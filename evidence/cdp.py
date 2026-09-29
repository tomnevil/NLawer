"""CDP 极简客户端：不依赖 playwright，直接用本机已装的 Chromium + WebSocket。

为什么不用 playwright：
  本机 `%LOCALAPPDATA%\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe` **已存在**，
  但 npm / pip 里都没有 playwright 包。装包要联网且要动 node_modules。
  而 CDP 本身就是 WebSocket + JSON，用标准库 + `websockets` 足够。

用途：给「渲染级验证」提供真实浏览器环境（`env(safe-area-inset-*)` 只在真浏览器里生效）。
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request

import websockets


def _chrome_candidates() -> list[pathlib.Path]:
    """候选浏览器路径，**按平台**排列。

    🚨 为什么要按平台（2026-09-24，用户拍板 ③）：原实现只有 Windows 路径
    （`%LOCALAPPDATA%` 与 `C:/Program Files/...`）⇒ 在 **Linux CI runner** 上
    `find_chrome()` 直接 `RuntimeError`。而 CI 的 5 个 job **全是** `ubuntu-latest`
    ⇒ **所有 CDP 探针一条都起不来**（这就是「浏览器 job」立项的技术根因）。
    ⚠️ GitHub `ubuntu-latest`（Ubuntu 24.04）**预装** Chrome 152 / Chromium 152 / Edge 152
    （出处：`actions/runner-images` 的 `Ubuntu2404-Readme.md`）⇒ **不需要安装步骤**。

    优先级：`CHROME_BIN` 环境变量 > **本平台**常规安装位。
    `CHROME_BIN` 是跨平台社区惯例（puppeteer / playwright 同款），
    既让 CI 可以显式指定，也让本机能强制选某个浏览器。
    ⚠️ **刻意不再跨平台兜底**：把另一平台的路径留在候选里，
    只会让「找错浏览器」这类问题更难诊断（找不到就该报错，不该乱猜）。
    """
    cands: list[pathlib.Path] = []
    env = os.environ.get("CHROME_BIN")
    if env:
        cands.append(pathlib.Path(env))
    if os.name == "nt":
        local = pathlib.Path(os.environ.get("LOCALAPPDATA", ""))
        cands += [
            local / "ms-playwright" / "chromium-1234" / "chrome-win64" / "chrome.exe",
            local / "ms-playwright" / "chromium-1228" / "chrome-win64" / "chrome.exe",
            pathlib.Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
            pathlib.Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
            pathlib.Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        ]
    else:
        cands += [
            pathlib.Path("/usr/bin/google-chrome"),
            pathlib.Path("/usr/bin/google-chrome-stable"),
            pathlib.Path("/usr/bin/chromium"),
            pathlib.Path("/usr/bin/chromium-browser"),
            pathlib.Path("/snap/bin/chromium"),
            pathlib.Path("/usr/bin/microsoft-edge"),
        ]
    return cands


CHROME_CANDIDATES = _chrome_candidates()


def find_chrome() -> pathlib.Path:
    for p in CHROME_CANDIDATES:
        if p.exists():
            return p
    raise RuntimeError(
        f"找不到可用浏览器（平台 os.name={os.name}），候选：\n  "
        + "\n  ".join(str(p) for p in CHROME_CANDIDATES)
        + "\n  ⇒ 可用 `CHROME_BIN=<路径>` 显式指定。")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http_json(url: str, timeout: float = 2.0):
    """读 CDP 的 HTTP 端点。

    ⚠️ **必须绕开代理**：本机 `HTTP_PROXY/HTTPS_PROXY` 指向沙箱代理（`127.0.0.1:14453`），
    且**没有设 `NO_PROXY`** ⇒ `urllib` 会把 `127.0.0.1:<debug-port>` 也发去代理，
    表现为「浏览器起来了但 30s 连不上调试端口」。
    实测教训：`curl` 需要 `--noproxy '*'`，Python 需要显式空 ProxyHandler。
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8"))


# 注入到每个新文档：冻结动画 / 过渡 / 光标 / 滚动条。
# **像素基线的前提**——不冻结的话，同一页面两次截图都不会逐像素一致，
# 比对会天天误报「视觉漂移」。
FREEZE_JS = """(() => {
  const css = '*,*::before,*::after{'
    + 'animation:none!important;transition:none!important;'
    + 'caret-color:transparent!important;scroll-behavior:auto!important}'
    + 'html{scrollbar-width:none!important}'
    + '::-webkit-scrollbar{display:none!important;width:0!important;height:0!important}';
  const add = () => {
    const s = document.createElement('style');
    s.setAttribute('data-cdp-freeze', '1');
    s.textContent = css;
    (document.head || document.documentElement).appendChild(s);
  };
  if (document.head) add();
  else document.addEventListener('DOMContentLoaded', add);
})()"""


class CDPError(RuntimeError):
    pass


class CDP:
    """一个 page target 的 CDP 会话。"""

    def __init__(self, ws) -> None:
        self._ws = ws
        self._id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._events: list[dict] = []
        self._reader: asyncio.Task | None = None

    async def start(self) -> None:
        self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        async for raw in self._ws:
            msg = json.loads(raw)
            if "id" in msg:
                fut = self._pending.pop(msg["id"], None)
                if fut and not fut.done():
                    fut.set_result(msg)
            else:
                self._events.append(msg)

    async def send(self, method: str, **params):
        self._id += 1
        mid = self._id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        await self._ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        msg = await asyncio.wait_for(fut, timeout=30)
        if "error" in msg:
            raise CDPError(f"{method} -> {msg['error']}")
        return msg.get("result", {})

    async def evaluate(self, expr: str):
        r = await self.send(
            "Runtime.evaluate",
            expression=expr,
            returnByValue=True,
            awaitPromise=True,
        )
        res = r.get("result", {})
        if r.get("exceptionDetails"):
            raise CDPError(f"JS 异常: {r['exceptionDetails'].get('text')} :: {expr[:120]}")
        return res.get("value")

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        await self._ws.close()

    def drain_events(self) -> list[dict]:
        """取出并清空累积的 CDP 事件。

        用途：检查**未捕获异常 / 控制台错误 / 失败请求**——
        「页面画出来了」不等于「页面没出错」。事件由 `_read_loop` 一直攒着，
        这里一次性取走，避免两次检查之间互相污染。
        """
        out, self._events = self._events, []
        return out


class Browser:
    """上下文管理器：起浏览器 → 连 page target → 退出时清理。"""

    def __init__(
        self,
        headless: bool = True,
        width: int = 390,
        height: int = 844,
        device_scale_factor: int = 3,
    ) -> None:
        self.headless = headless
        self.width = width
        self.height = height
        self.device_scale_factor = device_scale_factor
        self.port = free_port()
        self.profile = pathlib.Path(tempfile.mkdtemp(prefix="cdp-prof-"))
        self.proc: subprocess.Popen | None = None
        self.cdp: CDP | None = None
        self.version: dict = {}

    async def __aenter__(self) -> "Browser":
        exe = find_chrome()
        args = [
            str(exe),
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-gpu",
            # 沙箱环境：Chrome 的 setuid/命名空间限制 + 别走沙箱代理
            "--no-sandbox",
            "--no-proxy-server",
            "--disable-features=Translate,MediaRouter",
            "--window-size=800,1200",
        ]
        args.append("--headless=new" if self.headless else "--new-window")
        args.append("about:blank")
        self.proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0
        )

        base = f"http://127.0.0.1:{self.port}"
        deadline = time.time() + 30
        last_err: Exception | None = None
        while time.time() < deadline:
            try:
                self.version = http_json(f"{base}/json/version")
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                await asyncio.sleep(0.3)
        else:
            raise RuntimeError(f"浏览器未在 30s 内就绪（{base}）：{last_err}")

        # 找到第一个 page target（about:blank）
        page = None
        while time.time() < deadline:
            targets = http_json(f"{base}/json/list")
            pages = [t for t in targets if t.get("type") == "page"]
            if pages:
                page = pages[0]
                break
            await asyncio.sleep(0.3)
        if not page:
            raise RuntimeError("找不到 page target")

        ws = await websockets.connect(page["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024)
        self.cdp = CDP(ws)
        await self.cdp.start()
        await self.cdp.send("Page.enable")
        await self.cdp.send("Runtime.enable")
        await self.cdp.send("DOM.enable")
        await self.cdp.send("Network.enable")
        await self.apply_device()
        return self

    async def apply_device(self, *, safe_area: dict | None = None, mobile: bool = True) -> dict:
        """设移动端视口；可选设安全区内边距（模拟刘海屏）。

        `Emulation.setSafeAreaInsetsOverride` 是 Chrome 138+ 的方法——
        这是「`env(safe-area-inset-*)` 只在真浏览器里才有值」的唯一可靠模拟手段。
        """
        await self.cdp.send(
            "Emulation.setDeviceMetricsOverride",
            width=self.width,
            height=self.height,
            deviceScaleFactor=self.device_scale_factor,
            mobile=mobile,
        )
        if safe_area is None:
            return {"supported": None, "note": "未设置安全区"}
        try:
            await self.cdp.send(
                "Emulation.setSafeAreaInsetsOverride",
                insets={
                    "top": safe_area.get("top", 0),
                    "bottom": safe_area.get("bottom", 0),
                    "left": safe_area.get("left", 0),
                    "right": safe_area.get("right", 0),
                },
            )
            return {"supported": True}
        except CDPError as e:
            return {"supported": False, "note": str(e)[:200]}

    async def click_text(self, text: str, exact: bool = True) -> bool:
        """点第一个 `innerText` 匹配的元素（找不到返回 False，不抛）。"""
        js = (
            "(() => { const t = %s;"
            " const els = [...document.querySelectorAll('button,a,label,div[role=menuitem]')];"
            " const hit = els.find(x => %s); if (!hit) return false; hit.click(); return true; })()"
            % (json.dumps(text), ("x.innerText.trim() === t" if exact else "x.innerText.includes(t)"))
        )
        return bool(await self.cdp.evaluate(js))

    async def type_into(self, selector: str, text: str) -> bool:
        """先点中元素再用 `Input.insertText` 输入。

        必须走真实输入事件——React 受控组件直接改 `el.value` 不会触发 `onChange`。
        """
        box = await self.cdp.evaluate(
            "(() => { const el = document.querySelector(%s); if (!el) return null;"
            " const r = el.getBoundingClientRect(); return {x: r.x + r.width/2, y: r.y + r.height/2}; })()"
            % json.dumps(selector)
        )
        if not box:
            return False
        for t in ("mousePressed", "mouseReleased"):
            await self.cdp.send(
                "Input.dispatchMouseEvent", type=t, x=box["x"], y=box["y"], button="left", clickCount=1
            )
        await self.cdp.send("Input.insertText", text=text)
        return True

    async def freeze_animations(self) -> None:
        """冻结动画 / 过渡 / 光标 / 滚动条，并把 `prefers-reduced-motion` 置为 `reduce`。

        **必须在首次 `goto` 之前调用**（`addScriptToEvaluateOnNewDocument` 只对新文档生效）。
        """
        await self.cdp.send("Page.addScriptToEvaluateOnNewDocument", source=FREEZE_JS)
        await self.cdp.send(
            "Emulation.setEmulatedMedia",
            features=[{"name": "prefers-reduced-motion", "value": "reduce"}],
        )

    async def settle(self, extra: float = 2.0, timeout: float = 20.0) -> None:
        """等「文档完成 + 字体就绪」，再多等 `extra` 秒（等前端取数渲染完）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                ready = await self.cdp.evaluate("document.readyState")
                fonts = await self.cdp.evaluate("document.fonts ? document.fonts.status : 'loaded'")
            except Exception:  # noqa: BLE001  导航中途上下文销毁
                break
            if ready == "complete" and fonts == "loaded":
                break
            await asyncio.sleep(0.25)
        await asyncio.sleep(extra)

    async def goto(self, url: str, wait: float = 1.2) -> None:
        await self.cdp.send("Page.navigate", url=url)
        await asyncio.sleep(wait)

    async def screenshot(self, path: pathlib.Path, full: bool = True) -> None:
        r = await self.cdp.send("Page.captureScreenshot", format="png", captureBeyondViewport=full)
        import base64

        path.write_bytes(base64.b64decode(r["data"]))

    def drain_events(self) -> list[dict]:
        """取出并清空累积的 CDP 事件（异常 / 控制台错误 / 失败请求）。"""
        return self.cdp.drain_events() if self.cdp else []

    async def __aexit__(self, *exc) -> None:
        if self.cdp:
            await self.cdp.close()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        # 单目录清理（沙箱安全：只删自己建的临时 profile，逐文件但数量有限）
        if self.profile.exists():
            shutil.rmtree(self.profile, ignore_errors=True)
