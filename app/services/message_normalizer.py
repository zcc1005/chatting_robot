"""把交建通明文消息转换成统一消息模型。"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.models.schemas import NormalizedAttachment, NormalizedMessage


class MessageNormalizationError(ValueError):
    """明文消息缺少必填字段或字段格式不合法。"""


SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")


def normalize_jjt_message(
    payload: dict[str, Any], source: Literal["jjt", "mock"]
) -> NormalizedMessage:
    if not isinstance(payload, dict):
        raise MessageNormalizationError("消息必须是 JSON 对象")

    msgid = _required_string(payload, "msgid")
    chattype = _required_string(payload, "chattype")
    if chattype not in {"group", "single"}:
        raise MessageNormalizationError("chattype 必须是 group 或 single")
    msgtype = _required_string(payload, "msgtype")

    sender = payload.get("from")
    if not isinstance(sender, dict):
        raise MessageNormalizationError("缺少必填字段 from.userid")
    sender_userid = _required_string(sender, "userid", path="from.userid")
    chatid = _resolve_chatid(payload, chattype, sender_userid)
    chat_name = _resolve_chat_name(payload, chattype)

    text_content = ""
    attachments: list[NormalizedAttachment] = []
    if msgtype == "text":
        text_content = _nested_text(payload.get("text"), "content")
    elif msgtype == "image":
        attachments.append(_attachment_from_container(payload.get("image"), "image"))
    elif msgtype == "file":
        attachments.append(_attachment_from_container(payload.get("file"), "file"))
    elif msgtype == "mixed":
        text_content, attachments = _normalize_mixed(payload.get("mixed"))
    process_status = (
        "received" if msgtype in {"text", "image", "mixed", "file"} else "unsupported"
    )

    response_url = payload.get("response_url")
    if response_url is not None and not isinstance(response_url, str):
        raise MessageNormalizationError("response_url 必须是字符串或空值")

    aibotid = payload.get("aibotid")
    if aibotid is not None and not isinstance(aibotid, str):
        raise MessageNormalizationError("aibotid 必须是字符串或空值")

    return NormalizedMessage(
        source=source,
        msgid=msgid,
        aibotid=aibotid,
        chatid=chatid,
        chat_name=chat_name,
        chattype=chattype,
        sender_userid=sender_userid,
        msgtype=msgtype,
        text_content=text_content,
        attachments=attachments,
        response_url=response_url,
        received_at=_extract_received_at(payload),
        process_status=process_status,
        raw_payload=copy.deepcopy(payload),
    )


def _required_string(
    container: dict[str, Any], key: str, *, path: str | None = None
) -> str:
    value = container.get(key)
    field_name = path or key
    if not isinstance(value, str) or not value.strip():
        raise MessageNormalizationError(f"缺少必填字段 {field_name}")
    return value


def _resolve_chatid(
    payload: dict[str, Any], chattype: str, sender_userid: str
) -> str:
    value = payload.get("chatid")
    if isinstance(value, str) and value.strip():
        return value
    if chattype == "single":
        return f"single:{sender_userid}"
    raise MessageNormalizationError("群聊消息缺少必填字段 chatid")


def _resolve_chat_name(payload: dict[str, Any], chattype: str) -> str | None:
    if chattype != "group":
        return None
    for key in ("chatname", "chat_name", "group_name", "groupname"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:500]
    chat = payload.get("chat")
    if isinstance(chat, dict):
        for key in ("name", "title"):
            value = chat.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:500]
    return None


def _nested_text(container: Any, key: str) -> str:
    if container is None:
        return ""
    if not isinstance(container, dict):
        raise MessageNormalizationError(f"{key} 所在字段必须是对象")
    value = container.get(key, "")
    if not isinstance(value, str):
        raise MessageNormalizationError(f"{key} 必须是字符串")
    return value


def _attachment_from_container(
    container: Any, attachment_type: Literal["image", "file"]
) -> NormalizedAttachment:
    if not isinstance(container, dict):
        raise MessageNormalizationError(f"{attachment_type} 字段必须是对象")
    remote_url = container.get("url") or container.get("remote_url")
    if not isinstance(remote_url, str) or not remote_url.strip():
        raise MessageNormalizationError(f"{attachment_type}.url 不能为空")
    raw_md5 = container.get("md5") or container.get("filemd5")
    md5 = raw_md5 if isinstance(raw_md5, str) and raw_md5 else None
    return NormalizedAttachment(
        attachment_type=attachment_type,
        remote_url=remote_url,
        download_status="mock" if remote_url.startswith("mock://") else "pending",
        md5=md5,
    )


def _normalize_mixed(
    mixed: Any,
) -> tuple[str, list[NormalizedAttachment]]:
    if not isinstance(mixed, dict):
        raise MessageNormalizationError("mixed 字段必须是对象")
    raw_items = mixed.get("msg_item")
    if raw_items is None:
        raw_items = mixed.get("msg_items", mixed.get("items"))
    if not isinstance(raw_items, list):
        raise MessageNormalizationError("mixed.msg_item 必须是数组")

    text_parts: list[str] = []
    attachments: list[NormalizedAttachment] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            raise MessageNormalizationError(f"mixed.msg_item[{index}] 必须是对象")
        item_type = item.get("msgtype")
        if item_type == "text":
            content = _nested_text(item.get("text"), "content")
            if content:
                text_parts.append(content)
        elif item_type == "image":
            attachments.append(_attachment_from_container(item.get("image"), "image"))
        elif item_type == "file":
            attachments.append(_attachment_from_container(item.get("file"), "file"))
    return "\n".join(text_parts), attachments


def _extract_received_at(payload: dict[str, Any]) -> datetime:
    for key in ("received_at", "create_time", "createTime", "timestamp"):
        if key in payload and payload[key] not in (None, ""):
            return _parse_datetime(payload[key], key)
    return datetime.now(SHANGHAI_TIMEZONE)


def _parse_datetime(value: Any, field_name: str) -> datetime:
    parsed: datetime
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        parsed = datetime.fromtimestamp(timestamp, tz=SHANGHAI_TIMEZONE)
    elif isinstance(value, str):
        stripped = value.strip()
        try:
            if stripped.replace(".", "", 1).isdigit():
                timestamp = float(stripped)
                if timestamp > 10_000_000_000:
                    timestamp /= 1000
                parsed = datetime.fromtimestamp(timestamp, tz=SHANGHAI_TIMEZONE)
            else:
                parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
        except (ValueError, OSError, OverflowError) as exc:
            raise MessageNormalizationError(f"{field_name} 不是有效时间") from exc
    else:
        raise MessageNormalizationError(f"{field_name} 不是有效时间")

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=SHANGHAI_TIMEZONE)
    return parsed
