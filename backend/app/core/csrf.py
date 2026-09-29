"""CSRF 防护：签名式双提交令牌（signed double-submit cookie）。

**为什么需要它？**
refresh 令牌一旦改存 HttpOnly Cookie，浏览器会**自动携带**该 Cookie——
这正是 XSS 窃取不到令牌的原因，但也带来了 CSRF：攻击者诱导用户访问恶意页面，
浏览器会自动带上 Cookie 完成 refresh。

**方案：双提交 + 签名绑定**
1. 服务端下发 `csrf_token`（**非 HttpOnly** Cookie，前端可读）。
2. 前端在写请求里把它放进 `X-CSRF-Token` 头。
3. 服务端校验「头部值 == Cookie 值」**且签名有效**。

**为什么要签名，而不是纯随机值？**
纯双提交在「攻击者能写 Cookie」的场景（子域被控、中间人）会失效——攻击者
自己设一个 Cookie 再回填同值头即可绕过。签名把令牌与**会话标识**绑定，
攻击者伪造不出有效签名（没有 SECRET_KEY），也无法跨会话复用。

**TTL 豁免（P2-7）**
令牌 TTL（1 小时）远短于 refresh Cookie（7 天）：用户空闲超过 1 小时后，
令牌过期但 Cookie 还在，refresh 必 403——一次正常空闲变成误判掉线。
`validate_csrf_token_allow_expired` 供 refresh / logout 豁免路径使用：
签名 + 会话绑定仍强制校验（防伪造性质完整保留），仅忽略时效，
成功后随响应重发新令牌。
"""
from __future__ import annotations

import hmac
import os
import time
from hashlib import sha256
from typing import Optional, Tuple

from app.config import settings

#: 令牌有效期（秒）。CSRF 令牌是短时凭据，过期即重新下发。
CSRF_TOKEN_TTL_SECONDS = 3600


def _sign(payload: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), sha256
    ).hexdigest()


def generate_csrf_token(session_id: str = "") -> str:
    """生成签名 CSRF 令牌：`<nonce>.<ts>.<sig>`。

    `session_id` 参与签名 -> 令牌与当前会话绑定，不能跨会话复用。
    这里用 refresh 令牌的哈希前 16 位作为会话标识（不存原值，避免泄露）。
    """
    nonce = os.urandom(16).hex()
    ts = str(int(time.time()))
    payload = f"{nonce}.{ts}.{session_id}"
    return f"{payload}.{_sign(payload)}"


def _verify(token: Optional[str], session_id: str) -> Optional[Tuple[int]]:
    """签名 + 会话绑定校验（共用核）。返回签发时间戳；无效返回 None。"""
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 4:
        return None
    nonce, ts, sid, sig = parts
    payload = f"{nonce}.{ts}.{sid}"

    # 用 compare_digest 防时序侧信道
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    # 会话绑定：令牌里的 session_id 必须与当前会话一致
    if sid != session_id:
        return None
    try:
        issued = int(ts)
    except ValueError:
        return None
    return (issued,)


def validate_csrf_token(token: Optional[str], session_id: str = "") -> bool:
    """校验令牌签名与时效。返回 False 表示应拒绝请求。"""
    verified = _verify(token, session_id)
    if verified is None:
        return False
    return abs(int(time.time()) - verified[0]) <= CSRF_TOKEN_TTL_SECONDS


def validate_csrf_token_allow_expired(token: Optional[str], session_id: str = "") -> bool:
    """仅校验签名与会话绑定，**忽略 TTL**（P2-7，refresh / logout 豁免专用）。"""
    return _verify(token, session_id) is not None


def session_id_from_refresh(refresh_token: str) -> str:
    """由 refresh 令牌派生会话标识（只取哈希前缀，不落原值）。"""
    return sha256(refresh_token.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "CSRF_TOKEN_TTL_SECONDS",
    "generate_csrf_token",
    "validate_csrf_token",
    "validate_csrf_token_allow_expired",
    "session_id_from_refresh",
]
