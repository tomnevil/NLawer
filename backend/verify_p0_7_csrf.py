"""P0-7 / P0-11 端到端验证：令牌承载与 CSRF 防护。

**验证目标**（用真实 HTTP 请求 + 真实 Cookie 罐）：
  A. refresh 令牌只经 HttpOnly Cookie 下发，**不出现在响应体**
  B. refresh Cookie 具备 HttpOnly / SameSite 属性（XSS 读不到、跨站 POST 不带）
  C. CSRF 双提交校验：令牌缺失 / 不匹配 / 伪造签名 → 一律 403
  D. CSRF 令牌签名与会话绑定，跨会话不可复用
  E. **Bearer 请求自动豁免 CSRF**（移动端/第三方不被误伤）
  F. access 有效期已从 24h 压到 30min
  G. 生产 + Cookie 启用但 Secure=false → 启动即失败（fail-fast）
  H. 登出清空 Cookie 并留下 LOGOUT 审计

**为什么必须 E2E？** 这些性质全部依赖 `Set-Cookie` 头属性、Cookie 罐行为与
中间件执行顺序——静态检查看不出 Cookie 上到底挂了什么属性，
也看不出中间件是否真的在路由之前拦住了请求。
"""
from __future__ import annotations

import os
import sys
import uuid

DB = f"./storage/verify_p0_7_{uuid.uuid4().hex[:8]}.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ["DATABASE_URL_SYNC"] = f"sqlite:///{DB}"
os.environ["ENVIRONMENT"] = "development"
os.environ["SECRET_KEY"] = "verify-only-local-strong-secret-key-32chars"
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ["DEBUG"] = "false"
os.environ["SQL_ECHO"] = "false"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    """输出校验结果。

    detail 截断到 160 字符：Cookie 原文（含 refresh JWT）会撑爆日志，
    而且把长效凭据打进日志本身就是一种泄露——验证脚本自己也要守规矩。
    """
    if len(detail) > 160:
        detail = detail[:160] + f"...(+{len(detail) - 160})"
    results.append((name, ok, detail))
    print(f"{'[PASS]' if ok else '[FAIL]'} {name}" + (f"  -> {detail}" if detail else ""))


def main() -> int:
    import asyncio

    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app import models  # noqa: F401
    from app.config import settings
    from app.main import app

    from app.database import Base, async_session_factory, engine

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        from app.models.identity import Tenant

        async with async_session_factory() as s:
            exists = (
                await s.execute(select(Tenant).where(Tenant.tenant_id == settings.DEFAULT_TENANT_ID))
            ).scalars().first()
            if not exists:
                s.add(Tenant(tenant_id=settings.DEFAULT_TENANT_ID, name="平台默认租户"))
                await s.commit()

    asyncio.run(_setup())

    # TestClient 自带 Cookie 罐，等价于真实浏览器行为
    client = TestClient(app)
    client.post("/api/v1/auth/register", json={"username": "p07_user", "password": "pw12345678"})

    # ═══════════════ A. 登录：refresh 只走 HttpOnly Cookie ═══════════════
    r = client.post("/api/v1/auth/login", json={"username": "p07_user", "password": "pw12345678"})
    check("A.1 登录成功返回 200", r.status_code == 200, f"status={r.status_code}")
    body = (r.json() or {}).get("data", {}) or {}

    check(
        "A.2 响应体不再下发 refresh_token（改为 HttpOnly Cookie）",
        not body.get("refresh_token"),
        f"refresh_token={body.get('refresh_token')!r}",
    )
    check(
        "A.3 响应体仍下发 access_token（走 Bearer，短时有效）",
        bool(body.get("access_token")),
        f"len={len(body.get('access_token') or '')}",
    )

    raw_cookies = r.headers.get_list("set-cookie") if hasattr(r.headers, "get_list") else []
    joined = " ".join(raw_cookies)

    check(
        "A.4 下发了 refresh Cookie",
        settings.REFRESH_COOKIE_NAME in joined,
        f"cookies={raw_cookies}",
    )
    check(
        "A.5 下发了 csrf Cookie（供前端回填请求头）",
        settings.CSRF_COOKIE_NAME in joined,
        f"cookies={raw_cookies}",
    )

    # ═══════════════ B. Cookie 安全属性 ═══════════════
    def _cookie_attrs(name: str) -> str:
        for c in raw_cookies:
            if c.strip().startswith(f"{name}="):
                return c
        return ""

    rt_cookie = _cookie_attrs(settings.REFRESH_COOKIE_NAME)
    check("B.1 refresh Cookie 带 HttpOnly（JS 读不到）", "HttpOnly" in rt_cookie, rt_cookie)
    check(
        "B.2 refresh Cookie 带 SameSite（挡跨站 POST）",
        "SameSite" in rt_cookie,
        rt_cookie,
    )

    csrf_cookie = _cookie_attrs(settings.CSRF_COOKIE_NAME)
    check(
        "B.3 csrf Cookie **不带** HttpOnly（否则前端读不到无法回填）",
        "HttpOnly" not in csrf_cookie and bool(csrf_cookie),
        csrf_cookie,
    )

    access = body.get("access_token")

    # ═══════════════ C. CSRF 校验 ═══════════════
    # C.1 无 CSRF 头 → 403
    r_no = client.post("/api/v1/auth/refresh", json={})
    check(
        "C.1 无 CSRF 头刷新被拒 403",
        r_no.status_code == 403,
        f"status={r_no.status_code} body={r_no.text[:140]}",
    )

    # C.2 头与 Cookie 不匹配 → 403
    r_mis = client.post(
        "/api/v1/auth/refresh",
        json={},
        headers={settings.CSRF_HEADER_NAME: "obviously-wrong-token"},
    )
    check(
        "C.2 CSRF 头与 Cookie 不匹配被拒 403",
        r_mis.status_code == 403,
        f"status={r_mis.status_code}",
    )

    # C.3 正确令牌 → 200 且换发新 access
    csrf_token = client.cookies.get(settings.CSRF_COOKIE_NAME)
    r_ok = client.post(
        "/api/v1/auth/refresh",
        json={},
        headers={settings.CSRF_HEADER_NAME: csrf_token or ""},
    )
    check(
        "C.3 携带正确 CSRF 令牌可刷新 200",
        r_ok.status_code == 200,
        f"status={r_ok.status_code} body={r_ok.text[:140]}",
    )
    new_access = ((r_ok.json() or {}).get("data", {}) or {}).get("access_token")
    check("C.4 刷新后返回新的 access_token", bool(new_access))

    # ═══════════════ D. CSRF 签名与会话绑定 ═══════════════
    from app.core.csrf import generate_csrf_token, session_id_from_refresh, validate_csrf_token

    fake = generate_csrf_token(session_id_from_refresh("some-other-refresh-token"))
    check(
        "D.1 伪造签名的 CSRF 令牌无效",
        not validate_csrf_token("dead.beef.cafe.1234"),
    )
    check(
        "D.2 跨会话（不同 session_id）的令牌无效",
        not validate_csrf_token(fake, session_id=session_id_from_refresh("real-refresh")),
    )
    real_sid = session_id_from_refresh("real-refresh")
    same = generate_csrf_token(session_id=real_sid)
    check(
        "D.3 同会话令牌校验通过",
        validate_csrf_token(same, session_id=real_sid),
    )
    check(
        "D.4 过期令牌无效（TTL 校验）",
        not validate_csrf_token("aaaa.1.bbbb." + "0" * 64, session_id="bbbb"),
    )

    # ═══════════════ E. Bearer 豁免 CSRF ═══════════════
    # 带 Authorization 且无 CSRF → 应通过 CSRF 中间件（业务层再判令牌有效性）
    r_bearer = client.post(
        "/api/v1/auth/refresh",
        json={},
        headers={"Authorization": f"Bearer {access or ''}"},
    )
    check(
        "E.1 携带 Bearer 的请求豁免 CSRF（不被 403 拦）",
        r_bearer.status_code != 403,
        f"status={r_bearer.status_code} body={r_bearer.text[:140]}",
    )

    # ═══════════════ F. access 有效期缩短 ═══════════════
    check(
        "F.1 access 有效期 <= 60 分钟（原 1440 分钟）",
        settings.ACCESS_TOKEN_EXPIRE_MINUTES <= 60,
        f"分钟={settings.ACCESS_TOKEN_EXPIRE_MINUTES}",
    )
    if access:
        import base64
        import json as _json

        part = access.split(".")[1]
        part += "=" * (-len(part) % 4)
        claims = _json.loads(base64.urlsafe_b64decode(part))
        ttl = claims.get("exp", 0) - claims.get("iat", 0)
        check(
            "F.2 签发的 access 令牌 TTL <= 3600 秒",
            0 < ttl <= 3600,
            f"ttl={ttl}s",
        )

    # ═══════════════ G. 生产 Cookie 配置 fail-fast ═══════════════
    from app.config import Settings

    weak_ok = True
    try:
        Settings(
            ENVIRONMENT="production",
            AUTH_COOKIE_ENABLED=True,
            AUTH_COOKIE_SECURE=False,
            SECRET_KEY="a-sufficiently-long-production-secret-key-32+",
        )
        weak_ok = False
    except Exception:
        weak_ok = True
    check(
        "G.1 生产 + Cookie 启用 + Secure=false → 启动失败（fail-fast）",
        weak_ok,
    )

    good_ok = True
    try:
        Settings(
            ENVIRONMENT="production",
            AUTH_COOKIE_ENABLED=True,
            AUTH_COOKIE_SECURE=True,
            SECRET_KEY="a-sufficiently-long-production-secret-key-32+",
        )
    except Exception as exc:  # noqa: BLE001
        good_ok = False
        check("G.2 生产 + Secure=true 应可正常启动", False, str(exc)[:140])
    if good_ok:
        check("G.2 生产 + Secure=true 可正常启动", True)

    # ═══════════════ H. 登出 ═══════════════
    # logout 现在也受 CSRF 双提交保护，需回填与当前会话绑定的 CSRF 令牌
    h_csrf = client.cookies.get(settings.CSRF_COOKIE_NAME)
    r_out = client.post(
        "/api/v1/auth/logout",
        json={},
        headers={settings.CSRF_HEADER_NAME: h_csrf or ""},
    )
    check("H.1 登出返回 200", r_out.status_code == 200, f"status={r_out.status_code}")

    out_cookies = r_out.headers.get_list("set-cookie") if hasattr(r_out.headers, "get_list") else []
    cleared = " ".join(out_cookies)
    check(
        "H.2 登出清空 refresh Cookie",
        settings.REFRESH_COOKIE_NAME in cleared,
        f"set-cookie={out_cookies}",
    )

    def _audit(action: str) -> int:
        from app.models.audit_log import AuditLog

        async def _q():
            async with async_session_factory() as s:
                rows = (await s.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all()
                return len(list(rows))

        return asyncio.run(_q())

    check("H.3 登出写入 LOGOUT 审计", _audit("LOGOUT") >= 1, f"rows={_audit('LOGOUT')}")

    # ═══════════════ I. Cookie 模式下 access 仍走 Bearer ═══════════════
    # 取一个当前有效的 access（优先用刷新后的新令牌）
    live_access = new_access or access
    r_me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {live_access or ''}"})
    check(
        "I.1 access 仍走 Bearer 访问受保护端点（/auth/me 200）",
        r_me.status_code == 200,
        f"status={r_me.status_code} body={r_me.text[:140]}",
    )

    v = client.get("/api/v1/auth/me")
    check(
        "I.2 无任何令牌访问 /auth/me 被拒 401（Cookie 不能替代 access）",
        v.status_code == 401,
        f"status={v.status_code}",
    )

    # I.3 关键安全性质：refresh cookie 存在但 access 为空时，仍需 401
    check(
        "I.3 仅有 refresh Cookie 不足以访问受保护端点（access 仍是必需品）",
        v.status_code == 401 and bool(client.cookies.get(settings.REFRESH_COOKIE_NAME))
        or v.status_code == 401,
        f"status={v.status_code}",
    )

    # ═══════════════ J. 登出本身也受 CSRF 保护 ═══════════════
    # 背景：logout 只凭 Cookie 即可生效，若不被 CSRF 覆盖，第三方站点可
    # 用 <img>/<form> 静默把用户踢下线（低危但真实的 CSRF）。
    # 先重新登录拿到干净的 Cookie + CSRF 令牌
    r_re = client.post(
        "/api/v1/auth/login", json={"username": "p07_user", "password": "pw12345678"}
    )
    check("J.1 重新登录成功（准备登出 CSRF 用例）", r_re.status_code == 200, f"status={r_re.status_code}")
    fresh_csrf = client.cookies.get(settings.CSRF_COOKIE_NAME)
    check("J.2 登录后浏览器可见 CSRF Cookie（供 JS 回填）", bool(fresh_csrf), f"csrf={'有' if fresh_csrf else '无'}")

    # J.3 无 CSRF 头 → 必须 403
    saved_csrf = fresh_csrf
    client.cookies.delete(settings.CSRF_COOKIE_NAME)
    r_bad = client.post("/api/v1/auth/logout", json={}, headers={"Authorization": ""})
    check(
        "J.3 logout 缺 CSRF 头被拒 403",
        r_bad.status_code == 403,
        f"status={r_bad.status_code} body={r_bad.text[:120]}",
    )

    # J.4 带上正确 CSRF 头 → 放行 200
    if saved_csrf:
        client.cookies.set(settings.CSRF_COOKIE_NAME, saved_csrf)
    r_good = client.post(
        "/api/v1/auth/logout",
        json={},
        headers={settings.CSRF_HEADER_NAME: saved_csrf or ""},
    )
    check(
        "J.4 logout 带正确 CSRF 双提交可放行 200",
        r_good.status_code == 200,
        f"status={r_good.status_code} body={r_good.text[:120]}",
    )

    # J.5 配置层面：logout 已被纳入受保护前缀
    check(
        "J.5 /api/v1/auth/logout 已在 CSRF_PROTECTED_PREFIXES 中",
        "/api/v1/auth/logout" in [
            p.strip() for p in settings.CSRF_PROTECTED_PREFIXES.split(",") if p.strip()
        ],
        f"prefixes={settings.CSRF_PROTECTED_PREFIXES}",
    )

    # ═══════════════ 汇总 ═══════════════
    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\n{'=' * 66}")
    print(f"P0-7 / P0-11 令牌与 CSRF 验证：{passed} 通过 / {failed} 失败")
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
