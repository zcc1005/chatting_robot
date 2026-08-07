from __future__ import annotations

import json
import socket
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.main import create_app
from app.models.database_models import (
    DailyReportSendAttempt,
    DailyReportSummary,
    DailyReportSummaryItem,
    Message,
    ProjectReport,
    ReportEquipment,
    ReportWorkItem,
)
from app.services.response_url_client import MockResponseUrlClient
from tests.conftest import sqlite_url


CHATID = "publication-group"
REPORT_DATE = date(2026, 8, 6)


def _settings(tmp_path: Path, name: str = "publication.db") -> Settings:
    return Settings(
        app_env="development",
        enable_mock_api=True,
        enable_jjt_callback=False,
        database_url=sqlite_url(tmp_path / name),
        message_data_dir=tmp_path / "messages",
    )


@pytest.fixture
def publication_client(tmp_path: Path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        yield client


@pytest.fixture
def failed_send_client(tmp_path: Path):
    with TestClient(
        create_app(
            _settings(tmp_path, "failed-send.db"),
            response_url_client=MockResponseUrlClient(force_failure=True),
        )
    ) as client:
        yield client


def _seed_completed_report(client: TestClient, *, msgid: str = "source-report") -> int:
    with client.app.state.session_factory() as session:
        message = Message(
            msgid=msgid,
            source="mock",
            chatid=CHATID,
            chattype="group",
            sender_userid="report-user",
            msgtype="text",
            text_content="施工日报结构化来源",
            raw_json="{}",
            received_at=datetime.now(timezone.utc),
            process_status="received",
        )
        session.add(message)
        session.flush()
        report = ProjectReport(
            msgid=msgid,
            message_id=message.id,
            project_name="桥梁一标",
            report_date=REPORT_DATE,
            weather="晴",
            management_count=3,
            worker_count=20,
            tomorrow_plan="继续施工",
            safety_status="安全正常",
            quality_status="质量合格",
            missing_fields="[]",
            confidence=1.0,
            extraction_status="completed",
            extraction_source="manual",
            raw_extraction_json="{}",
        )
        report.equipment.append(
            ReportEquipment(name="挖掘机", count=2, unit="台", position=0)
        )
        report.work_items.append(
            ReportWorkItem(
                location="1号桥",
                content="桩基施工",
                progress="完成80%",
                position=0,
            )
        )
        session.add(report)
        session.commit()
        return report.id


def _save_summary(client: TestClient, *, with_report: bool = True) -> dict:
    if with_report:
        _seed_completed_report(client)
    response = client.post(
        "/api/daily-reports",
        json={"chatid": CHATID, "report_date": REPORT_DATE.isoformat()},
    )
    assert response.status_code == 200
    return response.json()


def _confirm(client: TestClient, summary_id: int) -> dict:
    response = client.post(
        f"/api/daily-reports/{summary_id}/confirm",
        json={
            "confirmed_by": "admin-user-001",
            "confirmation_note": "已人工核对",
        },
    )
    assert response.status_code == 200
    return response.json()


def _seed_trigger(
    client: TestClient,
    *,
    msgid: str = "generate-report-command-001",
    chatid: str = CHATID,
    response_url: str | None = None,
    received_at: str | None = None,
) -> None:
    payload = {
        "msgid": msgid,
        "aibotid": "bot-001",
        "chatid": chatid,
        "chattype": "group",
        "from": {"userid": "admin-user-001"},
        "msgtype": "text",
        "text": {"content": "@机器人 生成2026年8月6日施工日报"},
    }
    if response_url is not None:
        payload["response_url"] = response_url
    if received_at is not None:
        payload["received_at"] = received_at
    response = client.post("/api/dev/mock-message", json=payload)
    assert response.status_code == 200


def _prepare_confirmed(client: TestClient) -> tuple[int, str]:
    summary_id = _save_summary(client)["id"]
    _confirm(client, summary_id)
    trigger_msgid = "generate-report-command-001"
    _seed_trigger(
        client,
        msgid=trigger_msgid,
        response_url=f"mock://response-url/{trigger_msgid}",
    )
    return summary_id, trigger_msgid


def _send(client: TestClient, summary_id: int, trigger_msgid: str):
    return client.post(
        f"/api/daily-reports/{summary_id}/send",
        json={"trigger_msgid": trigger_msgid},
    )


def test_draft_summary_can_be_confirmed(publication_client) -> None:
    summary = _save_summary(publication_client)
    assert summary["publication_status"] == "draft"
    body = _confirm(publication_client, summary["id"])
    assert body["publication_status"] == "confirmed"
    assert body["confirmed_by"] == "admin-user-001"
    assert body["confirmed_at"] is not None
    assert body["confirmation_note"] == "已人工核对"


def test_needs_review_summary_can_be_manually_confirmed(publication_client) -> None:
    summary = _save_summary(publication_client, with_report=False)
    assert summary["generation_status"] == "needs_review"
    confirmed = _confirm(publication_client, summary["id"])
    assert confirmed["generation_status"] == "needs_review"
    assert confirmed["publication_status"] == "confirmed"


def test_confirmed_summary_can_return_to_draft(publication_client) -> None:
    summary_id = _save_summary(publication_client)["id"]
    _confirm(publication_client, summary_id)
    response = publication_client.post(
        f"/api/daily-reports/{summary_id}/unconfirm"
    )
    body = response.json()
    assert response.status_code == 200
    assert body["publication_status"] == "draft"
    assert body["confirmed_by"] is None
    assert body["confirmed_at"] is None
    assert body["confirmation_note"] is None


def test_sent_summary_cannot_be_unconfirmed(publication_client) -> None:
    summary_id, trigger_msgid = _prepare_confirmed(publication_client)
    assert _send(publication_client, summary_id, trigger_msgid).status_code == 200
    response = publication_client.post(
        f"/api/daily-reports/{summary_id}/unconfirm"
    )
    assert response.status_code == 409
    assert "sent" in response.json()["detail"]


def test_unconfirmed_summary_cannot_be_sent(publication_client) -> None:
    summary_id = _save_summary(publication_client)["id"]
    response = _send(publication_client, summary_id, "unused-trigger")
    assert response.status_code == 409
    assert "尚未人工确认" in response.json()["detail"]


def test_send_requires_trigger_msgid(publication_client) -> None:
    summary_id = _save_summary(publication_client)["id"]
    _confirm(publication_client, summary_id)
    response = publication_client.post(
        f"/api/daily-reports/{summary_id}/send", json={}
    )
    assert response.status_code == 422


def test_trigger_message_must_exist(publication_client) -> None:
    summary_id = _save_summary(publication_client)["id"]
    _confirm(publication_client, summary_id)
    response = _send(publication_client, summary_id, "missing-trigger")
    assert response.status_code == 404
    assert response.json()["detail"] == "触发消息不存在"


def test_trigger_message_must_have_response_url(publication_client) -> None:
    summary_id = _save_summary(publication_client)["id"]
    _confirm(publication_client, summary_id)
    _seed_trigger(publication_client, msgid="no-response-url")
    response = _send(publication_client, summary_id, "no-response-url")
    assert response.status_code == 409
    assert "缺少 response_url" in response.json()["detail"]


def test_trigger_chatid_must_match_summary(publication_client) -> None:
    summary_id = _save_summary(publication_client)["id"]
    _confirm(publication_client, summary_id)
    _seed_trigger(
        publication_client,
        msgid="wrong-chat",
        chatid="another-group",
        response_url="mock://response-url/wrong-chat",
    )
    response = _send(publication_client, summary_id, "wrong-chat")
    assert response.status_code == 409
    assert "chatid" in response.json()["detail"]


def test_mock_send_succeeds_and_marks_summary_sent(publication_client) -> None:
    summary_id, trigger_msgid = _prepare_confirmed(publication_client)
    response = _send(publication_client, summary_id, trigger_msgid)
    body = response.json()
    assert response.status_code == 200
    assert body["publication_status"] == "sent"
    assert body["sent_at"] is not None
    assert body["attempt"]["send_status"] == "sent"
    assert body["attempt"]["transport"] == "mock"

    detail = publication_client.get(
        f"/api/daily-reports/{summary_id}"
    ).json()
    assert detail["publication_status"] == "sent"
    assert detail["sent_at"] == body["sent_at"]


def test_successful_send_attempt_is_saved_without_full_url(publication_client) -> None:
    summary_id, trigger_msgid = _prepare_confirmed(publication_client)
    response_url = f"mock://response-url/{trigger_msgid}"
    _send(publication_client, summary_id, trigger_msgid)
    response = publication_client.get(
        f"/api/daily-reports/{summary_id}/send-attempts"
    )
    attempts = response.json()["items"]
    assert response.status_code == 200
    assert len(attempts) == 1
    assert attempts[0]["trigger_msgid"] == trigger_msgid
    assert len(attempts[0]["response_url_hash"]) == 64
    assert response_url not in json.dumps(attempts, ensure_ascii=False)
    with publication_client.app.state.session_factory() as session:
        stored = session.scalar(select(DailyReportSendAttempt))
        assert stored is not None
        assert response_url not in json.dumps(
            {column.name: getattr(stored, column.name) for column in stored.__table__.columns},
            ensure_ascii=False,
            default=str,
        )


def test_sent_summary_cannot_be_sent_twice(publication_client) -> None:
    summary_id, trigger_msgid = _prepare_confirmed(publication_client)
    assert _send(publication_client, summary_id, trigger_msgid).status_code == 200
    response = _send(publication_client, summary_id, trigger_msgid)
    assert response.status_code == 409
    with publication_client.app.state.session_factory() as session:
        count = session.scalar(
            select(func.count()).select_from(DailyReportSendAttempt)
        )
    assert count == 1


def test_mock_failure_marks_summary_and_attempt_failed(failed_send_client) -> None:
    summary_id, trigger_msgid = _prepare_confirmed(failed_send_client)
    response = _send(failed_send_client, summary_id, trigger_msgid)
    body = response.json()
    assert response.status_code == 200
    assert body["publication_status"] == "send_failed"
    assert body["sent_at"] is None
    assert body["attempt"]["send_status"] == "send_failed"
    assert body["attempt"]["error_type"] == "mock_send_failure"


def test_send_failed_can_be_reconfirmed_and_retried(failed_send_client) -> None:
    summary_id, trigger_msgid = _prepare_confirmed(failed_send_client)
    assert _send(failed_send_client, summary_id, trigger_msgid).json()[
        "publication_status"
    ] == "send_failed"
    reconfirmed = failed_send_client.post(
        f"/api/daily-reports/{summary_id}/confirm",
        json={"confirmed_by": "reviewer-002", "confirmation_note": "复核后重试"},
    )
    assert reconfirmed.status_code == 200
    assert reconfirmed.json()["publication_status"] == "confirmed"

    failed_send_client.app.state.response_url_client = MockResponseUrlClient()
    retried = _send(failed_send_client, summary_id, trigger_msgid)
    assert retried.status_code == 200
    assert retried.json()["publication_status"] == "sent"
    assert len(
        failed_send_client.get(
            f"/api/daily-reports/{summary_id}/send-attempts"
        ).json()["items"]
    ) == 2


def test_sending_state_blocks_duplicate_or_concurrent_send(publication_client) -> None:
    summary_id, trigger_msgid = _prepare_confirmed(publication_client)
    with publication_client.app.state.session_factory() as session:
        summary = session.get(DailyReportSummary, summary_id)
        assert summary is not None
        summary.publication_status = "sending"
        session.commit()
    response = _send(publication_client, summary_id, trigger_msgid)
    assert response.status_code == 409
    assert "正在发送" in response.json()["detail"]
    with publication_client.app.state.session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(DailyReportSendAttempt)
        ) == 0


def test_send_does_not_modify_snapshot_markdown_or_source_links(publication_client) -> None:
    source_id = _seed_completed_report(publication_client)
    saved = publication_client.post(
        "/api/daily-reports",
        json={"chatid": CHATID, "report_date": REPORT_DATE.isoformat()},
    ).json()
    summary_id = saved["id"]
    with publication_client.app.state.session_factory() as session:
        record = session.get(DailyReportSummary, summary_id)
        original = (record.markdown_content, record.snapshot_json)
        original_sources = list(
            session.scalars(
                select(DailyReportSummaryItem.project_report_id).where(
                    DailyReportSummaryItem.summary_id == summary_id
                )
            ).all()
        )
    _confirm(publication_client, summary_id)
    _seed_trigger(
        publication_client,
        response_url="mock://response-url/generate-report-command-001",
    )
    assert _send(
        publication_client, summary_id, "generate-report-command-001"
    ).status_code == 200
    with publication_client.app.state.session_factory() as session:
        record = session.get(DailyReportSummary, summary_id)
        current_sources = list(
            session.scalars(
                select(DailyReportSummaryItem.project_report_id).where(
                    DailyReportSummaryItem.summary_id == summary_id
                )
            ).all()
        )
        assert (record.markdown_content, record.snapshot_json) == original
        assert current_sources == original_sources == [source_id]


def test_default_configuration_never_calls_http(publication_client, monkeypatch) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("默认 Mock 模式不得访问网络")

    monkeypatch.setattr(socket, "create_connection", fail_if_called)
    summary_id, trigger_msgid = _prepare_confirmed(publication_client)
    response = _send(publication_client, summary_id, trigger_msgid)
    assert response.status_code == 200
    assert response.json()["attempt"]["transport"] == "mock"


def test_old_response_url_returns_expiry_warning(publication_client) -> None:
    summary_id = _save_summary(publication_client)["id"]
    _confirm(publication_client, summary_id)
    _seed_trigger(
        publication_client,
        msgid="old-trigger",
        response_url="mock://response-url/old-trigger",
        received_at="2026-08-05T00:00:00+08:00",
    )
    response = _send(publication_client, summary_id, "old-trigger")
    assert response.status_code == 200
    assert any("可能已经过期" in item for item in response.json()["warnings"])


def test_missing_summary_publication_endpoints_return_404(publication_client) -> None:
    assert publication_client.post(
        "/api/daily-reports/999999/confirm",
        json={"confirmed_by": "admin"},
    ).status_code == 404
    assert publication_client.get(
        "/api/daily-reports/999999/send-attempts"
    ).status_code == 404
