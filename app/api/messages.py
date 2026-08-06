"""SQLite 消息列表和详情查询接口。"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.database_models import Message, MessageReportDetection
from app.models.schemas import (
    AttachmentDetail,
    MessageDetailResponse,
    MessageListItem,
    MessageListResponse,
    ReportDetectionDetail,
    ReportDetectionResponse,
)
from app.repositories.message_repository import get_message_detail, list_messages
from app.repositories.report_detection_repository import deserialize_matched_rules
from app.services import report_detection_service


router = APIRouter(prefix="/api/messages", tags=["消息查询"])
logger = logging.getLogger(__name__)
ProcessStatus = Literal["received", "ignored", "failed", "unsupported"]


@router.get("", response_model=MessageListResponse)
def query_messages(
    request: Request,
    chatid: str | None = Query(default=None),
    msgtype: str | None = Query(default=None),
    process_status: ProcessStatus | None = Query(default=None),
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> MessageListResponse:
    timezone = ZoneInfo(request.app.state.settings.timezone)
    normalized_start = _make_aware(start_time, timezone)
    normalized_end = _make_aware(end_time, timezone)
    if (
        normalized_start is not None
        and normalized_end is not None
        and normalized_start > normalized_end
    ):
        raise HTTPException(status_code=422, detail="start_time 不能晚于 end_time")
    records = list_messages(
        session,
        chatid=chatid,
        msgtype=msgtype,
        process_status=process_status,
        start_time=normalized_start,
        end_time=normalized_end,
        limit=limit,
        offset=offset,
    )
    return MessageListResponse(
        items=[_to_list_item(record) for record in records],
        limit=limit,
        offset=offset,
    )


@router.get("/{msgid}", response_model=MessageDetailResponse)
def query_message_detail(
    msgid: str,
    session: Session = Depends(get_db),
) -> MessageDetailResponse:
    record = get_message_detail(session, msgid)
    if record is None:
        raise HTTPException(status_code=404, detail="消息不存在")
    try:
        raw_payload = json.loads(record.raw_json)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="数据库原始消息格式损坏") from None
    return MessageDetailResponse(
        id=record.id,
        msgid=record.msgid,
        source=record.source,
        aibotid=record.aibotid,
        chatid=record.chatid,
        chattype=record.chattype,
        sender_userid=record.sender_userid,
        msgtype=record.msgtype,
        text_content=record.text_content,
        response_url=_redact_response_url(record.response_url),
        raw_payload=_redact_response_urls(raw_payload),
        received_at=record.received_at,
        process_status=record.process_status,
        created_at=record.created_at,
        updated_at=record.updated_at,
        attachments=[
            AttachmentDetail(
                id=attachment.id,
                attachment_type=attachment.attachment_type,
                remote_url=attachment.remote_url,
                local_path=attachment.local_path,
                download_status=attachment.download_status,
                md5=attachment.md5,
                created_at=attachment.created_at,
            )
            for attachment in record.attachments
        ],
        report_detection=(
            _to_detection_detail(record.report_detection)
            if record.report_detection is not None
            else None
        ),
    )


@router.post("/{msgid}/detect-report", response_model=ReportDetectionResponse)
def detect_message_report(
    msgid: str,
    session: Session = Depends(get_db),
) -> ReportDetectionResponse:
    record = get_message_detail(session, msgid)
    if record is None:
        raise HTTPException(status_code=404, detail="消息不存在")
    try:
        detection = report_detection_service.detect_and_save_report(session, record)
    except Exception as exc:
        session.rollback()
        logger.exception(
            "manual report detection failed msgid=%s database_id=%s error_type=%s",
            record.msgid,
            record.id,
            type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail="施工日报识别失败") from None
    return ReportDetectionResponse(
        msgid=detection.msgid,
        detection_status=detection.detection_status,
        score=detection.score,
        is_report_candidate=detection.is_report_candidate,
        matched_rules=deserialize_matched_rules(detection.matched_rules),
        reason=detection.reason,
    )


def _to_list_item(record: Message) -> MessageListItem:
    return MessageListItem(
        id=record.id,
        msgid=record.msgid,
        source=record.source,
        chatid=record.chatid,
        chattype=record.chattype,
        sender_userid=record.sender_userid,
        msgtype=record.msgtype,
        text_content=record.text_content,
        received_at=record.received_at,
        process_status=record.process_status,
        attachment_count=len(record.attachments),
    )


def _to_detection_detail(record: MessageReportDetection) -> ReportDetectionDetail:
    return ReportDetectionDetail(
        msgid=record.msgid,
        message_id=record.message_id,
        detection_status=record.detection_status,
        score=record.score,
        is_report_candidate=record.is_report_candidate,
        matched_rules=deserialize_matched_rules(record.matched_rules),
        reason=record.reason,
        detector_version=record.detector_version,
        detected_at=record.detected_at,
        updated_at=record.updated_at,
    )


def _redact_response_url(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "[REDACTED]"
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "[REDACTED]"
    path_parts = [part for part in parsed.path.split("/") if part]
    visible_path = f"/{path_parts[0]}/…" if path_parts else ""
    return urlunsplit((parsed.scheme, parsed.netloc, visible_path, "", ""))


def _redact_response_urls(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                _redact_response_url(item) if key == "response_url" else _redact_response_urls(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_response_urls(item) for item in value]
    return value


def _make_aware(value: datetime | None, timezone: ZoneInfo) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone)
    return value
