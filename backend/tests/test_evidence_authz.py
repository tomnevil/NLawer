"""证据接口的「案件归属」校验回归测试。

## 为什么需要这个文件

`app/api/v1/cases.py` 里 `_case_or_404` 的注释记录过一次致命缺陷：
按 `case_id` 读取时只做租户过滤，任意已登录用户遍历 `case_id`
即可读取他人案件数据。该缺陷当时只在 `cases.py` 内修复。

问题是**同一份守卫在 `evidence.py` 里是被复制过去的**，
修一处不等于修全部：`evidence.py` 的 `_case_or_404` 长期只接收
`tenant_id`，于是同租户内的客户乙可以读到客户甲案件的证据文件名、
材料缺失清单与证据时间线。证据文件名本身就是高度敏感信息
（"离婚协议书"、"劳动仲裁申请书"、"欠条"）。

本文件把 evidence 的三个读入口锁死，并且**同时断言正向路径**——
防止有人用「一律 404」的方式把测试改绿：律师仍必须能读本租户案件，
客户仍必须能读自己的案件。
"""
from __future__ import annotations

import asyncio
import pathlib
import uuid

import pytest

TENANT_A = "evidence-authz-a"
TENANT_B = "evidence-authz-b"

#: 需要做归属校验的读入口
CASE_SCOPED_ENDPOINTS = [
    "/api/v1/evidence/cases/{cid}",
    "/api/v1/evidence/cases/{cid}/missing",
    "/api/v1/evidence/cases/{cid}/timeline",
]


# ═══════════════════════ 夹具 ═══════════════════════


@pytest.fixture(scope="module")
def _engine():
    """模块级引擎：`create_all` 开销大，不每个用例重做。

    库文件落在项目内 `_tmp_tests/`（`tmp_path` 在部分沙箱环境不可写），
    且**不主动删除**——删除会触发沙箱的批量删除守卫。
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401  触发全部模型注册
    from app.models.base import Base

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"evidence_authz_{uuid.uuid4().hex[:8]}.db"

    engine = create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    yield engine
    asyncio.run(engine.dispose())


@pytest.fixture(scope="module")
def seeded(_engine):
    """同一租户下的两个客户，案件与证据均归客户甲；另在租户 B 放一个案件。

    返回的 `ids` 会被各用例读取，因此这里刻意**不做清理**——
    数据是只读的，用例之间不会互相污染。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models.case import Case
    from app.models.enums import CaseGrade, CaseStatus, EvidenceCategory, EvidenceStatus
    from app.models.evidence import Evidence
    from app.models.identity import User

    factory = async_sessionmaker(_engine, expire_on_commit=False)

    async def _seed() -> dict[str, int]:
        async with factory() as db:
            client_a = User(
                tenant_id=TENANT_A, username="authz_a", hashed_password="x", full_name="客户甲"
            )
            client_b = User(
                tenant_id=TENANT_A, username="authz_b", hashed_password="x", full_name="客户乙"
            )
            lawyer = User(
                tenant_id=TENANT_A, username="authz_law", hashed_password="x", full_name="律师丙"
            )
            db.add_all([client_a, client_b, lawyer])
            await db.flush()

            own = Case(
                case_no=f"AUTHZ-A-{uuid.uuid4().hex[:6]}",
                tenant_id=TENANT_A,
                title="客户甲的案件",
                client_user_id=client_a.id,
                status=CaseStatus.INTAKE,
                grade=CaseGrade.B,
                dispute_type="劳动争议",
            )
            foreign = Case(
                case_no=f"AUTHZ-B-{uuid.uuid4().hex[:6]}",
                tenant_id=TENANT_B,
                title="另一个租户的案件",
                status=CaseStatus.INTAKE,
                grade=CaseGrade.C,
                dispute_type="买卖合同",
            )
            db.add_all([own, foreign])
            await db.flush()

            db.add(
                Evidence(
                    tenant_id=TENANT_A,
                    case_id=own.id,
                    uploaded_by=client_a.id,
                    name="离婚协议书.pdf",
                    file_path="authz/secret.pdf",
                    file_type="application/pdf",
                    file_size=1024,
                    status=EvidenceStatus.UPLOADED,
                    category=EvidenceCategory.CONTRACT,
                )
            )
            await db.commit()

            return {
                "client_a": client_a.id,
                "client_b": client_b.id,
                "lawyer": lawyer.id,
                "own_case": own.id,
                "foreign_case": foreign.id,
            }

    return asyncio.run(_seed())


def _client_for(engine, *, tenant_id: str, user_id: int, role):
    """构造一个「以某身份发起请求」的 TestClient。

    同时覆盖 `get_db` 与 `get_tenant_context`，使用例完全不依赖外部
    `DATABASE_URL`——否则跑测试前必须先手工准备一套库。
    """
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.deps import TenantContext, get_current_user, get_db, get_tenant_context
    from app.main import create_app
    from app.models.identity import User

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def _override_user():
        # Q-R：证据端点按权限码门控，`require_permissions` 读 `get_current_user.role`，
        # 故身份注入必须同时提供带角色的 user（仅覆盖 get_tenant_context 不够）。
        return User(id=user_id, tenant_id=tenant_id, role=role,
                    username="override", full_name="override")

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        tenant_id=tenant_id, user_id=user_id, role=role
    )
    # 不用 with：不触发 lifespan（避免 alembic 迁移），表已由 create_all 建好
    return TestClient(app)


# ═══════════════════════ 反向断言：越权必须被拒 ═══════════════════════


@pytest.mark.parametrize("endpoint", CASE_SCOPED_ENDPOINTS)
def test_client_cannot_read_other_clients_evidence(_engine, seeded, endpoint):
    """同租户内，客户乙读客户甲案件的证据类资源 → 404。

    返回 404 而非 403 是有意的：403 会泄露「该 case_id 存在」这一事实，
    攻击者可据此枚举出有效案件编号。`cases.py` 的守卫同样返回 404。
    """
    from app.core.rbac import Role

    client = _client_for(_engine, tenant_id=TENANT_A, user_id=seeded["client_b"], role=Role.CLIENT)
    res = client.get(endpoint.format(cid=seeded["own_case"]))

    assert res.status_code == 404, (
        f"{endpoint} 应拒绝非归属客户，实际 {res.status_code}：{res.text[:200]}"
    )


def test_case_detail_guard_still_returns_404(_engine, seeded):
    """对照组：`cases.py` 的守卫是本文件的参照口径，必须同样返回 404。

    如果这条失败，说明测试环境的身份注入有问题，上一条用例的
    「通过」也就不可信了。
    """
    from app.core.rbac import Role

    client = _client_for(_engine, tenant_id=TENANT_A, user_id=seeded["client_b"], role=Role.CLIENT)
    res = client.get(f"/api/v1/cases/{seeded['own_case']}")
    assert res.status_code == 404


@pytest.mark.parametrize("endpoint", CASE_SCOPED_ENDPOINTS)
def test_cross_tenant_denied(_engine, seeded, endpoint):
    """跨租户读取 → 404（租户隔离不能因本次修复而松动）。"""
    from app.core.rbac import Role

    client = _client_for(_engine, tenant_id=TENANT_A, user_id=seeded["client_a"], role=Role.CLIENT)
    res = client.get(endpoint.format(cid=seeded["foreign_case"]))
    assert res.status_code == 404, f"{endpoint} 跨租户应返回 404，实际 {res.status_code}"


# ═══════════════════════ 正向断言：不得「一律拒绝」 ═══════════════════════


@pytest.mark.parametrize("endpoint", CASE_SCOPED_ENDPOINTS)
def test_client_can_read_own_evidence(_engine, seeded, endpoint):
    """客户读自己的案件 → 200。

    没有这条，把守卫改成 `raise NotFoundError` 就能让上面所有用例变绿——
    那会直接废掉证据功能。
    """
    from app.core.rbac import Role

    client = _client_for(
        _engine, tenant_id=TENANT_A, user_id=seeded["client_a"], role=Role.CLIENT
    )
    res = client.get(endpoint.format(cid=seeded["own_case"]))
    assert res.status_code == 200, f"{endpoint} 应允许本人读取，实际 {res.status_code}"


@pytest.mark.parametrize("endpoint", CASE_SCOPED_ENDPOINTS)
def test_lawyer_can_read_tenant_case_evidence(_engine, seeded, endpoint):
    """本租户律师读本租户案件 → 200。

    律师的隔离口径是「租户级」而非「归属级」：同一律所内多位律师协作
    同一案件是常态，若把守卫写成只允许 `client_user_id` 匹配，
    会直接把律师端办不了案。
    """
    from app.core.rbac import Role

    client = _client_for(
        _engine, tenant_id=TENANT_A, user_id=seeded["lawyer"], role=Role.LAWYER
    )
    res = client.get(endpoint.format(cid=seeded["own_case"]))
    assert res.status_code == 200, f"{endpoint} 应允许本租户律师读取，实际 {res.status_code}"
