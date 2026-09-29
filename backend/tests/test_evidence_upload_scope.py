"""证据**写端点**的判据（§3.18 / §3.19，2026-09-20）。

## 为什么这个文件必须存在

`test_evidence_authz.py` 把 evidence 的三个**读**入口锁死了，但 `CASE_SCOPED_ENDPOINTS`
里全是 GET —— `POST /evidence/cases/{case_id}`（上传）与
`POST /evidence/{evidence_id}/parse`（重解析）此前**零覆盖**。

实测结论（本轮）：

| 事实 | 实测 |
|------|------|
| `tests/` 下对 `POST /evidence/cases/{cid}` 的请求次数 | **0** |
| `tests/` 下对 `POST /evidence/{id}/parse` 的请求次数 | **0** |
| `save_upload` 的覆盖 | 有（`test_storage_path_containment.py`），但**只测服务层** |

⇒ 与 §3.17（计费）同一个问题：**门里的东西有没有判据，没人知道。**
`save_upload(tenant_id)` 收哪个租户，取决于**端点传了什么**——服务层测得再全，
端点传错照样串号，而且在单租户测试环境里**照样 200**。

## 覆盖清单

| 编号 | 性质 |
|------|------|
| U1 | AST：`save_upload` 的 `tenant_id` 实参必须来自 `ctx.tenant_id` |
| U2 | AST：`_case_or_404` 必须排在 `save_upload` **之前**（先鉴权后落盘） |
| U3 | AST：`Evidence(...)` 的 `tenant_id` 必须来自 `ctx.tenant_id` |
| U4 | 运行时：`trigger_evidence_parse` 收到的租户 == `ctx.tenant_id` |
| U5 | HTTP 数据边界：跨租户上传 ⇒ 404 **且磁盘上没留下文件** |
| U6 | HTTP 数据边界：同租户其他客户上传 ⇒ 404 |
| U7 | **反向量**：本客户上传自己案件 ⇒ 200，文件落在 `<root>/<tenant>/evidence/` 下 |
| U8 | **反向量**：本租户律师上传 ⇒ 200（防把写端点焊死） |
| V1 | HTTP：客户乙重解析客户甲的证据 ⇒ 404 |
| V2 | HTTP：跨租户重解析 ⇒ 404 |
| V3 | **反向量**：客户甲重解析自己的证据 ⇒ 200 |
| V4 | **反向量**：本租户律师重解析 ⇒ 200 |
| V5 | AST：`EvidenceService.parse` 必须带 `tenant_id`（服务层已有的纵深防御不能被绕过） |

## 关于 U2 的顺序判据

「拒绝前先写盘」是一种**不报错的泄漏**：攻击者拿到 404，但文件已经落在
别人租户的目录里，并且没有任何审计记录（因为 `record()` 在 `save_upload` 之后）。
顺序反了在功能测试里完全看不出来。
"""
from __future__ import annotations

import ast
import pathlib
import uuid

import pytest
from sqlalchemy import select

TENANT_A = "ev-up-a"
TENANT_B = "ev-up-b"

EVIDENCE_FILE = (
    pathlib.Path(__file__).resolve().parent.parent / "app" / "api" / "v1" / "evidence.py"
)

#: 合法的 `.pdf` 内容（只过扩展名白名单，不做内容校验）
PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


# ═══════════════════════ AST 结构判据 ═══════════════════════


def _module() -> ast.Module:
    return ast.parse(EVIDENCE_FILE.read_text(encoding="utf-8"))


def _func(tree: ast.Module, name: str):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"找不到函数 {name} —— 判据已失效，请更新本文件")


def _calls(node: ast.AST, func_name: str) -> list[ast.Call]:
    """收集所有对 `func_name(...)` 的调用（不限是否 await）。"""
    out = []
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Name) and f.id == func_name:
            out.append(n)
    return out


def _is_ctx_tenant(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "tenant_id"
        and isinstance(node.value, ast.Name)
        and node.value.id == "ctx"
    )


def _positional(node: ast.Call, name: str) -> ast.expr | None:
    """用 `inspect.signature` 把形参名映射到实参位置（位置参数也要覆盖）。

    ⚠️ 与 B1 同一个坑（`methodology.md` 80）：只扫 `tenant_id=` 关键字会漏掉
    位置传参，而漏掉时判据**不会报错**，只会静默退化成空集。
    """
    import inspect

    fn_name = node.func.id if isinstance(node.func, ast.Name) else None
    assert fn_name, ast.unparse(node)
    import app.api.v1.evidence as ev_mod

    fn = getattr(ev_mod, fn_name, None)
    assert fn is not None, f"{fn_name} 未从 evidence 模块导入 —— 判据已失效"
    params = [p for p in inspect.signature(fn).parameters if p != "self"]
    if name not in params:
        return None
    idx = params.index(name)
    return node.args[idx] if idx < len(node.args) else None


def _kwarg(node: ast.Call, name: str) -> ast.expr | None:
    for kw in node.keywords:
        if kw.arg == name:
            return kw.value
    return None


def test_save_upload_receives_context_tenant():
    """U1：`save_upload` 的 `tenant_id` 必须来自 `ctx.tenant_id`。

    为什么用 AST 而不只测 HTTP：写死 `settings.DEFAULT_TENANT_ID` 的端点在
    单租户测试环境里**照样 200 且数据看着正常**，只有上多租户才串号。
    """
    calls = _calls(_func(_module(), "upload_evidence"), "save_upload")
    assert len(calls) == 1, f"upload_evidence 里 save_upload 调用数 = {len(calls)}"

    arg = _kwarg(calls[0], "tenant_id") or _positional(calls[0], "tenant_id")
    assert arg is not None, "定位不到 save_upload 的 tenant_id 实参"
    assert _is_ctx_tenant(arg), (
        f"save_upload 的 tenant_id 不是 ctx.tenant_id，而是 {ast.unparse(arg)}"
    )


def test_case_guard_runs_before_disk_write():
    """U2：`_case_or_404` 必须排在 `save_upload` **之前**。

    顺序反了 = 「先落盘再拒绝」：文件已经写进别人的租户目录，且因为 `record()`
    在 `save_upload` 之后，**连审计都没有**——一种完全静默的写入。
    """
    fn = _func(_module(), "upload_evidence")
    guard = _calls(fn, "_case_or_404")
    write = _calls(fn, "save_upload")

    assert guard, "upload_evidence 没有调用 _case_or_404 ⇒ 越权上传可直接落盘"
    assert write, "upload_evidence 没有调用 save_upload —— 判据已失效"
    assert guard[0].lineno < write[0].lineno, (
        f"先落盘后鉴权：_case_or_404 在第 {guard[0].lineno} 行，"
        f"save_upload 在第 {write[0].lineno} 行"
    )


def test_evidence_row_tenant_comes_from_context():
    """U3：`Evidence(...)` 落库的 `tenant_id` 必须来自 `ctx.tenant_id`。

    端点校验用的是 `ctx.tenant_id`，落库却写 `user.tenant_id` 也是常见的串号写法：
    平台管理员用 `X-Tenant-Id` 切换到租户 B 时，`user.tenant_id` 仍是自己的租户，
    于是**用 B 的身份校验、把数据写进 A**。
    """
    fn = _func(_module(), "upload_evidence")
    rows = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name) and n.func.id == "Evidence"]
    assert rows, "upload_evidence 里没有构造 Evidence —— 判据已失效"

    for row in rows:
        arg = _kwarg(row, "tenant_id")
        assert arg is not None, "Evidence(...) 没显式传 tenant_id"
        assert _is_ctx_tenant(arg), f"Evidence.tenant_id 不是 ctx.tenant_id：{ast.unparse(arg)}"


def test_parse_service_receives_tenant_id():
    """V5：`EvidenceService.parse` 必须带 `tenant_id`（服务层纵深防御不能被绕过）。

    `EvidenceService.parse` 的 docstring 明确写了「worker 从 job 恢复执行时同样应传入，
    避免仅凭 evidence_id 即可解析他人证据（IDOR 纵深防御）」——但端点没传，
    等于**自己实现的防护自己没用上**：端点里的手工判等一旦被删/写错，
    服务层不会兜住。
    """
    fn = _func(_module(), "reparse")
    parse = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "parse"]
    assert parse, "reparse 没调用 EvidenceService.parse —— 判据已失效"

    for call in parse:
        arg = _kwarg(call, "tenant_id")
        assert arg is not None, (
            "EvidenceService.parse 没传 tenant_id ⇒ 服务层的归属校验被绕过"
        )
        assert _is_ctx_tenant(arg), f"parse 的 tenant_id 不是 ctx.tenant_id：{ast.unparse(arg)}"


# ═══════════════════════ 夹具 ═══════════════════════


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"evidence_upload_{uuid.uuid4().hex[:8]}.db"
    return create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)


@pytest.fixture(scope="module")
def seeded(engine):
    """租户 A 下两个客户（甲有案件、乙没有）+ 一个律师；租户 B 有一个案件。"""
    import asyncio

    import app.models  # noqa: F401
    from app.core.rbac import Role
    from app.core.security import hash_password
    from app.models.base import Base
    from app.models.case import Case
    from app.models.enums import CaseGrade, CaseStatus, UserStatus
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

            def _user(tag, role, tid):
                return User(
                    username=f"evup-{tag}-{uuid.uuid4().hex[:6]}",
                    hashed_password=hash_password("Str0ngPass!"),
                    full_name=tag,
                    role=role,
                    tenant_id=tid,
                    status=UserStatus.ACTIVE,
                    is_active=True,
                )

            client_a = _user("clientA", Role.CLIENT, TENANT_A)
            client_b = _user("clientB", Role.CLIENT, TENANT_A)
            lawyer = _user("lawyer", Role.LAWYER, TENANT_A)
            # 平台管理员：自身租户是 "platform"，靠 X-Tenant-Id 切换。
            # 这是**唯一**能让 user.tenant_id != ctx.tenant_id 的身份，
            # 也是「用 B 的身份校验、把数据写进 A」这种串号的真实触发场景。
            admin = _user("admin", Role.PLATFORM_ADMIN, "platform")
            s.add_all([client_a, client_b, lawyer, admin])
            await s.flush()

            def _case(tag, tid, owner_id):
                return Case(
                    case_no=f"EVUP-{tag}-{uuid.uuid4().hex[:6]}",
                    tenant_id=tid,
                    title=f"{tag} 的案件",
                    client_user_id=owner_id,
                    status=CaseStatus.INTAKE,
                    grade=CaseGrade.B,
                    dispute_type="劳动争议",
                )

            own = _case("A", TENANT_A, client_a.id)
            foreign = _case("B", TENANT_B, None)
            s.add_all([own, foreign])
            await s.flush()

            # 预置一份「客户甲案件的证据」：让 V1–V4 不依赖上传用例先跑
            # （用例间顺序依赖会让判据在随机顺序下假红/假绿）
            from app.models.enums import EvidenceCategory, EvidenceStatus
            from app.models.evidence import Evidence

            seeded_ev = Evidence(
                tenant_id=TENANT_A,
                case_id=own.id,
                uploaded_by=client_a.id,
                name="离婚协议书.pdf",
                file_path=f"{TENANT_A}/evidence/seeded.pdf",
                file_type="application/pdf",
                file_size=1024,
                status=EvidenceStatus.PARSED,
                category=EvidenceCategory.CONTRACT,
            )
            s.add(seeded_ev)
            await s.flush()
            await s.commit()

            return {
                "client_a": client_a.id,
                "client_b": client_b.id,
                "lawyer": lawyer.id,
                "own_case": own.id,
                "foreign_case": foreign.id,
                "evidence": seeded_ev.id,
                "admin": admin.id,
            }

    return asyncio.run(_seed())


@pytest.fixture
def storage_root(tmp_path_factory, monkeypatch):
    """把存储根指到本次用例的临时目录，便于断言「拒绝后没留下文件」。

    ⚠️ 不用 pytest 的 `tmp_path`：沙箱里系统临时目录不可写（`WinError 5`）。
    """
    root = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests" / f"st-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)

    from app.config import settings

    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", str(root))
    return root


def _files_under(root: pathlib.Path) -> set[str]:
    return {str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def app_factory(engine, storage_root, monkeypatch):
    """真实 `create_app()` + 只覆盖 `get_db` / `get_current_user`，并接管后台任务。

    ⚠️ **刻意不覆盖 `get_tenant_context`**（`methodology.md` 82）：
    覆盖掉它就测不到「租户上下文到底从哪来」。

    `trigger_evidence_parse` 被替换成记录型桩：既避免真起常驻队列消费协程，
    又能顺便把「端点传给后台任务的租户是哪个」变成一条运行时判据（U4）。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    import app.api.v1.evidence as ev_mod
    from app.core.deps import get_current_user, get_db
    from app.main import create_app

    calls: list[tuple[int, str]] = []

    async def _stub_trigger(db, evidence_id, tenant_id, background):
        calls.append((evidence_id, tenant_id))
        return 1

    monkeypatch.setattr(ev_mod, "trigger_evidence_parse", _stub_trigger)

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
                return (await s.execute(select(User).where(User.id == user_id))).scalars().one()

        app = create_app()
        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = _override_user
        return app

    _make.calls = calls  # type: ignore[attr-defined]
    return _make


def _upload(client, case_id: int, filename: str = "离职证明.pdf", headers: dict | None = None):
    return client.post(
        f"/api/v1/evidence/cases/{case_id}",
        files={"file": (filename, PDF_BYTES, "application/pdf")},
        headers=headers or {},
    )


# ═══════════════════════ U4–U8 上传端点 ═══════════════════════


def test_upload_passes_context_tenant_to_background_job(app_factory, seeded, engine):
    """U4：端点传给后台解析任务的租户必须是 `ctx.tenant_id`（运行时，非 AST）。

    ⚠️ **必须用平台管理员来测**：普通用户的 `user.tenant_id` 与 `ctx.tenant_id`
    恒等，于是「端点误传 `user.tenant_id`」在测试里**根本看不出来**——
    本判据第一版就是这样漏掉的（注入 `jobtenant` 臂时 13 条全绿）。
    平台管理员 `user.tenant_id=="platform"`、靠 `X-Tenant-Id` 切到 TENANT_A，
    两者不同，串号才会红。

    ⇒ 判据的**夹具**也必须被质疑：两条恒等的量之间，任何判据都测不出差异。
    """
    import asyncio

    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models.evidence import Evidence

    with TestClient(app_factory(seeded["admin"])) as client:
        resp = _upload(client, seeded["own_case"], headers={"X-Tenant-Id": TENANT_A})
    assert resp.status_code == 200, f"{resp.status_code} {resp.text[:300]}"

    assert app_factory.calls, "后台解析任务没被触发 —— 判据已失效"
    tenants = {t for _, t in app_factory.calls}
    assert tenants == {TENANT_A}, f"后台任务收到的租户不对：{tenants}"

    # 顺带把「落库租户」也钉在运行时：AST（U3）只证明源码里写了什么
    async def _row_tenant() -> set[str]:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            rows = (await s.execute(select(Evidence.tenant_id))).scalars().all()
        return set(rows)

    stored = asyncio.run(_row_tenant())
    assert "platform" not in stored, f"落库串到了管理员自身租户：{stored}"
    assert TENANT_A in stored, f"没落到目标租户：{stored}"


def test_cross_tenant_upload_rejected_and_leaves_no_file(app_factory, seeded, storage_root):
    """U5：跨租户上传 ⇒ 404，**且磁盘上一个文件都没留下**。

    只断言 404 不够：`_case_or_404` 若被挪到 `save_upload` 之后，状态码仍是 404，
    但文件已经写进受害者租户的目录了（U2 从结构上防，这条从运行时防）。
    """
    from fastapi.testclient import TestClient

    before = _files_under(storage_root)
    with TestClient(app_factory(seeded["client_a"])) as client:
        resp = _upload(client, seeded["foreign_case"])
    after = _files_under(storage_root)

    assert resp.status_code == 404, f"跨租户上传被放行：{resp.status_code} {resp.text[:300]}"
    assert after == before, f"拒绝后仍落盘：新增 {sorted(after - before)}"


def test_same_tenant_other_client_upload_rejected(app_factory, seeded, storage_root):
    """U6：同租户的客户乙往客户甲的案件上传 ⇒ 404。

    `_case_or_404` 的客户归属分支在**读**端点上已被 `test_evidence_authz.py` 锁住，
    但写端点此前没测——往别人案件里塞材料比读别人的材料清单更严重：
    会污染证据链，且材料会出现在对方的「缺失清单」里。
    """
    from fastapi.testclient import TestClient

    before = _files_under(storage_root)
    with TestClient(app_factory(seeded["client_b"])) as client:
        resp = _upload(client, seeded["own_case"])
    after = _files_under(storage_root)

    assert resp.status_code == 404, f"客户乙竟能上传到他人案件：{resp.status_code}"
    assert after == before, f"拒绝后仍落盘：新增 {sorted(after - before)}"


def test_owner_can_upload_and_file_lands_in_own_tenant_dir(app_factory, seeded, storage_root):
    """U7（**反向量**）：本客户上传自己案件 ⇒ 200，且文件落在自己租户目录下。

    没有这条，把 `upload_evidence` 改成一律 404 上面两条依然全绿，
    而上传功能其实已经废了。同时断言**落盘位置**——只断言 200 抓不到
    「存进了别的租户目录」这种串号。
    """
    from fastapi.testclient import TestClient

    with TestClient(app_factory(seeded["client_a"])) as client:
        resp = _upload(client, seeded["own_case"])
    assert resp.status_code == 200, f"{resp.status_code} {resp.text[:300]}"

    body = resp.json()
    data = body.get("data", body)
    rel = data.get("file_path", "")
    assert rel.startswith(f"{TENANT_A}/evidence/"), f"落盘路径不在本租户目录下：{rel}"

    written = _files_under(storage_root)
    assert any(f.startswith(f"{TENANT_A}/evidence/") for f in written), (
        f"磁盘上没找到本租户的文件：{sorted(written)}"
    )
    assert not any(f.startswith(TENANT_B) for f in written), (
        f"串到租户 B 的目录：{sorted(written)}"
    )


def test_lawyer_of_same_tenant_can_upload(app_factory, seeded, storage_root):
    """U8（**反向量**）：本租户律师上传 ⇒ 200。

    与 `test_evidence_authz.py` 同一口径：律师的隔离边界是**租户级**不是归属级。
    这条防止有人把 U6 的修复写成「只有 `client_user_id` 能传」。
    """
    from fastapi.testclient import TestClient

    with TestClient(app_factory(seeded["lawyer"])) as client:
        resp = _upload(client, seeded["own_case"])
    assert resp.status_code == 200, f"本租户律师不能上传 ⇒ 功能被焊死：{resp.status_code}"


# ═══════════════════════ V1–V4 重解析端点 ═══════════════════════


def test_other_client_cannot_reparse(app_factory, seeded):
    """V1：客户乙重解析客户甲的证据 ⇒ 404。

    `reparse` 只做了 `ev.tenant_id != ctx.tenant_id` 的**租户**判等，
    没做客户归属校验；而它返回的是 `EvidenceOut` **全字段**——含 `ocr_text`
    与 `legality_risks`。也就是说，读端点已在 `_case_or_404` 堵住的泄露
    （文件名足以暴露纠纷性质），在这个**写**端点上仍然敞开，而且还能覆盖解析结果。
    """
    from fastapi.testclient import TestClient

    eid = seeded["evidence"]

    with TestClient(app_factory(seeded["client_b"])) as client:
        resp = client.post(f"/api/v1/evidence/{eid}/parse")
    assert resp.status_code == 404, f"客户乙能重解析他人证据：{resp.status_code} {resp.text[:300]}"


def test_cross_tenant_reparse_rejected(app_factory, seeded):
    """V2：跨租户重解析 ⇒ 404（老口径，防止修 V1 时把租户隔离改松）。"""
    from fastapi.testclient import TestClient

    # 租户 B 没有证据行，用「不存在的 id」验证不会退化成 200/500
    with TestClient(app_factory(seeded["client_a"])) as client:
        resp = client.post("/api/v1/evidence/999999/parse")
    assert resp.status_code == 404, f"不存在的证据应 404，实际 {resp.status_code}"


def test_owner_can_reparse_own_evidence(app_factory, seeded):
    """V3（**反向量**）：客户甲重解析自己的证据 ⇒ 200。

    防「一律 404」。重解析是产品里的正常操作（解析失败后重试）。
    """
    from fastapi.testclient import TestClient

    eid = seeded["evidence"]

    with TestClient(app_factory(seeded["client_a"])) as client:
        resp = client.post(f"/api/v1/evidence/{eid}/parse")
    assert resp.status_code == 200, f"本人不能重解析 ⇒ 功能被焊死：{resp.status_code} {resp.text[:300]}"


def test_lawyer_can_reparse_tenant_evidence(app_factory, seeded):
    """V4（**反向量**）：本租户律师重解析 ⇒ 200（租户级边界，同 U8）。"""
    from fastapi.testclient import TestClient

    eid = seeded["evidence"]

    with TestClient(app_factory(seeded["lawyer"])) as client:
        resp = client.post(f"/api/v1/evidence/{eid}/parse")
    assert resp.status_code == 200, f"本租户律师不能重解析：{resp.status_code} {resp.text[:300]}"
