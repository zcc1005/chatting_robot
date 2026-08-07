"""消息与附件的 SQLAlchemy ORM 模型。"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
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
    project_report: Mapped["ProjectReport | None"] = relationship(
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


class ProjectReport(Base):
    __tablename__ = "project_reports"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_project_reports_message_id"),
        CheckConstraint(
            "extraction_status IN ('pending', 'completed', 'needs_review', 'failed')",
            name="ck_project_reports_extraction_status",
        ),
        CheckConstraint(
            "extraction_source IN ('llm', 'manual')",
            name="ck_project_reports_extraction_source",
        ),
        Index("ix_project_reports_msgid", "msgid"),
        Index("ix_project_reports_project_name", "project_name"),
        Index("ix_project_reports_report_date", "report_date"),
        Index("ix_project_reports_extraction_status", "extraction_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    msgid: Mapped[str] = mapped_column(String(255), nullable=False)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    project_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    report_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    weather: Mapped[str | None] = mapped_column(String(255), nullable=True)
    management_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    worker_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tomorrow_plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    safety_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    missing_fields: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    extraction_status: Mapped[str] = mapped_column(String(32), nullable=False)
    extraction_source: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_extraction_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )

    message: Mapped[Message] = relationship(back_populates="project_report")
    equipment: Mapped[list["ReportEquipment"]] = relationship(
        back_populates="project_report",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ReportEquipment.position",
    )
    work_items: Mapped[list["ReportWorkItem"]] = relationship(
        back_populates="project_report",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ReportWorkItem.position",
    )
    summary_items: Mapped[list["DailyReportSummaryItem"]] = relationship(
        back_populates="project_report"
    )


class ReportEquipment(Base):
    __tablename__ = "report_equipment"
    __table_args__ = (Index("ix_report_equipment_report_id", "project_report_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_report_id: Mapped[int] = mapped_column(
        ForeignKey("project_reports.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    project_report: Mapped[ProjectReport] = relationship(back_populates="equipment")


class ReportWorkItem(Base):
    __tablename__ = "report_work_items"
    __table_args__ = (Index("ix_report_work_items_report_id", "project_report_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_report_id: Mapped[int] = mapped_column(
        ForeignKey("project_reports.id", ondelete="CASCADE"), nullable=False
    )
    location: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    progress: Mapped[str | None] = mapped_column(String(255), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    project_report: Mapped[ProjectReport] = relationship(back_populates="work_items")


class DailyReportSummary(Base):
    __tablename__ = "daily_report_summaries"
    __table_args__ = (
        CheckConstraint(
            "project_count >= 0",
            name="ck_daily_summaries_project_count_nonnegative",
        ),
        CheckConstraint(
            "management_total IS NULL OR management_total >= 0",
            name="ck_daily_summaries_management_total_nonnegative",
        ),
        CheckConstraint(
            "worker_total IS NULL OR worker_total >= 0",
            name="ck_daily_summaries_worker_total_nonnegative",
        ),
        CheckConstraint(
            "generation_status IN ('completed', 'needs_review')",
            name="ck_daily_summaries_generation_status",
        ),
        CheckConstraint(
            "publication_status IN "
            "('draft', 'confirmed', 'sending', 'sent', 'send_failed')",
            name="ck_daily_summaries_publication_status",
        ),
        Index("ix_daily_summaries_chatid", "chatid"),
        Index("ix_daily_summaries_report_date", "report_date"),
        Index("ix_daily_summaries_generation_status", "generation_status"),
        Index("ix_daily_summaries_publication_status", "publication_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chatid: Mapped[str] = mapped_column(String(255), nullable=False)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    project_count: Mapped[int] = mapped_column(Integer, nullable=False)
    management_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    worker_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    publication_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="draft", server_default="draft"
    )
    confirmed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(
        AwareDateTime(), nullable=True
    )
    confirmation_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    markdown_content: Mapped[str] = mapped_column(Text, nullable=False)
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )

    items: Mapped[list["DailyReportSummaryItem"]] = relationship(
        back_populates="summary",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DailyReportSummaryItem.display_order",
    )
    send_attempts: Mapped[list["DailyReportSendAttempt"]] = relationship(
        back_populates="summary",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DailyReportSendAttempt.id",
    )


class DailyReportSummaryItem(Base):
    __tablename__ = "daily_report_summary_items"
    __table_args__ = (
        UniqueConstraint(
            "summary_id",
            "project_report_id",
            name="uq_daily_summary_items_source",
        ),
        CheckConstraint(
            "display_order >= 0",
            name="ck_daily_summary_items_display_order_nonnegative",
        ),
        Index("ix_daily_summary_items_summary_id", "summary_id"),
        Index("ix_daily_summary_items_project_report_id", "project_report_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    summary_id: Mapped[int] = mapped_column(
        ForeignKey("daily_report_summaries.id", ondelete="CASCADE"), nullable=False
    )
    project_report_id: Mapped[int] = mapped_column(
        ForeignKey("project_reports.id"), nullable=False
    )
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)

    summary: Mapped[DailyReportSummary] = relationship(back_populates="items")
    project_report: Mapped[ProjectReport] = relationship(
        back_populates="summary_items"
    )


class DailyReportSendAttempt(Base):
    __tablename__ = "daily_report_send_attempts"
    __table_args__ = (
        CheckConstraint(
            "send_status IN ('sending', 'sent', 'send_failed')",
            name="ck_daily_send_attempts_status",
        ),
        CheckConstraint(
            "transport IN ('mock', 'real')",
            name="ck_daily_send_attempts_transport",
        ),
        Index("ix_daily_send_attempts_summary_id", "summary_id"),
        Index("ix_daily_send_attempts_trigger_message_id", "trigger_message_id"),
        Index("ix_daily_send_attempts_status", "send_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    summary_id: Mapped[int] = mapped_column(
        ForeignKey("daily_report_summaries.id", ondelete="CASCADE"), nullable=False
    )
    trigger_message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id"), nullable=False
    )
    trigger_msgid: Mapped[str] = mapped_column(String(255), nullable=False)
    response_url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    send_status: Mapped[str] = mapped_column(String(32), nullable=False)
    transport: Mapped[str] = mapped_column(String(32), nullable=False)
    http_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        AwareDateTime(), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), nullable=False, default=utc_now
    )

    summary: Mapped[DailyReportSummary] = relationship(
        back_populates="send_attempts"
    )
