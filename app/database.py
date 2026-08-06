"""SQLAlchemy 2.x 同步数据库配置。"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
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
    Base.metadata.create_all(bind=active_engine)


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
