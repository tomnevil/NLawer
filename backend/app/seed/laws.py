"""法规条文种子（platform 租户共享）。

覆盖劳动用工与合同纠纷高频条款，供 RAG 检索与引用溯源。
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.citation import LawArticle

LAW_SEED: list[dict] = [
    {
        "law_name": "中华人民共和国劳动合同法",
        "article_no": "第82条",
        "chapter": "第二章 劳动合同的订立",
        "content": "用人单位自用工之日起超过一个月不满一年未与劳动者订立书面劳动合同的，应当向劳动者每月支付二倍的工资。",
        "tags": "劳动争议,劳动合同,二倍工资",
        "effective_date": "2008-01-01",
        "region": "全国",
    },
    {
        "law_name": "中华人民共和国劳动合同法",
        "article_no": "第47条",
        "chapter": "第四章 劳动合同的解除和终止",
        "content": "经济补偿按劳动者在本单位工作的年限，每满一年支付一个月工资的标准向劳动者支付。六个月以上不满一年的，按一年计算；不满六个月的，向劳动者支付半个月工资的经济补偿。",
        "tags": "劳动争议,经济补偿,解除",
        "effective_date": "2008-01-01",
        "region": "全国",
    },
    {
        "law_name": "中华人民共和国劳动合同法",
        "article_no": "第87条",
        "chapter": "第四章 劳动合同的解除和终止",
        "content": "用人单位违反本法规定解除或者终止劳动合同的，应当依照本法第四十七条规定的经济补偿标准的二倍向劳动者支付赔偿金。",
        "tags": "劳动争议,违法解除,赔偿金",
        "effective_date": "2008-01-01",
        "region": "全国",
    },
    {
        "law_name": "中华人民共和国劳动合同法",
        "article_no": "第38条",
        "chapter": "第四章 劳动合同的解除和终止",
        "content": "用人单位有下列情形之一的，劳动者可以解除劳动合同：（一）未按照劳动合同约定提供劳动保护或者劳动条件的；（二）未及时足额支付劳动报酬的；（三）未依法为劳动者缴纳社会保险费的；",
        "tags": "劳动争议,劳动者解除,欠薪",
        "effective_date": "2008-01-01",
        "region": "全国",
    },
    {
        "law_name": "中华人民共和国劳动合同法",
        "article_no": "第40条",
        "chapter": "第四章 劳动合同的解除和终止",
        "content": "有下列情形之一的，用人单位提前三十日以书面形式通知劳动者本人或者额外支付劳动者一个月工资后，可以解除劳动合同：（一）劳动者患病或者非因工负伤，在规定的医疗期满后不能从事原工作，也不能从事由用人单位另行安排的工作的；",
        "tags": "劳动争议,用人单位解除,代通知金",
        "effective_date": "2008-01-01",
        "region": "全国",
    },
    {
        "law_name": "中华人民共和国劳动争议调解仲裁法",
        "article_no": "第27条",
        "chapter": "第二章 申请和受理",
        "content": "劳动争议申请仲裁的时效期间为一年。仲裁时效期间从当事人知道或者应当知道其权利被侵害之日起计算。劳动关系存续期间因拖欠劳动报酬发生争议的，劳动者申请仲裁不受本条第一款规定的仲裁时效期间的限制。",
        "tags": "劳动争议,仲裁时效,一年",
        "effective_date": "2008-05-01",
        "region": "全国",
    },
    {
        "law_name": "中华人民共和国劳动争议调解仲裁法",
        "article_no": "第6条",
        "chapter": "第一章 总则",
        "content": "发生劳动争议，当事人对自己提出的主张，有责任提供证据。与争议事项有关的证据属于用人单位掌握管理的，用人单位应当提供；用人单位不提供的，应当承担不利后果。",
        "tags": "劳动争议,举证责任,用人单位",
        "effective_date": "2008-05-01",
        "region": "全国",
    },
    {
        "law_name": "中华人民共和国民法典",
        "article_no": "第577条",
        "chapter": "合同编 第一分编 通则 第八章 违约责任",
        "content": "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。",
        "tags": "合同纠纷,违约责任",
        "effective_date": "2021-01-01",
        "region": "全国",
    },
    {
        "law_name": "中华人民共和国民法典",
        "article_no": "第584条",
        "chapter": "合同编 第一分编 通则 第八章 违约责任",
        "content": "当事人一方不履行合同义务或者履行合同义务不符合约定，造成对方损失的，损失赔偿额应当相当于因违约所造成的损失，包括合同履行后可以获得的利益；但是，不得超过违约一方订立合同时预见到或者应当预见到的因违约可能造成的损失。",
        "tags": "合同纠纷,损失赔偿,违约金",
        "effective_date": "2021-01-01",
        "region": "全国",
    },
    {
        "law_name": "中华人民共和国民法典",
        "article_no": "第563条",
        "chapter": "合同编 第一分编 通则 第七章 合同的权利义务终止",
        "content": "有下列情形之一的，当事人可以解除合同：（一）因不可抗力致使不能实现合同目的；（二）在履行期限届满前，当事人一方明确表示或者以自己的行为表明不履行主要债务；（三）当事人一方迟延履行主要债务，经催告后在合理期限内仍未履行；",
        "tags": "合同纠纷,法定解除",
        "effective_date": "2021-01-01",
        "region": "全国",
    },
    {
        "law_name": "中华人民共和国劳动合同法",
        "article_no": "第26条",
        "chapter": "第三章 劳动合同的履行和变更",
        "content": "下列劳动合同无效或者部分无效：（一）以欺诈、胁迫的手段或者乘人之危，使对方在违背真实意思的情况下订立或者变更劳动合同的；（二）用人单位免除自己的法定责任、排除劳动者权利的；",
        "tags": "劳动争议,合同无效",
        "effective_date": "2008-01-01",
        "region": "全国",
    },
    {
        "law_name": "工伤保险条例",
        "article_no": "第37条",
        "chapter": "第六章 工伤保险待遇",
        "content": "职工因工致残被鉴定为七级至十级伤残的，享受一次性伤残补助金；劳动、聘用合同期满终止，或者职工本人提出解除劳动、聘用合同的，由工伤保险基金支付一次性工伤医疗补助金，由用人单位支付一次性伤残就业补助金。",
        "tags": "工伤,伤残补助",
        "effective_date": "2011-01-01",
        "region": "全国",
    },
]


async def seed_laws(session: AsyncSession) -> int:
    """幂等灌入法规条文，返回新增条数。"""
    count = 0
    for item in LAW_SEED:
        existing = (
            await session.execute(
                select(LawArticle).where(
                    LawArticle.law_name == item["law_name"],
                    LawArticle.article_no == item["article_no"],
                )
            )
        ).scalars().first()
        if existing:
            continue
        session.add(LawArticle(tenant_id="platform", **item))
        count += 1
    return count
