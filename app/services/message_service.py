"""真实回调与模拟消息共用的消息处理入口。"""

from __future__ import annotations

import logging
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.models.schemas import MessageCreateResult
from app.repositories.message_repository import create_message
from app.services.message_normalizer import normalize_jjt_message


logger = logging.getLogger(__name__)


def process_plain_message(
    payload: dict[str, Any],
    source: Literal["jjt", "mock"],
    session: Session,
) -> MessageCreateResult:
    normalized = normalize_jjt_message(payload, source)
    outcome = create_message(session, normalized)
    result = MessageCreateResult(
        status="ignored" if outcome.duplicate else "saved",
        msgid=normalized.msgid,
        duplicate=outcome.duplicate,
        database_id=outcome.message.id,
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

