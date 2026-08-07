"""仅供本地验收使用的聊天式开发页面。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse


router = APIRouter(tags=["开发验收页面"])
ASSET_DIR = Path(__file__).resolve().parent.parent / "dev_chat"
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


@router.get("/dev/chat", include_in_schema=False)
def dev_chat_page() -> FileResponse:
    return FileResponse(
        ASSET_DIR / "index.html",
        media_type="text/html; charset=utf-8",
        headers=SECURITY_HEADERS,
    )


@router.get("/dev/chat.css", include_in_schema=False)
def dev_chat_styles() -> FileResponse:
    return FileResponse(
        ASSET_DIR / "chat.css",
        media_type="text/css; charset=utf-8",
        headers=SECURITY_HEADERS,
    )


@router.get("/dev/chat.js", include_in_schema=False)
def dev_chat_script() -> FileResponse:
    return FileResponse(
        ASSET_DIR / "chat.js",
        media_type="text/javascript; charset=utf-8",
        headers=SECURITY_HEADERS,
    )
