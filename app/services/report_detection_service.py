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
DETECTOR_VERSION = "rules-v4"

_DATE_PATTERN = re.compile(
    r"(?:\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)"
    r"|(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2})"
    r"|(?<!\d)(?:\d{1,2}\s*月\s*\d{1,2}\s*日)"
)
_NUMBER = r"[零〇一二两三四五六七八九十百千万\d]+"
_PERSONNEL_LABEL = (
    r"(?:管理(?:人员|员)?|施工(?:人员|员)?|作业(?:人员|员)?|现场人员|"
    r"现场工人(?:总计)?|工人|劳务(?:人员|工人)?|总施工人数|施工总人数|"
    r"总人数|人员总计|人员合计|班组人员|技工|普工)"
)
_PERSONNEL_PATTERN = re.compile(
    rf"{_PERSONNEL_LABEL}\s*(?:共|合计|总计)?\s*[:：]?\s*{_NUMBER}\s*(?:人|名)"
)
_EQUIPMENT_NAME = (
    r"(?:挖掘机|挖机|吊车|汽车吊|履带吊|起重机|塔吊|施工电梯|升降机|"
    r"装载机|铲车|推土机|压路机|摊铺机|泵车|混凝土泵|旋挖钻机|"
    r"钻机|打桩机|搅拌机|发电机|空压机|电焊机|焊机|叉车|"
    r"直臂车|曲臂车|高空作业车|渣土车|运输车|洒水车|机械设备)"
)
_EQUIPMENT_PATTERN = re.compile(
    rf"(?:{_EQUIPMENT_NAME}\s*(?:共|合计|总计)?\s*[:：]?\s*{_NUMBER}\s*(?:台|辆|套|部)"
    rf"|{_NUMBER}\s*(?:台|辆|套|部)\s*{_EQUIPMENT_NAME})"
)
_WEATHER_PATTERN = re.compile(
    r"(?:(?:天气|天气情况)\s*[:：]?\s*"
    r"(?:晴|多云|阴|雨|小雨|中雨|大雨|阵雨|雷阵雨|雪)"
    r"(?:\s*[-~～至到]?\s*-?\d{1,2}(?:\s*[-~～至到]\s*-?\d{1,2})?"
    r"\s*(?:℃|°C|°c|C|c))?)"
    r"|(?:晴天|阴天|小雨|中雨|大雨|阵雨|多云)"
)
_CONSTRUCTION_CONTENT_TERMS = (
    "今日完成",
    "当日完成",
    "完成情况",
    "今日施工",
    "当日施工",
    "现场施工",
    "施工内容",
    "施工进度",
    "施工情况",
    "工程进度",
    "工程进展",
    "现场进度",
    "现场进展",
    "工作内容",
    "作业内容",
    "形象进度",
)
_ENGINEERING_TERMS = (
    "项目",
    "工程",
    "标段",
    "工区",
    "施工现场",
    "楼栋",
    "隧道",
    "桥梁",
    "道路",
    "路基",
    "基坑",
    "厂房",
    "车站",
)
_REPORT_STRUCTURE_TERMS = (
    "机械情况",
    "人员情况",
    "明日计划",
    "安全情况",
    "质量情况",
    "产值完成",
)
_REPORT_QUERY_PATTERN = re.compile(
    r"(?:施工日报|项目日报|日报).{0,8}(?:呢|吗|发了没|发了吗|有吗|在哪|哪里|"
    r"出来了吗|什么时候发|怎么还没发)\s*[？?。！!]*$"
)
_STATUS_QUERY_PATTERN = re.compile(
    r"(?:施工|工程|现场|项目).{0,12}(?:进度|进展|情况|完成情况)"
    r".{0,8}(?:怎么样(?:了|啦|呢)?|如何|到哪了|到哪里了|完成了吗|结束了吗|有更新吗|"
    r"什么情况)\s*[？?。！!]*$"
)
_MOCK_REPORT_COMMAND_PATTERN = re.compile(
    r"(?:生成|汇总|发送|查看).{0,18}(?:施工日报|项目日报|日报)\s*[。！!]*$"
)


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
    elif "工程日报" in text or "现场日报" in text:
        score += 4
        matched_rules.append("包含工程日报表述")

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

    if any(term in text for term in _REPORT_STRUCTURE_TERMS):
        score += 1
        matched_rules.append("包含日报栏目")

    if len(text) >= 30:
        score += 1
        matched_rules.append("正文长度不少于30个字符")

    is_report_query = len(text) <= 30 and _REPORT_QUERY_PATTERN.search(text)
    if is_report_query:
        score -= 4
        matched_rules.append("疑似询问或催要日报")

    is_status_query = len(text) <= 40 and _STATUS_QUERY_PATTERN.search(text)
    if is_status_query:
        score -= 4
        matched_rules.append("疑似询问施工进展")

    is_report_command = (
        len(text) <= 50 and _MOCK_REPORT_COMMAND_PATTERN.search(text)
    )
    if is_report_command:
        score -= 4
        matched_rules.append("识别为日报生成命令")

    if score >= 5:
        detection_status: DetectionStatus = "report_candidate"
        reason = "命中多项施工日报特征"
    elif score >= 2:
        detection_status = "needs_review"
        reason = "命中部分施工日报特征，建议先进行大模型结构化复核"
    else:
        detection_status = "ignored"
        if is_report_command:
            reason = "识别为日报生成命令，不作为项目日报正文"
        elif is_report_query or is_status_query:
            reason = "疑似询问或催要日报，不作为日报正文"
        else:
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
