from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import sqlite_url


@pytest.fixture
def dev_chat_client(tmp_path: Path):
    settings = Settings(
        app_env="development",
        enable_mock_api=True,
        enable_jjt_callback=False,
        database_url=sqlite_url(tmp_path / "dev-chat.db"),
        message_data_dir=tmp_path / "messages",
    )
    with TestClient(create_app(settings)) as client:
        yield client


def test_development_can_access_chat_page(dev_chat_client) -> None:
    response = dev_chat_client.get("/dev/chat")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in response.headers["content-security-policy"]


@pytest.mark.parametrize(
    ("app_env", "enable_mock_api"),
    [("production", True), ("development", False)],
)
def test_chat_page_is_not_registered_outside_enabled_development(
    tmp_path: Path, app_env: str, enable_mock_api: bool
) -> None:
    settings = Settings(
        app_env=app_env,
        enable_mock_api=enable_mock_api,
        enable_jjt_callback=False,
        database_url=sqlite_url(tmp_path / f"{app_env}-{enable_mock_api}.db"),
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/dev/chat").status_code == 404
        assert client.get("/dev/chat.js").status_code == 404
        assert client.get("/dev/chat.css").status_code == 404


def test_page_contains_message_input_and_group_selector(dev_chat_client) -> None:
    html = dev_chat_client.get("/dev/chat").text

    assert 'id="message-input"' in html
    assert 'name="sender_userid"' in html
    assert 'name="sender_name"' in html
    assert 'id="chatid-select"' in html
    assert "construction-group-001" in html
    assert "Enter 发送" in html


def test_chat_view_has_scrollable_context_and_navigation(dev_chat_client) -> None:
    html = dev_chat_client.get("/dev/chat").text
    styles = dev_chat_client.get("/dev/chat.css").text
    script = dev_chat_client.get("/dev/chat.js").text

    assert 'id="scroll-oldest"' in html
    assert 'id="scroll-latest"' in html
    assert 'id="message-count"' in html
    assert "overflow-y: scroll" in styles
    assert "scrollbar-gutter: stable" in styles
    assert "function scrollMessages" in script
    assert "messageList.scrollHeight" in script


def test_page_calls_existing_mock_message_api(dev_chat_client) -> None:
    html = dev_chat_client.get("/dev/chat").text
    script = dev_chat_client.get("/dev/chat.js")

    assert 'data-mock-endpoint="/api/dev/mock-message"' in html
    assert script.status_code == 200
    assert "fetch(endpoint" in script.text
    assert "endpoints.mock" in script.text
    assert 'msgtype: "text"' in script.text
    assert "response_url" not in script.text.lower()


def test_page_assets_do_not_contain_hardcoded_secrets(dev_chat_client) -> None:
    assets = "\n".join(
        [
            dev_chat_client.get("/dev/chat").text,
            dev_chat_client.get("/dev/chat.js").text,
            dev_chat_client.get("/dev/chat.css").text,
        ]
    )

    assert "Bearer " not in assets
    assert "sk-" not in assets
    assert "LLM_API_KEY=" not in assets
    assert "JJT_ENCODING_AES_KEY=" not in assets
    assert "JJT_CALLBACK_TOKEN=" not in assets


def test_user_text_is_rendered_with_text_content(dev_chat_client) -> None:
    script = dev_chat_client.get("/dev/chat.js").text

    assert "element.textContent = String(text)" in script
    assert "dialogContent.textContent" in script
    assert ".innerHTML" not in script
    assert "renderMarkdown" in script
    assert "document.createElement" in script


def test_summary_preview_fields_and_markdown_can_be_displayed(
    dev_chat_client,
) -> None:
    script = dev_chat_client.get("/dev/chat.js").text

    for field_name in (
        "project_count",
        "management_total",
        "worker_total",
        "equipment",
        "generation_status",
        "warnings",
        "duplicate_projects",
        "markdown_content",
    ):
        assert field_name in script
    assert "生成汇总预览" in dev_chat_client.get("/dev/chat").text
    assert "保存汇总快照" in script
    assert "查看汇总详情" in script


def test_user_facing_missing_fields_use_chinese_labels(dev_chat_client) -> None:
    script = dev_chat_client.get("/dev/chat.js").text

    assert 'management_count: "管理人员数量"' in script
    assert 'tomorrow_plan: "明日计划"' in script
    assert 'safety_status: "安全情况"' in script
    assert 'quality_status: "质量情况"' in script
    assert '"还需补充的信息"' in script
    assert "fieldListText(report.missing_fields)" in script
    assert "function summaryProjectNames" in script
    assert "function humanizeSummaryText" in script
    assert "humanizeSummaryText(rawLine.trim(), summaryData)" in script
    assert '项目“${projectName}”' in script
    assert '"项目名称未识别的日报"' in script
    assert "map(report => report.msgid)" not in script


def test_candidate_is_llm_extracted_before_preview(dev_chat_client) -> None:
    html = dev_chat_client.get("/dev/chat").text
    script = dev_chat_client.get("/dev/chat.js").text

    assert "自动复核已开启" in html
    assert "生成汇总预览" in html
    assert "function autoExtractNewMessage" in script
    assert "function extractPendingReportsBeforePreview" in script
    assert "await autoExtractNewMessage(result)" in script
    assert "await extractPendingReportsBeforePreview()" in script
    assert "右侧汇总已自动刷新" in script
    assert "没有生成可汇总的结构化日报" in script
    assert "if (!extractionReady) return" in script
    assert 'extraction_status === "failed"' in script
    assert "网络请求失败" in script


def test_preview_is_sent_as_local_robot_chat_message(dev_chat_client) -> None:
    html = dev_chat_client.get("/dev/chat").text
    script = dev_chat_client.get("/dev/chat.js").text
    styles = dev_chat_client.get("/dev/chat.css").text

    assert "机器人会在聊天区直接发送一条汇总日报卡片" in html
    assert "function renderSummaryChatMessage" in script
    assert "renderSummaryChatMessage(preview)" in script
    assert "本地预览 · 未发送真实群聊" in script
    assert "施工日报汇总预览" in script
    assert "messageList.scrollHeight" in script
    assert ".summary-chat-bubble" in styles


def test_saved_summary_can_be_confirmed_and_unconfirmed(dev_chat_client) -> None:
    html = dev_chat_client.get("/dev/chat").text
    script = dev_chat_client.get("/dev/chat.js").text

    assert "人工确认流程" in html
    assert "确认只更新本地快照状态" in html
    assert "人工确认汇总快照" in script
    assert "confirmed_by" in script
    assert "confirmation_note" in script
    assert "}/confirm`" in script
    assert "}/unconfirm`" in script
    assert "我已核对，确认汇总" in script


def test_mocked_user_html_is_not_embedded_in_page(
    dev_chat_client,
) -> None:
    malicious_text = '<img src=x onerror="document.body.dataset.pwned=1">'
    response = dev_chat_client.post(
        "/api/dev/mock-message",
        json={
            "msgid": "xss-safe-message",
            "aibotid": "dev-chat-bot",
            "chatid": "construction-group-001",
            "chattype": "group",
            "from": {"userid": "builder-zhang", "name": "张工"},
            "msgtype": "text",
            "text": {"content": malicious_text},
        },
    )

    assert response.status_code == 200
    assert malicious_text not in dev_chat_client.get("/dev/chat").text
    listed = dev_chat_client.get(
        "/api/messages", params={"chatid": "construction-group-001"}
    ).json()
    assert listed["items"][0]["text_content"] == malicious_text
