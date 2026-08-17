"""单条施工日报结构化提取、查询与人工修正接口。"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.database_models import Message, ProjectReport
from app.models.report_schemas import (
    ExtractionStatus,
    ExtractedEquipment,
    ExtractedWorkItem,
    ProjectReportListResponse,
    ProjectReportPatch,
    ProjectReportResponse,
)
from app.repositories.message_repository import get_message_detail
from app.repositories.project_report_repository import (
    apply_manual_patch,
    deserialize_missing_fields,
    deserialize_string_list,
    get_by_msgid,
    list_reports,
)
from app.services.llm_extraction_client import ReportExtractionClient
from app.services.report_extraction_service import (
    ReportExtractionFailedError,
    ReportExtractionTimeoutError,
    extract_and_save_report,
)


router = APIRouter(prefix="/api", tags=["施工日报结构化提取"])
logger = logging.getLogger(__name__)


@router.post(
    "/messages/{msgid}/extract-report", response_model=ProjectReportResponse
)
def extract_message_report(
    msgid: str,
    request: Request,
    session: Session = Depends(get_db),
) -> ProjectReportResponse:
    message = get_message_detail(session, msgid)
    if message is None:
        raise HTTPException(status_code=404, detail="消息不存在")
    _ensure_message_is_eligible(message)

    client: ReportExtractionClient | None = getattr(
        request.app.state, "report_extraction_client", None
    )
    if client is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "未配置大模型，请设置 LLM_API_KEY、LLM_MODEL 和 LLM_BASE_URL"
            ),
        )
    try:
        record = extract_and_save_report(session, message, client)
    except ReportExtractionTimeoutError as exc:
        logger.warning(
            "report extraction timed out msgid=%s database_id=%s",
            message.msgid,
            message.id,
        )
        raise HTTPException(status_code=504, detail=str(exc)) from None
    except ReportExtractionFailedError as exc:
        logger.warning(
            "report extraction failed msgid=%s database_id=%s error_type=%s",
            message.msgid,
            message.id,
            type(exc).__name__,
        )
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except SQLAlchemyError:
        session.rollback()
        logger.exception(
            "report extraction persistence failed msgid=%s database_id=%s",
            message.msgid,
            message.id,
        )
        raise HTTPException(status_code=500, detail="结构化提取结果保存失败") from None
    return _to_response(record)


@router.get("/project-reports", response_model=ProjectReportListResponse)
def query_project_reports(
    project_name: str | None = Query(default=None),
    report_date: date | None = Query(default=None),
    extraction_status: ExtractionStatus | None = Query(default=None),
    chatid: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> ProjectReportListResponse:
    records = list_reports(
        session,
        project_name=project_name,
        report_date=report_date,
        extraction_status=extraction_status,
        chatid=chatid,
        limit=limit,
        offset=offset,
    )
    return ProjectReportListResponse(
        items=[_to_response(record) for record in records],
        limit=limit,
        offset=offset,
    )


@router.get("/project-reports/{msgid}", response_model=ProjectReportResponse)
def query_project_report(
    msgid: str,
    session: Session = Depends(get_db),
) -> ProjectReportResponse:
    record = get_by_msgid(session, msgid)
    if record is None:
        raise HTTPException(status_code=404, detail="结构化日报不存在")
    return _to_response(record)


@router.patch("/project-reports/{msgid}", response_model=ProjectReportResponse)
def patch_project_report(
    msgid: str,
    patch: ProjectReportPatch,
    session: Session = Depends(get_db),
) -> ProjectReportResponse:
    record = get_by_msgid(session, msgid)
    if record is None:
        raise HTTPException(status_code=404, detail="结构化日报不存在")
    try:
        updated = apply_manual_patch(session, record, patch)
    except SQLAlchemyError:
        session.rollback()
        logger.exception(
            "manual project report patch failed msgid=%s report_id=%s",
            record.msgid,
            record.id,
        )
        raise HTTPException(status_code=500, detail="人工修正保存失败") from None
    return _to_response(updated)


def _ensure_message_is_eligible(message: Message) -> None:
    detection = message.report_detection
    if (
        detection is None
        or detection.detection_status not in {"report_candidate", "needs_review"}
        or message.msgtype not in {"text", "mixed"}
        or not message.text_content.strip()
    ):
        raise HTTPException(
            status_code=409,
            detail="仅 report_candidate 或 needs_review 的正文消息允许提取",
        )


def _to_response(record: ProjectReport) -> ProjectReportResponse:
    missing_fields = deserialize_missing_fields(record.missing_fields)
    return ProjectReportResponse(
        id=record.id,
        msgid=record.msgid,
        message_id=record.message_id,
        chatid=record.message.chatid,
        chat_name=record.message.chat_name,
        project_name=record.project_name,
        report_date=record.report_date,
        weather=record.weather,
        management_count=record.management_count,
        worker_count=record.worker_count,
        equipment=(
            None
            if "equipment" in missing_fields
            else [
                ExtractedEquipment(name=item.name, count=item.count, unit=item.unit)
                for item in record.equipment
            ]
        ),
        work_items=(
            None
            if "work_items" in missing_fields
            else [
                ExtractedWorkItem(
                    location=item.location,
                    content=item.content,
                    progress=item.progress,
                )
                for item in record.work_items
            ]
        ),
        tomorrow_plan=record.tomorrow_plan,
        safety_status=record.safety_status,
        quality_status=record.quality_status,
        missing_fields=missing_fields,
        confidence=record.confidence,
        extraction_status=record.extraction_status,
        extraction_source=record.extraction_source,
        relevance_status=record.relevance_status,
        relevance_reason=record.relevance_reason,
        relevance_confidence=record.relevance_confidence,
        date_source=record.date_source,
        normalization_warnings=deserialize_string_list(
            record.normalization_warnings
        ),
        raw_extraction_json=record.raw_extraction_json,
        error_message=record.error_message,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
