"""汇总日报人工确认、发送抢占和审计记录状态机。"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.database_models import (
    DailyReportSendAttempt,
    DailyReportSummary,
    Message,
)
from app.repositories.daily_report_summary_repository import get_summary
from app.repositories.message_repository import get_by_msgid
from app.services.response_url_client import (
    ResponseUrlClient,
    ResponseUrlSendResult,
)


logger = logging.getLogger(__name__)
RESPONSE_URL_WARNING_AGE_SECONDS = 3600


class SummaryNotFoundError(LookupError):
    pass


class TriggerMessageNotFoundError(LookupError):
    pass


class PublicationConflictError(RuntimeError):
    pass


def confirm_summary(
    session: Session,
    *,
    summary_id: int,
    confirmed_by: str,
    confirmation_note: str | None,
) -> DailyReportSummary:
    summary = session.get(DailyReportSummary, summary_id)
    if summary is None:
        raise SummaryNotFoundError
    if summary.publication_status not in {"draft", "send_failed"}:
        if summary.publication_status == "sent":
            raise PublicationConflictError("已发送日报不能重复确认")
        raise PublicationConflictError(
            f"当前发布状态 {summary.publication_status} 不允许确认"
        )

    now = datetime.now(timezone.utc)
    summary.publication_status = "confirmed"
    summary.confirmed_by = confirmed_by
    summary.confirmed_at = now
    summary.confirmation_note = confirmation_note
    summary.sent_at = None
    summary.updated_at = now
    session.commit()
    return get_summary(session, summary_id) or summary


def unconfirm_summary(
    session: Session, *, summary_id: int
) -> DailyReportSummary:
    summary = session.get(DailyReportSummary, summary_id)
    if summary is None:
        raise SummaryNotFoundError
    if summary.publication_status != "confirmed":
        if summary.publication_status in {"sending", "sent"}:
            raise PublicationConflictError(
                f"当前发布状态 {summary.publication_status} 不允许取消确认"
            )
        raise PublicationConflictError("只有 confirmed 日报可以取消确认")

    summary.publication_status = "draft"
    summary.confirmed_by = None
    summary.confirmed_at = None
    summary.confirmation_note = None
    summary.sent_at = None
    summary.updated_at = datetime.now(timezone.utc)
    session.commit()
    return get_summary(session, summary_id) or summary


def send_summary(
    session: Session,
    *,
    summary_id: int,
    trigger_msgid: str,
    client: ResponseUrlClient,
) -> tuple[DailyReportSummary, DailyReportSendAttempt, list[str]]:
    summary = session.get(DailyReportSummary, summary_id)
    if summary is None:
        raise SummaryNotFoundError
    _ensure_sendable(summary)

    trigger = get_by_msgid(session, trigger_msgid)
    if trigger is None:
        raise TriggerMessageNotFoundError
    response_url = (trigger.response_url or "").strip()
    if not response_url:
        raise PublicationConflictError("触发消息缺少 response_url")
    if trigger.chatid != summary.chatid:
        raise PublicationConflictError("触发消息 chatid 与汇总日报不一致")

    warnings = _response_url_warnings(trigger)
    attempt = _claim_send(
        session,
        summary=summary,
        trigger=trigger,
        response_url=response_url,
        transport=_safe_transport(client.transport),
    )
    try:
        result = client.send(
            response_url=response_url,
            content=summary.markdown_content,
        )
    except Exception as exc:
        logger.error(
            "response_url client raised summary_id=%s attempt_id=%s error_type=%s",
            summary_id,
            attempt.id,
            type(exc).__name__,
        )
        result = ResponseUrlSendResult(
            success=False,
            transport=_safe_transport(client.transport),
            error_type="response_client_error",
            error_message="response_url 发送客户端发生异常",
        )

    _complete_send(
        session,
        summary_id=summary_id,
        attempt_id=attempt.id,
        result=result,
        response_url=response_url,
    )
    refreshed_summary = get_summary(session, summary_id)
    refreshed_attempt = session.get(DailyReportSendAttempt, attempt.id)
    if refreshed_summary is None or refreshed_attempt is None:
        raise RuntimeError("发送结果持久化后无法读取")
    return refreshed_summary, refreshed_attempt, warnings


def _ensure_sendable(summary: DailyReportSummary) -> None:
    status = summary.publication_status
    if status in {"confirmed", "send_failed"}:
        return
    if status == "sent":
        raise PublicationConflictError("日报已发送，禁止重复发送")
    if status == "sending":
        raise PublicationConflictError("日报正在发送，请勿重复提交")
    raise PublicationConflictError("日报尚未人工确认，禁止发送")


def _claim_send(
    session: Session,
    *,
    summary: DailyReportSummary,
    trigger: Message,
    response_url: str,
    transport: str,
) -> DailyReportSendAttempt:
    now = datetime.now(timezone.utc)
    result = session.execute(
        update(DailyReportSummary)
        .where(
            DailyReportSummary.id == summary.id,
            DailyReportSummary.publication_status.in_(
                ("confirmed", "send_failed")
            ),
        )
        .values(publication_status="sending", updated_at=now)
    )
    if result.rowcount != 1:
        session.rollback()
        current = session.get(
            DailyReportSummary, summary.id, populate_existing=True
        )
        if current is None:
            raise SummaryNotFoundError
        _ensure_sendable(current)
        raise PublicationConflictError("日报发送状态发生并发变更")

    attempt = DailyReportSendAttempt(
        summary_id=summary.id,
        trigger_message_id=trigger.id,
        trigger_msgid=trigger.msgid,
        response_url_hash=hashlib.sha256(response_url.encode("utf-8")).hexdigest(),
        send_status="sending",
        transport=transport,
        attempted_at=now,
        created_at=now,
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    return attempt


def _complete_send(
    session: Session,
    *,
    summary_id: int,
    attempt_id: int,
    result: ResponseUrlSendResult,
    response_url: str,
) -> None:
    now = datetime.now(timezone.utc)
    succeeded = bool(result.success)
    final_status = "sent" if succeeded else "send_failed"
    attempt = session.get(DailyReportSendAttempt, attempt_id)
    summary = session.get(DailyReportSummary, summary_id, populate_existing=True)
    if attempt is None or summary is None:
        raise RuntimeError("发送审计记录不存在")

    attempt.send_status = final_status
    attempt.transport = _safe_transport(result.transport)
    attempt.http_status_code = result.http_status_code
    attempt.error_type = _safe_error(result.error_type, response_url, 128)
    attempt.error_message = _safe_error(result.error_message, response_url, 2000)
    attempt.completed_at = now
    summary.publication_status = final_status
    summary.sent_at = now if succeeded else None
    summary.updated_at = now
    session.commit()


def _response_url_warnings(trigger: Message) -> list[str]:
    age_seconds = (
        datetime.now(timezone.utc) - trigger.received_at.astimezone(timezone.utc)
    ).total_seconds()
    if age_seconds > RESPONSE_URL_WARNING_AGE_SECONDS:
        return ["触发消息的 response_url 可能已经过期，请核对发送结果"]
    return []


def _safe_transport(value: str) -> str:
    return "real" if value == "real" else "mock"


def _safe_error(value: str | None, response_url: str, limit: int) -> str | None:
    if value is None:
        return None
    return value.replace(response_url, "[REDACTED]")[:limit]
