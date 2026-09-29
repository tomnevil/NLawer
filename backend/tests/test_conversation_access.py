"""会话归属判据单元测试（PRD NP-07~09，M2 出口标准：「8 类身份用例」）。

## 为什么这个文件必须存在

`conversation_access.can_access_conversation` 是**唯一的**会话可见性判据，
WS 与 REST 三处共用。它同时也是本轮修复的核心安全缺陷的落点：

> 收敛前，详情/发消息/会话 WS 三处只查 `tenant_id`。叠加「公开自助注册把
> 所有客户放进同一个默认租户」，等于**任意注册客户可读取任意其他客户的
> 会话与消息**（含咨询内容，受保密义务约束）。

一个承载这种职责的纯函数**没有测试**是不可接受的：判据分散时靠「四处对比」
能发现漂移，收敛成一处后就只剩「穷尽身份用例」这一种发现方式了。

## 本文件的两类断言

1. **越权必须被拒**（安全）：端用户跨用户、跨租户 —— 这是修复目标。
2. **合法访问必须仍然放行**（不回归）：收敛判据时最容易犯的错不是放太宽，
   而是**误伤**——把原本合法的角色（如企业管理员）顺手挡在门外。
   第 2 类断言与第 1 类同等重要，且只有穷尽身份才能发现。
"""
from __future__ import annotations

import asyncio
import pathlib
import uuid

import pytest

TENANT_A = "firm_access"
TENANT_B = "firm_access_other"
CLIENT_UID = 801
OTHER_CLIENT_UID = 802  # 同租户的另一个客户：本次修复的核心面
BOUND_LAWYER_UID = 803
CASE_OWNER_UID = 804
STAFF_UID = 805


class _Conv:
    """判据只读取三个属性，因此无需真实 ORM 对象即可穷尽断言。

    这也是把判据做成**纯函数**（`case_owner_id` 由调用方预解析）的目的：
    判据本身是本模块唯一需要被穷尽验证的逻辑，不该被数据库夹具的复杂度掩盖。
    """

    def __init__(
        self,
        *,
        tenant_id: str = TENANT_A,
        client_user_id: int | None = CLIENT_UID,
        bind_lawyer_id: int | None = None,
        case_id: int | None = None,
    ) -> None:
        self.tenant_id = tenant_id
        self.client_user_id = client_user_id
        self.bind_lawyer_id = bind_lawyer_id
        self.case_id = case_id


def _allowed(conv, *, user_id, role, tenant_id=TENANT_A, case_owner_id=None) -> bool:
    from app.services.conversation_access import can_access_conversation

    return can_access_conversation(
        conv,
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        case_owner_id=case_owner_id,
    )


# ═══════════════════ A. 八类身份判据（PRD 判据表逐行） ═══════════════════


def test_identity_1_client_owner_allowed():
    """① 会话客户本人 —— 唯一无争议的放行。"""
    from app.core.rbac import Role

    assert _allowed(_Conv(), user_id=CLIENT_UID, role=Role.CLIENT) is True


def test_identity_2_client_same_tenant_other_user_denied():
    """② **同租户另一个客户 —— 本次修复的核心，必须拒绝**。

    修复前详情/发消息/WS 三处只查 `tenant_id`，此处会放行；
    叠加「自助注册客户全落默认租户」后即为真实的跨客户数据泄露。
    """
    from app.core.rbac import Role

    assert _allowed(_Conv(), user_id=OTHER_CLIENT_UID, role=Role.CLIENT) is False


def test_identity_3_bound_lawyer_allowed():
    """③ 会话绑定律师 —— 扫码/名片绑定即服务关系。"""
    from app.core.rbac import Role

    conv = _Conv(bind_lawyer_id=BOUND_LAWYER_UID)
    assert _allowed(conv, user_id=BOUND_LAWYER_UID, role=Role.LAWYER) is True


def test_identity_4_case_owner_allowed():
    """④ 关联案件的承办律师 —— 会话已升级为案件，必须能看到上下文。"""
    from app.core.rbac import Role

    conv = _Conv(case_id=99)
    assert (
        _allowed(conv, user_id=CASE_OWNER_UID, role=Role.LAWYER, case_owner_id=CASE_OWNER_UID)
        is True
    )


def test_identity_5_same_tenant_lawyer_allowed():
    """⑤ 同租户未绑定律师 —— 律所协作模式（既有设计，见 Q4）。"""
    from app.core.rbac import Role

    assert _allowed(_Conv(), user_id=STAFF_UID, role=Role.LAWYER) is True


def test_identity_6_assistant_allowed():
    """⑥ 助理 —— 所内人员，同上。"""
    from app.core.rbac import Role

    assert _allowed(_Conv(), user_id=STAFF_UID, role=Role.ASSISTANT) is True


def test_identity_7_firm_admin_allowed():
    """⑦ 律所管理员 —— 同上。"""
    from app.core.rbac import Role

    assert _allowed(_Conv(), user_id=STAFF_UID, role=Role.FIRM_ADMIN) is True


def test_identity_8_platform_admin_allowed_cross_tenant():
    """⑧ 平台管理员 —— 跨租户是**设计意图**（运维与合规审计）。"""
    from app.core.rbac import Role

    conv = _Conv(tenant_id=TENANT_B)
    assert (
        _allowed(conv, user_id=STAFF_UID, role=Role.PLATFORM_ADMIN, tenant_id=TENANT_A)
        is True
    )


# ═══════════════════ B. 不回归：收敛时最易误伤的角色 ═══════════════════


def test_enterprise_admin_is_not_collateral_damage():
    """**企业管理员必须仍被放行**（本租户内）。

    收敛判据前，三处端点都只查 `tenant_id`，企业管理员原本可用。
    `ENTERPRISE_ADMIN` 是产品线 B 的**租户级管理员**（与 `FIRM_ADMIN`
    对律所的关系一致）；若角色集合里漏列它，收敛动作就会把它挡在门外——
    那是**功能回归**而非安全收紧。

    这条用例是真实发现的缺陷的固化：初版 `STAFF_ROLES` 只列了
    LAWYER / ASSISTANT / FIRM_ADMIN，`ENTERPRISE_ADMIN` 落到「默认拒绝」分支。
    """
    from app.core.rbac import Role

    assert _allowed(_Conv(), user_id=STAFF_UID, role=Role.ENTERPRISE_ADMIN) is True


def test_enterprise_user_is_end_user_not_staff():
    """`ENTERPRISE_USER` 是**端用户**，与企业客户面临同样的保密约束。

    它绝不能因为「名字里有 ENTERPRISE」而被当成企业侧人员放行——
    那会重新引入跨用户泄露，只是换了一条产品线。
    """
    from app.core.rbac import Role

    assert _allowed(_Conv(), user_id=OTHER_CLIENT_UID, role=Role.ENTERPRISE_USER) is False
    # 但本人访问自己的会话仍应放行
    assert _allowed(_Conv(), user_id=CLIENT_UID, role=Role.ENTERPRISE_USER) is True


# ═══════════════════ C. 租户隔离底线 ═══════════════════


def test_cross_tenant_staff_denied():
    """跨租户任何身份（平台管理员除外）一律拒绝 —— 租户隔离底线。"""
    from app.core.rbac import Role

    conv = _Conv(tenant_id=TENANT_B)
    for role in (Role.LAWYER, Role.ASSISTANT, Role.FIRM_ADMIN, Role.ENTERPRISE_ADMIN):
        assert _allowed(conv, user_id=STAFF_UID, role=role, tenant_id=TENANT_A) is False, role


def test_cross_tenant_client_denied():
    from app.core.rbac import Role

    conv = _Conv(tenant_id=TENANT_B, client_user_id=CLIENT_UID)
    assert (
        _allowed(conv, user_id=CLIENT_UID, role=Role.CLIENT, tenant_id=TENANT_A) is False
    )


def test_tenant_floor_precedes_identity_shortcuts():
    """**判据顺序**：租户检查必须先于「客户本人」这一快速路径。

    若把「本人」判断放在租户检查之前，一个被迁移/伪造了 tenant 上下文的
    请求会仅凭 `client_user_id` 相等就通过 —— 租户隔离被短路。
    """
    from app.core.rbac import Role

    conv = _Conv(tenant_id=TENANT_B, client_user_id=CLIENT_UID)
    assert (
        _allowed(conv, user_id=CLIENT_UID, role=Role.CLIENT, tenant_id=TENANT_A) is False
    )


# ═══════════════════ D. 默认拒绝与边界 ═══════════════════


def test_non_positive_user_id_denied():
    """哨兵值 `user_id <= 0` 不是真实用户（未认证/占位），必须拒绝。"""
    from app.core.rbac import Role

    assert _allowed(_Conv(), user_id=0, role=Role.CLIENT) is False
    assert _allowed(_Conv(), user_id=-1, role=Role.LAWYER) is False


def test_unknown_role_fails_safe():
    """**默认拒绝**：未列举的角色值一律拒绝，而非放行。

    失败方向必须是安全的那一侧——新增角色时若忘记更新判据，
    表现应为「访问被拒」（可发现、可修），而不是「越权放行」（不可发现）。
    """

    class _FutureRole(str):
        pass

    assert _allowed(_Conv(), user_id=STAFF_UID, role=_FutureRole("SUPER_USER")) is False


def test_unbound_conversation_without_case_owner():
    """未绑定律师、无关联案件时，非本人的同租户律师**仍**放行（既有设计）。"""
    from app.core.rbac import Role

    conv = _Conv(bind_lawyer_id=None, case_id=None, client_user_id=OTHER_CLIENT_UID)
    assert _allowed(conv, user_id=STAFF_UID, role=Role.LAWYER) is True


# ═══════════════════ E. load_accessible_conversation（含 DB） ═══════════════════


@pytest.fixture(scope="module")
def _engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401
    from app.models.base import Base

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"conv_access_{uuid.uuid4().hex[:8]}.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    yield engine
    asyncio.run(engine.dispose())


@pytest.fixture
def db_ctx(_engine):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(_engine, expire_on_commit=False)

    async def _clean():
        async with _engine.begin() as conn:
            await conn.execute(text("DELETE FROM conversations"))

    asyncio.run(_clean())
    return factory


async def _make_conv(db, **kw):
    from app.models.conversation import Conversation
    from app.models.enums import ConversationStatus, IMChannel

    conv = Conversation(
        tenant_id=kw.pop("tenant_id", TENANT_A),
        external_user_id=kw.pop("external_user_id", f"ext-{uuid.uuid4().hex[:6]}"),
        channel=IMChannel.WEB_SIM,
        status=ConversationStatus.BOT,
        context={},
        **kw,
    )
    db.add(conv)
    await db.commit()
    return conv.id


def test_load_missing_conversation_raises_not_found(db_ctx):
    from app.core.errors import ErrorCode, NotFoundError
    from app.core.rbac import Role
    from app.services.conversation_access import load_accessible_conversation

    async def _go():
        async with db_ctx() as s:
            try:
                await load_accessible_conversation(
                    s, 999999, user_id=CLIENT_UID, tenant_id=TENANT_A, role=Role.CLIENT
                )
            except NotFoundError as exc:
                return exc.code

    assert _run(_go()) == ErrorCode.CONVERSATION_NOT_FOUND


def test_load_unauthorized_is_indistinguishable_from_missing(db_ctx):
    """**不可枚举**：无权与不存在必须抛出**完全一样**的错误。

    全局自增 id 下，若两者可区分，攻击者可用状态码/错误码枚举出
    「哪些会话 id 真实存在」，进而推断业务规模与他人的活跃度（OWASP IDOR）。
    """
    from app.core.errors import ErrorCode, NotFoundError
    from app.core.rbac import Role
    from app.services.conversation_access import load_accessible_conversation

    async def _go():
        async with db_ctx() as s:
            real_id = await _make_conv(s, client_user_id=OTHER_CLIENT_UID)
            results = []
            for cid in (real_id, 999999):  # 存在但无权 / 不存在
                try:
                    await load_accessible_conversation(
                        s, cid, user_id=CLIENT_UID, tenant_id=TENANT_A, role=Role.CLIENT
                    )
                except NotFoundError as exc:
                    results.append((exc.code, str(exc.message)))
            return results

    a, b = _run(_go())
    assert a == b, f"无权与不存在的错误可区分，可被用于枚举：{a} vs {b}"
    assert a[0] == ErrorCode.CONVERSATION_NOT_FOUND


def test_load_authorized_returns_conversation(db_ctx):
    from app.core.rbac import Role
    from app.services.conversation_access import load_accessible_conversation

    async def _go():
        async with db_ctx() as s:
            cid = await _make_conv(s, client_user_id=CLIENT_UID)
            conv = await load_accessible_conversation(
                s, cid, user_id=CLIENT_UID, tenant_id=TENANT_A, role=Role.CLIENT
            )
            return conv.id

    assert _run(_go()) > 0


def test_load_skips_case_lookup_for_end_users(db_ctx, monkeypatch):
    """**性能契约**：端用户路径不得触发 `cases` 查询。

    端用户的全部合法场景都被判据 1/2 覆盖，做案件查询是纯浪费。
    若忘记这个短路，每个客户打开会话都会多一次无谓查询——
    这类开销不会报错，只会表现为「数据库 QPS 莫名偏高」。

    断言方式：把 `resolve_case_owner_id` 换成探针，直接检查**有没有被调用**，
    比解析 SQL 语句更稳定（不受 SQL 方言与日志格式影响）。
    """
    from app.core.rbac import Role
    from app.services import conversation_access as ca
    from app.services.conversation_access import load_accessible_conversation

    calls: list[int] = []

    async def _spy(db, conv):
        calls.append(conv.id)
        return None

    monkeypatch.setattr(ca, "resolve_case_owner_id", _spy)

    async def _go():
        async with db_ctx() as s:
            cid = await _make_conv(s, client_user_id=CLIENT_UID, case_id=12345)
            await load_accessible_conversation(
                s, cid, user_id=CLIENT_UID, tenant_id=TENANT_A, role=Role.CLIENT
            )
            return cid

    cid = _run(_go())
    assert calls == [], f"端用户路径仍解析了案件归属（会话 {cid}）：{calls}"


def test_load_resolves_case_for_staff_when_not_bound(db_ctx, monkeypatch):
    """对照用例：所内人员且非绑定律师时，**必须**解析案件归属。

    与上一条互补——只测「不该查的时候没查」会掩盖「该查的时候也没查」：
    若把 `needs_case` 写成恒 `False`，上一条仍会通过，但承办律师会被误拒。
    """
    from app.core.rbac import Role
    from app.services import conversation_access as ca
    from app.services.conversation_access import load_accessible_conversation

    calls: list[int] = []

    async def _spy(db, conv):
        calls.append(conv.id)
        return CASE_OWNER_UID

    monkeypatch.setattr(ca, "resolve_case_owner_id", _spy)

    async def _go():
        async with db_ctx() as s:
            cid = await _make_conv(s, client_user_id=CLIENT_UID, case_id=12345)
            conv = await load_accessible_conversation(
                s, cid, user_id=CASE_OWNER_UID, tenant_id=TENANT_A, role=Role.LAWYER
            )
            return cid, conv.id

    cid, got = _run(_go())
    assert calls == [cid], f"承办律师路径未解析案件归属：{calls}"
    assert got == cid


def _run(coro):
    return asyncio.run(coro)
