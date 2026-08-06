"""API 输入模型。"""

from pydantic import BaseModel, ConfigDict, Field


class EncryptedCallback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    encrypt: str = Field(min_length=1)

