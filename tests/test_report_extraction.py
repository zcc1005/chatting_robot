from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.main import create_app
from app.models.database_models import (
    Message,
    ProjectReport,
    ReportEquipment,
    ReportWorkItem,
)
from app.services.llm_extraction_client import LLMClientTimeout
from tests.conftest import sqlite_url


class MockExtractionClient:
    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def extract(self, text_content: str) -> str:
        self.calls.append(text_content)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def full_extraction(**overrides) -> dict[str, object]:
    result: dict[str, object] = {
        "project_name": "兴城桥梁项目",
        "report_date": "2026-08-06",
        "weather": "晴",
        "management_count": 8,
        "worker_count": 117,
        "equipment": [
            {"name": "挖掘机", "count": 2, "unit": "台"},
            {"name": "吊车", "count": 1, "unit": "辆"},
        ],
        "work_items": [
            {"location": "1号桥", "content": "桩基浇筑", "progress": "完成80%"},
            {"location": "2号墩", "content": "钢筋绑扎", "progress": "完成50%"},
        ],
        "tomorrow_plan": "继续进行桩基浇筑",
        "safety_status": "无安全事故",
        "quality_status": "质量检查合格",
        "missing_fields": [],
        "confidence": 0.95,
    }
    result.update(overrides)
    return result


def report_message(
    msgid: str,
    content: str | None = None,
    *,
    chatid: str = "extraction-group",
) -> dict[str, object]:
    return {
        "msgid": msgid,
        "chatid": chatid,
        "chattype": "group",
        "from": {"userid": "extraction-user"},
        "msgtype": "text",
        "text": {
            "content": content
            or "兴城桥梁项目施工日报，2026年8月6日天气晴，管理人员8人，"
            "施工人员117人，挖掘机2台，今日完成桩基浇筑。"
        },
    }


@pytest.fixture
def client_and_llm(tmp_path: Path):
    mock_client = MockExtractionClient(
        [json.dumps(full_extraction(), ensure_ascii=False)]
    )
    settings = Settings(
        app_env="development",
        enable_mock_api=True,
        enable_jjt_callback=False,
        database_url=sqlite_url(tmp_path / "extraction.db"),
        message_data_dir=tmp_path / "messages",
    )
    with TestClient(create_app(settings, mock_client)) as client:
        yield client, mock_client


def save_candidate(client: TestClient, msgid: str, **kwargs) -> None:
    response = client.post(
        "/api/dev/mock-message", json=report_message(msgid, **kwargs)
    )
    assert response.status_code == 200
    assert response.json()["detection_status"] == "report_candidate"


def test_standard_report_complete_extraction(client_and_llm) -> None:
    client, mock_llm = client_and_llm
    save_candidate(client, "complete-report")
    assert mock_llm.calls == []
    response = client.post("/api/messages/complete-report/extract-report")
    body = response.json()
    assert response.status_code == 200
    assert body["project_name"] == "兴城桥梁项目"
    assert body["report_date"] == "2026-08-06"
    assert body["extraction_status"] == "completed"
    assert body["extraction_source"] == "llm"
    assert body["missing_fields"] == []
    assert len(mock_llm.calls) == 1
    assert "施工日报" in mock_llm.calls[0]


def test_compact_real_world_report_enters_daily_summary(client_and_llm) -> None:
    client, mock_llm = client_and_llm
    mock_llm.responses = [
        json.dumps(
            full_extraction(
                project_name="埃塞未来城项目",
                report_date="2026-08-07",
                weather="雨12-22°C",
                management_count=None,
                worker_count=25,
                equipment=[
                    {"name": "塔吊", "count": 2, "unit": "台"},
                    {"name": "施工电梯", "count": 2, "unit": "台"},
                ],
                missing_fields=["management_count"],
            ),
            ensure_ascii=False,
        )
    ]
    payload = report_message(
        "compact-summary-report",
        "埃塞未来城项目2026年8月7日施工情况天气情况:雨12-22°C"
        "机械情况:塔吊2台,施工电梯2台。四、施工内容:"
        "1.层楼梯变更2人；2.二层线管安装2人；今日产值完成1.04万元。",
        chatid="construction-group-003",
    )

    detected = client.post("/api/dev/mock-message", json=payload).json()
    assert detected["detection_status"] == "report_candidate"
    assert "包含天气" in detected["matched_rules"]
    assert "包含机械设备数量" in detected["matched_rules"]

    extracted = client.post(
        "/api/messages/compact-summary-report/extract-report"
    ).json()
    assert extracted["weather"] == "雨12-22°C"
    assert extracted["extraction_status"] == "completed"

    preview = client.post(
        "/api/daily-reports/preview",
        json={"chatid": "construction-group-003", "report_date": "2026-08-07"},
    ).json()
    assert preview["project_count"] == 1
    assert preview["worker_total"] == 25
    assert preview["projects"][0]["project_name"] == "埃塞未来城项目"
    assert preview["projects"][0]["weather"] == "雨12-22°C"
    assert preview["equipment"] == [
        {"name": "塔吊", "count": 2, "unit": "台"},
        {"name": "施工电梯", "count": 2, "unit": "台"},
    ]


def test_explicit_people_totals_override_llm_misclassification(
    client_and_llm,
) -> None:
    client, mock_llm = client_and_llm
    mock_llm.responses = [
        json.dumps(
            full_extraction(
                project_name="佳沃（淄博）全球数智农食产业基地项目",
                report_date="2026-08-12",
                management_count=9,
                worker_count=None,
                missing_fields=["worker_count"],
            ),
            ensure_ascii=False,
        )
    ]
    content = (
        "佳沃（淄博）全球数智农食产业基地项目2026年8月12日施工情况\n"
        "1、天气情况：晴，气温27℃~39℃\n"
        "2、机械情况：吊车1台\n"
        "3、施工总人数：36人\n"
        "4、施工内容：\n"
        "(1)8#厂房：一层垃圾清理4人\n"
        "(2)8#9#中间连廊：梁钢筋绑扎3人，梁板模板支护3人\n"
        "(3)9#厂房：二层女儿墙拆模4人\n"
        "(4)10#厂房及北侧连廊：高支模内架搭设6人，普工3人\n"
        "5、管理及后台\n"
        "(5)主体单位管理人员6人\n"
        "(6)市政单位管理人员1人\n"
        "(7)安全文明施工2人\n"
        "(8)钢筋后台加工3人"
    )
    saved = client.post(
        "/api/dev/mock-message",
        json=report_message("explicit-people-override", content),
    )
    assert saved.json()["detection_status"] == "report_candidate"

    extracted = client.post(
        "/api/messages/explicit-people-override/extract-report"
    ).json()

    assert extracted["management_count"] == 7
    assert extracted["worker_count"] == 36
    assert "management_count" not in extracted["missing_fields"]
    assert "worker_count" not in extracted["missing_fields"]
    assert any(
        "施工总人数为 36 人" in warning
        for warning in extracted["normalization_warnings"]
    )
    assert any(
        "管理人员数量为 7 人" in warning
        for warning in extracted["normalization_warnings"]
    )


def test_work_item_people_are_not_guessed_as_worker_total(
    client_and_llm,
) -> None:
    client, mock_llm = client_and_llm
    mock_llm.responses = [
        json.dumps(full_extraction(worker_count=12), ensure_ascii=False)
    ]
    content = (
        "桥梁项目2026年8月12日施工情况：桩基施工4人，钢筋绑扎3人，"
        "模板支护5人。"
    )
    client.post(
        "/api/dev/mock-message",
        json=report_message("no-worker-total-guess", content),
    )
    extracted = client.post(
        "/api/messages/no-worker-total-guess/extract-report"
    ).json()

    assert extracted["worker_count"] == 12
    assert not any(
        "施工总人数" in warning
        for warning in extracted["normalization_warnings"]
    )


def test_unitless_management_counts_and_conflict_are_preserved(
    client_and_llm,
) -> None:
    client, mock_llm = client_and_llm
    mock_llm.responses = [
        json.dumps(
            full_extraction(
                project_name="中交一公局临平项目",
                report_date="2026-08-13",
                management_count=None,
                worker_count=None,
                missing_fields=["management_count", "worker_count"],
            ),
            ensure_ascii=False,
        )
    ]
    content = (
        "中交一公局临平项目2026年8月13日施工情况\n"
        "一、天气情况：晴，气温26~36℃；机械情况：塔吊3台、挖机2台\n"
        "三、施工人数：\n"
        "管理人员15：项目管理人员10人，协作队伍管理人员7；\n"
        "现场工人总计：47人。\n"
        "四、施工内容：9#楼地梁拆模11人，地梁浇水养护1人。"
    )
    client.post(
        "/api/dev/mock-message",
        json=report_message("unitless-management-conflict", content),
    )
    extracted = client.post(
        "/api/messages/unitless-management-conflict/extract-report"
    ).json()

    assert extracted["management_count"] is None
    assert extracted["worker_count"] == 47
    assert "management_count" in extracted["missing_fields"]
    assert "worker_count" not in extracted["missing_fields"]
    assert "管理人员数据冲突：原文总数 15 人，明细合计 17 人" in (
        extracted["normalization_warnings"]
    )


def test_missing_weather_is_null_and_listed(client_and_llm) -> None:
    client, mock_llm = client_and_llm
    extraction = full_extraction(weather=None, missing_fields=["weather"])
    mock_llm.responses = [json.dumps(extraction, ensure_ascii=False)]
    save_candidate(client, "missing-weather")
    body = client.post("/api/messages/missing-weather/extract-report").json()
    assert body["weather"] is None
    assert body["missing_fields"] == ["weather"]
    assert body["extraction_status"] == "completed"


def test_missing_project_name_needs_review(client_and_llm) -> None:
    client, mock_llm = client_and_llm
    extraction = full_extraction(
        project_name=None, missing_fields=["project_name"]
    )
    mock_llm.responses = [json.dumps(extraction, ensure_ascii=False)]
    save_candidate(client, "missing-project")
    body = client.post("/api/messages/missing-project/extract-report").json()
    assert body["project_name"] is None
    assert body["extraction_status"] == "needs_review"
    assert "project_name" in body["missing_fields"]


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [("report_date", None), ("work_items", None)],
)
def test_other_missing_critical_fields_need_review(
    client_and_llm, field_name: str, field_value: object
) -> None:
    client, mock_llm = client_and_llm
    extraction = full_extraction(
        **{field_name: field_value, "missing_fields": [field_name]}
    )
    mock_llm.responses = [json.dumps(extraction, ensure_ascii=False)]
    msgid = f"missing-{field_name}"
    if field_name == "report_date":
        save_candidate(
            client,
            msgid,
            content=(
                "兴城桥梁项目施工日报，天气晴，管理人员8人，"
                "施工人员117人，挖掘机2台，今日完成桩基浇筑。"
            ),
        )
    else:
        save_candidate(client, msgid)
    body = client.post(f"/api/messages/{msgid}/extract-report").json()
    assert body["extraction_status"] == "needs_review"
    assert field_name in body["missing_fields"]


def test_multiple_equipment_are_saved(client_and_llm) -> None:
    client, _ = client_and_llm
    save_candidate(client, "multiple-equipment")
    body = client.post("/api/messages/multiple-equipment/extract-report").json()
    assert body["equipment"] == [
        {"name": "挖掘机", "count": 2, "unit": "台"},
        {"name": "吊车", "count": 1, "unit": "辆"},
    ]
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ReportEquipment)) == 2


def test_multiple_work_items_are_saved(client_and_llm) -> None:
    client, _ = client_and_llm
    save_candidate(client, "multiple-work-items")
    body = client.post("/api/messages/multiple-work-items/extract-report").json()
    assert [item["content"] for item in body["work_items"]] == [
        "桩基浇筑",
        "钢筋绑扎",
    ]
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ReportWorkItem)) == 2


def test_ordinary_chat_is_forbidden_without_calling_llm(client_and_llm) -> None:
    client, mock_llm = client_and_llm
    response = client.post(
        "/api/dev/mock-message",
        json=report_message("ordinary-chat-extraction", "明天上午九点开会"),
    )
    assert response.json()["detection_status"] == "ignored"
    extract = client.post(
        "/api/messages/ordinary-chat-extraction/extract-report"
    )
    assert extract.status_code == 409
    assert mock_llm.calls == []


def test_pure_image_is_forbidden_without_calling_llm(client_and_llm) -> None:
    client, mock_llm = client_and_llm
    payload = {
        "msgid": "image-extraction",
        "chatid": "extraction-group",
        "chattype": "group",
        "from": {"userid": "extraction-user"},
        "msgtype": "image",
        "image": {"url": "mock://not-downloaded"},
    }
    saved = client.post("/api/dev/mock-message", json=payload)
    assert saved.json()["detection_status"] == "not_applicable"
    extract = client.post("/api/messages/image-extraction/extract-report")
    assert extract.status_code == 409
    assert mock_llm.calls == []


@pytest.mark.parametrize("msgtype", ["file", "voice"])
def test_file_and_voice_are_forbidden_without_calling_llm(
    client_and_llm, msgtype: str
) -> None:
    client, mock_llm = client_and_llm
    payload = {
        "msgid": f"{msgtype}-extraction",
        "chatid": "extraction-group",
        "chattype": "group",
        "from": {"userid": "extraction-user"},
        "msgtype": msgtype,
        msgtype: {"url": f"mock://{msgtype}-not-downloaded"},
    }
    client.post("/api/dev/mock-message", json=payload)
    extract = client.post(f"/api/messages/{msgtype}-extraction/extract-report")
    assert extract.status_code == 409
    assert mock_llm.calls == []


def test_needs_review_message_is_allowed_to_extract(client_and_llm) -> None:
    client, mock_llm = client_and_llm
    saved = client.post(
        "/api/dev/mock-message",
        json=report_message("review-allowed", "8月6日施工进度待补充"),
    )
    assert saved.json()["detection_status"] == "needs_review"
    extracted = client.post("/api/messages/review-allowed/extract-report")
    assert extracted.status_code == 200
    assert len(mock_llm.calls) == 1


def test_month_day_only_uses_message_received_year_and_enters_summary(
    client_and_llm,
) -> None:
    client, mock_llm = client_and_llm
    extraction = full_extraction(
        project_name="武清电商园项目",
        report_date=None,
        missing_fields=["report_date"],
    )
    mock_llm.responses = [json.dumps(extraction, ensure_ascii=False)]
    payload = report_message(
        "month-day-report",
        "武清电商园项目8月10日施工情况，天气晴，施工人员106人，"
        "塔吊3台，施工内容：酒店顶板吊装完成90%。",
        chatid="month-day-group",
    )
    payload["received_at"] = "2026-08-10T01:00:00Z"
    saved = client.post("/api/dev/mock-message", json=payload)
    assert saved.json()["detection_status"] == "report_candidate"

    extracted = client.post(
        "/api/messages/month-day-report/extract-report"
    ).json()
    assert extracted["report_date"] == "2026-08-10"
    assert extracted["date_source"] == "text_month_day_message_year"
    assert "report_date" not in extracted["missing_fields"]
    assert extracted["extraction_status"] == "completed"

    preview = client.post(
        "/api/daily-reports/preview",
        json={"chatid": "month-day-group", "report_date": "2026-08-10"},
    ).json()
    assert preview["project_count"] == 1
    assert preview["projects"][0]["project_name"] == "武清电商园项目"


def test_obvious_report_question_is_filtered_locally_without_llm(
    client_and_llm,
) -> None:
    client, mock_llm = client_and_llm
    saved = client.post(
        "/api/dev/mock-message",
        json=report_message("report-question", "今天的施工日报呢"),
    ).json()
    assert saved["detection_status"] == "ignored"
    assert "疑似询问或催要日报" in saved["matched_rules"]
    assert mock_llm.calls == []


def test_llm_can_demote_ambiguous_message_to_ordinary_chat(
    client_and_llm,
) -> None:
    client, mock_llm = client_and_llm
    extraction = {
        field: None
        for field in (
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
        )
    }
    extraction.update(
        {
            "missing_fields": [
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
            "confidence": 0,
            "relevance_status": "ordinary_chat",
            "relevance_reason": "只是一般性描述，没有日报数据",
            "relevance_confidence": 0.96,
        }
    )
    mock_llm.responses = [json.dumps(extraction, ensure_ascii=False)]
    saved = client.post(
        "/api/dev/mock-message",
        json=report_message("llm-ordinary", "桥梁施工进度正常"),
    ).json()
    assert saved["detection_status"] == "needs_review"

    extracted = client.post(
        "/api/messages/llm-ordinary/extract-report"
    ).json()
    assert extracted["relevance_status"] == "ordinary_chat"
    assert extracted["relevance_confidence"] == 0.96
    detail = client.get("/api/messages/llm-ordinary").json()
    assert detail["report_detection"]["detection_status"] == "ignored"
    assert "大模型相关性复核为普通聊天" in detail["report_detection"]["reason"]


def test_llm_related_update_remains_reviewable(client_and_llm) -> None:
    client, mock_llm = client_and_llm
    extraction = full_extraction(
        project_name=None,
        report_date=None,
        weather=None,
        management_count=None,
        worker_count=None,
        equipment=None,
        work_items=[
            {
                "location": "桥梁",
                "content": "施工进度正常",
                "progress": None,
            }
        ],
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
            "tomorrow_plan",
            "safety_status",
            "quality_status",
        ],
        relevance_status="related_update",
        relevance_reason="包含施工进度补充但不是完整日报",
        relevance_confidence=0.9,
    )
    mock_llm.responses = [json.dumps(extraction, ensure_ascii=False)]
    client.post(
        "/api/dev/mock-message",
        json=report_message("related-update", "桥梁施工进度正常"),
    )
    body = client.post(
        "/api/messages/related-update/extract-report"
    ).json()
    assert body["relevance_status"] == "related_update"
    assert body["extraction_status"] == "needs_review"
    detail = client.get("/api/messages/related-update").json()
    assert detail["report_detection"]["detection_status"] == "needs_review"


def test_missing_content_in_one_work_item_does_not_fail_whole_report(
    client_and_llm,
) -> None:
    client, mock_llm = client_and_llm
    extraction = full_extraction(
        project_name="伊拉克米桑医院项目",
        report_date="2026-08-04",
        work_items=[
            {"location": "4区", "content": "混凝土养护", "progress": None},
            {"location": "新办公区", "content": None, "progress": None},
        ],
    )
    mock_llm.responses = [json.dumps(extraction, ensure_ascii=False)]
    save_candidate(client, "partial-invalid-work-item")
    response = client.post(
        "/api/messages/partial-invalid-work-item/extract-report"
    )
    body = response.json()
    assert response.status_code == 200
    assert body["extraction_status"] == "completed"
    assert body["work_items"] == [
        {"location": "4区", "content": "混凝土养护", "progress": None}
    ]
    assert len(body["normalization_warnings"]) == 1
    assert "1 项施工子项" in body["normalization_warnings"][0]


def test_invalid_json_is_saved_as_failed(client_and_llm) -> None:
    client, mock_llm = client_and_llm
    mock_llm.responses = ["not-json"]
    save_candidate(client, "invalid-json")
    response = client.post("/api/messages/invalid-json/extract-report")
    assert response.status_code == 502
    detail = client.get("/api/project-reports/invalid-json").json()
    assert detail["extraction_status"] == "failed"
    assert detail["error_message"] == "大模型返回非法 JSON"
    assert detail["raw_extraction_json"] == "not-json"


def test_wrong_field_type_is_saved_as_failed(client_and_llm) -> None:
    client, mock_llm = client_and_llm
    extraction = full_extraction(worker_count="117")
    mock_llm.responses = [json.dumps(extraction, ensure_ascii=False)]
    save_candidate(client, "wrong-type")
    response = client.post("/api/messages/wrong-type/extract-report")
    assert response.status_code == 502
    detail = client.get("/api/project-reports/wrong-type").json()
    assert detail["extraction_status"] == "failed"
    assert detail["error_message"] == "大模型返回字段校验失败"


def test_timeout_is_saved_as_failed(client_and_llm) -> None:
    client, mock_llm = client_and_llm
    mock_llm.responses = [LLMClientTimeout("大模型调用超时（已自动重试 1 次）")]
    save_candidate(client, "timeout-report")
    response = client.post("/api/messages/timeout-report/extract-report")
    assert response.status_code == 504
    detail = client.get("/api/project-reports/timeout-report").json()
    assert detail["extraction_status"] == "failed"
    assert detail["error_message"] == "大模型调用超时（已自动重试 1 次）"


def test_repeated_extraction_updates_one_record(client_and_llm) -> None:
    client, mock_llm = client_and_llm
    second = full_extraction(
        weather="小雨",
        equipment=[{"name": "吊车", "count": 3, "unit": "辆"}],
    )
    mock_llm.responses.append(json.dumps(second, ensure_ascii=False))
    save_candidate(client, "repeat-extraction")
    first = client.post("/api/messages/repeat-extraction/extract-report").json()
    updated = client.post("/api/messages/repeat-extraction/extract-report").json()
    assert first["id"] == updated["id"]
    assert updated["weather"] == "小雨"
    assert updated["equipment"] == [{"name": "吊车", "count": 3, "unit": "辆"}]
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ProjectReport)) == 1
        assert session.scalar(select(func.count()).select_from(ReportEquipment)) == 1


def test_manual_patch_updates_fields_and_source(client_and_llm) -> None:
    client, _ = client_and_llm
    save_candidate(client, "manual-patch")
    client.post("/api/messages/manual-patch/extract-report")
    response = client.patch(
        "/api/project-reports/manual-patch",
        json={
            "project_name": "人工修正项目",
            "worker_count": 120,
            "work_items": [
                {
                    "location": "3号墩",
                    "content": "模板安装",
                    "progress": None,
                }
            ],
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert body["project_name"] == "人工修正项目"
    assert body["worker_count"] == 120
    assert body["work_items"][0]["content"] == "模板安装"
    assert body["extraction_source"] == "manual"
    assert body["extraction_status"] == "completed"


def test_missing_message_returns_404(client_and_llm) -> None:
    client, mock_llm = client_and_llm
    response = client.post("/api/messages/not-found/extract-report")
    assert response.status_code == 404
    assert mock_llm.calls == []


def test_unconfigured_llm_does_not_prevent_startup(tmp_path: Path) -> None:
    settings = Settings(
        app_env="development",
        enable_mock_api=True,
        enable_jjt_callback=False,
        database_url=sqlite_url(tmp_path / "unconfigured.db"),
        message_data_dir=tmp_path / "messages",
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        save_candidate(client, "unconfigured-llm")
        response = client.post("/api/messages/unconfigured-llm/extract-report")
    assert response.status_code == 503
    assert "未配置大模型" in response.json()["detail"]


def test_failed_extraction_does_not_change_original_message(client_and_llm) -> None:
    client, mock_llm = client_and_llm
    mock_llm.responses = ["invalid"]
    payload = report_message("preserved-message")
    client.post("/api/dev/mock-message", json=payload)
    client.post("/api/messages/preserved-message/extract-report")
    message = client.get("/api/messages/preserved-message").json()
    assert message["raw_payload"] == payload
    assert message["report_detection"]["detection_status"] == "report_candidate"
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Message)) == 1


def test_project_report_list_filters(client_and_llm) -> None:
    client, mock_llm = client_and_llm
    second = full_extraction(project_name="另一项目", report_date="2026-08-07")
    mock_llm.responses.append(json.dumps(second, ensure_ascii=False))
    save_candidate(client, "filter-first", chatid="target-chat")
    client.post("/api/messages/filter-first/extract-report")
    save_candidate(client, "filter-second", chatid="other-chat")
    client.post("/api/messages/filter-second/extract-report")
    response = client.get(
        "/api/project-reports",
        params={
            "project_name": "兴城桥梁项目",
            "report_date": "2026-08-06",
            "extraction_status": "completed",
            "chatid": "target-chat",
        },
    )
    assert response.status_code == 200
    assert [item["msgid"] for item in response.json()["items"]] == [
        "filter-first"
    ]
