"""消息附件数据库操作。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.database_models import Message, MessageAttachment
from app.models.schemas import NormalizedAttachment


def create_attachments(
    session: Session,
    message: Message,
    attachments: list[NormalizedAttachment],
) -> list[MessageAttachment]:
    records = [
        MessageAttachment(
            message=message,
            attachment_type=item.attachment_type,
            remote_url=item.remote_url,
            local_path=item.local_path,
            download_status=item.download_status,
            md5=item.md5,
        )
        for item in attachments
    ]
    session.add_all(records)
    return records


def list_for_message(session: Session, message_id: int) -> list[MessageAttachment]:
    statement = (
        select(MessageAttachment)
        .where(MessageAttachment.message_id == message_id)
        .order_by(MessageAttachment.id.asc())
    )
    return list(session.scalars(statement).all())

