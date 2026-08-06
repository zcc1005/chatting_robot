"""仅在离线开发环境开放的明文模拟消息接口。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.schemas import MessageCreateResult
from app.services.message_normalizer import MessageNormalizationError
from app.services.message_service import process_plain_message


router = APIRouter(prefix="/api/dev", tags=["开发模拟"])
logger = logging.getLogger(__name__)


@router.post("/mock-message", response_model=MessageCreateResult)
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
