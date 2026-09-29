"""通知**写入侧**（生产者）测试 —— P0-15 第二轮：补齐缺失的写入点。

## 为什么需要这个文件

P0-15 第一阶段的根因是「只写不读」；读路径补完后做第二轮排查，发现
9 种 `NotificationType` 里有 5 种**从来没有写入点**——枚举定义了、
前端图标配好了、`_TITLE` 映射也写了，但全库没有任何业务代码会发出它。

这类缺陷的可怕之处在于**完全静默**：不报错、不 500、日志干净、
接口返回 200。只是那个功能「什么都不发生」。因此它不会被任何
「接口能跑通」的测试发现，必须专门针对生产者写断言。

本文件针对已接线的 4 个生产者，逐条锁死三件最容易写错的事：

1. **收件人是谁**。发错人比不发更糟：客户在等「谁接了我的案子」，
   通知却发给了刚点完「接单」的律师本人——双方都没拿到有用信息，
   还多消耗一次通知配额的信噪比。
2. **发几次**。额度预警是最典型的反面教材：按「低于阈值」判断会让
   用户一旦进入预警区就**每次扣减都收到一条**，最终训练用户忽略所有
   通知，连带让「派单待接」「待复核」这些真通知一起失效。
3. **什么时候发**。证据分类是**异步**写入的，检查点放错位置会发出
   「用户刚上传完材料，系统立刻说他材料缺失」——最伤信任的那类假警报。

第 5 种 `DOCUMENT_CONFIRMED` **故意没有接线**，原因见
`test_document_confirmed_still_has_no_producer`。
"""
from __future__ import annotations

import asyncio
import pathlib
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

TENANT_A = "firm_a"
LAWYER_ID = 101
CLIENT_ID = 102
STRANGER_ID = 999


# ═══════════════════════ 夹具 ═══════════════════════


@pytest.fixture(scope="module")
def _engine():
    """模块级引擎：`create_all` 建表开销约 25s，不能每个用例重做。

    库文件落在项目内 `_tmp_tests/`（`tmp_path` 在部分沙箱环境不可写），
    且**不主动删除**——删除会触发沙箱的批量删除守卫。
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401  触发全部模型注册
    from app.models.base import Base

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"producer_{uuid.uuid4().hex[:8]}.db"

    engine = create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    yield engine
    asyncio.run(engine.dispose())


@pytest.fixture
def db_ctx(_engine):
    """函数级会话工厂；每个用例前清空业务表，保证用例间互不污染。

    清表而非重建库：单条 `DELETE` 是毫秒级，重建库是秒级。
    SQLite 默认不启用外键约束，因此删除顺序不影响执行；仍按
    「子表 -> 父表」书写，以便将来开启 `PRAGMA foreign_keys=ON` 时不必改。
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(_engine, expire_on_commit=False)

    async def _clean():
        async with _engine.begin() as conn:
            for table in ("notifications", "review_records", "reviews", "cases", "users"):
                await conn.execute(text(f"DELETE FROM {table}"))

    asyncio.run(_clean())
    return factory


def _run(coro):
    return asyncio.run(coro)


async def _all_notifications(s) -> list[Any]:
    from sqlalchemy import select

    from app.models.notification import Notification

    return list((await s.execute(select(Notification).order_by(Notification.id))).scalars().all())


async def _make_user(s, *, username: str, full_name: str, role):
    from app.models.identity import User

    u = User(
        tenant_id=TENANT_A,
        username=username,
        hashed_password="x",
        full_name=full_name,
        role=role,
    )
    s.add(u)
    await s.flush()
    return u


async def _make_case(s, *, case_no: str, title: str, client_user_id=None, lawyer_id=None):
    from app.models.case import Case

    c = Case(
        tenant_id=TENANT_A,
        case_no=case_no,
        title=title,
        client_user_id=client_user_id,
        lawyer_id=lawyer_id,
    )
    s.add(c)
    await s.flush()
    return c


# ═══════════ A. 额度预警阈值：纯函数，无需 DB ═══════════


def test_quota_warn_threshold_is_ceiling_not_floor():
    """**核心用例**：额度 3 时阈值必须是 3，而不是 `int(3*0.8)=2`。

    向下取整会把阈值压到「额度已耗尽」的位置，预警**永远不触发**：
    额度 3 次的套餐，第 2 次扣减时就该提醒（还剩 1 次），
    而 `int(2.4)=2` 意味着「用到第 2 次提醒」看似相同，实则
    在额度 1、2 时彻底失效（阈值 = 额度 = 耗尽点，已由工单通知覆盖）。
    """
    from app.services.billing_service import _quota_warn_threshold

    assert _quota_warn_threshold(3) == 3  # int(2.4)=2 是错的
    assert _quota_warn_threshold(2) == 2  # int(1.6)=1 是错的
    assert _quota_warn_threshold(1) == 1  # int(0.8)=0 会让预警完全不触发


def test_quota_warn_threshold_common_limits():
    from app.services.billing_service import _quota_warn_threshold

    assert _quota_warn_threshold(100) == 80
    assert _quota_warn_threshold(10) == 8
    assert _quota_warn_threshold(5) == 4
    assert _quota_warn_threshold(4) == 4  # ceil(3.2)


def test_quota_warn_threshold_never_exceeds_limit():
    """阈值必须落在 `[1, limit]` 内：超出 limit 等于永不触发。"""
    from app.services.billing_service import _quota_warn_threshold

    for limit in range(1, 500):
        t = _quota_warn_threshold(limit)
        assert 1 <= t <= limit, f"limit={limit} threshold={t}"


def test_quota_warn_threshold_unlimited_and_invalid():
    """`limit<=0` 表示不限量，没有「额度不足」的概念。"""
    from app.services.billing_service import _quota_warn_threshold

    assert _quota_warn_threshold(0) == 0
    assert _quota_warn_threshold(-7) == 0


# ═══════════ B. 额度预警：跨阈值语义（反噪音） ═══════════


def _quota(limit: int, used: int):
    """构造内存中的额度行（不落库，纯逻辑测试）。"""
    from app.models.billing import UsageQuota
    from app.models.enums import UsageType

    return UsageQuota(
        id=1,
        tenant_id=TENANT_A,
        usage_type=UsageType.QA,
        period="2026-09",
        limit_count=limit,
        used_count=used,
    )


def _spy_notify():
    """替换 `billing_service` 模块内的 `notify`，返回记录列表与 patch 上下文。"""
    calls: list[dict] = []
    fake = AsyncMock(side_effect=lambda *a, **kw: calls.append(kw))
    return calls, patch("app.services.billing_service.notify", new=fake)


async def _warn(svc, *, limit: int, used_before: int, user_id=CLIENT_ID):
    from app.models.enums import UsageType

    await svc._maybe_warn_quota(
        tenant_id=TENANT_A,
        user_id=user_id,
        q=_quota(limit, used_before + 1),
        used_before=used_before,
        usage_type=UsageType.QA,
        ref_type="conversation",
        ref_id=88,
    )


def test_quota_warning_fires_on_threshold_crossing():
    """`used_before=79 -> used_after=80`（额度 100）必须发一条。"""
    from app.models.enums import NotificationType
    from app.services.billing_service import BillingService

    calls, p = _spy_notify()
    with p:
        _run(_warn(BillingService(None), limit=100, used_before=79))

    assert len(calls) == 1
    assert calls[0]["type"] == NotificationType.QUOTA_WARNING
    assert calls[0]["user_id"] == CLIENT_ID
    assert calls[0]["tenant_id"] == TENANT_A
    assert "80/100" in calls[0]["content"]
    assert calls[0]["ref_type"] == "conversation"
    assert calls[0]["payload"]["usage_type"] == "QA"


def test_quota_warning_does_not_refire_inside_warning_zone():
    """**反噪音核心用例**：已在预警区内继续扣减，一条都不能再发。

    这正是「低于阈值就发」写法会踩的坑：额度剩 20 次、用户连用 19 次，
    会收到 19 条「额度不足」。此用例把这个退化行为钉死。
    """
    from app.services.billing_service import BillingService

    calls, p = _spy_notify()
    with p:
        svc = BillingService(None)
        for used_before in range(80, 100):  # 81 -> 100，全程都在预警区内
            _run(_warn(svc, limit=100, used_before=used_before))

    assert calls == [], f"预警区内重复推送了 {len(calls)} 条"


def test_quota_warning_fires_exactly_once_across_full_consumption():
    """模拟一个账期从 0 用到 100：**恰好**一条预警，且落在第 80 次。

    这是本组最强的一条断言——它同时覆盖「该发时发」与「不该发时不发」，
    任何阈值偏移（79 / 81）或重复触发都会让它失败。
    """
    from app.services.billing_service import BillingService

    calls, p = _spy_notify()
    with p:
        svc = BillingService(None)
        for used_before in range(0, 100):  # 1 -> 100
            _run(_warn(svc, limit=100, used_before=used_before))

    assert len(calls) == 1, f"期望恰好 1 条预警，实际 {len(calls)} 条"
    assert "80/100" in calls[0]["content"]


def test_quota_warning_respects_small_limit_ceiling():
    """额度 3：预警必须落在第 3 次（阈值=3），而不是第 2 次。"""
    from app.services.billing_service import BillingService

    calls, p = _spy_notify()
    with p:
        svc = BillingService(None)
        for used_before in range(0, 3):  # 1 -> 3
            _run(_warn(svc, limit=3, used_before=used_before))

    assert len(calls) == 1
    assert "3/3" in calls[0]["content"]


def test_quota_warning_skipped_for_unlimited_plan():
    """`limit_count<=0` = 不限量套餐，不该有「额度不足」通知。"""
    from app.services.billing_service import BillingService

    calls, p = _spy_notify()
    with p:
        svc = BillingService(None)
        for used_before in range(0, 50):
            _run(_warn(svc, limit=0, used_before=used_before))

    assert calls == []


def test_quota_warning_skipped_without_recipient():
    """租户级批量扣费没有具体用户上下文时不写通知（写入侧哨兵）。"""
    from app.services.billing_service import BillingService

    calls, p = _spy_notify()
    with p:
        _run(_warn(BillingService(None), limit=100, used_before=79, user_id=None))

    assert calls == []


# ═══════════ C. CASE_ACCEPTED：接单后通知客户 ═══════════


def test_accept_notifies_client_with_lawyer_real_name(db_ctx):
    """**收件人用例**：接单通知发给客户，且带上律师真名。"""
    from app.core.rbac import Role
    from app.models.enums import NotificationType
    from app.services.dispatch_service import DispatchService

    async def _go():
        async with db_ctx() as s:
            lawyer = await _make_user(s, username="law1", full_name="王律师", role=Role.LAWYER)
            client = await _make_user(s, username="cli1", full_name="张三", role=Role.CLIENT)
            case = await _make_case(
                s, case_no="C-1", title="张三诉李四买卖合同纠纷",
                client_user_id=client.id, lawyer_id=lawyer.id,
            )

            await DispatchService(s)._notify_accepted(case, lawyer.id)
            await s.commit()

            rows = await _all_notifications(s)
            assert len(rows) == 1
            n = rows[0]
            assert n.type == NotificationType.CASE_ACCEPTED
            # 收件人是客户，不是刚点完「接单」的律师
            assert n.user_id == client.id
            assert n.user_id != lawyer.id
            assert n.tenant_id == TENANT_A
            assert "王律师" in n.content
            assert "张三诉李四买卖合同纠纷" in n.content
            assert (n.ref_type, n.ref_id) == ("case", case.id)
            assert n.is_read == 0

    _run(_go())


def test_accept_notification_falls_back_when_lawyer_row_missing(db_ctx):
    """律师行不存在时文案兜底为「承办律师」，**不得抛异常**。

    通知是旁路：它失败绝不能把「接单」这个主流程带崩。
    """
    from app.core.rbac import Role
    from app.services.dispatch_service import DispatchService

    async def _go():
        async with db_ctx() as s:
            client = await _make_user(s, username="cli1", full_name="张三", role=Role.CLIENT)
            case = await _make_case(
                s, case_no="C-2", title="劳动争议",
                client_user_id=client.id, lawyer_id=STRANGER_ID,  # 无对应用户行
            )

            await DispatchService(s)._notify_accepted(case, STRANGER_ID)
            await s.commit()

            rows = await _all_notifications(s)
            assert len(rows) == 1
            assert "承办律师" in rows[0].content

    _run(_go())


def test_accept_skips_case_without_client_account(db_ctx):
    """线下录入的案件没有客户端账号 -> 无接收人 -> 不写。

    这正是写入侧「拒绝无接收人通知」规则的第一个受益点：
    写进去也永远没人能读到（读路径按 user_id 过滤）。
    """
    from app.core.rbac import Role
    from app.services.dispatch_service import DispatchService

    async def _go():
        async with db_ctx() as s:
            lawyer = await _make_user(s, username="law1", full_name="王律师", role=Role.LAWYER)
            case = await _make_case(
                s, case_no="C-3", title="线下案件",
                client_user_id=None, lawyer_id=lawyer.id,
            )

            await DispatchService(s)._notify_accepted(case, lawyer.id)
            await s.commit()

            assert await _all_notifications(s) == []

    _run(_go())


def test_accept_does_not_self_notify(db_ctx):
    """律师本人即客户（自助办案）时不自推「你已接单」。"""
    from app.core.rbac import Role
    from app.services.dispatch_service import DispatchService

    async def _go():
        async with db_ctx() as s:
            me = await _make_user(s, username="solo", full_name="李律师", role=Role.LAWYER)
            case = await _make_case(
                s, case_no="C-4", title="自助办案",
                client_user_id=me.id, lawyer_id=me.id,
            )

            await DispatchService(s)._notify_accepted(case, me.id)
            await s.commit()

            assert await _all_notifications(s) == []

    _run(_go())


# ═══════════ D. REVIEW_DECIDED：复核出结论通知承办律师 ═══════════


async def _make_review(s, *, case_id, required_level):
    from app.models.enums import ReviewStatus, ReviewTargetType
    from app.models.review import Review

    r = Review(
        tenant_id=TENANT_A,
        target_type=ReviewTargetType.CASE_ANALYSIS,
        target_id=1,
        case_id=case_id,
        status=ReviewStatus.PENDING_CONFIRM,
        required_level=required_level,
    )
    s.add(r)
    await s.flush()
    return r


def test_review_decided_notifies_assigned_lawyer(db_ctx):
    """`REVISION` 结论 -> 承办律师收到，且 ref 指向**复核**而非案件。"""
    from app.core.rbac import Role
    from app.models.enums import NotificationType, ReviewLevel
    from app.services.review_service import ReviewService

    async def _go():
        async with db_ctx() as s:
            lawyer = await _make_user(s, username="law1", full_name="王律师", role=Role.LAWYER)
            reviewer = await _make_user(s, username="rev1", full_name="赵复核", role=Role.FIRM_ADMIN)
            case = await _make_case(
                s, case_no="C-5", title="民间借贷纠纷", lawyer_id=lawyer.id
            )
            r = await _make_review(s, case_id=case.id, required_level=ReviewLevel.L2)

            await ReviewService(s)._notify_decided(
                r, actor=reviewer, comment="第 3 段法条引用有误", outcome="REVISION"
            )
            await s.commit()

            rows = await _all_notifications(s)
            assert len(rows) == 1
            n = rows[0]
            assert n.type == NotificationType.REVIEW_DECIDED
            assert n.user_id == lawyer.id          # 承办律师，不是复核人
            assert n.user_id != reviewer.id
            assert "要求修改" in n.content
            assert "第 3 段法条引用有误" in n.content
            assert "赵复核" in n.content
            # ref 指向复核任务：律师要处理的是这次复核，少一次跳转
            assert (n.ref_type, n.ref_id) == ("review", r.id)
            assert n.payload["outcome"] == "REVISION"
            assert n.payload["case_id"] == case.id

    _run(_go())


def test_review_decided_rejected_mentions_voided(db_ctx):
    """`REJECTED` 有实质后果（作废），必须通知。"""
    from app.core.rbac import Role
    from app.models.enums import ReviewLevel
    from app.services.review_service import ReviewService

    async def _go():
        async with db_ctx() as s:
            lawyer = await _make_user(s, username="law1", full_name="王律师", role=Role.LAWYER)
            reviewer = await _make_user(s, username="rev1", full_name="赵复核", role=Role.FIRM_ADMIN)
            case = await _make_case(s, case_no="C-6", title="合同纠纷", lawyer_id=lawyer.id)
            r = await _make_review(s, case_id=case.id, required_level=ReviewLevel.L2)

            await ReviewService(s)._notify_decided(
                r, actor=reviewer, comment=None, outcome="REJECTED"
            )
            await s.commit()

            rows = await _all_notifications(s)
            assert len(rows) == 1
            assert "作废" in rows[0].content

    _run(_go())


def test_review_decided_confirmed_is_forward_signal(db_ctx):
    """`CONFIRMED` 是推进信号（可定稿归档），通知文案要体现。"""
    from app.core.rbac import Role
    from app.models.enums import ReviewLevel
    from app.services.review_service import ReviewService

    async def _go():
        async with db_ctx() as s:
            lawyer = await _make_user(s, username="law1", full_name="王律师", role=Role.LAWYER)
            reviewer = await _make_user(s, username="rev1", full_name="赵复核", role=Role.FIRM_ADMIN)
            case = await _make_case(s, case_no="C-7", title="侵权纠纷", lawyer_id=lawyer.id)
            r = await _make_review(s, case_id=case.id, required_level=ReviewLevel.L2)

            await ReviewService(s)._notify_decided(
                r, actor=reviewer, comment=None, outcome="CONFIRMED"
            )
            await s.commit()

            rows = await _all_notifications(s)
            assert len(rows) == 1
            assert "可以定稿归档" in rows[0].content

    _run(_go())


def test_review_decided_skips_self_review(db_ctx):
    """复核人就是承办律师（律师复核自己的产出）时不自推。"""
    from app.core.rbac import Role
    from app.models.enums import ReviewLevel
    from app.services.review_service import ReviewService

    async def _go():
        async with db_ctx() as s:
            lawyer = await _make_user(s, username="law1", full_name="王律师", role=Role.LAWYER)
            case = await _make_case(s, case_no="C-8", title="自审案件", lawyer_id=lawyer.id)
            r = await _make_review(s, case_id=case.id, required_level=ReviewLevel.L2)

            await ReviewService(s)._notify_decided(
                r, actor=lawyer, comment=None, outcome="REVISION"
            )
            await s.commit()

            assert await _all_notifications(s) == []

    _run(_go())


def test_review_decided_skips_case_without_lawyer(db_ctx):
    """案件尚未分配律师 -> 无接收人 -> 不写。"""
    from app.core.rbac import Role
    from app.models.enums import ReviewLevel
    from app.services.review_service import ReviewService

    async def _go():
        async with db_ctx() as s:
            reviewer = await _make_user(s, username="rev1", full_name="赵复核", role=Role.FIRM_ADMIN)
            case = await _make_case(s, case_no="C-9", title="未分配案件", lawyer_id=None)
            r = await _make_review(s, case_id=case.id, required_level=ReviewLevel.L2)

            await ReviewService(s)._notify_decided(
                r, actor=reviewer, comment=None, outcome="REVISION"
            )
            await s.commit()

            assert await _all_notifications(s) == []

    _run(_go())


def test_review_decided_skips_orphan_review(db_ctx):
    """`case_id` 为空（如独立的文书复核）时直接返回，不得抛异常。"""
    from app.core.rbac import Role
    from app.models.enums import ReviewLevel
    from app.services.review_service import ReviewService

    async def _go():
        async with db_ctx() as s:
            reviewer = await _make_user(s, username="rev1", full_name="赵复核", role=Role.FIRM_ADMIN)
            r = await _make_review(s, case_id=None, required_level=ReviewLevel.L2)

            await ReviewService(s)._notify_decided(
                r, actor=reviewer, comment=None, outcome="CONFIRMED"
            )
            await s.commit()

            assert await _all_notifications(s) == []

    _run(_go())


def test_review_decided_truncates_long_comment(db_ctx):
    """复核意见超长时截断到 80 字 + 省略号，避免通知正文撑爆列表。"""
    from app.core.rbac import Role
    from app.models.enums import ReviewLevel
    from app.services.review_service import ReviewService

    async def _go():
        async with db_ctx() as s:
            lawyer = await _make_user(s, username="law1", full_name="王律师", role=Role.LAWYER)
            reviewer = await _make_user(s, username="rev1", full_name="赵复核", role=Role.FIRM_ADMIN)
            case = await _make_case(s, case_no="C-10", title="长意见案件", lawyer_id=lawyer.id)
            r = await _make_review(s, case_id=case.id, required_level=ReviewLevel.L2)

            long_comment = "意" * 200
            await ReviewService(s)._notify_decided(
                r, actor=reviewer, comment=long_comment, outcome="REVISION"
            )
            await s.commit()

            rows = await _all_notifications(s)
            assert len(rows) == 1
            content = rows[0].content
            assert "…" in content
            assert "意" * 81 not in content   # 截断生效
            assert "意" * 80 in content
            # 完整意见仍在 payload 里，前端可展开查看
            assert rows[0].payload["comment"] == long_comment

    _run(_go())


def test_partial_approval_does_not_notify(db_ctx):
    """**信噪比核心用例**：`APPROVED` 但级别不足时**不发**通知。

    走 `decide()` 全流程（而非直接调 `_notify_decided`），因为「部分通过
    不通知」这个决策点在 `decide()` 的分支里，而不是在通知函数内部。

    理由：L2 通过、还等 L3 终审时，承办律师**无事可做**。一条不需要
    任何行动的通知，代价不是它占的那一行，而是它稀释了「待接单」
    「待复核」这些真通知的注意力。
    """
    from app.core.rbac import Role
    from app.models.enums import ReviewDecision, ReviewLevel, ReviewStatus
    from app.services.review_service import ReviewService

    async def _go():
        async with db_ctx() as s:
            lawyer = await _make_user(s, username="law1", full_name="王律师", role=Role.LAWYER)
            # LAWYER 且无 LawyerProfile -> resolve_actor_level 返回 L2
            reviewer = await _make_user(s, username="rev2", full_name="钱律师", role=Role.LAWYER)
            case = await _make_case(s, case_no="C-11", title="需 L3 终审", lawyer_id=lawyer.id)
            r = await _make_review(s, case_id=case.id, required_level=ReviewLevel.L3)

            out = await ReviewService(s).decide(
                r.id, actor=reviewer, decision=ReviewDecision.APPROVED, comment="我这边没问题"
            )
            await s.commit()

            assert out.status == ReviewStatus.PENDING_CONFIRM  # 未定稿，等 L3
            assert await _all_notifications(s) == [], "部分通过不该产生通知"

    _run(_go())


def test_full_approval_does_notify(db_ctx):
    """对照用例：达到要求级别 -> 定稿 -> **必须**发通知。"""
    from app.core.rbac import Role
    from app.models.enums import ReviewDecision, ReviewLevel, ReviewStatus
    from app.services.review_service import ReviewService

    async def _go():
        async with db_ctx() as s:
            lawyer = await _make_user(s, username="law1", full_name="王律师", role=Role.LAWYER)
            admin = await _make_user(s, username="adm1", full_name="孙合伙人", role=Role.FIRM_ADMIN)
            case = await _make_case(s, case_no="C-12", title="L3 终审案件", lawyer_id=lawyer.id)
            r = await _make_review(s, case_id=case.id, required_level=ReviewLevel.L2)

            out = await ReviewService(s).decide(
                r.id, actor=admin, decision=ReviewDecision.APPROVED, comment="通过"
            )
            await s.commit()

            assert out.status == ReviewStatus.CONFIRMED
            rows = await _all_notifications(s)
            assert len(rows) == 1
            assert "可以定稿归档" in rows[0].content
            assert rows[0].payload["outcome"] == "CONFIRMED"

    _run(_go())


# ═══════════ E. EVIDENCE_MISSING：关键材料缺失 ═══════════


def _checklist_items():
    return [
        {"item": "借款合同", "category": "CONTRACT", "priority": 1,
         "description": None, "missing": True},
        {"item": "转账凭证", "category": "PAYMENT", "priority": 1,
         "description": None, "missing": True},
        {"item": "聊天记录", "category": "COMMUNICATION", "priority": 2,
         "description": None, "missing": True},
        {"item": "身份证", "category": "IDENTITY", "priority": 1,
         "description": None, "missing": False},  # 已上传，不算缺
    ]


def test_missing_key_evidence_notifies_lawyer_with_only_priority1(db_ctx):
    """只推 `priority==1` 且 `missing` 的项；已上传/次要项不得混入。"""
    from app.core.rbac import Role
    from app.models.enums import NotificationType
    from app.services.evidence_service import EvidenceService
    from app.services.job_handlers import _notify_missing_key_evidence

    async def _go():
        async with db_ctx() as s:
            lawyer = await _make_user(s, username="law1", full_name="王律师", role=Role.LAWYER)
            case = await _make_case(
                s, case_no="C-13", title="民间借贷纠纷", lawyer_id=lawyer.id
            )
            ev = SimpleNamespace(id=555, case_id=case.id)

            with patch.object(
                EvidenceService, "missing_list", new=AsyncMock(return_value=_checklist_items())
            ):
                await _notify_missing_key_evidence(s, ev)
            await s.commit()

            rows = await _all_notifications(s)
            assert len(rows) == 1
            n = rows[0]
            assert n.type == NotificationType.EVIDENCE_MISSING
            assert n.user_id == lawyer.id
            assert "借款合同" in n.content and "转账凭证" in n.content
            assert "聊天记录" not in n.content   # priority=2 不推
            assert "身份证" not in n.content     # 已上传，不算缺
            assert n.payload["key_missing"] == ["借款合同", "转账凭证"]
            assert n.payload["trigger_evidence_id"] == 555
            assert (n.ref_type, n.ref_id) == ("case", case.id)

    _run(_go())


def test_missing_key_evidence_skips_when_only_secondary_missing(db_ctx):
    """只有次要材料缺失时不打扰律师——一条通知塞 12 个待办等于没有重点。"""
    from app.core.rbac import Role
    from app.services.evidence_service import EvidenceService
    from app.services.job_handlers import _notify_missing_key_evidence

    async def _go():
        async with db_ctx() as s:
            lawyer = await _make_user(s, username="law1", full_name="王律师", role=Role.LAWYER)
            case = await _make_case(s, case_no="C-14", title="次要材料缺", lawyer_id=lawyer.id)
            ev = SimpleNamespace(id=556, case_id=case.id)

            secondary_only = [
                {"item": "聊天记录", "category": "COMMUNICATION", "priority": 2,
                 "description": None, "missing": True},
                {"item": "补充说明", "category": "OTHER", "priority": 3,
                 "description": None, "missing": True},
            ]
            with patch.object(
                EvidenceService, "missing_list", new=AsyncMock(return_value=secondary_only)
            ):
                await _notify_missing_key_evidence(s, ev)
            await s.commit()

            assert await _all_notifications(s) == []

    _run(_go())


def test_missing_key_evidence_skips_case_without_lawyer(db_ctx):
    from app.services.evidence_service import EvidenceService
    from app.services.job_handlers import _notify_missing_key_evidence

    async def _go():
        async with db_ctx() as s:
            case = await _make_case(s, case_no="C-15", title="未分配", lawyer_id=None)
            ev = SimpleNamespace(id=557, case_id=case.id)

            with patch.object(
                EvidenceService, "missing_list", new=AsyncMock(return_value=_checklist_items())
            ):
                await _notify_missing_key_evidence(s, ev)
            await s.commit()

            assert await _all_notifications(s) == []

    _run(_go())


def test_missing_key_evidence_summarizes_more_than_three(db_ctx):
    """超过 3 项关键缺失时折叠为「等 N 项」，正文保持一行可读。"""
    from app.core.rbac import Role
    from app.services.evidence_service import EvidenceService
    from app.services.job_handlers import _notify_missing_key_evidence

    async def _go():
        async with db_ctx() as s:
            lawyer = await _make_user(s, username="law1", full_name="王律师", role=Role.LAWYER)
            case = await _make_case(s, case_no="C-16", title="多项缺失", lawyer_id=lawyer.id)
            ev = SimpleNamespace(id=558, case_id=case.id)

            many = [
                {"item": f"材料{i}", "category": "OTHER", "priority": 1,
                 "description": None, "missing": True}
                for i in range(1, 7)
            ]
            with patch.object(
                EvidenceService, "missing_list", new=AsyncMock(return_value=many)
            ):
                await _notify_missing_key_evidence(s, ev)
            await s.commit()

            rows = await _all_notifications(s)
            assert len(rows) == 1
            content = rows[0].content
            assert "材料1、材料2、材料3" in content
            assert "等 6 项" in content
            assert "材料4" not in content          # 只展示前 3 项
            assert len(rows[0].payload["key_missing"]) == 6  # 完整清单进 payload

    _run(_go())


def test_evidence_parse_worker_notifies_after_category_is_written(db_ctx):
    """**顺序用例**：通知必须在 `category` 落库**之后**触发。

    若把检查点放在上传接口（那一刻 `ev.category` 还是空值），
    `missing_list()` 会把刚上传的这份材料也算成「缺」——用户刚做完
    一个动作，系统立刻说他没做。这是最伤信任的假警报类型。

    这里通过 spy 断言：被调用时 `category` 已就位，且 `step_state`
    已回写（说明赋值发生在调用之前）。
    """
    from app.models.enums import EvidenceCategory
    from app.services import job_handlers
    from app.services.evidence_service import EvidenceService

    seen: dict[str, Any] = {}
    holder: dict[str, Any] = {}

    async def _spy(db, ev):
        job = holder["job"]
        seen["category"] = ev.category
        seen["step_state_category"] = job.step_state.get("category")
        seen["progress"] = job.progress

    async def _go():
        async with db_ctx() as s:
            holder["job"] = SimpleNamespace(
                ref_id=99, tenant_id=TENANT_A, step_state={},
                step_name=None, progress=0,
            )
            fake_ev = SimpleNamespace(id=99, case_id=1, category=EvidenceCategory.CONTRACT)

            with patch.object(
                EvidenceService, "parse", new=AsyncMock(return_value=fake_ev)
            ), patch.object(
                job_handlers, "_notify_missing_key_evidence", new=_spy
            ):
                await job_handlers._evidence_parse_worker(holder["job"], s)

    _run(_go())

    assert seen["category"] == EvidenceCategory.CONTRACT
    assert seen["step_state_category"] == "CONTRACT"
    assert seen["progress"] == 100, "通知必须在进度置 100（即解析完成）之后才发"
    assert holder["job"].step_state["parsed"] is True


def test_evidence_parse_worker_skips_notify_when_already_parsed(db_ctx):
    """断点续跑：已解析的任务不得重复推送材料缺失通知。"""
    from app.services import job_handlers
    from app.services.evidence_service import EvidenceService

    called: list[int] = []

    async def _spy(db, ev):
        called.append(ev.id)

    async def _go():
        async with db_ctx() as s:
            job = SimpleNamespace(
                ref_id=99, tenant_id=TENANT_A,
                step_state={"parsed": True, "category": "CONTRACT"},
                step_name=None, progress=0,
            )
            with patch.object(
                EvidenceService, "parse", new=AsyncMock(side_effect=AssertionError("不该重新解析"))
            ), patch.object(
                job_handlers, "_notify_missing_key_evidence", new=_spy
            ):
                await job_handlers._evidence_parse_worker(job, s)
            assert job.progress == 100

    _run(_go())
    assert called == [], "已解析的任务重复推送了通知"


# ═══════════ F. 缺口哨兵：DOCUMENT_CONFIRMED ═══════════


def test_document_confirmed_still_has_no_producer():
    """**缺口哨兵**：`DOCUMENT_CONFIRMED` 至今没有生产者，且这不是通知系统的锅。

    排查发现 `DocumentStatus.CONFIRMED` 在全库（`app/` 与 `tests/`）
    **零引用**——「文书确认定稿」这条业务流本身尚未实现。

    给一个不存在的流程补 `notify(...)` 会制造两样东西：一段永不触发的
    死代码，和一句「9 种通知已全部打通」的假话。因此本轮**故意不接线**，
    把它记为独立的业务功能缺口，而不是通知系统的缺口。

    本用例是回归哨兵：将来实现了文书确认流并接线后，它会失败，
    提醒把 DOCUMENT_CONFIRMED 从缺口清单移到已覆盖清单。

    扫描范围**只含 `app/`**（生产代码）：本文件自身的说明文字里就写着
    这个字面量，若一并扫描会自我命中，哨兵将永远为红。
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    hits: list[str] = []
    for p in (root / "app").rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        if "DocumentStatus.CONFIRMED" in text:
            hits.append(str(p.relative_to(root)).replace("\\", "/"))

    assert hits == [], (
        "检测到 DocumentStatus.CONFIRMED 的引用："
        f"{hits}。这说明文书确认流已实现——请为 DOCUMENT_CONFIRMED "
        "补上写入点，并把本用例改写为断言该通知会被发出。"
    )


def test_document_flow_stops_before_confirmation():
    """**缺口量化**：文书状态机只实现了前半段（DRAFT/COLLECTING/GENERATED）。

    `DOCUMENT_CONFIRMED` 没有生产者不是遗漏，而是**下游流程不存在**：
    `document_service.py` 从不写 IN_REVIEW / CONFIRMED / EXPORTED / VOIDED。
    也就是说文书生成出来后，既不能送审、也不能确认、更不能导出。

    这条断言把缺口的**边界**固定下来——当 IN_REVIEW 或 CONFIRMED
    开始被赋值时它会失败，提示下游流程已落地、该评估通知接入了。
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    src = (root / "app" / "services" / "document_service.py").read_text(encoding="utf-8")

    implemented = {s for s in ("DRAFT", "COLLECTING", "GENERATED") if f"DocumentStatus.{s}" in src}
    missing = {
        s for s in ("IN_REVIEW", "CONFIRMED", "EXPORTED", "VOIDED")
        if f"DocumentStatus.{s}" not in src
    }

    assert implemented == {"DRAFT", "COLLECTING", "GENERATED"}
    assert missing == {"IN_REVIEW", "CONFIRMED", "EXPORTED", "VOIDED"}, (
        "文书状态机已向下游推进（实现了 "
        f"{sorted(set('IN_REVIEW CONFIRMED EXPORTED VOIDED'.split()) - missing)}），"
        "请重新评估 DOCUMENT_CONFIRMED 通知的接入点。"
    )
