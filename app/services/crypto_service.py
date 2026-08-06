"""WXBizMsgCrypt 兼容的签名与 AES-256-CBC 解密服务。"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import struct
from dataclasses import dataclass

from Crypto.Cipher import AES


class CryptoError(Exception):
    """加解密服务基础异常。"""


class SignatureVerificationError(CryptoError):
    """消息签名校验失败。"""


class AESDecryptError(CryptoError):
    """AES 密文无法解密或填充不合法。"""


class InvalidMessageFormatError(CryptoError):
    """解密后的协议字节结构不合法。"""


@dataclass(frozen=True, slots=True)
class DecryptedPayload:
    content: bytes
    receive_id: bytes


class JJTCryptoService:
    """独立协议适配层，后续可由官方库实现替换。"""

    block_size = 32

    def __init__(self, token: str, encoding_aes_key: str, receive_id: str = ""):
        if not token:
            raise ValueError("callback token 不能为空")
        if len(encoding_aes_key) != 43:
            raise ValueError("EncodingAESKey 必须是 43 个字符")
        try:
            aes_key = base64.b64decode(encoding_aes_key + "=", validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("EncodingAESKey 不是有效的 Base64 编码") from exc
        if len(aes_key) != 32:
            raise ValueError("EncodingAESKey 解码后必须是 32 字节")

        self._token = token
        self._aes_key = aes_key
        self._receive_id = receive_id.encode("utf-8")

    def calculate_signature(self, timestamp: str, nonce: str, encrypt: str) -> str:
        values = sorted((self._token, str(timestamp), str(nonce), encrypt))
        return hashlib.sha1("".join(values).encode("utf-8")).hexdigest()

    def verify_signature(
        self, msg_signature: str, timestamp: str, nonce: str, encrypt: str
    ) -> bool:
        expected = self.calculate_signature(timestamp, nonce, encrypt)
        return hmac.compare_digest(expected, msg_signature)

    def decrypt(self, encrypted: str) -> DecryptedPayload:
        try:
            ciphertext = base64.b64decode(encrypted, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise AESDecryptError("密文不是有效的 Base64") from exc
        if not ciphertext or len(ciphertext) % AES.block_size != 0:
            raise AESDecryptError("AES 密文长度不合法")

        try:
            padded = AES.new(
                self._aes_key, AES.MODE_CBC, iv=self._aes_key[:16]
            ).decrypt(ciphertext)
        except ValueError as exc:
            raise AESDecryptError("AES 解密失败") from exc

        plaintext = self._strict_unpad(padded)
        if len(plaintext) < 20:
            raise InvalidMessageFormatError("解密结果不足 20 字节")

        message_length = struct.unpack(">I", plaintext[16:20])[0]
        message_end = 20 + message_length
        if message_end > len(plaintext):
            raise InvalidMessageFormatError("消息长度越界")

        payload = DecryptedPayload(
            content=plaintext[20:message_end],
            receive_id=plaintext[message_end:],
        )
        # 智能机器人 receiveid 按协议可为空；仅在配置了非空值时校验。
        if self._receive_id and not hmac.compare_digest(
            payload.receive_id, self._receive_id
        ):
            raise InvalidMessageFormatError("receiveid 不匹配")
        return payload

    def verify_url(
        self, msg_signature: str, timestamp: str, nonce: str, echostr: str
    ) -> bytes:
        if not self.verify_signature(msg_signature, timestamp, nonce, echostr):
            raise SignatureVerificationError("URL 验证签名不匹配")
        return self.decrypt(echostr).content

    def decrypt_callback_message(
        self, msg_signature: str, timestamp: str, nonce: str, encrypt: str
    ) -> bytes:
        if not self.verify_signature(msg_signature, timestamp, nonce, encrypt):
            raise SignatureVerificationError("消息回调签名不匹配")
        return self.decrypt(encrypt).content

    @classmethod
    def _strict_unpad(cls, data: bytes) -> bytes:
        if not data:
            raise AESDecryptError("解密结果为空")
        padding_length = data[-1]
        if padding_length < 1 or padding_length > cls.block_size:
            raise AESDecryptError("PKCS#7 填充长度不合法")
        expected_padding = bytes([padding_length]) * padding_length
        if len(data) < padding_length or not hmac.compare_digest(
            data[-padding_length:], expected_padding
        ):
            raise AESDecryptError("PKCS#7 填充字节不一致")
        return data[:-padding_length]

