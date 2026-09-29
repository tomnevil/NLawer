"""结构化日志：请求 ID 透传 + 可选 JSON 输出。

**为什么自建而不加 `structlog` / `python-json-logger`？**
与 `core/metrics.py` 同一原则：零新增运行期依赖。本项目日志由 loguru 统一
收口，只需给 loguru 挂一个「注入上下文 + 按需 JSON 序列化」的 sink，即可
满足可观测性要求，无需再引入一套日志框架（同时避免两套日志栈并存）。

**三个必须解决的问题**
1. **request_id 穿透**：service 层深处发生异常时，日志里必须能关联到具体
   请求。此前的实现只在中间件里把 request_id 写进响应头，进程内其它日志
   拿不到。这里用 `contextvars`（异步安全的 thread-local 替代）承载。
2. **JSON 输出的正确转义**：直接用 f-string 拼 JSON 是本项目历史上真实
   踩过的坑——消息里一个双引号就能产出无法解析的日志行，而日志解析失败
   往往在事故复盘时才发现（最需要它的时候失效）。因此这里统一走
   `json.dumps(ensure_ascii=False, default=str)`，非可序列化对象降级为
   `str()` 而不是抛异常。
3. **JSON 下必须重写异常栈**：loguru 的 `{exception}` 是带 ANSI 颜色与
   多行格式的文本，塞进 JSON 会产生非法行（换行未转义）。因此结构化模式
   下显式提取 `record["exception"]` 并用 `traceback` 重建纯文本栈。

**输出格式选择**：`LOG_FORMAT=json` 用于生产（采集器直读）；默认 `text`
保留人眼可读的 loguru 默认格式，本地开发不受影响。
"""
from __future__ import annotations

import json
import sys
import traceback
from contextvars import ContextVar
from typing import Any, Dict, Optional

from loguru import logger

#: 当前请求上下文（异步安全）。默认 None 便于非请求场景（脚本 / 定时任务）复用。
request_id_ctx: ContextVar[Optional[str]] = ContextVar("nlaw_request_id", default=None)
#: 当前租户（便于多租户问题定位；同样异步安全）
tenant_id_ctx: ContextVar[Optional[str]] = ContextVar("nlaw_tenant_id", default=None)

#: 需要从日志中脱敏的字段名（宁可少记，也不要把凭据写进日志）
_REDACT_KEYS = frozenset(
    {
        "password",
        "new_password",
        "old_password",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "secret",
        "api_key",
        "csrf_token",
    }
)

_configured = False


def set_request_id(value: Optional[str]) -> None:
    request_id_ctx.set(value)


def get_request_id() -> Optional[str]:
    return request_id_ctx.get()


def set_tenant_id(value: Optional[str]) -> None:
    tenant_id_ctx.set(value)


def _redact(payload: Dict[str, Any]) -> Dict[str, Any]:
    """按字段名脱敏。仅做浅层 + 一层嵌套，避免递归开销影响热路径。"""
    out: Dict[str, Any] = {}
    for key, value in payload.items():
        if key.lower() in _REDACT_KEYS:
            out[key] = "***"
        elif isinstance(value, dict):
            out[key] = {
                k: ("***" if k.lower() in _REDACT_KEYS else v) for k, v in value.items()
            }
        else:
            out[key] = value
    return out


def _patch_record(record: Dict[str, Any]) -> None:
    """给每条日志注入 request_id / tenant_id（缺失时置空而非省略）。

    置空而非省略的理由：日志采集侧（ELK / Loki）用固定字段建索引，
    字段忽有忽无会导致查询语句必须写兼容分支。
    """
    extra = record["extra"]
    extra.setdefault("request_id", request_id_ctx.get() or "-")
    extra.setdefault("tenant_id", tenant_id_ctx.get() or "-")


def _json_sink(message: Any) -> None:
    """结构化 sink：每条日志一行合法 JSON。"""
    record = message.record
    payload: Dict[str, Any] = {
        "ts": record["time"].isoformat(),
        "level": record["level"].name,
        "logger": record["name"],
        "module": record["module"],
        "function": record["function"],
        "line": record["line"],
        "message": record["message"],
        "request_id": record["extra"].get("request_id", "-"),
        "tenant_id": record["extra"].get("tenant_id", "-"),
    }
    # 业务侧通过 logger.bind(**kwargs) 附加上下文字段
    bound = {
        k: v
        for k, v in record["extra"].items()
        if k not in {"request_id", "tenant_id"}
    }
    if bound:
        payload["extra"] = _redact(bound)

    exc = record["exception"]
    if exc is not None:
        payload["exception"] = {
            "type": exc.type.__name__ if exc.type else "Exception",
            "value": str(exc.value) if exc.value else "",
            # 必须重建纯文本栈：loguru 的格式化栈含换行与 ANSI 颜色，
            # 直接写入会破坏「一行一条 JSON」的约定。
            "traceback": "".join(
                traceback.format_exception(exc.type, exc.value, exc.traceback)
            ).rstrip(),
        }

    try:
        line = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):  # pragma: no cover - 兜底，绝不因日志崩服务
        line = json.dumps(
            {"ts": payload["ts"], "level": "ERROR", "message": "日志序列化失败"},
            ensure_ascii=False,
        )
    sys.stderr.write(line + "\n")


def configure_logging(log_format: str = "text", level: str = "INFO") -> None:
    """按配置装配 loguru sink。幂等：重复调用只生效一次。"""
    global _configured
    if _configured:
        return
    _configured = True

    logger.remove()
    logger.configure(patcher=_patch_record)

    if log_format.lower() == "json":
        logger.add(_json_sink, level=level, backtrace=False, diagnose=False)
    else:
        logger.add(
            sys.stderr,
            level=level,
            backtrace=False,
            diagnose=False,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{extra[request_id]}</cyan> | "
                "<level>{message}</level>"
            ),
        )


def reset_logging_for_tests() -> None:
    """仅供测试：允许重新装配 sink。"""
    global _configured
    _configured = False
