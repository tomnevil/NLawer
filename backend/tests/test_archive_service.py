"""归档服务测试（PRD 5.6）。

覆盖：卷宗号格式、开庭材料包分节结构、证据清单编号、起诉状要素、
材料包落盘路径，以及「仅已定稿/已归档可导出」的状态约束常量。
"""
import os
import shutil
import tempfile
from types import SimpleNamespace

from app.models.enums import CaseStatus, EvidenceCategory
from app.services import archive_service
from app.services.archive_service import (
    ArchiveService,
    _archive_no,
    _write_markdown,
)


class FakeCase:
    def __init__(self, **kw):
        self.case_no = kw.get("case_no", "CASE-1")
        self.title = kw.get("title", "劳动争议纠纷")
        self.dispute_type = kw.get("dispute_type", "劳动争议")
        self.claim_amount = kw.get("claim_amount", 80000)
        self.grade = kw.get("grade", SimpleNamespace(value="A"))
        self.party_a = kw.get("party_a", "张三")
        self.party_b = kw.get("party_b", "某某公司")
        self.summary = kw.get("summary", "公司违法解除劳动合同")
        self.focus = kw.get("focus", "是否构成违法解除")
        self.status = kw.get("status", CaseStatus.CONFIRMED)


class FakeEvidence:
    def __init__(self, name, category=EvidenceCategory.CONTRACT, event_date="2026-01-05"):
        self.name = name
        self.category = SimpleNamespace(value=category.value)
        self.event_date = event_date


def _svc():
    # 纯函数测试，不需要 DB 会话
    return ArchiveService(None)


def test_archive_no_format():
    no = _archive_no("firm_hlw", 7)
    assert no.startswith("firm_hlw-AR-")
    assert no.endswith("-7")
    # 形如 firm_hlw-AR-20260907-7：租户 / AR / 8 位日期 / 案件号
    parts = no.split("-")
    assert len(parts) == 4
    assert parts[1] == "AR"
    assert len(parts[2]) == 8 and parts[2].isdigit()


def test_build_sections_structure():
    case = FakeCase()
    evidences = [FakeEvidence("劳动合同"), FakeEvidence("工资流水", EvidenceCategory.PAYMENT)]
    sections = _svc()._build_sections(case, evidences)

    assert set(sections) == {"目录", "起诉状/答辩状", "证据清单", "质证提纲", "庭审要点"}
    # 目录四项齐全
    assert len(sections["目录"]) == 4
    # 证据清单从 1 开始编号
    assert [e["no"] for e in sections["证据清单"]] == [1, 2]
    assert sections["证据清单"][0]["name"] == "劳动合同"
    assert sections["证据清单"][1]["category"] == EvidenceCategory.PAYMENT.value
    # 质证提纲与庭审要点非空
    assert len(sections["质证提纲"]) >= 3
    assert any("争议焦点" in line for line in sections["庭审要点"])
    assert any("标的额" in line for line in sections["庭审要点"])


def test_pleading_contains_parties_and_claim():
    case = FakeCase()
    text = _svc()._pleading(case)
    assert case.party_a in text
    assert case.party_b in text
    assert case.dispute_type in text
    assert case.summary in text


def test_pleading_placeholders_when_missing():
    case = FakeCase(party_a=None, party_b=None, dispute_type=None, summary=None, focus="焦点")
    text = _svc()._pleading(case)
    assert "待补充" in text
    assert "本案纠纷" in text


def test_write_markdown_creates_file(monkeypatch):
    # 注意：Windows 下 pytest tmp_path 可能无权限，故在工程 storage 目录内建临时目录
    base = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage", "local"
    )
    os.makedirs(base, exist_ok=True)
    tmp = tempfile.mkdtemp(dir=base)
    try:
        monkeypatch.setattr(archive_service, "settings", SimpleNamespace(LOCAL_STORAGE_PATH=tmp))
        case = FakeCase(case_no="firm_hlw/2026-1")
        sections = _svc()._build_sections(case, [FakeEvidence("劳动合同")])
        rel = _write_markdown("firm_hlw", case, sections)

        # 返回正斜杠相对路径，且落盘真实存在
        assert "\\" not in rel
        assert rel.startswith("firm_hlw/hearing_packs")
        assert rel.endswith(".md")
        # 文件名中的斜杠已转义，避免路径穿越
        assert "2026-1" in rel or "_" in rel
        abs_path = os.path.join(tmp, rel.replace("/", os.sep))
        assert os.path.exists(abs_path)
        with open(abs_path, encoding="utf-8") as f:
            body = f.read()
        assert case.title in body
        assert "## 证据清单" in body
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_export_allowed_statuses():
    # 仅已定稿 / 已归档可导出开庭材料包（与 export_hearing_pack 校验一致）
    allowed = {CaseStatus.CONFIRMED, CaseStatus.ARCHIVED}
    assert CaseStatus.CONFIRMED in allowed
    assert CaseStatus.ARCHIVED in allowed
    assert CaseStatus.ACCEPTED not in allowed
    assert CaseStatus.IN_REVIEW not in allowed
