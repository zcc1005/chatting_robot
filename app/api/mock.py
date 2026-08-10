"""仅在离线开发环境开放的明文模拟消息接口。"""

from __future__ import annotations

import logging
import base64
import binascii
import re
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.database_models import DailyReportSummary, Message
from app.models.schemas import (
    MessageCreateResult,
    MockImageMessageRequest,
    MockTriggerMessageRequest,
    MockTriggerMessageResponse,
)
from app.services.message_normalizer import MessageNormalizationError
from app.services.message_service import process_plain_message
from app.services.image_recognition_client import (
    ImageRecognitionClientError,
    validate_image_bytes,
)
from app.services.image_recognition_tasks import (
    schedule_image_recognition_if_enabled,
)


router = APIRouter(prefix="/api/dev", tags=["开发模拟"])
logger = logging.getLogger(__name__)
DEV_IMAGE_PATTERN = re.compile(r"^[0-9a-f]{32}\.(?:png|jpg|webp)$")


@router.post(
    "/mock-message",
    response_model=MessageCreateResult,
    response_model_exclude_none=True,
)
def create_mock_message(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: dict[str, Any] = Body(...),
    session: Session = Depends(get_db),
) -> MessageCreateResult:
    if not request.app.state.settings.mock_api_available:
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        result = process_plain_message(payload, "mock", session)
        schedule_image_recognition_if_enabled(
            background_tasks, request, result
        )
        return result
    except MessageNormalizationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except SQLAlchemyError:
        logger.exception("mock message database persistence failed")
        raise HTTPException(status_code=500, detail="消息数据库保存失败") from None


@router.post(
    "/mock-image-message",
    response_model=MessageCreateResult,
    response_model_exclude_none=True,
)
def create_mock_image_message(
    request: Request,
    request_data: MockImageMessageRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_db),
) -> MessageCreateResult:
    if not request.app.state.settings.mock_api_available:
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        image_bytes = base64.b64decode(
            request_data.image_base64, validate=True
        )
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail="image_base64 不是有效 Base64") from None
    if len(image_bytes) > request.app.state.settings.image_max_bytes:
        raise HTTPException(status_code=413, detail="模拟图片大小超出允许范围")
    try:
        image = validate_image_bytes(image_bytes)
    except ImageRecognitionClientError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if image.media_type != request_data.content_type:
        raise HTTPException(status_code=422, detail="图片内容与 content_type 不一致")

    extension = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
    }[image.media_type]
    identifier = uuid4().hex
    filename = f"{identifier}.{extension}"
    image_directory = request.app.state.settings.message_data_dir / "dev-images"
    image_directory.mkdir(parents=True, exist_ok=True)
    local_path = image_directory / filename
    try:
        local_path.write_bytes(image.data)
    except OSError:
        raise HTTPException(status_code=500, detail="模拟图片保存失败") from None

    msgid = f"dev-image-{identifier}"
    sender = {"userid": request_data.sender_userid}
    if request_data.sender_name:
        sender["name"] = request_data.sender_name
    payload = {
        "msgid": msgid,
        "aibotid": "dev-chat-bot",
        "chatid": request_data.chatid,
        "chattype": "group",
        "from": sender,
        "msgtype": "image",
        "image": {"url": f"/api/dev/images/{filename}"},
    }
    if request_data.received_at is not None:
        payload["received_at"] = request_data.received_at.isoformat()
    try:
        result = process_plain_message(payload, "mock", session)
        message = session.get(Message, result.database_id)
        if message is None or not message.attachments:
            raise RuntimeError("模拟图片消息附件未保存")
        attachment = message.attachments[0]
        attachment.local_path = str(local_path.resolve())
        attachment.download_status = "downloaded"
        session.commit()
        schedule_image_recognition_if_enabled(
            background_tasks, request, result
        )
        return result
    except MessageNormalizationError as exc:
        session.rollback()
        local_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except Exception as exc:
        session.rollback()
        local_path.unlink(missing_ok=True)
        logger.error(
            "mock image persistence failed error_type=%s", type(exc).__name__
        )
        raise HTTPException(status_code=500, detail="模拟图片消息保存失败") from None


@router.get("/images/{filename}", include_in_schema=False)
def get_mock_image(request: Request, filename: str) -> FileResponse:
    if not request.app.state.settings.mock_api_available:
        raise HTTPException(status_code=404, detail="Not Found")
    if not DEV_IMAGE_PATTERN.fullmatch(filename):
        raise HTTPException(status_code=404, detail="模拟图片不存在")
    path = request.app.state.settings.message_data_dir / "dev-images" / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="模拟图片不存在")
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".webp": "image/webp",
    }[path.suffix.lower()]
    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/mock-trigger-message",
    response_model=MockTriggerMessageResponse,
)
def create_mock_trigger_message(
    request: Request,
    request_data: MockTriggerMessageRequest,
    session: Session = Depends(get_db),
) -> MockTriggerMessageResponse:
    if not request.app.state.settings.mock_api_available:
        raise HTTPException(status_code=404, detail="Not Found")

    summary = session.get(DailyReportSummary, request_data.summary_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="日报汇总不存在")
    if summary.chatid != request_data.chatid:
        raise HTTPException(
            status_code=409,
            detail="所选群聊与汇总快照不一致，请重新选择对应群聊",
        )

    trigger_msgid = f"dev-trigger-{uuid4().hex}"
    payload = {
        "msgid": trigger_msgid,
        "aibotid": "dev-chat-bot",
        "chatid": request_data.chatid,
        "chattype": "group",
        "from": {"userid": "dev-mock-operator", "name": "本地验收"},
        "msgtype": "text",
        "text": {
            "content": f"本地模拟发送触发消息（汇总快照 {request_data.summary_id}）"
        },
        "response_url": f"mock://response-url/{uuid4().hex}",
    }
    try:
        result = process_plain_message(payload, "mock", session)
    except MessageNormalizationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except SQLAlchemyError:
        logger.exception(
            "mock trigger database persistence failed summary_id=%s",
            request_data.summary_id,
        )
        raise HTTPException(
            status_code=500, detail="模拟触发消息保存失败"
        ) from None
    if result.duplicate:
        raise HTTPException(status_code=500, detail="模拟触发消息编号生成冲突")
    return MockTriggerMessageResponse(
        status="saved",
        trigger_msgid=trigger_msgid,
        chatid=request_data.chatid,
        summary_id=request_data.summary_id,
    )
