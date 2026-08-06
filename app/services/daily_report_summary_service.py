"""按群聊和日期确定性汇总结构化日报，并生成 Markdown 预览。"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from app.models.database_models import ProjectReport
from app.models.daily_report_schemas import (
    DailyReportPreviewResponse,
    DuplicateProject,
    SummaryEquipment,
    SummaryMissingData,
    SummaryProjectDetail,
    SummaryReviewReport,
    SummarySourceReport,
)
from app.models.report_schemas import EXTRACTED_FIELD_NAMES, ExtractedWorkItem
from app.repositories.project_report_repository import deserialize_missing_fields


class DailyReportSummaryError(ValueError):
    """结构化来源数据不满足确定性汇总约束。"""


def build_daily_report_preview(
    reports: list[ProjectReport],
    *,
    chatid: str,
    report_date: date,
) -> DailyReportPreviewResponse:
    _validate_nonnegative_quantities(reports)

    duplicate_groups = _find_duplicate_groups(reports)
    duplicate_ids = {
        report.id for group in duplicate_groups for report in group
    }
    review_reasons: dict[int, list[str]] = defaultdict(list)
    warnings: list[str] = []

    if not reports:
        warnings.append("未找到指定群聊和日期的结构化日报")

    for report in reports:
        if report.extraction_status != "completed":
            reason = f"状态为 {report.extraction_status}"
            review_reasons[report.id].append(reason)
            warnings.append(
                f"日报 {report.msgid} {reason}，未进入数值汇总"
            )

    duplicate_projects: list[DuplicateProject] = []
    for group in duplicate_groups:
        project_name = group[0].project_name
        if project_name is None:
            continue
        sources = [_source_report(report, False) for report in group]
        duplicate_projects.append(
            DuplicateProject(
                project_name=project_name,
                report_date=report_date,
                reports=sources,
            )
        )
        related_msgids = "、".join(report.msgid for report in group)
        warnings.append(
            f"项目 {project_name} 存在重复日报（{related_msgids}），相关数据未进入汇总"
        )
        for report in group:
            review_reasons[report.id].append("同项目同日期存在重复日报")

    included_reports: list[ProjectReport] = []
    for report in reports:
        if report.extraction_status != "completed" or report.id in duplicate_ids:
            continue
        if report.project_name is None or not report.work_items:
            review_reasons[report.id].append("completed 日报缺少关键字段")
            warnings.append(
                f"日报 {report.msgid} 缺少项目名称或施工内容，未进入数值汇总"
            )
            continue
        included_reports.append(report)

    included_ids = {report.id for report in included_reports}
    missing_data: list[SummaryMissingData] = []
    for report in reports:
        fields = _actual_missing_fields(report)
        if fields:
            missing_data.append(
                SummaryMissingData(
                    project_report_id=report.id,
                    msgid=report.msgid,
                    project_name=report.project_name,
                    fields=fields,
                )
            )
            warnings.append(
                f"日报 {report.msgid} 缺失字段：{', '.join(fields)}"
            )

    management_values = [
        report.management_count
        for report in included_reports
        if report.management_count is not None
    ]
    worker_values = [
        report.worker_count
        for report in included_reports
        if report.worker_count is not None
    ]
    management_has_missing = any(
        report.management_count is None for report in included_reports
    )
    worker_has_missing = any(
        report.worker_count is None for report in included_reports
    )
    if management_has_missing:
        warnings.append("部分有效日报缺少管理人员数量，管理人员合计仅包含已知数据")
    if worker_has_missing:
        warnings.append("部分有效日报缺少施工人员数量，施工人员合计仅包含已知数据")

    equipment_totals: dict[tuple[str, str], int] = defaultdict(int)
    for report in included_reports:
        for item in report.equipment:
            equipment_totals[(item.name, item.unit)] += item.count
    equipment = [
        SummaryEquipment(name=name, count=count, unit=unit)
        for (name, unit), count in sorted(equipment_totals.items())
    ]

    projects = [
        SummaryProjectDetail(
            project_report_id=report.id,
            msgid=report.msgid,
            project_name=report.project_name or "",
            weather=report.weather,
            work_items=[
                ExtractedWorkItem(
                    location=item.location,
                    content=item.content,
                    progress=item.progress,
                )
                for item in report.work_items
            ],
            tomorrow_plan=report.tomorrow_plan,
            safety_status=report.safety_status,
            quality_status=report.quality_status,
            missing_fields=_actual_missing_fields(report),
        )
        for report in included_reports
    ]
    review_reports = [
        SummaryReviewReport(
            project_report_id=report.id,
            msgid=report.msgid,
            project_name=report.project_name,
            extraction_status=report.extraction_status,
            review_reason="；".join(review_reasons[report.id]),
        )
        for report in reports
        if report.id in review_reasons
    ]
    source_reports = [
        _source_report(report, report.id in included_ids) for report in reports
    ]

    if not included_reports:
        warnings.append("没有可进入数值汇总的 completed 日报")

    management_total = sum(management_values) if management_values else None
    worker_total = sum(worker_values) if worker_values else None
    fully_complete_project_count = sum(
        1 for project in projects if not project.missing_fields
    )
    partial_project_count = len(projects) - fully_complete_project_count
    generation_status = "needs_review" if warnings else "completed"
    markdown_content = render_markdown(
        chatid=chatid,
        report_date=report_date,
        project_count=len(included_reports),
        fully_complete_project_count=fully_complete_project_count,
        partial_project_count=partial_project_count,
        management_total=management_total,
        worker_total=worker_total,
        management_has_missing=management_has_missing,
        worker_has_missing=worker_has_missing,
        equipment=equipment,
        projects=projects,
        review_reports=review_reports,
        duplicate_projects=duplicate_projects,
        missing_data=missing_data,
        warnings=warnings,
    )
    return DailyReportPreviewResponse(
        chatid=chatid,
        report_date=report_date,
        project_count=len(included_reports),
        fully_complete_project_count=fully_complete_project_count,
        partial_project_count=partial_project_count,
        management_total=management_total,
        worker_total=worker_total,
        equipment=equipment,
        projects=projects,
        missing_data=missing_data,
        review_reports=review_reports,
        duplicate_projects=duplicate_projects,
        source_reports=source_reports,
        generation_status=generation_status,
        warnings=warnings,
        markdown_content=markdown_content,
    )


def render_markdown(
    *,
    chatid: str,
    report_date: date,
    project_count: int,
    fully_complete_project_count: int,
    partial_project_count: int,
    management_total: int | None,
    worker_total: int | None,
    management_has_missing: bool,
    worker_has_missing: bool,
    equipment: list[SummaryEquipment],
    projects: list[SummaryProjectDetail],
    review_reports: list[SummaryReviewReport],
    duplicate_projects: list[DuplicateProject],
    missing_data: list[SummaryMissingData],
    warnings: list[str],
) -> str:
    lines = [
        f"# {report_date.isoformat()} 施工日报汇总预览",
        "",
        f"- 群聊：{_one_line(chatid)}",
        f"- 日期：{report_date.isoformat()}",
        "",
        "## 总体概览",
        "",
        f"- 纳入汇总项目数：{project_count}",
        f"- 字段完整项目数：{fully_complete_project_count}",
        f"- 存在缺失信息项目数：{partial_project_count}",
        f"- 管理人员总数：{_format_people(management_total, management_has_missing)}",
        f"- 施工人员总数：{_format_people(worker_total, worker_has_missing)}",
        "",
        "## 机械设备汇总",
        "",
    ]
    if equipment:
        lines.extend(
            f"- {_one_line(item.name)}：{item.count} {_one_line(item.unit)}"
            for item in equipment
        )
    else:
        lines.append("- 暂无可统计机械数据")

    lines.extend(["", "## 各项目施工情况", ""])
    if projects:
        for index, project in enumerate(projects, start=1):
            lines.extend(
                [
                    f"### {index}. {_one_line(project.project_name)}",
                    "",
                    f"- 天气：{_optional_text(project.weather)}",
                    "- 今日施工：",
                ]
            )
            for item in project.work_items:
                location = _optional_text(item.location)
                progress = (
                    f"（进度：{_one_line(item.progress)}）"
                    if item.progress is not None
                    else ""
                )
                lines.append(
                    f"  - {location}：{_one_line(item.content)}{progress}"
                )
            lines.append("")
    else:
        lines.extend(["- 暂无可展示的有效项目施工内容", ""])

    lines.extend(["## 明日计划", ""])
    if projects:
        lines.extend(
            f"- {_one_line(project.project_name)}：{_optional_text(project.tomorrow_plan)}"
            for project in projects
        )
    else:
        lines.append("- 暂无")

    lines.extend(["", "## 安全和质量情况", ""])
    if projects:
        for project in projects:
            lines.append(
                f"- {_one_line(project.project_name)}：安全："
                f"{_optional_text(project.safety_status)}；质量："
                f"{_optional_text(project.quality_status)}"
            )
    else:
        lines.append("- 暂无")

    lines.extend(["", "## 待确认和缺失信息", ""])
    if warnings:
        lines.extend(f"- {_one_line(warning)}" for warning in warnings)
    else:
        lines.append("- 无")

    if duplicate_projects:
        lines.extend(["", "### 重复项目", ""])
        for duplicate in duplicate_projects:
            msgids = "、".join(item.msgid for item in duplicate.reports)
            lines.append(f"- {_one_line(duplicate.project_name)}：{_one_line(msgids)}")
    if review_reports:
        lines.extend(["", "### 待人工确认日报", ""])
        lines.extend(
            f"- {item.msgid}：{_one_line(item.review_reason)}"
            for item in review_reports
        )
    if missing_data:
        lines.extend(["", "### 缺失字段", ""])
        lines.extend(
            f"- {item.msgid}：{', '.join(item.fields)}" for item in missing_data
        )
    return "\n".join(lines).rstrip() + "\n"


def _find_duplicate_groups(
    reports: list[ProjectReport],
) -> list[list[ProjectReport]]:
    groups: dict[str, list[ProjectReport]] = defaultdict(list)
    for report in reports:
        if report.project_name is not None:
            groups[report.project_name].append(report)
    return [groups[name] for name in sorted(groups) if len(groups[name]) > 1]


def _actual_missing_fields(report: ProjectReport) -> list[str]:
    stored = set(deserialize_missing_fields(report.missing_fields))
    actual = {
        "project_name": report.project_name,
        "report_date": report.report_date,
        "weather": report.weather,
        "management_count": report.management_count,
        "worker_count": report.worker_count,
        "equipment": report.equipment or None,
        "work_items": report.work_items or None,
        "tomorrow_plan": report.tomorrow_plan,
        "safety_status": report.safety_status,
        "quality_status": report.quality_status,
    }
    stored.update(name for name, value in actual.items() if value is None)
    return [name for name in EXTRACTED_FIELD_NAMES if name in stored]


def _source_report(
    report: ProjectReport, included_in_totals: bool
) -> SummarySourceReport:
    return SummarySourceReport(
        project_report_id=report.id,
        msgid=report.msgid,
        project_name=report.project_name,
        extraction_status=report.extraction_status,
        included_in_totals=included_in_totals,
    )


def _validate_nonnegative_quantities(reports: list[ProjectReport]) -> None:
    for report in reports:
        for field_name in ("management_count", "worker_count"):
            value = getattr(report, field_name)
            if value is not None and value < 0:
                raise DailyReportSummaryError(
                    f"日报 {report.msgid} 包含负数人员数量"
                )
        if any(item.count < 0 for item in report.equipment):
            raise DailyReportSummaryError(f"日报 {report.msgid} 包含负数机械数量")


def _format_people(value: int | None, has_missing: bool) -> str:
    if value is None:
        return "未完整统计"
    suffix = "（存在缺失，仅汇总已知数据）" if has_missing else ""
    return f"{value} 人{suffix}"


def _optional_text(value: str | None) -> str:
    return _one_line(value) if value is not None else "待确认"


def _one_line(value: str) -> str:
    return " ".join(value.replace("\r", "\n").splitlines()).strip()
