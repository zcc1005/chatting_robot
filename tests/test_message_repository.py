from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import configure_database, init_db
from app.models.database_models import Message, MessageAttachment
from app.repositories import message_repository
from app.repositories.message_repository import list_messages
from app.services.message_service import process_plain_message
from tests.conftest import sqlite_url


@pytest.fixture
def db_session(tmp_path: Path):
    runtime = configure_database(sqlite_url(tmp_path / "repository.db"))
    init_db(runtime)
    with runtime.session_factory() as session:
        yield session
    runtime.engine.dispose()


def test_save_message_and_database_fields(
    db_session: Session, load_message_fixture
) -> None:
    result = process_plain_message(
        load_message_fixture("text_message.json"), "mock", db_session
    )
    record = message_repository.get_by_msgid(db_session, result.msgid)
    assert result.status == "saved"
    assert result.duplicate is False
    assert record is not None
    assert record.source == "mock"
    assert "兴城项目" in record.text_content
    assert "response_url" in record.raw_json
    assert record.received_at.tzinfo is not None
    assert record.created_at.tzinfo is not None


def test_duplicate_msgid_returns_existing_database_row(
    db_session: Session, load_message_fixture
) -> None:
    payload = load_message_fixture("text_message.json")
    first = process_plain_message(payload, "mock", db_session)
    second = process_plain_message(payload, "mock", db_session)
    count = db_session.scalar(select(func.count()).select_from(Message))
    assert first.status == "saved"
    assert second.status == "ignored"
    assert second.duplicate is True
    assert second.database_id == first.database_id
    assert count == 1


def test_mixed_attachments_are_saved_in_same_message(
    db_session: Session, load_message_fixture
) -> None:
    result = process_plain_message(
        load_message_fixture("mixed_message.json"), "mock", db_session
    )
    record = message_repository.get_message_detail(db_session, result.msgid)
    assert record is not None
    assert len(record.attachments) == 2
    assert [item.remote_url for item in record.attachments] == [
        "mock://image-001",
        "mock://image-002",
    ]


def test_message_and_attachments_rollback_together(
    db_session: Session, load_message_fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_attachments(*_args, **_kwargs):
        raise RuntimeError("simulated attachment failure")

    monkeypatch.setattr(message_repository, "create_attachments", fail_attachments)
    with pytest.raises(RuntimeError, match="attachment failure"):
        process_plain_message(
            load_message_fixture("mixed_message.json"), "mock", db_session
        )
    message_count = db_session.scalar(select(func.count()).select_from(Message))
    attachment_count = db_session.scalar(
        select(func.count()).select_from(MessageAttachment)
    )
    assert message_count == 0
    assert attachment_count == 0


def test_list_messages_filters_chatid_and_msgtype(
    db_session: Session, load_message_fixture
) -> None:
    text_payload = load_message_fixture("text_message.json")
    image_payload = load_message_fixture("image_message.json")
    image_payload["chatid"] = "another-group"
    process_plain_message(text_payload, "mock", db_session)
    process_plain_message(image_payload, "mock", db_session)
    assert [item.msgid for item in list_messages(db_session, chatid="construction-group-001")] == [
        "mock-text-001"
    ]
    assert [item.msgid for item in list_messages(db_session, msgtype="image")] == [
        "mock-image-001"
    ]


def test_list_messages_filters_time_range(
    db_session: Session, load_message_fixture
) -> None:
    timezone = ZoneInfo("Asia/Shanghai")
    old_payload = load_message_fixture("text_message.json")
    old_payload["msgid"] = "old-message"
    old_payload["received_at"] = "2026-08-04T09:00:00+08:00"
    new_payload = load_message_fixture("text_message.json")
    new_payload["msgid"] = "new-message"
    new_payload["received_at"] = "2026-08-05T09:00:00+08:00"
    process_plain_message(old_payload, "mock", db_session)
    process_plain_message(new_payload, "mock", db_session)
    start = datetime(2026, 8, 5, 0, 0, tzinfo=timezone)
    end = start + timedelta(days=1)
    results = list_messages(db_session, start_time=start, end_time=end)
    assert [item.msgid for item in results] == ["new-message"]

