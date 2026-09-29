"""统一错误码与异常体系（/api/v1 标准错误响应）。

所有业务异常继承 AppError，由 ErrorHandlerMiddleware 转为标准
`{success: false, error: {code, message, details}}`，避免响应格式散落不一致。
"""
from typing import Any, Optional

from fastapi import HTTPException, status


class ErrorCode:
    """错误码字典（前端据此做国际化与分支处理）。"""

    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_STATE = "INVALID_STATE"

    # 认证 / 授权
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_INVALID_CREDENTIALS = "AUTH_INVALID_CREDENTIALS"
    AUTH_TOKEN_INVALID = "AUTH_TOKEN_INVALID"
    AUTH_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"
    AUTH_USER_INACTIVE = "AUTH_USER_INACTIVE"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    TENANT_DENIED = "TENANT_DENIED"

    # 资源 / 幂等 / 限流
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    CONFLICT = "CONFLICT"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"

    # 意图识别与接待
    INTENT_UNRECOGNIZED = "INTENT_UNRECOGNIZED"
    CONTEXT_REQUIRED = "CONTEXT_REQUIRED"
    TENANT_REQUIRED = "TENANT_REQUIRED"

    # 会话与消息
    CONVERSATION_NOT_FOUND = "CONVERSATION_NOT_FOUND"
    CONVERSATION_CLOSED = "CONVERSATION_CLOSED"
    MESSAGE_SEND_FAILED = "MESSAGE_SEND_FAILED"

    # 案件与派单
    CASE_NOT_FOUND = "CASE_NOT_FOUND"
    CASE_INVALID_STATE = "CASE_INVALID_STATE"
    CASE_ALREADY_ASSIGNED = "CASE_ALREADY_ASSIGNED"
    DISPATCH_NOT_FOUND = "DISPATCH_NOT_FOUND"
    DISPATCH_NO_CANDIDATE = "DISPATCH_NO_CANDIDATE"
    DISPATCH_RULE_CONFLICT = "DISPATCH_RULE_CONFLICT"
    GRAB_NOT_ALLOWED = "GRAB_NOT_ALLOWED"

    # AI 办案
    ANALYSIS_NOT_FOUND = "ANALYSIS_NOT_FOUND"
    ANALYSIS_GENERATION_FAILED = "ANALYSIS_GENERATION_FAILED"

    # 证据
    EVIDENCE_NOT_FOUND = "EVIDENCE_NOT_FOUND"
    EVIDENCE_PARSE_FAILED = "EVIDENCE_PARSE_FAILED"
    EVIDENCE_FILE_TOO_LARGE = "EVIDENCE_FILE_TOO_LARGE"
    EVIDENCE_TYPE_NOT_ALLOWED = "EVIDENCE_TYPE_NOT_ALLOWED"

    # 引用溯源（PRD：引用缺失视为生成失败）
    CITATION_MISSING = "CITATION_MISSING"
    CITATION_NOT_FOUND = "CITATION_NOT_FOUND"

    # 复核工作流
    REVIEW_NOT_FOUND = "REVIEW_NOT_FOUND"
    REVIEW_TRANSITION_DENIED = "REVIEW_TRANSITION_DENIED"
    REVIEW_LEVEL_INSUFFICIENT = "REVIEW_LEVEL_INSUFFICIENT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REVIEW_ALREADY_DECIDED = "REVIEW_ALREADY_DECIDED"
    REVIEW_INVALID_PARAM = "REVIEW_INVALID_PARAM"

    # 归档
    ARCHIVE_NOT_CONFIRMED = "ARCHIVE_NOT_CONFIRMED"
    ARCHIVE_NOT_FOUND = "ARCHIVE_NOT_FOUND"
    HEARING_PACK_EXPORT_FAILED = "HEARING_PACK_EXPORT_FAILED"

    # 文书与合同
    DOCUMENT_NOT_FOUND = "DOCUMENT_NOT_FOUND"
    TEMPLATE_NOT_FOUND = "TEMPLATE_NOT_FOUND"
    DOCUMENT_VARIABLES_INCOMPLETE = "DOCUMENT_VARIABLES_INCOMPLETE"
    CONTRACT_REVIEW_FAILED = "CONTRACT_REVIEW_FAILED"
    # 2026-09-23：异步审查完成后按 id 取回结果时用。
    # 刻意**不用** `REVIEW_NOT_FOUND`——那是律师复核（`reviews.py`）的，
    # 混用会让前端无法区分「审查不存在」与「复核不存在」。
    CONTRACT_REVIEW_NOT_FOUND = "CONTRACT_REVIEW_NOT_FOUND"

    # 合规扫描
    SCAN_NOT_FOUND = "SCAN_NOT_FOUND"
    SCAN_FAILED = "SCAN_FAILED"

    # 知识库与计费
    KNOWLEDGE_NOT_FOUND = "KNOWLEDGE_NOT_FOUND"
    KNOWLEDGE_TENANT_DENIED = "KNOWLEDGE_TENANT_DENIED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    WORK_ORDER_NOT_FOUND = "WORK_ORDER_NOT_FOUND"

    # 异步任务
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    JOB_NOT_RETRYABLE = "JOB_NOT_RETRYABLE"
    JOB_MAX_RETRIES_EXCEEDED = "JOB_MAX_RETRIES_EXCEEDED"

    # 系统配置（生产环境关键依赖缺失，必须显式失败而非静默降级）
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"

    # 通知（P0-15 读路径）
    NOTIFICATION_NOT_FOUND = "NOTIFICATION_NOT_FOUND"

    # 内容安全（《生成式人工智能服务管理暂行办法》第十四条）
    CONTENT_BLOCKED = "CONTENT_BLOCKED"
    CONTENT_UNDER_REVIEW = "CONTENT_UNDER_REVIEW"
    MODERATION_UNAVAILABLE = "MODERATION_UNAVAILABLE"


class AppError(Exception):
    """业务异常基类。"""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = ErrorCode.VALIDATION_ERROR

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        status_code: Optional[int] = None,
        details: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details

    def to_dict(self) -> dict:
        return {
            "success": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            },
        }


class BadRequestError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = ErrorCode.VALIDATION_ERROR


class UnauthorizedError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = ErrorCode.AUTH_REQUIRED


class InvalidCredentialsError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = ErrorCode.AUTH_INVALID_CREDENTIALS


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = ErrorCode.PERMISSION_DENIED


class TenantDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = ErrorCode.TENANT_DENIED


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = ErrorCode.RESOURCE_NOT_FOUND


class UnprocessableEntityError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = ErrorCode.REVIEW_INVALID_PARAM


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = ErrorCode.CONFLICT


class InvalidStateError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = ErrorCode.INVALID_STATE


class RateLimitError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = ErrorCode.RATE_LIMIT_EXCEEDED


class ConfigurationError(AppError):
    """系统配置错误：生产环境关键依赖（模型凭证等）缺失。

    这类错误属于**服务端自身问题**，不应向终端用户暴露内部配置细节，
    统一以 500 返回；由网关/监控按 code=CONFIGURATION_ERROR 告警。
    """

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = ErrorCode.CONFIGURATION_ERROR


def http_exception_to_app_error(exc: HTTPException) -> AppError:
    """归一化 FastAPI 内置 HTTPException，保证错误响应结构一致。"""
    detail = exc.detail
    message = detail if isinstance(detail, str) else str(detail)
    mapping = {
        400: ErrorCode.VALIDATION_ERROR,
        401: ErrorCode.AUTH_REQUIRED,
        403: ErrorCode.PERMISSION_DENIED,
        404: ErrorCode.RESOURCE_NOT_FOUND,
        409: ErrorCode.CONFLICT,
        429: ErrorCode.RATE_LIMIT_EXCEEDED,
    }
    return AppError(
        message=message,
        code=mapping.get(exc.status_code, ErrorCode.VALIDATION_ERROR),
        status_code=exc.status_code,
    )
