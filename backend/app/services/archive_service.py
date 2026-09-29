"""备案存档与开庭支持（PRD 5.6）。

- 归档前二次校验：业务对象必须已 CONFIRMED，未确认不可归档（双重保障）
- 卷宗结构化 + 版本控制（ArchiveVersion 快照，支持回溯对比）
- 一键导出开庭材料包：目录 + 起诉状/答辩状 + 证据清单 + 质证提纲 + 庭审要点
"""
import os
from typing import Any, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import BadRequestError, ErrorCode, NotFoundError
from app.models.archive import Archive, ArchiveVersion, HearingPack
from app.models.case import Case, CaseEvent
from app.models.enums import CaseStatus, NotificationType
from app.models.evidence import Evidence
from app.services.notification_service import notify


class ArchiveService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ---------------- 归档 ----------------
    async def archive_case(self, case_id: int, *, actor_id: int, tenant_id: str, note: Optional[str] = None) -> Archive:
        case = await self.db.get(Case, case_id)
        if case is None or case.tenant_id != tenant_id:
            raise NotFoundError("案件不存在", code=ErrorCode.CASE_NOT_FOUND)

        # 硬约束：必须先定稿（CONFIRMED）才能归档
        if case.status != CaseStatus.CONFIRMED:
            raise BadRequestError(
                "未确认定稿的案件不可归档",
                code=ErrorCode.ARCHIVE_NOT_CONFIRMED,
                details={"current_status": case.status.value},
            )

        existing = (
            await self.db.execute(select(Archive).where(Archive.case_id == case_id))
        ).scalars().first()
        if existing is not None:
            return existing

        dossier = await self._build_dossier(case)
        archive = Archive(
            tenant_id=tenant_id,
            case_id=case_id,
            archive_no=_archive_no(tenant_id, case_id),
            title=case.title,
            dossier=dossier,
            current_version=1,
            archived_by=actor_id,
            archived_at=_now(),
            note=note,
        )
        self.db.add(archive)
        await self.db.flush()
        self.db.add(
            ArchiveVersion(
                tenant_id=tenant_id,
                archive_id=archive.id,
                version=1,
                snapshot=dossier,
                change_note="首次归档",
                created_by_user_id=actor_id,
            )
        )
        case.status = CaseStatus.ARCHIVED
        self.db.add(
            CaseEvent(case_id=case_id, event_type="ARCHIVE", title="案件归档", description=f"卷宗号 {archive.archive_no}", actor_user_id=actor_id)
        )
        # 通知客户与承办律师
        targets = [u for u in (case.client_user_id, case.lawyer_id) if u]
        for uid in targets:
            await notify(
                self.db, tenant_id=tenant_id, user_id=uid,
                type=NotificationType.CASE_ARCHIVED,
                content=f"案件《{case.title}》已归档", ref_type="case", ref_id=case_id,
            )
        return archive

    async def _build_dossier(self, case: Case) -> dict[str, Any]:
        """组装卷宗结构化清单。"""
        evidences = list(
            (await self.db.execute(select(Evidence).where(Evidence.case_id == case.id))).scalars().all()
        )
        events = list(
            (await self.db.execute(select(CaseEvent).where(CaseEvent.case_id == case.id))).scalars().all()
        )
        from app.models.analysis import CaseAnalysis

        analyses = list(
            (await self.db.execute(select(CaseAnalysis).where(CaseAnalysis.case_id == case.id))).scalars().all()
        )
        return {
            "case": {
                "case_no": case.case_no,
                "title": case.title,
                "dispute_type": case.dispute_type,
                "grade": case.grade.value,
                "claim_amount": case.claim_amount,
            },
            "pleadings": [a.summary for a in analyses if a.summary],
            "evidence_ids": [e.id for e in evidences],
            "evidence_names": [e.name for e in evidences],
            "research": [a.legal_analysis for a in analyses if a.legal_analysis],
            "timeline": [{"title": e.title, "type": e.event_type} for e in events],
        }

    # ---------------- 版本回溯 ----------------
    async def versions(self, archive_id: int) -> list[ArchiveVersion]:
        rows = (
            await self.db.execute(
                select(ArchiveVersion)
                .where(ArchiveVersion.archive_id == archive_id)
                .order_by(ArchiveVersion.version.asc())
            )
        ).scalars().all()
        return list(rows)

    # ---------------- 开庭材料包 ----------------
    async def export_hearing_pack(self, case_id: int, *, actor_id: int, tenant_id: str) -> HearingPack:
        case = await self.db.get(Case, case_id)
        if case is None or case.tenant_id != tenant_id:
            raise NotFoundError("案件不存在", code=ErrorCode.CASE_NOT_FOUND)
        if case.status not in (CaseStatus.CONFIRMED, CaseStatus.ARCHIVED):
            raise BadRequestError("仅已定稿或已归档案件可导出开庭材料包", code=ErrorCode.HEARING_PACK_EXPORT_FAILED)

        archives = (
            await self.db.execute(select(Archive).where(Archive.case_id == case_id))
        ).scalars().first()
        evidences = list(
            (await self.db.execute(select(Evidence).where(Evidence.case_id == case_id))).scalars().all()
        )
        sections = self._build_sections(case, evidences)

        pack = HearingPack(
            tenant_id=tenant_id,
            case_id=case_id,
            archive_id=archives.id if archives else None,
            title=f"{case.title} 开庭材料包",
            sections=sections,
            generated_by=actor_id,
        )
        self.db.add(pack)
        await self.db.flush()

        try:
            rel_path = _write_markdown(tenant_id, case, sections)
            pack.file_path = rel_path
            if archives is not None:
                archives.hearing_pack_path = rel_path
        except Exception as exc:  # noqa: BLE001  导出失败不阻断，内容已入库
            logger.warning("开庭材料包文件写入失败（内容已入库）: {}", exc)
            raise BadRequestError(
                "开庭材料包导出失败", code=ErrorCode.HEARING_PACK_EXPORT_FAILED, details={"error": str(exc)}
            )
        return pack

    def _build_sections(self, case: Case, evidences: list[Evidence]) -> dict[str, Any]:
        evidence_list = [
            {"no": i + 1, "name": e.name, "category": e.category.value, "event_date": e.event_date}
            for i, e in enumerate(evidences)
        ]
        return {
            "目录": ["一、起诉状/答辩状", "二、证据清单", "三、质证提纲", "四、庭审要点"],
            "起诉状/答辩状": self._pleading(case),
            "证据清单": evidence_list,
            "质证提纲": [
                "对对方证据的真实性、合法性、关联性逐项发表意见",
                "重点质疑无原件核对的复制件与截图",
                "对己方证据说明来源与证明目的",
            ],
            "庭审要点": [
                f"争议焦点：{case.focus or '待明确'}",
                f"请求权基础：{case.dispute_type or '待明确'}相关法律规定",
                f"标的额：{case.claim_amount or '未填写'} 元",
                "重点陈述己方证据链与法律依据，回应对方抗辩",
            ],
        }

    def _pleading(self, case: Case) -> str:
        return (
            f"原告/申请人：{case.party_a or '待补充'}\n"
            f"被告/被申请人：{case.party_b or '待补充'}\n"
            f"诉讼请求：就{case.dispute_type or '本案纠纷'}主张相应权利\n"
            f"事实与理由：{case.summary or (case.focus or '')}"
        )


def _write_markdown(tenant_id: str, case: Case, sections: dict[str, Any]) -> str:
    """将材料包写为 Markdown 落盘，返回相对路径。"""
    rel_dir = os.path.join(tenant_id, "hearing_packs")
    abs_dir = os.path.join(settings.LOCAL_STORAGE_PATH, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    name = f"{case.case_no}_hearing_pack.md".replace("/", "_")
    abs_path = os.path.join(abs_dir, name)

    lines = [f"# {case.title} 开庭材料包", ""]
    for key, val in sections.items():
        lines.append(f"## {key}")
        if isinstance(val, list):
            for item in val:
                lines.append(f"- {item}" if isinstance(item, str) else f"- {item}")
        else:
            lines.append(str(val))
        lines.append("")
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return os.path.join(rel_dir, name).replace("\\", "/")


def _archive_no(tenant_id: str, case_id: int) -> str:
    import datetime

    return f"{tenant_id}-AR-{datetime.datetime.now().strftime('%Y%m%d')}-{case_id}"


def _now() -> str:
    import datetime

    return datetime.datetime.now().isoformat(timespec="seconds")
