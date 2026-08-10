"""群聊施工图片手动识别、查询与人工关联接口。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.database_models import MessageImageRecognition, ProjectReport
from app.models.image_recognition_schemas import (
    AssociationStatus,
    ImageAssociationResponse,
    ImageRecognitionListResponse,
    ImageRecognitionResponse,
    ManualImageAssociationPatch,
    RecognitionStatus,
)
from app.repositories.image_recognition_repository import (
    deserialize_candidate_scores,
    deserialize_string_list,
    get_recognition,
    list_recognitions,
)
from app.repositories.message_repository import get_message_detail
from app.services.image_recognition_client import (
    ImageContentLoader,
    ImageRecognitionClient,
)
from app.services.image_recognition_service import (
    ImageMessageNotEligibleError,
    manually_associate_image,
    recognize_message_images,
)


router = APIRouter(prefix="/api", tags=["群聊施工图片识别"])
logger = logging.getLogger(__name__)


@router.post(
    "/messages/{msgid}/recognize-images",
    response_model=list[ImageRecognitionResponse],
)
def recognize_images(
    msgid: str,
    request: Request,
    session: Session = Depends(get_db),
) -> list[ImageRecognitionResponse]:
    message = get_message_detail(session, msgid)
    if message is None:
        raise HTTPException(status_code=404, detail="消息不存在")
    if message.chattype != "group" or not any(
        item.attachment_type == "image" for item in message.attachments
    ):
        raise HTTPException(status_code=409, detail="仅群聊图片消息允许图片识别")

    client: ImageRecognitionClient | None = getattr(
        request.app.state, "image_recognition_client", None
    )
    if client is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "未配置视觉模型，请设置 VISION_API_KEY、VISION_MODEL 和 "
                "VISION_BASE_URL"
            ),
        )
    loader: ImageContentLoader = request.app.state.image_content_loader
    try:
        records = recognize_message_images(
            session,
            message,
            loader=loader,
            client=client,
            timezone=request.app.state.settings.timezone,
        )
    except ImageMessageNotEligibleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except Exception as exc:
        session.rollback()
        logger.error(
            "image recognition orchestration failed msgid=%s message_id=%s "
            "error_type=%s",
            message.msgid,
            message.id,
            type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail="图片识别结果保存失败") from None

    refreshed = [
        get_recognition(session, record.attachment_id) for record in records
    ]
    return [_to_response(item) for item in refreshed if item is not None]


@router.get(
    "/image-recognitions", response_model=ImageRecognitionListResponse
)
def query_image_recognitions(
    chatid: str | None = Query(default=None),
    recognition_status: RecognitionStatus | None = Query(default=None),
    association_status: AssociationStatus | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> ImageRecognitionListResponse:
    records = list_recognitions(
        session,
        chatid=chatid,
        recognition_status=recognition_status,
        association_status=association_status,
        limit=limit,
        offset=offset,
    )
    return ImageRecognitionListResponse(
        items=[_to_response(item) for item in records],
        limit=limit,
        offset=offset,
    )


@router.patch(
    "/image-recognitions/{attachment_id}/association",
    response_model=ImageRecognitionResponse,
)
def patch_image_association(
    attachment_id: int,
    patch: ManualImageAssociationPatch,
    session: Session = Depends(get_db),
) -> ImageRecognitionResponse:
    recognition = get_recognition(session, attachment_id)
    if recognition is None:
        raise HTTPException(status_code=404, detail="图片识别结果不存在")
    if recognition.recognition_status != "completed":
        raise HTTPException(status_code=409, detail="仅识别完成的图片可以关联项目")

    report = None
    if patch.project_report_id is not None:
        report = session.get(ProjectReport, patch.project_report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="目标结构化日报不存在")
    try:
        manually_associate_image(
            session, recognition=recognition, project_report=report
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except Exception as exc:
        session.rollback()
        logger.error(
            "manual image association failed attachment_id=%s error_type=%s",
            attachment_id,
            type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail="人工关联图片保存失败") from None
    refreshed = get_recognition(session, attachment_id)
    if refreshed is None:  # pragma: no cover
        raise HTTPException(status_code=500, detail="图片识别结果读取失败")
    return _to_response(refreshed)


def _to_response(record: MessageImageRecognition) -> ImageRecognitionResponse:
    attachment = record.attachment
    message = attachment.message
    association = record.association
    association_response = None
    if association is not None:
        project_report = association.project_report
        association_response = ImageAssociationResponse(
            id=association.id,
            project_report_id=association.project_report_id,
            project_report_msgid=(
                project_report.msgid if project_report is not None else None
            ),
            project_name=(
                project_report.project_name if project_report is not None else None
            ),
            association_status=association.association_status,
            score=association.score,
            matched_rules=deserialize_string_list(association.matched_rules),
            candidate_scores=deserialize_candidate_scores(
                association.candidate_scores_json
            ),
            reason=association.reason,
            updated_at=association.updated_at,
        )
    return ImageRecognitionResponse(
        id=record.id,
        attachment_id=record.attachment_id,
        message_id=message.id,
        msgid=message.msgid,
        chatid=message.chatid,
        sender_userid=message.sender_userid,
        source_url=attachment.remote_url,
        recognition_status=record.recognition_status,
        project_name=record.project_name,
        report_date=record.report_date,
        captured_at=record.captured_at,
        weather=record.weather,
        location=record.location,
        construction_content=record.construction_content,
        ocr_text=record.ocr_text,
        scene_description=record.scene_description,
        confidence=record.confidence,
        image_sha256=record.image_sha256,
        error_message=record.error_message,
        recognizer_version=record.recognizer_version,
        association=association_response,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
