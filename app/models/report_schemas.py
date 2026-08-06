"""单条施工日报结构化提取的严格模型与 API 模型。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ExtractionStatus = Literal["pending", "completed", "needs_review", "failed"]
ExtractionSource = Literal["llm", "manual"]
ExtractedFieldName = Literal[
    "project_name",
    "report_date",
    "weather",
    "management_count",
    "worker_count",
    "equipment",
    "work_items",
    "tomorrow_plan",
    "safety_status",
    "quality_status",
]
EXTRACTED_FIELD_NAMES: tuple[ExtractedFieldName, ...] = (
    "project_name",
    "report_date",
    "weather",
    "management_count",
    "worker_count",
    "equipment",
    "work_items",
    "tomorrow_plan",
    "safety_status",
    "quality_status",
)


class ExtractedEquipment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=255)
    count: int = Field(ge=0)
    unit: str = Field(min_length=1, max_length=32)


class ExtractedWorkItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    location: str | None
    content: str = Field(min_length=1)
    progress: str | None

    @field_validator("location", "progress")
    @classmethod
    def optional_text_cannot_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("未提供的文本字段必须使用 null")
        return value


class ReportExtractionPayload(BaseModel):
    """大模型必须返回的 JSON；所有键必填，缺失值必须为 null。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    project_name: str | None
    report_date: date | None
    weather: str | None
    management_count: int | None = Field(ge=0)
    worker_count: int | None = Field(ge=0)
    equipment: list[ExtractedEquipment] | None
    work_items: list[ExtractedWorkItem] | None
    tomorrow_plan: str | None
    safety_status: str | None
    quality_status: str | None
    missing_fields: list[ExtractedFieldName]
    confidence: float = Field(ge=0, le=1)

    @field_validator(
        "project_name",
        "weather",
        "tomorrow_plan",
        "safety_status",
        "quality_status",
    )
    @classmethod
    def optional_text_cannot_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("未提供的文本字段必须使用 null")
        return value

    @field_validator("equipment", "work_items")
    @classmethod
    def lists_cannot_be_empty(cls, value: list[object] | None):
        if value == []:
            raise ValueError("未提供的列表字段必须使用 null")
        return value

    @model_validator(mode="after")
    def missing_fields_must_match_null_values(self) -> "ReportExtractionPayload":
        expected = {
            field_name
            for field_name in EXTRACTED_FIELD_NAMES
            if getattr(self, field_name) is None
        }
        supplied = set(self.missing_fields)
        if len(supplied) != len(self.missing_fields) or supplied != expected:
            raise ValueError("missing_fields 必须与值为 null 的字段完全一致")
        return self


class ProjectReportPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    project_name: str | None = None
    report_date: date | None = None
    weather: str | None = None
    management_count: int | None = Field(default=None, ge=0)
    worker_count: int | None = Field(default=None, ge=0)
    equipment: list[ExtractedEquipment] | None = None
    work_items: list[ExtractedWorkItem] | None = None
    tomorrow_plan: str | None = None
    safety_status: str | None = None
    quality_status: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator(
        "project_name",
        "weather",
        "tomorrow_plan",
        "safety_status",
        "quality_status",
    )
    @classmethod
    def optional_text_cannot_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("字段不能是空字符串，需要清空时请使用 null")
        return value

    @field_validator("equipment", "work_items")
    @classmethod
    def lists_cannot_be_empty(cls, value: list[object] | None):
        if value == []:
            raise ValueError("列表不能为空，需要清空时请使用 null")
        return value

    @model_validator(mode="after")
    def at_least_one_field(self) -> "ProjectReportPatch":
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要修改的字段")
        return self


class ProjectReportResponse(BaseModel):
    id: int
    msgid: str
    message_id: int
    chatid: str
    project_name: str | None
    report_date: date | None
    weather: str | None
    management_count: int | None
    worker_count: int | None
    equipment: list[ExtractedEquipment] | None
    work_items: list[ExtractedWorkItem] | None
    tomorrow_plan: str | None
    safety_status: str | None
    quality_status: str | None
    missing_fields: list[ExtractedFieldName]
    confidence: float | None
    extraction_status: ExtractionStatus
    extraction_source: ExtractionSource
    raw_extraction_json: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class ProjectReportListResponse(BaseModel):
    items: list[ProjectReportResponse]
    limit: int
    offset: int
