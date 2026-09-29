"""审计日志链路集成测试（P0-8）。

**为什么必须是真实 DB 测试，而不是静态检查？**
审计的失效模式是"静默的"：漏调用不报错、写错列不报错、事务回滚把审计一起
带走也不报错。静态检查只能证明"代码里出现了 record()"，无法证明
"调用之后 audit_log 表里真的有那一行，且内容正确"。本文件用真实会话写库
再查回来，覆盖三个最容易出错的环节：

1. `record()` 是否真的落库，且 actor / tenant / detail 完整
2. `record()` 是否吞掉异常而非阻断业务（审计故障不应导致下单失败）
3. `log_detached_ctx()` 是否能在**主事务回滚后**仍然留下记录
   —— 这是 LOGIN_FAILED 场景的核心，用同一会话写必然丢

另外守护 `AuditContext` 的**每请求隔离**：并发请求不能串 IP。
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.audit import AuditAction, log_detached, log_detached_ctx, record
from app.core.audit_context import clear_audit_context, get_audit_context, set_audit_context


@pytest.fixture()
async def session():
    """独立内存库会话：建表后交出，用完整表以隔离用例。"""
    from app import models  # noqa: F401
    from app.database import Base, async_session_factory, engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with async_session_factory() as s:
        yield s


class _FakeUser:
    """最小 actor 替身：只暴露 record() 会读取的三个属性。"""

    def __init__(self, uid: int = 7, role: str = "LAWYER", tenant: str = "tenant-a"):
        self.id = uid
        self.role = role
        self.tenant_id = tenant


async def _fetch(session, action: str):
    from app.models.audit_log import AuditLog

    return (
        await session.execute(
            select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id.desc())
        )
    ).scalars().all()


def _as_bool(v) -> bool:
    """SQLite 不存原生布尔，Boolean 列回读是 int 0/1；Postgres 回读是 bool。

    注意：这条差异**只影响测试断言**，不影响生产逻辑——因为生产代码里
    没有任何地方对 `success` 做 `is True` 判断（那样写会在 SQLite 下静默失效）。
    测试里统一归一化，才能在两种后端下跑同一份断言。
    """
    return bool(v)


# ─────────────────── 1. record() 真实落库 ───────────────────


async def test_record_persists_actor_and_detail(session):
    """核心用例：record() 后查得到那一行，且 who/when/what 都在。"""
    set_audit_context(ip_address="203.0.113.9", user_agent="pytest/1.0", request_id="req-abc")
    user = _FakeUser()

    await record(
        session,
        AuditAction.KNOWLEDGE_CREATE,
        "knowledge_doc",
        42,
        actor=user,
        detail={"title": "劳动合同模板", "chunk_count": 3},
    )
    await session.commit()

    rows = await _fetch(session, AuditAction.KNOWLEDGE_CREATE)
    assert len(rows) == 1, "审计必须落库，否则合规检查会判定为未留痕"

    row = rows[0]
    # 谁
    assert row.actor_id == 7
    assert row.actor_role == "LAWYER"
    # 哪个租户
    assert row.tenant_id == "tenant-a"
    # 做了什么
    assert row.resource_type == "knowledge_doc"
    assert row.resource_id == 42
    assert row.detail["chunk_count"] == 3
    # 从哪里来（等保要求可还原来源）
    assert row.ip_address == "203.0.113.9"
    assert row.user_agent == "pytest/1.0"
    assert row.request_id == "req-abc"
    assert _as_bool(row.success) is True


async def test_record_role_enum_normalized_to_value(session):
    """role 是枚举时必须存 .value，否则审计表里会出现 "<Role.LAWYER: 'LAWYER'>"。"""
    from app.core.rbac import Role

    class _EnumRoleUser:
        id = 1
        role = Role.LAWYER
        tenant_id = "tenant-a"

    await record(session, AuditAction.ANALYSIS_GENERATE, "analysis", 1, actor=_EnumRoleUser())
    await session.commit()

    row = (await _fetch(session, AuditAction.ANALYSIS_GENERATE))[0]
    assert row.actor_role == "LAWYER"


async def test_explicit_tenant_overrides_actor_tenant(session):
    """显式传 tenant_id 时以显式值为准（跨租户运维场景）。"""
    await record(
        session,
        AuditAction.FILE_DOWNLOAD,
        "file",
        1,
        actor=_FakeUser(tenant="tenant-a"),
        tenant_id="tenant-b",
    )
    await session.commit()
    assert (await _fetch(session, AuditAction.FILE_DOWNLOAD))[0].tenant_id == "tenant-b"


async def test_no_actor_still_writes_row(session):
    """无 actor（系统触发的操作）也要留痕，只是 actor 为空。"""
    await record(session, AuditAction.COMPLIANCE_SCAN, "compliance_scan", 5)
    await session.commit()
    row = (await _fetch(session, AuditAction.COMPLIANCE_SCAN))[0]
    assert row.actor_id is None
    assert row.resource_id == 5


# ─────────────────── 2. 审计失败不阻断业务 ───────────────────


async def test_record_swallows_db_errors(session):
    """审计写失败必须被吞掉：留痕重要，但不能因审计故障让业务不可用。"""
    from unittest.mock import patch

    with patch("app.core.audit.write_audit", side_effect=RuntimeError("disk full")):
        # 不应抛异常
        await record(session, AuditAction.LOGIN, "user", 1)


async def test_log_detached_swallows_db_errors():
    """审计失败**绝不阻断主流程** —— 这是无条件契约。

    注意 `_get_detached_factory` 本身也可能失败（配置错误 / 引擎创建异常），
    因此它同样必须在 try 内。第 8 轮加退避重试时曾把它挪到 try 外，
    本用例立刻抓到 —— 契约不能因为"加了重试"而悄悄变弱。
    """
    from unittest.mock import patch

    with patch("app.core.audit._get_detached_factory", side_effect=RuntimeError("no db")):
        await log_detached_ctx(AuditAction.LOGIN_FAILED, "user", None)


async def test_log_detached_retries_are_bounded():
    """重试次数必须有上限 —— 否则数据库持续不可用时会拖垮主流程。

    第 8 轮加退避重试是为了解决"独立会话与未提交主事务抢锁"，
    但重试必须**有限**：审计可以丢，主流程不能卡。
    """
    from unittest.mock import patch

    calls = {"n": 0}

    def _boom():
        calls["n"] += 1
        raise RuntimeError("still locked")

    with patch("app.core.audit._get_detached_factory", side_effect=_boom):
        await log_detached(action="X", resource_type="y", retries=3)

    assert calls["n"] == 3, f"实际尝试 {calls['n']} 次，期望恰好 3 次（有上限）"


# ─────────────────── 3. 独立会话：主事务回滚后仍留痕 ───────────────────


async def test_login_failed_survives_rollback(session):
    """**本组最重要的用例**。

    登录失败时业务层抛 401，请求事务被回滚。若用主会话写审计，这条记录
    会随回滚一起消失——而"哪个 IP 在反复试哪个账号"正是撞库检测的
    唯一线索。所以必须走独立会话。
    """
    from app import models  # noqa: F401
    from app.database import async_session_factory

    set_audit_context(ip_address="198.51.100.7", user_agent="attacker/1.0")

    # 模拟真实请求：主事务写业务数据 → 审计走独立会话 → 主事务回滚
    async with async_session_factory() as main_session:
        await log_detached_ctx(
            AuditAction.LOGIN_FAILED,
            "user",
            None,
            actor_id=None,
            detail={"username": "victim", "reason": "BAD_CREDENTIALS"},
            success=False,
        )
        # 主事务回滚（模拟 InvalidCredentialsError 触发的 rollback）
        await main_session.rollback()

    async with async_session_factory() as verify:
        rows = await _fetch(verify, AuditAction.LOGIN_FAILED)

    assert len(rows) == 1, "主事务回滚不应带走失败登录审计"
    row = rows[0]
    assert _as_bool(row.success) is False
    assert row.detail["username"] == "victim"
    assert row.detail["reason"] == "BAD_CREDENTIALS"
    assert row.ip_address == "198.51.100.7", "撞库检测必须能拿到来源 IP"


# ─────────────────── 4. 上下文隔离与列宽对齐 ───────────────────


async def test_context_truncates_long_values(session):
    """超长 UA 必须截断：AuditLog.user_agent 是 String(300)，
    不截断在 Postgres 上会直接 IntegrityError（SQLite 不校验，容易漏测）。"""
    set_audit_context(
        ip_address="1" * 200,
        user_agent="U" * 1000,
        request_id="R" * 200,
    )
    ctx = get_audit_context()
    assert len(ctx["ip_address"]) == 64
    assert len(ctx["user_agent"]) == 300
    assert len(ctx["request_id"]) == 64


async def test_context_is_isolated_per_task():
    """contextvars 必须每任务隔离：并发请求之间不能串 IP。

    这是 `contextvars` 相对全局变量的核心价值；用全局 dict 会串数据。
    """
    import asyncio

    results: dict[str, str | None] = {}

    async def worker(name: str, ip: str) -> None:
        set_audit_context(ip_address=ip)
        await asyncio.sleep(0.01)  # 制造协程切换窗口
        results[name] = get_audit_context().get("ip_address")

    await asyncio.gather(
        worker("a", "10.0.0.1"),
        worker("b", "10.0.0.2"),
        worker("c", "10.0.0.3"),
    )

    assert results == {"a": "10.0.0.1", "b": "10.0.0.2", "c": "10.0.0.3"}


async def test_clear_context_resets():
    set_audit_context(ip_address="10.1.1.1")
    assert get_audit_context()["ip_address"] == "10.1.1.1"
    clear_audit_context()
    assert get_audit_context() == {}


async def test_missing_context_is_safe(session):
    """无上下文（后台任务/单元测试）时 record() 不应崩，ip 为空即可。"""
    clear_audit_context()
    await record(session, AuditAction.ANALYSIS_EDIT, "analysis", 9)
    await session.commit()
    row = (await _fetch(session, AuditAction.ANALYSIS_EDIT))[0]
    assert row.ip_address is None
    assert row.resource_id == 9


# ─────────────────── 5. 守护：禁止对 success 做恒等判断 ───────────────────


def test_no_identity_check_on_success_in_source():
    """回归守卫：`success` 是 Boolean 列，SQLite 回读为 int。

    若生产代码写出 `if row.success is True:`，在 SQLite 下**恒为假**——
    这类 bug 不会报错，只会让功能静默失效。用源码扫描把这条钉死。
    """
    import os
    import re

    app_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
    offenders: list[str] = []
    pattern = re.compile(r"\.success\s+is\s+(True|False)")
    for root, _dirs, files in os.walk(app_dir):
        for f in files:
            if not f.endswith(".py"):
                continue
            p = os.path.join(root, f)
            with open(p, encoding="utf-8") as fh:
                for i, line in enumerate(fh, 1):
                    if pattern.search(line):
                        offenders.append(f"{os.path.relpath(p, app_dir)}:{i}")
    assert not offenders, f"对 success 使用 is 恒等判断（SQLite 下失效）: {offenders}"
