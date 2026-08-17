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


SYSTEM_PROMPT = """你负责先判断消息与施工日报的相关性，再提取结构化 JSON。
不得猜测、补编或生成原文不存在的数据；缺失字段必须为 null，并列入 missing_fields。
只返回一个 JSON 对象，不要 Markdown、解释、SQL 或其他文本。
JSON 必须包含以下全部键：project_name, report_date, weather,
management_count, worker_count, equipment, work_items, tomorrow_plan,
safety_status, quality_status, missing_fields, confidence,
relevance_status, relevance_reason, relevance_confidence。
relevance_status 只能是 report、related_update、ordinary_chat、uncertain：
- report：包含一份日报的项目、日期、人员、机械或多项施工内容等实质信息；
- related_update：是施工进度或现场补充，但不是一份完整日报；
- ordinary_chat：催问日报、会议通知、确认回复或其他普通聊天；
- uncertain：证据冲突，无法可靠判断。
relevance_reason 用一句简短中文说明依据，relevance_confidence 为 0 到 1。
report_date 使用 YYYY-MM-DD；confidence 为 0 到 1 的数字。
原文只有月日没有年份时 report_date 返回 null，年份由后端按消息时间补全。
原文明示“施工总人数/总施工人数”时，worker_count 必须使用该总数，不能改用
各施工子项人数相加。management_count 只统计原文明确标注为“管理人员”的人数；
若有多项单位管理人员则相加，不能仅因岗位位于“管理及后台”等章节就把安全文明
施工、加工人员或其他后台岗位算作管理人员。
equipment 为 null 或对象数组，每项严格包含 name、count、unit。
work_items 为 null 或对象数组，每项严格包含 location、content、progress，
其中 content 必须是原文明示的非空施工内容；无法确定 content 的片段不要加入数组，
缺失的 location 或 progress 使用 null。"""


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
