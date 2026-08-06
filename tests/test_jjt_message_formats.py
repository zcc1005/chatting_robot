from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.message_normalizer import (
    MessageNormalizationError,
    normalize_jjt_message,
)
from tests.conftest import sqlite_url


@pytest.fixture
def format_client(tmp_path: Path):
    settings = Settings(
        app_env="development",
        enable_mock_api=True,
        enable_jjt_callback=False,
        database_url=sqlite_url(tmp_path / "formats.db"),
        message_data_dir=tmp_path / "messages",
    )
    with TestClient(create_app(settings)) as client:
        yield client


def test_document_group_text_format(load_message_fixture) -> None:
    payload = load_message_fixture("jjt_formats/group_text.json")
    normalized = normalize_jjt_message(payload, "jjt")
    assert normalized.chatid == "group-doc-001"
    assert normalized.text_content == "群聊施工进度正常"
    assert normalized.process_status == "received"


def test_document_group_mixed_format(load_message_fixture) -> None:
    normalized = normalize_jjt_message(
        load_message_fixture("jjt_formats/group_mixed.json"), "jjt"
    )
    assert normalized.text_content == "第一段文字\n第二段文字"
    assert [item.remote_url for item in normalized.attachments] == [
        "mock://doc-image-001",
        "mock://doc-image-002",
    ]


@pytest.mark.parametrize(
    ("fixture_name", "userid", "attachment_type"),
    [
        ("single_image.json", "user-single-image", "image"),
        ("single_file.json", "user-single-file", "file"),
    ],
)
def test_document_single_message_generates_internal_chatid(
    load_message_fixture, fixture_name: str, userid: str, attachment_type: str
) -> None:
    payload = load_message_fixture(f"jjt_formats/{fixture_name}")
    assert "chatid" not in payload
    normalized = normalize_jjt_message(payload, "jjt")
    assert normalized.chatid == f"single:{userid}"
    assert normalized.attachments[0].attachment_type == attachment_type


def test_group_message_without_chatid_is_rejected(load_message_fixture) -> None:
    payload = load_message_fixture("jjt_formats/group_missing_chatid.json")
    with pytest.raises(MessageNormalizationError, match="群聊.*chatid"):
        normalize_jjt_message(payload, "jjt")


def test_quoted_text_is_preserved_but_not_merged(load_message_fixture) -> None:
    payload = load_message_fixture("jjt_formats/quoted_text.json")
    normalized = normalize_jjt_message(payload, "jjt")
    assert normalized.text_content == "这是当前文本"
    assert normalized.attachments == []
    assert normalized.raw_payload["quote"] == payload["quote"]


def test_quoted_mixed_does_not_duplicate_quote_content_or_image(
    load_message_fixture,
) -> None:
    payload = load_message_fixture("jjt_formats/quoted_mixed.json")
    normalized = normalize_jjt_message(payload, "jjt")
    assert normalized.text_content == "当前 mixed 文本"
    assert [item.remote_url for item in normalized.attachments] == [
        "mock://current-mixed-image"
    ]
    assert "引用文本不得拼接" not in normalized.text_content
    assert normalized.raw_payload["quote"] == payload["quote"]


@pytest.mark.parametrize("fixture_name", ["voice.json", "unknown.json"])
def test_unsupported_format_preserves_raw_without_attachments(
    load_message_fixture, fixture_name: str
) -> None:
    payload = load_message_fixture(f"jjt_formats/{fixture_name}")
    normalized = normalize_jjt_message(payload, "jjt")
    assert normalized.process_status == "unsupported"
    assert normalized.text_content == ""
    assert normalized.attachments == []
    assert normalized.raw_payload == payload


@pytest.mark.parametrize("fixture_name", ["voice.json", "unknown.json"])
def test_mock_api_saves_unsupported_formats_without_500(
    format_client, load_message_fixture, fixture_name: str
) -> None:
    payload = load_message_fixture(f"jjt_formats/{fixture_name}")
    response = format_client.post("/api/dev/mock-message", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "saved"
    detail = format_client.get(f"/api/messages/{payload['msgid']}")
    assert detail.status_code == 200
    assert detail.json()["process_status"] == "unsupported"
    assert detail.json()["attachments"] == []
    assert detail.json()["raw_payload"] == payload


def test_mock_api_single_without_chatid_uses_generated_value(
    format_client, load_message_fixture
) -> None:
    payload = load_message_fixture("jjt_formats/single_image.json")
    response = format_client.post("/api/dev/mock-message", json=payload)
    detail = format_client.get(f"/api/messages/{payload['msgid']}")
    assert response.status_code == 200
    assert detail.json()["chatid"] == "single:user-single-image"


def test_mock_api_group_without_chatid_returns_422(
    format_client, load_message_fixture
) -> None:
    payload = load_message_fixture("jjt_formats/group_missing_chatid.json")
    response = format_client.post("/api/dev/mock-message", json=payload)
    assert response.status_code == 422
    assert "chatid" in response.json()["detail"]


def test_quote_is_complete_in_database_raw_json(
    format_client, load_message_fixture
) -> None:
    payload = load_message_fixture("jjt_formats/quoted_mixed.json")
    response = format_client.post("/api/dev/mock-message", json=payload)
    assert response.status_code == 200
    detail = format_client.get(f"/api/messages/{payload['msgid']}")
    assert detail.json()["raw_payload"]["quote"] == payload["quote"]
    assert len(detail.json()["attachments"]) == 1


def test_unsupported_filter_returns_voice_and_unknown(
    format_client, load_message_fixture
) -> None:
    for name in ("voice.json", "unknown.json"):
        payload = load_message_fixture(f"jjt_formats/{name}")
        format_client.post("/api/dev/mock-message", json=payload)
    response = format_client.get(
        "/api/messages", params={"process_status": "unsupported"}
    )
    assert response.status_code == 200
    assert {item["msgid"] for item in response.json()["items"]} == {
        "doc-voice-001",
        "doc-unknown-001",
    }
