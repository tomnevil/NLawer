"""复核写端点的租户归属回归测试（P0-14）。

## 为什么这个文件必须存在

P0-14 的原文是「复核编辑路由缺租户校验（**可篡改他人租户已确认文书**）」。
修复收口在 `reviews.py:18` 的 `_review_or_404()`：

```python
r = await db.get(Review, review_id)
if r is None or r.tenant_id != tenant_id:
    raise NotFoundError("复核任务不存在", code=ErrorCode.REVIEW_NOT_FOUND)
```

它自己注释了历史缺陷：`edit` / `decide` / `archive` / `void` 四个写端点
**仅依赖 `get_current_user`，未做租户校验**，任何登录用户可凭 review_id
篡改或定稿他人租户的复核内容。

**但此前没有任何判据管它**：
- `tests/` 下 `/api/v1/reviews` 与 `/reviews/` **零命中** ⇒ 没有任何用例打过复核端点；
- `_review_or_404` **零命中** ⇒ 守卫函数本身也没被测过；
- 唯一构造 `Review(` 的是 `test_notification_producers.py`，那是测通知生产者，
  与租户隔离无关。

⇒ 与「注册越权」（`test_auth_register.py`）、「限流」（`test_rate_limit.py`）
**同一族**：**「写了」≠「有判据」**。把 `r.tenant_id != tenant_id` 这一行删掉，
全量回归会全绿。

## 覆盖清单

- G1 跨租户：租户 B 读/改租户 A 的复核 ⇒ `NotFoundError`
- G2 同租户：租户 A 自己读 ⇒ 正常返回
- G3 **不存在与跨租户必须同一个响应**：两者都 404、且消息一致（不泄露存在性）
- G4 HTTP 层：以租户 B 身份 `POST /reviews/{id}/edit` ⇒ **404**
- G5 四个写端点全部经过守卫（不含只读的列表/详情）
"""
from __future__ import annotations

import pathlib
import uuid

import pytest
from sqlalchemy import select

TENANT_A = "review-guard-a"
TENANT_B = "review-guard-b"


# ═══════════════════════ 夹具 ═══════════════════════


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"review_{uuid.uuid4().hex[:8]}.db"
    eng = create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)
    return eng


@pytest.fixture(scope="module")
def seeded(engine):
    """建表并播入：两个租户 + 租户 A 名下的一份复核任务。"""
    import asyncio

    import app.models  # noqa: F401
    from app.core.rbac import Role
    from app.core.security import hash_password
    from app.models.base import Base
    from app.models.enums import ReviewLevel, ReviewStatus, ReviewTargetType, UserStatus
    from app.models.identity import Tenant, User
    from app.models.review import Review

    async def _seed():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            for tid in (TENANT_A, TENANT_B):
                if not (
                    await s.execute(select(Tenant).where(Tenant.tenant_id == tid))
                ).scalars().first():
                    s.add(Tenant(tenant_id=tid, name=f"租户 {tid}"))
            await s.flush()

            actor = (
                await s.execute(select(User).where(User.username == "review-actor"))
            ).scalars().first()
            if actor is None:
                actor = User(
                    username="review-actor",
                    hashed_password=hash_password("Str0ngPass!"),
                    full_name="复核人",
                    role=Role.LAWYER,
                    tenant_id=TENANT_A,
                    status=UserStatus.ACTIVE,
                    is_active=True,
                )
                s.add(actor)
                await s.flush()

            review = Review(
                tenant_id=TENANT_A,
                target_type=ReviewTargetType.DOCUMENT,
                target_id=1,
                status=ReviewStatus.PENDING_CONFIRM,
                required_level=ReviewLevel.L2,
                assignee_id=actor.id,
            )
            s.add(review)
            await s.commit()
            return {"review_id": review.id, "actor_id": actor.id}

    return asyncio.run(_seed())


@pytest.fixture()
async def db(engine, seeded):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


# ═══════════════════════ G1–G3 守卫函数层 ═══════════════════════


async def test_cross_tenant_review_is_rejected(db, seeded):
    """G1：租户 B 取租户 A 的复核 ⇒ 必须拒绝。这是 P0-14 的核心。"""
    from app.api.v1.reviews import _review_or_404
    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await _review_or_404(db, seeded["review_id"], TENANT_B)


async def test_same_tenant_review_is_returned(db, seeded):
    """G2：同租户必须放行（**干净对照**——没有它，G1 的绿可能是「守卫拒绝一切」）。"""
    from app.api.v1.reviews import _review_or_404

    r = await _review_or_404(db, seeded["review_id"], TENANT_A)
    assert r.id == seeded["review_id"]
    assert r.tenant_id == TENANT_A


async def test_missing_and_cross_tenant_are_indistinguishable(db, seeded):
    """G3：跨租户与「不存在」必须是**同一个**错误码与消息。

    返回 403 会泄露「该 review_id 存在」这一事实，攻击者可据此枚举有效编号。
    本项目的约定是统一 404（`evidence.py` 的守卫同理），这条用例把它钉住。
    """
    from app.api.v1.reviews import _review_or_404
    from app.core.errors import AppError, NotFoundError

    missing_id = seeded["review_id"] + 9900

    err_missing = None
    err_cross = None
    with pytest.raises(NotFoundError) as e1:
        await _review_or_404(db, missing_id, TENANT_A)
    err_missing = e1.value
    with pytest.raises(NotFoundError) as e2:
        await _review_or_404(db, seeded["review_id"], TENANT_B)
    err_cross = e2.value

    assert isinstance(err_missing, AppError) and isinstance(err_cross, AppError)
    assert err_missing.code == err_cross.code, "跨租户与不存在返回了不同的错误码"
    assert err_missing.message == err_cross.message, (
        "跨租户与不存在返回了不同的消息——泄露了资源是否存在"
    )


# ═══════════════════════ G4 HTTP 层（攻击者路径） ═══════════════════════


async def test_http_edit_cross_tenant_returns_404(engine, seeded):
    """G4：以租户 B 身份走完整 HTTP 链路改租户 A 的复核 ⇒ 404。

    前三条测的是**守卫函数**；这一条测的是**端点真的调用了守卫**。
    两者之间隔着路由参数、依赖注入、序列化——历史上正是「端点没调守卫」出的漏洞，
    只测函数层会完全漏掉那一类。
    """
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.deps import get_current_user, get_db, get_tenant_context
    from app.core.rbac import Role
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
        async with factory() as s:
            return (
                await s.execute(select(User).where(User.id == seeded["actor_id"]))
            ).scalars().one()

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    # 攻击者画像：**已登录**（有合法 user），但租户上下文是 B
    app.dependency_overrides[get_tenant_context] = lambda: type(
        "Ctx", (), {"tenant_id": TENANT_B, "user_id": seeded["actor_id"], "role": Role.LAWYER}
    )()
    app.dependency_overrides[get_current_user] = _override_user

    with TestClient(app) as client:
        resp = client.post(
            f"/api/v1/reviews/{seeded['review_id']}/edit",
            json={"changes": {"title": "被篡改"}, "comment": "越权尝试"},
        )

    assert resp.status_code == 404, (
        f"跨租户编辑未被拦：{resp.status_code} {resp.text[:200]}"
    )


# ═══════════════════════ G5 四个写端点都过守卫 ═══════════════════════


def test_every_id_bearing_write_endpoint_calls_the_guard():
    """G5：**凡是带 `{review_id}` 的写端点**都必须调用 `_review_or_404`。

    写成**枚举路由**而不是写死 `["edit_review", ...]`：硬编码清单只能防住
    **已经出过事的那个**端点，将来新加一个漏守卫的写端点，硬编码判据照样全绿。
    判据要盯的是**性质**（带 ID 的写操作 ⇒ 必须校验归属），不是**名单**。

    附一条自检：历史上出过问题的四个端点必须都在集合里——否则很可能是路由
    定义方式变了（比如 ID 不再出现在 path 里），判据会静默变成空集。
    """
    import ast
    import inspect
    import textwrap

    from app.api.v1 import reviews as rv

    guarded = set()
    for route in rv.router.routes:
        methods = getattr(route, "methods", set()) or set()
        if "POST" not in methods:
            continue
        if "{review_id}" not in getattr(route, "path", ""):
            continue
        fn = route.endpoint
        # 🚨 必须走 AST 找**真实调用节点**，不能用 `"_review_or_404(" in source`。
        # 实测踩过：把调用整行注释掉（`# await _review_or_404(...)`）后，
        # 子串判据仍然命中注释里的函数名 ⇒ **假绿**。
        # 「源码里提到守卫」与「端点真的调用了守卫」是两件事。
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        called = {
            n.func.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "_review_or_404" in called, (
            f"写端点 {route.path}（{fn.__name__}）未**调用**租户守卫 `_review_or_404`"
            f"（源码里可能只是提到了它：{sorted(called)}）"
        )
        guarded.add(route.path)

    assert guarded, "判据自检失败：一个带 ID 的写端点都没枚举到"
    for suffix in ("edit", "decide", "archive", "void"):
        assert any(p.endswith("/" + suffix) for p in guarded), (
            f"判据自检失败：没找到 `/{suffix}` 端点——路由定义变了，判据要跟着改"
        )
