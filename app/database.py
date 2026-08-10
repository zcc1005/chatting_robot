"""SQLAlchemy 2.x 同步数据库配置。"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

from fastapi import Request
from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


engine: Engine | None = None
SessionLocal = sessionmaker[Session](
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


@dataclass(frozen=True, slots=True)
class DatabaseRuntime:
    engine: Engine
    session_factory: sessionmaker[Session]


def configure_database(database_url: str) -> DatabaseRuntime:
    """创建并注册当前应用使用的 SQLite engine 与 session factory。"""
    global engine

    _ensure_sqlite_parent(database_url)
    engine_options: dict[str, object] = {
        "connect_args": {"check_same_thread": False},
    }
    if database_url in {"sqlite://", "sqlite:///:memory:"}:
        engine_options["poolclass"] = StaticPool

    new_engine = create_engine(database_url, **engine_options)

    @event.listens_for(new_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    engine = new_engine
    SessionLocal.configure(bind=new_engine)
    session_factory = sessionmaker[Session](
        bind=new_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    return DatabaseRuntime(engine=new_engine, session_factory=session_factory)


def init_db(runtime: DatabaseRuntime | None = None) -> None:
    """直接创建本阶段所需数据表。"""
    from app.models import database_models  # noqa: F401

    active_engine = runtime.engine if runtime is not None else engine
    if active_engine is None:
        raise RuntimeError("数据库尚未配置")
    _migrate_process_status_constraint(active_engine)
    Base.metadata.create_all(bind=active_engine)
    _migrate_daily_report_publication_columns(active_engine)
    _migrate_project_report_review_columns(active_engine)


def get_db(request: Request) -> Generator[Session, None, None]:
    session_factory: sessionmaker[Session] = request.app.state.session_factory
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def _ensure_sqlite_parent(database_url: str) -> None:
    url = make_url(database_url)
    database = url.database
    if not database or database == ":memory:":
        return
    path = Path(database)
    path.parent.mkdir(parents=True, exist_ok=True)


def _migrate_process_status_constraint(active_engine: Engine) -> None:
    """兼容上一阶段不含 unsupported 的 SQLite CHECK 约束。"""
    inspector = inspect(active_engine)
    if "messages" not in inspector.get_table_names():
        return
    constraints = inspector.get_check_constraints("messages")
    status_constraint = next(
        (item for item in constraints if item.get("name") == "ck_messages_process_status"),
        None,
    )
    if status_constraint is None or "unsupported" in str(status_constraint.get("sqltext")):
        return

    message_columns = (
        "id,msgid,source,aibotid,chatid,chattype,sender_userid,msgtype,"
        "text_content,response_url,raw_json,received_at,process_status,"
        "created_at,updated_at"
    )
    attachment_columns = (
        "id,message_id,attachment_type,remote_url,local_path,download_status,md5,created_at"
    )
    with active_engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            with connection.begin():
                old_indexes = inspector.get_indexes("messages") + inspector.get_indexes(
                    "message_attachments"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE message_attachments RENAME TO message_attachments_old"
                )
                connection.exec_driver_sql("ALTER TABLE messages RENAME TO messages_old")
                for index in old_indexes:
                    name = index.get("name")
                    if name:
                        connection.exec_driver_sql(f'DROP INDEX IF EXISTS "{name}"')
                Base.metadata.tables["messages"].create(connection)
                Base.metadata.tables["message_attachments"].create(connection)
                connection.exec_driver_sql(
                    f"INSERT INTO messages ({message_columns}) "
                    f"SELECT {message_columns} FROM messages_old"
                )
                connection.exec_driver_sql(
                    f"INSERT INTO message_attachments ({attachment_columns}) "
                    f"SELECT {attachment_columns} FROM message_attachments_old"
                )
                connection.exec_driver_sql("DROP TABLE message_attachments_old")
                connection.exec_driver_sql("DROP TABLE messages_old")
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


def _migrate_daily_report_publication_columns(active_engine: Engine) -> None:
    """为既有 SQLite 汇总表补充发布状态字段，不覆盖历史快照。"""
    inspector = inspect(active_engine)
    if "daily_report_summaries" not in inspector.get_table_names():
        return

    existing = {
        column["name"]
        for column in inspector.get_columns("daily_report_summaries")
    }
    additions = {
        "publication_status": "VARCHAR(32) NOT NULL DEFAULT 'draft'",
        "confirmed_by": "VARCHAR(255)",
        "confirmed_at": "DATETIME",
        "confirmation_note": "TEXT",
        "sent_at": "DATETIME",
    }
    with active_engine.begin() as connection:
        for name, sql_type in additions.items():
            if name not in existing:
                connection.exec_driver_sql(
                    f'ALTER TABLE daily_report_summaries ADD COLUMN "{name}" {sql_type}'
                )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_daily_summaries_publication_status "
            "ON daily_report_summaries (publication_status)"
        )


def _migrate_project_report_review_columns(active_engine: Engine) -> None:
    """为既有结构化日报补充相关性、日期来源和规范化审计字段。"""
    inspector = inspect(active_engine)
    if "project_reports" not in inspector.get_table_names():
        return

    existing = {
        column["name"] for column in inspector.get_columns("project_reports")
    }
    additions = {
        "relevance_status": "VARCHAR(32) NOT NULL DEFAULT 'not_reviewed'",
        "relevance_reason": "TEXT",
        "relevance_confidence": "FLOAT",
        "date_source": "VARCHAR(64) NOT NULL DEFAULT 'missing'",
        "normalization_warnings": "TEXT NOT NULL DEFAULT '[]'",
    }
    with active_engine.begin() as connection:
        for name, sql_type in additions.items():
            if name not in existing:
                connection.exec_driver_sql(
                    f'ALTER TABLE project_reports ADD COLUMN "{name}" {sql_type}'
                )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_project_reports_relevance_status "
            "ON project_reports (relevance_status)"
        )
