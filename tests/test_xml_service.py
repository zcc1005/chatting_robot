from __future__ import annotations

import pytest

from app.services.xml_service import (
    XMLMessageError,
    normalize_plaintext_xml,
    parse_encrypted_envelope,
)


def test_parse_encrypted_xml_envelope() -> None:
    envelope = b"""<xml>
        <ToUserName><![CDATA[ww-corp]]></ToUserName>
        <Encrypt><![CDATA[base64-cipher]]></Encrypt>
        <AgentID>1000002</AgentID>
    </xml>"""
    assert parse_encrypted_envelope(envelope).encrypt == "base64-cipher"


def test_xml_envelope_requires_encrypt() -> None:
    with pytest.raises(XMLMessageError, match="Encrypt"):
        parse_encrypted_envelope(b"<xml><AgentID>1000002</AgentID></xml>")


def test_xml_parser_rejects_dtd_and_entities() -> None:
    dangerous = b"""<!DOCTYPE xml [<!ENTITY xxe SYSTEM "file:///secret">]>
    <xml><Encrypt>&xxe;</Encrypt></xml>"""
    with pytest.raises(XMLMessageError, match="危险"):
        parse_encrypted_envelope(dangerous)


def test_normalize_plaintext_xml_preserves_nested_and_repeated_fields() -> None:
    plaintext = """<xml>
        <ToUserName><![CDATA[ww-corp]]></ToUserName>
        <FromUserName><![CDATA[user-001]]></FromUserName>
        <CreateTime>1710000000</CreateTime>
        <MsgType><![CDATA[image]]></MsgType>
        <MsgId>90001</MsgId>
        <AgentID>1000002</AgentID>
        <SendPicsInfo><PicList>
            <item><PicMd5Sum>md5-one</PicMd5Sum></item>
            <item><PicMd5Sum>md5-two</PicMd5Sum></item>
        </PicList></SendPicsInfo>
    </xml>""".encode("utf-8")
    normalized = normalize_plaintext_xml(plaintext)
    assert normalized["source"] == "wecom_xml"
    assert normalized["msgid"] == "90001"
    assert normalized["from"] == {"userid": "user-001"}
    assert normalized["msgtype"] == "image"
    items = normalized["xml"]["SendPicsInfo"]["PicList"]["item"]
    assert items == [
        {"PicMd5Sum": "md5-one"},
        {"PicMd5Sum": "md5-two"},
    ]

