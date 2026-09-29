"""归档与开庭材料包 5 条路由的**端点层**判据（Q-T 清单第六批 · §3.30.2）。

## 为什么这个文件必须存在

`app/api/v1/archives.py` 的 5 条路由在 `tests/` 下**从未被请求过**。
`ArchiveService` 有服务层测试（卷宗组装 / 定稿前置条件），但服务层测试
**自己传 `tenant_id`** 进去——它证明了「给定租户参数时函数是对的」，
证明不了「端点真的把 `ctx.tenant_id` 传进去了」。

这一批的风险点跟前面几批**不同**：
- 归档是**不可逆的交付动作**（`case.status` → `ARCHIVED`，还会通知客户与承办律师）；
- 开庭材料包是**卷宗外流**——`HearingPack.sections` 是整包正文，
  `file_path` 直接指向落盘文件。

⚠️ 所以 V2 / V7 必须写成**反向量**（前后 DB 快照），不能只看状态码：
「返回 404」和「没有改动对方数据」是**两件事**，
§3.28 派单已经证明过状态码可以是 404 而数据已经 commit 完了。

## 覆盖清单

| 编号 | 性质 |
|------|------|
| V1 | 5 条路由**首次真实 HTTP 请求**：全员非 500 |
| **V2** | **跨租户归档 ⇒ 404 `CASE_NOT_FOUND` + 对方案件状态不变 + 没多出 Archive** |
| V3 | 未定稿（INTAKE）不可归档 ⇒ 400 `ARCHIVE_NOT_CONFIRMED`，且状态不变 |
| V4 | 同租户归档 CONFIRMED ⇒ 200，案件进 ARCHIVED（防修完把功能焊死） |
| V5 | `GET /cases/{case_id}` 跨租户 ⇒ 404 `ARCHIVE_NOT_FOUND` |
| **V6** | **`/versions` 跨租户 ⇒ 404**（`ArchiveService.versions` **不收** `tenant_id`，端点是唯一防线） |
| **V7** | **跨租户导出材料包 ⇒ 404，且对方案件状态 / 材料包数量不变** |
| V8 | `GET /hearing-packs/{pack_id}` 跨租户 ⇒ 404 |
| V9 | 归档与导出**各留一条审计**（压 `record(...)` 那两行，不是压业务结果） |
| V10 | AST：4 条端点都引用 `ctx.tenant_id`（带命中下限自检） |

⚠️ **刻意不覆盖 `get_tenant_context`**（`methodology.md` 82）。
⚠️ 材料包会**真的落盘**（`_write_markdown` 写 `settings.LOCAL_STORAGE_PATH`）。
   跑这一文件需要**允许写 `backend/storage/local`**；沙箱下会假红
   （§3.27 的 `test_archive_service` / `test_storage_path_containment` 就是这么红的）。
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
import uuid

import pytest
from sqlalchemy import func, select

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


# ═══════════════════════ 夹具 ═══════════════════════


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"arc_ep_{uuid.uuid4().hex[:8]}.db"
    return create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)


@pytest.fixture(scope="module")
def seeded(engine):
    """租户 A：一个 CONFIRMED 待归档案件 + 一个 INTAKE 未定稿案件 + 一个已归档案件；
    租户 B：一个 CONFIRMED 案件 + 它的卷宗 + 它的材料包（三个跨租户攻击目标）。
    """
    import app.models  # noqa: F401
    from app.core.rbac import Role
    from app.core.security import hash_password
    from app.models.archive import Archive, ArchiveVersion, HearingPack
    from app.models.base import Base
    from app.models.case import Case
    from app.models.enums import CaseStatus, UserStatus
    from app.models.identity import Tenant, User

    async def _seed():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            for tid in (TENANT_A, TENANT_B):
                exists = (await s.execute(
                    select(Tenant).where(Tenant.tenant_id == tid))).scalars().first()
                if not exists:
                    s.add(Tenant(tenant_id=tid, name=f"租户 {tid}"))
            await s.flush()

            def _user(tag, tid):
                return User(
                    username=f"aep-{tag}-{uuid.uuid4().hex[:6]}",
                    hashed_password=hash_password("Str0ngPass!"),
                    full_name=tag,
                    role=Role.LAWYER,
                    tenant_id=tid,
                    status=UserStatus.ACTIVE,
                    is_active=True,
                )

            lawyer_a = _user("lawyerA", TENANT_A)
            lawyer_b = _user("lawyerB", TENANT_B)
            s.add_all([lawyer_a, lawyer_b])
            await s.flush()

            def _case(tid, tag, status, lawyer_id=None):
                return Case(
                    tenant_id=tid,
                    case_no=f"CASE-{tag}-{uuid.uuid4().hex[:6]}",
                    title=f"{tag} 的案件",
                    status=status,
                    lawyer_id=lawyer_id,
                )

            case_a_ready = _case(TENANT_A, "AREADY", CaseStatus.CONFIRMED, lawyer_a.id)
            case_a_intake = _case(TENANT_A, "AINTK", CaseStatus.INTAKE, lawyer_a.id)
            case_a_arch = _case(TENANT_A, "AARCH", CaseStatus.ARCHIVED, lawyer_a.id)
            case_b_ready = _case(TENANT_B, "BREADY", CaseStatus.CONFIRMED, lawyer_b.id)
            s.add_all([case_a_ready, case_a_intake, case_a_arch, case_b_ready])
            await s.flush()

            # A 已归档案件的卷宗（V5 的**正向**目标 + V6 的同租户对照）
            arch_a = Archive(
                tenant_id=TENANT_A, case_id=case_a_arch.id,
                archive_no=f"{TENANT_A}-AR-seed-{uuid.uuid4().hex[:6]}",
                title="A 的卷宗", dossier={"timeline": ["A 的内部节点"]},
                current_version=1, archived_by=lawyer_a.id,
            )
            # B 的卷宗 —— 跨租户 `versions` 的目标
            arch_b = Archive(
                tenant_id=TENANT_B, case_id=case_b_ready.id,
                archive_no=f"{TENANT_B}-AR-seed-{uuid.uuid4().hex[:6]}",
                title="B 的卷宗", dossier={"timeline": ["B 的内部节点"]},
                current_version=1, archived_by=lawyer_b.id,
            )
            s.add_all([arch_a, arch_b])
            await s.flush()

            s.add(ArchiveVersion(
                tenant_id=TENANT_B, archive_id=arch_b.id, version=1,
                snapshot={"timeline": ["B 的内部节点"]},
                change_note="B 的首次归档", created_by_user_id=lawyer_b.id))
            # B 的材料包 —— 跨租户读的目标
            pack_b = HearingPack(
                tenant_id=TENANT_B, case_id=case_b_ready.id, archive_id=arch_b.id,
                title="B 的材料包", sections={"诉状": ["B 的起诉状正文"]},
                file_path=f"{TENANT_B}/hearing_packs/b.md", generated_by=lawyer_b.id,
            )
            s.add(pack_b)
            await s.commit()

            return {
                "a": lawyer_a.id,
                "b": lawyer_b.id,
                "case_a_ready": case_a_ready.id,
                "case_a_intake": case_a_intake.id,
                "case_a_arch": case_a_arch.id,
                "case_b_ready": case_b_ready.id,
                "arch_a": arch_a.id,
                "arch_b": arch_b.id,
                "pack_b": pack_b.id,
            }

    return asyncio.run(_seed())


@pytest.fixture(autouse=True)
def _reset_archives(engine, seeded):
    """每个用例前复位：**清掉测试期间新建的** Archive / HearingPack / AuditLog，
    并把 4 个案件的状态复位。

    与 §3.28 `_reset_dispatches` 同理——但这里还多一层：`archive_no` 有
    **唯一约束**，V4 归档成功后再跑一次 V2，`archive_case` 会因为撞号而 500，
    看起来像「越权被打回」，其实是**夹具脏了** ⇒ 假绿。
    """
    from sqlalchemy import delete
    from sqlalchemy import update as _upd

    from app.models.archive import Archive, ArchiveVersion, HearingPack
    from app.models.audit_log import AuditLog
    from app.models.case import Case
    from app.models.enums import CaseStatus

    async def _run() -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            await s.execute(delete(AuditLog))
            await s.execute(delete(ArchiveVersion).where(
                ArchiveVersion.archive_id.notin_([seeded["arch_a"], seeded["arch_b"]])))
            await s.execute(delete(Archive).where(
                Archive.id.notin_([seeded["arch_a"], seeded["arch_b"]])))
            await s.execute(delete(HearingPack))
            await s.commit()
        async with factory() as s:
            await s.execute(_upd(Case).where(Case.id == seeded["case_a_ready"]).values(
                status=CaseStatus.CONFIRMED))
            await s.execute(_upd(Case).where(Case.id == seeded["case_a_intake"]).values(
                status=CaseStatus.INTAKE))
            await s.execute(_upd(Case).where(Case.id == seeded["case_b_ready"]).values(
                status=CaseStatus.CONFIRMED))
            await s.commit()
        # pack_b 被上面 delete 掉了，重建它（V8 需要它存在）
        async with factory() as s:
            from app.models.archive import HearingPack as HP

            exists = await s.get(HP, seeded["pack_b"])
            if exists is None:
                s.add(HP(
                    id=seeded["pack_b"], tenant_id=TENANT_B,
                    case_id=seeded["case_b_ready"], archive_id=seeded["arch_b"],
                    title="B 的材料包", sections={"诉状": ["B 的起诉状正文"]},
                    file_path=f"{TENANT_B}/hearing_packs/b.md", generated_by=seeded["b"],
                ))
                await s.commit()

    asyncio.run(_run())


@pytest.fixture
def app_factory(engine):
    """真实 `create_app()` + 只覆盖 `get_db` / `get_current_user`。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.deps import get_current_user, get_db
    from app.main import create_app

    factory = async_sessionmaker(engine, expire_on_commit=False)

    def _make(user_id: int):
        async def _override_get_db():
            async with factory() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        async def _override_user():
            from app.models.identity import User

            async with factory() as s:
                return (await s.execute(
                    select(User).where(User.id == user_id))).scalars().one()

        app = create_app()
        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = _override_user
        return app

    return _make


def _client(app):
    from fastapi.testclient import TestClient

    return TestClient(app)


def _data(resp):
    return resp.json().get("data") or {}


def _code(resp) -> str:
    return str(resp.json().get("error", {}).get("code", ""))


def _case_status(engine, case_id: int):
    from app.models.case import Case

    async def _q():
        from sqlalchemy.ext.asyncio import async_sessionmaker

        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            c = await s.get(Case, case_id)
            st = c.status
            return st.value if hasattr(st, "value") else str(st)

    return asyncio.run(_q())


def _count(engine, model):
    async def _q():
        from sqlalchemy.ext.asyncio import async_sessionmaker

        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            return (await s.execute(select(func.count()).select_from(model))).scalar()

    return asyncio.run(_q())


def _audit_count(engine, action: str):
    from app.models.audit_log import AuditLog

    async def _q():
        from sqlalchemy.ext.asyncio import async_sessionmaker

        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            return (await s.execute(
                select(func.count()).select_from(AuditLog).where(
                    AuditLog.action == action))).scalar()

    return asyncio.run(_q())


def _func(name: str) -> str:
    import app.api.v1.archives as mod

    src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"archives.py 里找不到 {name}（端点改名了？AST 判据会静默退化）")


# ═══════════════════════ V1 装配层冒烟 ═══════════════════════


def test_all_routes_respond(app_factory, seeded):
    """V1：5 条路由**第一次**被真实请求——必须都不是 500。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        assert c.get(f"/api/v1/archives/cases/{seeded['case_a_arch']}").status_code == 200
        assert c.get(f"/api/v1/archives/{seeded['arch_a']}/versions").status_code == 200
        assert c.get(f"/api/v1/archives/hearing-packs/{seeded['pack_b']}").status_code == 404
        assert c.post(f"/api/v1/archives/cases/{seeded['case_a_intake']}").status_code == 400
        r = c.post(f"/api/v1/archives/cases/{seeded['case_a_ready']}/hearing-pack")
        assert r.status_code == 200, r.text


# ═══════════════════════ V2 跨租户归档（反向量） ═══════════════════════


def test_cross_tenant_archive_changes_nothing(app_factory, seeded, engine):
    """V2：**本批的核心**。用 A 的身份归档 B 的案件 ⇒ 404，且 B **一个字都没动**。

    为什么要 DB 快照：`archive_case` 会 `case.status = ARCHIVED` + 建卷宗 +
    **通知 B 的客户和律师**。若守卫只是「最后抛个 404」而前面的写已经 commit，
    单看状态码完全看不出来。
    """
    from app.models.archive import Archive

    before_status = _case_status(engine, seeded["case_b_ready"])
    before_archives = _count(engine, Archive)

    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post(f"/api/v1/archives/cases/{seeded['case_b_ready']}")
        assert r.status_code == 404, r.text
        assert _code(r) == "CASE_NOT_FOUND", r.text

    assert _case_status(engine, seeded["case_b_ready"]) == before_status, (
        f"跨租户归档改动了对方案件状态：{before_status} → "
        f"{_case_status(engine, seeded['case_b_ready'])}")
    assert _count(engine, Archive) == before_archives, "跨租户归档多建了卷宗"


# ═══════════════════════ V3 未定稿不可归档 ═══════════════════════


def test_unconfirmed_case_cannot_be_archived(app_factory, seeded, engine):
    """V3：INTAKE 案件 ⇒ 400 `ARCHIVE_NOT_CONFIRMED`，且状态不变。

    这条是**业务前置条件**，压的是 `archive_case` 里那段 `CONFIRMED` 硬约束；
    它跟租户隔离是**两层独立**的防线，两者都要有判据
    （只测跨租户的话，把定稿校验删掉照样全绿）。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post(f"/api/v1/archives/cases/{seeded['case_a_intake']}")
        assert r.status_code == 400, r.text
        assert _code(r) == "ARCHIVE_NOT_CONFIRMED", r.text
    # `CaseStatus` 的取值是**大写**（`INTAKE` / `ARCHIVED`）——别按别的模块想当然
    assert _case_status(engine, seeded["case_a_intake"]) == "INTAKE"


# ═══════════════════════ V4 同租户归档（正向） ═══════════════════════


def test_same_tenant_archive_works(app_factory, seeded, engine):
    """V4：同租户归档 CONFIRMED 案件 ⇒ 200，案件进 ARCHIVED。

    没有这条，V2/V3 的「红」可能只是「功能整个被焊死了」——
    **正向判据是反向判据的防伪标记**。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post(f"/api/v1/archives/cases/{seeded['case_a_ready']}")
        assert r.status_code == 200, r.text
        assert _data(r)["case_id"] == seeded["case_a_ready"], r.text
    assert _case_status(engine, seeded["case_a_ready"]) == "ARCHIVED"


# ═══════════════════════ V5 / V6 / V8 跨租户读 ═══════════════════════


def test_cross_tenant_get_archive_is_404(app_factory, seeded):
    """V5：`GET /cases/{case_id}` 跨租户 ⇒ 404 `ARCHIVE_NOT_FOUND`。

    注意这条查的是 **Archive 表**：A 用自己的身份去查 B 的 `case_id`，
    库里确实有一条 B 的 Archive（seeded），所以「不存在」和「别人的」
    在这里是**同一次查询的两个分支**——删掉租户比较就变成 200 泄露。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.get(f"/api/v1/archives/cases/{seeded['case_b_ready']}")
        assert r.status_code == 404, r.text
        assert _code(r) == "ARCHIVE_NOT_FOUND", r.text
        # B 卷宗里的内容不许出现在响应体里
        assert "B 的内部节点" not in r.text


def test_cross_tenant_versions_is_404(app_factory, seeded):
    """V6：`/versions` 跨租户 ⇒ 404。

    `ArchiveService.versions(archive_id)` **只按 archive_id 查**，签名里
    根本没有 `tenant_id`（`verify_service_tenant_param.py` 的 20 条候选之一）。
    ⇒ 端点那句 `a.tenant_id != ctx.tenant_id` 是**唯一**防线，删掉必红。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.get(f"/api/v1/archives/{seeded['arch_b']}/versions")
        assert r.status_code == 404, r.text
        assert _code(r) == "ARCHIVE_NOT_FOUND", r.text
        assert "B 的首次归档" not in r.text, "跨租户拿到了对方的版本备注"


def test_same_tenant_versions_works(app_factory, seeded):
    """V6b：同租户 `/versions` 正常返回（防伪标记：V6 的红不是因为功能坏了）。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.get(f"/api/v1/archives/{seeded['arch_a']}/versions")
        assert r.status_code == 200, r.text


def test_cross_tenant_get_hearing_pack_is_404(app_factory, seeded):
    """V8：`GET /hearing-packs/{pack_id}` 跨租户 ⇒ 404，正文不外泄。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.get(f"/api/v1/archives/hearing-packs/{seeded['pack_b']}")
        assert r.status_code == 404, r.text
        assert _code(r) == "ARCHIVE_NOT_FOUND", r.text
        assert "B 的起诉状正文" not in r.text


# ═══════════════════════ V7 跨租户导出材料包（反向量） ═══════════════════════


def test_cross_tenant_hearing_pack_changes_nothing(app_factory, seeded, engine):
    """V7：跨租户导出 ⇒ 404，且**没有多出材料包**、对方案件状态不变。

    材料包是「卷宗外流」，比读更重：它会 `os.makedirs` + **真的写一个 md 文件**
    到 `storage/local/{tenant_id}/hearing_packs/`。所以这条要同时看
    「DB 行数」和「对方案件状态」两个反向量。
    """
    from app.models.archive import HearingPack

    before_status = _case_status(engine, seeded["case_b_ready"])
    before_packs = _count(engine, HearingPack)

    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post(f"/api/v1/archives/cases/{seeded['case_b_ready']}/hearing-pack")
        assert r.status_code == 404, r.text
        assert _code(r) == "CASE_NOT_FOUND", r.text

    assert _count(engine, HearingPack) == before_packs, "跨租户导出多建了材料包"
    assert _case_status(engine, seeded["case_b_ready"]) == before_status


# ═══════════════════════ V9 审计留痕 ═══════════════════════


def test_archive_and_export_both_write_audit(app_factory, seeded, engine):
    """V9：归档与导出**各留一条审计**。

    这条压的是端点里那两行 `await record(...)`——它们跟业务结果**无关**
    （不写审计，归档照样成功、接口照样 200）。
    ⇒ 只有**直接查 audit_logs** 才测得到；这是典型的「没有判据的静默要求」。
    """
    from app.core.audit import AuditAction

    assert _audit_count(engine, AuditAction.ARCHIVE_CREATE) == 0
    assert _audit_count(engine, AuditAction.HEARING_PACK_EXPORT) == 0

    app = app_factory(seeded["a"])
    with _client(app) as c:
        assert c.post(f"/api/v1/archives/cases/{seeded['case_a_ready']}").status_code == 200
        r = c.post(f"/api/v1/archives/cases/{seeded['case_a_ready']}/hearing-pack")
        assert r.status_code == 200, r.text

    assert _audit_count(engine, AuditAction.ARCHIVE_CREATE) == 1, "归档没留审计"
    assert _audit_count(engine, AuditAction.HEARING_PACK_EXPORT) == 1, "材料包导出没留审计"


# ═══════════════════════ V10 AST 装配判据 ═══════════════════════


def test_all_endpoints_reference_tenant_context():
    """V10：4 条端点都必须引用 `ctx.tenant_id`（带「真的比过」的下限自检）。

    ⚠️ AST 只能防「守卫被删」，不能证明「守卫拦得住」——
    真正的证明是 V2/V5/V6/V7/V8 的注入反证。
    """
    for name in ("archive_case", "get_archive", "archive_versions",
                 "export_hearing_pack", "get_hearing_pack"):
        src = _func(name)
        assert "ctx.tenant_id" in src, f"{name} 没有引用 ctx.tenant_id"
