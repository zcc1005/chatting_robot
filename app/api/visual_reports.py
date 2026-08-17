"""通过不可猜测链接访问日报 HTML 和该快照内的项目图片。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.database_models import DailyReportSummary, MessageAttachment
from app.models.daily_report_schemas import DailyReportPreviewResponse
from app.services.visual_report_service import render_visual_report


router = APIRouter(prefix="/visual-reports", tags=["可视化日报"])


@router.get("/{token}", response_class=HTMLResponse, include_in_schema=False)
def visual_report(token: str, session: Session = Depends(get_db)) -> HTMLResponse:
    summary, preview = _get_preview(session, token)
    del summary
    response = HTMLResponse(render_visual_report(preview, public_token=token))
    _secure_headers(response)
    return response


@router.get(
    "/{token}/images/{attachment_id}",
    response_class=Response,
    include_in_schema=False,
)
def visual_report_image(
    token: str,
    attachment_id: int,
    request: Request,
    session: Session = Depends(get_db),
) -> Response:
    _, preview = _get_preview(session, token)
    allowed = {
        image.attachment_id
        for project in preview.projects
        for image in project.images
    }
    if attachment_id not in allowed:
        raise HTTPException(status_code=404, detail="图片不存在")
    attachment = session.scalar(
        select(MessageAttachment).where(
            MessageAttachment.id == attachment_id,
            MessageAttachment.attachment_type == "image",
        )
    )
    if attachment is None:
        raise HTTPException(status_code=404, detail="图片不存在")
    if attachment.local_path:
        local = _safe_local_path(
            Path(attachment.local_path),
            Path(request.app.state.settings.message_data_dir),
        )
        if local is not None and local.is_file():
            response = FileResponse(local)
            _secure_headers(response)
            return response
    try:
        image = request.app.state.image_content_loader.load(attachment)
    except Exception:
        raise HTTPException(status_code=404, detail="图片暂时无法读取") from None
    response = Response(image.data, media_type=image.media_type)
    _secure_headers(response)
    return response


def _get_preview(
    session: Session, token: str
) -> tuple[DailyReportSummary, DailyReportPreviewResponse]:
    if not 32 <= len(token) <= 128:
        raise HTTPException(status_code=404, detail="日报不存在")
    summary = session.scalar(
        select(DailyReportSummary).where(DailyReportSummary.public_token == token)
    )
    if summary is None:
        raise HTTPException(status_code=404, detail="日报不存在")
    try:
        preview = DailyReportPreviewResponse.model_validate_json(
            summary.snapshot_json
        )
    except ValidationError:
        raise HTTPException(status_code=500, detail="日报快照损坏") from None
    return summary, preview


def _safe_local_path(path: Path, root: Path) -> Path | None:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return None
    return resolved


def _secure_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "private, max-age=300"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:"
    )
