"""人数确认请求的持久化读写。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.database_models import (
    Message,
    PeopleConfirmationRequest,
    ProjectReport,
)


def get_pending_request(
    session: Session, *, chatid: str, sender_userid: str
) -> PeopleConfirmationRequest | None:
    statement = (
        select(PeopleConfirmationRequest)
        .where(
            PeopleConfirmationRequest.chatid == chatid,
            PeopleConfirmationRequest.sender_userid == sender_userid,
            PeopleConfirmationRequest.status == "pending",
        )
        .order_by(PeopleConfirmationRequest.id.desc())
        .limit(1)
    )
    return session.scalar(statement)


def create_pending_request(
    session: Session,
    *,
    message: Message,
    report: ProjectReport,
    requested_fields: list[str],
) -> PeopleConfirmationRequest:
    existing = get_pending_request(
        session, chatid=message.chatid, sender_userid=message.sender_userid
    )
    if existing is not None:
        _resolve(existing, "ignored", message.id)
    request = PeopleConfirmationRequest(
        chatid=message.chatid,
        sender_userid=message.sender_userid,
        project_report_id=report.id,
        source_message_id=message.id,
        requested_fields=json.dumps(requested_fields, ensure_ascii=False),
        status="pending",
    )
    session.add(request)
    session.commit()
    session.refresh(request)
    return request


def requested_fields(request: PeopleConfirmationRequest) -> list[str]:
    value = json.loads(request.requested_fields)
    return [item for item in value if isinstance(item, str)]


def update_requested_fields(
    session: Session,
    request: PeopleConfirmationRequest,
    fields: list[str],
    resolved_by: Message,
) -> None:
    request.requested_fields = json.dumps(fields, ensure_ascii=False)
    request.resolved_by_message_id = resolved_by.id
    request.updated_at = datetime.now(timezone.utc)
    session.commit()


def resolve_request(
    session: Session,
    request: PeopleConfirmationRequest,
    *,
    status: str,
    resolved_by: Message,
) -> None:
    _resolve(request, status, resolved_by.id)
    session.commit()


def _resolve(
    request: PeopleConfirmationRequest, status: str, message_id: int
) -> None:
    now = datetime.now(timezone.utc)
    request.status = status
    request.resolved_by_message_id = message_id
    request.resolved_at = now
    request.updated_at = now
