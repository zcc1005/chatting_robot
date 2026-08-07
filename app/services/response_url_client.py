"""隔离 response_url 发送传输；默认 Mock 永不访问网络。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit


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
    """真实传输安全边界。

    本地提供的交建通文档没有 response_url 请求体协议。为避免猜测字段，
    即使显式启用真实模式，本客户端也会在网络请求前返回可审计失败；待平台
    联调确认请求体后，只需替换本类内部传输实现，不影响业务状态机。
    """

    transport = "real"

    def __init__(self, *, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds

    def send(self, *, response_url: str, content: str) -> ResponseUrlSendResult:
        del content
        parsed = urlsplit(response_url)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            return ResponseUrlSendResult(
                success=False,
                transport=self.transport,
                error_type="unsafe_response_url",
                error_message="真实发送只允许有效的 HTTPS response_url",
            )
        return ResponseUrlSendResult(
            success=False,
            transport=self.transport,
            error_type="response_protocol_not_confirmed",
            error_message="交建通 response_url 请求协议尚未完成联调确认",
        )
