"""CSRF 令牌与认证 Cookie 的单元测试（P0-7 / P0-11）。

守护三类性质：
1. **签名不可伪造** —— 没有 SECRET_KEY 就造不出有效 CSRF 令牌
2. **会话绑定** —— 令牌与 refresh 会话绑定，跨会话不可复用
3. **时效** —— 过期令牌必须被拒

以及一条**反向断言**：access 令牌有效期必须短时，
防止有人「为了减少掉线」把 30 分钟改回 24 小时（那会把 XSS 窗口重新放大）。
"""
from __future__ import annotations

import time

import pytest

from app.config import settings
from app.core.csrf import (
    CSRF_TOKEN_TTL_SECONDS,
    generate_csrf_token,
    session_id_from_refresh,
    validate_csrf_token,
)


def test_generated_token_validates():
    sid = session_id_from_refresh("refresh-abc")
    token = generate_csrf_token(session_id=sid)
    assert validate_csrf_token(token, session_id=sid)


def test_token_rejected_without_session_match():
    """跨会话复用必须失败：令牌签名里嵌了 session_id。"""
    token = generate_csrf_token(session_id=session_id_from_refresh("session-A"))
    assert not validate_csrf_token(token, session_id=session_id_from_refresh("session-B"))


def test_forged_signature_rejected():
    """伪造签名（无 SECRET_KEY）必须失败。"""
    assert not validate_csrf_token("nonce.1234567890.sid." + "f" * 64, session_id="sid")


def test_malformed_tokens_rejected():
    for bad in ("", "   ", "abc", "a.b.c", "a.b.c.d.e", None):
        assert not validate_csrf_token(bad), f"应拒绝: {bad!r}"


def test_expired_token_rejected(monkeypatch):
    """超过 TTL 的令牌必须失败（防长期持有）。"""
    sid = "testsid"
    # 构造一个「签发于很久以前」的令牌
    stale_ts = str(int(time.time()) - CSRF_TOKEN_TTL_SECONDS - 10)
    from app.core.csrf import _sign

    payload = f"nonce123.{stale_ts}.{sid}"
    stale = f"{payload}.{_sign(payload)}"
    assert not validate_csrf_token(stale, session_id=sid)


def test_fresh_token_within_ttl_accepted():
    sid = "testsid"
    from app.core.csrf import _sign

    ts = str(int(time.time()) - CSRF_TOKEN_TTL_SECONDS + 60)  # 仍在窗口内
    payload = f"nonce456.{ts}.{sid}"
    token = f"{payload}.{_sign(payload)}"
    assert validate_csrf_token(token, session_id=sid)


def test_tokens_are_unique():
    """每次生成必须不同（nonce 随机），否则存在重放空间。"""
    sid = "s"
    tokens = {generate_csrf_token(session_id=sid) for _ in range(20)}
    assert len(tokens) == 20


def test_session_id_is_hashed_prefix():
    """会话标识取哈希前缀：不得泄露 refresh 原文。"""
    raw = "super-secret-refresh-token-value"
    sid = session_id_from_refresh(raw)
    assert raw not in sid
    assert len(sid) == 16


def test_access_token_ttl_stays_short():
    """回归守卫：access 有效期必须 <= 60 分钟。

    历史值是 1440（24 小时）。把它改回去等于把「一次 XSS = 一整天完整会话」
    的风险重新引入。如确需延长，请先评估 XSS 缓解措施并更新本条断言。
    """
    assert settings.ACCESS_TOKEN_EXPIRE_MINUTES <= 60, (
        f"access 有效期 {settings.ACCESS_TOKEN_EXPIRE_MINUTES} 分钟过长（XSS 窗口）"
    )


@pytest.mark.parametrize(
    "samesite", ["lax", "strict", "none"]
)
def test_cookie_config_is_sane(samesite, monkeypatch):
    """SameSite 必须是合法值；且启用 Cookie 时不得关闭 HttpOnly 语义。"""
    from app.core import auth_cookies

    monkeypatch.setattr(settings, "AUTH_COOKIE_SAMESITE", samesite)
    kwargs = auth_cookies._base_kwargs()
    assert kwargs["httponly"] is True, "refresh Cookie 必须 HttpOnly"
    assert kwargs["samesite"] == samesite
