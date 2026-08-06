"""消息数据库读写与唯一 msgid 去重。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.database_models import Message
from app.models.schemas import NormalizedMessage
from app.repositories.attachment_repository import create_attachments


@dataclass(frozen=True, slots=True)
class MessageCreateOutcome:
    message: Message
    duplicate: bool


def get_by_msgid(session: Session, msgid: str) -> Message | None:
    return session.scalar(select(Message).where(Message.msgid == msgid))


def create_message(
    session: Session, normalized_message: NormalizedMessage
) -> MessageCreateOutcome:
    message = Message(
        msgid=normalized_message.msgid,
        source=normalized_message.source,
        aibotid=normalized_message.aibotid,
        chatid=normalized_message.chatid,
        chattype=normalized_message.chattype,
        sender_userid=normalized_message.sender_userid,
        msgtype=normalized_message.msgtype,
        text_content=normalized_message.text_content,
        response_url=normalized_message.response_url,
        raw_json=json.dumps(
            normalized_message.raw_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        received_at=normalized_message.received_at,
        process_status=normalized_message.process_status,
    )
    session.add(message)
    try:
        create_attachments(session, message, normalized_message.attachments)
        session.commit()
        session.refresh(message)
        return MessageCreateOutcome(message=message, duplicate=False)
    except IntegrityError:
        session.rollback()
        existing = get_by_msgid(session, normalized_message.msgid)
        if existing is None:
            raise
        return MessageCreateOutcome(message=existing, duplicate=True)
    except Exception:
        session.rollback()
        raise


def list_messages(
    session: Session,
    chatid: str | None = None,
    msgtype: str | None = None,
    process_status: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Message]:
    statement = select(Message).options(selectinload(Message.attachments))
    if chatid is not None:
        statement = statement.where(Message.chatid == chatid)
    if msgtype is not None:
        statement = statement.where(Message.msgtype == msgtype)
    if process_status is not None:
        statement = statement.where(Message.process_status == process_status)
    if start_time is not None:
        statement = statement.where(Message.received_at >= start_time)
    if end_time is not None:
        statement = statement.where(Message.received_at <= end_time)
    statement = (
        statement.order_by(Message.received_at.desc(), Message.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(statement).all())


def get_message_detail(session: Session, msgid: str) -> Message | None:
    statement = (
        select(Message)
        .options(
            selectinload(Message.attachments),
            joinedload(Message.report_detection),
        )
        .where(Message.msgid == msgid)
    )
    return session.scalar(statement)
