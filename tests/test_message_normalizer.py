from __future__ import annotations

import copy

import pytest

from app.services.message_normalizer import (
    MessageNormalizationError,
    normalize_jjt_message,
)


def test_text_message_extracts_text_content(load_message_fixture) -> None:
    payload = load_message_fixture("text_message.json")
    normalized = normalize_jjt_message(payload, "mock")
    assert normalized.text_content == payload["text"]["content"]
    assert normalized.attachments == []


def test_image_message_creates_mock_image_attachment(load_message_fixture) -> None:
    normalized = normalize_jjt_message(
        load_message_fixture("image_message.json"), "mock"
    )
    assert len(normalized.attachments) == 1
    attachment = normalized.attachments[0]
    assert attachment.attachment_type == "image"
    assert attachment.remote_url == "mock://image-001"
    assert attachment.download_status == "mock"
    assert attachment.md5 == "image-md5-001"


def test_non_mock_attachment_is_pending(load_message_fixture) -> None:
    payload = load_message_fixture("image_message.json")
    payload["image"]["url"] = "https://example.invalid/image?id=1"
    normalized = normalize_jjt_message(payload, "jjt")
    assert normalized.attachments[0].download_status == "pending"


def test_mixed_message_combines_text_in_original_order(load_message_fixture) -> None:
    normalized = normalize_jjt_message(
        load_message_fixture("mixed_message.json"), "mock"
    )
    assert normalized.text_content == (
        "兴城项目施工日报\n今日完成1号楼基础钢筋绑扎。"
    )


def test_mixed_message_creates_multiple_attachments(load_message_fixture) -> None:
    normalized = normalize_jjt_message(
        load_message_fixture("mixed_message.json"), "mock"
    )
    assert [item.remote_url for item in normalized.attachments] == [
        "mock://image-001",
        "mock://image-002",
    ]
    assert all(item.attachment_type == "image" for item in normalized.attachments)


def test_file_message_creates_file_attachment(load_message_fixture) -> None:
    normalized = normalize_jjt_message(
        load_message_fixture("file_message.json"), "mock"
    )
    assert normalized.attachments[0].attachment_type == "file"
    assert normalized.attachments[0].remote_url == "mock://file-001"
    assert normalized.attachments[0].download_status == "mock"


def test_missing_msgid_raises_clear_error(load_message_fixture) -> None:
    payload = load_message_fixture("text_message.json")
    payload.pop("msgid")
    with pytest.raises(MessageNormalizationError, match="msgid"):
        normalize_jjt_message(payload, "mock")


def test_missing_sender_userid_raises_clear_error(load_message_fixture) -> None:
    payload = load_message_fixture("text_message.json")
    payload["from"] = {}
    with pytest.raises(MessageNormalizationError, match="from.userid"):
        normalize_jjt_message(payload, "mock")


def test_raw_payload_is_fully_preserved(load_message_fixture) -> None:
    payload = load_message_fixture("mixed_message.json")
    original = copy.deepcopy(payload)
    normalized = normalize_jjt_message(payload, "mock")
    assert normalized.raw_payload == original
    payload["mixed"]["msg_item"][0]["text"]["content"] = "changed"
    assert normalized.raw_payload == original


def test_received_at_has_timezone(load_message_fixture) -> None:
    normalized = normalize_jjt_message(
        load_message_fixture("text_message.json"), "mock"
    )
    assert normalized.received_at.tzinfo is not None
    assert normalized.received_at.utcoffset() is not None


def test_received_at_uses_reliable_timestamp(load_message_fixture) -> None:
    payload = load_message_fixture("text_message.json")
    payload["timestamp"] = 1785924600
    normalized = normalize_jjt_message(payload, "mock")
    assert int(normalized.received_at.timestamp()) == 1785924600

