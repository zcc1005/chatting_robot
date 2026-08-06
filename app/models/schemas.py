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
    chattype: Literal["group", "single"]
    sender_userid: str = Field(min_length=1)
    msgtype: str = Field(min_length=1)
    text_content: str = ""
    attachments: list[NormalizedAttachment] = Field(default_factory=list)
    response_url: str | None = None
    received_at: datetime
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("received_at")
    @classmethod
    def received_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("received_at 必须包含时区信息")
        return value


class MessageCreateResult(BaseModel):
    status: Literal["saved", "ignored"]
    msgid: str
    duplicate: bool
    database_id: int | None = None


class MessageListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    msgid: str
    source: str
    chatid: str
    chattype: str
    sender_userid: str
    msgtype: str
    text_content: str
    received_at: datetime
    process_status: str
    attachment_count: int


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

