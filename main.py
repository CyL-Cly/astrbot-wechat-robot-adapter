"""AstrBot 插件入口：导入适配器，并提供公众号查询工具（不进私聊流水线）。"""

from __future__ import annotations

import json
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star


class Main(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        from .wechat_robot_adapter import WechatRobotAdapter  # noqa: F401
        logger.info("wechat_robot: official-account tools registered (not FriendMessage)")

    @filter.llm_tool(name="wechat_official_list")
    async def wechat_official_list(
        self,
        event: AstrMessageEvent,
        limit: int = 20,
    ) -> str:
        """列出近期收到过推送的微信公众号（gh_*）。公众号不是私聊会话，只能通过本工具查询。

        Args:
            limit(number): 最多返回几个公众号，默认 20，最多 100。
        """
        from .wechat_official import OFFICIAL_INBOX

        rows = OFFICIAL_INBOX.list_accounts(limit=limit)
        return json.dumps(
            {
                "ok": True,
                "note": "公众号不进入 FriendMessage/Companion 私聊；仅工具可读近期缓冲。",
                "accounts": rows,
                "count": len(rows),
            },
            ensure_ascii=False,
        )

    @filter.llm_tool(name="wechat_official_lookup")
    async def wechat_official_lookup(
        self,
        event: AstrMessageEvent,
        gh_wxid: str = "",
        query: str = "",
        limit: int = 10,
    ) -> str:
        """按公众号 wxid（gh_ 开头）或关键词查询近期公众号推送正文。不会把公众号当成用户私聊。

        Args:
            gh_wxid(string): 可选，精确公众号 id，如 gh_abc123。
            query(string): 可选，在昵称/正文/推送摘要中模糊匹配。
            limit(number): 最多返回几条，默认 10，最多 50。
        """
        from .wechat_official import OFFICIAL_INBOX, is_official_account_wxid

        gh = (gh_wxid or "").strip()
        if gh and not is_official_account_wxid(gh):
            return json.dumps(
                {
                    "ok": False,
                    "error": "gh_wxid must start with gh_",
                    "hint": "普通好友私聊请直接对话；公众号只能用本工具。",
                },
                ensure_ascii=False,
            )
        rows = OFFICIAL_INBOX.recent(gh_wxid=gh, query=query or "", limit=limit)
        return json.dumps(
            {
                "ok": True,
                "note": "结果来自适配器内存缓冲（进程内近期 OA 推送），非微信历史库全量。",
                "messages": rows,
                "count": len(rows),
            },
            ensure_ascii=False,
        )
