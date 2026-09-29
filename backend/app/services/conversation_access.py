"""会话归属判据：**唯一来源**。

## 为什么必须收敛到一处

修复前，同一个问题在四个地方各写了一份，且**四份不一致**：

| 位置 | 判据 | 实际放行范围 |
|------|------|-------------|
| `api/v1/conversations.py` 列表 | `tenant_id` + （CLIENT 时）`client_user_id` | 客户端只看自己；律师看全租户 |
| `api/v1/conversations.py` 详情 | 仅 `tenant_id` | **同租户任何人** |
| `api/v1/conversations.py` 发消息 | 仅 `tenant_id` | **同租户任何人** |
| `api/v1/ws.py` 会话 WS | `tenant_id != ? AND client_user_id != ?` | **同租户任何人**（且逻辑运算符写错） |

三处比列表宽松，等于**列表把会话藏起来了，详情页和 WS 却直接放行**——
这是最没有意义的防御：知道 id 就能绕过列表直接读。判据分散必然漂移，
因此收敛为**一个纯函数**，WS 与 REST 三处共用。

## 实际风险有多大（叠加效应）

`POST /auth/register` 是**公开自助注册**，且把角色固定为 `Role.CLIENT`、
租户固定为**默认租户**（防自助提权，见 `schemas/auth.py:33-46`）。
也就是说：**所有自助注册的客户落在同一个租户里**。
在「仅按 tenant 放行」的判据下，**任意注册客户都能读取任意其他客户的
会话与消息**——这是真实的跨用户数据泄露，不是理论风险。

## 放行判据

| 身份 | 放行 | 理由 |
|------|------|------|
| 会话客户本人 | ✅ | 会话就是他的，唯一无争议 |
| 该会话绑定律师 | ✅ | 扫码/名片绑定即服务关系 |
| 关联案件的承办律师 | ✅ | 会话已升级为案件，承办律师必须能看到上下文 |
| 平台管理员 | ✅ | 平台运维与合规审计，跨租户是设计意图 |
| 律所管理员 / 律师 / 助理 | ✅（**限本租户**） | 律所协作模式：所内共享会话队列是产品既有设计（原列表接口注释明确写「律师 / 管理员看本租户全部」）。**⚠️ 该策略属开放问题，见下方「待产品拍板」** |
| 企业管理员 `ENTERPRISE_ADMIN` | ✅（**限本租户**） | 企业侧的**租户级管理员**（产品线 B，与 `FIRM_ADMIN` 对律所的关系一致）。收敛前它被 `tenant_id` 检查放行，收敛时若漏列会被**误伤成功能回归** |
| 端用户（客户 / 企业用户）跨用户 | ❌ | **本次修复的核心**：含客户咨询内容，受保密义务约束 |
| 跨租户任何用户 | ❌ | 租户隔离底线 |
| 未认证 | ❌ | 由调用方在更早阶段拒绝 |

## 待产品拍板（不阻塞本次修复）

「**同租户的其他律师/助理能否看到某会话**」是一个**产品策略问题**，不是工程缺陷：
律所内部共享会话队列是既有设计（律师要靠它抢单/接单），但《律师法》保密义务
与「客户间利益冲突」要求可能主张更严格的可见性。

**本次采取保守做法**：只收紧**无争议**的部分（端用户跨用户、跨租户），
**不改动律所内部可见性**——静默改变产品协作模式的风险远大于收益。
该问题已列入 PRD §8 的 Q4 与判据表第 6 行，**需产品 + 安全拍板**。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorCode, NotFoundError
from app.core.rbac import Role
from app.models.case import Case
from app.models.conversation import Conversation

#: **端用户角色**：非所内人员，只能访问自己的会话。
#: 用「角色语义」而非硬编码单个 CLIENT —— `ENTERPRISE_USER` 与企业客户
#: 面临完全相同的保密约束，遗漏它属实现疏漏（原列表接口只特判了 CLIENT）。
END_USER_ROLES: frozenset[Role] = frozenset({Role.CLIENT, Role.ENTERPRISE_USER})

#: **租户内服务/管理角色**：可在本租户内访问会话（见模块 docstring「待产品拍板」）。
#:
#: `ENTERPRISE_ADMIN` 必须在此集合内 —— 它是**企业侧的租户级管理员**
#: （产品线 B，与 `FIRM_ADMIN` 对律所的关系一致）。收敛判据前，三处端点都
#: 只查 `tenant_id`，因此企业管理员**原本是放行的**；若这里漏掉它，
#: 收敛动作会顺手把一个合法角色挡在门外 —— 那是**功能回归**，
#: 而不是「更安全的收紧」。此类「收敛时误伤」只有靠穷尽身份用例才能发现。
STAFF_ROLES: frozenset[Role] = frozenset(
    {Role.LAWYER, Role.ASSISTANT, Role.FIRM_ADMIN, Role.ENTERPRISE_ADMIN}
)


def can_access_conversation(
    conv: Conversation,
    *,
    user_id: int,
    tenant_id: str,
    role: Role,
    case_owner_id: Optional[int] = None,
) -> bool:
    """**纯函数**判据：给定会话与身份，是否放行。

    做成纯函数（`case_owner_id` 由调用方预先解析）是为了让判据表里
    **八类身份各一条用例**都能在无 DB 的情况下断言——判据本身是本模块
    唯一需要被穷尽验证的逻辑，不该被数据库夹具的复杂度掩盖。

    `case_owner_id` 为 `None` 表示「未解析 / 无关联案件」；调用方应先
    用 `resolve_case_owner_id` 解析（该方法在 `conv.case_id` 为空时短路，
    不做无谓查询）。
    """
    if user_id <= 0:
        return False

    # 平台管理员：跨租户是设计意图
    if role == Role.PLATFORM_ADMIN:
        return True

    # 租户隔离底线：先于一切身份判断（平台管理员已在上方放行）
    if conv.tenant_id != tenant_id:
        return False

    # ---- 以下均在「同租户」前提下 ----

    # 1. 客户本人：唯一无争议的放行，且应走零额外查询的快速路径
    if conv.client_user_id is not None and conv.client_user_id == user_id:
        return True

    # 2. 会话绑定律师
    if conv.bind_lawyer_id is not None and conv.bind_lawyer_id == user_id:
        return True

    # 3. 关联案件的承办律师
    if case_owner_id is not None and case_owner_id == user_id:
        return True

    # 4. 端用户到此为止：不得跨用户
    if role in END_USER_ROLES:
        return False

    # 5. 所内人员：本租户内放行（策略见模块 docstring）
    if role in STAFF_ROLES:
        return True

    # 未列举的角色（含未知值）一律拒绝：**默认拒绝**而非默认放行。
    # 新增角色时若忘记更新本函数，表现是「访问被拒」而非「越权放行」——
    # 失败方向必须是安全的那一侧。
    return False


async def resolve_case_owner_id(
    db: AsyncSession, conv: Conversation
) -> Optional[int]:
    """解析会话关联案件的承办律师 id；无关联案件时**短路返回 `None`**。

    短路是必要的：绝大多数会话尚未升级为案件（`case_id` 为空），
    若不做短路，每次访问会话都会多一次无谓的 `cases` 查询。
    """
    if not conv.case_id:
        return None
    case = await db.get(Case, conv.case_id)
    return case.lawyer_id if case is not None else None


async def load_accessible_conversation(
    db: AsyncSession,
    conversation_id: int,
    *,
    user_id: int,
    tenant_id: str,
    role: Role,
) -> Conversation:
    """取会话并校验归属；**无权或不存在都抛 `NotFoundError`**。

    刻意**不区分**「不存在」与「存在但无权」：全局自增 id 下，若两者
    返回不同结果，攻击者可用状态码枚举出「哪些会话 id 是真实存在的」，
    进而推断业务规模与他人的活跃度（OWASP IDOR）。对外一律表现为
    「会话不存在」，仅在服务端日志里区分两种情形。
    """
    conv = await db.get(Conversation, conversation_id)
    if conv is None:
        raise NotFoundError("会话不存在", code=ErrorCode.CONVERSATION_NOT_FOUND)

    # 端用户永远不需要案件归属（判据 1/2 即可覆盖其全部合法场景），
    # 故只在「所内人员且非本人/非绑定律师」时才做这一次额外查询。
    case_owner_id: Optional[int] = None
    needs_case = (
        conv.case_id is not None
        and conv.client_user_id != user_id
        and conv.bind_lawyer_id != user_id
        and role not in END_USER_ROLES
    )
    if needs_case:
        case_owner_id = await resolve_case_owner_id(db, conv)

    if not can_access_conversation(
        conv,
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        case_owner_id=case_owner_id,
    ):
        raise NotFoundError("会话不存在", code=ErrorCode.CONVERSATION_NOT_FOUND)
    return conv
