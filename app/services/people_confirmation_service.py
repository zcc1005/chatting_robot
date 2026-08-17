"""处理聊天中的人数确认、修改和拒绝。"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.database_models import Message, ProjectReport
from app.models.report_schemas import ProjectReportPatch
from app.repositories.people_confirmation_repository import (
    create_pending_request,
    get_pending_request,
    resolve_request,
    update_requested_fields,
)
from app.repositories.project_report_repository import (
    apply_manual_patch,
    get_by_id,
)


_MANAGEMENT_CORRECTION = re.compile(
    r"管理人员\s*(?:修改|改|调整)?\s*(?:为|是|[:：])?\s*(?P<count>\d+)\s*人"
)
_WORKER_CORRECTION = re.compile(
    r"(?:施工人员|施工人数|现场工人)\s*(?:修改|改|调整)?\s*"
    r"(?:为|是|[:：])?\s*(?P<count>\d+)\s*人"
)
_MENTION_PREFIX = re.compile(r"^\s*(?:@[^\s,，:：]+[\s,，:：]*)+")
_REFUSAL_PATTERN = re.compile(
    r"^(?:不修改人数|不用修改人数|不需要修改人数|不修改|不用修改|"
    r"不需要修改|不改|不用改|拒绝|算了|不用|否)"
    r"[吧呀啊了~～!！?？。,.，\s]*$"
)


def confirmation_fields(report: ProjectReport) -> list[str]:
    if report.relevance_status != "report":
        return []
    fields: list[str] = []
    if report.management_count is None:
        fields.append("management_count")
    if report.worker_count is None:
        fields.append("worker_count")
    return fields


def create_confirmation_if_needed(
    session: Session, *, message: Message, report: ProjectReport
) -> str | None:
    fields = confirmation_fields(report)
    if not fields:
        return None
    create_pending_request(
        session,
        message=message,
        report=report,
        requested_fields=fields,
    )
    return confirmation_prompt(fields)


def handle_pending_confirmation(
    session: Session,
    *,
    message: Message,
) -> tuple[bool, str | None]:
    """返回是否已消费当前消息，以及需要回复的内容。"""

    request = get_pending_request(
        session,
        chatid=message.chatid,
        sender_userid=message.sender_userid,
    )
    if request is None:
        return False, None

    corrections = _parse_corrections(message.text_content)
    if corrections:
        report = get_by_id(session, request.project_report_id)
        if report is None:
            resolve_request(
                session, request, status="ignored", resolved_by=message
            )
            return False, None
        patch = ProjectReportPatch.model_validate(corrections)
        updated = apply_manual_patch(session, report, patch)
        remaining = confirmation_fields(updated)
        confirmed_parts: list[str] = []
        if "management_count" in corrections:
            confirmed_parts.append(
                f"管理人员 {corrections['management_count']} 人"
            )
        if "worker_count" in corrections:
            confirmed_parts.append(f"施工人员 {corrections['worker_count']} 人")
        reply = "已确认人数：" + "，".join(confirmed_parts) + "。"
        if remaining:
            update_requested_fields(
                session, request, remaining, resolved_by=message
            )
            reply += "\n\n" + confirmation_prompt(remaining)
        else:
            resolve_request(
                session, request, status="confirmed", resolved_by=message
            )
        return True, reply

    if _is_refusal(message.text_content):
        resolve_request(
            session, request, status="refused", resolved_by=message
        )
        return True, "好的。"

    resolve_request(session, request, status="ignored", resolved_by=message)
    return False, None


def confirmation_prompt(fields: list[str]) -> str:
    examples: list[str] = []
    if "management_count" in fields:
        examples.append("管理人员为xx人")
    if "worker_count" in fields:
        examples.append("施工人员为xx人")
    return (
        "是否修改人数？若要修改，请输入"
        + "或".join(examples)
        + "。若不修改，可回复“不修改”。"
    )


def _parse_corrections(content: str | None) -> dict[str, Any]:
    text = (content or "").strip()
    values: dict[str, Any] = {}
    management = _MANAGEMENT_CORRECTION.search(text)
    worker = _WORKER_CORRECTION.search(text)
    if management is not None:
        values["management_count"] = int(management.group("count"))
    if worker is not None:
        values["worker_count"] = int(worker.group("count"))
    return values


def _is_refusal(content: str | None) -> bool:
    text = _MENTION_PREFIX.sub("", (content or "").strip()).strip()
    return bool(_REFUSAL_PATTERN.fullmatch(text))
