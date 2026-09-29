"""演示种子数据：租户、律师档案、六类角色演示账号。

登录页内置一键填充（前端 `apps/*/app/login/page.tsx`），账号密码与此处保持一致。
"""
from app.core.rbac import Role
from app.models.enums import TenantType

#: 平台默认免责声明（PRD 10.2：每次回答开头必须显式提示）
DEFAULT_DISCLAIMER = "本内容由 AI 生成，仅供参考，不构成法律意见"

#: 租户定义
TENANTS = [
    {
        "tenant_id": "platform",
        "name": "律小智平台",
        "type": TenantType.PLATFORM,
        "display_name": "律小智平台",
        "seat_limit": 999,
        "bot_welcome": "您好，我是律小智 AI 助手。",
        "bot_disclaimer": DEFAULT_DISCLAIMER,
    },
    {
        "tenant_id": "firm_hlw",
        "name": "华律律师事务所",
        "type": TenantType.LAW_FIRM,
        "display_name": "华律律师事务所",
        "industry": "法律服务业",
        "region": "广西南宁",
        "seat_limit": 20,
        "bot_welcome": (
            "您好，我是华律律师事务所的 AI 助手小智，很高兴为您服务。"
            "我可以先为您做初步法律分析，必要时为您转接专业律师。"
        ),
        "bot_disclaimer": DEFAULT_DISCLAIMER,
    },
    {
        "tenant_id": "ent_acme",
        "name": "示例科技（南宁）有限公司",
        "type": TenantType.ENTERPRISE,
        "display_name": "示例科技",
        "industry": "电子商务",
        "region": "广西南宁",
        "seat_limit": 10,
        "bot_welcome": "您好，我是示例科技的专属法务助手。",
        "bot_disclaimer": DEFAULT_DISCLAIMER,
    },
]

#: 律师档案（按 user_key 关联），专业领域用于派单匹配
LAWYER_PROFILES = {
    "lawyer_wang": {
        "specialties": "劳动争议,劳动仲裁,劳动合同",
        "practice_years": 12,
        "title": "高级合伙人",
        "can_l3_review": True,
        "available": True,
    },
    "lawyer_li": {
        "specialties": "合同纠纷,买卖合同,租赁合同",
        "practice_years": 8,
        "title": "执业律师",
        "can_l3_review": False,
        "available": True,
    },
    "lawyer_zhao": {
        "specialties": "婚姻家庭,继承纠纷,离婚协议",
        "practice_years": 15,
        "title": "主任律师",
        "can_l3_review": True,
        "available": True,
    },
    "firm_admin": {
        "specialties": "公司合规,股权架构",
        "practice_years": 18,
        "title": "律所主任",
        "can_l3_review": True,
        "available": False,
    },
}

#: 演示账号（user_key -> 定义）
DEMO_USERS = [
    {
        "key": "admin",
        "username": "admin",
        "password": "Admin@12345",
        "full_name": "平台管理员",
        "role": Role.PLATFORM_ADMIN,
        "tenant_id": "platform",
    },
    {
        "key": "firm_admin",
        "username": "firm_admin",
        "password": "Firm@12345",
        "full_name": "陈主任",
        "role": Role.FIRM_ADMIN,
        "tenant_id": "firm_hlw",
    },
    {
        "key": "lawyer_wang",
        "username": "lawyer_wang",
        "password": "Lawyer@12345",
        "full_name": "王律师",
        "role": Role.LAWYER,
        "tenant_id": "firm_hlw",
    },
    {
        "key": "lawyer_li",
        "username": "lawyer_li",
        "password": "Lawyer@12345",
        "full_name": "李律师",
        "role": Role.LAWYER,
        "tenant_id": "firm_hlw",
    },
    {
        "key": "lawyer_zhao",
        "username": "lawyer_zhao",
        "password": "Lawyer@12345",
        "full_name": "赵律师",
        "role": Role.LAWYER,
        "tenant_id": "firm_hlw",
    },
    {
        "key": "assistant",
        "username": "assistant",
        "password": "Assistant@12345",
        "full_name": "周助理",
        "role": Role.ASSISTANT,
        "tenant_id": "firm_hlw",
    },
    {
        "key": "client",
        "username": "client",
        "password": "Client@12345",
        "full_name": "张先生",
        "role": Role.CLIENT,
        "tenant_id": "firm_hlw",
    },
    {
        "key": "ent_admin",
        "username": "ent_admin",
        "password": "Ent@12345",
        "full_name": "刘经理",
        "role": Role.ENTERPRISE_ADMIN,
        "tenant_id": "ent_acme",
    },
    {
        "key": "ent_user",
        "username": "ent_user",
        "password": "Ent@12345",
        "full_name": "孙专员",
        "role": Role.ENTERPRISE_USER,
        "tenant_id": "ent_acme",
    },
]
