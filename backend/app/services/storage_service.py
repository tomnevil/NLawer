"""本地存储服务（MinIO 接口预留）。

MVP 落盘到 `settings.LOCAL_STORAGE_PATH`，按租户分子目录；
对象存储接入后替换 `_write` 实现即可，调用方无需改动。
"""
import os
import uuid
from typing import Optional

from fastapi import UploadFile

from app.config import settings
from app.core.errors import BadRequestError, ErrorCode

# 允许的证据文件类型
ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".doc", ".docx", ".txt", ".xls", ".xlsx"}
MAX_SIZE = 20 * 1024 * 1024  # 20MB


def _safe_name(filename: str) -> str:
    """把任意上传文件名换成**与攻击者输入无关**的 `{uuid}{ext}`。

    ⚠️ 保留扩展名是为了可检索/可预览，但**必须**是"从净化后的 basename 取 ext"，
    绝不能把原始文件名整段拼进路径 —— 否则 `../../etc/passwd` 就能落到存储根之外。
    """
    base = os.path.basename(filename or "")
    ext = os.path.splitext(base)[1].lower()
    return f"{uuid.uuid4().hex}{ext}"


def _resolve_under_root(*parts: str) -> str:
    """把若干路径片段拼到存储根目录下，**并确保结果仍在根目录内**。

    ⚠️ 为什么必须显式验证：`save_upload` 用 `tenant_id` 直接参与拼路径
    （`os.path.join(tenant_id, subdir)`），而 `tenant_id` 对平台管理员而言就是
    `X-Tenant-Id` 头部的任意字符串（`deps.py::get_tenant_context` 只做了 strip）。
    实测：`tenant_id="../victim-tenant"` ⇒ 解析后落在存储根**之外**；
    `tenant_id="/etc"` 在 Windows 上甚至会跨到盘符根。

    ⇒ 落盘前必须 `abspath` + `commonpath` 验证，否则就是一条静默的目录穿越
    （不报错、文件照写，只是写到了别人的目录里）。
    """
    root = os.path.abspath(settings.LOCAL_STORAGE_PATH)
    target = os.path.abspath(os.path.join(root, *parts))
    try:
        contained = os.path.commonpath([root, target]) == root
    except ValueError:
        # 不同盘符时 commonpath 抛 ValueError —— 一律按逃逸处理
        contained = False
    if not contained:
        raise BadRequestError(
            "非法的存储路径",
            code=ErrorCode.VALIDATION_ERROR,
            details={"reason": "path escapes storage root"},
        )
    return target


async def save_upload(file: UploadFile, tenant_id: str, subdir: str = "evidence") -> tuple[str, int, Optional[str]]:
    """保存上传文件，返回 (相对路径, 字节数, 内容类型)。"""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise BadRequestError(
            f"不支持的文件类型：{ext or '未知'}",
            code=ErrorCode.EVIDENCE_TYPE_NOT_ALLOWED,
            details={"allowed": sorted(ALLOWED_EXT)},
        )

    content = await file.read()
    if len(content) > MAX_SIZE:
        raise BadRequestError("文件超过 20MB 上限", code=ErrorCode.EVIDENCE_FILE_TOO_LARGE)

    rel_dir = os.path.join(tenant_id, subdir)
    abs_dir = _resolve_under_root(rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    name = _safe_name(file.filename or "upload")
    abs_path = _resolve_under_root(rel_dir, name)

    def _write() -> None:
        with open(abs_path, "wb") as f:
            f.write(content)

    import asyncio

    await asyncio.to_thread(_write)
    return os.path.join(rel_dir, name).replace("\\", "/"), len(content), file.content_type
