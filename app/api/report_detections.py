"""施工日报初步识别结果查询接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.schemas import (
    DetectionStatus,
    ReportDetectionListItem,
    ReportDetectionListResponse,
)
from app.repositories.report_detection_repository import (
    deserialize_matched_rules,
    list_detections,
)


router = APIRouter(prefix="/api", tags=["施工日报初步识别"])


@router.get("/report-detections", response_model=ReportDetectionListResponse)
def query_report_detections(
    detection_status: DetectionStatus | None = Query(default=None),
    chatid: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> ReportDetectionListResponse:
    return _query_detections(
        session,
        detection_status=detection_status,
        chatid=chatid,
        limit=limit,
        offset=offset,
    )


@router.get("/report-candidates", response_model=ReportDetectionListResponse)
def query_report_candidates(
    chatid: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> ReportDetectionListResponse:
    return _query_detections(
        session,
        detection_status="report_candidate",
        chatid=chatid,
        limit=limit,
        offset=offset,
    )


def _query_detections(
    session: Session,
    *,
    detection_status: DetectionStatus | None,
    chatid: str | None,
    limit: int,
    offset: int,
) -> ReportDetectionListResponse:
    records = list_detections(
        session,
        detection_status=detection_status,
        chatid=chatid,
        limit=limit,
        offset=offset,
    )
    return ReportDetectionListResponse(
        items=[
            ReportDetectionListItem(
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
                chatid=record.message.chatid,
                msgtype=record.message.msgtype,
                text_content=record.message.text_content,
                received_at=record.message.received_at,
            )
            for record in records
        ],
        limit=limit,
        offset=offset,
    )
