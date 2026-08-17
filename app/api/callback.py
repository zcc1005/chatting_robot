"""交建通 JSON 与企业微信 XML 回调接口。"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.logging_config import redact_for_logging
from app.schemas import EncryptedCallback
from app.services.crypto_service import (
    AESDecryptError,
    InvalidMessageFormatError,
    JJTCryptoService,
    SignatureVerificationError,
)
from app.services.message_storage import MessageStorage, MessageStorageError
from app.services.message_normalizer import MessageNormalizationError
from app.services.message_service import process_plain_message
from app.services.image_recognition_tasks import (
    schedule_image_recognition_if_enabled,
)
from app.services.image_archive_tasks import schedule_image_archive
from app.services.chat_workflow_service import (
    schedule_chat_workflow_if_enabled,
)
from app.services.xml_service import (
    XMLMessageError,
    normalize_plaintext_xml,
    parse_encrypted_envelope,
)


router = APIRouter(prefix="/jjt-robot", tags=["交建通/企业微信回调"])
logger = logging.getLogger(__name__)

CallbackFormat = Literal["json", "xml"]
MAX_CALLBACK_BODY_BYTES = 10 * 1024 * 1024


def _required_query(**values: str | None) -> dict[str, str]:
    missing = [name for name, value in values.items() if value is None or value == ""]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"缺少或为空的查询参数: {', '.join(missing)}",
        )
    return {name: value for name, value in values.items() if value is not None}


@router.get("/callback", response_class=Response)
def verify_callback_url(
    request: Request,
    msg_signature: str | None = Query(default=None),
    timestamp: str | None = Query(default=None),
    nonce: str | None = Query(default=None),
    echostr: str | None = Query(default=None),
) -> Response:
    query = _required_query(
        msg_signature=msg_signature,
        timestamp=timestamp,
        nonce=nonce,
        echostr=echostr,
    )
    crypto: JJTCryptoService = request.app.state.crypto
    try:
        plaintext = crypto.verify_url(
            query["msg_signature"],
            query["timestamp"],
            query["nonce"],
            query["echostr"],
        )
    except SignatureVerificationError:
        logger.warning("callback URL verification failed: invalid signature")
        raise HTTPException(status_code=403, detail="签名验证失败") from None
    except (AESDecryptError, InvalidMessageFormatError) as exc:
        logger.warning("callback URL verification failed: %s", type(exc).__name__)
        raise HTTPException(status_code=400, detail="echostr 解密失败") from None
    logger.info("callback URL verified")
    return Response(content=plaintext, status_code=200, media_type="text/plain")


@router.post(
    "/callback",
    response_class=Response,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["encrypt"],
                        "properties": {"encrypt": {"type": "string"}},
                    }
                },
                "text/xml": {"schema": {"type": "string", "format": "xml"}},
                "application/xml": {"schema": {"type": "string", "format": "xml"}},
            },
        }
    },
)
async def receive_callback_message(
    request: Request,
    background_tasks: BackgroundTasks,
    msg_signature: str | None = Query(default=None),
    timestamp: str | None = Query(default=None),
    nonce: str | None = Query(default=None),
    session: Session = Depends(get_db),
) -> Response:
    query = _required_query(
        msg_signature=msg_signature,
        timestamp=timestamp,
        nonce=nonce,
    )
    encrypted, callback_format = await _extract_encrypted_body(request)
    crypto: JJTCryptoService = request.app.state.crypto
    storage: MessageStorage = request.app.state.storage
    try:
        plaintext = crypto.decrypt_callback_message(
            query["msg_signature"],
            query["timestamp"],
            query["nonce"],
            encrypted,
        )
    except SignatureVerificationError:
        logger.warning("message callback rejected: invalid signature")
        raise HTTPException(status_code=403, detail="签名验证失败") from None
    except (AESDecryptError, InvalidMessageFormatError) as exc:
        logger.warning("message callback rejected: %s", type(exc).__name__)
        raise HTTPException(status_code=400, detail="消息解密失败") from None

    message = _parse_plaintext(plaintext, callback_format)
    sender = message.get("from")
    sender_userid = sender.get("userid") if isinstance(sender, dict) else None
    fields = {
        "source": message.get("source", "jjt_json"),
        "msgid": message.get("msgid"),
        "aibotid": message.get("aibotid"),
        "chatid": message.get("chatid"),
        "chattype": message.get("chattype"),
        "sender_userid": sender_userid,
        "msgtype": message.get("msgtype"),
    }
    logger.info("decrypted callback fields=%s", fields)
    if callback_format == "json":
        logger.info(
            "decrypted callback JSON=%s",
            json.dumps(
                redact_for_logging(message),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
    if not message.get("msgid"):
        logger.warning("callback message has no msgid")

    if callback_format == "json":
        try:
            result = process_plain_message(message, "jjt", session)
            schedule_image_archive(background_tasks, request, result)
            schedule_image_recognition_if_enabled(
                background_tasks, request, result
            )
            schedule_chat_workflow_if_enabled(
                background_tasks, request, result
            )
        except MessageNormalizationError as exc:
            logger.warning("JSON callback normalization rejected: %s", type(exc).__name__)
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except SQLAlchemyError:
            logger.exception("message callback database persistence failed")
            raise HTTPException(status_code=500, detail="消息数据库保存失败") from None
    try:
        saved = storage.save(message)
    except MessageStorageError:
        logger.exception("message callback persistence failed")
        raise HTTPException(status_code=500, detail="消息保存失败") from None
    if not saved:
        logger.info("duplicate callback msgid=%s", message.get("msgid"))

    if callback_format == "xml":
        return Response(content=b"success", status_code=200, media_type="text/plain")
    return Response(status_code=200)


async def _extract_encrypted_body(request: Request) -> tuple[str, CallbackFormat]:
    body = await request.body()
    if len(body) > MAX_CALLBACK_BODY_BYTES:
        raise HTTPException(status_code=413, detail="回调请求体过大")
    stripped = body.lstrip()
    # 部分网关会把交建通 JSON 外壳转发为 text/xml。优先嗅探请求体，
    # Content-Type 只用于无法从首字符判断时的兼容分流。
    if stripped.startswith(b"{"):
        return _parse_jjt_json_envelope(body), "json"
    if stripped.startswith(b"<"):
        return _parse_wecom_xml_envelope(body), "xml"
    media_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if media_type in {"application/xml", "text/xml"}:
        return _parse_wecom_xml_envelope(body), "xml"
    if media_type not in {"", "application/json"}:
        raise HTTPException(status_code=415, detail="仅支持 JSON 或 XML 回调外壳")
    return _parse_jjt_json_envelope(body), "json"


def _parse_wecom_xml_envelope(body: bytes) -> str:
    try:
        return parse_encrypted_envelope(body).encrypt
    except XMLMessageError as exc:
        logger.warning("XML callback envelope rejected: %s", type(exc).__name__)
        raise HTTPException(status_code=400, detail=str(exc)) from None


def _parse_jjt_json_envelope(body: bytes) -> str:
    try:
        raw_json: Any = json.loads(body.decode("utf-8"))
        parsed = EncryptedCallback.model_validate(raw_json)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, TypeError):
        logger.warning("JSON callback envelope rejected: invalid request body")
        raise HTTPException(status_code=422, detail="请求体必须包含非空 encrypt") from None
    return parsed.encrypt


def _parse_plaintext(
    plaintext: bytes, callback_format: CallbackFormat
) -> dict[str, Any]:
    if callback_format == "xml":
        return _parse_wecom_plaintext_xml(plaintext)
    return _parse_jjt_plaintext_json(plaintext)


def _parse_wecom_plaintext_xml(plaintext: bytes) -> dict[str, Any]:
    try:
        return normalize_plaintext_xml(plaintext)
    except XMLMessageError as exc:
        logger.warning("XML plaintext rejected: %s", type(exc).__name__)
        raise HTTPException(status_code=400, detail="明文不是合法企业微信 XML") from None


def _parse_jjt_plaintext_json(plaintext: bytes) -> dict[str, Any]:
    try:
        decoded = plaintext.decode("utf-8")
        message: Any = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("JSON plaintext rejected: %s", type(exc).__name__)
        raise HTTPException(status_code=400, detail="明文不是合法 UTF-8 JSON") from None
    if not isinstance(message, dict):
        logger.warning("JSON plaintext rejected: root is not an object")
        raise HTTPException(status_code=400, detail="明文 JSON 根节点必须是对象")
    return message
