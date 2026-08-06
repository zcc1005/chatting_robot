"""消息与附件的 SQLAlchemy ORM 模型。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AwareDateTime(TypeDecorator[datetime]):
    """为 SQLite 补回 UTC 时区信息。"""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, _dialect: object
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("数据库时间必须包含时区")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(
        self, value: datetime | None, _dialect: object
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("msgid", name="uq_messages_msgid"),
        CheckConstraint(
            "process_status IN ('received', 'ignored', 'failed', 'unsupported')",
            name="ck_messages_process_status",
        ),
        Index("ix_messages_msgid", "msgid"),
        Index("ix_messages_chatid", "chatid"),
        Index("ix_messages_received_at", "received_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    msgid: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    aibotid: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chatid: Mapped[str] = mapped_column(String(255), nullable=False)
    chattype: Mapped[str] = mapped_column(String(32), nullable=False)
    sender_userid: Mapped[str] = mapped_column(String(255), nullable=False)
    msgtype: Mapped[str] = mapped_column(String(64), nullable=False)
    text_content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    response_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    process_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="received"
    )
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )

    attachments: Mapped[list["MessageAttachment"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    report_detection: Mapped["MessageReportDetection | None"] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )


class MessageAttachment(Base):
    __tablename__ = "message_attachments"
    __table_args__ = (
        CheckConstraint(
            "attachment_type IN ('image', 'file')",
            name="ck_attachments_type",
        ),
        Index("ix_message_attachments_message_id", "message_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    attachment_type: Mapped[str] = mapped_column(String(32), nullable=False)
    remote_url: Mapped[str] = mapped_column(Text, nullable=False)
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    download_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )
    md5: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), nullable=False, default=utc_now
    )

    message: Mapped[Message] = relationship(back_populates="attachments")


class MessageReportDetection(Base):
    __tablename__ = "message_report_detections"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_report_detections_message_id"),
        CheckConstraint(
            "detection_status IN "
            "('report_candidate', 'needs_review', 'ignored', 'not_applicable')",
            name="ck_report_detections_status",
        ),
        Index("ix_report_detections_msgid", "msgid"),
        Index("ix_report_detections_status", "detection_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    msgid: Mapped[str] = mapped_column(String(255), nullable=False)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    detection_status: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_report_candidate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    matched_rules: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    detector_version: Mapped[str] = mapped_column(String(32), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )

    message: Mapped[Message] = relationship(back_populates="report_detection")
