"""P0-15 收口项端到端验证：通知实时推送。

## 为什么必须跑「真实 uvicorn + 真实 WebSocket over TCP」

单测（`tests/test_notification_push.py`）用假连接覆盖了注册表与提交桥的分支，
但它证明不了三件**只在真实链路上才成立**的事：

  1. **协议层真的通**：握手、鉴权、首帧 `connected`、心跳 `ping/pong`、
     关闭码（1008/1001/4429）——这些是 WebSocket 协议语义，假连接完全绕过了。
  2. **推送与业务事务真的在同一个事件循环里**：`after_commit` 是同步回调，
     它调度的协程必须落在**持有该 WebSocket 的那个循环**上。
     若不在同一个循环，`send_json` 会跨循环调用而报错——而这恰恰是最容易
     写错、也最难在单测里发现的地方（假连接不绑定循环）。
  3. **整条业务链真的会推**：HTTP `POST /dispatches/{id}/accept`
     → `DispatchService.accept()` → `notify()` → `flush` → `commit`
     → `after_commit` → `ConnectionManager` → 真实 WS 帧。
     这是门槛一出口标准（咨询→派单→接单→AI 摘要→律师确认）里
     「接单」那一步的消息触达。

## 为什么用真 uvicorn 而不是 `TestClient.websocket_connect`

`TestClient` 把应用跑在**另一个线程的 portal 循环**里，而脚本主体在调用线程。
要触发推送就得在「服务端的那个循环」里提交事务，`TestClient` 不暴露该循环。
真 uvicorn 则可以把它的循环捕获下来（`startup` 钩子），再用
`run_coroutine_threadsafe` 把「提交 / 回滚」投递到**正确的循环**上执行——
这与生产时序完全一致。

## 覆盖范围

鉴权（缺 token / 伪造 token）· 首帧未读快照且与 REST 一致 · 真实接单触发的
双人推送（客户收 CASE_ACCEPTED、律师收 DISPATCH_CREATED）· 推送帧与 REST
同形 · 同租户第三方零推送 · 送达时延 · 回滚不推（幽灵通知防线）·
单用户连接上限 · 心跳保活 · 空闲回收 · 7 个推送指标。
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading
import time
import uuid

# 每次运行使用**独立库文件**，而不是先删旧文件再建（避免触发沙箱批量删除守卫）。
DB = f"./storage/verify_p0_15_push_{uuid.uuid4().hex[:8]}.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ["DATABASE_URL_SYNC"] = f"sqlite:///{DB}"
os.environ["ENVIRONMENT"] = "development"
os.environ["SECRET_KEY"] = "verify-only-local-strong-secret-key-32chars"
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ["DEBUG"] = "false"
os.environ["SQL_ECHO"] = "false"

PW = "pw12345678"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'[PASS]' if ok else '[FAIL]'} {name}" + (f"  -> {detail}" if detail else ""))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _close_code(exc: BaseException) -> int | None:
    """从 `ConnectionClosed` 中取出关闭码（跨 websockets 版本稳妥些）。"""
    code = getattr(exc, "code", None)
    if code is not None:
        return int(code)
    rcvd = getattr(exc, "rcvd", None)
    if rcvd is not None and getattr(rcvd, "code", None) is not None:
        return int(rcvd.code)
    return None


def _metric(text: str, name: str, **labels: str) -> float | None:
    """从 Prometheus 文本中取某个样本值（标签需全部匹配）。"""
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        sample, _, value = line.partition(" ")
        if "{" in sample:
            base, _, raw = sample.partition("{")
            raw = raw[:-1] if raw.endswith("}") else raw
            pairs = {}
            for part in raw.split(","):
                k, _, v = part.partition("=")
                pairs[k.strip()] = v.strip().strip('"')
        else:
            base, pairs = sample, {}
        if base == name and all(pairs.get(k) == v for k, v in labels.items()):
            try:
                return float(value)
            except ValueError:
                return None
    return None


def main() -> int:  # noqa: C901  验证脚本按阶段线性展开，便于对照 PRD 逐条核对
    import httpx
    import uvicorn
    import websockets

    from app.main import app

    # ── 启动真实服务 ────────────────────────────────────────────────────
    port = _free_port()
    server_loops: list[asyncio.AbstractEventLoop] = []

    async def _capture_loop() -> None:
        server_loops.append(asyncio.get_running_loop())

    # 用**包装 lifespan** 而不是 `app.router.on_startup.append(...)`：
    # `create_app()` 显式传了 `lifespan=`，此时 Starlette 走的是
    # `lifespan_context` 分支，`on_startup` 处理器**根本不会被调用**
    # （挂上去不报错、也不生效——典型的静默失效）。
    # 包装 `lifespan_context` 对两种装配方式都成立。
    from contextlib import asynccontextmanager

    _original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def _lifespan_with_capture(app_):
        async with _original_lifespan(app_) as state:
            await _capture_loop()
            yield state

    app.router.lifespan_context = _lifespan_with_capture

    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="error", access_log=False
        )
    )
    threading.Thread(target=server.run, daemon=True, name="verify-uvicorn").start()

    base = f"http://127.0.0.1:{port}"
    ws_base = f"ws://127.0.0.1:{port}/api/v1/ws/notifications"

    deadline = time.time() + 90
    ready = False
    while time.time() < deadline:
        try:
            if httpx.get(f"{base}/api/health/livez", timeout=1.0).status_code == 200:
                ready = True
                break
        except Exception:  # noqa: BLE001  启动期连接被拒是正常的
            pass
        time.sleep(0.2)
    check("0.1 真实服务在 90s 内就绪", ready, f"port={port}")
    if not ready:
        return _summary()

    check("0.2 服务端事件循环已被捕获（供跨线程投递事务）", bool(server_loops))
    if not server_loops:
        return _summary()
    loop = server_loops[0]

    def in_server(coro, timeout: float = 30.0):
        """把协程投递到**服务端事件循环**执行（与生产时序一致）。"""
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=timeout)

    # ── 账号与夹具数据 ──────────────────────────────────────────────────
    from sqlalchemy import func, select, text

    from app.config import settings
    from app.core.rbac import Role
    from app.database import async_session_factory
    from app.models.case import Case, Dispatch
    from app.models.enums import (
        CaseGrade,
        CaseStatus,
        DispatchMode,
        DispatchStatus,
        NotificationType,
    )
    from app.models.identity import Tenant, User
    from app.models.notification import Notification

    async def _ensure_default_tenant() -> str:
        """`init_db()` **不会**创建默认租户，而注册接口要求它存在。

        不补这一步的后果是注册返回 409 `TENANT_REQUIRED`
        （`auth_service.register` 的租户存在性校验），而报错文案
        「租户 platform 不存在」与「注册」这个动作看起来毫不相干——
        排查会先怀疑用户名冲突（同为 409 CONFLICT 族）。
        """
        async with async_session_factory() as s:
            tid = settings.DEFAULT_TENANT_ID
            found = (
                await s.execute(select(Tenant).where(Tenant.tenant_id == tid))
            ).scalars().first()
            if not found:
                s.add(Tenant(tenant_id=tid, name="默认租户"))
                await s.commit()
            return tid

    default_tenant = in_server(_ensure_default_tenant())
    check("0.3 默认租户已就绪（注册的前置条件）", bool(default_tenant),
          f"tenant={default_tenant}")

    # `trust_env=False`：本机环境设有 HTTP_PROXY/HTTPS_PROXY，httpx 默认会
    # 把 **127.0.0.1 的请求也发给代理**，导致注册/登录静默失败（返回非 2xx
    # 而我当时没查状态码，表现为「四个账号都拿不到 token」）。
    # 直连本地服务必须显式忽略代理环境变量。
    http = httpx.Client(base_url=base, timeout=20.0, trust_env=False)
    reg_status: dict[str, int] = {}
    for u in ("p015p_client", "p015p_lawyer", "p015p_other", "p015p_cap"):
        reg_status[u] = http.post(
            "/api/v1/auth/register", json={"username": u, "password": PW}
        ).status_code

    def _login(name: str) -> tuple[str, int]:
        r = http.post("/api/v1/auth/login", json={"username": name, "password": PW})
        data = (r.json() or {}).get("data", {}) or {}
        return (
            data.get("access_token") or "",
            int((data.get("user") or {}).get("id") or 0),
        )

    tok_client, uid_client = _login("p015p_client")
    tok_lawyer, uid_lawyer = _login("p015p_lawyer")
    tok_other, uid_other = _login("p015p_other")
    tok_cap, uid_cap = _login("p015p_cap")
    check(
        "0.4 四个账号可注册并登录",
        all([tok_client, tok_lawyer, tok_other, tok_cap]),
        f"register={reg_status} ids={uid_client},{uid_lawyer},{uid_other},{uid_cap}",
    )

    async def _seed():
        """把 lawyer 提为 LAWYER + 造「待接单」案件与派单。

        派单是**真实业务行**，不是给推送特意准备的旁路；接单走真实 HTTP 端点。
        """
        async with async_session_factory() as s:
            existing = (
                await s.execute(
                    select(User).where(User.username == "p015p_lawyer")
                )
            ).scalars().first()
            if existing is not None:
                existing.role = Role.LAWYER
            case = Case(
                case_no=f"P015P-{uuid.uuid4().hex[:8].upper()}",
                tenant_id=settings.DEFAULT_TENANT_ID,
                title="张某诉李某买卖合同纠纷",
                status=CaseStatus.PENDING_DISPATCH,
                grade=CaseGrade.B,
                client_user_id=uid_client,
            )
            s.add(case)
            await s.flush()
            disp = Dispatch(
                tenant_id=settings.DEFAULT_TENANT_ID,
                case_id=case.id,
                mode=DispatchMode.POOL,
                status=DispatchStatus.PENDING,
            )
            s.add(disp)
            await s.commit()
            return case.id, disp.id

    case_id, dispatch_id = in_server(_seed())
    check("0.5 夹具：案件与待接单派单已落库", case_id > 0 and dispatch_id > 0,
          f"case={case_id} dispatch={dispatch_id}")

    async def _seed_client_unread():
        """给客户预置 2 条未读，让「首帧未读快照」的断言**非平凡**。

        若连上时未读数为 0，那 `ws=0 == rest=0` 无论如何都会通过，
        这条断言就退化成了「字段存在性检查」——测不出快照是否真的取对了值。
        """
        from app.services.notification_service import notify

        async with async_session_factory() as s:
            for i in (1, 2):
                await notify(
                    s,
                    tenant_id=settings.DEFAULT_TENANT_ID,
                    user_id=uid_client,
                    type=NotificationType.REVIEW_REQUIRED,
                    content=f"预置未读 {i}",
                    ref_type="case",
                    ref_id=case_id,
                )
            await s.commit()

    in_server(_seed_client_unread())

    # ═══════════════════════ A. 协议与鉴权 ═══════════════════════

    async def _connect_closed(url: str, timeout: float = 6.0):
        """连上去直到被服务端关闭，返回 (关闭码, 收到的帧列表)。"""
        frames: list[dict] = []
        try:
            async with websockets.connect(url) as ws:
                try:
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                        try:
                            frames.append(json.loads(raw))
                        except (json.JSONDecodeError, TypeError):
                            pass
                except websockets.exceptions.ConnectionClosed as exc:
                    return _close_code(exc), frames
        except Exception:  # noqa: BLE001  握手即失败
            return None, frames
        return None, frames

    code_none, _ = asyncio.run(_connect_closed(ws_base))
    check("A.1 缺少 token → 以 1008 拒绝", code_none == 1008, f"close_code={code_none}")

    code_bad, _ = asyncio.run(_connect_closed(f"{ws_base}?token=not-a-jwt"))
    check("A.2 伪造 token → 以 1008 拒绝", code_bad == 1008, f"close_code={code_bad}")


    async def _run_phases() -> dict:
        """所有需要保持连接的阶段（B~E）在同一个事件循环内完成。"""
        out: dict = {}
        ac = httpx.AsyncClient(base_url=base, timeout=20.0, trust_env=False)

        # ── A.3 首帧未读快照 ──────────────────────────────────────────
        # REST 未读数必须与建连**同一时刻**取，否则后面 B 阶段接单产生的
        # 通知会让「快照 vs 现在」变成跨时间的比较——快照取值是对的，
        # 却会因时序不同而误判为不一致（初版即因此假红）。
        rest_unread = (
            await ac.get(
                "/api/v1/notifications/unread-count",
                headers={"Authorization": f"Bearer {tok_client}"},
            )
        ).json()["data"]["total"]
        ws_client = await websockets.connect(f"{ws_base}?token={tok_client}")
        first = json.loads(await asyncio.wait_for(ws_client.recv(), timeout=6.0))
        out["first_frame"] = first
        out["rest_unread_at_connect"] = rest_unread

        ws_lawyer = await websockets.connect(f"{ws_base}?token={tok_lawyer}")
        await asyncio.wait_for(ws_lawyer.recv(), timeout=6.0)  # 吃掉 connected

        ws_other = await websockets.connect(f"{ws_base}?token={tok_other}")
        await asyncio.wait_for(ws_other.recv(), timeout=6.0)

        # ── B. 真实 HTTP 接单 → 双人推送 ─────────────────────────────
        t0 = time.monotonic()
        r = await ac.post(
            f"/api/v1/dispatches/{dispatch_id}/accept",
            headers={"Authorization": f"Bearer {tok_lawyer}"},
        )
        out["accept_status"] = r.status_code

        async def _next_notification(ws, timeout=6.0):
            """读到一条 notification 帧（跳过 ping 等其他帧）。"""
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                try:
                    raw = await asyncio.wait_for(
                        ws.recv(), timeout=max(0.05, end - time.monotonic())
                    )
                except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
                    return None
                try:
                    frame = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(frame, dict) and frame.get("type") == "notification":
                    return frame
            return None

        out["client_frame"] = await _next_notification(ws_client)
        out["latency_ms"] = (time.monotonic() - t0) * 1000.0
        out["lawyer_frame"] = await _next_notification(ws_lawyer)

        # 第三方（同租户、未参与）在 1.2s 内必须一条都收不到
        out["other_frame"] = await _next_notification(ws_other, timeout=1.2)

        # ── C. 回滚不推（幽灵通知防线） ───────────────────────────────
        async def _notify_then_rollback():
            from app.services.notification_service import notify

            async with async_session_factory() as s:
                await notify(
                    s,
                    tenant_id=settings.DEFAULT_TENANT_ID,
                    user_id=uid_client,
                    type=NotificationType.CASE_ARCHIVED,
                    content="这条通知会随事务回滚，绝不能被推送",
                    ref_type="case",
                    ref_id=case_id,
                )
                await s.rollback()

        fut = asyncio.run_coroutine_threadsafe(_notify_then_rollback(), loop)
        # `wrap_future` 而不是 `fut.result()`：后者是阻塞调用，会把本事件循环
        # 卡死，连带让 `ws_client.recv()` 无法推进，回滚测试就测不出东西。
        out["rollback_ok"] = await asyncio.wrap_future(fut)
        out["ghost_frame"] = await _next_notification(ws_client, timeout=1.2)

        # ── D. 单用户连接上限 ─────────────────────────────────────────
        cap_url = f"{ws_base}?token={tok_cap}"
        held = []
        for _ in range(5):
            sock = await websockets.connect(cap_url)
            await asyncio.wait_for(sock.recv(), timeout=6.0)  # connected
            held.append(sock)
        code_over, frames_over = await _connect_closed(cap_url)
        out["cap_close_code"] = code_over
        out["cap_error_frame"] = frames_over[0] if frames_over else None
        for sock in held:
            await sock.close()

        # ── E. 心跳与空闲回收（缩短参数以便在秒级内观测） ─────────────
        import app.api.v1.ws as ws_module

        ws_module.HEARTBEAT_INTERVAL = 0.4
        ws_module.IDLE_TIMEOUT = 1.0

        hb = await websockets.connect(f"{ws_base}?token={tok_other}")
        await asyncio.wait_for(hb.recv(), timeout=6.0)  # connected
        ping = json.loads(await asyncio.wait_for(hb.recv(), timeout=4.0))
        out["ping_frame"] = ping
        # 回 pong 保活：连续响应若干轮后连接必须仍然可用
        alive = True
        try:
            for _ in range(5):
                await hb.send("pong")
                await asyncio.wait_for(hb.recv(), timeout=4.0)  # 下一个 ping
        except Exception:  # noqa: BLE001
            alive = False
        out["pong_keeps_alive"] = alive
        await hb.close()

        # 完全不响应 → 空闲超时回收（1001）
        code_idle, _ = await _connect_closed(
            f"{ws_base}?token={tok_other}", timeout=4.0
        )
        out["idle_close_code"] = code_idle

        for ws in (ws_client, ws_lawyer, ws_other):
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
        await ac.aclose()
        return out

    ph = asyncio.run(_run_phases())

    first = ph["first_frame"]
    check(
        "A.3 首帧为 connected 且携带未读快照",
        first.get("type") == "connected"
        and isinstance(first.get("data", {}).get("unread_total"), int)
        and int(first["data"].get("latest_id", -1)) >= 0,
        f"frame={first}",
    )

    # 与建连同一时刻的 REST 值比对（非零，故断言非平凡）
    rest_at_connect = ph["rest_unread_at_connect"]
    ws_unread = first.get("data", {}).get("unread_total")
    check(
        "A.4 首帧快照与「建连同一时刻」的 REST 未读数一致（前端可省一次往返）",
        ws_unread == rest_at_connect and rest_at_connect > 0,
        f"ws={ws_unread} rest@connect={rest_at_connect}",
    )

    # ═══════════════════════ B. 推送链路 ═══════════════════════
    check("B.1 律师经真实 HTTP 接单成功", ph["accept_status"] == 200,
          f"status={ph['accept_status']}")

    cf = ph["client_frame"]
    check("B.2 客户通过 WebSocket 收到推送", cf is not None,
          f"frame={cf}")
    if cf:
        data = cf.get("data", {}) or {}
        check("B.3 推送类型为 CASE_ACCEPTED（接单通知客户）",
              data.get("type") == "CASE_ACCEPTED", f"type={data.get('type')}")
        check(
            "B.4 推送帧与 REST 契约同形（前端只需一套解析）",
            {"id", "type", "title", "content", "ref_type", "ref_id", "is_read"}
            <= set(data),
            f"keys={sorted(data.keys())}",
        )
        check(
            "B.5 is_read 收敛为布尔且 ref 指向该案件",
            data.get("is_read") is False and data.get("ref_type") == "case"
            and int(data.get("ref_id") or 0) == case_id,
            f"is_read={data.get('is_read')!r} ref={data.get('ref_type')}/{data.get('ref_id')}",
        )
        # 与 NotificationOut 反序列化对账：字段漂移会在这里炸
        from app.schemas.notification import NotificationOut

        try:
            NotificationOut.model_validate(data)
            check("B.6 推送帧可反序列化为 NotificationOut", True)
        except Exception as exc:  # noqa: BLE001
            check("B.6 推送帧可反序列化为 NotificationOut", False, repr(exc))

    check(
        "B.7 推送时延 < 2s（落库→送达，实时性达标）",
        ph["latency_ms"] < 2000,
        f"{ph['latency_ms']:.0f}ms",
    )

    lf = ph["lawyer_frame"]
    check(
        "B.8 接单律师同时收到 DISPATCH_CREATED（派单侧通知）",
        lf is not None and (lf.get("data", {}) or {}).get("type") == "DISPATCH_CREATED",
        f"type={(lf or {}).get('data', {}).get('type')}",
    )

    check(
        "B.9 同租户未参与用户**零推送**（用户级隔离）",
        ph["other_frame"] is None,
        f"frame={ph['other_frame']}",
    )

    # ═══════════════════════ C. 幽灵通知防线 ═══════════════════════
    check("C.1 回滚事务未向客户端推送任何通知（幽灵通知防线）",
          ph["ghost_frame"] is None, f"frame={ph['ghost_frame']}")

    async def _count_archived() -> int:
        async with async_session_factory() as s:
            return int(
                (
                    await s.execute(
                        select(func.count()).select_from(Notification).where(
                            Notification.user_id == uid_client,
                            Notification.content.like("%随事务回滚%"),
                        )
                    )
                ).scalar_one()
            )

    check("C.2 该通知确实未落库（证明「不推」是对的）",
          in_server(_count_archived()) == 0)

    # ═══════════════════════ D. 连接上限 ═══════════════════════
    check("D.1 第 6 个连接被拒并回 4429（服务端内存有界）",
          ph["cap_close_code"] == 4429, f"close_code={ph['cap_close_code']}")
    check(
        "D.2 被拒前明确告知原因（而非静默断开）",
        (ph["cap_error_frame"] or {}).get("type") == "error",
        f"frame={ph['cap_error_frame']}",
    )

    # ═══════════════════════ E. 心跳与空闲回收 ═══════════════════════
    check("E.1 服务端按间隔主动发 ping（穿透 NAT 空闲回收）",
          (ph["ping_frame"] or {}).get("type") == "ping",
          f"frame={ph['ping_frame']}")
    check("E.2 客户端回 pong 可持续保活（不误断）",
          ph["pong_keeps_alive"] is True)
    check("E.3 「假活」连接空闲超时后以 1001 回收（客户端据此重连）",
          ph["idle_close_code"] == 1001, f"close_code={ph['idle_close_code']}")

    # ═══════════════════════ F. 指标（NP-12） ═══════════════════════
    body = http.get("/metrics").text
    for name in (
        "nlaw_ws_connections_active",
        "nlaw_ws_connections_total",
        "nlaw_ws_connection_rejected_total",
        "nlaw_ws_auth_failed_total",
        "nlaw_ws_heartbeat_timeout_total",
        "nlaw_notification_push_total",
        "nlaw_notification_push_latency_milliseconds",
    ):
        check(f"F.1 指标 {name} 已导出", f"# TYPE {name} " in body)

    delivered = _metric(body, "nlaw_notification_push_total", result="delivered")
    rolled = _metric(body, "nlaw_notification_push_total", result="rolled_back")
    failed = _metric(body, "nlaw_notification_push_total", result="failed")
    check("F.2 计入 delivered（≥2：客户 + 律师各一条）",
          (delivered or 0) >= 2, f"delivered={delivered}")
    check("F.3 计入 rolled_back（幽灵通知被拦下的活体证据）",
          (rolled or 0) >= 1, f"rolled_back={rolled}")
    check("F.4 failed 未被误计（本次无发送失败）",
          (failed or 0) == 0, f"failed={failed}")
    check(
        "F.5 鉴权失败按原因区分",
        (_metric(body, "nlaw_ws_auth_failed_total", reason="missing_token") or 0) >= 1
        and (_metric(body, "nlaw_ws_auth_failed_total", reason="invalid_token") or 0) >= 1,
        f"missing={_metric(body, 'nlaw_ws_auth_failed_total', reason='missing_token')} "
        f"invalid={_metric(body, 'nlaw_ws_auth_failed_total', reason='invalid_token')}",
    )
    check(
        "F.6 连接建立与拒绝分别计数",
        (_metric(body, "nlaw_ws_connections_total", endpoint="notifications") or 0) >= 5
        and (
            _metric(body, "nlaw_ws_connection_rejected_total", reason="over_limit") or 0
        ) >= 1,
        f"total={_metric(body, 'nlaw_ws_connections_total', endpoint='notifications')} "
        f"rejected={_metric(body, 'nlaw_ws_connection_rejected_total', reason='over_limit')}",
    )
    check(
        "F.7 空闲回收被计数",
        (_metric(body, "nlaw_ws_heartbeat_timeout_total") or 0) >= 1,
        f"timeouts={_metric(body, 'nlaw_ws_heartbeat_timeout_total')}",
    )
    check(
        "F.8 推送时延已观测（含失败/回滚路径，否则 P99 会偏小）",
        (_metric(body, "nlaw_notification_push_latency_milliseconds_count") or 0) >= 2,
        f"count={_metric(body, 'nlaw_notification_push_latency_milliseconds_count')}",
    )
    check(
        "F.9 active 连接数已归零（指标与真实连接数不脱钩）",
        (_metric(body, "nlaw_ws_connections_active", endpoint="notifications") or 0) == 0,
        f"active={_metric(body, 'nlaw_ws_connections_active', endpoint='notifications')}",
    )

    # ═══════════════════════ G. 索引仍在位（回归） ═══════════════════════
    async def _indexes() -> set[str]:
        async with async_session_factory() as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='index' "
                        "AND tbl_name='notifications'"
                    )
                )
            ).all()
            return {r[0] for r in rows}

    idx = in_server(_indexes())
    check(
        "G.1 通知复合索引仍在位（本轮改动未回退第一轮成果）",
        {"ix_notifications_tenant_user_id", "ix_notifications_tenant_user_read"} <= idx,
        f"indexes={sorted(idx)}",
    )

    return _summary()


def _summary() -> int:
    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\n{'=' * 66}")
    print(f"P0-15 通知实时推送验证：{passed} 通过 / {failed} 失败")
    print("=" * 66)
    if failed:
        for name, ok, detail in results:
            if not ok:
                print(f"  [FAIL] {name}  -> {detail}")
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
