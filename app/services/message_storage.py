"""按日期保存 JSONL 消息，并提供进程内有限去重。"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


class MessageStorageError(Exception):
    """消息持久化失败。"""


class MessageStorage:
    def __init__(
        self, data_dir: Path, timezone: str, max_recent_msgids: int = 10_000
    ) -> None:
        if max_recent_msgids < 1:
            raise ValueError("max_recent_msgids 必须大于 0")
        self._data_dir = data_dir
        self._timezone = ZoneInfo(timezone)
        self._max_recent_msgids = max_recent_msgids
        self._recent_msgids: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()

    def save(self, message: dict[str, Any]) -> bool:
        """保存消息；若 msgid 在本进程内已出现则返回 False。"""
        raw_msgid = message.get("msgid")
        msgid = str(raw_msgid) if raw_msgid not in (None, "") else None
        now = datetime.now(self._timezone)
        sender = message.get("from")
        sender_userid = sender.get("userid") if isinstance(sender, dict) else None
        record = {
            "received_at": now.isoformat(),
            "msgid": raw_msgid,
            "chatid": message.get("chatid"),
            "sender_userid": sender_userid,
            "msgtype": message.get("msgtype"),
            "message": message,
        }

        with self._lock:
            if msgid is not None and msgid in self._recent_msgids:
                self._recent_msgids.move_to_end(msgid)
                return False
            try:
                self._data_dir.mkdir(parents=True, exist_ok=True)
                target = self._data_dir / f"{now.date().isoformat()}.jsonl"
                with target.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    stream.flush()
            except OSError as exc:
                raise MessageStorageError("无法写入消息 JSONL 文件") from exc

            if msgid is not None:
                self._recent_msgids[msgid] = None
                if len(self._recent_msgids) > self._max_recent_msgids:
                    self._recent_msgids.popitem(last=False)
        return True

