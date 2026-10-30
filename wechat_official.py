"""WeChat official-account (公众号 / gh_*) helpers.

Official accounts are NOT private/friend sessions. The adapter keeps a short
in-memory ring of recent OA payloads so LLM tools can look them up on demand
without committing FriendMessage events into AstrBot / Companion / Memory.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

# Shared with adapter ignore list.
IGNORE_WXID_EXACT = frozenset({
    "filehelper", "fmessage", "medianote", "floatbottle", "newsapp",
    "weixin", "qmessage", "qqmail", "tmessage", "blogapp", "facebookapp",
    "masssendapp", "feedsapp", "officialaccounts", "notification_messages",
    "brandcustomer", "downloaderapp", "brandsessionholder",
})
IGNORE_WXID_PREFIXES = ("gh_",)


def is_official_or_system_wxid(wxid: str) -> bool:
    value = (wxid or "").strip()
    if not value:
        return True
    lowered = value.lower()
    if lowered in IGNORE_WXID_EXACT:
        return True
    return any(lowered.startswith(prefix) for prefix in IGNORE_WXID_PREFIXES)


def is_official_account_wxid(wxid: str) -> bool:
    """True for 公众号 IDs (gh_*), not every system stub."""
    return (wxid or "").strip().lower().startswith("gh_")


class OfficialAccountInbox:
    """Process-local ring buffer of recent official-account messages."""

    def __init__(self, *, maxlen: int = 200) -> None:
        self._lock = threading.Lock()
        self._items: deque[dict[str, Any]] = deque(maxlen=maxlen)

    def record(
        self,
        *,
        gh_wxid: str,
        text: str,
        msg_type: int,
        message_id: str,
        nickname: str = "",
        push_content: str = "",
        raw: dict[str, Any] | None = None,
    ) -> None:
        item = {
            "gh_wxid": gh_wxid,
            "nickname": nickname or gh_wxid,
            "text": (text or "")[:4000],
            "msg_type": int(msg_type or 0),
            "message_id": message_id or "",
            "push_content": (push_content or "")[:500],
            "received_at": time.time(),
            "raw_keys": sorted((raw or {}).keys())[:40],
        }
        with self._lock:
            self._items.appendleft(item)

    def recent(
        self,
        *,
        gh_wxid: str = "",
        query: str = "",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(50, int(limit or 10)))
        gh = (gh_wxid or "").strip().lower()
        q = (query or "").strip().lower()
        with self._lock:
            items = list(self._items)
        out: list[dict[str, Any]] = []
        for item in items:
            if gh and item["gh_wxid"].lower() != gh:
                continue
            if q:
                blob = f"{item['gh_wxid']} {item['nickname']} {item['text']} {item['push_content']}".lower()
                if q not in blob:
                    continue
            out.append({
                "gh_wxid": item["gh_wxid"],
                "nickname": item["nickname"],
                "text": item["text"],
                "msg_type": item["msg_type"],
                "message_id": item["message_id"],
                "push_content": item["push_content"],
                "received_at": item["received_at"],
            })
            if len(out) >= limit:
                break
        return out

    def list_accounts(self, *, limit: int = 30) -> list[dict[str, Any]]:
        limit = max(1, min(100, int(limit or 30)))
        seen: dict[str, dict[str, Any]] = {}
        with self._lock:
            items = list(self._items)
        for item in items:
            key = item["gh_wxid"]
            if key in seen:
                continue
            seen[key] = {
                "gh_wxid": key,
                "nickname": item["nickname"],
                "last_text": item["text"][:200],
                "last_received_at": item["received_at"],
            }
            if len(seen) >= limit:
                break
        return list(seen.values())


# Module singleton shared by adapter + Star tools.
OFFICIAL_INBOX = OfficialAccountInbox()
