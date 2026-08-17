from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.chat_workflow_service import is_greeting, parse_report_command
from app.services.response_url_client import ResponseUrlSendResult
from tests.conftest import sqlite_url
from tests.test_callback import encrypt_for_test, signed_params
from tests.test_report_extraction import full_extraction


class RecordingResponseClient:
    transport = "real"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def send(self, *, response_url: str, content: str) -> ResponseUrlSendResult:
        self.calls.append((response_url, content))
        return ResponseUrlSendResult(
            success=True,
            transport="real",
            http_status_code=200,
        )


class ExtractionClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def extract(self, text_content: str) -> str:
        self.calls.append(text_content)
        return json.dumps(self.responses.pop(0), ensure_ascii=False)


def make_message(msgid: str, content: str) -> dict[str, object]:
    return {
        "msgid": msgid,
        "aibotid": "bot-001",
        "chatid": "prod-group-001",
        "chattype": "group",
        "from": {"userid": "builder-001"},
        "response_url": f"https://jjt.example/reply?code={msgid}",
        "msgtype": "text",
        "text": {"content": content},
        "received_at": "2026-08-12T09:00:00+08:00",
    }


def post_callback(client: TestClient, message: dict[str, object]):
    encrypted = encrypt_for_test(json.dumps(message).encode("utf-8"))
    return client.post(
        "/jjt-robot/callback",
        params=signed_params(encrypted),
        json={"encrypt": encrypted},
    )


def workflow_client(
    tmp_path: Path,
    extraction: ExtractionClient,
    responses: RecordingResponseClient,
) -> TestClient:
    settings = Settings(
        callback_token="test-callback-token",
        encoding_aes_key="AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
        message_data_dir=tmp_path / "messages",
        database_url=sqlite_url(tmp_path / "workflow.db"),
        enable_jjt_callback=True,
        enable_real_response_send=True,
        enable_auto_chat_workflow=True,
        public_base_url="https://reports.example.com",
    )
    return TestClient(
        create_app(
            settings,
            report_extraction_client=extraction,
            response_url_client=responses,
        )
    )


def test_report_callback_automatically_extracts_and_replies(tmp_path: Path) -> None:
    extraction = ExtractionClient(
        [full_extraction(project_name="海尔胶州项目", report_date="2026-08-12")]
    )
    responses = RecordingResponseClient()
    with workflow_client(tmp_path, extraction, responses) as client:
        response = post_callback(
            client,
            make_message(
                "prod-report-001",
                "海尔胶州项目2026年8月12日施工情况，天气情况：阴，"
                "总施工人数：18人，叉车1辆，直臂车1台。",
            ),
        )
        detail = client.get("/api/project-reports/prod-report-001")

    assert response.status_code == 200
    assert detail.status_code == 200
    assert detail.json()["project_name"] == "海尔胶州项目"
    assert len(extraction.calls) == 1
    assert len(responses.calls) == 1
    assert "日报已识别" in responses.calls[0][1]


def test_generate_command_saves_confirms_and_sends_summary(tmp_path: Path) -> None:
    extraction = ExtractionClient(
        [full_extraction(project_name="海尔胶州项目", report_date="2026-08-12")]
    )
    responses = RecordingResponseClient()
    with workflow_client(tmp_path, extraction, responses) as client:
        post_callback(
            client,
            make_message(
                "prod-report-002",
                "海尔胶州项目2026年8月12日施工情况，天气情况：阴，"
                "总施工人数：18人，叉车1辆，直臂车1台。",
            ),
        )
        command = post_callback(
            client,
            make_message("prod-command-001", "生成今天的施工日报"),
        )
        summaries = client.get(
            "/api/daily-reports",
            params={"chatid": "prod-group-001", "report_date": "2026-08-12"},
        ).json()["items"]

    assert command.status_code == 200
    assert len(summaries) == 1
    assert summaries[0]["publication_status"] == "sent"
    assert summaries[0]["project_count"] == 1
    assert len(responses.calls) == 2
    assert "施工日报汇总预览" in responses.calls[-1][1]
    assert "点击查看图表与项目图片" in responses.calls[-1][1]
    assert "https://reports.example.com/visual-reports/" in responses.calls[-1][1]


def test_duplicate_reports_automatically_use_latest_and_continue_summary(
    tmp_path: Path,
) -> None:
    first = full_extraction(
        project_name="重复桥梁项目",
        report_date="2026-08-12",
        management_count=3,
        worker_count=20,
    )
    second = full_extraction(
        project_name="重复桥梁项目",
        report_date="2026-08-12",
        management_count=4,
        worker_count=30,
    )
    extraction = ExtractionClient([first, second])
    responses = RecordingResponseClient()
    with workflow_client(tmp_path, extraction, responses) as client:
        post_callback(
            client,
            make_message(
                "duplicate-chat-one",
                "重复桥梁项目2026年8月12日日报，管理3人，施工20人，今日桩基施工。",
            ),
        )
        second_message = make_message(
            "duplicate-chat-two",
            "重复桥梁项目2026年8月12日日报，管理4人，施工30人，今日承台施工。",
        )
        second_message["received_at"] = "2026-08-12T10:00:00+08:00"
        post_callback(client, second_message)
        post_callback(
            client, make_message("duplicate-generate", "生成今天的施工日报")
        )

        summaries = client.get(
            "/api/daily-reports",
            params={"chatid": "prod-group-001", "report_date": "2026-08-12"},
        ).json()["items"]
        preview = client.post(
            "/api/daily-reports/preview",
            json={"chatid": "prod-group-001", "report_date": "2026-08-12"},
        ).json()

    assert summaries[-1]["publication_status"] == "sent"
    assert preview["project_count"] == 1
    assert preview["management_total"] == 4
    assert preview["worker_total"] == 30
    assert preview["duplicate_projects"] == []
    assert "施工日报汇总预览" in responses.calls[-1][1]
    assert "发现重复日报" not in responses.calls[-1][1]


def test_duplicate_reports_support_selecting_latest(tmp_path: Path) -> None:
    extraction = ExtractionClient(
        [
            full_extraction(
                project_name="重复隧道项目",
                report_date="2026-08-12",
                worker_count=18,
            ),
            full_extraction(
                project_name="重复隧道项目",
                report_date="2026-08-12",
                worker_count=36,
            ),
        ]
    )
    responses = RecordingResponseClient()
    with workflow_client(tmp_path, extraction, responses) as client:
        post_callback(
            client,
            make_message(
                "latest-chat-one",
                "重复隧道项目2026年8月12日日报，施工18人，今日掌子面施工。",
            ),
        )
        latest = make_message(
            "latest-chat-two",
            "重复隧道项目2026年8月12日日报，施工36人，今日二衬施工。",
        )
        latest["received_at"] = "2026-08-12T11:00:00+08:00"
        post_callback(client, latest)
        post_callback(
            client, make_message("latest-generate", "生成今天的施工日报")
        )
        preview = client.post(
            "/api/daily-reports/preview",
            json={"chatid": "prod-group-001", "report_date": "2026-08-12"},
        ).json()

    assert preview["project_count"] == 1
    assert preview["worker_total"] == 36
    assert preview["duplicate_projects"] == []


def test_greeting_gets_short_function_intro_without_llm(tmp_path: Path) -> None:
    extraction = ExtractionClient([])
    responses = RecordingResponseClient()
    with workflow_client(tmp_path, extraction, responses) as client:
        response = post_callback(client, make_message("prod-chat-001", "你在吗"))

    assert response.status_code == 200
    assert extraction.calls == []
    assert len(responses.calls) == 1
    assert responses.calls[0][1] == (
        "你好，我是施工日报机器人，可以识别施工信息并生成日报汇总。"
    )


def test_ordinary_chat_gets_concise_usage_reply_without_llm(tmp_path: Path) -> None:
    extraction = ExtractionClient([])
    responses = RecordingResponseClient()
    with workflow_client(tmp_path, extraction, responses) as client:
        response = post_callback(client, make_message("prod-chat-002", "怎么使用"))

    assert response.status_code == 200
    assert extraction.calls == []
    assert responses.calls[0][1] == (
        "我是施工日报机器人：请发送施工信息，或发送“生成日报”进行汇总。"
    )


def test_parse_command_supports_today_and_nearest_month_day() -> None:
    now = datetime(2026, 8, 12, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert parse_report_command("生成今天的施工日报", now=now).isoformat() == "2026-08-12"
    assert parse_report_command("@日报机器人 生成日报", now=now).isoformat() == "2026-08-12"
    assert parse_report_command("汇总8月10日日报", now=now).isoformat() == "2026-08-10"
    assert parse_report_command("你在吗", now=now) is None


def test_greeting_supports_common_wording_and_mentions() -> None:
    for content in ("你好", "您好！", "@测试机器人 早上好", "hello", "你在吗？"):
        assert is_greeting(content)
    assert not is_greeting("你好，生成日报")
    assert not is_greeting("明天上午开会")


def test_management_conflict_is_explained_in_chat_reply(tmp_path: Path) -> None:
    extraction = ExtractionClient(
        [
            full_extraction(
                project_name="中交一公局临平项目",
                report_date="2026-08-13",
                management_count=None,
                worker_count=None,
                missing_fields=["management_count", "worker_count"],
            )
        ]
    )
    responses = RecordingResponseClient()
    content = (
        "中交一公局临平项目2026年8月13日施工情况，"
        "管理人员15：项目管理人员10人，协作队伍管理人员7；"
        "现场工人总计47人；今日施工地梁拆模。"
    )
    with workflow_client(tmp_path, extraction, responses) as client:
        response = post_callback(
            client, make_message("management-conflict-chat", content)
        )

    assert response.status_code == 200
    assert len(responses.calls) == 1
    assert "管理人员：待确认（原文总数 15 人，明细合计 17 人）" in (
        responses.calls[0][1]
    )
    assert "施工人员：47 人" in responses.calls[0][1]
    assert "是否修改人数" in responses.calls[0][1]


def test_pending_people_confirmation_accepts_correction(tmp_path: Path) -> None:
    extraction = ExtractionClient(
        [
            full_extraction(
                management_count=None,
                worker_count=47,
                missing_fields=["management_count"],
            )
        ]
    )
    responses = RecordingResponseClient()
    with workflow_client(tmp_path, extraction, responses) as client:
        post_callback(
            client,
            make_message(
                "people-pending-001",
                "桥梁项目2026年8月12日施工情况，现场工人总计47人，今日施工地梁拆模。",
            ),
        )
        correction = post_callback(
            client,
            make_message(
                "people-correction-001", "@测试机器人 管理人员为15人"
            ),
        )
        report = client.get("/api/project-reports/people-pending-001").json()

    assert correction.status_code == 200
    assert report["management_count"] == 15
    assert report["worker_count"] == 47
    assert report["extraction_source"] == "manual"
    assert responses.calls[-1][1] == "已确认人数：管理人员 15 人。"


def test_pending_people_confirmation_supports_stepwise_updates(
    tmp_path: Path,
) -> None:
    extraction = ExtractionClient(
        [
            full_extraction(
                management_count=None,
                worker_count=None,
                missing_fields=["management_count", "worker_count"],
            )
        ]
    )
    responses = RecordingResponseClient()
    with workflow_client(tmp_path, extraction, responses) as client:
        post_callback(
            client,
            make_message(
                "people-pending-002",
                "桥梁项目2026年8月12日施工情况，今日施工地梁拆模，人员待补充。",
            ),
        )
        post_callback(
            client,
            make_message("people-correction-002", "管理人员为7人"),
        )
        post_callback(
            client,
            make_message("people-correction-003", "施工人员为36人"),
        )
        report = client.get("/api/project-reports/people-pending-002").json()

    assert report["management_count"] == 7
    assert report["worker_count"] == 36
    assert "施工人员为xx人" in responses.calls[-2][1]
    assert responses.calls[-1][1] == "已确认人数：施工人员 36 人。"


def test_pending_people_confirmation_can_be_refused(tmp_path: Path) -> None:
    extraction = ExtractionClient(
        [full_extraction(management_count=None, missing_fields=["management_count"])]
    )
    responses = RecordingResponseClient()
    with workflow_client(tmp_path, extraction, responses) as client:
        post_callback(
            client,
            make_message(
                "people-pending-003",
                "桥梁项目2026年8月12日施工情况，施工人员20人，今日施工地梁拆模。",
            ),
        )
        refused = post_callback(
            client,
            make_message("people-refused-001", "@测试机器人 不修改人数"),
        )

    assert refused.status_code == 200
    assert responses.calls[-1][1] == "好的。"


def test_ordinary_chat_does_not_create_people_confirmation(
    tmp_path: Path,
) -> None:
    extraction_payload = full_extraction(
        project_name=None,
        report_date=None,
        weather=None,
        management_count=None,
        worker_count=None,
        equipment=None,
        work_items=None,
        tomorrow_plan=None,
        safety_status=None,
        quality_status=None,
        missing_fields=[
            "project_name",
            "report_date",
            "weather",
            "management_count",
            "worker_count",
            "equipment",
            "work_items",
            "tomorrow_plan",
            "safety_status",
            "quality_status",
        ],
        relevance_status="ordinary_chat",
        relevance_reason="普通聊天",
        relevance_confidence=0.99,
    )
    extraction = ExtractionClient([extraction_payload])
    responses = RecordingResponseClient()
    with workflow_client(tmp_path, extraction, responses) as client:
        post_callback(
            client,
            make_message("ordinary-no-confirmation", "桥梁施工进度正常"),
        )

    assert "不是施工日报" in responses.calls[-1][1]
    assert "是否修改人数" not in responses.calls[-1][1]


def test_unrelated_message_silently_ignores_pending_confirmation(
    tmp_path: Path,
) -> None:
    extraction = ExtractionClient(
        [full_extraction(management_count=None, missing_fields=["management_count"])]
    )
    responses = RecordingResponseClient()
    with workflow_client(tmp_path, extraction, responses) as client:
        post_callback(
            client,
            make_message(
                "people-pending-004",
                "桥梁项目2026年8月12日施工情况，施工人员20人，今日施工地梁拆模。",
            ),
        )
        greeting = post_callback(
            client, make_message("people-ignore-greeting", "你好")
        )
        late_correction = post_callback(
            client,
            make_message("people-late-correction", "管理人员为9人"),
        )
        report = client.get("/api/project-reports/people-pending-004").json()

    assert greeting.status_code == 200
    assert late_correction.status_code == 200
    assert "你好，我是施工日报机器人" in responses.calls[-2][1]
    assert "请发送施工信息" in responses.calls[-1][1]
    assert report["management_count"] is None
