from __future__ import annotations

import base64
import json
from pathlib import Path

from app.services.crypto_service import JJTCryptoService
from scripts.verify_wecom_callback import (
    _jsonl_contains_msgid,
    _validate_base_url,
    encrypt_for_verification,
)


AES_KEY_BYTES = bytes(range(32))
ENCODING_AES_KEY = base64.b64encode(AES_KEY_BYTES).decode("ascii").rstrip("=")


def test_verification_encrypt_helper_is_protocol_compatible() -> None:
    service = JJTCryptoService("token", ENCODING_AES_KEY, "ww-corp")
    encrypted = encrypt_for_verification(
        b"verification-message", ENCODING_AES_KEY, "ww-corp"
    )
    payload = service.decrypt(encrypted)
    assert payload.content == b"verification-message"
    assert payload.receive_id == b"ww-corp"


def test_validate_base_url_normalizes_trailing_slash() -> None:
    assert _validate_base_url("https://example.com/") == "https://example.com"


def test_jsonl_contains_msgid(tmp_path: Path) -> None:
    target = tmp_path / "messages.jsonl"
    target.write_text(
        json.dumps({"msgid": "first"})
        + "\n"
        + "not-json\n"
        + json.dumps({"msgid": "wanted"})
        + "\n",
        encoding="utf-8",
    )
    assert _jsonl_contains_msgid(target, "wanted") is True
    assert _jsonl_contains_msgid(target, "missing") is False

