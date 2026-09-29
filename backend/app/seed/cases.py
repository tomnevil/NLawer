"""指导案例与类案种子（脱敏，platform 租户共享）。"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.citation import CasePrecedent

CASE_SEED: list[dict] = [
    {
        "case_no": "指导案例18号",
        "title": "用人单位未签书面劳动合同二倍工资争议",
        "court": "最高人民法院",
        "holding": "用人单位自用工之日起超过一个月未与劳动者订立书面劳动合同，应依法支付二倍工资；起算点为用工满一个月的次日。",
        "facts": "劳动者入职后单位长期未签合同，离职后主张二倍工资差额，仲裁与一审支持，单位上诉被驳回。",
        "dispute_type": "劳动争议",
        "related_laws": "劳动合同法第82条,劳动合同法第10条",
        "judgment_date": "2013-11-08",
        "region": "全国",
    },
    {
        "case_no": "指导案例73号",
        "title": "工伤认定中「上下班途中」交通事故的认定",
        "court": "最高人民法院",
        "holding": "在合理时间、合理路线的上下班途中受到非本人主要责任的交通事故伤害，应认定为工伤。",
        "facts": "职工下班顺路买菜后回家途中遇交通事故，社保部门不予认定，经诉讼最终认定为工伤。",
        "dispute_type": "工伤",
        "related_laws": "工伤保险条例第14条,工伤保险条例第37条",
        "judgment_date": "2014-08-21",
        "region": "全国",
    },
    {
        "case_no": "(2022)桂01民终1234号",
        "title": "违法解除劳动合同赔偿金争议",
        "court": "南宁市中级人民法院",
        "holding": "单位无法证明解除符合法定情形，构成违法解除，应按经济补偿标准二倍支付赔偿金。",
        "facts": "公司以「业绩不达标」单方辞退，未能举证考核制度合法性，判赔赔偿金。",
        "dispute_type": "劳动争议",
        "related_laws": "劳动合同法第87条,劳动合同法第47条",
        "judgment_date": "2022-06-15",
        "region": "广西",
    },
    {
        "case_no": "(2021)桂05民初567号",
        "title": "买卖合同逾期付款违约金纠纷",
        "court": "北海市中级人民法院",
        "holding": "买受人逾期付款构成违约，应承担继续履行及赔偿损失责任；约定违约金过高可依法酌减。",
        "facts": "卖方交付货物后买方长期拖欠货款，主张违约金，法院支持本金并酌减过高违约金。",
        "dispute_type": "合同纠纷",
        "related_laws": "民法典第577条,民法典第584条",
        "judgment_date": "2021-09-30",
        "region": "广西",
    },
    {
        "case_no": "(2020)桂02民终890号",
        "title": "房屋租赁合同提前解除补偿",
        "court": "柳州市中级人民法院",
        "holding": "承租人无正当理由提前退租构成违约，应赔偿出租人合理空置损失，但出租人负有减损义务。",
        "facts": "租期中承租人搬离，出租人未及时再出租，法院按合理空置期支持部分损失。",
        "dispute_type": "合同纠纷",
        "related_laws": "民法典第563条,民法典第584条",
        "judgment_date": "2020-12-11",
        "region": "广西",
    },
    {
        "case_no": "(2023)桂03民初234号",
        "title": "离婚财产分割与子女抚养",
        "court": "桂林市中级人民法院",
        "holding": "夫妻共同财产平均分割，抚养权以未成年子女利益最大化为原则综合判定。",
        "facts": "双方对房产与子女抚养争议，法院依出资与陪伴情况作出分割与抚养安排。",
        "dispute_type": "婚姻家庭",
        "related_laws": "民法典第1087条,民法典第1084条",
        "judgment_date": "2023-03-18",
        "region": "广西",
    },
]


async def seed_cases(session: AsyncSession) -> int:
    count = 0
    for item in CASE_SEED:
        existing = (
            await session.execute(
                select(CasePrecedent).where(CasePrecedent.case_no == item["case_no"])
            )
        ).scalars().first()
        if existing:
            continue
        session.add(CasePrecedent(tenant_id="platform", **item))
        count += 1
    return count
