"""只负责把单条日报正文提交给兼容 Chat Completions 的大模型。"""

from __future__ import annotations

from typing import Any, Protocol

import httpx


class ReportExtractionClient(Protocol):
    def extract(self, text_content: str) -> str:
        """返回大模型生成的原始 JSON 字符串。"""


class LLMClientError(RuntimeError):
    """大模型请求或响应信封不合法。"""


class LLMClientTimeout(LLMClientError):
    """大模型调用超时。"""


SYSTEM_PROMPT = """你只负责从一条施工日报原文中提取结构化 JSON。
不得猜测、补编或生成原文不存在的数据；缺失字段必须为 null，并列入 missing_fields。
只返回一个 JSON 对象，不要 Markdown、解释、SQL 或其他文本。
JSON 必须包含以下全部键：project_name, report_date, weather,
management_count, worker_count, equipment, work_items, tomorrow_plan,
safety_status, quality_status, missing_fields, confidence。
report_date 使用 YYYY-MM-DD；confidence 为 0 到 1 的数字。
equipment 为 null 或对象数组，每项严格包含 name、count、unit。
work_items 为 null 或对象数组，每项严格包含 location、content、progress，
其中缺失的 location 或 progress 使用 null。"""


class OpenAICompatibleExtractionClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._timeout_seconds = timeout_seconds

    def extract(self, text_content: str) -> str:
        request_json = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text_content},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        try:
            with httpx.Client(timeout=self._timeout_seconds) as client:
                response = client.post(
                    self._endpoint,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=request_json,
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMClientTimeout("大模型调用超时") from exc
        except httpx.HTTPError as exc:
            raise LLMClientError("大模型请求失败") from exc

        try:
            payload: Any = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMClientError("大模型响应格式不正确") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMClientError("大模型响应内容为空")
        if len(content) > 1_000_000:
            raise LLMClientError("大模型响应内容过大")
        return content
