"""单条施工日报结构化提取编排；大模型客户端不接触数据库。"""

from __future__ import annotations

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models.database_models import Message, ProjectReport
from app.models.report_schemas import ReportExtractionPayload
from app.repositories.project_report_repository import (
    save_failure,
    save_success,
    start_extraction,
)
from app.services.llm_extraction_client import (
    LLMClientTimeout,
    ReportExtractionClient,
)


class ReportNotEligibleError(ValueError):
    """消息不是允许提取的日报候选。"""


class ReportExtractionFailedError(RuntimeError):
    """提取失败且 failed 状态已保存。"""


class ReportExtractionTimeoutError(ReportExtractionFailedError):
    """提取调用超时。"""


def extract_and_save_report(
    session: Session,
    message: Message,
    client: ReportExtractionClient,
) -> ProjectReport:
    detection = message.report_detection
    if (
        detection is None
        or detection.detection_status not in {"report_candidate", "needs_review"}
        or message.msgtype not in {"text", "mixed"}
        or not message.text_content.strip()
    ):
        raise ReportNotEligibleError(
            "仅 report_candidate 或 needs_review 的正文消息允许提取"
        )

    record = start_extraction(session, message)
    try:
        raw_response = client.extract(message.text_content)
    except LLMClientTimeout as exc:
        error_message = str(exc) or "大模型调用超时"
        save_failure(session, record, error_message=error_message)
        raise ReportExtractionTimeoutError(error_message) from None
    except TimeoutError:
        save_failure(session, record, error_message="大模型调用超时")
        raise ReportExtractionTimeoutError("大模型调用超时") from None
    except Exception as exc:
        save_failure(session, record, error_message="大模型调用失败")
        raise ReportExtractionFailedError("大模型调用失败") from exc

    try:
        payload = ReportExtractionPayload.model_validate_json(raw_response)
    except ValidationError as exc:
        error_types = {item["type"] for item in exc.errors()}
        error_message = (
            "大模型返回非法 JSON"
            if "json_invalid" in error_types
            else "大模型返回字段校验失败"
        )
        save_failure(
            session,
            record,
            error_message=error_message,
            raw_response=raw_response,
        )
        raise ReportExtractionFailedError(error_message) from None

    return save_success(session, record, payload, raw_response)
