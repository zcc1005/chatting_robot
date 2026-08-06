"""企业微信自建应用 XML 回调的安全解析与标准化。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from xml.etree.ElementTree import Element, ParseError

from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException


class XMLMessageError(ValueError):
    """XML 不是合法的企业微信回调格式。"""


@dataclass(frozen=True, slots=True)
class EncryptedXMLEnvelope:
    encrypt: str


def parse_encrypted_envelope(xml_bytes: bytes) -> EncryptedXMLEnvelope:
    """解析企业微信的外层加密 XML，仅提取 Encrypt。"""
    root = _parse_root(xml_bytes)
    _require_xml_root(root)
    encrypt: str | None = None
    for child in root:
        if _local_name(child.tag) == "Encrypt":
            encrypt = (child.text or "").strip()
            break
    if not encrypt:
        raise XMLMessageError("XML 回调缺少 Encrypt 字段")
    return EncryptedXMLEnvelope(encrypt=encrypt)


def normalize_plaintext_xml(xml_bytes: bytes) -> dict[str, Any]:
    """把企业微信明文 XML 转成便于 JSONL 存储和后续处理的消息对象。"""
    try:
        raw_xml = xml_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise XMLMessageError("XML 明文不是合法 UTF-8") from exc

    root = _parse_root(xml_bytes)
    _require_xml_root(root)
    parsed = _children_to_dict(root)

    msgid = _text_value(parsed.get("MsgId"))
    sender = _text_value(parsed.get("FromUserName"))
    msgtype = _text_value(parsed.get("MsgType"))
    agent_id = _text_value(parsed.get("AgentID"))
    chat_id = _text_value(parsed.get("ChatId"))
    content = _text_value(parsed.get("Content"))

    message: dict[str, Any] = {
        "source": "wecom_xml",
        "msgid": msgid,
        "aibotid": agent_id,
        "chatid": chat_id,
        "chattype": None,
        "from": {"userid": sender},
        "msgtype": msgtype,
        "xml": parsed,
        "raw_xml": raw_xml,
    }
    if content is not None:
        message["text"] = {"content": content}
    return message


def _parse_root(xml_bytes: bytes) -> Element:
    if not xml_bytes or not xml_bytes.strip():
        raise XMLMessageError("XML 请求体为空")
    try:
        return DefusedElementTree.fromstring(xml_bytes)
    except (ParseError, DefusedXmlException, ValueError) as exc:
        raise XMLMessageError("XML 格式不合法或包含危险声明") from exc


def _require_xml_root(root: Element) -> None:
    if _local_name(root.tag).lower() != "xml":
        raise XMLMessageError("XML 根节点必须是 xml")


def _children_to_dict(element: Element) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for child in element:
        key = _local_name(child.tag)
        value = _element_value(child)
        if key in result:
            existing = result[key]
            if isinstance(existing, list):
                existing.append(value)
            else:
                result[key] = [existing, value]
        else:
            result[key] = value
    return result


def _element_value(element: Element) -> Any:
    if len(element):
        value: Any = _children_to_dict(element)
    else:
        value = element.text or ""
    if element.attrib:
        if isinstance(value, dict):
            return {"@attributes": dict(element.attrib), **value}
        return {"@attributes": dict(element.attrib), "#text": value}
    return value


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value) if isinstance(value, (int, float, bool)) else None

