"""对正在运行的服务执行企业微信兼容回调端到端自检。"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from Crypto.Cipher import AES
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import ConfigurationError, Settings  # noqa: E402
from app.services.crypto_service import JJTCryptoService  # noqa: E402


class VerificationError(RuntimeError):
    """回调自检未通过。"""


def encrypt_for_verification(
    content: bytes, encoding_aes_key: str, receive_id: str
) -> str:
    """仅供自检使用，生成 WXBizMsgCrypt 兼容密文。"""
    aes_key = base64.b64decode(encoding_aes_key + "=", validate=True)
    receive_id_bytes = receive_id.encode("utf-8")
    plaintext = (
        secrets.token_bytes(16)
        + struct.pack(">I", len(content))
        + content
        + receive_id_bytes
    )
    padding_length = 32 - (len(plaintext) % 32)
    padded = plaintext + bytes([padding_length]) * padding_length
    ciphertext = AES.new(
        aes_key, AES.MODE_CBC, iv=aes_key[:16]
    ).encrypt(padded)
    return base64.b64encode(ciphertext).decode("ascii")


def run_verification(
    base_url: str,
    timeout_seconds: float = 5.0,
    check_storage: bool = True,
) -> None:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    settings = Settings.from_env()
    if not settings.receive_id:
        raise VerificationError(
            "企业微信验证要求 JJT_RECEIVE_ID 填写企业 CorpID"
        )
    normalized_base_url = _validate_base_url(base_url)
    crypto = JJTCryptoService(
        settings.callback_token,
        settings.encoding_aes_key,
        settings.receive_id,
    )

    print("[1/5] 配置格式有效（Token 和 AESKey 已隐藏）")
    health = _request(
        f"{normalized_base_url}/health", timeout_seconds=timeout_seconds
    )
    if health.status != 200:
        raise VerificationError(f"健康检查返回 HTTP {health.status}")
    try:
        health_body = json.loads(health.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError("健康检查没有返回合法 JSON") from exc
    if health_body != {"status": "ok", "service": "jjt-daily-report-bot"}:
        raise VerificationError("健康检查响应内容不符合预期")
    print(f"[2/5] 健康检查通过：{normalized_base_url}/health")

    timestamp = str(int(datetime.now(tz=ZoneInfo("UTC")).timestamp()))
    nonce = secrets.token_hex(8)
    echo_plaintext = b"wecom-url-verification-ok"
    encrypted_echo = encrypt_for_verification(
        echo_plaintext,
        settings.encoding_aes_key,
        settings.receive_id,
    )
    echo_signature = crypto.calculate_signature(timestamp, nonce, encrypted_echo)
    echo_query = urllib.parse.urlencode(
        {
            "msg_signature": echo_signature,
            "timestamp": timestamp,
            "nonce": nonce,
            "echostr": encrypted_echo,
        }
    )
    echo_response = _request(
        f"{normalized_base_url}/jjt-robot/callback?{echo_query}",
        timeout_seconds=timeout_seconds,
    )
    if echo_response.status != 200 or echo_response.body != echo_plaintext:
        raise VerificationError(
            f"GET URL 验证失败，HTTP {echo_response.status} 或明文不匹配"
        )
    print("[3/5] GET URL 验证通过：验签、解密和裸明文响应均正确")

    verification_msgid = f"local-wecom-check-{uuid.uuid4().hex}"
    plaintext_xml = _build_plaintext_xml(
        settings.receive_id, verification_msgid
    )
    encrypted_message = encrypt_for_verification(
        plaintext_xml,
        settings.encoding_aes_key,
        settings.receive_id,
    )
    message_nonce = secrets.token_hex(8)
    message_timestamp = str(int(datetime.now(tz=ZoneInfo("UTC")).timestamp()))
    message_signature = crypto.calculate_signature(
        message_timestamp, message_nonce, encrypted_message
    )
    message_query = urllib.parse.urlencode(
        {
            "msg_signature": message_signature,
            "timestamp": message_timestamp,
            "nonce": message_nonce,
        }
    )
    encrypted_envelope = (
        "<xml>"
        f"<ToUserName>{escape(settings.receive_id)}</ToUserName>"
        f"<Encrypt><![CDATA[{encrypted_message}]]></Encrypt>"
        "<AgentID>1000002</AgentID>"
        "</xml>"
    ).encode("utf-8")
    post_response = _request(
        f"{normalized_base_url}/jjt-robot/callback?{message_query}",
        method="POST",
        body=encrypted_envelope,
        headers={"Content-Type": "text/xml; charset=utf-8"},
        timeout_seconds=timeout_seconds,
    )
    if post_response.status != 200 or post_response.body != b"success":
        raise VerificationError(
            f"XML POST 验证失败，HTTP {post_response.status}，未返回 success"
        )
    print("[4/5] XML POST 通过：验签、解密、解析及 success 响应均正确")

    if check_storage:
        data_dir = settings.message_data_dir
        if not data_dir.is_absolute():
            data_dir = PROJECT_ROOT / data_dir
        target_file = data_dir / (
            datetime.now(ZoneInfo(settings.timezone)).date().isoformat() + ".jsonl"
        )
        if not _jsonl_contains_msgid(target_file, verification_msgid):
            raise VerificationError(
                f"没有在预期 JSONL 文件中找到自检消息：{target_file}"
            )
        print(f"[5/5] JSONL 落盘通过：{target_file}")
    else:
        print("[5/5] 已按参数跳过本地 JSONL 检查")

    print("企业微信兼容回调端到端自检全部通过。")


class _HTTPResult:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self.body = body


def _request(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: float,
) -> _HTTPResult:
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers or {},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return _HTTPResult(response.status, response.read())
    except urllib.error.HTTPError as exc:
        return _HTTPResult(exc.code, exc.read())
    except urllib.error.URLError as exc:
        reason = type(exc.reason).__name__
        raise VerificationError(f"无法连接回调服务（{reason}）") from exc


def _validate_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise VerificationError("base URL 必须是有效的 http:// 或 https:// 地址")
    if parsed.query or parsed.fragment:
        raise VerificationError("base URL 不能包含查询参数或片段")
    return normalized


def _build_plaintext_xml(receive_id: str, msgid: str) -> bytes:
    return (
        "<xml>"
        f"<ToUserName>{escape(receive_id)}</ToUserName>"
        "<FromUserName><![CDATA[local-verification-user]]></FromUserName>"
        f"<CreateTime>{int(datetime.now(tz=ZoneInfo('UTC')).timestamp())}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        "<Content><![CDATA[企业微信 XML 本地回调验证]]></Content>"
        f"<MsgId>{msgid}</MsgId>"
        "<AgentID>1000002</AgentID>"
        "</xml>"
    ).encode("utf-8")


def _jsonl_contains_msgid(path: Path, msgid: str) -> bool:
    if not path.is_file():
        return False
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    record: Any = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("msgid") == msgid:
                    return True
    except OSError as exc:
        raise VerificationError(f"无法读取 JSONL 文件：{path}") from exc
    return False


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="验证企业微信兼容 GET/POST 回调链路，不显示密钥。"
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="服务基础地址；默认 http://127.0.0.1:8000",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="单个 HTTP 请求超时秒数；默认 5",
    )
    parser.add_argument(
        "--skip-storage-check",
        action="store_true",
        help="服务不在当前电脑运行时跳过本地 JSONL 检查",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.timeout <= 0:
        print("验证失败：timeout 必须大于 0", file=sys.stderr)
        return 2
    try:
        run_verification(
            args.base_url,
            timeout_seconds=args.timeout,
            check_storage=not args.skip_storage_check,
        )
    except (ConfigurationError, VerificationError, ValueError) as exc:
        print(f"验证失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
