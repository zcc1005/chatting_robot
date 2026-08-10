"""安全读取施工图片并调用兼容 Chat Completions 的视觉模型。"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from app.models.database_models import MessageAttachment


class ImageRecognitionClientError(RuntimeError):
    """图片读取、视觉模型请求或响应信封不合法。"""


class ImageRecognitionClientTimeout(ImageRecognitionClientError):
    """图片下载或视觉模型调用超时。"""


@dataclass(frozen=True, slots=True)
class ImageBinary:
    data: bytes
    media_type: str
    sha256: str


class ImageContentLoader(Protocol):
    def load(self, attachment: MessageAttachment) -> ImageBinary:
        """读取附件内容，不改变数据库。"""


class ImageRecognitionClient(Protocol):
    def recognize(self, image: ImageBinary) -> str:
        """返回严格 JSON 字符串，不接触数据库。"""


class SafeImageContentLoader:
    def __init__(
        self,
        *,
        local_root: Path,
        timeout_seconds: float,
        max_bytes: int,
    ) -> None:
        self._local_root = local_root.resolve()
        self._timeout_seconds = timeout_seconds
        self._max_bytes = max_bytes

    def load(self, attachment: MessageAttachment) -> ImageBinary:
        if attachment.attachment_type != "image":
            raise ImageRecognitionClientError("附件不是图片")
        if attachment.local_path:
            return self._load_local(Path(attachment.local_path))
        return self._download_https(attachment.remote_url)

    def _load_local(self, path: Path) -> ImageBinary:
        resolved = path.resolve()
        try:
            resolved.relative_to(self._local_root)
        except ValueError as exc:
            raise ImageRecognitionClientError("本地图片路径不在允许目录内") from exc
        try:
            size = resolved.stat().st_size
        except OSError as exc:
            raise ImageRecognitionClientError("本地图片不存在或不可读") from exc
        if size <= 0 or size > self._max_bytes:
            raise ImageRecognitionClientError("图片大小超出允许范围")
        try:
            data = resolved.read_bytes()
        except OSError as exc:
            raise ImageRecognitionClientError("本地图片读取失败") from exc
        return validate_image_bytes(data)

    def _download_https(self, remote_url: str) -> ImageBinary:
        try:
            parsed = urlsplit(remote_url)
        except ValueError as exc:
            raise ImageRecognitionClientError("图片地址格式不合法") from exc
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise ImageRecognitionClientError("远程图片只允许 HTTPS 地址")
        _ensure_public_host(parsed.hostname, parsed.port or 443)

        timeout = httpx.Timeout(
            self._timeout_seconds,
            connect=min(self._timeout_seconds, 10.0),
        )
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                with client.stream("GET", remote_url) as response:
                    if 300 <= response.status_code < 400:
                        raise ImageRecognitionClientError("图片下载不允许重定向")
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "")
                    if not content_type.lower().startswith("image/"):
                        raise ImageRecognitionClientError("远程内容不是图片")
                    content_length = response.headers.get("content-length")
                    if content_length:
                        try:
                            if int(content_length) > self._max_bytes:
                                raise ImageRecognitionClientError(
                                    "图片大小超出允许范围"
                                )
                        except ValueError:
                            pass
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > self._max_bytes:
                            raise ImageRecognitionClientError(
                                "图片大小超出允许范围"
                            )
                        chunks.append(chunk)
        except httpx.TimeoutException as exc:
            raise ImageRecognitionClientTimeout("图片下载超时") from exc
        except httpx.HTTPStatusError as exc:
            raise ImageRecognitionClientError("图片下载失败") from exc
        except httpx.RequestError as exc:
            raise ImageRecognitionClientError("图片网络请求失败") from exc
        return validate_image_bytes(b"".join(chunks))


VISION_SYSTEM_PROMPT = """你只负责识别一张施工现场图片并返回 JSON。
不得猜测、补编图片中不存在的信息。先读取图片内可见文字，再描述可见施工场景。
只返回一个 JSON 对象，不要 Markdown、解释、SQL 或其他文本。
JSON 必须严格包含以下全部键：project_name, report_date, captured_at,
weather, location, construction_content, ocr_text, scene_description, confidence。
无法识别的字段必须为 null；report_date 使用 YYYY-MM-DD。只有图片明确显示时区时，
captured_at 才使用带时区的 ISO 8601，否则必须为 null，并把原始时间保留在 ocr_text。
ocr_text 保留图片内有业务意义的可见文字；confidence 为 0 到 1 的数字。
不要从图片外推人员、机械数量或完成进度。"""


class OpenAICompatibleImageRecognitionClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
        max_retries: int = 1,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries

    def recognize(self, image: ImageBinary) -> str:
        encoded = base64.b64encode(image.data).decode("ascii")
        request_json = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": VISION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "请严格识别这张施工图片，不要补充图片外信息。",
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": (
                                    f"data:{image.media_type};base64,{encoded}"
                                )
                            },
                        },
                    ],
                },
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        timeout = httpx.Timeout(
            self._timeout_seconds,
            connect=min(self._timeout_seconds, 10.0),
        )
        with httpx.Client(timeout=timeout) as client:
            for attempt in range(self._max_retries + 1):
                try:
                    response = client.post(
                        self._endpoint,
                        headers={
                            "Authorization": f"Bearer {self._api_key}",
                            "Content-Type": "application/json",
                        },
                        json=request_json,
                    )
                    response.raise_for_status()
                    break
                except httpx.TimeoutException as exc:
                    if attempt < self._max_retries:
                        _wait_before_retry(attempt)
                        continue
                    raise ImageRecognitionClientTimeout(
                        "图片识别调用超时"
                    ) from exc
                except httpx.HTTPStatusError as exc:
                    status_code = exc.response.status_code
                    if (
                        status_code == 429 or status_code >= 500
                    ) and attempt < self._max_retries:
                        _wait_before_retry(attempt)
                        continue
                    raise ImageRecognitionClientError("图片识别请求失败") from exc
                except httpx.RequestError as exc:
                    if attempt < self._max_retries:
                        _wait_before_retry(attempt)
                        continue
                    raise ImageRecognitionClientError(
                        "图片识别网络请求失败"
                    ) from exc
            else:  # pragma: no cover
                raise ImageRecognitionClientError("图片识别请求失败")

        try:
            payload: Any = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ImageRecognitionClientError("图片识别响应格式不正确") from exc
        if not isinstance(content, str) or not content.strip():
            raise ImageRecognitionClientError("图片识别响应内容为空")
        if len(content) > 1_000_000:
            raise ImageRecognitionClientError("图片识别响应内容过大")
        return content


def validate_image_bytes(data: bytes) -> ImageBinary:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        media_type = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        media_type = "image/jpeg"
    elif len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        media_type = "image/webp"
    else:
        raise ImageRecognitionClientError("仅支持 PNG、JPEG 或 WEBP 图片")
    return ImageBinary(
        data=data,
        media_type=media_type,
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _ensure_public_host(hostname: str, port: int) -> None:
    try:
        addresses = socket.getaddrinfo(
            hostname, port, type=socket.SOCK_STREAM
        )
    except OSError as exc:
        raise ImageRecognitionClientError("图片地址域名解析失败") from exc
    if not addresses:
        raise ImageRecognitionClientError("图片地址没有可用网络地址")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ImageRecognitionClientError("图片地址指向非公网网络")


def _wait_before_retry(attempt: int) -> None:
    time.sleep(min(0.5 * (2**attempt), 2.0))
