"""真实回调与模拟消息共用的消息处理入口。"""

from __future__ import annotations

import logging
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.models.schemas import MessageCreateResult
from app.repositories.message_repository import create_message
from app.repositories.report_detection_repository import deserialize_matched_rules
from app.services import report_detection_service
from app.services.message_normalizer import normalize_jjt_message


logger = logging.getLogger(__name__)


def process_plain_message(
    payload: dict[str, Any],
    source: Literal["jjt", "mock"],
    session: Session,
) -> MessageCreateResult:
    normalized = normalize_jjt_message(payload, source)
    outcome = create_message(session, normalized)
    detection = None
    if not outcome.duplicate:
        try:
            detection = report_detection_service.detect_and_save_report(
                session, outcome.message
            )
        except Exception as exc:
            session.rollback()
            logger.exception(
                "report detection failed msgid=%s database_id=%s error_type=%s",
                normalized.msgid,
                outcome.message.id,
                type(exc).__name__,
            )
    result = MessageCreateResult(
        status="ignored" if outcome.duplicate else "saved",
        msgid=normalized.msgid,
        duplicate=outcome.duplicate,
        database_id=outcome.message.id,
        detection_status=(detection.detection_status if detection else None),
        score=(detection.score if detection else None),
        is_report_candidate=(detection.is_report_candidate if detection else None),
        matched_rules=(
            deserialize_matched_rules(detection.matched_rules) if detection else None
        ),
        reason=(detection.reason if detection else None),
    )
    logger.info(
        "message processed msgid=%s chatid=%s sender_userid=%s "
        "msgtype=%s duplicate=%s database_id=%s",
        normalized.msgid,
        normalized.chatid,
        normalized.sender_userid,
        normalized.msgtype,
        outcome.duplicate,
        outcome.message.id,
    )
    return result
