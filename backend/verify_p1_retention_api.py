"""审计保留期管理接口的端到端验证（真 HTTP 请求路径）。

覆盖：
  E. 端点鉴权 —— 匿名/普通用户不得访问清理与归档（可跨租户删审计）
  F. 默认安全 —— purge 不传 confirm 必须 dry-run
  G. 端到端清理 —— confirm=true 真删并留墓碑
  H. 健康检查 —— /api/health 暴露生效保留天数（合规巡检用）

用法：python verify_p1_retention_api.py
退出码：0 全通过 / 1 有失败
"""
from __future__ import annotations

import os
import sys
import uuid

DB = f"./verify_p1_api_{uuid.uuid4().hex[:8]}.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ["DATABASE_URL_SYNC"] = f"sqlite:///{DB}"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail[:220]))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail[:180]}" if detail else ""))


from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402


def main() -> int:
    app = create_app()
    with TestClient(app) as client:
        # ── E. 鉴权 ──────────────────────────────────
        r = client.get("/api/v1/audit/retention/stats")
        check(
            "E.1 匿名访问保留状态被拒（401/403）",
            r.status_code in (401, 403),
            f"HTTP {r.status_code}",
        )

        r = client.post("/api/v1/audit/retention/purge", json={"confirm": True})
        check(
            "E.2 匿名触发清理被拒（否则可跨租户删审计）",
            r.status_code in (401, 403),
            f"HTTP {r.status_code}",
        )

        r = client.post("/api/v1/audit/retention/archive", json={})
        check(
            "E.3 匿名触发归档被拒",
            r.status_code in (401, 403),
            f"HTTP {r.status_code}",
        )

        # ── F. 公开策略 ──────────────────────────────
        r = client.get("/api/v1/audit/retention/policy")
        ok = r.status_code == 200
        data = r.json().get("data") if ok else {}
        check(
            "F.1 保留策略可公开查阅（合规审查取用）",
            ok and data.get("baseline_days") == 180,
            f"HTTP {r.status_code} baseline={data.get('baseline_days')}",
        )
        check(
            "F.2 生效保留期不低于等保下限 180 天",
            ok and (data.get("effective_days") or 0) >= 180,
            f"effective_days={data.get('effective_days')}",
        )

        # ── H. 健康检查 ──────────────────────────────
        r = client.get("/api/health")
        ok = r.status_code == 200
        # 健康检查响应统一包在 {success, data} 信封里
        health = (r.json() or {}).get("data") or {} if ok else {}
        ar = health.get("audit_retention") or {}
        check(
            "H.1 /api/health 暴露审计保留期生效值",
            ok and ar.get("effective_days", 0) >= 180,
            f"effective_days={ar.get('effective_days')} configured={ar.get('configured_days')}",
        )
        check(
            "H.2 /api/health 暴露保留期管理开关状态",
            "admin_enabled" in ar,
            f"admin_enabled={ar.get('admin_enabled')}",
        )

        # ── I. 管理员路径（真造过期数据 + 真调接口）──────────
        from app.database import async_session_factory
        from app.models.audit_log import AuditLog
        import asyncio
        import datetime

        from app.core.audit_context import set_audit_context  # noqa: F401  确保上下文模块已加载
        from app.models.identity import User
        from app.models.enums import UserStatus
        from app.core.rbac import Role
        from app.core.security import create_access_token

        async def _seed():
            async with async_session_factory() as db:
                from sqlalchemy import select

                admin = (
                    await db.execute(select(User).where(User.username == "retverify"))
                ).scalars().first()
                if admin is None:
                    admin = User(
                        username="retverify",
                        email="retverify@example.com",
                        hashed_password="x",
                        role=Role.PLATFORM_ADMIN,
                        status=UserStatus.ACTIVE,
                        tenant_id="platform",
                    )
                    db.add(admin)
                    await db.flush()
                old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=400)
                for i in range(3):
                    db.add(
                        AuditLog(
                            tenant_id="t_api", actor_id=None, actor_role=None,
                            action=f"API_TEST_{i}", resource_type="verify",
                            resource_id=None, detail={"i": i}, ip_address=None,
                            user_agent=None, request_id=None, success=True,
                            created_at=old,
                        )
                    )
                await db.commit()
                return admin.id

        admin_id = asyncio.run(_seed())
        token = create_access_token({"sub": str(admin_id), "role": Role.PLATFORM_ADMIN.value})
        headers = {"Authorization": f"Bearer {token}"}

        r = client.get("/api/v1/audit/retention/stats", headers=headers)
        ok = r.status_code == 200
        stats = r.json().get("data") if ok else {}
        check(
            "I.1 管理员可读保留状态且能识别过期量",
            ok and stats.get("expired", 0) >= 3,
            f"HTTP {r.status_code} total={stats.get('total')} expired={stats.get('expired')}",
        )

        # purge 默认 dry-run
        r = client.post("/api/v1/audit/retention/purge", json={}, headers=headers)
        ok = r.status_code == 200
        d = r.json().get("data") if ok else {}
        check(
            "I.2 管理员 purge 不传 confirm 仍为 dry-run",
            ok and d.get("dry_run") is True,
            f"HTTP {r.status_code} dry_run={d.get('dry_run')} would_delete={d.get('would_delete')}",
        )

        # 归档
        r = client.post("/api/v1/audit/retention/archive", json={}, headers=headers)
        ok = r.status_code == 200
        arc = r.json().get("data") if ok else {}
        check(
            "I.3 管理员归档返回可校验产物（行数 + sha256）",
            ok and bool(arc.get("sha256")) and (arc.get("rows") or 0) >= 3,
            f"HTTP {r.status_code} rows={arc.get('rows')} sha={str(arc.get('sha256'))[:12]}",
        )

        # archive-then-purge（confirm）
        r = client.post(
            "/api/v1/audit/retention/archive-then-purge",
            json={"confirm": True},
            headers=headers,
        )
        ok = r.status_code == 200
        atp = r.json().get("data") if ok else {}
        purged = (atp or {}).get("purged") or {}
        check(
            "I.4 归档校验通过后清理生效",
            ok and purged.get("dry_run") is False and (purged.get("deleted") or 0) >= 3,
            f"HTTP {r.status_code} deleted={purged.get('deleted')} note={atp.get('note')}",
        )

        # 墓碑审计
        async def _tomb():
            async with async_session_factory() as db:
                from sqlalchemy import func, select

                return int(
                    (
                        await db.execute(
                            select(func.count())
                            .select_from(AuditLog)
                            .where(AuditLog.action == "AUDIT_RETENTION_PURGE")
                        )
                    ).scalar()
                    or 0
                )

        tombstones = asyncio.run(_tomb())
        check(
            "I.5 清理留痕 AUDIT_RETENTION_PURGE（审计的删除也被审计）",
            tombstones >= 1,
            f"墓碑={tombstones}",
        )

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print("\n" + "=" * 68)
    print(f"审计保留期接口端到端验证：{passed} 通过 / {failed} 失败")
    print("=" * 68)
    for n, ok, d in results:
        if not ok:
            print(f"  [FAIL] {n}  -> {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
