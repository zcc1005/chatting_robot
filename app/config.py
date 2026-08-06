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
        )
        settings.validate()
        return settings

    @property
    def mock_api_available(self) -> bool:
        return self.app_env.lower() == "development" and self.enable_mock_api

    def validate(self) -> None:
        if not self.app_env.strip():
            raise ConfigurationError("APP_ENV 不能为空")
        if not self.database_url.startswith("sqlite:///"):
            raise ConfigurationError("DATABASE_URL 本阶段必须使用 SQLite")
        if not self.message_data_dir:
            raise ConfigurationError("JJT_MESSAGE_DATA_DIR 不能为空")
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

