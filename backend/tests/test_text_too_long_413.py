"""`string_too_long` ⇒ **413**（而非默认 422），其余校验错误仍 422。

与 `tests/test_request_text_limits.py`（schema 层「真的会拒」）是同一防线的**两端**：
那一条证明源码里真的有 `max_length` 且模型会拒；这一条证明**超长时返回的是
413 + 转上传指引**，而不是 422、更不是静默截断 —— 即 `app/main.py` 的
`_too_long_to_413` 处理器真的接管了 `RequestValidationError`。

取值依据：`deliverables/product-strategy/roadmap-update-pending-rulings-2026-09-22.md` §4.1
（裁定表要求「超限 413 + 提示转上传，禁静默截断」）。
"""
from __future__ import annotations

import json

from fastapi.exceptions import RequestValidationError
from starlette.requests import Request

from app.main import _too_long_to_413


def _body(resp):
    return json.loads(resp.body)


def _req() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/qa",
            "headers": [],
            "query_string": b"",
        }
    )


def _too_long(field: str = "question", max_len: int = 4000, actual: int = 5000):
    return RequestValidationError(
        [
            {
                "type": "string_too_long",
                "loc": ("body", field),
                "ctx": {"max_length": max_len, "actual_length": actual},
                "msg": "String should have at most %d characters" % max_len,
                "input": "x" * actual,
            }
        ]
    )


async def test_string_too_long_returns_413_with_guidance():
    resp = await _too_long_to_413(_req(), _too_long())
    assert resp.status_code == 413
    err = _body(resp)["error"]
    assert err["code"] == "CONTENT_TOO_LARGE"
    assert err["max_length"] == 4000
    assert err["field"] == "question"
    assert err["guidance"] == "upload_or_async"
    # 必须明说「转上传」且「不截断」—— 这是裁定表的硬要求
    assert "上传" in err["message"] and "截断" in err["message"]


async def test_other_validation_error_still_422():
    """缺必填字段**仍走 422**，不可被误升 413 —— 否则会丢「缺字段」这种更该红的信号。"""
    exc = RequestValidationError(
        [
            {
                "type": "missing",
                "loc": ("body", "question"),
                "msg": "Field required",
                "input": None,
            }
        ]
    )
    resp = await _too_long_to_413(_req(), exc)
    assert resp.status_code == 422
    assert "detail" in _body(resp)


async def test_mixed_error_prefers_413_when_too_long_present():
    """同一请求里既缺字段又超长时，以 413 优先（超长是最该提示「转上传」的那条）。"""
    exc = RequestValidationError(
        [
            {"type": "missing", "loc": ("body", "title"), "msg": "Field required", "input": None},
            {
                "type": "string_too_long",
                "loc": ("body", "question"),
                "ctx": {"max_length": 4000},
                "msg": "too long",
                "input": "x" * 9000,
            },
        ]
    )
    resp = await _too_long_to_413(_req(), exc)
    assert resp.status_code == 413


def test_handler_is_registered_on_app():
    """🚨 上面三条是对**函数本体**的单测；这条证明它真的挂到了 app 上。

    否则有人删掉 `create_app()` 里的 `app.add_exception_handler(RequestValidationError,
    _too_long_to_413)` 一行，函数还在、单测全绿，但线上**已经回退成 422** —— 又是「空转的臂」。
    """
    from app.main import create_app

    app = create_app()
    assert app.exception_handlers.get(RequestValidationError) is _too_long_to_413
