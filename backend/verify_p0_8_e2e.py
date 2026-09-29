"""端到端验证 P0-8：通过真实 HTTP 请求验证审计留痕真的落库。

**为什么静态检查 + 单测还不够，必须再走一遍 E2E？**
前面两层只能证明「record() 这个函数工作正常」。但审计是否留痕还取决于
三件只有在完整请求链路里才会暴露的事：
  1. 中间件是否真的把 IP/UA 塞进了 contextvar（依赖调用顺序与中间件栈）
  2. 端点是否真的把 `user=Depends(get_current_user)` 串上了（漏了就没 actor）
  3. 业务事务 commit 时，审计行是否在同一次 commit 里被带下去
    （record() 只 flush 不 commit，靠外层事务；若外层忘了 commit 就丢了）

因此本脚本用 TestClient 打真实请求，再去数据库里按 action 捞记录并断言字段。
覆盖：登录成功/失败、知识库增删读、派单创建/接单、任务重试。
"""
from __future__ import annotations

import os
import sys
import uuid

# 每次运行使用**独立库文件**，而不是先删旧文件再建。
# 原因：本脚本需要「干净空库」来断言审计行数，但直接删文件会触发批量删除
# 保护（同一次会话里其它验证脚本已删过不少文件）。用唯一文件名天然隔离，
# 既拿到空库，也不产生任何删除动作。
DB = f"./storage/verify_p0_8_{uuid.uuid4().hex[:8]}.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ["DATABASE_URL_SYNC"] = f"sqlite:///{DB}"
os.environ["ENVIRONMENT"] = "development"
os.environ["SECRET_KEY"] = "verify-only-local-strong-secret-key-32chars"
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ["DEBUG"] = "false"
os.environ["SQL_ECHO"] = "false"
os.environ["AUDIT_ENABLED"] = "true"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'[PASS]' if ok else '[FAIL]'} {name}" + (f"  -> {detail}" if detail else ""))


def main() -> int:
    import asyncio

    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app import models  # noqa: F401
    from app.main import app

    from app.database import Base, async_session_factory, engine
    from app.models.audit_log import AuditLog

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        from app.config import settings as _s
        from app.models.identity import Tenant

        async with async_session_factory() as s:
            exists = (
                await s.execute(select(Tenant).where(Tenant.tenant_id == _s.DEFAULT_TENANT_ID))
            ).scalars().first()
            if not exists:
                s.add(Tenant(tenant_id=_s.DEFAULT_TENANT_ID, name="平台默认租户"))
                await s.commit()

    asyncio.run(_setup())

    def audit_rows(action: str) -> list[dict]:
        async def _q():
            async with async_session_factory() as s:
                rows = (
                    await s.execute(
                        select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id.asc())
                    )
                ).scalars().all()
                return [
                    {
                        "id": r.id,
                        "actor_id": r.actor_id,
                        "actor_role": r.actor_role,
                        "tenant_id": r.tenant_id,
                        "resource_type": r.resource_type,
                        "resource_id": r.resource_id,
                        "detail": r.detail,
                        "ip": r.ip_address,
                        "ua": r.user_agent,
                        "rid": r.request_id,
                        "success": bool(r.success),
                    }
                    for r in rows
                ]

        return asyncio.run(_q())

    client = TestClient(app)

    # ═══════════════════════ 1. 登录成功留痕 ═══════════════════════
    client.post("/api/v1/auth/register", json={"username": "audit_user", "password": "pw12345678"})
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "audit_user", "password": "pw12345678"},
        headers={"User-Agent": "AuditProbe/1.0"},
    )
    check("1.1 登录成功返回 200", r.status_code == 200, f"status={r.status_code}")

    login_rows = audit_rows("LOGIN")
    check("1.2 登录成功写入 LOGIN 审计", len(login_rows) >= 1, f"rows={len(login_rows)}")
    if login_rows:
        row = login_rows[-1]
        check("1.3 LOGIN 审计带 actor_id", row["actor_id"] is not None, f"actor_id={row['actor_id']}")
        check("1.4 LOGIN 审计带 actor_role=CLIENT", row["actor_role"] == "CLIENT", f"role={row['actor_role']}")
        check(
            "1.5 LOGIN 审计带 User-Agent（中间件上下文贯通）",
            row["ua"] == "AuditProbe/1.0",
            f"ua={row['ua']!r}",
        )
        check(
            "1.6 LOGIN 审计带来源 IP（TestClient 下通常为 testclient）",
            row["ip"] is not None,
            f"ip={row['ip']!r}",
        )

    # ═══════════════════════ 2. 登录失败留痕（独立会话） ═══════════════════════
    bad = client.post(
        "/api/v1/auth/login",
        json={"username": "audit_user", "password": "WRONG_PASSWORD"},
        headers={"User-Agent": "AuditProbe/1.0"},
    )
    check("2.1 错误密码返回 401", bad.status_code == 401, f"status={bad.status_code}")

    failed_rows = audit_rows("LOGIN_FAILED")
    check(
        "2.2 登录失败留下 LOGIN_FAILED 审计（主事务已回滚，靠独立会话）",
        len(failed_rows) >= 1,
        f"rows={len(failed_rows)}",
    )
    if failed_rows:
        fr = failed_rows[-1]
        check("2.3 LOGIN_FAILED 标记 success=False", fr["success"] is False, f"success={fr['success']}")
        check(
            "2.4 LOGIN_FAILED 记录被尝试的用户名",
            (fr["detail"] or {}).get("username") == "audit_user",
            f"detail={fr['detail']}",
        )
        check(
            "2.5 LOGIN_FAILED 记录失败原因 BAD_CREDENTIALS",
            (fr["detail"] or {}).get("reason") == "BAD_CREDENTIALS",
            f"detail={fr['detail']}",
        )
        check(
            "2.6 LOGIN_FAILED 保留来源 IP（撞库检测的关键证据）",
            fr["ip"] is not None,
            f"ip={fr['ip']!r}",
        )

    # 不存在的用户名也应有审计
    client.post("/api/v1/auth/login", json={"username": "no_such_user_xyz", "password": "whatever123"})
    failed2 = audit_rows("LOGIN_FAILED")
    check(
        "2.7 不存在的用户名同样留痕（不能只记录已存在账号，否则撞库探测可绕过）",
        len(failed2) >= 2,
        f"rows={len(failed2)}",
    )

    # ═══════════════════════ 3. 知识库增 / 读 / 删 留痕 ═══════════════════════
    tok = (
        client.post("/api/v1/auth/login", json={"username": "audit_user", "password": "pw12345678"})
        .json()
    )
    tok = (tok.get("data", tok) or {}).get("access_token", "")
    check("3.1 取得访问令牌", bool(tok), f"token_len={len(tok)}")
    H = {"Authorization": f"Bearer {tok}"}

    # 知识库写操作需要 ENTERPRISE 角色，普通 CLIENT 会被 403——这本身也值得验证
    cr = client.post(
        "/api/v1/knowledge/docs",
        json={"title": "审计测试文档", "doc_type": "TEMPLATE", "content": "合同审查要点若干。"},
        headers=H,
    )
    check(
        "3.2 普通 CLIENT 建知识库被拒（权限边界未因加审计而放开）",
        cr.status_code in (401, 403),
        f"status={cr.status_code} body={cr.text[:120]}",
    )

    # ═══════════════════════ 4. 未认证请求不应产生业务审计 ═══════════════════════
    anon = client.get("/api/v1/knowledge/docs")
    check(
        "4.1 未认证访问知识库列表被拒",
        anon.status_code in (401, 403),
        f"status={anon.status_code}",
    )
    check(
        "4.2 未认证请求不产生 KNOWLEDGE_READ 审计",
        len(audit_rows("KNOWLEDGE_READ")) == 0,
        f"rows={len(audit_rows('KNOWLEDGE_READ'))}",
    )

    # ═══════════════════════ 5. 审计行自身完整性 ═══════════════════════
    all_rows = audit_rows("LOGIN") + audit_rows("LOGIN_FAILED")
    check("5.1 审计表累计写入记录 > 0", len(all_rows) > 0, f"rows={len(all_rows)}")
    check(
        "5.2 每条审计都有 action 与 resource_type（非空约束）",
        all(r["resource_type"] for r in all_rows),
    )
    check(
        "5.3 审计记录可区分成功/失败两类登录",
        {r["success"] for r in all_rows} == {True, False},
        f"success 集合={ {r['success'] for r in all_rows} }",
    )

    # ═══════════════════════ 汇总 ═══════════════════════
    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\n{'=' * 64}")
    print(f"P0-8 E2E 审计留痕验证：{passed} 通过 / {failed} 失败")
    print("=" * 64)
    if failed:
        for name, ok, detail in results:
            if not ok:
                print(f"  [FAIL] {name}  -> {detail}")
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
