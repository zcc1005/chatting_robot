"""仅在离线开发环境开放的明文模拟消息接口。"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.database_models import DailyReportSummary
from app.models.schemas import (
    MessageCreateResult,
    MockTriggerMessageRequest,
    MockTriggerMessageResponse,
)
from app.services.message_normalizer import MessageNormalizationError
from app.services.message_service import process_plain_message


router = APIRouter(prefix="/api/dev", tags=["开发模拟"])
logger = logging.getLogger(__name__)


@router.post(
    "/mock-message",
    response_model=MessageCreateResult,
    response_model_exclude_none=True,
)
def create_mock_message(
    request: Request,
    payload: dict[str, Any] = Body(...),
    session: Session = Depends(get_db),
) -> MessageCreateResult:
    if not request.app.state.settings.mock_api_available:
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        return process_plain_message(payload, "mock", session)
    except MessageNormalizationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except SQLAlchemyError:
        logger.exception("mock message database persistence failed")
        raise HTTPException(status_code=500, detail="消息数据库保存失败") from None


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
