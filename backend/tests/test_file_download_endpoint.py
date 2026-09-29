"""卷宗文件下载端点 `GET /api/v1/files/{tenant_id}/{subdir}/{filename}` 的端点层判据。

## 为什么这个文件必须存在

2026-09-20 的「路由 × 请求次数」全量扫描（`_scan_routes.py`，扫完即删）给出：
**79 条路由里 56 条在 `tests/` 下从未被请求过**。本端点是其中风险最高的一条，
因为它是全站**唯一**把磁盘文件直接吐给客户端的接口，且是早期
`StaticFiles` 挂载（完全绕过鉴权）整改后的产物——整改有没有真的守住，
此前**没有任何判据**。

扫描还暴露出一个结构性事实：`app/api/v1/*.py` 共 14 个模块引用
`ctx.tenant_id`，而 `files.py` 是**唯一一个引用数为 0**的模块——它改用
`user.tenant_id` 判等。这条差异在普通用户身上看不出来（两者恒等），
只在平台管理员身上显形：

- `deps.py::get_tenant_context` 明确写着「平台管理员可通过 `X-Tenant-Id`
  切换到目标租户进行运营管理」；
- 于是管理员切到租户 A 后，列表接口（用 ctx）能**看到**材料，
  下载接口（用 user）却 **404** —— 同一个功能被两套身份切成两半。

⇒ 期望值的出处是三处一致的产品约定（模块 docstring「路径首段必须是当前
**请求所属**租户 ID」、`get_tenant_context` 的切换语义、其余 14 个模块的通行做法），
不是本文件自己拍的。

## 覆盖清单

| 编号 | 性质 |
|------|------|
| D1 | 本人下载自己租户下的文件 ⇒ 200，且**字节一致**（反向量：防把端点焊死） |
| D2 | 路径租户与请求租户不符 ⇒ 404 `RESOURCE_NOT_FOUND`（不回 403，避免泄露存在性） |
| D3 | 扩展名不在白名单 ⇒ 404；白名单内 ⇒ 200（双向） |
| D4 | 平台管理员 + `X-Tenant-Id` 切换 ⇒ 200（**修复前为红**，缺陷①） |
| D5 | 越出「租户目录」但仍在存储根内（`../`）⇒ 404 且**不吐字节**（**修复前为红**，缺陷②） |
| D6 | `_resolve_abs_path` 逃逸向量参数化 ⇒ 拒绝；正常片段 ⇒ 放行（反向量） |
| D7 | 审计留痕：成功下载 ⇒ 一条 `FILE_DOWNLOAD`；留痕抛错 ⇒ 下载仍 200 |
| D8 | AST：鉴权必须落在 `ctx.tenant_id` 上，不得退回 `user.tenant_id` |

⚠️ 本文件**刻意不覆盖 `get_tenant_context`**（`methodology.md` 82）：
覆盖掉它就测不到「租户上下文到底从哪来」，D4 会变成假绿。
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import uuid

import pytest
from sqlalchemy import select

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n%%EOF\n"


# ═══════════════════════ 夹具 ═══════════════════════


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"filedl_{uuid.uuid4().hex[:8]}.db"
    return create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)


@pytest.fixture(scope="module")
def seeded(engine):
    """租户 A 的客户甲 + 一个平台管理员（自身租户 "platform"）。"""
    import app.models  # noqa: F401
    from app.core.rbac import Role
    from app.core.security import hash_password
    from app.models.base import Base
    from app.models.enums import UserStatus
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
                    username=f"fdl-{tag}-{uuid.uuid4().hex[:6]}",
                    hashed_password=hash_password("Str0ngPass!"),
                    full_name=tag,
                    role=role,
                    tenant_id=tid,
                    status=UserStatus.ACTIVE,
                    is_active=True,
                )

            client_a = _user("clientA", Role.CLIENT, TENANT_A)
            # Q-S：同租户的**另一个客户**乙 —— 用来验证「下载按案件归属授权」
            client_b = _user("clientB", Role.CLIENT, TENANT_A)
            # 律师：D3（扩展名白名单）要用 —— 白名单与「案件归属」是**正交**的两道门，
            # D3 会现写一个临时文件（没有 Evidence 记录），用客户身份会被
            # Q-S 的「失败即拒」挡掉，测的就不是白名单了。
            lawyer = _user("lawyer", Role.LAWYER, TENANT_A)
            # 平台管理员是**唯一**能让 user.tenant_id != ctx.tenant_id 的身份
            admin = _user("admin", Role.PLATFORM_ADMIN, "platform")
            s.add_all([client_a, client_b, lawyer, admin])
            await s.flush()

            # Q-S 反查链路：`storage_root` 里那份 `evidence/离职证明.pdf`
            # 必须有对应 Evidence 记录、且归属客户甲的案件。
            # （新检查对客户是**失败即拒**，没有这条记录的话 D1 也会 404。）
            from app.models.case import Case
            from app.models.enums import (
                CaseGrade,
                CaseStatus,
                EvidenceCategory,
                EvidenceStatus,
            )
            from app.models.evidence import Evidence

            case_a = Case(
                case_no=f"FDL-A-{uuid.uuid4().hex[:6]}",
                tenant_id=TENANT_A,
                title="客户甲的案件",
                client_user_id=client_a.id,
                status=CaseStatus.INTAKE,
                grade=CaseGrade.B,
                dispute_type="劳动争议",
            )
            s.add(case_a)
            await s.flush()
            s.add(
                Evidence(
                    tenant_id=TENANT_A,
                    case_id=case_a.id,
                    uploaded_by=client_a.id,
                    name="离职证明.pdf",
                    file_path=f"{TENANT_A}/evidence/离职证明.pdf",
                    file_type="application/pdf",
                    file_size=len(PDF_BYTES),
                    status=EvidenceStatus.UPLOADED,
                    category=EvidenceCategory.CONTRACT,
                )
            )
            await s.commit()
            return {
                "client_a": client_a.id,
                "client_b": client_b.id,
                "lawyer": lawyer.id,
                "admin": admin.id,
            }

    return asyncio.run(_seed())


@pytest.fixture
def storage_root(monkeypatch):
    """把存储根指到本次用例的临时目录。

    ⚠️ 不用 pytest 的 `tmp_path`：沙箱里系统临时目录不可写（`WinError 5`）。
    """
    root = (
        pathlib.Path(__file__).resolve().parent.parent
        / "_tmp_tests"
        / f"fdl-st-{uuid.uuid4().hex[:8]}"
    )
    (root / TENANT_A / "evidence").mkdir(parents=True, exist_ok=True)
    (root / TENANT_B / "evidence").mkdir(parents=True, exist_ok=True)

    # 租户 A 自己的材料
    (root / TENANT_A / "evidence" / "离职证明.pdf").write_bytes(PDF_BYTES)
    # 租户 B 的材料（D2 用）
    (root / TENANT_B / "evidence" / "对方证据.pdf").write_bytes(b"%PDF-1.4 B\n")
    # ⚠️ 落在**存储根内、但不在任何租户目录下**的文件：D5 的诱饵。
    # 现实中不该存在，但它恰好能区分「只校验在根内」和「校验在租户目录内」。
    (root / "secret-at-root.pdf").write_bytes(b"ROOT-LEVEL-SECRET")

    from app.config import settings

    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", str(root))
    return root


@pytest.fixture
def app_factory(engine, storage_root, monkeypatch):
    """真实 `create_app()` + 只覆盖 `get_db` / `get_current_user`。

    `log_detached_ctx` 换成记录型桩：既避免审计落到另一个库（detached 会话
    走的是全局引擎，不是本夹具的库），又把「留了什么痕」变成可断言的量。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    import app.api.v1.files as files_mod
    from app.core.deps import get_current_user, get_db
    from app.main import create_app

    calls: list[dict] = []

    async def _stub_audit(action, resource_type, **kwargs):
        calls.append({"action": action, "resource_type": resource_type, **kwargs})
        return None

    monkeypatch.setattr(files_mod, "log_detached_ctx", _stub_audit)

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

    _make.calls = calls  # type: ignore[attr-defined]
    return _make


def _get(app, path, headers=None):
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        return client.get(path, headers=headers or {})


def _code(resp) -> str:
    return str(resp.json().get("error", {}).get("code", ""))


# ═══════════════════════ D1–D5 端点层 ═══════════════════════


def test_owner_can_download_own_tenant_file(app_factory, seeded):
    """D1（反向量）：本人下载自己租户下的文件 ⇒ 200 且字节一致。

    反向量是必须的：只测「拒绝」的判据会被「把端点焊死（永远 404）」骗过去。
    """
    app = app_factory(seeded["client_a"])
    resp = _get(app, f"/api/v1/files/{TENANT_A}/evidence/离职证明.pdf")
    assert resp.status_code == 200, resp.text
    assert resp.content == PDF_BYTES


def test_other_client_in_same_tenant_cannot_download(app_factory, seeded):
    """D9（Q-S 已裁定）：同租户的**客户乙**下载客户甲案件的卷宗原文 ⇒ 404。

    Q-S 拍板前，下载只校验**租户** ⇒ 同租户客户甲可下载客户乙的卷宗原文；
    而重解析（ `_evidence_or_404`）早已按案件归属卡住 ⇒ **修了一半**：
    「解析结果看不了，原文却能下」，等于没修。

    本用例同时是 D1 的**反向量搭档**：D1 证明归属者能下，本条证明非归属者不能下。
    """
    app = app_factory(seeded["client_b"])
    resp = _get(app, f"/api/v1/files/{TENANT_A}/evidence/离职证明.pdf")
    assert resp.status_code == 404, (
        f"同租户他人下载了别人案件的卷宗原文：{resp.status_code} {resp.text[:200]}"
    )


def test_mismatched_tenant_returns_404_not_403(app_factory, seeded):
    """D2：路径里的租户与请求租户不符 ⇒ 404，且**不是** 403。

    403 会泄露「该文件存在，只是你没权限」；产品约定是不回 403。
    """
    app = app_factory(seeded["client_a"])
    resp = _get(app, f"/api/v1/files/{TENANT_B}/evidence/对方证据.pdf")
    assert resp.status_code == 404, resp.text
    # 必须是**端点**判的 404，不是「路由没匹配上」。FastAPI 的路由未命中
    # 返回 `{"detail": "Not Found"}`，不带 error.code —— 用这个把两者分开，
    # 否则路径写错时判据会假绿（`methodology.md` 87：先怀疑注入/请求没生效）。
    assert _code(resp) == "RESOURCE_NOT_FOUND", resp.text
    assert b"%PDF-1.4 B" not in resp.content


@pytest.mark.parametrize(
    "name",
    [
        "离职证明.exe",      # 扩展名不在白名单
        "离职证明",          # 无扩展名
        "离职证明.PDF.exe",  # 双扩展名：只看最后一个
        "离职证明.PDF",      # 大写扩展名：端点已 lower，故应放行（反向量）
    ],
)
def test_extension_whitelist(app_factory, seeded, storage_root, name):
    """D3：扩展名白名单双向判据——白名单外 ⇒ 404，白名单内 ⇒ 200。

    ⚠️ 双向是必须的：只测拒绝的话，把白名单改成空集合也能全绿（把门焊死）。
    ⚠️ 别在函数体里 `import app.config`：`app` 在本文件里是 FastAPI 实例名，
    会被 import 语句重新绑成模块，`TestClient(app)` 直接报
    `'module' object is not callable`。

    ⚠️ 用**律师**而非客户：本用例现写的临时文件没有 Evidence 记录，
    以客户身份会被 Q-S 的「失败即拒」挡掉（那是归属门，不是白名单门），
    测的就不再是白名单了。白名单与归属是**正交**的两道门，要分开测。
    """
    app = app_factory(seeded["lawyer"])
    (storage_root / TENANT_A / "evidence" / name).write_bytes(PDF_BYTES)

    resp = _get(app, f"/api/v1/files/{TENANT_A}/evidence/{name}")
    if name.lower().endswith(".pdf"):
        assert resp.status_code == 200, resp.text
    else:
        assert resp.status_code == 404, resp.text


def test_platform_admin_in_switched_tenant_can_download(app_factory, seeded):
    """D4：平台管理员切到租户 A ⇒ 能下载 A 的卷宗。

    **这是缺陷①的红灯判据。** 修复前 `download_file` 用 `user.tenant_id`
    （恒为 `"platform"`）与路径租户判等 ⇒ 管理员在任何被运营的租户里都只能拿到
    404，而同一份材料在列表接口（用 `ctx`）里看得见——功能被两套身份切成两半。

    期望值出处：`deps.py::get_tenant_context` 的切换语义 + 其余 14 个模块用
    `ctx.tenant_id` 的通行做法 + 本模块 docstring 的「当前**请求所属**租户」。
    """
    app = app_factory(seeded["admin"])
    resp = _get(
        app,
        f"/api/v1/files/{TENANT_A}/evidence/离职证明.pdf",
        headers={"X-Tenant-Id": TENANT_A},
    )
    assert resp.status_code == 200, resp.text
    assert resp.content == PDF_BYTES


def test_admin_switch_does_not_grant_other_tenant(app_factory, seeded):
    """D4b：管理员切到租户 B 后，仍拿不到租户 A 的文件（防「改成 ctx」时顺手删校验）。"""
    app = app_factory(seeded["admin"])
    resp = _get(
        app,
        f"/api/v1/files/{TENANT_A}/evidence/离职证明.pdf",
        headers={"X-Tenant-Id": TENANT_B},
    )
    assert resp.status_code == 404, resp.text


def test_cannot_escape_tenant_directory_within_storage_root(app_factory, seeded):
    """D5：`%2e%2e` 越出租户目录、但仍在存储根内 ⇒ 拒绝，且不吐字节。

    **这是缺陷②的红灯判据。** 修复前 `_resolve_abs_path` 只校验「在存储根内」，
    于是 `/files/{自己的租户}/%2e%2e/secret-at-root.pdf` 会解析到**存储根**下
    的文件并正常 200（实测：`ROOT-LEVEL-SECRET` 原样返回）——
    租户目录这道边界等于不存在。

    ⚠️ **URL 里必须写 `%2e%2e` 而不是 `..`**：httpx 会规范化掉字面量的点段，
    请求根本进不到端点，返回的是 FastAPI 的 `{"detail":"Not Found"}`。
    那样判据会**假红**——看起来像被拦住了，其实只是没走到（`methodology.md` 87）。
    判据因此同时断言 `_code(resp)` 非空：路由未命中的响应没有 `error.code`。

    期望 403 `PERMISSION_DENIED` 而非 404：逃逸属于安全防线拦截，与既有的
    「越出存储根」保持一致；404 只用于「不是你的 / 不存在」这类业务判等。
    """
    app = app_factory(seeded["client_a"])
    resp = _get(app, f"/api/v1/files/{TENANT_A}/%2e%2e/secret-at-root.pdf")
    assert resp.status_code == 403, resp.text
    assert _code(resp) == "PERMISSION_DENIED", resp.text
    assert b"ROOT-LEVEL-SECRET" not in resp.content


# ═══════════════════════ D6 _resolve_abs_path 单元层 ═══════════════════════


@pytest.mark.parametrize(
    "rel",
    [
        "../../etc/passwd",
        "..\\..\\windows\\win.ini",
        "/etc/passwd",
        "C:\\windows\\win.ini",
        "a/../../outside.pdf",
        f"{TENANT_A}/../secret-at-root.pdf",
    ],
)
def test_resolve_abs_path_never_leaves_storage_root(storage_root, rel):
    """D6：不论走哪条分支，解析结果**绝不能落在存储根之外**。

    不写「必须 raise」：`rel_path.lstrip("/\\\\")` 会把 `/etc/passwd` 变成
    `etc/passwd`，于是它是被**收进根内**而不是被拒绝——这也是安全的。
    真正的不变量只有一条：**结果在根内，或者被拒**。

    另设**拒绝数下界**（≥3）：若把校验整段删掉，多数向量会直接返回根外路径，
    不变量会红；但如果只留下「全部收进根内」的实现，不变量仍绿却已改变语义，
    下界能把这种退化钉出来。
    """
    from app.api.v1.files import _resolve_abs_path

    root = os.path.realpath(str(storage_root))
    raised = False
    try:
        got = _resolve_abs_path(rel)
    except Exception as exc:  # noqa: BLE001 - 任何拒绝都算「拦住了」
        raised = True
        assert getattr(exc, "status_code", 403) in (400, 403)
    if not raised:
        assert got == root or got.startswith(root + os.sep), (rel, got)


def test_resolve_abs_path_rejects_real_escapes(storage_root):
    """D6b：真正跨出存储根的向量必须被**拒绝**，而不是被悄悄收进根内。"""
    from app.api.v1.files import _resolve_abs_path
    from app.core.errors import ErrorCode, PermissionDeniedError

    rejected = 0
    for rel in ("../../etc/passwd", "..\\..\\windows\\win.ini",
                "C:\\windows\\win.ini", "a/../../outside.pdf"):
        try:
            _resolve_abs_path(rel)
        except PermissionDeniedError as exc:
            assert exc.code == ErrorCode.PERMISSION_DENIED
            rejected += 1
    assert rejected >= 3, f"只有 {rejected} 个逃逸向量被拒——防线被削弱了"


def test_resolve_abs_path_allows_normal_path(storage_root):
    """D6b（反向量）：正常片段 ⇒ 解析结果确实落在根内。"""
    from app.api.v1.files import _resolve_abs_path

    got = _resolve_abs_path(os.path.join(TENANT_A, "evidence", "离职证明.pdf"))
    assert got.startswith(os.path.realpath(str(storage_root)) + os.sep)
    assert os.path.isfile(got)


# ═══════════════════════ D7 审计留痕 ═══════════════════════


def test_download_writes_file_download_audit(app_factory, seeded):
    """D7：下载成功 ⇒ 落一条 `FILE_DOWNLOAD` 审计（等保/律所审计要求卷宗调阅留痕）。"""
    app = app_factory(seeded["client_a"])
    resp = _get(app, f"/api/v1/files/{TENANT_A}/evidence/离职证明.pdf")
    assert resp.status_code == 200
    actions = [c["action"] for c in app_factory.calls]
    assert any(str(a).endswith("FILE_DOWNLOAD") for a in actions), actions


def test_audit_failure_does_not_block_download(app_factory, seeded, monkeypatch):
    """D7b：留痕抛错 ⇒ 下载仍然 200。

    这条契约由 `log_detached` 内部的重试 + 吞异常保证（`audit.py:158-167` 明写
    「审计失败绝不阻断主流程」是无条件契约）。钉住它是为了防止将来有人在
    端点里加一句 `raise` 把它改坏。
    """
    import app.api.v1.files as files_mod

    async def _boom(*a, **kw):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(files_mod, "log_detached_ctx", _boom)
    app = app_factory(seeded["client_a"])
    resp = _get(app, f"/api/v1/files/{TENANT_A}/evidence/离职证明.pdf")
    assert resp.status_code == 200, resp.text


# ═══════════════════════ D8 AST 判据 ═══════════════════════


def test_authorization_uses_context_tenant_not_user_tenant():
    """D8：`download_file` 的租户判等必须落在 `ctx.tenant_id` 上。

    AST 判据必须带**命中数下界自检**（`methodology.md` 里那条「AST 判据会静默
    退化成空集」）：函数找不到就直接失败，而不是 0 命中却报通过。
    """
    import ast

    src = (
        pathlib.Path(__file__).resolve().parent.parent
        / "app"
        / "api"
        / "v1"
        / "files.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "download_file"
        ),
        None,
    )
    assert fn is not None, "download_file 端点不见了——判据必须先自检再断言"

    seg = ast.get_source_segment(src, fn) or ""
    assert "ctx.tenant_id" in seg, "鉴权没有用 ctx.tenant_id（缺陷①回归）"
    assert "get_tenant_context" in seg
    # 反向量：不得退回 user.tenant_id 判等
    assert 'getattr(user, "tenant_id"' not in seg
