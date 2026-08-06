from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import inspect

from app.database import configure_database, init_db
from app.services.message_service import process_plain_message
from tests.conftest import sqlite_url


def test_old_process_status_constraint_is_migrated_without_data_loss(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "old-schema.db"
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                msgid VARCHAR(255) NOT NULL,
                source VARCHAR(32) NOT NULL,
                aibotid VARCHAR(255),
                chatid VARCHAR(255) NOT NULL,
                chattype VARCHAR(32) NOT NULL,
                sender_userid VARCHAR(255) NOT NULL,
                msgtype VARCHAR(64) NOT NULL,
                text_content TEXT NOT NULL,
                response_url TEXT,
                raw_json TEXT NOT NULL,
                received_at DATETIME NOT NULL,
                process_status VARCHAR(32) NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                CONSTRAINT uq_messages_msgid UNIQUE (msgid),
                CONSTRAINT ck_messages_process_status
                    CHECK (process_status IN ('received', 'ignored', 'failed'))
            );
            CREATE TABLE message_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                attachment_type VARCHAR(32) NOT NULL,
                remote_url TEXT NOT NULL,
                local_path TEXT,
                download_status VARCHAR(32) NOT NULL,
                md5 VARCHAR(64),
                created_at DATETIME NOT NULL,
                CONSTRAINT ck_attachments_type
                    CHECK (attachment_type IN ('image', 'file'))
            );
            INSERT INTO messages (
                msgid,source,chatid,chattype,sender_userid,msgtype,text_content,
                raw_json,received_at,process_status,created_at,updated_at
            ) VALUES (
                'old-message','mock','old-group','group','old-user','text','old',
                '{}','2026-08-05 00:00:00','received',
                '2026-08-05 00:00:00','2026-08-05 00:00:00'
            );
            """
        )
        connection.commit()
    finally:
        connection.close()

    runtime = configure_database(sqlite_url(database_path))
    try:
        init_db(runtime)
        constraints = inspect(runtime.engine).get_check_constraints("messages")
        status_sql = next(
            item["sqltext"]
            for item in constraints
            if item["name"] == "ck_messages_process_status"
        )
        assert "unsupported" in status_sql

        with runtime.session_factory() as session:
            result = process_plain_message(
                {
                    "msgid": "new-voice",
                    "chatid": "voice-group",
                    "chattype": "group",
                    "from": {"userid": "voice-user"},
                    "msgtype": "voice",
                    "voice": {"url": "mock://not-downloaded"},
                },
                "mock",
                session,
            )
        assert result.status == "saved"

        check_connection = sqlite3.connect(database_path)
        try:
            rows = dict(
                check_connection.execute(
                    "SELECT msgid, process_status FROM messages ORDER BY id"
                ).fetchall()
            )
        finally:
            check_connection.close()
        assert rows == {"old-message": "received", "new-voice": "unsupported"}
    finally:
        runtime.engine.dispose()

