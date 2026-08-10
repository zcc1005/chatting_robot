"""多条结构化项目日报的确定性汇总与快照 API 模型。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.report_schemas import ExtractedWorkItem, ExtractionStatus


GenerationStatus = Literal["completed", "needs_review"]
PublicationStatus = Literal[
    "draft", "confirmed", "sending", "sent", "send_failed"
]
SendStatus = Literal["sending", "sent", "send_failed"]
SendTransport = Literal["mock", "real"]


class DailyReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chatid: str = Field(min_length=1, max_length=255)
    report_date: date

    @field_validator("chatid")
    @classmethod
    def chatid_cannot_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("chatid 不能为空")
        return value


class SummaryEquipment(BaseModel):
    name: str
    count: int = Field(ge=0)
    unit: str


class SummarySourceReport(BaseModel):
    project_report_id: int
    msgid: str
    project_name: str | None
    extraction_status: ExtractionStatus
    included_in_totals: bool


class SummaryMissingData(BaseModel):
    project_report_id: int
    msgid: str
    project_name: str | None
    fields: list[str]


class SummaryReviewReport(BaseModel):
    project_report_id: int
    msgid: str
    project_name: str | None
    extraction_status: ExtractionStatus
    review_reason: str


class DuplicateProject(BaseModel):
    project_name: str
    report_date: date
    reports: list[SummarySourceReport]


class SummaryProjectImage(BaseModel):
    attachment_id: int
    image_msgid: str
    source_url: str
    project_name: str | None
    captured_at: datetime | None
    weather: str | None
    location: str | None
    construction_content: str | None
    ocr_text: str | None
    scene_description: str | None
    confidence: float | None = Field(default=None, ge=0, le=1)
    association_status: Literal["linked", "manual"]


class SummaryImageReview(BaseModel):
    attachment_id: int
    image_msgid: str
    recognition_status: Literal["pending", "completed", "failed"]
    association_status: Literal[
        "linked", "needs_review", "unmatched", "manual"
    ] | None
    candidate_project_report_id: int | None
    candidate_project_name: str | None
    reason: str


class SummaryProjectDetail(BaseModel):
    project_report_id: int
    msgid: str
    project_name: str
    weather: str | None
    work_items: list[ExtractedWorkItem]
    tomorrow_plan: str | None
    safety_status: str | None
    quality_status: str | None
    missing_fields: list[str]
    images: list[SummaryProjectImage] = Field(default_factory=list)


class DailyReportPreviewResponse(BaseModel):
    chatid: str
    report_date: date
    project_count: int = Field(ge=0)
    fully_complete_project_count: int = Field(ge=0)
    partial_project_count: int = Field(ge=0)
    management_total: int | None = Field(default=None, ge=0)
    worker_total: int | None = Field(default=None, ge=0)
    equipment: list[SummaryEquipment]
    projects: list[SummaryProjectDetail]
    missing_data: list[SummaryMissingData]
    review_reports: list[SummaryReviewReport]
    duplicate_projects: list[DuplicateProject]
    source_reports: list[SummarySourceReport]
    image_reviews: list[SummaryImageReview] = Field(default_factory=list)
    generation_status: GenerationStatus
    warnings: list[str]
    markdown_content: str

    @model_validator(mode="before")
    @classmethod
    def populate_legacy_project_counts(cls, data):
        if isinstance(data, dict) and (
            "fully_complete_project_count" not in data
            or "partial_project_count" not in data
        ):
            projects = data.get("projects", [])
            fully_complete = sum(
                1 for project in projects if not project.get("missing_fields", [])
            )
            data = dict(data)
            data.setdefault("fully_complete_project_count", fully_complete)
            data.setdefault(
                "partial_project_count",
                max(int(data.get("project_count", 0)) - fully_complete, 0),
            )
        return data

    @model_validator(mode="after")
    def project_counts_must_balance(self) -> "DailyReportPreviewResponse":
        if self.project_count != (
            self.fully_complete_project_count + self.partial_project_count
        ):
            raise ValueError("项目数量拆分与 project_count 不一致")
        return self


class DailyReportSummaryResponse(DailyReportPreviewResponse):
    id: int
    publication_status: PublicationStatus
    confirmed_by: str | None
    confirmed_at: datetime | None
    confirmation_note: str | None
    sent_at: datetime | None
    send_attempts: list["DailyReportSendAttemptResponse"]
    created_at: datetime
    updated_at: datetime


class DailyReportSummaryListItem(BaseModel):
    id: int
    chatid: str
    report_date: date
    project_count: int = Field(ge=0)
    fully_complete_project_count: int = Field(ge=0)
    partial_project_count: int = Field(ge=0)
    management_total: int | None = Field(default=None, ge=0)
    worker_total: int | None = Field(default=None, ge=0)
    generation_status: GenerationStatus
    publication_status: PublicationStatus
    warnings: list[str]
    created_at: datetime
    updated_at: datetime


class DailyReportSummaryListResponse(BaseModel):
    items: list[DailyReportSummaryListItem]
    limit: int
    offset: int


class DailyReportConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed_by: str = Field(min_length=1, max_length=255)
    confirmation_note: str | None = Field(default=None, max_length=2000)

    @field_validator("confirmed_by")
    @classmethod
    def confirmed_by_cannot_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("confirmed_by 不能为空")
        return stripped


class DailyReportSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger_msgid: str = Field(min_length=1, max_length=255)

    @field_validator("trigger_msgid")
    @classmethod
    def trigger_msgid_cannot_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("trigger_msgid 不能为空")
        return stripped


class DailyReportSendAttemptResponse(BaseModel):
    id: int
    summary_id: int
    trigger_message_id: int
    trigger_msgid: str
    response_url_hash: str
    send_status: SendStatus
    transport: SendTransport
    http_status_code: int | None
    error_type: str | None
    error_message: str | None
    attempted_at: datetime
    completed_at: datetime | None
    created_at: datetime


class DailyReportSendAttemptsResponse(BaseModel):
    items: list[DailyReportSendAttemptResponse]


class DailyReportSendResponse(BaseModel):
    summary_id: int
    attempt: DailyReportSendAttemptResponse
    publication_status: PublicationStatus
    sent_at: datetime | None
    warnings: list[str]
