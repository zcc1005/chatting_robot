from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.main import create_app
from app.models.database_models import (
    DailyReportSummary,
    DailyReportSummaryItem,
    Message,
    MessageAttachment,
    ProjectReport,
    ReportEquipment,
    ReportWorkItem,
)
from tests.conftest import sqlite_url


REPORT_DATE = date(2026, 8, 6)
CHATID = "summary-group"


@pytest.fixture
def summary_client(tmp_path: Path):
    settings = Settings(
        app_env="development",
        enable_mock_api=True,
        enable_jjt_callback=False,
        database_url=sqlite_url(tmp_path / "summaries.db"),
        message_data_dir=tmp_path / "messages",
        public_base_url="https://reports.example.com",
    )
    with TestClient(create_app(settings)) as client:
        yield client


def seed_report(
    client: TestClient,
    *,
    msgid: str,
    project_name: str | None,
    extraction_status: str = "completed",
    chatid: str = CHATID,
    chat_name: str | None = None,
    report_date: date = REPORT_DATE,
    management_count: int | None = 2,
    worker_count: int | None = 10,
    equipment: list[tuple[str, int, str]] | None = None,
    work_items: list[tuple[str | None, str, str | None]] | None = None,
    missing_fields: list[str] | None = None,
    image_paths: list[Path] | None = None,
) -> int:
    equipment = (
        [("挖掘机", 1, "台")] if equipment is None else equipment
    )
    work_items = (
        [("1号桥", "桩基施工", "完成80%")] if work_items is None else work_items
    )
    if missing_fields is None:
        missing_fields = []
        if project_name is None:
            missing_fields.append("project_name")
        if management_count is None:
            missing_fields.append("management_count")
        if worker_count is None:
            missing_fields.append("worker_count")
        if not equipment:
            missing_fields.append("equipment")
        if not work_items:
            missing_fields.append("work_items")

    with client.app.state.session_factory() as session:
        message = Message(
            msgid=msgid,
            source="mock",
            chatid=chatid,
            chat_name=chat_name,
            chattype="group",
            sender_userid="summary-user",
            msgtype="text",
            text_content="结构化日报测试正文",
            raw_json="{}",
            received_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
            process_status="received",
        )
        session.add(message)
        session.flush()
        for index, image_path in enumerate(image_paths or []):
            message.attachments.append(
                MessageAttachment(
                    attachment_type="image",
                    remote_url=f"https://images.example.com/{msgid}-{index}.jpg",
                    local_path=str(image_path.resolve()),
                    download_status="downloaded",
                )
            )
        report = ProjectReport(
            msgid=msgid,
            message_id=message.id,
            project_name=project_name,
            report_date=report_date,
            weather="晴",
            management_count=management_count,
            worker_count=worker_count,
            tomorrow_plan="继续施工",
            safety_status="安全正常",
            quality_status="质量合格",
            missing_fields=json.dumps(missing_fields, ensure_ascii=False),
            confidence=0.9,
            extraction_status=extraction_status,
            extraction_source="manual",
            raw_extraction_json="{}",
        )
        report.equipment.extend(
            ReportEquipment(name=name, count=count, unit=unit, position=index)
            for index, (name, count, unit) in enumerate(equipment)
        )
        report.work_items.extend(
            ReportWorkItem(
                location=location,
                content=content,
                progress=progress,
                position=index,
            )
            for index, (location, content, progress) in enumerate(work_items)
        )
        session.add(report)
        session.commit()
        return report.id


def preview(client: TestClient, *, chatid: str = CHATID, day: date = REPORT_DATE):
    return client.post(
        "/api/daily-reports/preview",
        json={"chatid": chatid, "report_date": day.isoformat()},
    )


def test_single_project_summary(summary_client) -> None:
    seed_report(
        summary_client,
        msgid="single-project",
        project_name="桥梁一标",
        management_count=3,
        worker_count=20,
    )
    response = preview(summary_client)
    body = response.json()
    assert response.status_code == 200
    assert body["project_count"] == 1
    assert body["fully_complete_project_count"] == 1
    assert body["partial_project_count"] == 0
    assert body["project_count"] == (
        body["fully_complete_project_count"] + body["partial_project_count"]
    )
    assert body["management_total"] == 3
    assert body["worker_total"] == 20
    assert body["generation_status"] == "completed"


def test_multiple_projects_people_are_summed(summary_client) -> None:
    seed_report(
        summary_client,
        msgid="people-one",
        project_name="桥梁一标",
        management_count=3,
        worker_count=20,
    )
    seed_report(
        summary_client,
        msgid="people-two",
        project_name="隧道二标",
        management_count=4,
        worker_count=30,
    )
    body = preview(summary_client).json()
    assert body["project_count"] == 2
    assert body["management_total"] == 7
    assert body["worker_total"] == 50


def test_same_equipment_name_and_unit_are_merged(summary_client) -> None:
    seed_report(
        summary_client,
        msgid="equipment-one",
        project_name="桥梁一标",
        equipment=[("挖掘机", 2, "台")],
    )
    seed_report(
        summary_client,
        msgid="equipment-two",
        project_name="隧道二标",
        equipment=[("挖掘机", 3, "台")],
    )
    assert preview(summary_client).json()["equipment"] == [
        {"name": "挖掘机", "count": 5, "unit": "台"}
    ]


def test_equipment_with_different_units_is_not_merged(summary_client) -> None:
    seed_report(
        summary_client,
        msgid="unit-one",
        project_name="桥梁一标",
        equipment=[("吊车", 2, "台")],
    )
    seed_report(
        summary_client,
        msgid="unit-two",
        project_name="隧道二标",
        equipment=[("吊车", 3, "辆")],
    )
    assert preview(summary_client).json()["equipment"] == [
        {"name": "吊车", "count": 2, "unit": "台"},
        {"name": "吊车", "count": 3, "unit": "辆"},
    ]


def test_needs_review_does_not_enter_totals(summary_client) -> None:
    seed_report(
        summary_client,
        msgid="valid-report",
        project_name="桥梁一标",
        management_count=2,
        worker_count=10,
    )
    review_id = seed_report(
        summary_client,
        msgid="review-report",
        project_name="隧道二标",
        extraction_status="needs_review",
        management_count=100,
        worker_count=200,
    )
    body = preview(summary_client).json()
    assert body["project_count"] == 1
    assert body["fully_complete_project_count"] == 1
    assert body["partial_project_count"] == 0
    assert body["management_total"] == 2
    assert body["worker_total"] == 10
    assert body["generation_status"] == "needs_review"
    assert body["review_reports"][0]["project_report_id"] == review_id


def test_failed_report_does_not_enter_totals(summary_client) -> None:
    seed_report(
        summary_client,
        msgid="valid-for-failed",
        project_name="桥梁一标",
    )
    seed_report(
        summary_client,
        msgid="failed-report",
        project_name="隧道二标",
        extraction_status="failed",
        management_count=99,
        worker_count=99,
    )
    body = preview(summary_client).json()
    assert body["project_count"] == 1
    assert body["management_total"] == 2
    assert body["worker_total"] == 10
    assert any(item["extraction_status"] == "failed" for item in body["review_reports"])


def test_pending_report_is_listed_for_review_and_not_summed(summary_client) -> None:
    seed_report(
        summary_client,
        msgid="valid-for-pending",
        project_name="桥梁一标",
    )
    seed_report(
        summary_client,
        msgid="pending-report",
        project_name="隧道二标",
        extraction_status="pending",
        management_count=88,
        worker_count=88,
    )
    body = preview(summary_client).json()
    assert body["project_count"] == 1
    assert body["management_total"] == 2
    assert body["worker_total"] == 10
    assert any(
        item["extraction_status"] == "pending"
        for item in body["review_reports"]
    )


def test_partial_people_total_only_sums_known_values(summary_client) -> None:
    seed_report(
        summary_client,
        msgid="known-management",
        project_name="桥梁一标",
        management_count=5,
    )
    seed_report(
        summary_client,
        msgid="unknown-management",
        project_name="隧道二标",
        management_count=None,
    )
    body = preview(summary_client).json()
    assert body["management_total"] == 5
    assert body["project_count"] == 2
    assert body["fully_complete_project_count"] == 1
    assert body["partial_project_count"] == 1
    assert body["project_count"] == (
        body["fully_complete_project_count"] + body["partial_project_count"]
    )
    assert "仅汇总已知数据" in body["markdown_content"]


def test_missing_people_count_is_not_treated_as_zero(summary_client) -> None:
    seed_report(
        summary_client,
        msgid="missing-management",
        project_name="桥梁一标",
        management_count=None,
        worker_count=20,
    )
    body = preview(summary_client).json()
    assert body["project_count"] == 1
    assert body["fully_complete_project_count"] == 0
    assert body["partial_project_count"] == 1
    assert body["management_total"] is None
    assert body["worker_total"] == 20
    assert "management_count" in body["missing_data"][0]["fields"]
    assert body["missing_data"][0]["project_name"] == "桥梁一标"
    assert "管理人员数量" in body["markdown_content"]
    assert "management_count" not in body["markdown_content"]
    assert "项目“桥梁一标”" in body["markdown_content"]
    assert "missing-management" not in body["markdown_content"]
    assert "未完整统计" in body["markdown_content"]


def test_duplicate_project_automatically_uses_latest(summary_client) -> None:
    seed_report(
        summary_client,
        msgid="duplicate-one",
        project_name="重复桥梁项目",
        management_count=3,
        worker_count=20,
    )
    seed_report(
        summary_client,
        msgid="duplicate-two",
        project_name="重复桥梁项目",
        management_count=4,
        worker_count=30,
    )
    body = preview(summary_client).json()
    assert body["project_count"] == 1
    assert body["management_total"] == 4
    assert body["worker_total"] == 30
    assert body["generation_status"] == "completed"
    assert body["duplicate_projects"] == []


def test_same_project_in_different_groups_is_strictly_isolated(
    summary_client,
) -> None:
    seed_report(
        summary_client,
        msgid="group-a-old",
        chatid="unit-group-a",
        project_name="同名项目",
        management_count=2,
        worker_count=10,
    )
    seed_report(
        summary_client,
        msgid="group-a-new",
        chatid="unit-group-a",
        project_name="同名项目",
        management_count=3,
        worker_count=20,
    )
    seed_report(
        summary_client,
        msgid="group-b-only",
        chatid="unit-group-b",
        project_name="同名项目",
        management_count=8,
        worker_count=80,
    )

    group_a = preview(summary_client, chatid="unit-group-a").json()
    group_b = preview(summary_client, chatid="unit-group-b").json()

    assert (group_a["management_total"], group_a["worker_total"]) == (3, 20)
    assert (group_b["management_total"], group_b["worker_total"]) == (8, 80)
    assert group_a["project_count"] == group_b["project_count"] == 1


def test_real_group_name_is_displayed_without_internal_chatid(
    summary_client,
) -> None:
    seed_report(
        summary_client,
        msgid="named-group-report",
        chatid="internal-chat-id",
        chat_name="中交三航局三级单位群",
        project_name="桥梁一标",
    )
    body = preview(summary_client, chatid="internal-chat-id").json()
    assert body["chat_name"] == "中交三航局三级单位群"
    assert "单位群：中交三航局三级单位群" in body["markdown_content"]
    assert "internal-chat-id" not in body["markdown_content"]


def test_visual_report_has_charts_and_direct_message_images(
    summary_client, tmp_path: Path
) -> None:
    image_path = tmp_path / "messages" / "project-site.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"\xff\xd8\xff\xe0project-image")
    seed_report(
        summary_client,
        msgid="visual-source",
        project_name="可视化桥梁项目",
        management_count=5,
        worker_count=42,
        image_paths=[image_path],
    )
    saved = summary_client.post(
        "/api/daily-reports",
        json={"chatid": CHATID, "report_date": REPORT_DATE.isoformat()},
    ).json()

    assert saved["visual_report_url"].startswith(
        "https://reports.example.com/visual-reports/"
    )
    assert "点击查看图表与项目图片" in saved["markdown_content"]
    assert len(saved["projects"][0]["images"]) == 1
    assert saved["image_reviews"] == []

    page_path = urlsplit(saved["visual_report_url"]).path
    page = summary_client.get(page_path)
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert '<svg' in page.text
    assert "各项目人员分布" in page.text
    assert "可视化桥梁项目" in page.text
    assert "report-shell" in page.text
    attachment_id = saved["projects"][0]["images"][0]["attachment_id"]
    image = summary_client.get(f"{page_path}/images/{attachment_id}")
    assert image.status_code == 200
    assert image.content.startswith(b"\xff\xd8\xff")


def test_visual_report_token_cannot_read_another_summary_image(
    summary_client, tmp_path: Path
) -> None:
    first_image = tmp_path / "messages" / "first.jpg"
    second_image = tmp_path / "messages" / "second.jpg"
    first_image.parent.mkdir(parents=True, exist_ok=True)
    first_image.write_bytes(b"\xff\xd8\xfffirst")
    second_image.write_bytes(b"\xff\xd8\xffsecond")
    seed_report(
        summary_client,
        msgid="token-first",
        project_name="第一项目",
        chatid="token-group-a",
        image_paths=[first_image],
    )
    seed_report(
        summary_client,
        msgid="token-second",
        project_name="第二项目",
        chatid="token-group-b",
        image_paths=[second_image],
    )
    first = summary_client.post(
        "/api/daily-reports",
        json={"chatid": "token-group-a", "report_date": REPORT_DATE.isoformat()},
    ).json()
    second = summary_client.post(
        "/api/daily-reports",
        json={"chatid": "token-group-b", "report_date": REPORT_DATE.isoformat()},
    ).json()
    first_path = urlsplit(first["visual_report_url"]).path
    second_attachment = second["projects"][0]["images"][0]["attachment_id"]
    assert summary_client.get(
        f"{first_path}/images/{second_attachment}"
    ).status_code == 404


def test_no_valid_report_returns_review_preview(summary_client) -> None:
    body = preview(summary_client).json()
    assert body["project_count"] == 0
    assert body["management_total"] is None
    assert body["worker_total"] is None
    assert body["generation_status"] == "needs_review"
    assert any("未找到" in warning for warning in body["warnings"])


def test_preview_does_not_write_summary_tables(summary_client) -> None:
    seed_report(summary_client, msgid="preview-only", project_name="桥梁一标")
    assert preview(summary_client).status_code == 200
    with summary_client.app.state.session_factory() as session:
        summary_count = session.scalar(
            select(func.count()).select_from(DailyReportSummary)
        )
        item_count = session.scalar(
            select(func.count()).select_from(DailyReportSummaryItem)
        )
    assert summary_count == item_count == 0


def test_save_summary_snapshot(summary_client) -> None:
    seed_report(summary_client, msgid="saved-summary", project_name="桥梁一标")
    response = summary_client.post(
        "/api/daily-reports",
        json={"chatid": CHATID, "report_date": REPORT_DATE.isoformat()},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["id"] > 0
    assert body["project_count"] == 1
    assert body["fully_complete_project_count"] == 1
    assert body["partial_project_count"] == 0
    with summary_client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(DailyReportSummary)) == 1


def test_query_summary_detail_returns_markdown(summary_client) -> None:
    seed_report(summary_client, msgid="detail-source", project_name="桥梁一标")
    saved = summary_client.post(
        "/api/daily-reports",
        json={"chatid": CHATID, "report_date": REPORT_DATE.isoformat()},
    ).json()
    response = summary_client.get(f"/api/daily-reports/{saved['id']}")
    assert response.status_code == 200
    assert response.json()["markdown_content"] == saved["markdown_content"]
    assert response.json()["source_reports"][0]["msgid"] == "detail-source"


def test_saved_source_associations_are_correct(summary_client) -> None:
    first_id = seed_report(
        summary_client, msgid="source-one", project_name="桥梁一标"
    )
    second_id = seed_report(
        summary_client, msgid="source-two", project_name="隧道二标"
    )
    summary_id = summary_client.post(
        "/api/daily-reports",
        json={"chatid": CHATID, "report_date": REPORT_DATE.isoformat()},
    ).json()["id"]
    with summary_client.app.state.session_factory() as session:
        items = list(
            session.scalars(
                select(DailyReportSummaryItem)
                .where(DailyReportSummaryItem.summary_id == summary_id)
                .order_by(DailyReportSummaryItem.display_order)
            ).all()
        )
    assert [item.project_report_id for item in items] == [first_id, second_id]
    assert [item.display_order for item in items] == [0, 1]


def test_repeated_generation_creates_auditable_snapshots(summary_client) -> None:
    source_id = seed_report(
        summary_client, msgid="repeat-source", project_name="桥梁一标"
    )
    request_json = {"chatid": CHATID, "report_date": REPORT_DATE.isoformat()}
    first = summary_client.post("/api/daily-reports", json=request_json).json()
    second = summary_client.post("/api/daily-reports", json=request_json).json()
    assert first["id"] != second["id"]
    with summary_client.app.state.session_factory() as session:
        summaries = session.scalar(
            select(func.count()).select_from(DailyReportSummary)
        )
        source_links = list(
            session.scalars(
                select(DailyReportSummaryItem.project_report_id).order_by(
                    DailyReportSummaryItem.summary_id
                )
            ).all()
        )
    assert summaries == 2
    assert source_links == [source_id, source_id]


def test_markdown_contains_required_sections_and_content(summary_client) -> None:
    seed_report(
        summary_client,
        msgid="markdown-source",
        project_name="桥梁一标",
        management_count=3,
        worker_count=20,
        equipment=[("挖掘机", 2, "台")],
        work_items=[("1号桥", "桩基浇筑", "完成80%")],
    )
    markdown = preview(summary_client).json()["markdown_content"]
    for expected in (
        "# 2026-08-06 施工日报汇总预览",
        "## 总体概览",
        "纳入汇总项目数：1",
        "字段完整项目数：1",
        "存在缺失信息项目数：0",
        "管理人员总数：3 人",
        "施工人员总数：20 人",
        "## 机械设备汇总",
        "挖掘机：2 台",
        "## 各项目施工情况",
        "桩基浇筑",
        "## 明日计划",
        "## 安全和质量情况",
        "## 待确认和缺失信息",
    ):
        assert expected in markdown
    assert "群聊：" not in markdown
    assert CHATID not in markdown


def test_summary_list_filters(summary_client) -> None:
    seed_report(summary_client, msgid="filter-source", project_name="桥梁一标")
    summary_client.post(
        "/api/daily-reports",
        json={"chatid": CHATID, "report_date": REPORT_DATE.isoformat()},
    )
    response = summary_client.get(
        "/api/daily-reports",
        params={
            "chatid": CHATID,
            "report_date": REPORT_DATE.isoformat(),
            "generation_status": "completed",
        },
    )
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    item = response.json()["items"][0]
    assert item["project_count"] == 1
    assert item["fully_complete_project_count"] == 1
    assert item["partial_project_count"] == 0


def test_missing_summary_returns_404(summary_client) -> None:
    response = summary_client.get("/api/daily-reports/999999")
    assert response.status_code == 404
