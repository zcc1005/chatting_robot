"""群聊施工图片识别、关联及 API 模型。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


RecognitionStatus = Literal["pending", "completed", "failed"]
AssociationStatus = Literal["linked", "needs_review", "unmatched", "manual"]


class ImageRecognitionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    project_name: str | None
    report_date: date | None
    captured_at: datetime | None
    weather: str | None
    location: str | None
    construction_content: str | None
    ocr_text: str | None
    scene_description: str | None
    confidence: float = Field(ge=0, le=1)

    @field_validator(
        "project_name",
        "weather",
        "location",
        "construction_content",
        "ocr_text",
        "scene_description",
    )
    @classmethod
    def optional_text_cannot_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("图片未识别出的文本字段必须使用 null")
        return value

    @field_validator("captured_at")
    @classmethod
    def captured_at_must_be_aware(
        cls, value: datetime | None
    ) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("captured_at 必须包含时区")
        return value


class ImageAssociationResponse(BaseModel):
    id: int
    project_report_id: int | None
    project_report_msgid: str | None
    project_name: str | None
    association_status: AssociationStatus
    score: int = Field(ge=0)
    matched_rules: list[str]
    candidate_scores: list[dict[str, object]]
    reason: str
    updated_at: datetime


class ImageRecognitionResponse(BaseModel):
    id: int
    attachment_id: int
    message_id: int
    msgid: str
    chatid: str
    sender_userid: str
    source_url: str
    recognition_status: RecognitionStatus
    project_name: str | None
    report_date: date | None
    captured_at: datetime | None
    weather: str | None
    location: str | None
    construction_content: str | None
    ocr_text: str | None
    scene_description: str | None
    confidence: float | None
    image_sha256: str | None
    error_message: str | None
    recognizer_version: str
    association: ImageAssociationResponse | None
    created_at: datetime
    updated_at: datetime


class ImageRecognitionListResponse(BaseModel):
    items: list[ImageRecognitionResponse]
    limit: int
    offset: int


class ManualImageAssociationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    project_report_id: int | None
