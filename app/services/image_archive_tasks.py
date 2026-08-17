"""独立于视觉识别保存消息气泡中的原始施工图片。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Request

from app.models.schemas import MessageCreateResult
from app.repositories.message_repository import get_message_detail


logger = logging.getLogger(__name__)
_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def schedule_image_archive(
    background_tasks: BackgroundTasks,
    request: Request,
    result: MessageCreateResult,
) -> None:
    if result.duplicate or not result.msgid:
        return
    background_tasks.add_task(_archive_message_images, request.app, result.msgid)


def _archive_message_images(app: Any, msgid: str) -> None:
    with app.state.session_factory() as session:
        message = get_message_detail(session, msgid)
        if message is None:
            return
        image_attachments = [
            item
            for item in message.attachments
            if item.attachment_type == "image" and not item.local_path
        ]
        if not image_attachments:
            return
        target_dir = Path(app.state.settings.message_data_dir) / "attachments"
        target_dir.mkdir(parents=True, exist_ok=True)
        for attachment in image_attachments:
            try:
                image = app.state.image_content_loader.load(attachment)
                extension = _EXTENSIONS[image.media_type]
                target = target_dir / f"{attachment.id}-{image.sha256}{extension}"
                if not target.exists():
                    target.write_bytes(image.data)
                attachment.local_path = str(target.resolve())
                attachment.download_status = "downloaded"
            except Exception as exc:
                attachment.download_status = "failed"
                logger.warning(
                    "image archive failed msgid=%s attachment_id=%s error_type=%s",
                    msgid,
                    attachment.id,
                    type(exc).__name__,
                )
        session.commit()
