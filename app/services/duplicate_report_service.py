"""同群、同项目、同日期的重复日报自动取最新一条。"""

from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy.orm import Session

from app.models.database_models import ProjectReport


logger = logging.getLogger(__name__)


def auto_select_latest_reports(
    session: Session, reports: list[ProjectReport]
) -> list[ProjectReport]:
    """保留每组最新记录，并持久化旧记录的替代关系。"""

    groups: dict[tuple[str, object], list[ProjectReport]] = defaultdict(list)
    for report in reports:
        if report.project_name is not None and report.report_date is not None:
            groups[(report.project_name, report.report_date)].append(report)

    superseded_ids: set[int] = set()
    changed = False
    for (project_name, report_date), group in groups.items():
        if len(group) < 2:
            continue
        latest = max(
            group,
            key=lambda item: (item.message.received_at, item.id),
        )
        for report in group:
            if report.id == latest.id:
                continue
            report.superseded_by_report_id = latest.id
            superseded_ids.add(report.id)
            changed = True
        logger.info(
            "duplicate reports auto-resolved project=%s report_date=%s "
            "selected_report_id=%s ignored_count=%s",
            project_name,
            report_date,
            latest.id,
            len(group) - 1,
        )
    if changed:
        session.commit()
    return [report for report in reports if report.id not in superseded_ids]
