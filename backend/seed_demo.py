"""一键灌入演示种子数据。

用法：
    cd backend
    python seed_demo.py            # 幂等：已存在则跳过
    python seed_demo.py --reset    # 先清空业务表再灌入
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import select

from app.core.rbac import Role
from app.core.security import hash_password
from app.database import async_session_factory, init_db
from app.models import LawyerProfile, Tenant, User  # noqa: F401  确保元数据注册
from app.models.enums import UserStatus
from app.seed.business import seed_business
from app.seed.cases import seed_cases
from app.seed.checklists import seed_checklists
from app.seed.data import DEMO_USERS, LAWYER_PROFILES, TENANTS
from app.seed.laws import seed_laws
from app.seed.templates import seed_templates


async def seed_tenants(session) -> dict:
    """写入租户，返回 tenant_id -> Tenant 映射。"""
    result = {}
    for item in TENANTS:
        existing = (
            await session.execute(
                select(Tenant).where(Tenant.tenant_id == item["tenant_id"])
            )
        ).scalars().first()
        if existing:
            result[item["tenant_id"]] = existing
            continue
        tenant = Tenant(**item)
        session.add(tenant)
        await session.flush()
        result[item["tenant_id"]] = tenant
    return result


async def seed_users(session) -> dict:
    """写入演示账号与律师档案，返回 user_key -> User 映射。"""
    result = {}
    for item in DEMO_USERS:
        existing = (
            await session.execute(
                select(User).where(User.username == item["username"])
            )
        ).scalars().first()
        if existing:
            result[item["key"]] = existing
            continue

        user = User(
            username=item["username"],
            hashed_password=hash_password(item["password"]),
            full_name=item["full_name"],
            role=item["role"],
            tenant_id=item["tenant_id"],
            status=UserStatus.ACTIVE,
            is_active=True,
        )
        session.add(user)
        await session.flush()
        result[item["key"]] = user

        # 律师与律所管理员建立档案（派单匹配依据）
        if item["key"] in LAWYER_PROFILES:
            profile_data = LAWYER_PROFILES[item["key"]]
            session.add(
                LawyerProfile(
                    user_id=user.id,
                    tenant_id=user.tenant_id,
                    specialties=profile_data["specialties"],
                    practice_years=profile_data["practice_years"],
                    title=profile_data["title"],
                    available=profile_data["available"],
                    can_l3_review=profile_data["can_l3_review"],
                    active_case_count=0,
                )
            )
            await session.flush()
    return result


async def reset_business_tables(session) -> None:
    """清空业务表（顺序：先删依赖方）。"""
    from app.models import (  # noqa: F401
        AiDecision,
        AiRun,
        Archive,
        Case,
        CaseAnalysis,
        CaseEvent,
        ComplianceFinding,
        ComplianceScan,
        Conversation,
        Dispatch,
        Document,
        Evidence,
        HearingPack,
        Job,
        Message,
        Notification,
        Review,
        Subscription,
        UsageQuota,
        UsageRecord,
        WorkOrder,
    )

    for table in (
        CaseEvent,
        Review,
        CaseAnalysis,
        Evidence,
        Dispatch,
        Document,
        HearingPack,
        AiDecision,
        AiRun,
        Message,
        Archive,
        Case,
        Conversation,
        Job,
        Notification,
        UsageQuota,
        UsageRecord,
        Subscription,
        WorkOrder,
        ComplianceFinding,
        ComplianceScan,
        LawyerProfile,
        User,
        Tenant,
    ):
        await session.execute(table.__table__.delete())
    await session.commit()


def _print_accounts() -> None:
    print("\n演示账号（登录页可一键填充）：")
    print("-" * 68)
    role_names = {
        Role.PLATFORM_ADMIN: "平台管理员",
        Role.FIRM_ADMIN: "律所管理员",
        Role.LAWYER: "执业律师",
        Role.ASSISTANT: "律师助理",
        Role.CLIENT: "客户",
        Role.ENTERPRISE_ADMIN: "企业管理员",
        Role.ENTERPRISE_USER: "企业员工",
    }
    for item in DEMO_USERS:
        role_cn = role_names.get(item["role"], item["role"].value)
        print(
            f"  {item['username']:<14} {item['password']:<16} "
            f"{role_cn:<8} {item['tenant_id']}"
        )
    print("-" * 68)


async def main(reset: bool = False) -> None:
    # 先确保表存在（开发期等同 Alembic 建表）
    await init_db()
    async with async_session_factory() as session:
        try:
            if reset:
                print("正在清空业务表...")
                await reset_business_tables(session)
            await seed_tenants(session)
            await seed_users(session)
            laws = await seed_laws(session)
            cases = await seed_cases(session)
            templates = await seed_templates(session)
            checklists = await seed_checklists(session)
            business = await seed_business(session)
            await session.commit()
            print(
                f"种子数据写入完成：租户{len(TENANTS)}个、账号{len(DEMO_USERS)}个、"
                f"法规{laws}条、案例{cases}个、模板{templates}个、材料清单{checklists}条。"
            )
            if business:
                print(
                    f"业务演示数据：案件{business['cases']}个、派单{business['dispatches']}条、"
                    f"复核{business['reviews']}条、工单{business['work_orders']}个、"
                    f"合规扫描{business['scans']}个。"
                )
            _print_accounts()
        except Exception as exc:
            await session.rollback()
            print(f"种子数据写入失败：{exc}")
            raise


if __name__ == "__main__":
    # 生产门禁（P1-3）：种子数据含明文演示口令，落进生产库等同预置默认凭据。
    import os

    from app.config import settings

    if settings.ENVIRONMENT == "production" and os.environ.get("SEED_DEMO_ALLOW") != "1":
        raise SystemExit(
            "拒绝在生产环境灌入演示种子数据（含明文口令）。"
            "如确有需要，请显式设置环境变量 SEED_DEMO_ALLOW=1"
        )
    parser = argparse.ArgumentParser(description="灌入律小智演示种子数据")
    parser.add_argument("--reset", action="store_true", help="先清空业务表再灌入")
    args = parser.parse_args()
    asyncio.run(main(reset=args.reset))
