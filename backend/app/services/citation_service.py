"""引用溯源强制校验（PRD 5.11）。

硬约束：结论性段落（摘要 / 法律依据 / 初步建议 / 相关法条 / 类案）必须至少对应
一个有效 Citation；缺失即标记 CITATION_MISSING，由调用方触发重试或降级——
直接对齐 PRD「引用缺失视为生成失败」。

注意：仅在校验「内容非空」时要求引用；空内容（如案件无类案）放行，避免误伤。
"""
from typing import Any, Dict, List

from app.core.errors import BadRequestError, ErrorCode

# 必须带引用的结论性段落
_CITATION_REQUIRED_SECTIONS = {
    "summary",
    "legal_analysis",
    "suggestions",
    "related_laws",
    "similar_cases",
}


def validate_citations(
    sections: Dict[str, Any],
    citation_ids: List[int],
) -> None:
    """校验：存在结论性内容时，必须有有效引用列表。

    citation_ids 为该分析关联的有效 Citation.id（非空即通过）。
    """
    has_conclusion = any(sections.get(s) for s in _CITATION_REQUIRED_SECTIONS)
    if not has_conclusion:
        return
    if not citation_ids:
        raise BadRequestError(
            "结论性内容缺少引用溯源，生成失败",
            code=ErrorCode.CITATION_MISSING,
            details={"required_sections": sorted(_CITATION_REQUIRED_SECTIONS)},
        )


def validate_section_citations(
    sections: Dict[str, Any],
    citation_resolver,
) -> None:
    """逐段强校验：related_laws / similar_cases 等结构化条目必须含 citation_id。

    citation_resolver(id) -> bool：判断引用是否存在且属于当前租户。
    """
    has_conclusion = any(sections.get(s) for s in _CITATION_REQUIRED_SECTIONS)
    if not has_conclusion:
        return

    missing: List[str] = []
    for key in ("related_laws", "similar_cases"):
        items = sections.get(key) or []
        for i, item in enumerate(items):
            cid = item.get("citation_id") if isinstance(item, dict) else None
            if not cid or not citation_resolver(cid):
                missing.append(f"{key}[{i}]")

    if missing:
        raise BadRequestError(
            "部分结论条目缺少有效引用溯源",
            code=ErrorCode.CITATION_MISSING,
            details={"missing": missing},
        )
