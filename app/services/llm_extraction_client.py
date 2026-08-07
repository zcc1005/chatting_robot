"""只负责把单条日报正文提交给兼容 Chat Completions 的大模型。"""

from __future__ import annotations

import time
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
        max_retries: int = 1,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries

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
                    retry_note = (
                        f"（已自动重试 {self._max_retries} 次）"
                        if self._max_retries
                        else ""
                    )
                    raise LLMClientTimeout(
                        f"大模型调用超时{retry_note}"
                    ) from exc
                except httpx.HTTPStatusError as exc:
                    status_code = exc.response.status_code
                    if (
                        status_code == 429 or status_code >= 500
                    ) and attempt < self._max_retries:
                        _wait_before_retry(attempt)
                        continue
                    raise LLMClientError("大模型请求失败") from exc
                except httpx.RequestError as exc:
                    if attempt < self._max_retries:
                        _wait_before_retry(attempt)
                        continue
                    raise LLMClientError(
                        "大模型网络请求失败（已自动重试）"
                    ) from exc
            else:  # pragma: no cover - 循环只会 break 或抛出异常
                raise LLMClientError("大模型请求失败")

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


def _wait_before_retry(attempt: int) -> None:
    time.sleep(min(0.5 * (2**attempt), 2.0))
