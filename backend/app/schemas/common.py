"""通用 Schema：统一响应体与分页。"""
from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class ORMModel(BaseModel):
    """允许从 ORM 对象直接构造（from_attributes）。"""

    model_config = ConfigDict(from_attributes=True)


class ApiResponse(BaseModel, Generic[T]):
    """统一响应体：{success, data, error}。"""

    success: bool = True
    data: Optional[T] = None
    error: Optional[dict] = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Optional[Any] = None


class PageMeta(BaseModel):
    total: int
    page: int
    page_size: int
    pages: int


class Paged(BaseModel, Generic[T]):
    items: list[T]
    meta: PageMeta


class JobRef(BaseModel):
    """异步任务引用：接口立即返回，前端凭 job_id 轮询进度。"""

    job_id: int
    status: str
    progress: int = 0
    message: Optional[str] = None
