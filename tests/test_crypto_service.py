from __future__ import annotations

import base64
import hashlib
import os
import struct

import pytest
from Crypto.Cipher import AES

from app.config import ConfigurationError, Settings
from app.services.crypto_service import AESDecryptError, JJTCryptoService


TOKEN = "test-callback-token"
AES_KEY_BYTES = bytes(range(32))
ENCODING_AES_KEY = base64.b64encode(AES_KEY_BYTES).decode("ascii").rstrip("=")


def encrypt_for_test(content: bytes, receive_id: bytes = b"") -> str:
    plaintext = os.urandom(16) + struct.pack(">I", len(content)) + content + receive_id
    padding_length = 32 - (len(plaintext) % 32)
    padded = plaintext + bytes([padding_length]) * padding_length
    encrypted = AES.new(
        AES_KEY_BYTES, AES.MODE_CBC, iv=AES_KEY_BYTES[:16]
    ).encrypt(padded)
    return base64.b64encode(encrypted).decode("ascii")


def test_encoding_aes_key_length_error() -> None:
    with pytest.raises(ValueError, match="43"):
        JJTCryptoService(TOKEN, "too-short")


def test_encoding_aes_key_decoded_length_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.base64.b64decode", lambda *_args, **_kwargs: b"x" * 31)
    settings = Settings(callback_token=TOKEN, encoding_aes_key="A" * 43)
    with pytest.raises(ConfigurationError, match="32 字节"):
        settings.validate()


def test_signature_calculation_is_deterministic() -> None:
    service = JJTCryptoService(TOKEN, ENCODING_AES_KEY)
    expected = hashlib.sha1(
        "".join(sorted((TOKEN, "1710000000", "nonce-1", "ciphertext"))).encode()
    ).hexdigest()
    assert service.calculate_signature("1710000000", "nonce-1", "ciphertext") == expected
    assert service.calculate_signature("1710000000", "nonce-1", "ciphertext") == expected


def test_verify_signature_returns_true_for_valid_signature() -> None:
    service = JJTCryptoService(TOKEN, ENCODING_AES_KEY)
    signature = service.calculate_signature("100", "abc", "encrypted")
    assert service.verify_signature(signature, "100", "abc", "encrypted") is True


def test_verify_signature_returns_false_for_invalid_signature() -> None:
    service = JJTCryptoService(TOKEN, ENCODING_AES_KEY)
    assert service.verify_signature("0" * 40, "100", "abc", "encrypted") is False


def test_aes_encrypt_decrypt_round_trip() -> None:
    service = JJTCryptoService(TOKEN, ENCODING_AES_KEY)
    message = '{"content":"施工正常"}'.encode("utf-8")
    payload = service.decrypt(encrypt_for_test(message))
    assert payload.content == message
    assert payload.receive_id == b""


def test_strict_pkcs7_rejects_invalid_padding() -> None:
    with pytest.raises(AESDecryptError, match="填充"):
        JJTCryptoService._strict_unpad(b"content" + bytes([2, 3]))

