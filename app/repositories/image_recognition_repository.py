"""施工图片识别结果与项目关联的数据库操作。"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.database_models import (
    Message,
    MessageAttachment,
    MessageImageRecognition,
    ProjectReport,
    ProjectReportImage,
)
from app.models.image_recognition_schemas import ImageRecognitionPayload


RECOGNIZER_VERSION = "vision-v1"


def start_recognition(
    session: Session, attachment: MessageAttachment
) -> MessageImageRecognition:
    record = session.scalar(
        select(MessageImageRecognition).where(
            MessageImageRecognition.attachment_id == attachment.id
        )
    )
    now = datetime.now(timezone.utc)
    if record is None:
        record = MessageImageRecognition(
            attachment_id=attachment.id,
            recognition_status="pending",
            recognizer_version=RECOGNIZER_VERSION,
            created_at=now,
            updated_at=now,
        )
        session.add(record)
    else:
        record.recognition_status = "pending"
        record.project_name = None
        record.report_date = None
        record.captured_at = None
        record.weather = None
        record.location = None
        record.construction_content = None
        record.ocr_text = None
        record.scene_description = None
        record.confidence = None
        record.image_sha256 = None
        record.raw_recognition_json = None
        record.error_message = None
        record.recognizer_version = RECOGNIZER_VERSION
        record.updated_at = now
        if record.association is not None:
            session.delete(record.association)
    session.commit()
    session.refresh(record)
    return record


def save_success(
    session: Session,
    record: MessageImageRecognition,
    *,
    payload: ImageRecognitionPayload,
    image_sha256: str,
    raw_response: str,
) -> MessageImageRecognition:
    record.recognition_status = "completed"
    record.project_name = payload.project_name
    record.report_date = payload.report_date
    record.captured_at = payload.captured_at
    record.weather = payload.weather
    record.location = payload.location
    record.construction_content = payload.construction_content
    record.ocr_text = payload.ocr_text
    record.scene_description = payload.scene_description
    record.confidence = payload.confidence
    record.image_sha256 = image_sha256
    record.raw_recognition_json = raw_response
    record.error_message = None
    record.updated_at = datetime.now(timezone.utc)
    session.commit()
    return record


def save_failure(
    session: Session,
    record: MessageImageRecognition,
    *,
    error_message: str,
    image_sha256: str | None = None,
    raw_response: str | None = None,
) -> MessageImageRecognition:
    record.recognition_status = "failed"
    record.image_sha256 = image_sha256
    record.raw_recognition_json = raw_response
    record.error_message = error_message[:1000]
    record.updated_at = datetime.now(timezone.utc)
    session.commit()
    return record


def list_candidate_reports(
    session: Session,
    *,
    chatid: str,
    report_date: date,
) -> list[ProjectReport]:
    statement = (
        select(ProjectReport)
        .join(ProjectReport.message)
        .options(
            joinedload(ProjectReport.message),
            selectinload(ProjectReport.work_items),
        )
        .where(
            Message.chatid == chatid,
            ProjectReport.report_date == report_date,
            ProjectReport.extraction_status.in_(("completed", "needs_review")),
        )
        .order_by(ProjectReport.id.asc())
    )
    return list(session.scalars(statement).all())


def save_association(
    session: Session,
    recognition: MessageImageRecognition,
    *,
    project_report_id: int | None,
    association_status: str,
    score: int,
    matched_rules: list[str],
    candidate_scores: list[dict[str, object]],
    reason: str,
) -> ProjectReportImage:
    record = recognition.association
    now = datetime.now(timezone.utc)
    if record is None:
        record = ProjectReportImage(
            attachment=recognition.attachment,
            recognition=recognition,
            project_report_id=project_report_id,
            association_status=association_status,
            score=score,
            matched_rules="[]",
            candidate_scores_json="[]",
            reason=reason,
            created_at=now,
            updated_at=now,
        )
        session.add(record)
    record.project_report_id = project_report_id
    record.association_status = association_status
    record.score = score
    record.matched_rules = json.dumps(
        matched_rules, ensure_ascii=False, separators=(",", ":")
    )
    record.candidate_scores_json = json.dumps(
        candidate_scores, ensure_ascii=False, separators=(",", ":")
    )
    record.reason = reason
    record.updated_at = now
    session.commit()
    return get_association(session, recognition.attachment_id) or record


def get_association(
    session: Session, attachment_id: int
) -> ProjectReportImage | None:
    statement = (
        select(ProjectReportImage)
        .options(joinedload(ProjectReportImage.project_report))
        .where(ProjectReportImage.attachment_id == attachment_id)
    )
    return session.scalar(statement)


def get_recognition(
    session: Session, attachment_id: int
) -> MessageImageRecognition | None:
    statement = (
        select(MessageImageRecognition)
        .join(MessageImageRecognition.attachment)
        .options(
            joinedload(MessageImageRecognition.attachment).joinedload(
                MessageAttachment.message
            ),
            joinedload(MessageImageRecognition.association).joinedload(
                ProjectReportImage.project_report
            ),
        )
        .where(MessageImageRecognition.attachment_id == attachment_id)
        .execution_options(populate_existing=True)
    )
    return session.scalar(statement)


def list_recognitions(
    session: Session,
    *,
    chatid: str | None = None,
    recognition_status: str | None = None,
    association_status: str | None = None,
    limit: int = 100,
    offset: int = 0,
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
    )
    if chatid is not None:
        statement = statement.where(Message.chatid == chatid)
    if recognition_status is not None:
        statement = statement.where(
            MessageImageRecognition.recognition_status == recognition_status
        )
    if association_status is not None:
        statement = statement.join(MessageImageRecognition.association).where(
            ProjectReportImage.association_status == association_status
        )
    statement = (
        statement.order_by(MessageImageRecognition.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(statement).all())


def deserialize_string_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    return parsed if isinstance(parsed, list) and all(
        isinstance(item, str) for item in parsed
    ) else []


def deserialize_candidate_scores(value: str) -> list[dict[str, object]]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(
        parsed, list
    ) else []
