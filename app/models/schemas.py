"""统一消息模型及 API 返回模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NormalizedAttachment(BaseModel):
    attachment_type: Literal["image", "file"]
    remote_url: str = Field(min_length=1)
    local_path: str | None = None
    download_status: str = "pending"
    md5: str | None = None


class NormalizedMessage(BaseModel):
    source: Literal["jjt", "mock"]
    msgid: str = Field(min_length=1)
    aibotid: str | None = None
    chatid: str = Field(min_length=1)
    chat_name: str | None = None
    chattype: Literal["group", "single"]
    sender_userid: str = Field(min_length=1)
    msgtype: str = Field(min_length=1)
    text_content: str = ""
    attachments: list[NormalizedAttachment] = Field(default_factory=list)
    response_url: str | None = None
    received_at: datetime
    process_status: Literal["received", "unsupported"] = "received"
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("received_at")
    @classmethod
    def received_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("received_at 必须包含时区信息")
        return value


DetectionStatus = Literal[
    "report_candidate", "needs_review", "ignored", "not_applicable"
]


class MessageCreateResult(BaseModel):
    status: Literal["saved", "ignored"]
    msgid: str
    duplicate: bool
    database_id: int | None = None
    detection_status: DetectionStatus | None = None
    score: int | None = None
    is_report_candidate: bool | None = None
    matched_rules: list[str] | None = None
    reason: str | None = None


class MockTriggerMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chatid: str = Field(min_length=1, max_length=255)
    summary_id: int = Field(gt=0)

    @field_validator("chatid")
    @classmethod
    def chatid_cannot_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("chatid 不能为空")
        return stripped


class MockTriggerMessageResponse(BaseModel):
    status: Literal["saved"]
    trigger_msgid: str
    chatid: str
    summary_id: int


class MockImageMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chatid: str = Field(min_length=1, max_length=255)
    sender_userid: str = Field(min_length=1, max_length=255)
    sender_name: str | None = Field(default=None, max_length=255)
    filename: str = Field(min_length=1, max_length=255)
    content_type: Literal["image/png", "image/jpeg", "image/webp"]
    image_base64: str = Field(min_length=1, max_length=70_000_000)
    received_at: datetime | None = None

    @field_validator("chatid", "sender_userid")
    @classmethod
    def identifier_cannot_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("标识字段不能为空")
        return stripped

    @field_validator("received_at")
    @classmethod
    def optional_time_must_be_aware(
        cls, value: datetime | None
    ) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("received_at 必须包含时区")
        return value


class ReportDetectionResponse(BaseModel):
    msgid: str
    detection_status: DetectionStatus
    score: int
    is_report_candidate: bool
    matched_rules: list[str]
    reason: str


class ReportDetectionDetail(ReportDetectionResponse):
    message_id: int
    detector_version: str
    detected_at: datetime
    updated_at: datetime


class ReportDetectionListItem(ReportDetectionDetail):
    chatid: str
    msgtype: str
    text_content: str
    received_at: datetime


class ReportDetectionListResponse(BaseModel):
    items: list[ReportDetectionListItem]
    limit: int
    offset: int


class MessageListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    msgid: str
    source: str
    chatid: str
    chat_name: str | None
    chattype: str
    sender_userid: str
    msgtype: str
    text_content: str
    received_at: datetime
    process_status: str
    attachment_count: int
    image_urls: list[str] = Field(default_factory=list)


class MessageListResponse(BaseModel):
    items: list[MessageListItem]
    limit: int
    offset: int


class AttachmentDetail(BaseModel):
    id: int
    attachment_type: str
    remote_url: str
    local_path: str | None
    download_status: str
    md5: str | None
    created_at: datetime


class MessageDetailResponse(BaseModel):
    id: int
    msgid: str
    source: str
    aibotid: str | None
    chatid: str
    chat_name: str | None
    chattype: str
    sender_userid: str
    msgtype: str
    text_content: str
    response_url: str | None
    raw_payload: dict[str, Any]
    received_at: datetime
    process_status: str
    created_at: datetime
    updated_at: datetime
    attachments: list[AttachmentDetail]
    report_detection: ReportDetectionDetail | None = None
