"""认证服务：注册、登录、令牌刷新（含 refresh 会话吊销与轮换，P1-1）。"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit import AuditAction, log_detached_ctx, record
from app.core.errors import (
    ConflictError,
    ErrorCode,
    InvalidCredentialsError,
    PermissionDeniedError,
)
from app.core.rbac import Role
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token_or_none,
    hash_password,
    verify_password,
)
from app.models.auth_session import RefreshSession
from app.models.enums import UserStatus
from app.models.identity import Tenant, User


def _now_db() -> datetime:
    """naive UTC：SQLite 的 DateTime 列无时区，比较双方必须同为 naive。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AuthService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def register(
        self,
        *,
        username: str,
        password: str,
        full_name: Optional[str] = None,
        role: Role = Role.CLIENT,
        tenant_id: Optional[str] = None,
    ) -> User:
        """创建用户。

        安全约束（纵深防御）：非特权角色不可经此方法创建。
        公开注册入口必须传 CLIENT；若将来需要创建律师/管理员账号，
        应新增带 require_permissions 的受控方法，而非放宽此处的校验。
        """
        if role != Role.CLIENT:
            raise PermissionDeniedError(
                "不允许通过注册接口创建特权账号",
                details={"requested_role": role.value},
            )

        existing = (
            await self.db.execute(select(User).where(User.username == username))
        ).scalars().first()
        if existing:
            raise ConflictError("用户名已存在", code=ErrorCode.CONFLICT)

        resolved_tenant = tenant_id or settings.DEFAULT_TENANT_ID
        tenant = (
            await self.db.execute(
                select(Tenant).where(Tenant.tenant_id == resolved_tenant)
            )
        ).scalars().first()
        if tenant is None:
            raise ConflictError(
                f"租户 {resolved_tenant} 不存在", code=ErrorCode.TENANT_REQUIRED
            )

        user = User(
            username=username,
            hashed_password=hash_password(password),
            full_name=full_name or username,
            role=role,
            tenant_id=resolved_tenant,
            status=UserStatus.ACTIVE,
            is_active=True,
        )
        self.db.add(user)
        await self.db.flush()

        # 用 record() 而非裸 write_audit()：前者会自动带上请求上下文
        # （IP / UA / request_id）。曾用 write_audit() 导致所有注册记录的
        # 来源 IP 为空——审计写了个「有内容但没有来源」的残条。
        await record(
            self.db,
            AuditAction.CREATE,
            "user",
            user.id,
            actor_id=user.id,
            actor_role=role.value,
            tenant_id=resolved_tenant,
            detail={"username": username, "role": role.value},
        )
        return user

    async def authenticate(self, username: str, password: str) -> User:
        user = (
            await self.db.execute(select(User).where(User.username == username))
        ).scalars().first()

        # 认证失败必须用独立会话留痕：本方法随后抛 401，请求事务会被回滚，
        # 用当前会话写的审计会一并丢失。而「从哪个 IP 反复试哪个账号」
        # 恰恰是撞库检测最需要的证据。
        if user is None or not verify_password(password, user.hashed_password):
            await log_detached_ctx(
                AuditAction.LOGIN_FAILED,
                "user",
                user.id if user is not None else None,
                actor_id=user.id if user is not None else None,
                actor_role=user.role.value if user is not None else None,
                tenant_id=user.tenant_id if user is not None else None,
                detail={"username": username, "reason": "BAD_CREDENTIALS"},
                success=False,
            )
            raise InvalidCredentialsError("用户名或密码错误")

        if not user.is_active or user.status != UserStatus.ACTIVE:
            await log_detached_ctx(
                AuditAction.LOGIN_FAILED,
                "user",
                user.id,
                actor_id=user.id,
                actor_role=user.role.value,
                tenant_id=user.tenant_id,
                detail={"username": username, "reason": "ACCOUNT_DISABLED"},
                success=False,
            )
            raise InvalidCredentialsError("账号已被停用")

        # 登录成功同样要带来源 IP —— 与 LOGIN_FAILED 保持同一份证据口径，
        # 否则无法回答「这次登录是从常用地还是异地发起的」。
        await record(
            self.db,
            AuditAction.LOGIN,
            "user",
            user.id,
            actor_id=user.id,
            actor_role=user.role.value,
            tenant_id=user.tenant_id,
        )

        # 顺带清理已过期的会话登记（防 refresh_sessions 无限增长；
        # 行数量级 = 活跃会话数，开销可忽略）。
        await self.db.execute(
            delete(RefreshSession).where(RefreshSession.expires_at < _now_db())
        )
        return user
    async def issue_session_tokens(
        self, user: User, jti: Optional[str] = None
    ) -> tuple[str, str, int]:
        """签发 access + refresh，并把 refresh 的 jti 登记进会话表（P1-1）。

        取代原 issue_tokens（静态、无登记）：无登记的 refresh 令牌无法
        通过 refresh 校验——服务端吊销必须以登记为前提。
        """
        payload = {
            "sub": str(user.id),
            "username": user.username,
            "role": user.role.value,
            "tenant_id": user.tenant_id,
        }
        jti = jti or uuid.uuid4().hex
        access = create_access_token(payload)
        refresh = create_refresh_token(payload, jti=jti)
        self.db.add(
            RefreshSession(
                jti=jti,
                user_id=user.id,
                expires_at=_now_db() + timedelta(days=settings.REFRESH_TOKEN_DAYS),
            )
        )
        await self.db.flush()
        return access, refresh, settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60

    async def rotate_refresh_session(self, token: str) -> tuple[User, str, str, int]:
        """校验 refresh 令牌（含服务端会话表）-> 轮换 -> 签发新令牌对。

        返回 (user, access, refresh, expires_in)。语义（P1-1）：
        - jti 不在表中（含部署前签发的旧令牌）-> 拒绝，要求重新登录
        - 命中已吊销的 jti 即重放：吊销该用户全部会话后拒绝
        - 正常路径：旧行标记 revoked + replaced_by，新 jti 登记入库
        """
        payload = decode_token_or_none(token)
        if payload is None or payload.get("type") != "refresh":
            raise InvalidCredentialsError("刷新令牌无效或已过期")
        jti = payload.get("jti")
        if not jti:
            raise InvalidCredentialsError("登录状态已失效，请重新登录")

        row = (
            await self.db.execute(
                select(RefreshSession).where(RefreshSession.jti == jti)
            )
        ).scalars().first()
        if row is None:
            raise InvalidCredentialsError("登录状态已失效，请重新登录")
        if row.revoked or (row.expires_at is not None and row.expires_at < _now_db()):
            await self._revoke_all_detached(row.user_id)
            raise InvalidCredentialsError("检测到令牌重放，所有会话已注销，请重新登录")

        subject = payload.get("sub")
        user = (
            await self.db.execute(select(User).where(User.id == int(subject)))
        ).scalars().first()
        if user is None or not user.is_active:
            raise InvalidCredentialsError("用户不存在或已停用")

        # 轮换：旧 jti 立即作废（随本请求事务提交），新 jti 登记入库。
        # 注意顺序：先给旧行写 replaced_by 再签发，保证轮换链可溯源。
        row.revoked = True
        row.replaced_by = uuid.uuid4().hex
        access, refresh, expires_in = await self.issue_session_tokens(
            user, jti=row.replaced_by
        )
        await self.db.flush()
        return user, access, refresh, expires_in

    async def revoke_refresh_session(self, token: str) -> None:
        """登出：按 jti 吊销会话（幂等；部署前的旧令牌无 jti，静默跳过）。"""
        payload = decode_token_or_none(token)
        jti = (payload or {}).get("jti")
        if not jti:
            return
        await self.db.execute(
            update(RefreshSession).where(RefreshSession.jti == jti).values(revoked=True)
        )

    async def _revoke_all_detached(self, user_id: int) -> None:
        """独立事务吊销该用户全部会话。

        重放检测路径最终以 401 收场——主请求事务会被回滚，吊销若写在
        主事务里就会一起蒸发（攻击者可无限重放）。必须独立提交，
        与 log_detached_ctx 处理 LOGIN_FAILED 审计是同一个思路。
        """
        from app.database import async_session_factory

        async with async_session_factory() as session:
            await session.execute(
                update(RefreshSession)
                .where(
                    RefreshSession.user_id == user_id,
                    RefreshSession.revoked.is_(False),
                )
                .values(revoked=True)
            )
            await session.commit()

    async def user_from_refresh_token(self, token: str) -> User:
        """由 refresh 令牌解析用户（不做会话表校验）。

        仅供 logout 审计等「令牌失效也应继续」的路径使用；
        refresh 端点必须走 rotate_refresh_session（含吊销与重放检测）。
        """
        payload = decode_token_or_none(token)
        if payload is None or payload.get("type") != "refresh":
            raise InvalidCredentialsError("刷新令牌无效或已过期")
        subject = payload.get("sub")
        user = (
            await self.db.execute(select(User).where(User.id == int(subject)))
        ).scalars().first()
        if user is None or not user.is_active:
            raise InvalidCredentialsError("用户不存在或已停用")
        return user
    async def revoke_refresh_session_detached(self, token: str) -> None:
        """独立事务按 jti 吊销（logout 专用，P1-1 修正版）。

        为什么必须 detached：logout 随后会用独立会话写审计（log_detached_ctx）。
        若吊销走主会话（get_db 收尾才 commit，期间持 SQLite 写锁），两个连接
        在单写者的 SQLite 上互卡——实测 5s busy 超时后 UPDATE 报
        database is locked（第一次 logout 500 的根因）。吊销与审计改为
        独立会话**串行**提交：先吊销提交释放锁，再写审计，互不持锁。
        """
        payload = decode_token_or_none(token)
        jti = (payload or {}).get("jti")
        if not jti:
            return
        from app.database import async_session_factory

        async with async_session_factory() as session:
            await session.execute(
                update(RefreshSession).where(RefreshSession.jti == jti).values(revoked=True)
            )
            await session.commit()
