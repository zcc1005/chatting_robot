"""日报汇总来源查询、快照保存与查询。"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.database_models import (
    DailyReportSummary,
    DailyReportSummaryItem,
    DailyReportSendAttempt,
    Message,
    MessageAttachment,
    MessageImageRecognition,
    ProjectReport,
    ProjectReportImage,
)
from app.models.daily_report_schemas import DailyReportPreviewResponse


def list_source_reports(
    session: Session, *, chatid: str, report_date: date
) -> list[ProjectReport]:
    statement = (
        select(ProjectReport)
        .join(ProjectReport.message)
        .options(
            joinedload(ProjectReport.message),
            selectinload(ProjectReport.equipment),
            selectinload(ProjectReport.work_items),
            selectinload(ProjectReport.images)
            .joinedload(ProjectReportImage.recognition),
            selectinload(ProjectReport.images)
            .joinedload(ProjectReportImage.attachment)
            .joinedload(MessageAttachment.message),
        )
        .where(Message.chatid == chatid, ProjectReport.report_date == report_date)
        .order_by(ProjectReport.id.asc())
    )
    return list(session.scalars(statement).all())


def list_source_images(
    session: Session, *, chatid: str
) -> list[MessageImageRecognition]:
    statement = (
        select(MessageImageRecognition)
        .join(MessageImageRecognition.attachment)
        .join(MessageAttachment.message)
        .options(
            joinedload(MessageImageRecognition.attachment).joinedload(
                MessageAttachment.message
            ),
            joinedload(MessageImageRecognition.association).joinedload(
                ProjectReportImage.project_report
            ),
        )
        .where(Message.chatid == chatid)
        .order_by(MessageImageRecognition.id.asc())
    )
    return list(session.scalars(statement).all())


def save_summary_snapshot(
    session: Session,
    preview: DailyReportPreviewResponse,
    source_reports: list[ProjectReport],
) -> DailyReportSummary:
    now = datetime.now(timezone.utc)
    record = DailyReportSummary(
        chatid=preview.chatid,
        report_date=preview.report_date,
        project_count=preview.project_count,
        management_total=preview.management_total,
        worker_total=preview.worker_total,
        generation_status=preview.generation_status,
        markdown_content=preview.markdown_content,
        warnings_json=json.dumps(
            preview.warnings, ensure_ascii=False, separators=(",", ":")
        ),
        snapshot_json=preview.model_dump_json(),
        created_at=now,
        updated_at=now,
    )
    record.items.extend(
        DailyReportSummaryItem(
            project_report_id=report.id,
            display_order=index,
        )
        for index, report in enumerate(source_reports)
    )
    session.add(record)
    session.commit()
    return get_summary(session, record.id) or record


def get_summary(session: Session, summary_id: int) -> DailyReportSummary | None:
    statement = (
        select(DailyReportSummary)
        .options(
            selectinload(DailyReportSummary.items)
            .joinedload(DailyReportSummaryItem.project_report)
            .joinedload(ProjectReport.message),
            selectinload(DailyReportSummary.send_attempts),
        )
        .where(DailyReportSummary.id == summary_id)
    )
    return session.scalar(statement)


def list_summaries(
    session: Session,
    *,
    chatid: str | None = None,
    report_date: date | None = None,
    generation_status: str | None = None,
    publication_status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[DailyReportSummary]:
    statement = select(DailyReportSummary)
    if chatid is not None:
        statement = statement.where(DailyReportSummary.chatid == chatid)
    if report_date is not None:
        statement = statement.where(DailyReportSummary.report_date == report_date)
    if generation_status is not None:
        statement = statement.where(
            DailyReportSummary.generation_status == generation_status
        )
    if publication_status is not None:
        statement = statement.where(
            DailyReportSummary.publication_status == publication_status
        )
    statement = (
        statement.order_by(
            DailyReportSummary.created_at.desc(), DailyReportSummary.id.desc()
        )
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(statement).all())


def list_send_attempts(
    session: Session, summary_id: int
) -> list[DailyReportSendAttempt]:
    statement = (
        select(DailyReportSendAttempt)
        .where(DailyReportSendAttempt.summary_id == summary_id)
        .order_by(DailyReportSendAttempt.id.asc())
    )
    return list(session.scalars(statement).all())


def deserialize_warnings(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list) or not all(
        isinstance(item, str) for item in parsed
    ):
        return []
    return parsed
