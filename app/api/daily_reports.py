"""确定性日报汇总预览、快照保存与查询接口。"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.database_models import DailyReportSummary
from app.models.daily_report_schemas import (
    DailyReportConfirmationRequest,
    DailyReportPreviewResponse,
    DailyReportRequest,
    DailyReportSendAttemptResponse,
    DailyReportSendAttemptsResponse,
    DailyReportSendRequest,
    DailyReportSendResponse,
    DailyReportSummaryListItem,
    DailyReportSummaryListResponse,
    DailyReportSummaryResponse,
    GenerationStatus,
    PublicationStatus,
)
from app.repositories.daily_report_summary_repository import (
    deserialize_warnings,
    get_summary,
    list_send_attempts,
    list_source_images,
    list_source_reports,
    list_summaries,
    save_summary_snapshot,
)
from app.repositories.message_repository import get_chat_name
from app.services.daily_report_publication_service import (
    PublicationConflictError,
    SummaryNotFoundError,
    TriggerMessageNotFoundError,
    confirm_summary,
    send_summary,
    unconfirm_summary,
)
from app.services.daily_report_summary_service import (
    DailyReportSummaryError,
    build_daily_report_preview,
)
from app.services.duplicate_report_service import auto_select_latest_reports


router = APIRouter(prefix="/api/daily-reports", tags=["多项目日报汇总预览"])
logger = logging.getLogger(__name__)


@router.post("/preview", response_model=DailyReportPreviewResponse)
def preview_daily_report(
    request_data: DailyReportRequest,
    session: Session = Depends(get_db),
) -> DailyReportPreviewResponse:
    reports = auto_select_latest_reports(
        session,
        list_source_reports(
            session,
            chatid=request_data.chatid,
            report_date=request_data.report_date,
        ),
    )
    images = list_source_images(session, chatid=request_data.chatid)
    return _build_preview(session, request_data, reports, images)


@router.post("", response_model=DailyReportSummaryResponse)
def save_daily_report(
    request_data: DailyReportRequest,
    request: Request,
    session: Session = Depends(get_db),
) -> DailyReportSummaryResponse:
    reports = auto_select_latest_reports(
        session,
        list_source_reports(
            session,
            chatid=request_data.chatid,
            report_date=request_data.report_date,
        ),
    )
    images = list_source_images(session, chatid=request_data.chatid)
    preview = _build_preview(session, request_data, reports, images)
    try:
        record = save_summary_snapshot(
            session,
            preview,
            reports,
            public_base_url=request.app.state.settings.public_base_url,
        )
    except SQLAlchemyError:
        session.rollback()
        logger.exception(
            "daily report summary persistence failed chatid=%s report_date=%s",
            request_data.chatid,
            request_data.report_date,
        )
        raise HTTPException(status_code=500, detail="日报汇总快照保存失败") from None
    return _to_detail(record)


@router.get("", response_model=DailyReportSummaryListResponse)
def query_daily_reports(
    chatid: str | None = Query(default=None),
    report_date: date | None = Query(default=None),
    generation_status: GenerationStatus | None = Query(default=None),
    publication_status: PublicationStatus | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> DailyReportSummaryListResponse:
    records = list_summaries(
        session,
        chatid=chatid,
        report_date=report_date,
        generation_status=generation_status,
        publication_status=publication_status,
        limit=limit,
        offset=offset,
    )
    return DailyReportSummaryListResponse(
        items=[_to_list_item(record) for record in records],
        limit=limit,
        offset=offset,
    )


@router.get("/{summary_id}", response_model=DailyReportSummaryResponse)
def query_daily_report_detail(
    summary_id: int,
    session: Session = Depends(get_db),
) -> DailyReportSummaryResponse:
    record = get_summary(session, summary_id)
    if record is None:
        raise HTTPException(status_code=404, detail="日报汇总不存在")
    return _to_detail(record)


@router.post(
    "/{summary_id}/confirm", response_model=DailyReportSummaryResponse
)
def confirm_daily_report(
    summary_id: int,
    request_data: DailyReportConfirmationRequest,
    session: Session = Depends(get_db),
) -> DailyReportSummaryResponse:
    try:
        record = confirm_summary(
            session,
            summary_id=summary_id,
            confirmed_by=request_data.confirmed_by,
            confirmation_note=request_data.confirmation_note,
        )
    except SummaryNotFoundError:
        raise HTTPException(status_code=404, detail="日报汇总不存在") from None
    except PublicationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return _to_detail(record)


@router.post(
    "/{summary_id}/unconfirm", response_model=DailyReportSummaryResponse
)
def unconfirm_daily_report(
    summary_id: int,
    session: Session = Depends(get_db),
) -> DailyReportSummaryResponse:
    try:
        record = unconfirm_summary(session, summary_id=summary_id)
    except SummaryNotFoundError:
        raise HTTPException(status_code=404, detail="日报汇总不存在") from None
    except PublicationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return _to_detail(record)


@router.post("/{summary_id}/send", response_model=DailyReportSendResponse)
def send_daily_report(
    summary_id: int,
    request_data: DailyReportSendRequest,
    request: Request,
    session: Session = Depends(get_db),
) -> DailyReportSendResponse:
    try:
        summary, attempt, warnings = send_summary(
            session,
            summary_id=summary_id,
            trigger_msgid=request_data.trigger_msgid,
            client=request.app.state.response_url_client,
        )
    except SummaryNotFoundError:
        raise HTTPException(status_code=404, detail="日报汇总不存在") from None
    except TriggerMessageNotFoundError:
        raise HTTPException(status_code=404, detail="触发消息不存在") from None
    except PublicationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return DailyReportSendResponse(
        summary_id=summary.id,
        attempt=_to_attempt(attempt),
        publication_status=summary.publication_status,
        sent_at=summary.sent_at,
        warnings=warnings,
    )


@router.get(
    "/{summary_id}/send-attempts",
    response_model=DailyReportSendAttemptsResponse,
)
def query_daily_report_send_attempts(
    summary_id: int,
    session: Session = Depends(get_db),
) -> DailyReportSendAttemptsResponse:
    if session.get(DailyReportSummary, summary_id) is None:
        raise HTTPException(status_code=404, detail="日报汇总不存在")
    return DailyReportSendAttemptsResponse(
        items=[
            _to_attempt(attempt)
            for attempt in list_send_attempts(session, summary_id)
        ]
    )


def _build_preview(
    session: Session, request_data: DailyReportRequest, reports, images
) -> DailyReportPreviewResponse:
    try:
        return build_daily_report_preview(
            reports,
            chatid=request_data.chatid,
            chat_name=get_chat_name(session, request_data.chatid),
            report_date=request_data.report_date,
            image_recognitions=images,
        )
    except DailyReportSummaryError as exc:
        logger.warning(
            "daily report summary rejected chatid=%s report_date=%s error_type=%s",
            request_data.chatid,
            request_data.report_date,
            type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail=str(exc)) from None


def _to_detail(record: DailyReportSummary) -> DailyReportSummaryResponse:
    preview = _snapshot_preview(record)
    return DailyReportSummaryResponse(
        **preview.model_dump(),
        id=record.id,
        publication_status=record.publication_status,
        confirmed_by=record.confirmed_by,
        confirmed_at=record.confirmed_at,
        confirmation_note=record.confirmation_note,
        sent_at=record.sent_at,
        send_attempts=[_to_attempt(item) for item in record.send_attempts],
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _to_list_item(record: DailyReportSummary) -> DailyReportSummaryListItem:
    preview = _snapshot_preview(record)
    return DailyReportSummaryListItem(
        id=record.id,
        chatid=record.chatid,
        chat_name=preview.chat_name,
        report_date=record.report_date,
        project_count=record.project_count,
        fully_complete_project_count=preview.fully_complete_project_count,
        partial_project_count=preview.partial_project_count,
        management_total=record.management_total,
        worker_total=record.worker_total,
        generation_status=record.generation_status,
        publication_status=record.publication_status,
        warnings=deserialize_warnings(record.warnings_json),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _to_attempt(record) -> DailyReportSendAttemptResponse:
    return DailyReportSendAttemptResponse.model_validate(
        record, from_attributes=True
    )


def _snapshot_preview(record: DailyReportSummary) -> DailyReportPreviewResponse:
    try:
        return DailyReportPreviewResponse.model_validate_json(
            record.snapshot_json
        )
    except ValidationError:
        logger.error("daily report snapshot is invalid summary_id=%s", record.id)
        raise HTTPException(status_code=500, detail="日报汇总快照格式损坏") from None
