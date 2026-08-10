from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.database import configure_database, init_db
from app.main import create_app
from app.models.database_models import Message, ProjectReport
from tests.conftest import sqlite_url


def extraction_payload(**overrides) -> dict[str, object]:
    payload: dict[str, object] = {
        "project_name": "武清电商园项目",
        "report_date": "2026-08-10",
        "weather": "晴",
        "management_count": 3,
        "worker_count": 81,
        "equipment": [{"name": "塔吊", "count": 2, "unit": "台"}],
        "work_items": [
            {"location": "4区", "content": "防水保护层混凝土养护", "progress": None}
        ],
        "tomorrow_plan": None,
        "safety_status": None,
        "quality_status": None,
        "missing_fields": ["tomorrow_plan", "safety_status", "quality_status"],
        "confidence": 0.9,
        "relevance_status": "report",
        "relevance_reason": "包含项目、日期和施工内容",
        "relevance_confidence": 0.98,
    }
    payload.update(overrides)
    return payload


def seed_historical_report(
    database_path: Path,
    *,
    msgid: str,
    text_content: str,
    raw_response: str,
) -> None:
    runtime = configure_database(sqlite_url(database_path))
    init_db(runtime)
    received_at = datetime(2026, 8, 10, 2, 0, tzinfo=timezone.utc)
    with runtime.session_factory() as session:
        message = Message(
            msgid=msgid,
            source="mock",
            aibotid="dev-chat-bot",
            chatid="construction-group-001",
            chattype="group",
            sender_userid="builder-zhang",
            msgtype="text",
            text_content=text_content,
            raw_json="{}",
            received_at=received_at,
            process_status="received",
        )
        session.add(message)
        session.flush()
        session.add(
            ProjectReport(
                msgid=msgid,
                message_id=message.id,
                missing_fields="[]",
                extraction_status="failed",
                extraction_source="llm",
                raw_extraction_json=raw_response,
                error_message="历史版本字段校验失败",
            )
        )
        session.commit()
    runtime.engine.dispose()


def repaired_report(database_path: Path, msgid: str) -> dict[str, object]:
    settings = Settings(
        app_env="development",
        enable_mock_api=True,
        enable_jjt_callback=False,
        database_url=sqlite_url(database_path),
        message_data_dir=database_path.parent / "messages",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/project-reports/{msgid}")
        assert response.status_code == 200
        return response.json()


def test_mock_startup_repairs_month_day_only_without_llm(tmp_path: Path) -> None:
    database_path = tmp_path / "month-day-repair.db"
    raw_payload = extraction_payload(
        report_date=None,
        missing_fields=[
            "report_date",
            "tomorrow_plan",
            "safety_status",
            "quality_status",
        ],
    )
    raw_response = json.dumps(raw_payload, ensure_ascii=False)
    seed_historical_report(
        database_path,
        msgid="historical-wuqing",
        text_content=(
            "武清电商园项目8月10日施工情况，天气晴，施工人员81人，"
            "塔吊2台，施工内容：4区防水保护层混凝土养护。"
        ),
        raw_response=raw_response,
    )

    report = repaired_report(database_path, "historical-wuqing")

    assert report["extraction_status"] == "completed"
    assert report["report_date"] == "2026-08-10"
    assert report["date_source"] == "text_month_day_message_year"
    assert "report_date" not in report["missing_fields"]
    assert report["raw_extraction_json"] == raw_response


def test_mock_startup_repairs_one_empty_work_item(tmp_path: Path) -> None:
    database_path = tmp_path / "empty-work-item-repair.db"
    raw_payload = extraction_payload(
        project_name="伊拉克米桑医院项目",
        work_items=[
            {"location": "4区", "content": "防水保护层混凝土养护", "progress": None},
            {"location": "6区", "content": None, "progress": None},
        ],
    )
    raw_response = json.dumps(raw_payload, ensure_ascii=False)
    seed_historical_report(
        database_path,
        msgid="historical-maysan",
        text_content="伊拉克米桑医院项目2026年8月4日施工情况，施工内容包括混凝土养护。",
        raw_response=raw_response,
    )

    report = repaired_report(database_path, "historical-maysan")

    assert report["extraction_status"] == "completed"
    assert len(report["work_items"]) == 1
    assert report["work_items"][0]["content"] == "防水保护层混凝土养护"
    assert len(report["normalization_warnings"]) == 1
    assert "1 项施工子项" in report["normalization_warnings"][0]
    assert report["raw_extraction_json"] == raw_response


@pytest.mark.parametrize(
    "raw_response",
    [
        "not-json",
        json.dumps(extraction_payload(worker_count="81"), ensure_ascii=False),
    ],
)
def test_mock_startup_leaves_unrecoverable_history_failed(
    tmp_path: Path, raw_response: str
) -> None:
    database_path = tmp_path / f"unrecoverable-{abs(hash(raw_response))}.db"
    seed_historical_report(
        database_path,
        msgid="historical-invalid",
        text_content="武清电商园项目8月10日施工情况，施工人员81人。",
        raw_response=raw_response,
    )

    report = repaired_report(database_path, "historical-invalid")

    assert report["extraction_status"] == "failed"
    assert report["error_message"] == "历史版本字段校验失败"
    assert report["raw_extraction_json"] == raw_response
