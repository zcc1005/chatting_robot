"""施工日报初步识别结果的数据库读写。"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.database_models import Message, MessageReportDetection


def get_by_message_id(
    session: Session, message_id: int
) -> MessageReportDetection | None:
    return session.scalar(
        select(MessageReportDetection).where(
            MessageReportDetection.message_id == message_id
        )
    )


def upsert_detection(
    session: Session,
    *,
    message: Message,
    detection_status: str,
    score: int,
    is_report_candidate: bool,
    matched_rules: list[str],
    reason: str,
    detector_version: str,
    detected_at: datetime,
) -> MessageReportDetection:
    record = get_by_message_id(session, message.id)
    serialized_rules = json.dumps(
        matched_rules, ensure_ascii=False, separators=(",", ":")
    )
    if record is None:
        record = MessageReportDetection(
            msgid=message.msgid,
            message_id=message.id,
            detection_status=detection_status,
            score=score,
            is_report_candidate=is_report_candidate,
            matched_rules=serialized_rules,
            reason=reason,
            detector_version=detector_version,
            detected_at=detected_at,
            updated_at=detected_at,
        )
        session.add(record)
    else:
        record.msgid = message.msgid
        record.detection_status = detection_status
        record.score = score
        record.is_report_candidate = is_report_candidate
        record.matched_rules = serialized_rules
        record.reason = reason
        record.detector_version = detector_version
        record.detected_at = detected_at
        record.updated_at = detected_at
    return record


def list_detections(
    session: Session,
    *,
    detection_status: str | None = None,
    chatid: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[MessageReportDetection]:
    statement = (
        select(MessageReportDetection)
        .join(MessageReportDetection.message)
        .options(joinedload(MessageReportDetection.message))
    )
    if detection_status is not None:
        statement = statement.where(
            MessageReportDetection.detection_status == detection_status
        )
    if chatid is not None:
        statement = statement.where(Message.chatid == chatid)
    statement = (
        statement.order_by(
            MessageReportDetection.detected_at.desc(),
            MessageReportDetection.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(statement).all())


def deserialize_matched_rules(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list) or not all(
        isinstance(item, str) for item in parsed
    ):
        return []
    return parsed
