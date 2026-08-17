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
_COUNT_TOKEN = r"[零〇一二两三四五六七八九十百千万\d]+"
_WORKER_TOTAL_PATTERN = re.compile(
    rf"(?:施工总人数|总施工人数|施工人员(?:总数|总计|合计)|"
    rf"现场施工人员(?:总数|总计|合计)|现场工人(?:总数|总计|合计))"
    rf"\s*(?:共|为)?\s*[:：]?\s*(?P<count>{_COUNT_TOKEN})\s*(?:人|名)?"
    rf"(?=\s*[:：；;，,。\n]|$)"
)
_MANAGEMENT_DECLARED_TOTAL_PATTERN = re.compile(
    rf"(?:管理人员(?:总数|总计|合计)|管理总人数)"
    rf"\s*(?:共|为)?\s*[:：]?\s*(?P<count>{_COUNT_TOKEN})\s*(?:人|名)?"
    rf"(?=\s*[:：；;，,。\n]|$)"
)
_MANAGEMENT_LINE_TOTAL_PATTERN = re.compile(
    rf"(?m)(?:^|[\n；;。])\s*"
    rf"(?:[一二三四五六七八九十\d]+\s*[、.．]\s*)?"
    rf"管理人员\s*(?:共|为)?\s*[:：]?\s*(?P<count>{_COUNT_TOKEN})"
    rf"\s*(?:人|名)?(?=\s*[:：；;，,。\n]|$)"
)
_MANAGEMENT_COLON_TOTAL_PATTERN = re.compile(
    rf"管理人员\s*(?:共|为)?\s*[:：]?\s*(?P<count>{_COUNT_TOKEN})"
    rf"\s*(?:人|名)?\s*[:：]"
)
_MANAGEMENT_ITEM_PATTERN = re.compile(
    rf"管理人员\s*(?:共|为)?\s*[:：]?\s*(?P<count>{_COUNT_TOKEN})"
    rf"\s*(?:人|名)?(?=\s*[:：；;，,。()（）\n]|$)"
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
    """只修复可由原文确定的日期、人数和无效施工子项。"""
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

    worker_count = _single_explicit_count(_WORKER_TOTAL_PATTERN, text_content)
    if worker_count is not None and payload.get("worker_count") != worker_count:
        payload["worker_count"] = worker_count
        warnings.append(
            f"原文明示施工总人数为 {worker_count} 人，已覆盖大模型施工人员数量"
        )
        changed = True

    management_count, management_warning = _resolve_management_count(
        text_content
    )
    if management_warning is not None:
        payload["management_count"] = None
        warnings.append(management_warning)
        changed = True
    elif (
        management_count is not None
        and payload.get("management_count") != management_count
    ):
        payload["management_count"] = management_count
        warnings.append(
            f"原文明示的管理人员数量为 {management_count} 人，"
            "已覆盖大模型管理人员数量"
        )
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


def _single_explicit_count(
    pattern: re.Pattern[str], text_content: str
) -> int | None:
    values = _all_explicit_counts(pattern, text_content)
    if not values or len(set(values)) != 1:
        return None
    return values[0]


def _all_explicit_counts(
    pattern: re.Pattern[str], text_content: str
) -> list[int]:
    values: list[int] = []
    for match in pattern.finditer(text_content):
        value = _parse_count(match.group("count"))
        if value is not None:
            values.append(value)
    return values


def _resolve_management_count(
    text_content: str,
) -> tuple[int | None, str | None]:
    total_matches = _unique_count_matches(
        (
            _MANAGEMENT_DECLARED_TOTAL_PATTERN,
            _MANAGEMENT_LINE_TOTAL_PATTERN,
            _MANAGEMENT_COLON_TOTAL_PATTERN,
        ),
        text_content,
    )
    total_values = [value for _, value in total_matches]
    unique_totals = sorted(set(total_values))
    if len(unique_totals) > 1:
        values = "、".join(str(value) for value in unique_totals)
        return None, f"管理人员数据冲突：原文出现多个总数（{values} 人）"

    total_spans = [span for span, _ in total_matches]
    item_values = [
        value
        for span, value in _count_matches(
            _MANAGEMENT_ITEM_PATTERN, text_content
        )
        if not any(_spans_overlap(span, total_span) for total_span in total_spans)
    ]
    total = unique_totals[0] if unique_totals else None
    item_sum = sum(item_values) if item_values else None
    if total is not None and item_sum is not None and total != item_sum:
        return (
            None,
            f"管理人员数据冲突：原文总数 {total} 人，明细合计 {item_sum} 人",
        )
    if total is not None:
        return total, None
    if item_sum is not None:
        return item_sum, None
    return None, None


def _unique_count_matches(
    patterns: tuple[re.Pattern[str], ...], text_content: str
) -> list[tuple[tuple[int, int], int]]:
    matches: list[tuple[tuple[int, int], int]] = []
    seen: set[tuple[int, int]] = set()
    for pattern in patterns:
        for span, value in _count_matches(pattern, text_content):
            if span not in seen:
                seen.add(span)
                matches.append((span, value))
    return matches


def _count_matches(
    pattern: re.Pattern[str], text_content: str
) -> list[tuple[tuple[int, int], int]]:
    matches: list[tuple[tuple[int, int], int]] = []
    for match in pattern.finditer(text_content):
        value = _parse_count(match.group("count"))
        if value is not None:
            matches.append((match.span(), value))
    return matches


def _spans_overlap(first: tuple[int, int], second: tuple[int, int]) -> bool:
    return first[0] < second[1] and second[0] < first[1]


def _parse_count(raw: str) -> int | None:
    if raw.isdigit():
        return int(raw)
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    units = {"十": 10, "百": 100, "千": 1000}
    total = 0
    section = 0
    number = 0
    for character in raw:
        if character in digits:
            number = digits[character]
        elif character in units:
            section += (number or 1) * units[character]
            number = 0
        elif character == "万":
            total += (section + number) * 10_000
            section = 0
            number = 0
        else:
            return None
    return total + section + number
