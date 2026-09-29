"""安全与认证：密码哈希、JWT 签发校验、敏感字段信封加密。

移植自 AIECO `app/core/security.py`，保留 bcrypt + Fernet 组合
（AIECO 实际直接 import bcrypt，passlib 未使用，故不引入）。
"""
import base64
import hashlib
import uuid
import warnings
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, status
from fastapi.security import HTTPBearer
from jose import JWTError, jwt

from app.config import settings

security = HTTPBearer()


def _utcnow() -> datetime:
    """时区感知的当前 UTC 时间（替代已废弃的 datetime.utcnow）。"""
    return datetime.now(timezone.utc)


def hash_password(password: str) -> str:
    # bcrypt 仅处理前 72 字节
    password_bytes = password.encode("utf-8")[:72]
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    plain_bytes = plain_password.encode("utf-8")[:72]
    return bcrypt.checkpw(plain_bytes, hashed_password.encode("utf-8"))


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = _utcnow() + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "type": "access", "iat": _utcnow()})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(data: dict, jti: Optional[str] = None) -> str:
    """签发 refresh 令牌。

    `jti`（JWT ID）自 P1-1 起必带：服务端 RefreshSession 表按它登记/吊销/做
    重放检测。调用方应把同一个 jti 写入会话表（见 AuthService.issue_session_tokens）；
    不传则自动生成（此时令牌无法通过 refresh 校验——仅供测试/兼容路径）。
    """
    to_encode = data.copy()
    expire = _utcnow() + timedelta(days=settings.REFRESH_TOKEN_DAYS)
    to_encode.update(
        {"exp": expire, "type": "refresh", "iat": _utcnow(), "jti": jti or uuid.uuid4().hex}
    )
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的或已过期的令牌"
        )


def decode_token_or_none(token: str) -> Optional[dict]:
    """不抛异常的解码，供可选鉴权依赖使用。"""
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None


def _fernet() -> Fernet:
    """由字段加密主密钥派生 Fernet 密钥（P2-11）。

    独立的 DATA_ENCRYPTION_KEY 优先；未配置时回退 SECRET_KEY 派生（兼容
    存量），但那样 JWT 密钥的轮换/泄露会连带密文作废——生产环境回退路径
    会打警告，提示显式配置独立密钥。
    """
    master = settings.DATA_ENCRYPTION_KEY or settings.SECRET_KEY
    if not settings.DATA_ENCRYPTION_KEY and settings.ENVIRONMENT == "production":
        warnings.warn(
            "DATA_ENCRYPTION_KEY 未配置，字段加密回退为 SECRET_KEY 派生——"
            "JWT 密钥轮换将连带敏感字段密文作废。请显式设置独立密钥。",
            RuntimeWarning,
            stacklevel=2,
        )
    digest = hashlib.sha256(master.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_secret(encrypted: str) -> Optional[str]:
    """解密失败（主密钥变更等）返回 None，不应导致服务崩溃。"""
    try:
        return _fernet().decrypt(encrypted.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None
