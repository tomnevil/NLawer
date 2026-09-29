"""文件访问接口：带鉴权的证据/文书下载。

**为什么不能用 StaticFiles 直接挂载？**
早期实现是 `app.mount("/storage", StaticFiles(...))`，该挂载点**完全绕过鉴权**——
任何人只要知道（或猜到）URL 即可下载他人租户的证据材料与文书，属于法律产品
最严重的越权数据泄露（违反《律师法》保密义务与《个人信息保护法》）。

现改为显式接口：路径首段必须是当前请求所属租户 ID，再做文件存在性校验。
可选 `?token=` 查询参数用于 `<img>` / 新窗口下载等无法携带 Header 的场景。
"""
import os
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit import AuditAction, log_detached_ctx
from app.core.deps import TenantContext, get_current_user, get_db, get_tenant_context
from app.core.errors import ErrorCode, NotFoundError, PermissionDeniedError
from app.core.rbac import Role
from app.models.case import Case
from app.models.evidence import Evidence

router = APIRouter(prefix="/files", tags=["文件访问"])

#: 允许下载的扩展名（与上传白名单保持一致的子集，防止误暴露非预期文件）
_ALLOWED_EXT = {
    ".pdf", ".png", ".jpg", ".jpeg", ".webp",
    ".doc", ".docx", ".txt", ".xls", ".xlsx",
}


def _resolve_abs_path(rel_path: str, tenant_root: Optional[str] = None) -> str:
    """将请求路径安全地解析为磁盘绝对路径。

    三重防穿越：
      1. 规范化后必须以 storage 根目录为前缀（挡住 `../../etc/passwd`）
      2. 拒绝符号链接逃逸（`realpath` 后再校验一次）
      3. 给了 `tenant_root` 时，还必须落在该**租户目录内**

    ⚠️ 第 3 条是 2026-09-21 补的：只校验「在存储根内」时，
    `GET /files/{自己的租户}/%2e%2e/secret.pdf` 会解析到**存储根**下、
    不属于任何租户的文件并正常 200（`subdir`/`filename` 是路径参数，
    字面量的 `..` 会被 httpx 规范化掉，但 `%2e%2e` 不会）。
    由 `tests/test_file_download_endpoint.py::D5` 钉住。
    """
    root = os.path.realpath(os.path.abspath(settings.LOCAL_STORAGE_PATH))
    candidate = os.path.realpath(os.path.join(root, rel_path.lstrip("/\\")))
    if candidate != root and not candidate.startswith(root + os.sep):
        raise PermissionDeniedError("非法的文件路径", code=ErrorCode.PERMISSION_DENIED)
    if tenant_root is not None:
        troot = os.path.realpath(tenant_root)
        if candidate != troot and not candidate.startswith(troot + os.sep):
            raise PermissionDeniedError("非法的文件路径", code=ErrorCode.PERMISSION_DENIED)
    return candidate


@router.get("/{tenant_id}/{subdir}/{filename}", summary="下载租户文件（鉴权）")
async def download_file(
    tenant_id: str,
    subdir: str,
    filename: str,
    token: Optional[str] = Query(None, description="可选：浏览器直链场景的访问令牌"),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """下载指定租户下的文件。

    安全约束：路径中的 `tenant_id` **必须等于当前请求的租户上下文**
    （`ctx.tenant_id`），否则直接 404（不返回 403，避免泄露文件是否存在）。

    ⚠️ **必须用 `ctx.tenant_id` 而不是 `user.tenant_id`**（2026-09-21 修正）。
    平台管理员靠 `X-Tenant-Id` 切换到被运营的租户后，两者是不同的：
    `ctx.tenant_id` 是目标租户，`user.tenant_id` 恒为其自身的 `"platform"`。
    改用 `user` 判等的后果是——管理员在列表接口（用 ctx）**看得见**材料，
    在下载接口（用 user）却只能拿到 404，同一功能被两套身份切成两半。
    由 `tests/test_file_download_endpoint.py::D4` 钉住。

    **合规约束（等保 / 律所审计）**：卷宗原文的获取必须留痕。审计用
    `log_detached` 写入独立会话——本接口直接返回 `FileResponse`，不经过
    请求事务的 commit，用当前会话写审计会随请求结束而丢失。
    """
    if ctx.tenant_id != tenant_id:
        raise NotFoundError("文件不存在", code=ErrorCode.RESOURCE_NOT_FOUND)

    ext = os.path.splitext(filename)[1].lower()
    if ext not in _ALLOWED_EXT:
        raise NotFoundError("文件不存在", code=ErrorCode.RESOURCE_NOT_FOUND)

    root = os.path.realpath(os.path.abspath(settings.LOCAL_STORAGE_PATH))
    abs_path = _resolve_abs_path(
        os.path.join(tenant_id, subdir, filename),
        tenant_root=os.path.join(root, tenant_id),
    )
    if not os.path.isfile(abs_path):
        raise NotFoundError("文件不存在", code=ErrorCode.RESOURCE_NOT_FOUND)

    # Q-S（2026-09-22 裁定）：卷宗下载按**案件归属**授权，与重解析同一口径。
    #
    # 此前只校验租户 ⇒ 同租户客户甲可下载客户乙的卷宗原文。而重解析
    # （`evidence.py::_evidence_or_404`）早已按归属卡住 ⇒ **修了一半**：
    # 「看不了解析结果，却能下原文」，等于没修。
    #
    # 反查链路：`save_upload` 返回的 `rel_path` 就是 `{tenant_id}/{subdir}/{filename}`，
    # 与下载 URL 同形 ⇒ 可用 `Evidence.file_path` 精确反查归属案件。
    # （此前确实**没有**这条反查链路，`_evidence_or_404` 只按 `evidence_id` 取数。）
    #
    # ⚠️ 对客户**失败即拒**（fail-closed）：查不到 Evidence 记录的无主文件也一律 404。
    # 当前 `save_upload` 只有证据上传一处调用，故「无主」只可能是孤儿文件；
    # 宁可误拒，不可越权。
    if ctx.role == Role.CLIENT:
        rel = f"{tenant_id}/{subdir}/{filename}"
        ev = (
            await db.execute(
                select(Evidence).where(
                    Evidence.tenant_id == tenant_id,
                    Evidence.file_path == rel,
                )
            )
        ).scalars().first()
        if ev is None:
            raise NotFoundError("文件不存在", code=ErrorCode.RESOURCE_NOT_FOUND)
        if ev.case_id is not None:
            case = await db.get(Case, ev.case_id)
            if case is None or case.client_user_id != ctx.user_id:
                raise NotFoundError("文件不存在", code=ErrorCode.RESOURCE_NOT_FOUND)
        elif ev.uploaded_by != ctx.user_id:
            raise NotFoundError("文件不存在", code=ErrorCode.RESOURCE_NOT_FOUND)

    # 留痕失败不阻断下载（合规要留痕，但不能因审计存储故障导致业务不可用）。
    # 用 log_detached_ctx 而非 log_detached：会自动带上来源 IP / UA，
    # 否则只能知道「谁下载了卷宗」，不知道「从哪下载的」。
    # ⚠️ `log_detached` 内部只吞「写库失败」；而 `log_detached_ctx` 还会在
    # 取审计上下文（IP/UA/request_id）时抛出。这里必须自己兜住，否则
    # 「审计不可用」会直接把下载打成 500 —— 与本函数承诺的
    # 「不能因审计存储故障导致业务不可用」不符（D7b 钉住）。
    try:
        await log_detached_ctx(
            AuditAction.FILE_DOWNLOAD,
            "file",
            actor_id=getattr(user, "id", None),
            actor_role=getattr(getattr(user, "role", None), "value", None),
            tenant_id=tenant_id,
            detail={
                "subdir": subdir,
                "filename": filename,
                "size": os.path.getsize(abs_path),
            },
        )
    except Exception as exc:  # noqa: BLE001 - 留痕失败绝不阻断下载
        logger.warning(f"文件下载审计留痕失败（不阻断下载）：{exc}")

    return FileResponse(abs_path, filename=filename)
