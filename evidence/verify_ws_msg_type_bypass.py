"""WS 写路径绕过 schema 校验：`msg_type` 可被任意字符串污染（端到端复现）。

## 发现路径

枚举漂移扫描（`verify_enum_domain_drift.py`）**看不到这条路径** ——
它只扫「Pydantic 请求体」。而 `app/api/v1/ws.py:309` 是从**裸 dict** 取值的：

    payload = json.loads(raw)                    # WS 帧，无 schema
    ...
    Message(..., msg_type=payload.get("msg_type", "text"), ...)

中间**没有任何校验**。这是本次修复里 `SendMessageRequest.msg_type` 的
**同形状漏洞**，只是换了一条入口 —— 收紧 REST 侧并不会关上它。

> 这也是一条关于**扫描器边界**的证据：轴 B 的判据是「请求体 schema 的类型」，
> 而裸 dict 路径上根本没有 schema 可查。**工具没报，不等于没有。**

## 后果

落一条 `msg_type='BOGUS'` ⇒ `select(Message)`（`conversations.py:92`，会话详情）
在**结果物化阶段**抛 `LookupError` ⇒ `GET /api/v1/conversations/{id}` **永久 500**。

## 本脚本

真实 WS 帧端到端复现，并在结束时**清理现场**（删掉污染行与探针会话），
最后再断言一次「清理后接口恢复 200」—— 证明 500 确实由那一行造成。

用法：
    python evidence/verify_ws_msg_type_bypass.py
    python evidence/verify_ws_msg_type_bypass.py --keep   # 保留现场（调试用）

退出码（与项目其它证据工具同口径：`0` 通过 / `1` 产品缺陷 / `2` 环境问题）：
    `0` 已加固 —— WS 帧被**显式拒绝**，越界值未落库
    `1` **产品缺陷** —— 越界值落库（且会话详情 500）
    `2` 环境问题 —— 连不上 / 建会话失败 / 没看到显式拒绝 ⇒ 结论不可信
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sqlite3
import sys
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).parent
BACKEND_DIR = HERE.parents[0] / "backend"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(BACKEND_DIR))

from envprobe import (  # noqa: E402
    ADMIN,
    resolve_live_db,
)
from envprobe import BACKEND as API  # noqa: E402

# 🚨 **不写死库路径**（2026-09-25 修）：此前是 `BACKEND_DIR / "demo_8001.db"`。
#    编排器（`run_browser_job.py`）用**隔离临时库**起后端时，写死的路径会让
#    `stored_msg_types()` 读到**空列表** ⇒ 走到「越界值未落库」分支 ⇒
#    **把「产品有洞（rc=1）」误报成「环境问题（rc=2）」**。
#    详见 `envprobe.resolve_live_db()` 的长注释。
TENANT = "firm_hlw"
BOGUS = "BOGUS"  # 不在 MessageType 里
PROBE_TEXT = "ws-probe-msg-type-bypass"


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def login(username: str) -> str:
    from app.seed.data import DEMO_USERS

    u = next(x for x in DEMO_USERS if x["username"] == username)
    body = json.dumps({"username": u["username"], "password": u["password"]}).encode()
    req = urllib.request.Request(
        f"{API}/api/v1/auth/login",
        data=body,
        headers={"Content-Type": "application/json", "Origin": ADMIN},
    )
    with _opener().open(req, timeout=8.0) as r:  # noqa: S310
        return json.loads(r.read().decode())["data"]["access_token"]


def call(method: str, path: str, tok: str, payload: dict | None = None) -> tuple[int, dict | str]:
    """返回 (状态码, 解析后的 body 或错误串)。**不抛异常** —— 500 是要断言的观测值。"""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {tok}",
            "X-Tenant-Id": TENANT,
            "Origin": ADMIN,
            "Content-Type": "application/json",
        },
    )
    try:
        with _opener().open(req, timeout=8.0) as r:  # noqa: S310
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:200]


async def send_bogus_frame(conv_id: int, tok: str) -> str:
    """连上 WS 发一帧越界 `msg_type`。返回服务端回帧（或异常说明）。"""
    import websockets

    url = f"ws://127.0.0.1:8001/api/v1/ws/conversations/{conv_id}?token={tok}"
    frame = json.dumps({"text": PROBE_TEXT, "msg_type": BOGUS})
    try:
        async with websockets.connect(url, proxy=None, open_timeout=8) as ws:
            await ws.send(frame)
            try:
                return await asyncio.wait_for(ws.recv(), timeout=6)
            except asyncio.TimeoutError:
                return "(无回帧)"
    except TypeError:
        # 老版本 websockets 没有 proxy 参数
        async with websockets.connect(url, open_timeout=8) as ws:
            await ws.send(frame)
            try:
                return await asyncio.wait_for(ws.recv(), timeout=6)
            except asyncio.TimeoutError:
                return "(无回帧)"


def stored_msg_types(conv_id: int) -> list:
    db = resolve_live_db()
    if db is None:
        return []
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "select id, msg_type, sender, content from messages where conversation_id=?",
            (conv_id,),
        ).fetchall()
    finally:
        con.close()


def cleanup(conv_id: int) -> None:
    db = resolve_live_db()
    if db is None:
        return
    con = sqlite3.connect(db)
    try:
        con.execute("delete from messages where conversation_id=?", (conv_id,))
        con.execute("delete from conversations where id=?", (conv_id,))
        con.commit()
    finally:
        con.close()


async def main() -> int:
    ap = argparse.ArgumentParser(description="WS msg_type 越界写入端到端复现")
    ap.add_argument("--keep", action="store_true", help="保留现场（不清理）")
    args = ap.parse_args()

    db = resolve_live_db()
    if db is None:
        print("[ENV ] 找不到运行库 —— `DATABASE_URL` 没指向可读的 SQLite，"
              "且常见候选路径都不存在")
        return 2

    print("── WS `msg_type` 越界写入复现 ──\n")
    print(f"  运行库：{db}")

    # ① 客户建会话（client_user_id 由端点设为客户本人）
    try:
        client_tok = login("client")
        lawyer_tok = login("lawyer_wang")
    except Exception as e:  # noqa: BLE001
        print(f"[ENV ] 登录失败：{type(e).__name__}: {e}")
        return 2

    code, body = call(
        "POST", "/api/v1/conversations", client_tok,
        {"external_user_id": f"ws-probe-{asyncio.get_event_loop().time():.0f}"},
    )
    if code != 200:
        print(f"[ENV ] 建会话失败 HTTP {code}: {body}")
        return 2
    conv_id = body["data"]["id"]
    print(f"  会话已建：id={conv_id}（租户 {TENANT}）")

    # ② 基线：详情接口此刻是好的
    code0, _ = call("GET", f"/api/v1/conversations/{conv_id}", lawyer_tok)
    print(f"  ① 注入前  GET /conversations/{conv_id}  →  HTTP {code0}")
    if code0 != 200:
        print("[ENV ] 注入前接口就不是 200，前提不成立")
        if not args.keep:
            cleanup(conv_id)
        return 2

    # ③ 用**律师**身份连 WS：`sender=LAWYER` ⇒ 不走会话引擎 ⇒ 直接 commit，
    #    污染行不会被引擎异常回滚掉。
    reply = await send_bogus_frame(conv_id, lawyer_tok)
    print(f"  ② 已发 WS 帧：{{\"text\": \"{PROBE_TEXT}\", \"msg_type\": \"{BOGUS}\"}}")
    print(f"     服务端回帧：{reply!r}")

    rows = stored_msg_types(conv_id)
    print(f"  ③ 落库的消息：{rows}")
    poisoned = [r for r in rows if r[1] == BOGUS]
    if not poisoned:
        # 区分「已加固」与「根本没测成」——只留一个出口会把环境问题报成修复
        if "未知消息类型" in str(reply):
            print("\n=> 退出码 0：**已加固** —— WS 帧被显式拒绝（回帧含「未知消息类型」），")
            print("   越界值未落库。")
            if not args.keep:
                cleanup(conv_id)
            return 0
        print(f"\n=> 退出码 2：越界值未落库，但**没看到显式拒绝**（回帧 {reply!r}）")
        print("   ⇒ 可能是连接失败或服务端行为变了，结论不可信。")
        if not args.keep:
            cleanup(conv_id)
        return 2

    # ④ 后果：详情接口 500
    code1, err = call("GET", f"/api/v1/conversations/{conv_id}", lawyer_tok)
    print(f"  ④ 注入后  GET /conversations/{conv_id}  →  HTTP {code1}")
    if code1 == 500:
        print(f"     响应体（截断）：{str(err)[:120]}")

    # ⑤ 清理现场，并断言接口恢复 —— 证明 500 确实由那一行造成
    if args.keep:
        print("\n  [--keep] 保留现场，未清理")
        return 1 if code1 == 500 else 2
    cleanup(conv_id)
    code2, _ = call("GET", f"/api/v1/conversations/{conv_id}", lawyer_tok)
    print(f"  ⑤ 清理后  GET /conversations/{conv_id}  →  HTTP {code2}（会话已删，404 属正常）")
    print(f"     剩余消息：{stored_msg_types(conv_id)}")

    print("\n" + "=" * 72)
    if code1 == 500:
        print("=> 退出码 1：**产品缺陷** —— WS 裸 dict 路径可写入越界 `msg_type`，")
        print("   并让会话详情接口对该会话永久 500。")
        return 1
    print(f"=> 退出码 2：越界值落了库，但详情接口仍为 HTTP {code1}（后果与预期不符，需复查）")
    return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
