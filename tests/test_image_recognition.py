from __future__ import annotations

import base64
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.main import create_app
from app.models.database_models import (
    Message,
    MessageAttachment,
    MessageImageRecognition,
    ProjectReport,
    ProjectReportImage,
    ReportEquipment,
    ReportWorkItem,
)
from app.services.image_recognition_client import (
    ImageBinary,
    ImageRecognitionClientError,
    SafeImageContentLoader,
)
from tests.conftest import sqlite_url


CHATID = "image-group"
REPORT_DATE = date(2026, 8, 4)
BASE_TIME = datetime(2026, 8, 4, 9, 0, tzinfo=timezone(timedelta(hours=8)))


SAMPLE_RESULT = {
    "project_name": "埃塞俄比亚未来经济区一期项目",
    "report_date": "2026-08-04",
    "captured_at": None,
    "weather": "小雨 13℃",
    "location": "亚的斯亚贝巴",
    "construction_content": "十八楼公寓梁板模板的铺设",
    "ocr_text": (
        "埃塞俄比亚未来经济区一期项目；施工区域：十九楼公寓；"
        "施工内容：梁板模板的铺设；拍摄时间：2026.08.04 08:54"
    ),
    "scene_description": "施工楼层正在进行梁板模板铺设，可见钢筋和模板。",
    "confidence": 0.94,
}


class StubImageLoader:
    def load(self, attachment):
        data = b"\x89PNG\r\n\x1a\nmock-image-content"
        return ImageBinary(
            data=data,
            media_type="image/png",
            sha256=hashlib.sha256(data).hexdigest(),
        )


class StubVisionClient:
    def __init__(self, result: dict | str | None = None) -> None:
        self.result = result if result is not None else SAMPLE_RESULT.copy()
        self.calls = 0

    def recognize(self, image: ImageBinary) -> str:
        self.calls += 1
        if isinstance(self.result, str):
            return self.result
        return json.dumps(self.result, ensure_ascii=False)


@pytest.fixture
def image_runtime(tmp_path: Path):
    vision = StubVisionClient()
    settings = Settings(
        app_env="development",
        enable_mock_api=True,
        enable_jjt_callback=False,
        database_url=sqlite_url(tmp_path / "images.db"),
        message_data_dir=tmp_path / "messages",
    )
    with TestClient(
        create_app(
            settings,
            image_recognition_client=vision,
            image_content_loader=StubImageLoader(),
        )
    ) as client:
        yield client, vision


def _seed_project_report(
    client: TestClient,
    *,
    msgid: str = "text-report-001",
    chatid: str = CHATID,
    sender_userid: str = "builder-zhang",
    project_name: str = "埃塞未来城项目",
    received_at: datetime = BASE_TIME,
    work_content: str = "18层模板安装",
) -> int:
    with client.app.state.session_factory() as session:
        message = Message(
            msgid=msgid,
            source="mock",
            chatid=chatid,
            chattype="group",
            sender_userid=sender_userid,
            msgtype="text",
            text_content="项目施工日报",
            raw_json="{}",
            received_at=received_at,
            process_status="received",
        )
        session.add(message)
        session.flush()
        report = ProjectReport(
            msgid=msgid,
            message_id=message.id,
            project_name=project_name,
            report_date=REPORT_DATE,
            weather="雨 12-22℃",
            management_count=2,
            worker_count=117,
            tomorrow_plan="继续施工",
            safety_status="安全正常",
            quality_status="质量正常",
            missing_fields="[]",
            confidence=0.95,
            extraction_status="completed",
            extraction_source="manual",
            raw_extraction_json="{}",
        )
        report.equipment.extend(
            [
                ReportEquipment(name="塔吊", count=2, unit="台", position=0),
                ReportEquipment(name="施工电梯", count=2, unit="台", position=1),
            ]
        )
        report.work_items.append(
            ReportWorkItem(
                location="18层",
                content=work_content,
                progress="53%",
                position=0,
            )
        )
        session.add(report)
        session.commit()
        return report.id


def _seed_image_message(
    client: TestClient,
    *,
    msgid: str = "image-001",
    chatid: str = CHATID,
    sender_userid: str = "builder-zhang",
    received_at: datetime = BASE_TIME + timedelta(minutes=2),
) -> int:
    response = client.post(
        "/api/dev/mock-message",
        json={
            "msgid": msgid,
            "chatid": chatid,
            "chattype": "group",
            "from": {"userid": sender_userid},
            "msgtype": "image",
            "image": {"url": f"mock://construction-image/{msgid}"},
            "received_at": received_at.isoformat(),
        },
    )
    assert response.status_code == 200
    with client.app.state.session_factory() as session:
        message = session.scalar(select(Message).where(Message.msgid == msgid))
        assert message is not None
        return message.attachments[0].id


def _recognize(client: TestClient, msgid: str = "image-001"):
    return client.post(f"/api/messages/{msgid}/recognize-images")


def test_group_image_is_recognized_and_saved(image_runtime) -> None:
    client, vision = image_runtime
    _seed_project_report(client)
    attachment_id = _seed_image_message(client)
    response = _recognize(client)
    body = response.json()[0]
    assert response.status_code == 200
    assert vision.calls == 1
    assert body["attachment_id"] == attachment_id
    assert body["recognition_status"] == "completed"
    assert body["project_name"] == SAMPLE_RESULT["project_name"]
    assert body["construction_content"] == SAMPLE_RESULT["construction_content"]
    assert "梁板模板" in body["ocr_text"]
    assert len(body["image_sha256"]) == 64


def test_content_sender_and_time_evidence_auto_links_project(image_runtime) -> None:
    client, _ = image_runtime
    report_id = _seed_project_report(client)
    _seed_image_message(client)
    association = _recognize(client).json()[0]["association"]
    assert association["association_status"] == "linked"
    assert association["project_report_id"] == report_id
    assert association["score"] >= 5
    assert any("发送人相同" in item for item in association["matched_rules"])
    assert any("项目名称" in item for item in association["matched_rules"])


def test_same_sender_and_time_without_content_evidence_needs_review(
    image_runtime,
) -> None:
    client, vision = image_runtime
    vision.result = {
        **SAMPLE_RESULT,
        "project_name": None,
        "construction_content": "现场施工",
        "ocr_text": "2026年8月4日",
        "scene_description": "可见施工现场",
    }
    _seed_project_report(client)
    _seed_image_message(client)
    association = _recognize(client).json()[0]["association"]
    assert association["association_status"] == "needs_review"
    assert association["project_report_id"] is not None
    assert "内容证据不足" in association["reason"]


def test_different_chat_project_is_not_a_candidate(image_runtime) -> None:
    client, _ = image_runtime
    _seed_project_report(client, chatid="other-group")
    _seed_image_message(client)
    association = _recognize(client).json()[0]["association"]
    assert association["association_status"] == "unmatched"
    assert association["project_report_id"] is None


def test_repeated_recognition_updates_current_result(image_runtime) -> None:
    client, vision = image_runtime
    _seed_project_report(client)
    _seed_image_message(client)
    assert _recognize(client).status_code == 200
    vision.result = {**SAMPLE_RESULT, "weather": "晴 20℃"}
    second = _recognize(client)
    assert second.status_code == 200
    assert second.json()[0]["weather"] == "晴 20℃"
    with client.app.state.session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(MessageImageRecognition)
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(ProjectReportImage)
        ) == 1


def test_invalid_vision_json_is_saved_as_failed(image_runtime) -> None:
    client, vision = image_runtime
    vision.result = "not-json"
    _seed_image_message(client)
    response = _recognize(client)
    assert response.status_code == 200
    body = response.json()[0]
    assert body["recognition_status"] == "failed"
    assert body["error_message"] == "图片识别返回非法 JSON"
    assert body["association"] is None


def test_non_image_message_cannot_use_image_recognition(image_runtime) -> None:
    client, vision = image_runtime
    response = client.post(
        "/api/dev/mock-message",
        json={
            "msgid": "plain-text",
            "chatid": CHATID,
            "chattype": "group",
            "from": {"userid": "builder-zhang"},
            "msgtype": "text",
            "text": {"content": "收到"},
        },
    )
    assert response.status_code == 200
    denied = client.post("/api/messages/plain-text/recognize-images")
    assert denied.status_code == 409
    assert vision.calls == 0


def test_manual_association_can_confirm_candidate(image_runtime) -> None:
    client, vision = image_runtime
    vision.result = {
        **SAMPLE_RESULT,
        "project_name": None,
        "construction_content": "现场施工",
        "ocr_text": "2026年8月4日",
        "scene_description": "可见施工现场",
    }
    report_id = _seed_project_report(client)
    attachment_id = _seed_image_message(client)
    assert _recognize(client).json()[0]["association"][
        "association_status"
    ] == "needs_review"
    patched = client.patch(
        f"/api/image-recognitions/{attachment_id}/association",
        json={"project_report_id": report_id},
    )
    assert patched.status_code == 200
    assert patched.json()["association"]["association_status"] == "manual"
    assert patched.json()["association"]["project_report_id"] == report_id


def test_manual_association_rejects_project_from_other_chat(image_runtime) -> None:
    client, _ = image_runtime
    _seed_project_report(client)
    other_id = _seed_project_report(
        client,
        msgid="other-report",
        chatid="other-group",
        project_name="其他项目",
    )
    attachment_id = _seed_image_message(client)
    assert _recognize(client).status_code == 200
    response = client.patch(
        f"/api/image-recognitions/{attachment_id}/association",
        json={"project_report_id": other_id},
    )
    assert response.status_code == 409


def test_linked_image_is_shown_under_project_without_changing_totals(
    image_runtime,
) -> None:
    client, _ = image_runtime
    _seed_project_report(client)
    _seed_image_message(client)
    assert _recognize(client).status_code == 200
    response = client.post(
        "/api/daily-reports/preview",
        json={"chatid": CHATID, "report_date": REPORT_DATE.isoformat()},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["project_count"] == 1
    assert body["management_total"] == 2
    assert body["worker_total"] == 117
    assert body["equipment"] == [
        {"name": "塔吊", "count": 2, "unit": "台"},
        {"name": "施工电梯", "count": 2, "unit": "台"},
    ]
    project_image = body["projects"][0]["images"][0]
    assert project_image["image_msgid"] == "image-001"
    assert "梁板模板" in project_image["ocr_text"]
    markdown = body["markdown_content"]
    assert "现场图片与识别补充" in markdown
    assert "![埃塞未来城项目现场图1]" in markdown
    assert "图片文字" in markdown
    assert markdown.index("### 1. 埃塞未来城项目") < markdown.index(
        "现场图片与识别补充"
    )


def test_uncertain_image_is_listed_for_review_not_under_project(
    image_runtime,
) -> None:
    client, vision = image_runtime
    vision.result = {
        **SAMPLE_RESULT,
        "project_name": None,
        "construction_content": "现场施工",
        "ocr_text": "2026年8月4日",
        "scene_description": "可见施工现场",
    }
    _seed_project_report(client)
    _seed_image_message(client)
    _recognize(client)
    body = client.post(
        "/api/daily-reports/preview",
        json={"chatid": CHATID, "report_date": REPORT_DATE.isoformat()},
    ).json()
    assert body["projects"][0]["images"] == []
    assert body["image_reviews"][0]["association_status"] == "needs_review"
    assert body["generation_status"] == "needs_review"
    assert "待人工确认图片" in body["markdown_content"]


def test_saved_summary_snapshot_preserves_linked_image(image_runtime) -> None:
    client, _ = image_runtime
    _seed_project_report(client)
    _seed_image_message(client)
    _recognize(client)
    saved = client.post(
        "/api/daily-reports",
        json={"chatid": CHATID, "report_date": REPORT_DATE.isoformat()},
    ).json()
    detail = client.get(f"/api/daily-reports/{saved['id']}").json()
    assert detail["projects"][0]["images"] == saved["projects"][0]["images"]
    assert detail["markdown_content"] == saved["markdown_content"]


def test_image_recognition_query_supports_status_filters(image_runtime) -> None:
    client, _ = image_runtime
    _seed_project_report(client)
    _seed_image_message(client)
    _recognize(client)
    response = client.get(
        "/api/image-recognitions",
        params={
            "chatid": CHATID,
            "recognition_status": "completed",
            "association_status": "linked",
        },
    )
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1


def test_unconfigured_vision_model_does_not_break_startup(tmp_path: Path) -> None:
    settings = Settings(
        app_env="development",
        enable_mock_api=True,
        enable_jjt_callback=False,
        database_url=sqlite_url(tmp_path / "unconfigured-images.db"),
        message_data_dir=tmp_path / "messages",
    )
    with TestClient(create_app(settings)) as client:
        _seed_image_message(client)
        response = _recognize(client)
        assert response.status_code == 503
        assert "VISION_MODEL" in response.json()["detail"]


def test_dev_mock_image_upload_can_be_recognized_without_network(
    tmp_path: Path,
) -> None:
    vision = StubVisionClient()
    settings = Settings(
        app_env="development",
        enable_mock_api=True,
        enable_jjt_callback=False,
        database_url=sqlite_url(tmp_path / "uploaded-image.db"),
        message_data_dir=tmp_path / "messages",
    )
    image_bytes = b"\x89PNG\r\n\x1a\nlocal-dev-image"
    with TestClient(
        create_app(settings, image_recognition_client=vision)
    ) as client:
        uploaded = client.post(
            "/api/dev/mock-image-message",
            json={
                "chatid": CHATID,
                "sender_userid": "builder-zhang",
                "sender_name": "张工",
                "filename": "site.png",
                "content_type": "image/png",
                "image_base64": base64.b64encode(image_bytes).decode("ascii"),
                "received_at": BASE_TIME.isoformat(),
            },
        )
        assert uploaded.status_code == 200
        msgid = uploaded.json()["msgid"]
        detail = client.get(f"/api/messages/{msgid}").json()
        attachment = detail["attachments"][0]
        assert attachment["download_status"] == "downloaded"
        assert Path(attachment["local_path"]).is_file()
        image_response = client.get(attachment["remote_url"])
        assert image_response.status_code == 200
        assert image_response.content == image_bytes
        listed = client.get(
            "/api/messages", params={"chatid": CHATID}
        ).json()["items"]
        assert listed[0]["image_urls"] == [attachment["remote_url"]]
        recognized = client.post(
            f"/api/messages/{msgid}/recognize-images"
        )
        assert recognized.status_code == 200
        assert recognized.json()[0]["recognition_status"] == "completed"
        assert vision.calls == 1


def test_safe_loader_rejects_non_https_remote_url(tmp_path: Path) -> None:
    loader = SafeImageContentLoader(
        local_root=tmp_path,
        timeout_seconds=1,
        max_bytes=1024,
    )
    attachment = MessageAttachment(
        message_id=1,
        attachment_type="image",
        remote_url="http://127.0.0.1/private.png",
        download_status="pending",
    )
    with pytest.raises(ImageRecognitionClientError, match="HTTPS"):
        loader.load(attachment)


def test_safe_loader_rejects_local_path_outside_allowed_root(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\ncontent")
    loader = SafeImageContentLoader(
        local_root=allowed,
        timeout_seconds=1,
        max_bytes=1024,
    )
    attachment = MessageAttachment(
        message_id=1,
        attachment_type="image",
        remote_url="/api/dev/images/outside.png",
        local_path=str(outside),
        download_status="downloaded",
    )
    with pytest.raises(ImageRecognitionClientError, match="允许目录"):
        loader.load(attachment)


def test_auto_image_recognition_runs_after_message_is_saved(
    tmp_path: Path,
) -> None:
    vision = StubVisionClient()
    settings = Settings(
        app_env="development",
        enable_mock_api=True,
        enable_jjt_callback=False,
        enable_auto_image_recognition=True,
        database_url=sqlite_url(tmp_path / "auto-images.db"),
        message_data_dir=tmp_path / "messages",
    )
    with TestClient(
        create_app(
            settings,
            image_recognition_client=vision,
            image_content_loader=StubImageLoader(),
        )
    ) as client:
        response = client.post(
            "/api/dev/mock-message",
            json={
                "msgid": "auto-image",
                "chatid": CHATID,
                "chattype": "group",
                "from": {"userid": "builder-zhang"},
                "msgtype": "image",
                "image": {"url": "mock://construction-image/auto"},
                "received_at": BASE_TIME.isoformat(),
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "saved"
        records = client.get(
            "/api/image-recognitions",
            params={"chatid": CHATID},
        ).json()["items"]
        assert len(records) == 1
        assert records[0]["recognition_status"] == "completed"
        assert vision.calls == 1
