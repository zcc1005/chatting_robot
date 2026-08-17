"""单条施工日报结构化结果及子项的数据库读写。"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.database_models import (
    Message,
    ProjectReport,
    ReportEquipment,
    ReportWorkItem,
)
from app.models.report_schemas import (
    EXTRACTED_FIELD_NAMES,
    ProjectReportPatch,
    ReportExtractionPayload,
)


def _load_options():
    return (
        joinedload(ProjectReport.message),
        selectinload(ProjectReport.equipment),
        selectinload(ProjectReport.work_items),
    )


def get_by_message_id(session: Session, message_id: int) -> ProjectReport | None:
    statement = (
        select(ProjectReport)
        .options(*_load_options())
        .where(ProjectReport.message_id == message_id)
    )
    return session.scalar(statement)


def get_by_msgid(session: Session, msgid: str) -> ProjectReport | None:
    statement = (
        select(ProjectReport)
        .options(*_load_options())
        .where(ProjectReport.msgid == msgid)
    )
    return session.scalar(statement)


def get_by_id(session: Session, report_id: int) -> ProjectReport | None:
    statement = (
        select(ProjectReport)
        .options(*_load_options())
        .where(ProjectReport.id == report_id)
    )
    return session.scalar(statement)


def start_extraction(session: Session, message: Message) -> ProjectReport:
    record = get_by_message_id(session, message.id)
    now = datetime.now(timezone.utc)
    if record is None:
        record = ProjectReport(
            msgid=message.msgid,
            message_id=message.id,
            missing_fields=serialize_missing_fields(list(EXTRACTED_FIELD_NAMES)),
            extraction_status="pending",
            extraction_source="llm",
            created_at=now,
            updated_at=now,
        )
        session.add(record)
    else:
        _clear_extracted_fields(record)
        record.msgid = message.msgid
        record.missing_fields = serialize_missing_fields(
            list(EXTRACTED_FIELD_NAMES)
        )
        record.extraction_status = "pending"
        record.extraction_source = "llm"
        record.raw_extraction_json = None
        record.error_message = None
        record.relevance_status = "not_reviewed"
        record.relevance_reason = None
        record.relevance_confidence = None
        record.date_source = "missing"
        record.normalization_warnings = "[]"
        record.updated_at = now
    session.commit()
    return get_by_message_id(session, message.id) or record


def save_success(
    session: Session,
    record: ProjectReport,
    payload: ReportExtractionPayload,
    raw_response: str,
    *,
    date_source: str = "llm",
    normalization_warnings: list[str] | None = None,
) -> ProjectReport:
    record.project_name = payload.project_name
    record.report_date = payload.report_date
    record.weather = payload.weather
    record.management_count = payload.management_count
    record.worker_count = payload.worker_count
    record.tomorrow_plan = payload.tomorrow_plan
    record.safety_status = payload.safety_status
    record.quality_status = payload.quality_status
    record.missing_fields = serialize_missing_fields(payload.missing_fields)
    record.confidence = payload.confidence
    record.extraction_status = determine_extraction_status(
        payload.project_name, payload.report_date, payload.work_items
    )
    record.extraction_source = "llm"
    record.relevance_status = payload.relevance_status
    record.relevance_reason = payload.relevance_reason
    record.relevance_confidence = payload.relevance_confidence
    record.date_source = date_source
    record.normalization_warnings = serialize_string_list(
        normalization_warnings or []
    )
    record.raw_extraction_json = raw_response
    record.error_message = None
    record.updated_at = datetime.now(timezone.utc)
    _replace_equipment(record, payload.equipment)
    _replace_work_items(record, payload.work_items)
    session.commit()
    return get_by_message_id(session, record.message_id) or record


def save_failure(
    session: Session,
    record: ProjectReport,
    *,
    error_message: str,
    raw_response: str | None = None,
) -> ProjectReport:
    _clear_extracted_fields(record)
    record.missing_fields = serialize_missing_fields(list(EXTRACTED_FIELD_NAMES))
    record.extraction_status = "failed"
    record.extraction_source = "llm"
    record.raw_extraction_json = raw_response
    record.error_message = error_message
    record.relevance_status = "not_reviewed"
    record.relevance_reason = None
    record.relevance_confidence = None
    record.date_source = "missing"
    record.normalization_warnings = "[]"
    record.updated_at = datetime.now(timezone.utc)
    session.commit()
    return get_by_message_id(session, record.message_id) or record


def apply_manual_patch(
    session: Session,
    record: ProjectReport,
    patch: ProjectReportPatch,
) -> ProjectReport:
    scalar_fields = {
        "project_name",
        "report_date",
        "weather",
        "management_count",
        "worker_count",
        "tomorrow_plan",
        "safety_status",
        "quality_status",
        "confidence",
    }
    for field_name in patch.model_fields_set & scalar_fields:
        setattr(record, field_name, getattr(patch, field_name))
    if "equipment" in patch.model_fields_set:
        _replace_equipment(record, patch.equipment)
    if "work_items" in patch.model_fields_set:
        _replace_work_items(record, patch.work_items)

    if {"management_count", "worker_count"} & patch.model_fields_set:
        warnings = deserialize_string_list(record.normalization_warnings)
        if "management_count" in patch.model_fields_set:
            warnings = [
                warning
                for warning in warnings
                if not warning.startswith("管理人员数据冲突：")
                and "管理人员数量" not in warning
            ]
        if "worker_count" in patch.model_fields_set:
            warnings = [
                warning
                for warning in warnings
                if "施工总人数" not in warning
                and "施工人员数量" not in warning
            ]
        record.normalization_warnings = serialize_string_list(warnings)

    missing_fields = _calculate_record_missing_fields(record)
    record.missing_fields = serialize_missing_fields(missing_fields)
    record.extraction_status = determine_extraction_status(
        record.project_name,
        record.report_date,
        record.work_items or None,
    )
    record.extraction_source = "manual"
    if "report_date" in patch.model_fields_set:
        record.date_source = "manual" if record.report_date else "missing"
    if record.extraction_status == "completed" and record.relevance_status in {
        "not_reviewed",
        "ordinary_chat",
        "uncertain",
    }:
        record.relevance_status = "report"
        record.relevance_reason = "人工修正后具备项目、日期和施工内容"
        record.relevance_confidence = None
    record.error_message = None
    record.updated_at = datetime.now(timezone.utc)
    session.commit()
    return get_by_message_id(session, record.message_id) or record


def list_reports(
    session: Session,
    *,
    project_name: str | None = None,
    report_date: date | None = None,
    extraction_status: str | None = None,
    chatid: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ProjectReport]:
    statement = select(ProjectReport).join(ProjectReport.message).options(
        *_load_options()
    )
    if project_name is not None:
        statement = statement.where(ProjectReport.project_name == project_name)
    if report_date is not None:
        statement = statement.where(ProjectReport.report_date == report_date)
    if extraction_status is not None:
        statement = statement.where(
            ProjectReport.extraction_status == extraction_status
        )
    if chatid is not None:
        statement = statement.where(Message.chatid == chatid)
    statement = (
        statement.order_by(ProjectReport.updated_at.desc(), ProjectReport.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(statement).all())


def serialize_missing_fields(fields: list[str]) -> str:
    return json.dumps(fields, ensure_ascii=False, separators=(",", ":"))


def deserialize_missing_fields(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list) or not all(
        isinstance(item, str) for item in parsed
    ):
        return []
    return parsed


def serialize_string_list(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def deserialize_string_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list) or not all(
        isinstance(item, str) for item in parsed
    ):
        return []
    return parsed


def determine_extraction_status(
    project_name: str | None,
    report_date: date | None,
    work_items: list[object] | None,
) -> str:
    if project_name is None or report_date is None or not work_items:
        return "needs_review"
    return "completed"


def _clear_extracted_fields(record: ProjectReport) -> None:
    for field_name in (
        "project_name",
        "report_date",
        "weather",
        "management_count",
        "worker_count",
        "tomorrow_plan",
        "safety_status",
        "quality_status",
        "confidence",
    ):
        setattr(record, field_name, None)
    record.equipment.clear()
    record.work_items.clear()


def _replace_equipment(record: ProjectReport, equipment) -> None:
    record.equipment.clear()
    if equipment is None:
        return
    record.equipment.extend(
        ReportEquipment(
            name=item.name,
            count=item.count,
            unit=item.unit,
            position=index,
        )
        for index, item in enumerate(equipment)
    )


def _replace_work_items(record: ProjectReport, work_items) -> None:
    record.work_items.clear()
    if work_items is None:
        return
    record.work_items.extend(
        ReportWorkItem(
            location=item.location,
            content=item.content,
            progress=item.progress,
            position=index,
        )
        for index, item in enumerate(work_items)
    )


def _calculate_record_missing_fields(record: ProjectReport) -> list[str]:
    missing: list[str] = []
    for field_name in EXTRACTED_FIELD_NAMES:
        if field_name == "equipment":
            value = record.equipment or None
        elif field_name == "work_items":
            value = record.work_items or None
        else:
            value = getattr(record, field_name)
        if value is None:
            missing.append(field_name)
    return missing
