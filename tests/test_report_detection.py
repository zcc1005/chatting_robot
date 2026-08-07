from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.main import create_app
from app.models.database_models import Message, MessageReportDetection
from app.services import report_detection_service
from tests.conftest import sqlite_url


@pytest.fixture
def detection_client(tmp_path: Path):
    settings = Settings(
        app_env="development",
        enable_mock_api=True,
        enable_jjt_callback=False,
        database_url=sqlite_url(tmp_path / "report-detection.db"),
        message_data_dir=tmp_path / "messages",
    )
    with TestClient(create_app(settings)) as client:
        yield client


def text_message(
    msgid: str,
    content: str,
    *,
    chatid: str = "report-group-001",
) -> dict[str, object]:
    return {
        "msgid": msgid,
        "chatid": chatid,
        "chattype": "group",
        "from": {"userid": "report-user-001"},
        "msgtype": "text",
        "text": {"content": content},
    }


def test_standard_construction_report_is_candidate(detection_client) -> None:
    response = detection_client.post(
        "/api/dev/mock-message",
        json=text_message(
            "standard-report",
            "兴城项目施工日报 2026年8月6日 天气晴，管理人员8人，"
            "施工人员117人，挖掘机2台，今日完成1号楼基础钢筋绑扎。",
        ),
    )
    body = response.json()
    assert response.status_code == 200
    assert body["detection_status"] == "report_candidate"
    assert body["score"] >= 5
    assert body["is_report_candidate"] is True
    assert "包含施工日报" in body["matched_rules"]
    assert "包含人员数量" in body["matched_rules"]
    assert "包含施工内容" in body["matched_rules"]


def test_nonstandard_report_without_report_title_is_candidate(detection_client) -> None:
    response = detection_client.post(
        "/api/dev/mock-message",
        json=text_message(
            "nonstandard-report",
            "今日施工内容：桥梁桩基浇筑；施工人员20人，挖掘机2台，"
            "现场作业按计划有序推进。",
        ),
    )
    body = response.json()
    assert body["detection_status"] == "report_candidate"
    assert "包含施工日报" not in body["matched_rules"]
    assert {"包含施工内容", "包含人员数量", "包含机械设备数量"}.issubset(
        body["matched_rules"]
    )


def test_weather_situation_and_common_site_equipment_are_recognized(
    detection_client,
) -> None:
    response = detection_client.post(
        "/api/dev/mock-message",
        json=text_message(
            "compact-real-world-report",
            "埃塞未来城项目2026年8月7日施工情况天气情况:雨12-22°C"
            "机械情况:塔吊2台,施工电梯2台。四、施工内容:"
            "1.层楼梯变更2人；2.二层线管安装2人；今日产值完成1.04万元。",
        ),
    )
    body = response.json()

    assert response.status_code == 200
    assert body["detection_status"] == "report_candidate"
    assert "包含日期" in body["matched_rules"]
    assert "包含天气" in body["matched_rules"]
    assert "包含机械设备数量" in body["matched_rules"]
    assert "包含施工内容" in body["matched_rules"]


@pytest.mark.parametrize(
    ("msgid", "content"),
    [
        ("ordinary-chat", "明天上午九点开会"),
        ("short-received", "收到"),
        ("short-okay", "好的"),
    ],
)
def test_ordinary_and_short_chat_is_ignored(
    detection_client, msgid: str, content: str
) -> None:
    body = detection_client.post(
        "/api/dev/mock-message", json=text_message(msgid, content)
    ).json()
    assert body["detection_status"] == "ignored"
    assert body["score"] <= 1
    assert body["is_report_candidate"] is False


def test_incomplete_suspected_report_needs_review(detection_client) -> None:
    body = detection_client.post(
        "/api/dev/mock-message",
        json=text_message("incomplete-report", "8月6日施工进度待补充"),
    ).json()
    assert body["detection_status"] == "needs_review"
    assert 2 <= body["score"] <= 4


def test_mixed_message_uses_text_for_detection(detection_client) -> None:
    payload = {
        "msgid": "mixed-report",
        "chatid": "report-group-001",
        "chattype": "group",
        "from": {"userid": "report-user-001"},
        "msgtype": "mixed",
        "mixed": {
            "msg_item": [
                {
                    "msgtype": "text",
                    "text": {
                        "content": "桥梁项目施工日报，今日完成桩基施工，施工人员20人。"
                    },
                },
                {"msgtype": "image", "image": {"url": "mock://report-image"}},
            ]
        },
    }
    body = detection_client.post("/api/dev/mock-message", json=payload).json()
    assert body["detection_status"] == "report_candidate"
    detail = detection_client.get("/api/messages/mixed-report").json()
    assert detail["report_detection"]["detection_status"] == "report_candidate"
    assert len(detail["attachments"]) == 1


@pytest.mark.parametrize(
    ("msgid", "msgtype", "content_field"),
    [
        ("pure-image", "image", {"image": {"url": "mock://pure-image"}}),
        ("pure-file", "file", {"file": {"url": "mock://pure-file"}}),
        ("pure-voice", "voice", {"voice": {"url": "mock://pure-voice"}}),
    ],
)
def test_non_text_message_is_not_applicable(
    detection_client,
    msgid: str,
    msgtype: str,
    content_field: dict[str, object],
) -> None:
    payload: dict[str, object] = {
        "msgid": msgid,
        "chatid": "report-group-001",
        "chattype": "group",
        "from": {"userid": "report-user-001"},
        "msgtype": msgtype,
        **content_field,
    }
    response = detection_client.post("/api/dev/mock-message", json=payload)
    assert response.status_code == 200
    assert response.json()["detection_status"] == "not_applicable"
    detail = detection_client.get(f"/api/messages/{msgid}").json()
    assert detail["report_detection"]["score"] == 0


def test_repeated_manual_detection_updates_single_record(detection_client) -> None:
    detection_client.post(
        "/api/dev/mock-message",
        json=text_message("repeat-detection", "8月6日施工进度待补充"),
    )
    first = detection_client.post(
        "/api/messages/repeat-detection/detect-report"
    ).json()
    second = detection_client.post(
        "/api/messages/repeat-detection/detect-report"
    ).json()
    assert first["detection_status"] == second["detection_status"] == "needs_review"
    with detection_client.app.state.session_factory() as session:
        count = session.scalar(
            select(func.count()).select_from(MessageReportDetection)
        )
    assert count == 1


def test_duplicate_msgid_does_not_add_detection_record(detection_client) -> None:
    payload = text_message("duplicate-report", "桥梁项目施工日报，今日完成桩基施工。")
    first = detection_client.post("/api/dev/mock-message", json=payload)
    second = detection_client.post("/api/dev/mock-message", json=payload)
    assert first.json()["detection_status"] == "report_candidate"
    assert second.json()["status"] == "ignored"
    assert "detection_status" not in second.json()
    with detection_client.app.state.session_factory() as session:
        message_count = session.scalar(select(func.count()).select_from(Message))
        detection_count = session.scalar(
            select(func.count()).select_from(MessageReportDetection)
        )
    assert message_count == detection_count == 1


def test_report_candidates_returns_only_candidates(detection_client) -> None:
    detection_client.post(
        "/api/dev/mock-message",
        json=text_message("candidate-one", "桥梁项目施工日报，今日完成桩基施工。"),
    )
    detection_client.post(
        "/api/dev/mock-message",
        json=text_message("ignored-one", "明天上午九点开会"),
    )
    response = detection_client.get("/api/report-candidates")
    assert response.status_code == 200
    assert [item["msgid"] for item in response.json()["items"]] == [
        "candidate-one"
    ]


def test_report_detections_filters_status_and_chatid(detection_client) -> None:
    detection_client.post(
        "/api/dev/mock-message",
        json=text_message("review-in-target", "8月6日施工进度待补充", chatid="target"),
    )
    detection_client.post(
        "/api/dev/mock-message",
        json=text_message("ignored-in-target", "好的", chatid="target"),
    )
    detection_client.post(
        "/api/dev/mock-message",
        json=text_message("review-elsewhere", "8月7日施工进度待补充", chatid="other"),
    )
    response = detection_client.get(
        "/api/report-detections",
        params={"detection_status": "needs_review", "chatid": "target"},
    )
    assert response.status_code == 200
    assert [item["msgid"] for item in response.json()["items"]] == [
        "review-in-target"
    ]


def test_detect_missing_msgid_returns_404(detection_client) -> None:
    response = detection_client.post(
        "/api/messages/does-not-exist/detect-report"
    )
    assert response.status_code == 404


def test_automatic_detection_failure_does_not_lose_message(
    detection_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_detection(*_args, **_kwargs):
        raise RuntimeError("simulated detection failure")

    monkeypatch.setattr(
        report_detection_service, "detect_and_save_report", fail_detection
    )
    response = detection_client.post(
        "/api/dev/mock-message",
        json=text_message("detection-failed", "桥梁项目施工日报，今日完成桩基施工。"),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "saved"
    assert "detection_status" not in response.json()
    detail = detection_client.get("/api/messages/detection-failed")
    assert detail.status_code == 200
    assert detail.json()["report_detection"] is None
