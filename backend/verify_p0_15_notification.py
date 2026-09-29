"""P0-15 端到端验证：通知读路径（真实 HTTP + 真实 Cookie 罐 + 真实 DB）。

## 为什么单测过了还要再走一遍 E2E？

`tests/test_notification_api.py` 直接调用服务层函数，证明的是「给定
`tenant_id` + `user_id`，查询逻辑正确」。但线上是否隔离，还取决于三件
**只有在完整请求链路里才会暴露**的事：

  1. 路由是否真的挂了 `Depends(get_tenant_context)` —— 漏了就没有
     `user_id`，端点会退化成「谁都能读」或直接 500
  2. `user_id` 是否**只**取自 JWT，而不是某个可被请求参数覆盖的入口
     （若能传参指定，隔离形同虚设）
  3. 越权时返回的是 404 还是 403 —— 这决定攻击者能否枚举他人通知的存在性

因此本脚本用 TestClient 打真实请求，覆盖「同租户跨用户」「跨租户」
两条越权路径，并断言状态码。

## 覆盖范围
未认证拦截 / 列表归属 / 未读数与分组 / 单条越权 / 跨租户越权 /
标记已读幂等 / 批量跳过他人 id / 全部已读不波及其他用户 /
参数校验 / 分页结构 / OpenAPI 端点齐全 / user_id 不可被请求参数覆盖。
"""
from __future__ import annotations

import os
import sys
import uuid

# 每次运行使用**独立库文件**，而不是先删旧文件再建（避免触发沙箱批量删除守卫）。
DB = f"./storage/verify_p0_15_{uuid.uuid4().hex[:8]}.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ["DATABASE_URL_SYNC"] = f"sqlite:///{DB}"
os.environ["ENVIRONMENT"] = "development"
os.environ["SECRET_KEY"] = "verify-only-local-strong-secret-key-32chars"
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ["DEBUG"] = "false"
os.environ["SQL_ECHO"] = "false"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'[PASS]' if ok else '[FAIL]'} {name}" + (f"  -> {detail}" if detail else ""))


def main() -> int:
    import asyncio

    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app import models  # noqa: F401
    from app.config import settings
    from app.database import Base, async_session_factory, engine
    from app.main import app
    from app.models.enums import NotificationType
    from app.models.identity import Tenant, User
    from app.services.notification_service import notify

    OTHER_TENANT = "firm_p015"

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_session_factory() as s:
            for tid, name in ((settings.DEFAULT_TENANT_ID, "默认租户"), (OTHER_TENANT, "另一家律所")):
                found = (
                    await s.execute(select(Tenant).where(Tenant.tenant_id == tid))
                ).scalars().first()
                if not found:
                    s.add(Tenant(tenant_id=tid, name=name))
            await s.commit()

    asyncio.run(_setup())

    client = TestClient(app)
    PW = "pw12345678"

    # 注册：A、B 同属默认租户（同租户跨用户是最关键隔离面）；C 属另一租户
    for u in ("p015_a", "p015_b"):
        client.post("/api/v1/auth/register", json={"username": u, "password": PW})
    client.post(
        "/api/v1/auth/register",
        json={"username": "p015_c", "password": PW},
    )

    # C 需要在另一个租户 —— 注册接口固定落默认租户（防自助提权），
    # 故此处直接改库，模拟由 seed 脚本创建的跨租户账号。
    async def _move_c():
        async with async_session_factory() as s:
            u = (
                await s.execute(select(User).where(User.username == "p015_c"))
            ).scalars().first()
            u.tenant_id = OTHER_TENANT
            await s.commit()
            return u.id

    c_id = asyncio.run(_move_c())

    def _login(name: str) -> tuple[str, int]:
        r = client.post("/api/v1/auth/login", json={"username": name, "password": PW})
        data = (r.json() or {}).get("data", {}) or {}
        return data.get("access_token") or "", int((data.get("user") or {}).get("id") or 0)

    tok_a, uid_a = _login("p015_a")
    tok_b, uid_b = _login("p015_b")
    tok_c, _ = _login("p015_c")

    check("A.1 三个账号均可登录并拿到 access 令牌", all([tok_a, tok_b, tok_c]), f"ids={uid_a},{uid_b},{c_id}")

    def H(tok: str) -> dict:
        return {"Authorization": f"Bearer {tok}"}

    # ── 造数据：A 三条未读、B 两条未读、C 一条未读 ──────────────────────
    async def _seed():
        async with async_session_factory() as s:
            for _ in range(2):
                await notify(
                    s, tenant_id=settings.DEFAULT_TENANT_ID, user_id=uid_a,
                    type=NotificationType.DISPATCH_CREATED,
                    content="您有新案件待接：《张某诉李某合同纠纷》",
                    ref_type="case", ref_id=101,
                )
            await notify(
                s, tenant_id=settings.DEFAULT_TENANT_ID, user_id=uid_a,
                type=NotificationType.REVIEW_REQUIRED,
                content="有新的强制复核任务待处理",
                ref_type="analysis", ref_id=202,
            )
            for _ in range(2):
                await notify(
                    s, tenant_id=settings.DEFAULT_TENANT_ID, user_id=uid_b,
                    type=NotificationType.REVIEW_REQUIRED,
                    content="乙的强制复核任务",
                    ref_type="analysis", ref_id=303,
                )
            await notify(
                s, tenant_id=OTHER_TENANT, user_id=c_id,
                type=NotificationType.DISPATCH_CREATED,
                content="丙的派单提醒",
                ref_type="case", ref_id=404,
            )
            await s.commit()

    asyncio.run(_seed())

    # ═══════════════ B. 鉴权与基本读路径 ═══════════════
    r = client.get("/api/v1/notifications")
    check("B.1 未认证访问列表被拒 401", r.status_code == 401, f"status={r.status_code}")

    r = client.get("/api/v1/notifications", headers=H(tok_a))
    check("B.2 列表返回 200", r.status_code == 200, f"status={r.status_code}")
    page = (r.json() or {}).get("data", {}) or {}
    items = page.get("items") or []
    check("B.3 列表只含本人 3 条通知", len(items) == 3, f"len={len(items)}")
    check(
        "B.4 列表条目均带归属字段（不含他人内容）",
        all("乙的" not in (i.get("content") or "") and "丙的" not in (i.get("content") or "") for i in items),
        f"contents={[i.get('content') for i in items]}",
    )
    check("B.5 响应含分页结构（total/page/page_size/pages）", {"total", "page", "page_size", "pages"} <= set(page.keys()), f"keys={sorted(page.keys())}")
    check("B.6 列表内附带 unread_total（省一次往返）", page.get("unread_total") == 3, f"unread_total={page.get('unread_total')}")
    check("B.7 按 id 倒序（最新在前）", [i["id"] for i in items] == sorted([i["id"] for i in items], reverse=True))
    # 库里是 Integer(0/1)，契约必须是布尔——前端要直接做 `if (n.is_read)` 判断，
    # 拿到 0/1 会让 `!n.is_read` 之类的写法出现反直觉结果
    check(
        "B.8 is_read 序列化为真正的布尔值（非 0/1）",
        all(isinstance(i.get("is_read"), bool) for i in items),
        f"types={sorted({type(i.get('is_read')).__name__ for i in items})}",
    )

    # ═══════════════ C. 未读数 ═══════════════
    r = client.get("/api/v1/notifications/unread-count", headers=H(tok_a))
    unread = (r.json() or {}).get("data", {}) or {}
    check("C.1 未读数 200 且总数正确", r.status_code == 200 and unread.get("total") == 3, f"data={unread}")
    check(
        "C.2 未读数按类型分组正确",
        unread.get("by_type") == {"DISPATCH_CREATED": 2, "REVIEW_REQUIRED": 1},
        f"by_type={unread.get('by_type')}",
    )
    check("C.3 未读数含 latest_id（供轮询判新）", int(unread.get("latest_id") or 0) > 0, f"latest_id={unread.get('latest_id')}")
    check("C.4 未读数不含他人通知（B 的两条未计入）", unread.get("total") == 3, f"total={unread.get('total')}")

    # ═══════════════ D. 越权：同租户跨用户 / 跨租户 ═══════════════
    b_notif_id = None

    async def _b_notif_id():
        from app.models.notification import Notification

        async with async_session_factory() as s:
            row = (
                await s.execute(
                    select(Notification).where(Notification.user_id == uid_b).limit(1)
                )
            ).scalars().first()
            return row.id

    b_notif_id = asyncio.run(_b_notif_id())

    r = client.get(f"/api/v1/notifications/{b_notif_id}", headers=H(tok_a))
    check(
        "D.1 同租户跨用户读详情 → 404（不泄露存在性）",
        r.status_code == 404,
        f"status={r.status_code} body={r.text[:120]}",
    )

    r = client.post(f"/api/v1/notifications/{b_notif_id}/read", headers=H(tok_a))
    check(
        "D.2 同租户跨用户标记已读 → 404",
        r.status_code == 404,
        f"status={r.status_code}",
    )

    async def _b_unread() -> int:
        from sqlalchemy import func as _f

        from app.models.notification import Notification

        async with async_session_factory() as s:
            return int(
                (
                    await s.execute(
                        select(_f.count()).select_from(Notification).where(
                            Notification.user_id == uid_b, Notification.is_read == 0
                        )
                    )
                ).scalar_one()
            )

    check("D.3 越权标记未改动他人数据（B 仍 2 条未读）", asyncio.run(_b_unread()) == 2, f"unread={asyncio.run(_b_unread())}")

    # 跨租户：A 去读 C 的通知
    async def _c_notif_id():
        from app.models.notification import Notification

        async with async_session_factory() as s:
            row = (
                await s.execute(
                    select(Notification).where(Notification.user_id == c_id).limit(1)
                )
            ).scalars().first()
            return row.id

    c_notif_id = asyncio.run(_c_notif_id())
    r = client.get(f"/api/v1/notifications/{c_notif_id}", headers=H(tok_a))
    check("D.4 跨租户读详情 → 404", r.status_code == 404, f"status={r.status_code}")

    r = client.get("/api/v1/notifications", headers=H(tok_c))
    c_items = ((r.json() or {}).get("data", {}) or {}).get("items") or []
    check("D.5 另一租户只看到自己的 1 条", len(c_items) == 1, f"len={len(c_items)}")

    # ═══════════════ E. user_id 不可被请求参数覆盖 ═══════════════
    r = client.get(f"/api/v1/notifications?user_id={uid_b}&user_id={c_id}", headers=H(tok_a))
    page2 = (r.json() or {}).get("data", {}) or {}
    items2 = page2.get("items") or []
    check(
        "E.1 试图用查询参数指定 user_id 无效（仍只返回本人）",
        r.status_code == 200 and len(items2) == 3,
        f"status={r.status_code} len={len(items2)}",
    )
    check(
        "E.2 该请求未泄露他人通知内容",
        all("乙的" not in (i.get("content") or "") for i in items2),
    )

    # ═══════════════ F. 标记已读（幂等） ═══════════════
    a_first_id = items2[-1]["id"]  # 最早一条
    r = client.post(f"/api/v1/notifications/{a_first_id}/read", headers=H(tok_a))
    body = (r.json() or {}).get("data", {}) or {}
    check("F.1 标记单条已读 200", r.status_code == 200, f"status={r.status_code}")
    first_read_at = (body.get("notification") or {}).get("read_at")
    check("F.2 已读时间戳被写入", bool(first_read_at), f"read_at={first_read_at}")
    check("F.3 返回体附带最新未读数 2", body.get("unread_total") == 2, f"unread_total={body.get('unread_total')}")

    r = client.post(f"/api/v1/notifications/{a_first_id}/read", headers=H(tok_a))
    body2 = (r.json() or {}).get("data", {}) or {}
    check("F.4 重复标记仍 200（幂等）", r.status_code == 200, f"status={r.status_code}")
    check(
        "F.5 重复标记不刷新首次已读时间戳",
        (body2.get("notification") or {}).get("read_at") == first_read_at,
        f"first={first_read_at} second={(body2.get('notification') or {}).get('read_at')}",
    )
    check("F.6 重复标记后未读数仍为 2", body2.get("unread_total") == 2, f"unread_total={body2.get('unread_total')}")

    r = client.get("/api/v1/notifications?is_read=true", headers=H(tok_a))
    read_items = ((r.json() or {}).get("data", {}) or {}).get("items") or []
    check("F.7 is_read=true 筛选返回 1 条", len(read_items) == 1, f"len={len(read_items)}")

    r = client.get("/api/v1/notifications?is_read=false", headers=H(tok_a))
    unread_items = ((r.json() or {}).get("data", {}) or {}).get("items") or []
    check("F.8 is_read=false 筛选返回 2 条", len(unread_items) == 2, f"len={len(unread_items)}")

    r = client.get("/api/v1/notifications?type=REVIEW_REQUIRED", headers=H(tok_a))
    typed = ((r.json() or {}).get("data", {}) or {}).get("items") or []
    check("F.9 type 筛选生效（REVIEW_REQUIRED 1 条）", len(typed) == 1, f"len={len(typed)}")

    r = client.get("/api/v1/notifications?type=NOT_A_REAL_TYPE", headers=H(tok_a))
    check("F.10 未知 type → 400（不静默返回空列表）", r.status_code == 400, f"status={r.status_code}")

    # ═══════════════ G. 批量标记 ═══════════════
    unread_ids = [i["id"] for i in unread_items]
    r = client.post(
        "/api/v1/notifications/read",
        json={"ids": unread_ids + [b_notif_id, c_notif_id]},  # 混入他人 id
        headers=H(tok_a),
    )
    b = (r.json() or {}).get("data", {}) or {}
    check("G.1 批量标记只计入本人（2 条），他人 id 静默跳过", b.get("updated") == 2, f"data={b}")
    check("G.2 批量后未读数为 0", b.get("unread_total") == 0, f"unread_total={b.get('unread_total')}")

    r = client.post(
        "/api/v1/notifications/read",
        json={"ids": unread_ids},
        headers=H(tok_a),
    )
    b2 = (r.json() or {}).get("data", {}) or {}
    check("G.3 重复批量 updated=0（幂等）", b2.get("updated") == 0, f"data={b2}")

    check("G.4 他人未读未被波及（B 仍 2 条）", asyncio.run(_b_unread()) == 2, f"unread={asyncio.run(_b_unread())}")

    async def _c_unread() -> int:
        from sqlalchemy import func as _f

        from app.models.notification import Notification

        async with async_session_factory() as s:
            return int(
                (
                    await s.execute(
                        select(_f.count()).select_from(Notification).where(
                            Notification.user_id == c_id, Notification.is_read == 0
                        )
                    )
                ).scalar_one()
            )

    check("G.5 跨租户未读未被波及（C 仍 1 条）", asyncio.run(_c_unread()) == 1, f"unread={asyncio.run(_c_unread())}")

    # ═══════════════ H. 全部已读 ═══════════════
    r = client.post("/api/v1/notifications/read-all", headers=H(tok_b))
    hb = (r.json() or {}).get("data", {}) or {}
    check("H.1 B 全部已读 → updated=2", hb.get("updated") == 2, f"data={hb}")

    r = client.post("/api/v1/notifications/read-all", headers=H(tok_b))
    hb2 = (r.json() or {}).get("data", {}) or {}
    check("H.2 再次全部已读 → updated=0（幂等）", hb2.get("updated") == 0, f"data={hb2}")

    r = client.get("/api/v1/notifications/unread-count", headers=H(tok_a))
    ua = (r.json() or {}).get("data", {}) or {}
    check("H.3 B 的全部已读未影响 A（A 仍 0）", ua.get("total") == 0, f"total={ua.get('total')}")
    check("H.4 全部已读后 latest_id 归零", ua.get("latest_id") == 0, f"latest_id={ua.get('latest_id')}")

    check("H.5 C 的未读仍为 1（全部已读未跨租户）", asyncio.run(_c_unread()) == 1, f"unread={asyncio.run(_c_unread())}")

    # ═══════════════ I. OpenAPI 契约 ═══════════════
    spec = app.openapi()
    npaths = {p for p in spec["paths"] if "/notifications" in p}
    expected = {
        "/api/v1/notifications",
        "/api/v1/notifications/unread-count",
        "/api/v1/notifications/{notification_id}",
        "/api/v1/notifications/{notification_id}/read",
        "/api/v1/notifications/read",
        "/api/v1/notifications/read-all",
    }
    check("I.1 OpenAPI 暴露全部 6 个通知端点", expected <= npaths, f"missing={sorted(expected - npaths)}")

    # ═══════════════ J. 索引落地 ═══════════════
    async def _indexes() -> set[str]:
        from sqlalchemy import text as _t

        async with engine.begin() as conn:
            rows = (
                await conn.execute(_t("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='notifications'"))
            ).all()
            return {r[0] for r in rows}

    idx = asyncio.run(_indexes())
    check(
        "J.1 复合索引已随建表落地",
        {"ix_notifications_tenant_user_id", "ix_notifications_tenant_user_read"} <= idx,
        f"indexes={sorted(idx)}",
    )

    async def _plan() -> str:
        from sqlalchemy import text as _t

        async with engine.begin() as conn:
            rows = (
                await conn.execute(
                    _t(
                        "EXPLAIN QUERY PLAN SELECT * FROM notifications "
                        "WHERE tenant_id='t' AND user_id=1 ORDER BY id DESC LIMIT 20"
                    )
                )
            ).all()
            return " | ".join(str(r[-1]) for r in rows)

    plan = asyncio.run(_plan())
    check(
        "J.2 列表查询命中复合索引（无全表扫描）",
        "ix_notifications_tenant_user_id" in plan,
        plan[:140],
    )

    # ═══════════════ K. since_id 增量补拉（NP-17） ═══════════════
    # 前文 A 的 3 条通知已全部标记已读（未加 is_read 筛选时仍会返回）。
    page_all = (
        (client.get("/api/v1/notifications?page_size=100", headers=H(tok_a)).json() or {})
        .get("data", {})
        or {}
    )
    all_ids = [i["id"] for i in (page_all.get("items") or [])]
    max_id = max(all_ids)
    check("K.0 前置：A 有 3 条通知可供补拉", len(all_ids) == 3, f"ids={all_ids}")

    r = client.get(f"/api/v1/notifications?since_id={max_id}", headers=H(tok_a))
    kb = (r.json() or {}).get("data", {}) or {}
    check(
        "K.1 since_id=当前最大 id → 空列表（严格大于，不重复边界那条）",
        r.status_code == 200 and kb.get("items") == [],
        f"status={r.status_code} items={kb.get('items')}",
    )
    check(
        "K.2 响应回显 since_id 与 latest_id（供前端推进游标）",
        kb.get("since_id") == max_id and "latest_id" in kb,
        f"since_id={kb.get('since_id')} latest_id={kb.get('latest_id')}",
    )

    r = client.get("/api/v1/notifications?since_id=0", headers=H(tok_a))
    kb = (r.json() or {}).get("data", {}) or {}
    got_ids = [i["id"] for i in (kb.get("items") or [])]
    check(
        "K.3 since_id=0 返回全部且**升序**（补拉语义与常规列表相反）",
        got_ids == sorted(all_ids) and got_ids == sorted(got_ids),
        f"got={got_ids} expected={sorted(all_ids)}",
    )

    # offset 在 since_id 模式下必须被忽略：否则追赶时会因新写入而漂移
    r1 = client.get("/api/v1/notifications?since_id=0&page_size=2&page=1", headers=H(tok_a))
    r2 = client.get("/api/v1/notifications?since_id=0&page_size=2&page=2", headers=H(tok_a))
    ids1 = [i["id"] for i in ((r1.json() or {}).get("data", {}) or {}).get("items", [])]
    ids2 = [i["id"] for i in ((r2.json() or {}).get("data", {}) or {}).get("items", [])]
    check(
        "K.4 since_id 模式下 offset 被忽略（避免 offset 漂移）",
        ids1 == ids2 == sorted(all_ids)[:2],
        f"page1={ids1} page2={ids2}",
    )

    # 归属：B 带任何 since_id 都只能拿到自己的
    r = client.get("/api/v1/notifications?since_id=0&page_size=100", headers=H(tok_b))
    b_ids = [i["id"] for i in ((r.json() or {}).get("data", {}) or {}).get("items", [])]
    check(
        "K.5 补拉仍受 (tenant_id,user_id) 约束（B 拿不到 A 的）",
        not (set(b_ids) & set(all_ids)) and len(b_ids) == 2,
        f"b_ids={b_ids} a_ids={all_ids}",
    )

    # 用他人 id 作锚点探测：不得返回他人通知
    r = client.get(f"/api/v1/notifications?since_id={max_id - 1}&page_size=100", headers=H(tok_b))
    probe = [i["id"] for i in ((r.json() or {}).get("data", {}) or {}).get("items", [])]
    check(
        "K.6 since_id 不参与归属判据（不能借 id 区间探测他人通知）",
        not (set(probe) & set(all_ids)),
        f"probe={probe}",
    )

    # 跨租户
    r = client.get("/api/v1/notifications?since_id=0&page_size=100", headers=H(tok_c))
    c_ids = [i["id"] for i in ((r.json() or {}).get("data", {}) or {}).get("items", [])]
    check(
        "K.7 补拉不跨租户（C 只看到自己的 1 条）",
        len(c_ids) == 1 and not (set(c_ids) & set(all_ids)),
        f"c_ids={c_ids}",
    )

    # 向后兼容：不传 since_id 时仍是倒序
    r = client.get("/api/v1/notifications?page_size=100", headers=H(tok_a))
    desc_ids = [i["id"] for i in ((r.json() or {}).get("data", {}) or {}).get("items", [])]
    check(
        "K.8 不传 since_id 时行为不变（仍为倒序，向后兼容）",
        desc_ids == sorted(all_ids, reverse=True),
        f"desc={desc_ids}",
    )

    # ═══════════════ 汇总 ═══════════════
    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\n{'=' * 66}")
    print(f"P0-15 通知读路径验证：{passed} 通过 / {failed} 失败")
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
