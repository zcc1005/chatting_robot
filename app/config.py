"""应用配置及启动时校验。"""

from __future__ import annotations

import base64
import binascii
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


class ConfigurationError(ValueError):
    """配置缺失或格式不合法。"""


def _read_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} 必须是 true 或 false")


def _read_float(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} 必须是数字") from exc


def _read_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} 必须是整数") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    # 显式构造 Settings 时默认保留历史回调行为；from_env 使用离线开发默认值。
    callback_token: str = ""
    encoding_aes_key: str = ""
    receive_id: str = ""
    message_data_dir: Path = Path("./data/messages")
    log_level: str = "INFO"
    timezone: str = "Asia/Shanghai"
    app_env: str = "development"
    enable_mock_api: bool = False
    enable_jjt_callback: bool = True
    database_url: str = "sqlite:///./data/jjt_bot.db"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_base_url: str = ""
    llm_timeout_seconds: float = 90.0
    llm_max_retries: int = 1
    vision_api_key: str = ""
    vision_model: str = ""
    vision_base_url: str = ""
    vision_timeout_seconds: float = 90.0
    vision_max_retries: int = 1
    image_download_timeout_seconds: float = 15.0
    image_max_bytes: int = 10_000_000
    enable_auto_image_recognition: bool = False
    enable_real_response_send: bool = False
    response_send_timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        settings = cls(
            callback_token=os.getenv("JJT_CALLBACK_TOKEN", ""),
            encoding_aes_key=os.getenv("JJT_ENCODING_AES_KEY", ""),
            receive_id=os.getenv("JJT_RECEIVE_ID", ""),
            message_data_dir=Path(
                os.getenv("JJT_MESSAGE_DATA_DIR", "./data/messages")
            ),
            log_level=os.getenv("JJT_LOG_LEVEL", "INFO"),
            timezone=os.getenv("JJT_TIMEZONE", "Asia/Shanghai"),
            app_env=os.getenv("APP_ENV", "development"),
            enable_mock_api=_read_bool("ENABLE_MOCK_API", True),
            enable_jjt_callback=_read_bool("ENABLE_JJT_CALLBACK", False),
            database_url=os.getenv(
                "DATABASE_URL", "sqlite:///./data/jjt_bot.db"
            ),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_model=os.getenv("LLM_MODEL", ""),
            llm_base_url=os.getenv("LLM_BASE_URL", ""),
            llm_timeout_seconds=_read_float("LLM_TIMEOUT_SECONDS", 90.0),
            llm_max_retries=_read_int("LLM_MAX_RETRIES", 1),
            vision_api_key=os.getenv(
                "VISION_API_KEY", os.getenv("LLM_API_KEY", "")
            ),
            vision_model=os.getenv("VISION_MODEL", ""),
            vision_base_url=os.getenv(
                "VISION_BASE_URL", os.getenv("LLM_BASE_URL", "")
            ),
            vision_timeout_seconds=_read_float(
                "VISION_TIMEOUT_SECONDS", 90.0
            ),
            vision_max_retries=_read_int("VISION_MAX_RETRIES", 1),
            image_download_timeout_seconds=_read_float(
                "IMAGE_DOWNLOAD_TIMEOUT_SECONDS", 15.0
            ),
            image_max_bytes=_read_int("IMAGE_MAX_BYTES", 10_000_000),
            enable_auto_image_recognition=_read_bool(
                "ENABLE_AUTO_IMAGE_RECOGNITION", False
            ),
            enable_real_response_send=_read_bool(
                "ENABLE_REAL_RESPONSE_SEND", False
            ),
            response_send_timeout_seconds=_read_float(
                "RESPONSE_SEND_TIMEOUT_SECONDS", 10.0
            ),
        )
        settings.validate()
        return settings

    @property
    def mock_api_available(self) -> bool:
        return self.app_env.lower() == "development" and self.enable_mock_api

    @property
    def llm_configured(self) -> bool:
        return all(
            value.strip()
            for value in (self.llm_api_key, self.llm_model, self.llm_base_url)
        )

    @property
    def vision_configured(self) -> bool:
        return all(
            value.strip()
            for value in (
                self.vision_api_key,
                self.vision_model,
                self.vision_base_url,
            )
        )

    def validate(self) -> None:
        if not self.app_env.strip():
            raise ConfigurationError("APP_ENV 不能为空")
        if not self.database_url.startswith("sqlite:///"):
            raise ConfigurationError("DATABASE_URL 本阶段必须使用 SQLite")
        if not self.message_data_dir:
            raise ConfigurationError("JJT_MESSAGE_DATA_DIR 不能为空")
        if self.llm_timeout_seconds <= 0:
            raise ConfigurationError("LLM_TIMEOUT_SECONDS 必须大于 0")
        if not 0 <= self.llm_max_retries <= 3:
            raise ConfigurationError("LLM_MAX_RETRIES 必须在 0 到 3 之间")
        if self.vision_timeout_seconds <= 0:
            raise ConfigurationError("VISION_TIMEOUT_SECONDS 必须大于 0")
        if not 0 <= self.vision_max_retries <= 3:
            raise ConfigurationError("VISION_MAX_RETRIES 必须在 0 到 3 之间")
        if self.image_download_timeout_seconds <= 0:
            raise ConfigurationError(
                "IMAGE_DOWNLOAD_TIMEOUT_SECONDS 必须大于 0"
            )
        if not 1 <= self.image_max_bytes <= 50_000_000:
            raise ConfigurationError("IMAGE_MAX_BYTES 必须在 1 到 50000000 之间")
        if self.response_send_timeout_seconds <= 0:
            raise ConfigurationError(
                "RESPONSE_SEND_TIMEOUT_SECONDS 必须大于 0"
            )
        resolved_log_level = getattr(logging, self.log_level.upper(), None)
        if not isinstance(resolved_log_level, int):
            raise ConfigurationError(f"无效的 JJT_LOG_LEVEL: {self.log_level}")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ConfigurationError(
                f"无效的 JJT_TIMEZONE: {self.timezone}"
            ) from exc

        if self.enable_jjt_callback:
            self._validate_callback_crypto()

    def _validate_callback_crypto(self) -> None:
        if not self.callback_token:
            raise ConfigurationError(
                "ENABLE_JJT_CALLBACK=true 时 JJT_CALLBACK_TOKEN 不能为空"
            )
        if len(self.encoding_aes_key) != 43:
            raise ConfigurationError(
                "ENABLE_JJT_CALLBACK=true 时 JJT_ENCODING_AES_KEY 必须是 43 个字符"
            )
        try:
            decoded_key = base64.b64decode(
                self.encoding_aes_key + "=", validate=True
            )
        except (binascii.Error, ValueError) as exc:
            raise ConfigurationError(
                "JJT_ENCODING_AES_KEY 不是有效的 Base64 编码"
            ) from exc
        if len(decoded_key) != 32:
            raise ConfigurationError(
                "JJT_ENCODING_AES_KEY 解码后必须是 32 字节"
            )
