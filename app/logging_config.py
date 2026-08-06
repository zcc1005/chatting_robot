"""日志配置与安全脱敏。"""

from __future__ import annotations

import logging
from typing import Any


_SENSITIVE_KEYS = {
    "response_url",
    "token",
    "access_token",
    "download_token",
    "encoding_aes_key",
}


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )


def redact_for_logging(value: Any) -> Any:
    """递归复制 JSON 值，同时隐藏 URL 和 token 类敏感字段。"""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if (
                normalized in _SENSITIVE_KEYS
                or normalized.endswith("_token")
                or normalized.endswith("_url")
                or normalized == "url"
            ):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = redact_for_logging(item)
        return redacted
    if isinstance(value, list):
        return [redact_for_logging(item) for item in value]
    return value
