"""完全本地、可解释的施工日报初步识别规则。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy.orm import Session

from app.models.database_models import Message, MessageReportDetection
from app.repositories.report_detection_repository import upsert_detection


DetectionStatus = Literal[
    "report_candidate", "needs_review", "ignored", "not_applicable"
]
DETECTOR_VERSION = "rules-v1"

_DATE_PATTERN = re.compile(
    r"(?:\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)"
    r"|(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2})"
    r"|(?<!\d)(?:\d{1,2}\s*月\s*\d{1,2}\s*日)"
)
_PERSONNEL_PATTERN = re.compile(
    r"(?:管理人员|施工人员|作业人员)\s*[:：]?\s*"
    r"[零〇一二三四五六七八九十百千万\d]+\s*人"
)
_EQUIPMENT_PATTERN = re.compile(
    r"(?:挖掘机|吊车|起重机|装载机|推土机|压路机|机械设备)\s*[:：]?\s*"
    r"[零〇一二三四五六七八九十百千万\d]+\s*(?:台|辆)"
)
_WEATHER_PATTERN = re.compile(
    r"(?:天气\s*[:：]?\s*(?:晴|多云|阴|小雨|中雨|大雨|阵雨|雪))"
    r"|(?:晴天|阴天|小雨|中雨|大雨|阵雨|多云)"
)
_CONSTRUCTION_CONTENT_TERMS = ("今日完成", "今日施工", "施工内容", "施工进度")
_ENGINEERING_TERMS = ("项目", "标段", "工区", "楼栋", "隧道", "桥梁")


@dataclass(frozen=True, slots=True)
class DetectionEvaluation:
    detection_status: DetectionStatus
    score: int
    is_report_candidate: bool
    matched_rules: list[str]
    reason: str


def evaluate_report_text(msgtype: str, text_content: str | None) -> DetectionEvaluation:
    text = (text_content or "").strip()
    if msgtype not in {"text", "mixed"} or not text:
        return DetectionEvaluation(
            detection_status="not_applicable",
            score=0,
            is_report_candidate=False,
            matched_rules=[],
            reason="消息没有可识别正文",
        )

    score = 0
    matched_rules: list[str] = []

    if "施工日报" in text:
        score += 4
        matched_rules.append("包含施工日报")
    elif "项目日报" in text:
        score += 4
        matched_rules.append("包含项目日报")

    if any(term in text for term in _CONSTRUCTION_CONTENT_TERMS):
        score += 2
        matched_rules.append("包含施工内容")

    if _DATE_PATTERN.search(text):
        score += 1
        matched_rules.append("包含日期")

    if _PERSONNEL_PATTERN.search(text):
        score += 1
        matched_rules.append("包含人员数量")

    if _EQUIPMENT_PATTERN.search(text):
        score += 1
        matched_rules.append("包含机械设备数量")

    if _WEATHER_PATTERN.search(text):
        score += 1
        matched_rules.append("包含天气")

    if any(term in text for term in _ENGINEERING_TERMS):
        score += 1
        matched_rules.append("包含工程场景词")

    if len(text) >= 30:
        score += 1
        matched_rules.append("正文长度不少于30个字符")

    if score >= 5:
        detection_status: DetectionStatus = "report_candidate"
        reason = "命中多项施工日报特征"
    elif score >= 2:
        detection_status = "needs_review"
        reason = "命中部分施工日报特征，信息不足，建议人工确认"
    else:
        detection_status = "ignored"
        reason = "未命中足够的施工日报特征"

    return DetectionEvaluation(
        detection_status=detection_status,
        score=score,
        is_report_candidate=detection_status == "report_candidate",
        matched_rules=matched_rules,
        reason=reason,
    )


def detect_and_save_report(
    session: Session, message: Message
) -> MessageReportDetection:
    evaluation = evaluate_report_text(message.msgtype, message.text_content)
    detected_at = datetime.now(timezone.utc)
    record = upsert_detection(
        session,
        message=message,
        detection_status=evaluation.detection_status,
        score=evaluation.score,
        is_report_candidate=evaluation.is_report_candidate,
        matched_rules=evaluation.matched_rules,
        reason=evaluation.reason,
        detector_version=DETECTOR_VERSION,
        detected_at=detected_at,
    )
    try:
        session.commit()
        session.refresh(record)
    except Exception:
        session.rollback()
        raise
    return record
