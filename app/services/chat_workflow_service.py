"""把本地验收页的日报能力迁移为真实机器人聊天工作流。"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.database_models import Message, MessageReportDetection, ProjectReport
from app.repositories.daily_report_summary_repository import (
    list_source_images,
    list_source_reports,
    save_summary_snapshot,
)
from app.repositories.message_repository import get_message_detail
from app.repositories.message_repository import get_chat_name
from app.repositories.project_report_repository import (
    deserialize_missing_fields,
    deserialize_string_list,
)
from app.services.daily_report_publication_service import (
    confirm_summary,
    send_summary,
)
from app.services.daily_report_summary_service import build_daily_report_preview
from app.services.report_extraction_service import extract_and_save_report
from app.services.response_url_client import ResponseUrlClient
from app.services.people_confirmation_service import (
    create_confirmation_if_needed,
    handle_pending_confirmation,
)
from app.services.duplicate_report_service import auto_select_latest_reports


logger = logging.getLogger(__name__)

_COMMAND_PATTERN = re.compile(r"(?:生成|汇总|发送|查看).{0,18}(?:施工日报|项目日报|日报)")
_FULL_DATE_PATTERN = re.compile(
    r"(?P<year>\d{4})\s*(?:年|[-/.])\s*(?P<month>\d{1,2})"
    r"\s*(?:月|[-/.])\s*(?P<day>\d{1,2})\s*(?:日|号)?"
)
_MONTH_DAY_PATTERN = re.compile(
    r"(?<!\d)(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*[日号]"
)
_GREETING_PATTERN = re.compile(
    r"^(?:@[\w\u4e00-\u9fff-]+[\s,:：，]*)?"
    r"(?:你好|您好|嗨|哈喽|hello|hi|早上好|上午好|中午好|下午好|晚上好|"
    r"在吗|你在吗|有人吗|机器人在吗)"
    r"[呀啊吗呢哇~～!！?？。,.，\s]*$",
    re.IGNORECASE,
)


def schedule_chat_workflow_if_enabled(
    background_tasks: Any,
    request: Any,
    result: Any,
) -> None:
    settings = request.app.state.settings
    if (
        result.duplicate
        or not settings.enable_auto_chat_workflow
        or not getattr(result, "msgid", None)
    ):
        return
    background_tasks.add_task(run_chat_workflow, request.app, result.msgid)


def run_chat_workflow(app: Any, msgid: str) -> None:
    """在回调应答完成后处理，避免 LLM 调用拖慢企微回调。"""

    with app.state.session_factory() as session:
        message = get_message_detail(session, msgid)
        if message is None or not (message.response_url or "").strip():
            logger.warning("chat workflow skipped: no message/response_url msgid=%s", msgid)
            return
        client: ResponseUrlClient = app.state.response_url_client
        try:
            consumed, confirmation_reply = handle_pending_confirmation(
                session, message=message
            )
            if consumed:
                if confirmation_reply is not None:
                    _send_reply(client, message, confirmation_reply)
                return

            command_date = parse_report_command(
                message.text_content,
                now=message.received_at,
                timezone=app.state.settings.timezone,
            )
            if command_date is not None:
                _run_summary_command(
                    session,
                    message=message,
                    report_date=command_date,
                    extraction_client=app.state.report_extraction_client,
                    response_client=client,
                    public_base_url=app.state.settings.public_base_url,
                )
                return

            detection = message.report_detection
            if (
                detection is not None
                and detection.detection_status in {"report_candidate", "needs_review"}
                and message.msgtype in {"text", "mixed"}
                and message.text_content.strip()
            ):
                _run_report_intake(
                    session,
                    message=message,
                    extraction_client=app.state.report_extraction_client,
                    response_client=client,
                )
                return

            if is_greeting(message.text_content):
                reply = "你好，我是施工日报机器人，可以识别施工信息并生成日报汇总。"
            else:
                reply = (
                    "我是施工日报机器人：请发送施工信息，或发送“生成日报”进行汇总。"
                )
            _send_reply(client, message, reply)
        except Exception as exc:
            session.rollback()
            logger.exception(
                "chat workflow failed msgid=%s error_type=%s",
                msgid,
                type(exc).__name__,
            )
            _send_reply(
                client,
                message,
                "日报处理未完成，请稍后重试。原始消息已经安全保存，不需要重新提交正文。",
            )


def parse_report_command(
    content: str | None,
    *,
    now: datetime | None = None,
    timezone: str = "Asia/Shanghai",
) -> date | None:
    text = (content or "").strip()
    if not _COMMAND_PATTERN.search(text):
        return None
    zone = ZoneInfo(timezone)
    local_now = now.astimezone(zone) if now is not None else datetime.now(zone)

    full = _FULL_DATE_PATTERN.search(text)
    if full:
        return _safe_date(full.group("year"), full.group("month"), full.group("day"))
    month_day = _MONTH_DAY_PATTERN.search(text)
    if month_day:
        return _nearest_month_day(
            int(month_day.group("month")),
            int(month_day.group("day")),
            local_now.date(),
        )
    return local_now.date()


def is_greeting(content: str | None) -> bool:
    return bool(_GREETING_PATTERN.fullmatch((content or "").strip()))


def _run_report_intake(
    session: Session,
    *,
    message: Message,
    extraction_client: Any,
    response_client: ResponseUrlClient,
) -> None:
    if extraction_client is None:
        _send_reply(
            response_client,
            message,
            "日报已收到并保存，但服务端尚未配置大模型，暂时无法提取结构化字段。",
        )
        return
    report = extract_and_save_report(session, message, extraction_client)
    confirmation = create_confirmation_if_needed(
        session, message=message, report=report
    )
    if report.relevance_status == "ordinary_chat":
        content = "消息已收到。大模型复核后判断它不是施工日报，因此未纳入日报汇总。"
    elif report.relevance_status == "related_update":
        content = "施工补充消息已收到，需要后续关联到具体项目日报后才能进入汇总。"
    elif report.extraction_status == "completed":
        management = _people(report.management_count)
        conflict = next(
            (
                warning.removeprefix("管理人员数据冲突：")
                for warning in deserialize_string_list(
                    report.normalization_warnings
                )
                if warning.startswith("管理人员数据冲突：")
            ),
            None,
        )
        if conflict is not None:
            management = f"待确认（{conflict}）"
        content = (
            "### 日报已识别\n\n"
            f"- 项目：{report.project_name or '待确认'}\n"
            f"- 日期：{report.report_date.isoformat() if report.report_date else '待确认'}\n"
            f"- 管理人员：{management}\n"
            f"- 施工人员：{_people(report.worker_count)}\n\n"
            "需要汇总时，请发送“生成日报”。"
        )
    else:
        missing = "、".join(deserialize_missing_fields(report.missing_fields)) or "关键字段"
        content = f"日报已收到并完成初步提取，但仍缺少{missing}，暂不进入自动汇总。"
    if confirmation is not None:
        content += "\n\n" + confirmation
    _send_reply(response_client, message, content)


def _run_summary_command(
    session: Session,
    *,
    message: Message,
    report_date: date,
    extraction_client: Any,
    response_client: ResponseUrlClient,
    public_base_url: str = "",
) -> None:
    if extraction_client is not None:
        for candidate in _pending_messages(session, message.chatid):
            try:
                extract_and_save_report(session, candidate, extraction_client)
            except Exception as exc:
                logger.warning(
                    "command pre-extraction failed msgid=%s error_type=%s",
                    candidate.msgid,
                    type(exc).__name__,
                )

    reports = auto_select_latest_reports(
        session,
        list_source_reports(
            session, chatid=message.chatid, report_date=report_date
        ),
    )
    images = list_source_images(session, chatid=message.chatid)
    preview = build_daily_report_preview(
        reports,
        chatid=message.chatid,
        chat_name=get_chat_name(session, message.chatid),
        report_date=report_date,
        image_recognitions=images,
    )
    if _has_summary_blockers(preview):
        _send_reply(
            response_client,
            message,
            preview.markdown_content
            + "\n> 当前存在无有效日报、重复日报或未完成提取记录，已停止自动发布。",
        )
        return

    summary = save_summary_snapshot(
        session,
        preview,
        reports,
        public_base_url=public_base_url,
    )
    confirm_summary(
        session,
        summary_id=summary.id,
        confirmed_by=f"robot-command:{message.sender_userid}",
        confirmation_note="用户通过企业微信聊天指令触发自动汇总",
    )
    sent, attempt, _warnings = send_summary(
        session,
        summary_id=summary.id,
        trigger_msgid=message.msgid,
        client=response_client,
    )
    logger.info(
        "chat summary command completed msgid=%s summary_id=%s status=%s attempt_id=%s",
        message.msgid,
        sent.id,
        sent.publication_status,
        attempt.id,
    )
    if attempt.send_status != "sent":
        logger.error(
            "chat summary response_url send failed msgid=%s summary_id=%s error_type=%s",
            message.msgid,
            sent.id,
            attempt.error_type,
        )


def _pending_messages(session: Session, chatid: str) -> list[Message]:
    statement = (
        select(Message)
        .join(Message.report_detection)
        .outerjoin(ProjectReport, ProjectReport.message_id == Message.id)
        .options(joinedload(Message.report_detection))
        .where(
            Message.chatid == chatid,
            MessageReportDetection.detection_status.in_(
                ("report_candidate", "needs_review")
            ),
            Message.msgtype.in_(("text", "mixed")),
            (ProjectReport.id.is_(None) | ProjectReport.extraction_status.in_(("failed", "pending"))),
        )
        .order_by(Message.received_at.asc(), Message.id.asc())
    )
    return list(session.scalars(statement).unique().all())


def _has_summary_blockers(preview: Any) -> bool:
    return (
        preview.project_count < 1
        or bool(preview.duplicate_projects)
        or any(
            item.extraction_status != "completed"
            for item in preview.review_reports
        )
    )


def _send_reply(
    client: ResponseUrlClient,
    message: Message,
    content: str,
) -> None:
    result = client.send(
        response_url=(message.response_url or "").strip(),
        content=content,
    )
    logger.info(
        "chat reply completed msgid=%s transport=%s success=%s http_status=%s error_type=%s",
        message.msgid,
        result.transport,
        result.success,
        result.http_status_code,
        result.error_type,
    )


def _safe_date(year: str, month: str, day: str) -> date:
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        raise ValueError("日报命令包含无效日期") from None


def _nearest_month_day(month: int, day: int, today: date) -> date:
    candidates: list[date] = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue
    if not candidates:
        raise ValueError("日报命令包含无效日期")
    return min(candidates, key=lambda item: abs((item - today).days))


def _people(value: int | None) -> str:
    return "待确认" if value is None else f"{value} 人"
