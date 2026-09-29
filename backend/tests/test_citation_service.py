"""引用溯源强制校验测试（PRD 5.11）。

覆盖：结论性内容必须有引用（缺失即失败）；空内容放行（避免误伤）；
结构化条目逐条带有效 citation_id。
"""
import pytest

from app.core.errors import BadRequestError
from app.services.citation_service import (
    validate_citations,
    validate_section_citations,
)

_EMPTY_SECTIONS = {"summary": "", "legal_analysis": None, "suggestions": []}


def test_no_conclusion_skips_validation():
    # 没有任何结论性内容 -> 不要求引用
    validate_citations(_EMPTY_SECTIONS, citation_ids=[])
    validate_citations(_EMPTY_SECTIONS, citation_ids=None)


def test_missing_citation_raises():
    sections = {"summary": "本案构成违约", "legal_analysis": "依据民法典"}
    with pytest.raises(BadRequestError):
        validate_citations(sections, citation_ids=[])


def test_valid_citation_passes():
    sections = {"summary": "本案构成违约", "related_laws": [{"citation_id": 1}]}
    validate_citations(sections, citation_ids=[1, 2])


def test_section_level_missing_citation_raises():
    sections = {
        "summary": "结论",
        "related_laws": [{"citation_id": 1}, {"citation_id": 99}],
        "similar_cases": [{"citation_id": 5}],
    }

    def resolver(cid):
        return cid in {1, 5}  # 99 不存在

    with pytest.raises(BadRequestError):
        validate_section_citations(sections, resolver)


def test_section_level_all_valid_passes():
    sections = {
        "summary": "结论",
        "related_laws": [{"citation_id": 1}],
        "similar_cases": [{"citation_id": 2}],
    }

    def resolver(cid):
        return cid in {1, 2}

    validate_section_citations(sections, resolver)


def test_section_level_no_conclusion_skips():
    def resolver(cid):
        return False  # 即使全部无效，无结论也不应报错

    validate_section_citations(_EMPTY_SECTIONS, resolver)
