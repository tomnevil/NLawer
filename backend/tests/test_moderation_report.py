"""合规上报链路（第十四条）的判据（2026-09-20）。

## 为什么这个文件必须存在

《生成式人工智能服务管理暂行办法》第十四条要求：发现违法内容应当**采取处置措施**
并**向有关主管部门报告**。产品里对应的实现是：

- `app/core/moderation.py::report_to_authority()`（上报钩子）
- `app/services/moderation_service.py::_persist()` 里的上报状态判定

清查发现这一整条链路**零判据**：

| 关键词 | 在 `tests/` 下的命中数 |
|--------|----------------------|
| `report_to_authority` | **0** |
| `MODERATION_REPORT_ENABLED` / `MODERATION_REPORT_URL` | **0 / 0** |
| `report_status` | **0** |
| `ReportStatus` | **0** |

`test_moderation_enforcement.py`（P0-13，7 条）覆盖的是**拦截执行链**
（`blocked` 是否为真、留痕、审计、指标），**没有一条碰上报状态**。
⇒ 把 `_persist` 里的上报判定整段删掉，那 7 条会**全部保持绿**。

## 本轮顺带修掉的一个语义错配

`ReportStatus` 定义了四个值，但服务层**从来不用 `FAILED`**：

```python
reported = await report_to_authority(...)
if reported:
    report_status = REPORTED
# 否则……一直停在 PENDING
```

而 `PENDING` 的语义是「**需上报但通道未配置** → 人工兜底」。
⇒ 通道**配好了**却调用失败时，状态也落 `PENDING`，
运维按「通道没配」去处理 ⇒ statutory 上报被延误，且**不报错**（静默错分）。

现已改成：通道已配但失败 ⇒ `FAILED`；通道未配置 ⇒ `PENDING`。

## 覆盖清单

| 编号 | 性质 |
|------|------|
| R1 | BLOCK 级 + 通道未配置 ⇒ `PENDING`（**绝不能**是 `NOT_REQUIRED`＝静默丢弃） |
| R2 | ESCALATE 级 ⇒ 同上 |
| R3 | **反向量**：PASS / REVIEW 级 ⇒ `NOT_REQUIRED`（防把人工队列淹没） |
| R4 | `report_to_authority` 未配置 ⇒ 返回 `False` **且打 warning**（绝不静默） |
| R5 | 已配通道 + 调用**失败** ⇒ `FAILED`，**不是** `PENDING` |
| R6 | 已配通道 + 调用成功 ⇒ `REPORTED` |
| R7 | **假上报**：调用失败时**不得**标成 `REPORTED` |
| R8 | 上报失败**不得**把业务请求打挂（`report_to_authority` 吞异常、不向上抛） |
"""
from __future__ import annotations

import pathlib
import uuid

import pytest
from sqlalchemy import select

TENANT = "mod-report-tenant"


# ═══════════════════════ 夹具 ═══════════════════════


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"mod_report_{uuid.uuid4().hex[:8]}.db"
    return create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)


@pytest.fixture(scope="module")
def seeded(engine):
    import asyncio

    import app.models  # noqa: F401
    from app.models.base import Base

    async def _seed():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_seed())
    return True


@pytest.fixture()
async def db(engine, seeded):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest.fixture()
def detached_points_to_test_db(engine, monkeypatch):
    """把审计/留痕的「独立会话工厂」指到本模块的测试库。

    🚨 与 `test_moderation_enforcement.py` 同款陷阱：拦截留痕走独立会话，
    它用 `settings.DATABASE_URL` 建自己的 engine；不指向测试库的话
    「留痕其实成功」时判据红、「留痕被破坏」时判据绿（方向整个反过来）。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    import app.core.audit as audit_mod

    monkeypatch.setattr(
        audit_mod,
        "_detached_factory",
        async_sessionmaker(engine, expire_on_commit=False),
        raising=False,
    )


def _result(level):
    from app.core.moderation import Hit, ModerationCategory, ModerationResult

    return ModerationResult(
        level=level,
        hits=[Hit(ModerationCategory.POLITICAL, level, "命中词")],
        side="input",
    )


async def _persist(db, level):
    from app.services.moderation_service import ModerationService

    await ModerationService(db)._persist(
        _result(level),
        text="测试文本",
        scene="qa",
        actor=None,
        resource_type=None,
        resource_id=None,
        tenant_id=TENANT,
    )


async def _last_status(engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models.moderation import ModerationRecord

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        rows = (await s.execute(select(ModerationRecord))).scalars().all()
    assert rows, "一条审核记录都没落库 ⇒ 留痕本身失效了"
    return rows[-1]


# ═══════════════════════ R1–R3 状态判定 ═══════════════════════


@pytest.mark.parametrize("level_name", ["BLOCK", "ESCALATE"])
async def test_reportable_levels_are_never_marked_not_required(
    engine, db, detached_points_to_test_db, level_name
):
    """R1/R2：BLOCK / ESCALATE 级**绝不能**落成 `NOT_REQUIRED`。

    `NOT_REQUIRED` 的语义是「未达上报门槛」。违法内容落这个值
    ⇒ 上报义务被**静默丢弃**：不报错、不告警、运维永远查不到欠报了多少笔。
    """
    from app.core.moderation import ModerationLevel

    await _persist(db, ModerationLevel[level_name])
    row = await _last_status(engine)

    from app.models.moderation import ReportStatus

    assert row.report_status != ReportStatus.NOT_REQUIRED.value, (
        f"{level_name} 级被标成 NOT_REQUIRED ⇒ 上报义务被静默丢弃"
    )
    assert row.report_status == ReportStatus.PENDING.value, (
        f"通道未配置时 {level_name} 级应为 PENDING（人工兜底），实际 {row.report_status}"
    )
    assert row.report_note, "欠报时必须留下备注，否则人工兜底无从下手"


@pytest.mark.parametrize("level_name", ["PASS", "REVIEW"])
async def test_non_reportable_levels_are_not_required(
    engine, db, detached_points_to_test_db, level_name
):
    """R3：**反向量**——未达门槛的级别**不得**标成待上报。

    没有这条，「凡是命中一律标 PENDING」也能让 R1/R2 全绿，
    后果是人工补报队列被海量 REVIEW 级内容淹没 ⇒ 真正欠报的反而没人看。
    """
    from app.core.moderation import ModerationLevel
    from app.models.moderation import ReportStatus

    await _persist(db, ModerationLevel[level_name])
    await db.commit()  # 非 blocked 走请求会话，需显式提交才能读到

    row = await _last_status(engine)
    assert row.report_status == ReportStatus.NOT_REQUIRED.value, (
        f"{level_name} 级不应进入上报队列，实际 {row.report_status}"
    )


# ═══════════════════════ R4 `report_to_authority` 契约 ═══════════════════════


async def test_unconfigured_channel_returns_false_and_warns(monkeypatch):
    """R4：通道未配置 ⇒ 返回 `False` **且打 warning**。

    函数 docstring 写的是「**绝不能静默丢弃**」。只返回 False 不告警，
    调用方就会平平淡淡地继续跑 ⇒ 违法内容悄无声息地没上报。

    ⚠️ 本用例原先在签名里带了一个**根本没用到**的 `caplog`：
    项目用 loguru，告警是用 `logger.add(sink)` 抓的，caplog 全程没参与，
    却在 `-p no:logging` 下**直接 error**（`fixture 'caplog' not found`）。
    ⇒ 测试签名里不留没用的 fixture：它平时只是噪音，禁掉插件时变成假红。
    """
    from loguru import logger

    from app.config import settings
    from app.core.moderation import ModerationLevel, report_to_authority

    monkeypatch.setattr(settings, "MODERATION_REPORT_ENABLED", False, raising=False)
    monkeypatch.setattr(settings, "MODERATION_REPORT_URL", None, raising=False)

    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        ok = await report_to_authority(_result(ModerationLevel.BLOCK), subject="t1", period="")
    finally:
        logger.remove(sink_id)

    assert ok is False
    assert any("上报" in m for m in messages), (
        f"通道未配置却没有告警 ⇒ 上报被静默丢弃：{messages}"
    )


# ═══════════════════════ R5–R7 已配通道的成败分叉 ═══════════════════════


class _Resp:
    def __init__(self, ok: bool):
        self._ok = ok

    def raise_for_status(self):
        if not self._ok:
            raise RuntimeError("502 Bad Gateway")


class _FakeClient:
    """替身 httpx.AsyncClient：只关心**成败分叉**，不发真实请求。"""

    def __init__(self, ok: bool, calls: list):
        self._ok = ok
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        self.calls.append({"url": url, "json": json})
        return _Resp(self._ok)


@pytest.fixture()
def fake_httpx(monkeypatch):
    """把 `httpx.AsyncClient` 换成替身，记录调用次数。"""
    import sys
    import types

    calls: list = []
    ok = {"value": True}
    mod = types.ModuleType("httpx")

    def _client(*a, **kw):
        return _FakeClient(ok["value"], calls)

    mod.AsyncClient = _client
    monkeypatch.setitem(sys.modules, "httpx", mod)
    return {"calls": calls, "ok": ok}


async def test_configured_channel_failure_is_failed_not_pending(
    engine, db, monkeypatch, fake_httpx, detached_points_to_test_db
):
    """R5：通道**已配好**却调用失败 ⇒ 必须是 `FAILED`，**不是** `PENDING`。

    这是本轮修掉的语义错配。`PENDING` 的语义是「通道未配置」，
    用它表示「配了但没发成功」，运维会去配一个本来就配好了的通道，
    而真正该做的「重发 + 排查接口」没人做 ⇒ statutory 上报被延误。
    """
    from app.config import settings
    from app.core.moderation import ModerationLevel
    from app.models.moderation import ReportStatus

    monkeypatch.setattr(settings, "MODERATION_REPORT_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "MODERATION_REPORT_URL", "https://report.example/in", raising=False)
    fake_httpx["ok"]["value"] = False

    await _persist(db, ModerationLevel.BLOCK)
    row = await _last_status(engine)

    assert row.report_status == ReportStatus.FAILED.value, (
        f"通道已配但上报失败应落 FAILED（需重发），实际 {row.report_status}"
    )
    assert row.report_status != ReportStatus.PENDING.value, (
        "FAILED 被错分成 PENDING ⇒ 运维会按『通道没配』处理，延误 statutory 上报"
    )


async def test_configured_channel_success_is_reported(
    engine, db, monkeypatch, fake_httpx, detached_points_to_test_db
):
    """R6：**反向量**——通道已配且调用成功 ⇒ `REPORTED`，且备注清空。"""
    from app.config import settings
    from app.core.moderation import ModerationLevel
    from app.models.moderation import ReportStatus

    monkeypatch.setattr(settings, "MODERATION_REPORT_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "MODERATION_REPORT_URL", "https://report.example/in", raising=False)
    fake_httpx["ok"]["value"] = True

    await _persist(db, ModerationLevel.BLOCK)
    row = await _last_status(engine)

    assert row.report_status == ReportStatus.REPORTED.value, row.report_status
    assert row.report_note is None, "已上报却还留着『需人工补报』的备注，会误导运维"
    assert fake_httpx["calls"], "成功路径下上报通道一次都没被调用"


async def test_failure_never_fakes_a_reported_status(
    engine, db, monkeypatch, fake_httpx, detached_points_to_test_db
):
    """R7：**假上报比不上报更危险**——调用失败时绝不能标成 `REPORTED`。

    单独成条是因为它和 R5 抓的不是一件事：R5 抓「错分成 PENDING」，
    这条抓「直接假装成功」。后者会让备案材料显示「全部已上报」。
    """
    from app.config import settings
    from app.core.moderation import ModerationLevel
    from app.models.moderation import ReportStatus

    monkeypatch.setattr(settings, "MODERATION_REPORT_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "MODERATION_REPORT_URL", "https://report.example/in", raising=False)
    fake_httpx["ok"]["value"] = False

    await _persist(db, ModerationLevel.ESCALATE)
    row = await _last_status(engine)

    assert row.report_status != ReportStatus.REPORTED.value, (
        "上报明明失败了却标成 REPORTED ⇒ 假上报，比不上报更危险"
    )


async def test_report_failure_does_not_break_the_business_request(monkeypatch, fake_httpx):
    """R8：上报失败**不得**把业务请求打挂。

    `report_to_authority` 内部 `except Exception` 吞掉异常并返回 False ——
    这是刻意的（上报是旁路义务，不能因为监管接口抖动就让问答 500）。
    这条把这个契约钉住：将来有人「顺手」把异常放出去，这里会红。
    """
    from app.config import settings
    from app.core.moderation import ModerationLevel, report_to_authority

    monkeypatch.setattr(settings, "MODERATION_REPORT_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "MODERATION_REPORT_URL", "https://report.example/in", raising=False)
    fake_httpx["ok"]["value"] = False

    ok = await report_to_authority(
        _result(ModerationLevel.BLOCK), subject="t1", period=""
    )
    assert ok is False  # 不抛异常即为通过
