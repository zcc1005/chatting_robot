"""对大模型 JSON 做有限、可解释且不编造数据的确定性规范化。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.models.report_schemas import EXTRACTED_FIELD_NAMES


_FULL_CHINESE_DATE = re.compile(
    r"(?<!\d)(?P<year>\d{4})\s*年\s*(?P<month>\d{1,2})\s*月\s*"
    r"(?P<day>\d{1,2})\s*日"
)
_FULL_NUMERIC_DATE = re.compile(
    r"(?<!\d)(?P<year>\d{4})[-/.](?P<month>\d{1,2})[-/.]"
    r"(?P<day>\d{1,2})(?!\d)"
)
_MONTH_DAY = re.compile(
    r"(?<!\d)(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日"
)


@dataclass(frozen=True, slots=True)
class NormalizedExtractionResponse:
    json_text: str
    date_source: str
    warnings: list[str]


def normalize_extraction_response(
    raw_response: str,
    *,
    text_content: str,
    received_at: datetime,
) -> NormalizedExtractionResponse:
    """只修复可由原文确定的日期和无有效内容的孤立施工子项。"""
    try:
        payload = json.loads(raw_response)
    except (json.JSONDecodeError, TypeError):
        return NormalizedExtractionResponse(raw_response, "missing", [])
    if not isinstance(payload, dict):
        return NormalizedExtractionResponse(raw_response, "missing", [])

    warnings: list[str] = []
    changed = False
    supplied_date = payload.get("report_date")
    date_source = "llm" if supplied_date is not None else "missing"
    resolved, resolved_source = resolve_report_date(
        text_content, received_at=received_at
    )
    if resolved is not None:
        date_source = resolved_source
        if supplied_date is None or (
            isinstance(supplied_date, str)
            and supplied_date != resolved.isoformat()
        ):
            payload["report_date"] = resolved.isoformat()
            changed = True

    work_items = payload.get("work_items")
    if isinstance(work_items, list):
        valid_items: list[object] = []
        dropped = 0
        for item in work_items:
            if isinstance(item, dict) and (
                item.get("content") is None
                or (
                    isinstance(item.get("content"), str)
                    and not item["content"].strip()
                )
            ):
                dropped += 1
                continue
            valid_items.append(item)
        if dropped:
            payload["work_items"] = valid_items or None
            warnings.append(
                f"大模型返回的 {dropped} 项施工子项没有明确施工内容，已忽略并保留原始返回供人工核对"
            )
            changed = True

    if changed:
        payload["missing_fields"] = [
            field_name
            for field_name in EXTRACTED_FIELD_NAMES
            if payload.get(field_name) is None
        ]
        normalized = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        )
    else:
        normalized = raw_response
    return NormalizedExtractionResponse(normalized, date_source, warnings)


def resolve_report_date(
    text_content: str, *, received_at: datetime
) -> tuple[date | None, str]:
    """完整日期优先；只有月日时采用距消息接收日最近的年份。"""
    for pattern in (_FULL_CHINESE_DATE, _FULL_NUMERIC_DATE):
        match = pattern.search(text_content)
        if match is not None:
            resolved = _safe_date(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
            )
            if resolved is not None:
                return resolved, "text_full_date"

    match = _MONTH_DAY.search(text_content)
    if match is None:
        return None, "missing"
    local_received = _local_date(received_at)
    month = int(match.group("month"))
    day = int(match.group("day"))
    candidates = [
        candidate
        for year in (
            local_received.year - 1,
            local_received.year,
            local_received.year + 1,
        )
        if (candidate := _safe_date(year, month, day)) is not None
    ]
    if not candidates:
        return None, "missing"
    resolved = min(
        candidates,
        key=lambda candidate: (abs((candidate - local_received).days), candidate),
    )
    return resolved, "text_month_day_message_year"


def _local_date(value: datetime) -> date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo("Asia/Shanghai")).date()


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None
