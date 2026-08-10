"""施工图片识别编排与可解释项目关联。"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models.database_models import (
    Message,
    MessageAttachment,
    MessageImageRecognition,
    ProjectReport,
    ProjectReportImage,
)
from app.models.image_recognition_schemas import ImageRecognitionPayload
from app.repositories.image_recognition_repository import (
    list_candidate_reports,
    save_association,
    save_failure,
    save_success,
    start_recognition,
)
from app.services.image_recognition_client import (
    ImageContentLoader,
    ImageRecognitionClient,
    ImageRecognitionClientTimeout,
)


CONSTRUCTION_KEYWORDS = (
    "模板",
    "钢筋",
    "混凝土",
    "砌筑",
    "安装",
    "浇筑",
    "开挖",
    "回填",
    "桩基",
    "梁板",
    "支架",
    "防水",
    "管线",
    "焊接",
    "墩柱",
    "隧道",
    "桥梁",
)


class ImageMessageNotEligibleError(ValueError):
    pass


def recognize_message_images(
    session: Session,
    message: Message,
    *,
    loader: ImageContentLoader,
    client: ImageRecognitionClient,
    timezone: str,
) -> list[MessageImageRecognition]:
    images = [
        item for item in message.attachments if item.attachment_type == "image"
    ]
    if message.chattype != "group" or not images:
        raise ImageMessageNotEligibleError("仅群聊图片消息允许图片识别")

    results: list[MessageImageRecognition] = []
    for attachment in images:
        record = start_recognition(session, attachment)
        image_sha256: str | None = None
        raw_response: str | None = None
        try:
            image = loader.load(attachment)
            image_sha256 = image.sha256
            raw_response = client.recognize(image)
            payload = ImageRecognitionPayload.model_validate_json(raw_response)
            record = save_success(
                session,
                record,
                payload=payload,
                image_sha256=image.sha256,
                raw_response=raw_response,
            )
            _associate_recognition(
                session,
                message=message,
                recognition=record,
                timezone=timezone,
            )
        except ImageRecognitionClientTimeout:
            session.rollback()
            record = save_failure(
                session,
                record,
                error_message="图片识别调用超时",
                image_sha256=image_sha256,
            )
        except ValidationError as exc:
            session.rollback()
            error_types = {item["type"] for item in exc.errors()}
            error_message = (
                "图片识别返回非法 JSON"
                if "json_invalid" in error_types
                else "图片识别返回字段校验失败"
            )
            record = save_failure(
                session,
                record,
                error_message=error_message,
                image_sha256=image_sha256,
                raw_response=raw_response,
            )
        except Exception:
            session.rollback()
            record = save_failure(
                session,
                record,
                error_message="图片读取或识别失败",
                image_sha256=image_sha256,
            )
        results.append(record)
    return results


def manually_associate_image(
    session: Session,
    *,
    recognition: MessageImageRecognition,
    project_report: ProjectReport | None,
) -> ProjectReportImage:
    image_message = recognition.attachment.message
    if project_report is not None and (
        project_report.message.chatid != image_message.chatid
    ):
        raise ValueError("图片与目标项目日报不属于同一群聊")
    if project_report is None:
        return save_association(
            session,
            recognition,
            project_report_id=None,
            association_status="unmatched",
            score=0,
            matched_rules=["人工取消项目关联"],
            candidate_scores=[],
            reason="已由人工取消项目关联",
        )
    return save_association(
        session,
        recognition,
        project_report_id=project_report.id,
        association_status="manual",
        score=0,
        matched_rules=["人工指定项目"],
        candidate_scores=[],
        reason="已由人工确认图片所属项目",
    )


def _associate_recognition(
    session: Session,
    *,
    message: Message,
    recognition: MessageImageRecognition,
    timezone: str,
) -> ProjectReportImage:
    target_date = recognition.report_date or message.received_at.astimezone(
        ZoneInfo(timezone)
    ).date()
    candidates = list_candidate_reports(
        session, chatid=message.chatid, report_date=target_date
    )
    ranked = [
        _score_candidate(message, recognition, report) for report in candidates
    ]
    ranked.sort(key=lambda item: (-item["score"], item["project_report_id"]))
    if not ranked:
        return save_association(
            session,
            recognition,
            project_report_id=None,
            association_status="unmatched",
            score=0,
            matched_rules=[],
            candidate_scores=[],
            reason="同群聊同日期没有可关联的结构化项目日报",
        )

    best = ranked[0]
    second_score = ranked[1]["score"] if len(ranked) > 1 else -1
    has_semantic_evidence = bool(best["semantic_evidence"])
    unique_margin = best["score"] - second_score
    if best["score"] >= 5 and has_semantic_evidence and unique_margin >= 2:
        status = "linked"
        project_report_id = int(best["project_report_id"])
        reason = "命中项目内容证据，并结合日期、发送人或时间完成自动关联"
    elif best["score"] >= 3:
        status = "needs_review"
        project_report_id = int(best["project_report_id"])
        reason = (
            "存在候选项目，但内容证据不足或多个候选分数接近，需要人工确认"
        )
    else:
        status = "unmatched"
        project_report_id = None
        reason = "未找到足够可信的项目关联证据"
    return save_association(
        session,
        recognition,
        project_report_id=project_report_id,
        association_status=status,
        score=int(best["score"]),
        matched_rules=list(best["matched_rules"]),
        candidate_scores=[_public_candidate(item) for item in ranked],
        reason=reason,
    )


def _score_candidate(
    message: Message,
    recognition: MessageImageRecognition,
    report: ProjectReport,
) -> dict[str, object]:
    score = 0
    rules: list[str] = []
    semantic_evidence = False

    if recognition.report_date == report.report_date:
        score += 1
        rules.append("图片日期与日报日期一致 +1")

    image_project = _normalized_text(recognition.project_name)
    report_project = _normalized_text(report.project_name)
    if image_project and report_project:
        similarity = SequenceMatcher(None, image_project, report_project).ratio()
        if image_project == report_project:
            score += 5
            rules.append("图片项目名称完全匹配 +5")
            semantic_evidence = True
        elif similarity >= 0.65:
            score += 4
            rules.append("图片项目名称高度相似 +4")
            semantic_evidence = True
        elif similarity >= 0.40:
            score += 3
            rules.append("图片项目名称部分相似 +3")
            semantic_evidence = True

    image_keywords = _construction_keywords(
        " ".join(
            item
            for item in (
                recognition.construction_content,
                recognition.ocr_text,
                recognition.scene_description,
            )
            if item
        )
    )
    report_keywords = _construction_keywords(
        " ".join(item.content for item in report.work_items)
    )
    overlap = sorted(image_keywords & report_keywords)
    if overlap:
        score += 2
        rules.append(f"施工关键词一致（{'、'.join(overlap)}） +2")
        semantic_evidence = True

    if message.sender_userid == report.message.sender_userid:
        score += 2
        rules.append("图片与日报发送人相同 +2")

    seconds = abs(
        (message.received_at - report.message.received_at).total_seconds()
    )
    if seconds <= 600:
        score += 2
        rules.append("图片与日报发送时间相差不超过10分钟 +2")
    elif seconds <= 1800:
        score += 1
        rules.append("图片与日报发送时间相差不超过30分钟 +1")

    return {
        "project_report_id": report.id,
        "msgid": report.msgid,
        "project_name": report.project_name,
        "score": score,
        "matched_rules": rules,
        "semantic_evidence": semantic_evidence,
    }


def _public_candidate(item: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in item.items()
        if key != "semantic_evidence"
    }


def _normalized_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", value).lower()
    for suffix in ("建设项目", "工程项目", "项目", "工程"):
        normalized = normalized.replace(suffix, "")
    return normalized


def _construction_keywords(value: str) -> set[str]:
    return {keyword for keyword in CONSTRUCTION_KEYWORDS if keyword in value}
