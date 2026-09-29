"""落盘路径的目录穿越防线 + 租户上下文断言（2026-09-20）。

## 为什么这个文件必须存在

`app/services/storage_service.py::save_upload` 是产品**唯一**把用户上传写到磁盘的
路径，清查发现它：

- `save_upload` / `_safe_name` 在 `tests/` 下 **0 命中**；
- `test_evidence_authz.py` 只测了**读**端点（`test_client_cannot_read_*` /
  `test_cross_tenant_denied` / `test_lawyer_can_read_*`），
  **上传端点 `POST /cases/{case_id}` 从未被请求过**；
- 而它用 `os.path.join(tenant_id, subdir)` 直接拼路径 —— `tenant_id` 对平台管理员
  而言就是 `X-Tenant-Id` 头部的任意字符串（`deps.py::get_tenant_context` 只 strip 了空白）。

**实测逃逸**（改动前）：

```
tenant_id="../victim-tenant"  -> 落在存储根之外
tenant_id="..\\..\\windows"   -> 落在存储根之外
tenant_id="/etc"              -> 在 Windows 上跨到盘符根
```

⇒ 这是一条**静默**的目录穿越：不报错、文件照写，只是写进了别人的目录。
本轮补上 `_resolve_under_root()`（落盘前 `abspath` + `commonpath` 验证）。

## 覆盖清单

| 编号 | 性质 |
|------|------|
| S1 | `_safe_name`：恶意文件名 ⇒ 输出**不含**分隔符/空字节/原名（参数化 9 种） |
| S2 | `_safe_name`：空 / None ⇒ 仍然安全 |
| S3 | `_resolve_under_root`：正常片段 ⇒ 在根内（反向量） |
| S4 | `_resolve_under_root`：逃逸片段 ⇒ 拒绝（参数化 5 种） |
| S5 | `save_upload`：正常租户 ⇒ 真落盘且**确实在根内**（反向量：防"把路径焊死"） |
| S6 | `save_upload`：恶意 tenant_id ⇒ 拒绝，**且不在别处留下文件** |
| A1 | `TenantContext.assert_tenant`：不匹配 ⇒ 拒绝 |
| A2 | `assert_tenant`：匹配 ⇒ 放行 |
| A3 | **登记型**：空 / None 目标**绕过**校验（已知语义） |
"""
from __future__ import annotations

import io
import os
import pathlib
import re
import shutil
import uuid

import pytest


@pytest.fixture()
def storage_root():
    """存储根目录（用完即删）。

    ⚠️ 刻意**不用** pytest 的 `storage_root`：沙箱下系统临时目录会报
    `PermissionError: [WinError 5] 拒绝访问`（实测，12 个用例因此 error），
    改用仓库内的 `_tmp_tests/`（与 `test_review_tenant_guard.py` 同款做法）。
    """
    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    d = base / f"storage_{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)

SAFE_NAME_RE = re.compile(r"[0-9a-f]{32}(\.[a-z0-9]+)?")

NASTY_FILENAMES = [
    "../../../../etc/passwd",
    "..\\..\\windows\\system32\\config\\sam",
    "/etc/passwd",
    "a/b/c.pdf",
    "....//....//x.pdf",
    "x.pdf\x00.sh",
    "..",
    ".",
    "  .pdf",
]

ESCAPING_TENANTS = [
    "../victim-tenant",
    "..\\..\\windows",
    "a/../../b",
    "/etc",
    "..",
]


# ═══════════════════════ S1–S2 `_safe_name` ═══════════════════════


@pytest.mark.parametrize("filename", NASTY_FILENAMES)
def test_safe_name_never_leaks_attacker_input(filename):
    """S1：恶意文件名不得产出含分隔符 / 空字节 / 原始名字的结果。

    判据写成"输出必须长这样"（32 位 hex + 可选扩展名），而不是"不得包含 `..`"——
    后者是黑名单，漏一个变形就绿；前者是白名单，任何漏网字符都会红。
    """
    from app.services.storage_service import _safe_name

    name = _safe_name(filename)

    assert SAFE_NAME_RE.fullmatch(name), (
        f"净化后的名字不符合白名单形状：{name!r}（输入 {filename!r}）"
    )
    assert "/" not in name and "\\" not in name, f"名字里出现了路径分隔符：{name!r}"
    assert "\x00" not in name, f"名字里出现了空字节：{name!r}"
    assert name.split(".")[0] != filename, "原始文件名被整段保留了"


def test_safe_name_handles_empty_and_none():
    """S2：空文件名 / None ⇒ 仍产出安全名字（不能抛异常把上传打断，也不能产出空串）。"""
    from app.services.storage_service import _safe_name

    for empty in ("", None):
        name = _safe_name(empty)
        assert SAFE_NAME_RE.fullmatch(name), f"{empty!r} ⇒ {name!r} 不符合白名单形状"


# ═══════════════════════ S3–S4 `_resolve_under_root` ═══════════════════════


def test_resolve_under_root_keeps_normal_paths(storage_root, monkeypatch):
    """S3：反向量——正常租户目录必须**能**解析成功，否则就是把路径焊死了。"""
    from app.config import settings
    from app.services.storage_service import _resolve_under_root

    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", str(storage_root))
    target = _resolve_under_root("tenant-a", "evidence")
    assert os.path.abspath(target).startswith(os.path.abspath(str(storage_root)))


@pytest.mark.parametrize("tenant_id", ESCAPING_TENANTS)
def test_resolve_under_root_rejects_escaping_paths(storage_root, monkeypatch, tenant_id):
    """S4：逃逸出存储根目录的路径必须被**显式拒绝**。

    参数是**租户 ID**而不是文件名 —— 因为文件名侧已有 `_safe_name` 兜底，
    真正没人管的是 `tenant_id` 这个参与拼路径、却来自请求头的值。
    """
    from app.config import settings
    from app.core.errors import BadRequestError
    from app.services.storage_service import _resolve_under_root

    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", str(storage_root))
    with pytest.raises(BadRequestError):
        _resolve_under_root(tenant_id, "evidence")


# ═══════════════════════ S5–S6 `save_upload` 端到端 ═══════════════════════


def _upload_file(filename: str = "证据材料.pdf", content: bytes = b"irrelevant"):
    from fastapi import UploadFile

    return UploadFile(file=io.BytesIO(content), filename=filename)


async def test_save_upload_writes_inside_storage_root(storage_root, monkeypatch):
    """S5：正常租户 ⇒ 真的落盘，且落点**确实在存储根内**。

    反向量：没有这条，把 `_resolve_under_root` 改成一律拒绝，S4/S6 照样全绿，
    上传功能整个废掉而无人知晓。
    """
    from app.config import settings
    from app.services.storage_service import save_upload

    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", str(storage_root))

    rel_path, size, _ctype = await save_upload(_upload_file(), "tenant-a")

    assert size == len(b"irrelevant")
    assert rel_path.startswith("tenant-a/evidence/"), rel_path
    abs_path = os.path.join(str(storage_root), rel_path.replace("/", os.sep))
    assert os.path.exists(abs_path), f"文件没落盘：{abs_path}"
    assert os.path.abspath(abs_path).startswith(os.path.abspath(str(storage_root)))
    # 落盘文件名必须是净化后的 uuid 名，不能是"证据材料.pdf"
    assert "证据材料" not in rel_path, "原始文件名被带进了落盘路径"


@pytest.mark.parametrize("tenant_id", ESCAPING_TENANTS)
async def test_save_upload_refuses_escaping_tenant_id(storage_root, monkeypatch, tenant_id):
    """S6：恶意 tenant_id ⇒ 拒绝，且**不在存储根之外留下任何文件**。

    「不留文件」这条单独断言是有意义的：只拒绝、但先 `makedirs` 再拒绝，
    仍然会在根外创建目录（副作用已发生）。
    """
    from app.config import settings
    from app.core.errors import BadRequestError
    from app.services.storage_service import save_upload

    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", str(storage_root))
    before = {p for p in os.listdir(storage_root)}

    with pytest.raises(BadRequestError):
        await save_upload(_upload_file(), tenant_id)

    assert {p for p in os.listdir(storage_root)} == before, "拒绝前已经在存储根里写了东西"


# ═══════════════════════ A1–A3 `assert_tenant` ═══════════════════════


def _ctx(tenant_id: str = "t1"):
    from app.core.deps import TenantContext
    from app.core.rbac import Role

    return TenantContext(tenant_id=tenant_id, user_id=1, role=Role.LAWYER)


def test_assert_tenant_rejects_mismatch():
    """A1：目标资源属于别的租户 ⇒ 拒绝。"""
    from app.core.errors import TenantDeniedError

    with pytest.raises(TenantDeniedError):
        _ctx("t1").assert_tenant("t2")


def test_assert_tenant_allows_match():
    """A2：反向量——本租户的资源必须放行。"""
    _ctx("t1").assert_tenant("t1")


@pytest.mark.parametrize("target", ["", None])
def test_assert_tenant_silently_allows_falsy_target(target):
    """A3：**登记型**判据 —— 空 / None 目标会**绕过**校验。

    原实现是 `if target_tenant_id and target_tenant_id != self.tenant_id:`
    ⇒ 目标为空时**直接放行**，与 §3.13 那个 `"   "` 是 truthy 的坑同族
    （一个被 falsy 绕过、一个被 truthy 绕过，方向相反，都是"判空写错"）。

    现状下数据模型的 `tenant_id` 非空，所以**不构成漏洞**；但如果将来某张表的
    `tenant_id` 允许为 NULL，这条守卫会静默放行 ⇒ 先把这个语义钉住，
    将来谁收紧了它，这里会红，强制回头确认调用方。
    """
    _ctx("t1").assert_tenant(target)  # 不抛即为当前语义
