"""五类意图识别与「是否需派单」判定（PRD 5.2）。

采用「规则 + 关键词」的轻量判别，无 Key 时也能稳定演示；接入 LLM 后
可改为模型判别（router.TaskType.CONSULT 等），但判定结果结构保持一致。
"""
from typing import Optional

from app.models.enums import IntentType

# 各意图的高频关键词（演示用，可随种子知识库扩充）
_KEYWORDS: dict[IntentType, list[str]] = {
    IntentType.CONSULT: ["咨询", "请问", "怎么", "是否", "能否", "法律", "规定", "依据", "胜算", "风险"],
    IntentType.DOCUMENT: ["起草", "写一份", "合同", "起诉状", "答辩状", "文书", "模板", "协议", "函"],
    IntentType.CALCULATION: ["计算", "赔偿", "多少钱", "金额", "补偿", "二倍工资", "工伤", "标准"],
    IntentType.REVIEW: ["审查", "审核", "把关", "风险条款", "看看合同", "修改建议", "合规"],
    IntentType.ENTRUST: ["委托", "立案", "起诉", "代理", "打官司", "请律师", "开庭", "出庭", "仲裁"],
}


def recognize_intent(text: str) -> IntentType:
    """从用户文本识别最可能的意图（取命中关键词最多的类别）。"""
    text = (text or "").lower()
    best: IntentType = IntentType.CONSULT
    best_score = 0
    for intent, words in _KEYWORDS.items():
        score = sum(1 for w in words if w in text)
        if score > best_score:
            best, best_score = intent, score
    return best


def intent_needs_dispatch(intent: IntentType, *, complexity_hint: int = 0) -> bool:
    """判定该意图是否需要进入派单（转人工律师）。

    - 委托类（ENTRUST）：一定进派单（复杂纠纷 / 需出庭）
    - 审查 / 计算类：复杂时进派单，简单直答
    - 咨询 / 文书类：默认 AI 直答，除非复杂度高
    """
    if intent == IntentType.ENTRUST:
        return True
    if intent in (IntentType.REVIEW, IntentType.CALCULATION):
        return complexity_hint >= 2
    return complexity_hint >= 3


def summarize_dispute_type(text: str) -> Optional[str]:
    """粗略抽取纠纷类型，供派单规则与案件分类使用。"""
    mapping = {
        "劳动争议": ["工资", "工伤", "社保", "劳动合同", "辞退", "加班", "二倍工资", "竞业"],
        "合同纠纷": ["合同", "违约", "货款", "买卖", "借款", "租赁", "欠款"],
        "婚姻家庭": ["离婚", "抚养", "财产分割", "继承", "婚姻"],
        "交通事故": ["事故", "车祸", "交通肇事", "伤残"],
        "侵权纠纷": ["侵权", "名誉", "损害", "打人", "人身"],
    }
    for dtype, words in mapping.items():
        if any(w in (text or "") for w in words):
            return dtype
    return None
