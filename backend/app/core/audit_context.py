"""审计请求上下文：把 IP / User-Agent / request_id 透传到任意调用深度。

问题：审计写入点遍布 service 层，但 `Request` 对象只在路由层可见。若为每个
service 方法都加 `request` 参数，会污染大量签名且容易漏传。

方案：用 `contextvars` 在中间件里写入、在任意深处读取。`contextvars` 是
**每请求隔离**的（不是全局单例），并发请求之间不会串数据，也天然兼容
asyncio 的协程切换。
"""
from contextvars import ContextVar
from typing import Optional, TypedDict


class AuditContext(TypedDict, total=False):
    ip_address: Optional[str]
    user_agent: Optional[str]
    request_id: Optional[str]


_ctx: ContextVar[AuditContext] = ContextVar("audit_context", default={})


def set_audit_context(
    *,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    request_id: Optional[str] = None,
) -> None:
    """在中间件中调用，建立当前请求的审计上下文。"""
    data: AuditContext = {}
    if ip_address:
        data["ip_address"] = ip_address[:64]
    if user_agent:
        # 与 AuditLog.user_agent 的 String(300) 对齐，避免写库截断报错
        data["user_agent"] = user_agent[:300]
    if request_id:
        data["request_id"] = request_id[:64]
    _ctx.set(data)


def get_audit_context() -> AuditContext:
    """读取当前请求的审计上下文；无上下文（如后台任务）时返回空 dict。"""
    return _ctx.get()


def clear_audit_context() -> None:
    _ctx.set({})


__all__ = ["AuditContext", "set_audit_context", "get_audit_context", "clear_audit_context"]
