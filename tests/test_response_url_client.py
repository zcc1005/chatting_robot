from __future__ import annotations

import httpx
import pytest

from app.services import response_url_client
from app.services.response_url_client import RealResponseUrlClient


class FakeHttpClient:
    def __init__(self, response: httpx.Response | Exception, **_kwargs) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def post(self, url: str, *, json: dict[str, object]) -> httpx.Response:
        self.calls.append((url, json))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def install_fake(monkeypatch: pytest.MonkeyPatch, fake: FakeHttpClient) -> None:
    monkeypatch.setattr(response_url_client.httpx, "Client", lambda **_kwargs: fake)


def test_real_client_posts_documented_markdown_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeHttpClient(httpx.Response(200, json={"errcode": 0, "errmsg": "ok"}))
    install_fake(monkeypatch, fake)

    result = RealResponseUrlClient(timeout_seconds=10).send(
        response_url="https://jjt.example/cgi-bin/aibot/response?response_code=once",
        content="# 施工日报\n\n- 项目：测试项目",
    )

    assert result.success is True
    assert result.transport == "real"
    assert fake.calls == [
        (
            "https://jjt.example/cgi-bin/aibot/response?response_code=once",
            {
                "msgtype": "markdown",
                "markdown": {"content": "# 施工日报\n\n- 项目：测试项目"},
            },
        )
    ]


def test_real_client_rejects_non_https_without_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        response_url_client.httpx,
        "Client",
        lambda **_kwargs: pytest.fail("不安全 URL 不应发起请求"),
    )
    result = RealResponseUrlClient(timeout_seconds=10).send(
        response_url="http://jjt.example/reply",
        content="test",
    )
    assert result.success is False
    assert result.error_type == "unsafe_response_url"


def test_real_client_records_platform_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeHttpClient(
        httpx.Response(200, json={"errcode": 40001, "errmsg": "invalid code"})
    )
    install_fake(monkeypatch, fake)
    result = RealResponseUrlClient(timeout_seconds=10).send(
        response_url="https://jjt.example/reply",
        content="test",
    )
    assert result.success is False
    assert result.error_type == "response_platform_error"
    assert "40001" in (result.error_message or "")


def test_real_client_records_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("POST", "https://jjt.example/reply")
    fake = FakeHttpClient(httpx.ReadTimeout("slow", request=request))
    install_fake(monkeypatch, fake)
    result = RealResponseUrlClient(timeout_seconds=10).send(
        response_url="https://jjt.example/reply",
        content="test",
    )
    assert result.success is False
    assert result.error_type == "response_timeout"
