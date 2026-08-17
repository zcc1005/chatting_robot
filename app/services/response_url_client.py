"""隔离 response_url 发送传输；默认 Mock 永不访问网络。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Protocol
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True, slots=True)
class ResponseUrlSendResult:
    success: bool
    transport: str
    http_status_code: int | None = None
    error_type: str | None = None
    error_message: str | None = None


class ResponseUrlClient(Protocol):
    transport: str

    def send(self, *, response_url: str, content: str) -> ResponseUrlSendResult:
        """发送已经由业务层选定的确定性日报文本。"""


class MockResponseUrlClient:
    """离线模拟传输，不解析、打开或请求 response_url。"""

    transport = "mock"

    def __init__(self, *, force_failure: bool = False) -> None:
        self.force_failure = force_failure

    def send(self, *, response_url: str, content: str) -> ResponseUrlSendResult:
        del response_url, content
        if self.force_failure:
            return ResponseUrlSendResult(
                success=False,
                transport=self.transport,
                error_type="mock_send_failure",
                error_message="模拟发送失败",
            )
        return ResponseUrlSendResult(
            success=True,
            transport=self.transport,
            http_status_code=200,
        )


class RealResponseUrlClient:
    """按照交建通机器人协议向一次性 response_url 回复 Markdown。"""

    transport = "real"

    def __init__(self, *, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds

    def send(self, *, response_url: str, content: str) -> ResponseUrlSendResult:
        parsed = urlsplit(response_url)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            return ResponseUrlSendResult(
                success=False,
                transport=self.transport,
                error_type="unsafe_response_url",
                error_message="真实发送只允许有效的 HTTPS response_url",
            )
        payload = {
            "msgtype": "markdown",
            "markdown": {"content": content},
        }
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self.timeout_seconds),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = client.post(response_url, json=payload)
        except httpx.TimeoutException:
            return ResponseUrlSendResult(
                success=False,
                transport=self.transport,
                error_type="response_timeout",
                error_message="交建通 response_url 请求超时",
            )
        except httpx.HTTPError:
            return ResponseUrlSendResult(
                success=False,
                transport=self.transport,
                error_type="response_network_error",
                error_message="交建通 response_url 网络请求失败",
            )

        status_code = response.status_code
        if not 200 <= status_code < 300:
            return ResponseUrlSendResult(
                success=False,
                transport=self.transport,
                http_status_code=status_code,
                error_type="response_http_error",
                error_message=f"交建通 response_url 返回 HTTP {status_code}",
            )

        platform_error = _platform_error(response)
        if platform_error is not None:
            return ResponseUrlSendResult(
                success=False,
                transport=self.transport,
                http_status_code=status_code,
                error_type="response_platform_error",
                error_message=platform_error,
            )
        return ResponseUrlSendResult(
            success=True,
            transport=self.transport,
            http_status_code=status_code,
        )


def _platform_error(response: httpx.Response) -> str | None:
    if not response.content:
        return None
    try:
        body = response.json()
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    errcode = body.get("errcode")
    if errcode in (None, 0, "0"):
        return None
    return f"交建通回复失败，errcode={str(errcode)[:32]}"
