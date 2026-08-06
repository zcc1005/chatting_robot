from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import sqlite_url


@pytest.fixture
def mock_client(tmp_path: Path):
    settings = Settings(
        app_env="development",
        enable_mock_api=True,
        enable_jjt_callback=False,
        database_url=sqlite_url(tmp_path / "mock-api.db"),
        message_data_dir=tmp_path / "messages",
    )
    with TestClient(create_app(settings)) as client:
        yield client


def test_offline_app_starts_without_callback_secrets(mock_client) -> None:
    response = mock_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "jjt-daily-report-bot"}


def test_mock_text_first_saved_then_duplicate_ignored(
    mock_client, load_message_fixture
) -> None:
    payload = load_message_fixture("text_message.json")
    first = mock_client.post("/api/dev/mock-message", json=payload)
    second = mock_client.post("/api/dev/mock-message", json=payload)
    assert first.status_code == 200
    assert first.json()["status"] == "saved"
    assert first.json()["duplicate"] is False
    assert first.json()["database_id"] is not None
    assert second.status_code == 200
    assert second.json() == {
        "status": "ignored",
        "msgid": "mock-text-001",
        "duplicate": True,
        "database_id": first.json()["database_id"],
    }


def test_list_and_detail_api(mock_client, load_message_fixture) -> None:
    payload = load_message_fixture("text_message.json")
    mock_client.post("/api/dev/mock-message", json=payload)

    listing = mock_client.get(
        "/api/messages", params={"chatid": "construction-group-001"}
    )
    assert listing.status_code == 200
    assert listing.json()["limit"] == 100
    assert listing.json()["offset"] == 0
    assert listing.json()["items"][0]["msgid"] == "mock-text-001"
    assert listing.json()["items"][0]["attachment_count"] == 0
    assert "raw_payload" not in listing.json()["items"][0]
    assert "response_url" not in listing.json()["items"][0]

    detail = mock_client.get("/api/messages/mock-text-001")
    assert detail.status_code == 200
    body = detail.json()
    expected_raw_payload = dict(payload)
    expected_raw_payload["response_url"] = "https://example.invalid/response/…"
    assert body["raw_payload"] == expected_raw_payload
    assert body["response_url"] == "https://example.invalid/response/…"
    assert body["attachments"] == []


def test_mixed_api_returns_attachment_metadata(mock_client, load_message_fixture) -> None:
    payload = load_message_fixture("mixed_message.json")
    created = mock_client.post("/api/dev/mock-message", json=payload)
    detail = mock_client.get(f"/api/messages/{created.json()['msgid']}")
    assert created.status_code == 200
    assert detail.status_code == 200
    assert len(detail.json()["attachments"]) == 2
    assert all(
        item["download_status"] == "mock"
        for item in detail.json()["attachments"]
    )


def test_missing_message_returns_404(mock_client) -> None:
    response = mock_client.get("/api/messages/does-not-exist")
    assert response.status_code == 404


def test_mock_validation_error_is_422(mock_client, load_message_fixture) -> None:
    payload = load_message_fixture("text_message.json")
    payload.pop("msgid")
    response = mock_client.post("/api/dev/mock-message", json=payload)
    assert response.status_code == 422
    assert "msgid" in response.json()["detail"]


def test_production_does_not_register_mock_api(tmp_path: Path, load_message_fixture) -> None:
    settings = Settings(
        app_env="production",
        enable_mock_api=True,
        enable_jjt_callback=False,
        database_url=sqlite_url(tmp_path / "production.db"),
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/dev/mock-message", json=load_message_fixture("text_message.json")
        )
    assert response.status_code == 404


def test_callback_route_absent_when_disabled(mock_client) -> None:
    assert mock_client.get("/api/jjt/callback").status_code == 404
