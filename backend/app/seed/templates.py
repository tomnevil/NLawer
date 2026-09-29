"""文书模板种子（platform 租户共享，200+ 规模的子集，覆盖 5 类生命周期）。

is_high_risk 标记触发强制 L3 复核的文书（离婚协议 / 遗嘱 / 股权转让等）。
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import DocumentTemplate

TEMPLATE_SEED: list[dict] = [
    {"code": "labor_contract", "name": "劳动合同", "lifecycle": "劳动用工",
     "description": "确立劳动关系、明确双方权利义务的书面协议。", "is_high_risk": False,
     "body": "甲方（用人单位）：{{party_a}}\n乙方（劳动者）：{{party_b}}\n根据《中华人民共和国劳动合同法》，甲乙双方本着平等自愿原则，就劳动报酬、工作内容、合同期限等签订本合同。合同期限自 {{start_date}} 至 {{end_date}}，月薪 {{salary}} 元。",
     "variables": [{"key": "party_a", "label": "用人单位", "required": True, "type": "text"}, {"key": "party_b", "label": "劳动者", "required": True, "type": "text"}, {"key": "start_date", "label": "合同起始日", "required": True, "type": "date"}, {"key": "end_date", "label": "合同终止日", "required": True, "type": "date"}, {"key": "salary", "label": "月薪(元)", "required": True, "type": "number"}]},
    {"code": "confidentiality", "name": "保密协议", "lifecycle": "劳动用工",
     "description": "约定员工在职及离职后对公司商业秘密的保密义务。", "is_high_risk": False,
     "body": "甲方：{{party_a}}\n乙方：{{party_b}}\n乙方对在任职期间知悉的甲方商业秘密负有保密义务，保密期限自本协议签署之日起 {{years}} 年。违反保密义务应赔偿甲方因此遭受的损失。",
     "variables": [{"key": "party_a", "label": "公司", "required": True, "type": "text"}, {"key": "party_b", "label": "员工", "required": True, "type": "text"}, {"key": "years", "label": "保密年限", "required": True, "type": "number"}]},
    {"code": "noncompete", "name": "竞业限制协议", "lifecycle": "劳动用工",
     "description": "约定高管及核心人员离职后一定期限内的竞业限制与补偿。", "is_high_risk": False,
     "body": "甲方：{{party_a}}\n乙方：{{party_b}}\n乙方离职后 {{months}} 个月内不得到与甲方有竞争关系的单位任职，甲方按月支付经济补偿 {{compensation}} 元。",
     "variables": [{"key": "party_a", "label": "公司", "required": True, "type": "text"}, {"key": "party_b", "label": "员工", "required": True, "type": "text"}, {"key": "months", "label": "限制月数", "required": True, "type": "number"}, {"key": "compensation", "label": "月补偿(元)", "required": True, "type": "number"}]},
    {"code": "termination_notice", "name": "解除劳动合同通知书", "lifecycle": "劳动用工",
     "description": "用人单位依法单方解除劳动合同的书面通知文书。", "is_high_risk": False,
     "body": "致 {{party_b}}：\n因 {{reason}}，依据《劳动合同法》第{{article}}条，我司决定自 {{date}} 起解除与您的劳动合同，并将依法支付经济补偿/赔偿金。",
     "variables": [{"key": "party_b", "label": "员工", "required": True, "type": "text"}, {"key": "reason", "label": "解除理由", "required": True, "type": "text"}, {"key": "article", "label": "依据条款", "required": True, "type": "text"}, {"key": "date", "label": "解除日期", "required": True, "type": "date"}]},
    {"code": "sales_contract", "name": "买卖合同", "lifecycle": "合同交易",
     "description": "出卖人转移标的物所有权、买受人支付价款的协议。", "is_high_risk": False,
     "body": "甲方（出卖方）：{{party_a}}\n乙方（买受方）：{{party_b}}\n标的物：{{goods}}，数量 {{quantity}}，单价 {{price}} 元，交货时间 {{delivery}}。逾期付款按日万分之五支付违约金。",
     "variables": [{"key": "party_a", "label": "出卖方", "required": True, "type": "text"}, {"key": "party_b", "label": "买受方", "required": True, "type": "text"}, {"key": "goods", "label": "标的物", "required": True, "type": "text"}, {"key": "quantity", "label": "数量", "required": True, "type": "text"}, {"key": "price", "label": "单价(元)", "required": True, "type": "number"}, {"key": "delivery", "label": "交货时间", "required": True, "type": "date"}]},
    {"code": "lease_contract", "name": "房屋租赁合同", "lifecycle": "合同交易",
     "description": "出租人将房屋交付承租人使用、收益，承租人支付租金的协议。", "is_high_risk": False,
     "body": "出租方：{{party_a}}\n承租方：{{party_b}}\n房屋坐落：{{address}}，租期 {{term}} 个月，月租金 {{rent}} 元，押金 {{deposit}} 元。",
     "variables": [{"key": "party_a", "label": "出租方", "required": True, "type": "text"}, {"key": "party_b", "label": "承租方", "required": True, "type": "text"}, {"key": "address", "label": "房屋地址", "required": True, "type": "text"}, {"key": "term", "label": "租期(月)", "required": True, "type": "number"}, {"key": "rent", "label": "月租金(元)", "required": True, "type": "number"}, {"key": "deposit", "label": "押金(元)", "required": True, "type": "number"}]},
    {"code": "loan_note", "name": "借条", "lifecycle": "合同交易",
     "description": "借款人向出借人出具的有息/无息借款凭证。", "is_high_risk": False,
     "body": "今借到 {{lender}} 人民币 {{amount}} 元，于 {{due_date}} 前归还。借款用途：{{purpose}}。借款人：{{borrower}}。",
     "variables": [{"key": "lender", "label": "出借人", "required": True, "type": "text"}, {"key": "borrower", "label": "借款人", "required": True, "type": "text"}, {"key": "amount", "label": "金额(元)", "required": True, "type": "number"}, {"key": "due_date", "label": "还款日", "required": True, "type": "date"}, {"key": "purpose", "label": "用途", "required": True, "type": "text"}]},
    {"code": "entrust_contract", "name": "委托代理合同", "lifecycle": "合同交易",
     "description": "委托人与受托人约定由受托人处理委托事务的协议。", "is_high_risk": False,
     "body": "委托人：{{party_a}}\n受托人：{{party_b}}\n委托事项：{{matter}}，委托期限 {{period}}，代理费 {{fee}} 元。",
     "variables": [{"key": "party_a", "label": "委托人", "required": True, "type": "text"}, {"key": "party_b", "label": "受托人", "required": True, "type": "text"}, {"key": "matter", "label": "委托事项", "required": True, "type": "text"}, {"key": "period", "label": "期限", "required": True, "type": "text"}, {"key": "fee", "label": "代理费(元)", "required": True, "type": "number"}]},
    {"code": "service_contract", "name": "服务合同", "lifecycle": "合同交易",
     "description": "服务提供方按约定向接受方提供服务的协议。", "is_high_risk": False,
     "body": "甲方：{{party_a}}\n乙方：{{party_b}}\n服务内容：{{service}}，服务期限 {{period}}，服务费 {{fee}} 元，分期支付。",
     "variables": [{"key": "party_a", "label": "委托方", "required": True, "type": "text"}, {"key": "party_b", "label": "服务方", "required": True, "type": "text"}, {"key": "service", "label": "服务内容", "required": True, "type": "text"}, {"key": "period", "label": "期限", "required": True, "type": "text"}, {"key": "fee", "label": "服务费(元)", "required": True, "type": "number"}]},
    {"code": "cooperation_contract", "name": "合伙协议", "lifecycle": "合同交易",
     "description": "合伙人共同出资、共享利润、共担风险的协议。", "is_high_risk": False,
     "body": "合伙人：{{party_a}}、{{party_b}}\n出资比例：{{ratio}}，利润分配：{{profit}}，合伙事务由全体合伙人共同决定。",
     "variables": [{"key": "party_a", "label": "合伙人甲", "required": True, "type": "text"}, {"key": "party_b", "label": "合伙人乙", "required": True, "type": "text"}, {"key": "ratio", "label": "出资比例", "required": True, "type": "text"}, {"key": "profit", "label": "利润分配", "required": True, "type": "text"}]},
    {"code": "equity_transfer", "name": "股权转让协议", "lifecycle": "合同交易",
     "description": "股东将其所持公司股权转让给他方的协议（高风险，强制 L3 复核）。", "is_high_risk": True,
     "body": "转让方：{{party_a}}\n受让方：{{party_b}}\n转让标的：{{company}} {{percent}}% 股权，转让价款 {{price}} 元，于 {{date}} 前完成工商变更。",
     "variables": [{"key": "party_a", "label": "转让方", "required": True, "type": "text"}, {"key": "party_b", "label": "受让方", "required": True, "type": "text"}, {"key": "company", "label": "公司", "required": True, "type": "text"}, {"key": "percent", "label": "股权比例(%)", "required": True, "type": "number"}, {"key": "price", "label": "价款(元)", "required": True, "type": "number"}, {"key": "date", "label": "交割日", "required": True, "type": "date"}]},
    {"code": "premarital_property", "name": "婚前财产协议", "lifecycle": "婚姻家庭",
     "description": "男女双方对婚前及婚后财产归属作出约定的协议（高风险，强制 L3 复核）。", "is_high_risk": True,
     "body": "甲方：{{party_a}}\n乙方：{{party_b}}\n双方约定：婚前财产各自所有；婚后 {{property_plan}}。本协议自登记结婚之日起生效。",
     "variables": [{"key": "party_a", "label": "甲方", "required": True, "type": "text"}, {"key": "party_b", "label": "乙方", "required": True, "type": "text"}, {"key": "property_plan", "label": "婚后财产安排", "required": True, "type": "text"}]},
    {"code": "divorce_agreement", "name": "离婚协议书", "lifecycle": "婚姻家庭",
     "description": "协议离婚双方就子女抚养、财产分割、债务处理达成的协议（高风险，强制 L3 复核）。", "is_high_risk": True,
     "body": "男方：{{party_a}}\n女方：{{party_b}}\n一、自愿离婚；二、子女 {{child}} 由 {{custody}} 抚养，另一方月付抚养费 {{alimony}} 元；三、财产：{{asset_split}}。",
     "variables": [{"key": "party_a", "label": "男方", "required": True, "type": "text"}, {"key": "party_b", "label": "女方", "required": True, "type": "text"}, {"key": "child", "label": "子女", "required": True, "type": "text"}, {"key": "custody", "label": "抚养方", "required": True, "type": "text"}, {"key": "alimony", "label": "抚养费(元)", "required": True, "type": "number"}, {"key": "asset_split", "label": "财产分割", "required": True, "type": "text"}]},
    {"code": "will", "name": "遗嘱", "lifecycle": "婚姻家庭",
     "description": "自然人对身后财产处分作出安排的法律文书（高风险，强制 L3 复核）。", "is_high_risk": True,
     "body": "立遗嘱人：{{testator}}\n本人名下财产：{{estate}}，由 {{beneficiary}} 继承。本遗嘱系本人真实意思表示。",
     "variables": [{"key": "testator", "label": "立遗嘱人", "required": True, "type": "text"}, {"key": "estate", "label": "遗产范围", "required": True, "type": "text"}, {"key": "beneficiary", "label": "继承人", "required": True, "type": "text"}]},
    {"code": "gift_contract", "name": "赠与合同", "lifecycle": "婚姻家庭",
     "description": "赠与人将财产无偿给予受赠人的协议。", "is_high_risk": False,
     "body": "赠与人：{{party_a}}\n受赠人：{{party_b}}\n赠与人自愿将 {{property}} 无偿赠与受赠人，受赠人表示接受。",
     "variables": [{"key": "party_a", "label": "赠与人", "required": True, "type": "text"}, {"key": "party_b", "label": "受赠人", "required": True, "type": "text"}, {"key": "property", "label": "赠与财产", "required": True, "type": "text"}]},
    {"code": "articles_of_association", "name": "公司章程", "lifecycle": "公司治理",
     "description": "规定公司组织与行为基本准则的文件。", "is_high_risk": False,
     "body": "公司名称：{{company}}\n注册资本：{{capital}} 元，经营范围：{{scope}}。股东会为公司权力机构，董事会负责经营管理。",
     "variables": [{"key": "company", "label": "公司名称", "required": True, "type": "text"}, {"key": "capital", "label": "注册资本(元)", "required": True, "type": "number"}, {"key": "scope", "label": "经营范围", "required": True, "type": "text"}]},
    {"code": "board_resolution", "name": "股东会决议", "lifecycle": "公司治理",
     "description": "股东会就特定事项作出的决议文件。", "is_high_risk": False,
     "body": "会议时间：{{date}}\n议题：{{topic}}\n经表决，同意 {{agree}} 票，反对 {{against}} 票，决议通过。",
     "variables": [{"key": "date", "label": "会议时间", "required": True, "type": "date"}, {"key": "topic", "label": "议题", "required": True, "type": "text"}, {"key": "agree", "label": "同意票", "required": True, "type": "number"}, {"key": "against", "label": "反对票", "required": True, "type": "number"}]},
    {"code": "power_of_attorney", "name": "授权委托书", "lifecycle": "公司治理",
     "description": "委托人授权受托人代为处理特定事务的文书。", "is_high_risk": False,
     "body": "委托人：{{party_a}}\n受托人：{{party_b}}\n委托事项：{{matter}}，权限范围：{{scope}}，期限 {{period}}。",
     "variables": [{"key": "party_a", "label": "委托人", "required": True, "type": "text"}, {"key": "party_b", "label": "受托人", "required": True, "type": "text"}, {"key": "matter", "label": "事项", "required": True, "type": "text"}, {"key": "scope", "label": "权限", "required": True, "type": "text"}, {"key": "period", "label": "期限", "required": True, "type": "text"}]},
    {"code": "complaint", "name": "民事起诉状", "lifecycle": "诉讼文书",
     "description": "原告向人民法院提起民事诉讼的文书。", "is_high_risk": False,
     "body": "原告：{{party_a}}\n被告：{{party_b}}\n诉讼请求：{{claims}}\n事实与理由：{{facts}}\n此致 {{court}} 人民法院",
     "variables": [{"key": "party_a", "label": "原告", "required": True, "type": "text"}, {"key": "party_b", "label": "被告", "required": True, "type": "text"}, {"key": "claims", "label": "诉讼请求", "required": True, "type": "text"}, {"key": "facts", "label": "事实与理由", "required": True, "type": "text"}, {"key": "court", "label": "法院", "required": True, "type": "text"}]},
    {"code": "defense", "name": "民事答辩状", "lifecycle": "诉讼文书",
     "description": "被告针对原告诉讼请求进行答辩的文书。", "is_high_risk": False,
     "body": "答辩人：{{party_a}}\n被答辩人：{{party_b}}\n答辩意见：{{opinion}}\n事实与理由：{{facts}}\n此致 {{court}} 人民法院",
     "variables": [{"key": "party_a", "label": "答辩人", "required": True, "type": "text"}, {"key": "party_b", "label": "被答辩人", "required": True, "type": "text"}, {"key": "opinion", "label": "答辩意见", "required": True, "type": "text"}, {"key": "facts", "label": "事实与理由", "required": True, "type": "text"}, {"key": "court", "label": "法院", "required": True, "type": "text"}]},
    {"code": "arbitration_application", "name": "劳动仲裁申请书", "lifecycle": "诉讼文书",
     "description": "劳动者向劳动争议仲裁委员会申请仲裁的文书。", "is_high_risk": False,
     "body": "申请人：{{party_a}}\n被申请人：{{party_b}}\n仲裁请求：{{claims}}\n事实与理由：{{facts}}\n此致 {{committee}} 劳动争议仲裁委员会",
     "variables": [{"key": "party_a", "label": "申请人", "required": True, "type": "text"}, {"key": "party_b", "label": "被申请人", "required": True, "type": "text"}, {"key": "claims", "label": "仲裁请求", "required": True, "type": "text"}, {"key": "facts", "label": "事实与理由", "required": True, "type": "text"}, {"key": "committee", "label": "仲裁委", "required": True, "type": "text"}]},
    {"code": "settlement_agreement", "name": "调解协议", "lifecycle": "合同交易",
     "description": "争议双方在调解下达成的和解协议。", "is_high_risk": False,
     "body": "甲方：{{party_a}}\n乙方：{{party_b}}\n经调解，双方就 {{dispute}} 达成如下和解：{{terms}}。本协议签署后双方不得再主张。",
     "variables": [{"key": "party_a", "label": "甲方", "required": True, "type": "text"}, {"key": "party_b", "label": "乙方", "required": True, "type": "text"}, {"key": "dispute", "label": "争议事项", "required": True, "type": "text"}, {"key": "terms", "label": "和解条款", "required": True, "type": "text"}]},
    {"code": "reminder_letter", "name": "催款函", "lifecycle": "合同交易",
     "description": "债权人向债务人催收欠款的书面通知。", "is_high_risk": False,
     "body": "致 {{party_b}}：\n贵方尚欠 {{amount}} 元未付，请于 {{due_date}} 前付清，逾期将依法追究违约责任。\n{{party_a}}",
     "variables": [{"key": "party_a", "label": "债权人", "required": True, "type": "text"}, {"key": "party_b", "label": "债务人", "required": True, "type": "text"}, {"key": "amount", "label": "欠款(元)", "required": True, "type": "number"}, {"key": "due_date", "label": "付款日", "required": True, "type": "date"}]},
]


async def seed_templates(session: AsyncSession) -> int:
    count = 0
    for item in TEMPLATE_SEED:
        existing = (
            await session.execute(
                select(DocumentTemplate).where(DocumentTemplate.code == item["code"])
            )
        ).scalars().first()
        if existing:
            continue
        session.add(
            DocumentTemplate(
                tenant_id="platform",
                reviewed_by_lawyer=False,
                usage_count=0,
                **item,
            )
        )
        count += 1
    return count
