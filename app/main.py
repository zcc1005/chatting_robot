"""FastAPI 应用入口。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.api.callback import router as callback_router
from app.api.messages import router as messages_router
from app.api.mock import router as mock_router
from app.config import Settings
from app.database import configure_database, init_db
from app.logging_config import configure_logging
from app.services.crypto_service import JJTCryptoService
from app.services.message_storage import MessageStorage


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or Settings.from_env()
    active_settings.validate()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(active_settings.log_level)
        database_runtime = configure_database(active_settings.database_url)
        init_db(database_runtime)
        app.state.settings = active_settings
        app.state.database_engine = database_runtime.engine
        app.state.session_factory = database_runtime.session_factory

        if active_settings.enable_jjt_callback:
            app.state.crypto = JJTCryptoService(
                active_settings.callback_token,
                active_settings.encoding_aes_key,
                active_settings.receive_id,
            )
            app.state.storage = MessageStorage(
                active_settings.message_data_dir,
                active_settings.timezone,
            )
        try:
            yield
        finally:
            database_runtime.engine.dispose()

    application = FastAPI(
        title="交建通施工日报机器人",
        version="0.2.0",
        lifespan=lifespan,
    )

    @application.get("/health", tags=["系统"])
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "jjt-daily-report-bot"}

    application.include_router(messages_router)
    if active_settings.mock_api_available:
        application.include_router(mock_router)
    if active_settings.enable_jjt_callback:
        application.include_router(callback_router)
    return application


app = create_app()
