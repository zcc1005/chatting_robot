from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.services import image_recognition_client
from app.services.image_recognition_client import (
    ImageBinary,
    ImageRecognitionClientTimeout,
    OpenAICompatibleImageRecognitionClient,
)


VALID_CONTENT = '{"project_name":null,"confidence":0.8}'


class FakeResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": VALID_CONTENT}}]}


class FakeHttpClient:
    def __init__(self, effects: list[FakeResponse | Exception]) -> None:
        self.effects = effects
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "FakeHttpClient":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def post(self, _url: str, **kwargs) -> FakeResponse:
        self.calls.append(kwargs)
        effect = self.effects[len(self.calls) - 1]
        if isinstance(effect, Exception):
            raise effect
        return effect


def _client() -> OpenAICompatibleImageRecognitionClient:
    return OpenAICompatibleImageRecognitionClient(
        api_key="vision-key",
        model="vision-model",
        base_url="https://vision.invalid/v1",
        timeout_seconds=90,
        max_retries=1,
    )


def _image() -> ImageBinary:
    return ImageBinary(
        data=b"\x89PNG\r\n\x1a\nimage",
        media_type="image/png",
        sha256="0" * 64,
    )


def test_vision_client_sends_image_as_data_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeHttpClient([FakeResponse()])
    monkeypatch.setattr(
        image_recognition_client.httpx,
        "Client",
        lambda *, timeout: fake,
    )
    result = _client().recognize(_image())
    assert result == VALID_CONTENT
    request_json = fake.calls[0]["json"]
    assert request_json["model"] == "vision-model"
    image_url = request_json["messages"][1]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/png;base64,")
    assert request_json["response_format"] == {"type": "json_object"}


def test_vision_timeout_is_retried_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("POST", "https://vision.invalid/v1/chat/completions")
    fake = FakeHttpClient(
        [httpx.ReadTimeout("slow", request=request), FakeResponse()]
    )
    monkeypatch.setattr(
        image_recognition_client.httpx,
        "Client",
        lambda *, timeout: fake,
    )
    monkeypatch.setattr(image_recognition_client.time, "sleep", lambda _value: None)
    assert _client().recognize(_image()) == VALID_CONTENT
    assert len(fake.calls) == 2


def test_vision_timeout_after_retry_is_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("POST", "https://vision.invalid/v1/chat/completions")
    fake = FakeHttpClient(
        [
            httpx.ReadTimeout("slow", request=request),
            httpx.ReadTimeout("still slow", request=request),
        ]
    )
    monkeypatch.setattr(
        image_recognition_client.httpx,
        "Client",
        lambda *, timeout: fake,
    )
    monkeypatch.setattr(image_recognition_client.time, "sleep", lambda _value: None)
    with pytest.raises(ImageRecognitionClientTimeout, match="图片识别调用超时"):
        _client().recognize(_image())
    assert len(fake.calls) == 2
