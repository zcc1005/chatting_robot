"""FastAPI 应用入口。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.api.callback import router as callback_router
from app.api.daily_reports import router as daily_reports_router
from app.api.messages import router as messages_router
from app.api.mock import router as mock_router
from app.api.project_reports import router as project_reports_router
from app.api.report_detections import router as report_detections_router
from app.config import Settings
from app.database import configure_database, init_db
from app.logging_config import configure_logging
from app.services.crypto_service import JJTCryptoService
from app.services.message_storage import MessageStorage
from app.services.llm_extraction_client import (
    OpenAICompatibleExtractionClient,
    ReportExtractionClient,
)


def create_app(
    settings: Settings | None = None,
    report_extraction_client: ReportExtractionClient | None = None,
) -> FastAPI:
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
        if report_extraction_client is not None:
            app.state.report_extraction_client = report_extraction_client
        elif active_settings.llm_configured:
            app.state.report_extraction_client = OpenAICompatibleExtractionClient(
                api_key=active_settings.llm_api_key,
                model=active_settings.llm_model,
                base_url=active_settings.llm_base_url,
                timeout_seconds=active_settings.llm_timeout_seconds,
            )
        else:
            app.state.report_extraction_client = None

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
    application.include_router(report_detections_router)
    application.include_router(project_reports_router)
    application.include_router(daily_reports_router)
    if active_settings.mock_api_available:
        application.include_router(mock_router)
    if active_settings.enable_jjt_callback:
        application.include_router(callback_router)
    return application


app = create_app()
