"""图片消息入库后的可选后台识别任务。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import BackgroundTasks, Request

from app.models.schemas import MessageCreateResult
from app.repositories.message_repository import get_message_detail
from app.services.image_recognition_service import (
    ImageMessageNotEligibleError,
    recognize_message_images,
)


logger = logging.getLogger(__name__)


def schedule_image_recognition_if_enabled(
    background_tasks: BackgroundTasks,
    request: Request,
    result: MessageCreateResult,
) -> None:
    settings = request.app.state.settings
    if (
        result.duplicate
        or not settings.enable_auto_image_recognition
        or request.app.state.image_recognition_client is None
    ):
        return
    background_tasks.add_task(
        _run_image_recognition,
        request.app,
        result.msgid,
    )


def _run_image_recognition(app: Any, msgid: str) -> None:
    with app.state.session_factory() as session:
        message = get_message_detail(session, msgid)
        if message is None:
            logger.warning("background image recognition message missing msgid=%s", msgid)
            return
        try:
            recognize_message_images(
                session,
                message,
                loader=app.state.image_content_loader,
                client=app.state.image_recognition_client,
                timezone=app.state.settings.timezone,
            )
        except ImageMessageNotEligibleError:
            return
        except Exception as exc:
            session.rollback()
            logger.error(
                "background image recognition failed msgid=%s message_id=%s "
                "error_type=%s",
                message.msgid,
                message.id,
                type(exc).__name__,
            )
