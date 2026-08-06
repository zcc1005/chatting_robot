from __future__ import annotations

import base64
import json
import os
import struct
from pathlib import Path

import pytest
from Crypto.Cipher import AES
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.crypto_service import JJTCryptoService


TOKEN = "test-callback-token"
AES_KEY_BYTES = bytes(range(32))
ENCODING_AES_KEY = base64.b64encode(AES_KEY_BYTES).decode("ascii").rstrip("=")
WECOM_CORP_ID = "ww-test-corp-id"


def encrypt_for_test(content: bytes, receive_id: bytes = b"") -> str:
    plaintext = os.urandom(16) + struct.pack(">I", len(content)) + content + receive_id
    padding_length = 32 - (len(plaintext) % 32)
    padded = plaintext + bytes([padding_length]) * padding_length
    encrypted = AES.new(
        AES_KEY_BYTES, AES.MODE_CBC, iv=AES_KEY_BYTES[:16]
    ).encrypt(padded)
    return base64.b64encode(encrypted).decode("ascii")


@pytest.fixture
def client_and_dir(tmp_path: Path):
    data_dir = tmp_path / "messages"
    settings = Settings(
        callback_token=TOKEN,
        encoding_aes_key=ENCODING_AES_KEY,
        message_data_dir=data_dir,
        log_level="INFO",
        timezone="Asia/Shanghai",
        database_url=f"sqlite:///{(tmp_path / 'callback.db').as_posix()}",
    )
    with TestClient(create_app(settings)) as client:
        yield client, data_dir


@pytest.fixture
def wecom_client_and_dir(tmp_path: Path):
    data_dir = tmp_path / "wecom-messages"
    settings = Settings(
        callback_token=TOKEN,
        encoding_aes_key=ENCODING_AES_KEY,
        receive_id=WECOM_CORP_ID,
        message_data_dir=data_dir,
        log_level="INFO",
        timezone="Asia/Shanghai",
        database_url=f"sqlite:///{(tmp_path / 'wecom-callback.db').as_posix()}",
    )
    with TestClient(create_app(settings)) as client:
        yield client, data_dir


def signed_params(encrypt: str) -> dict[str, str]:
    timestamp = "1710000000"
    nonce = "test-nonce"
    service = JJTCryptoService(TOKEN, ENCODING_AES_KEY)
    return {
        "msg_signature": service.calculate_signature(timestamp, nonce, encrypt),
        "timestamp": timestamp,
        "nonce": nonce,
    }


def sample_message(msgid: str = "message-001") -> dict[str, object]:
    return {
        "msgid": msgid,
        "aibotid": "bot-001",
        "chatid": "chat-001",
        "chattype": "group",
        "from": {"userid": "user-001"},
        "response_url": "https://example.invalid/reply?token=secret",
        "msgtype": "text",
        "text": {"content": "@机器人 测试消息"},
    }


def sample_wecom_xml(msgid: str = "90001") -> bytes:
    return f"""<xml>
        <ToUserName><![CDATA[{WECOM_CORP_ID}]]></ToUserName>
        <FromUserName><![CDATA[user-001]]></FromUserName>
        <CreateTime>1710000000</CreateTime>
        <MsgType><![CDATA[text]]></MsgType>
        <Content><![CDATA[项目进度正常]]></Content>
        <MsgId>{msgid}</MsgId>
        <AgentID>1000002</AgentID>
    </xml>""".encode("utf-8")


def encrypted_wecom_envelope(encrypted: str) -> bytes:
    return f"""<xml>
        <ToUserName><![CDATA[{WECOM_CORP_ID}]]></ToUserName>
        <Encrypt><![CDATA[{encrypted}]]></Encrypt>
        <AgentID>1000002</AgentID>
    </xml>""".encode("utf-8")


def test_health_returns_200(client_and_dir) -> None:
    client, _ = client_and_dir
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "jjt-daily-report-bot"}


def test_openapi_documents_json_and_xml_callback_bodies(client_and_dir) -> None:
    client, _ = client_and_dir
    operation = client.get("/openapi.json").json()["paths"]["/api/jjt/callback"][
        "post"
    ]
    content_types = operation["requestBody"]["content"]
    assert "application/json" in content_types
    assert "text/xml" in content_types
    assert "application/xml" in content_types


def test_get_callback_returns_plaintext_without_newline(client_and_dir) -> None:
    client, _ = client_and_dir
    plaintext = "callback-ok"
    echostr = encrypt_for_test(plaintext.encode())
    response = client.get(
        "/api/jjt/callback", params={**signed_params(echostr), "echostr": echostr}
    )
    assert response.status_code == 200
    assert response.content == plaintext.encode()
    assert response.text == plaintext


def test_get_callback_invalid_signature_returns_403(client_and_dir) -> None:
    client, _ = client_and_dir
    echostr = encrypt_for_test(b"callback-ok")
    response = client.get(
        "/api/jjt/callback",
        params={
            "msg_signature": "0" * 40,
            "timestamp": "1",
            "nonce": "2",
            "echostr": echostr,
        },
    )
    assert response.status_code == 403


def test_get_callback_missing_parameter_returns_400(client_and_dir) -> None:
    client, _ = client_and_dir
    response = client.get("/api/jjt/callback")
    assert response.status_code == 400


def test_get_callback_supports_wecom_corp_id(wecom_client_and_dir) -> None:
    client, _ = wecom_client_and_dir
    echostr = encrypt_for_test(b"wecom-callback-ok", WECOM_CORP_ID.encode())
    response = client.get(
        "/api/jjt/callback", params={**signed_params(echostr), "echostr": echostr}
    )
    assert response.status_code == 200
    assert response.content == b"wecom-callback-ok"


def test_post_valid_message_returns_200(client_and_dir) -> None:
    client, _ = client_and_dir
    encrypted = encrypt_for_test(json.dumps(sample_message()).encode("utf-8"))
    response = client.post(
        "/api/jjt/callback", params=signed_params(encrypted), json={"encrypt": encrypted}
    )
    assert response.status_code == 200
    assert response.content == b""


def test_post_creates_jsonl_record(client_and_dir) -> None:
    client, data_dir = client_and_dir
    message = sample_message()
    encrypted = encrypt_for_test(json.dumps(message).encode("utf-8"))
    response = client.post(
        "/api/jjt/callback", params=signed_params(encrypted), json={"encrypt": encrypted}
    )
    assert response.status_code == 200
    files = list(data_dir.glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["msgid"] == "message-001"
    assert record["sender_userid"] == "user-001"
    assert record["message"] == message


def test_post_json_callback_also_saves_sqlite(client_and_dir) -> None:
    client, _ = client_and_dir
    message = sample_message("callback-database-001")
    encrypted = encrypt_for_test(json.dumps(message).encode("utf-8"))
    response = client.post(
        "/api/jjt/callback", params=signed_params(encrypted), json={"encrypt": encrypted}
    )
    detail = client.get("/api/messages/callback-database-001")
    assert response.status_code == 200
    assert detail.status_code == 200
    assert detail.json()["source"] == "jjt"
    assert detail.json()["text_content"] == "@机器人 测试消息"


def test_duplicate_msgid_is_saved_once(client_and_dir) -> None:
    client, data_dir = client_and_dir
    message = sample_message("duplicate-id")
    for _ in range(2):
        encrypted = encrypt_for_test(json.dumps(message).encode("utf-8"))
        response = client.post(
            "/api/jjt/callback",
            params=signed_params(encrypted),
            json={"encrypt": encrypted},
        )
        assert response.status_code == 200
    lines = next(data_dir.glob("*.jsonl")).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_post_missing_encrypt_returns_422(client_and_dir) -> None:
    client, _ = client_and_dir
    response = client.post(
        "/api/jjt/callback",
        params={"msg_signature": "x", "timestamp": "1", "nonce": "2"},
        json={},
    )
    assert response.status_code == 422


def test_post_non_json_plaintext_returns_400(client_and_dir) -> None:
    client, _ = client_and_dir
    encrypted = encrypt_for_test(b"not-json")
    response = client.post(
        "/api/jjt/callback", params=signed_params(encrypted), json={"encrypt": encrypted}
    )
    assert response.status_code == 400


def test_post_invalid_utf8_plaintext_returns_400(client_and_dir) -> None:
    client, _ = client_and_dir
    encrypted = encrypt_for_test(b"\xff\xfe")
    response = client.post(
        "/api/jjt/callback", params=signed_params(encrypted), json={"encrypt": encrypted}
    )
    assert response.status_code == 400


def test_post_wecom_xml_returns_success_and_saves_jsonl(wecom_client_and_dir) -> None:
    client, data_dir = wecom_client_and_dir
    plaintext = sample_wecom_xml()
    encrypted = encrypt_for_test(plaintext, WECOM_CORP_ID.encode())
    response = client.post(
        "/api/jjt/callback",
        params=signed_params(encrypted),
        content=encrypted_wecom_envelope(encrypted),
        headers={"content-type": "text/xml; charset=utf-8"},
    )
    assert response.status_code == 200
    assert response.content == b"success"

    files = list(data_dir.glob("*.jsonl"))
    assert len(files) == 1
    record = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
    assert record["msgid"] == "90001"
    assert record["sender_userid"] == "user-001"
    assert record["msgtype"] == "text"
    assert record["message"]["source"] == "wecom_xml"
    assert record["message"]["text"]["content"] == "项目进度正常"
    assert record["message"]["xml"]["AgentID"] == "1000002"
    assert record["message"]["raw_xml"] == plaintext.decode("utf-8")


def test_post_wecom_duplicate_msgid_is_saved_once(wecom_client_and_dir) -> None:
    client, data_dir = wecom_client_and_dir
    for _ in range(2):
        encrypted = encrypt_for_test(
            sample_wecom_xml("wecom-duplicate"), WECOM_CORP_ID.encode()
        )
        response = client.post(
            "/api/jjt/callback",
            params=signed_params(encrypted),
            content=encrypted_wecom_envelope(encrypted),
            headers={"content-type": "application/xml"},
        )
        assert response.status_code == 200
    lines = next(data_dir.glob("*.jsonl")).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_post_wecom_xml_missing_encrypt_returns_400(wecom_client_and_dir) -> None:
    client, _ = wecom_client_and_dir
    response = client.post(
        "/api/jjt/callback",
        params={"msg_signature": "x", "timestamp": "1", "nonce": "2"},
        content=b"<xml><AgentID>1000002</AgentID></xml>",
        headers={"content-type": "text/xml"},
    )
    assert response.status_code == 400


def test_post_wecom_invalid_plaintext_xml_returns_400(wecom_client_and_dir) -> None:
    client, _ = wecom_client_and_dir
    encrypted = encrypt_for_test(b"<xml><broken>", WECOM_CORP_ID.encode())
    response = client.post(
        "/api/jjt/callback",
        params=signed_params(encrypted),
        content=encrypted_wecom_envelope(encrypted),
        headers={"content-type": "text/xml"},
    )
    assert response.status_code == 400


def test_post_wecom_receive_id_mismatch_returns_400(wecom_client_and_dir) -> None:
    client, _ = wecom_client_and_dir
    encrypted = encrypt_for_test(sample_wecom_xml(), b"wrong-corp-id")
    response = client.post(
        "/api/jjt/callback",
        params=signed_params(encrypted),
        content=encrypted_wecom_envelope(encrypted),
        headers={"content-type": "text/xml"},
    )
    assert response.status_code == 400
