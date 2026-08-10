"""在不调用外部模型的前提下修复历史可恢复结构化结果。"""

from __future__ import annotations

import logging

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.database_models import ProjectReport
from app.models.report_schemas import ReportExtractionPayload
from app.repositories.project_report_repository import save_success
from app.services.report_extraction_normalizer import (
    normalize_extraction_response,
)


logger = logging.getLogger(__name__)


def repair_recoverable_project_reports(session: Session) -> int:
    """修复缺日期或旧版单个空施工子项造成的失败记录。"""
    records = list(
        session.scalars(
            select(ProjectReport)
            .options(
                joinedload(ProjectReport.message),
                selectinload(ProjectReport.equipment),
                selectinload(ProjectReport.work_items),
            )
            .where(
                ProjectReport.raw_extraction_json.is_not(None),
                (
                    (ProjectReport.report_date.is_(None))
                    | (ProjectReport.extraction_status == "failed")
                ),
            )
            .order_by(ProjectReport.id.asc())
        ).all()
    )
    repaired = 0
    for record in records:
        raw_response = record.raw_extraction_json
        if not raw_response:
            continue
        normalized = normalize_extraction_response(
            raw_response,
            text_content=record.message.text_content,
            received_at=record.message.received_at,
        )
        try:
            payload = ReportExtractionPayload.model_validate_json(
                normalized.json_text
            )
        except ValidationError:
            continue
        save_success(
            session,
            record,
            payload,
            raw_response,
            date_source=normalized.date_source,
            normalization_warnings=normalized.warnings,
        )
        repaired += 1
    if repaired:
        logger.info("repaired recoverable historical project reports count=%s", repaired)
    return repaired
