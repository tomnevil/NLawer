"""证据材料清单种子（platform 租户共享，PRD 5.4 缺失提醒）。

priority：1=必备（第一轮追问） 2=重要（第二轮） 3=补充（第三轮）
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import EvidenceCategory
from app.models.evidence import EvidenceChecklist

CHECKLIST_SEED: list[dict] = [
    # ---------- 劳动争议 ----------
    {"dispute_type": "劳动争议", "item": "劳动合同或用工协议", "category": EvidenceCategory.CONTRACT, "priority": 1,
     "description": "确认劳动关系成立的基础材料"},
    {"dispute_type": "劳动争议", "item": "工资流水 / 银行转账记录", "category": EvidenceCategory.PAYMENT, "priority": 1,
     "description": "核算欠付工资与经济补偿基数"},
    {"dispute_type": "劳动争议", "item": "解除/辞退通知", "category": EvidenceCategory.OFFICIAL, "priority": 1,
     "description": "判断解除是否合法的关键"},
    {"dispute_type": "劳动争议", "item": "社保缴纳记录", "category": EvidenceCategory.OFFICIAL, "priority": 2,
     "description": "证明缴费情况与工作年限"},
    {"dispute_type": "劳动争议", "item": "工作沟通记录（微信/邮件）", "category": EvidenceCategory.COMMUNICATION, "priority": 2,
     "description": "佐证工作安排与解除原因"},
    {"dispute_type": "劳动争议", "item": "身份证复印件", "category": EvidenceCategory.IDENTITY, "priority": 2,
     "description": "确认当事人主体身份"},
    {"dispute_type": "劳动争议", "item": "考勤记录 / 加班记录", "category": EvidenceCategory.OTHER, "priority": 3,
     "description": "主张加班费时补充提供"},
    # ---------- 合同纠纷 ----------
    {"dispute_type": "合同纠纷", "item": "书面合同原件", "category": EvidenceCategory.CONTRACT, "priority": 1,
     "description": "确认约定内容与违约条款"},
    {"dispute_type": "合同纠纷", "item": "付款凭证 / 发票", "category": EvidenceCategory.PAYMENT, "priority": 1,
     "description": "证明已付款或应付款金额"},
    {"dispute_type": "合同纠纷", "item": "交付/验收单据", "category": EvidenceCategory.OTHER, "priority": 2,
     "description": "证明履约情况"},
    {"dispute_type": "合同纠纷", "item": "催款函及送达记录", "category": EvidenceCategory.COMMUNICATION, "priority": 2,
     "description": "证明催告事实与主张权利"},
    {"dispute_type": "合同纠纷", "item": "双方主体资格证明", "category": EvidenceCategory.IDENTITY, "priority": 2,
     "description": "营业执照或身份证"},
    {"dispute_type": "合同纠纷", "item": "往来沟通记录", "category": EvidenceCategory.COMMUNICATION, "priority": 3,
     "description": "佐证磋商与变更约定"},
    # ---------- 婚姻家庭 ----------
    {"dispute_type": "婚姻家庭", "item": "结婚证 / 身份关系证明", "category": EvidenceCategory.IDENTITY, "priority": 1,
     "description": "确认婚姻关系"},
    {"dispute_type": "婚姻家庭", "item": "财产凭证（房产/车辆/存款）", "category": EvidenceCategory.PAYMENT, "priority": 1,
     "description": "界定夫妻共同财产范围"},
    {"dispute_type": "婚姻家庭", "item": "子女出生证明", "category": EvidenceCategory.IDENTITY, "priority": 2,
     "description": "涉及抚养权时必备"},
    {"dispute_type": "婚姻家庭", "item": "沟通与分居记录", "category": EvidenceCategory.COMMUNICATION, "priority": 3,
     "description": "证明感情破裂情形"},
    # ---------- 交通事故 ----------
    {"dispute_type": "交通事故", "item": "交通事故责任认定书", "category": EvidenceCategory.OFFICIAL, "priority": 1,
     "description": "划分责任的核心公文"},
    {"dispute_type": "交通事故", "item": "医疗费票据", "category": EvidenceCategory.PAYMENT, "priority": 1,
     "description": "核算医疗费损失"},
    {"dispute_type": "交通事故", "item": "伤残鉴定意见", "category": EvidenceCategory.OFFICIAL, "priority": 2,
     "description": "主张伤残赔偿金依据"},
    {"dispute_type": "交通事故", "item": "维修费发票 / 定损单", "category": EvidenceCategory.PAYMENT, "priority": 2,
     "description": "主张财产损失"},
    {"dispute_type": "交通事故", "item": "误工证明与收入流水", "category": EvidenceCategory.PAYMENT, "priority": 3,
     "description": "主张误工费"},
]


async def seed_checklists(session: AsyncSession) -> int:
    """幂等灌入材料清单模板，返回新增条数。"""
    count = 0
    for item in CHECKLIST_SEED:
        existing = (
            await session.execute(
                select(EvidenceChecklist).where(
                    EvidenceChecklist.dispute_type == item["dispute_type"],
                    EvidenceChecklist.item == item["item"],
                )
            )
        ).scalars().first()
        if existing:
            continue
        session.add(EvidenceChecklist(tenant_id="platform", **item))
        count += 1
    return count
