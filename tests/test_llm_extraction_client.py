from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.services import llm_extraction_client
from app.services.llm_extraction_client import (
    LLMClientError,
    LLMClientTimeout,
    OpenAICompatibleExtractionClient,
)


VALID_RESPONSE = {
    "choices": [{"message": {"content": '{"project_name":"测试项目"}'}}]
}


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = VALID_RESPONSE if payload is None else payload
        self.request = httpx.Request("POST", "https://llm.invalid/v1/chat/completions")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "request failed",
                request=self.request,
                response=httpx.Response(
                    self.status_code,
                    request=self.request,
                ),
            )

    def json(self) -> Any:
        return self._payload


class FakeHttpClient:
    def __init__(self, effects: list[FakeResponse | Exception]) -> None:
        self.effects = effects
        self.calls = 0

    def __enter__(self) -> "FakeHttpClient":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def post(self, *_args, **_kwargs) -> FakeResponse:
        effect = self.effects[self.calls]
        self.calls += 1
        if isinstance(effect, Exception):
            raise effect
        return effect


def make_client(max_retries: int = 1) -> OpenAICompatibleExtractionClient:
    return OpenAICompatibleExtractionClient(
        api_key="test-key",
        model="test-model",
        base_url="https://llm.invalid/v1",
        timeout_seconds=90,
        max_retries=max_retries,
    )


def install_fake_client(
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeHttpClient,
) -> list[httpx.Timeout]:
    captured_timeouts: list[httpx.Timeout] = []

    def factory(*, timeout: httpx.Timeout) -> FakeHttpClient:
        captured_timeouts.append(timeout)
        return fake

    monkeypatch.setattr(llm_extraction_client.httpx, "Client", factory)
    monkeypatch.setattr(llm_extraction_client.time, "sleep", lambda _seconds: None)
    return captured_timeouts


def read_timeout() -> httpx.ReadTimeout:
    request = httpx.Request("POST", "https://llm.invalid/v1/chat/completions")
    return httpx.ReadTimeout("slow response", request=request)


def test_timeout_is_retried_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeHttpClient([read_timeout(), FakeResponse()])
    captured_timeouts = install_fake_client(monkeypatch, fake)

    result = make_client().extract("测试日报")

    assert result == '{"project_name":"测试项目"}'
    assert fake.calls == 2
    assert captured_timeouts[0].connect == 10
    assert captured_timeouts[0].read == 90


def test_timeout_after_retry_has_clear_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeHttpClient([read_timeout(), read_timeout()])
    install_fake_client(monkeypatch, fake)

    with pytest.raises(LLMClientTimeout, match="已自动重试 1 次"):
        make_client().extract("测试日报")

    assert fake.calls == 2


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_transient_http_status_is_retried(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    fake = FakeHttpClient([FakeResponse(status_code), FakeResponse()])
    install_fake_client(monkeypatch, fake)

    assert make_client().extract("测试日报")
    assert fake.calls == 2


def test_non_retryable_http_status_fails_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeHttpClient([FakeResponse(400), FakeResponse()])
    install_fake_client(monkeypatch, fake)

    with pytest.raises(LLMClientError, match="大模型请求失败"):
        make_client().extract("测试日报")

    assert fake.calls == 1
