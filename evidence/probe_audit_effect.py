"""探针：**采集动作本身会不会写审计日志**（观察者效应）。

背景：像素基线里 `/audit` 页的「审计记录总量」在两次采集之间从 90 变成 93。
若原因是**采集脚本自己的页面加载**，那这一页就进不了稳定基线——它会自己把自己测脏。

跑法：`python evidence/probe_audit_effect.py`
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "backend"))

from cdp import Browser  # noqa: E402
from envprobe import ADMIN, api, api_token  # noqa: E402

PAGES = ("/", "/reviews", "/cases", "/dispatches", "/compliance", "/billing", "/complaints", "/audit")


def audit_total(tok: str) -> int | None:
    return api("/api/v1/audit/retention/stats", tok, "platform").get("data", {}).get("total")


async def main() -> int:
    from app.seed.data import DEMO_USERS

    u = next(x for x in DEMO_USERS if x["username"] == "admin")
    tok = api_token()
    before = audit_total(tok)
    print(f"导航前 total = {before}")

    async with Browser(headless=True, width=390, height=844) as b:
        await b.goto(f"{ADMIN}/login", wait=2)
        await b.type_into("#login-username", u["username"])
        await b.type_into("#login-password", u["password"])
        await b.click_text("登录")
        await asyncio.sleep(3)
        for p in PAGES:
            await b.goto(f"{ADMIN}{p}", wait=2)
            print(f"  访问 {p:14s} 后 total = {audit_total(tok)}")

    after = audit_total(tok)
    delta = (after or 0) - (before or 0)
    print(f"\n导航后 total = {after}   增量 = {delta}")
    if delta > 0:
        print("⇒ **采集动作自己会写审计日志**（观察者效应）⇒ `/audit` 不能进稳定像素基线。")
    else:
        print("⇒ 采集动作不写审计日志；`/audit` 的漂移另有原因。")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
