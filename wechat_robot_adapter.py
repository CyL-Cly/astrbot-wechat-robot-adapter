"""wechat_robot 平台适配器。

收：wechat-robot-client「系统设置」webhook（POST 原始 SyncMessage JSON）
发：客户端 /api/v1/robot/message/send/*

对照：
- message_callback.md（协议层字段形态）
- service/message.go MessageWebhook（实际推送 SyncMessage）
- router/router.go 发送路由
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections import OrderedDict
from typing import Any

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import At, File, Image, Plain, Record
from astrbot.api.platform import (
    AstrBotMessage,
    MessageMember,
    MessageType,
    Platform,
    PlatformMetadata,
    register_platform_adapter,
)
from astrbot.core.platform.astr_message_event import MessageSesion
from astrbot.core.platform.webhook_server import FastAPIWebhookServer

from .wechat_robot_client import WechatRobotHTTPClient
from .wechat_robot_event import WechatRobotEvent

MSG_TEXT = 1
MSG_IMAGE = 3
MSG_VOICE = 34
MSG_SYSTEM = 10000
MSG_REVOKE = 10002

_CHATROOM_RE = re.compile(r"@chatroom$", re.I)

from .wechat_official import (
    OFFICIAL_INBOX,
    is_official_account_wxid,
    is_official_or_system_wxid,
)


def _should_ignore_peer(wxid: str) -> bool:
    return is_official_or_system_wxid(wxid)


@register_platform_adapter(
    "wechat_robot",
    "wechat-robot-client（iPad 微信）适配器",
    default_config_tmpl={
        "id": "wechat_robot",
        "type": "wechat_robot",
        "enable": False,
        "wechat_client_base_url": "http://127.0.0.1:9001",
        "webhook_host": "0.0.0.0",
        "webhook_port": 6196,
        "webhook_path": "/wechat-robot/callback",
        "webhook_token": "",
        "webhook_token_header": "X-Webhook-Token",
        "robot_wxid": "",
        "ignore_self": True,
        "ignore_system": True,
        "ignore_official": True,
        "prefer_upload_image": True,
        "request_headers": {},
    },
    adapter_display_name="wechat-robot",
    support_streaming_message=False,
)
class WechatRobotAdapter(Platform):
    def __init__(
        self,
        platform_config: dict,
        platform_settings: dict,
        event_queue: asyncio.Queue,
    ) -> None:
        super().__init__(platform_config, event_queue)
        self.config = platform_config
        self.settings = platform_settings
        # 在 __init__ 就建好 HTTP 客户端，保证 Companion bridge 注册时
        # platform.get_client() 已是稳定对象（claims 用 event.bot is client）。
        headers = dict(self.config.get("request_headers") or {})
        self._client: WechatRobotHTTPClient | None = WechatRobotHTTPClient(
            self.config.get("wechat_client_base_url", "http://127.0.0.1:9001"),
            extra_headers=headers,
        )
        self._server: FastAPIWebhookServer | None = None
        self._shutdown_event = asyncio.Event()
        self._seen_ids: OrderedDict[str, None] = OrderedDict()
        self._seen_max = 2000

    def meta(self) -> PlatformMetadata:
        return PlatformMetadata(
            name="wechat_robot",
            description="wechat-robot-client（iPad 微信）适配器",
            id=self.config.get("id", "wechat_robot"),
            adapter_display_name="wechat-robot",
            support_streaming_message=False,
        )

    async def send_by_session(
        self,
        session: MessageSesion,
        message_chain: MessageChain,
    ) -> None:
        if self._client is None:
            logger.error("wechat_robot send_by_session: client unavailable (terminated?)")
            raise RuntimeError("wechat_robot client unavailable")
        to_wxid = session.session_id
        if is_official_or_system_wxid(to_wxid):
            logger.warning(
                f"拒绝向公众号/系统号主动发送: {to_wxid} "
                "(use wechat_official_* tools instead of FriendMessage)"
            )
            raise ValueError(f"refuse send to official/system wxid: {to_wxid}")

        prefer_upload = bool(self.config.get("prefer_upload_image", True))
        texts: list[str] = []
        sent_media = False
        for comp in message_chain.chain:
            if isinstance(comp, Plain) and comp.text:
                texts.append(comp.text)
            elif isinstance(comp, Image):
                await self._send_image_comp(to_wxid, comp, prefer_upload=prefer_upload)
                sent_media = True
            elif isinstance(comp, Record):
                path = await self._record_local_path(comp)
                if path:
                    await self._client.send_file_local(to_wxid, path)
                    sent_media = True
                else:
                    logger.warning("wechat_robot send_by_session: Record 无本地路径，已跳过")
            elif isinstance(comp, File):
                path = await self._file_local_path(comp)
                if path:
                    await self._client.send_file_local(to_wxid, path)
                    sent_media = True
                else:
                    logger.warning("wechat_robot send_by_session: File 无本地路径，已跳过")
            else:
                logger.debug(
                    "wechat_robot send_by_session 暂未支持的组件: %s",
                    type(comp).__name__,
                )

        text = "".join(texts).strip()
        if text:
            await self._client.send_text(to_wxid, text)
        if not text and not sent_media:
            # Do not call super success path — Companion would mark delivered.
            raise RuntimeError(
                "wechat_robot send_by_session: empty chain (no Plain/Image/Record/File)"
            )
        await super().send_by_session(session, message_chain)

    async def _send_image_comp(
        self,
        to_wxid: str,
        image: Image,
        *,
        prefer_upload: bool,
    ) -> None:
        url = getattr(image, "url", None) or ""
        file_field = getattr(image, "file", None) or ""
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            await self._client.send_image_urls(to_wxid, [url])
            return
        if isinstance(file_field, str) and file_field.startswith(("http://", "https://")):
            await self._client.send_image_urls(to_wxid, [file_field])
            return
        try:
            path = await image.convert_to_file_path()
        except Exception as e:
            logger.error(f"send_by_session 解析图片失败: {e}")
            raise
        if not path:
            raise RuntimeError("send_by_session: 图片组件无可发送路径")
        if prefer_upload:
            try:
                await self._client.send_image_upload(to_wxid, path)
                return
            except Exception as e:
                logger.warning(f"multipart 上传图片失败，尝试 local: {e}")
        await self._client.send_image_local(to_wxid, path)

    @staticmethod
    async def _record_local_path(record: Record) -> str:
        file_field = getattr(record, "file", None) or ""
        if isinstance(file_field, str) and file_field and not file_field.startswith(("http://", "https://", "base64://")):
            return file_field
        getter = getattr(record, "convert_to_file_path", None) or getattr(record, "get_file", None)
        if callable(getter):
            try:
                path = await getter() if asyncio.iscoroutinefunction(getter) else getter()
                if asyncio.iscoroutine(path):
                    path = await path
                if path and not str(path).startswith(("http://", "https://")):
                    return str(path)
            except Exception as e:
                logger.warning(f"解析 Record 路径失败: {e}")
        return ""

    @staticmethod
    async def _file_local_path(file_comp: File) -> str:
        path = None
        try:
            path = await file_comp.get_file(allow_return_url=False)
        except Exception:
            path = getattr(file_comp, "file", None) or ""
        if not path or str(path).startswith(("http://", "https://")):
            return ""
        return str(path)

    async def run(self) -> None:
        if self._client is None:
            headers = dict(self.config.get("request_headers") or {})
            self._client = WechatRobotHTTPClient(
                self.config.get("wechat_client_base_url", "http://127.0.0.1:9001"),
                extra_headers=headers,
            )

        host = self.config.get("webhook_host", "0.0.0.0")
        port = int(self.config.get("webhook_port", 6196))
        path = self.config.get("webhook_path", "/wechat-robot/callback")
        if not path.startswith("/"):
            path = "/" + path

        self._server = FastAPIWebhookServer("wechat-robot-webhook")
        self._server.add_url_rule(path, view_func=self._on_webhook, methods=["POST"])
        # 健康检查，方便排查连通性
        self._server.add_url_rule(
            path.rstrip("/") + "/health",
            view_func=self._on_health,
            methods=["GET"],
        )

        logger.info(
            f"wechat_robot webhook 监听 http://{host}:{port}{path} ；"
            f"请在 wechat-robot 管理后台 → 系统设置 → webhook_url 填入该地址"
            + (
                f"，并配置 Header {self.config.get('webhook_token_header')}"
                if self.config.get("webhook_token")
                else ""
            )
        )
        await self._server.run_task(
            host=host,
            port=port,
            shutdown_trigger=self._shutdown_trigger,
        )

    async def _shutdown_trigger(self) -> None:
        await self._shutdown_event.wait()

    async def terminate(self) -> None:
        self._shutdown_event.set()
        if self._server:
            await self._server.shutdown()
            self._server = None
        if self._client:
            self._client.mark_terminated()
            await self._client.close()
            self._client = None

    def get_client(self) -> WechatRobotHTTPClient | None:
        return self._client

    async def _on_health(self, _request: Any = None) -> dict:
        return {"ok": True, "adapter": "wechat_robot"}

    async def _on_webhook(self, request: Any) -> dict:
        token = self.config.get("webhook_token") or ""
        header_name = (self.config.get("webhook_token_header") or "X-Webhook-Token").lower()
        if token:
            headers = {str(k).lower(): v for k, v in dict(request.headers).items()}
            if headers.get(header_name) != token:
                logger.warning("wechat_robot webhook token 校验失败")
                return {"ok": False, "error": "invalid token"}

        payload = await request.get_json(silent=True)
        if payload is None:
            raw = await request.get_data()
            logger.warning(f"webhook body 非 JSON，长度={len(raw) if raw else 0}")
            return {"ok": False, "error": "invalid json"}

        if not isinstance(payload, dict):
            payload = {"AddMsgs": payload} if isinstance(payload, list) else {}

        query = {k: request.args.get(k) for k in ("robot_id", "robot_code", "robot_wxid")}
        query = {k: v for k, v in query.items() if v is not None}
        await self._ingest_payload(payload, query)
        return {"ok": True}

    async def _ingest_payload(self, payload: dict[str, Any], query: dict[str, str]) -> None:
        robot_wxid = (
            query.get("robot_wxid")
            or self.config.get("robot_wxid")
            or ""
        )
        add_msgs = payload.get("AddMsgs") or payload.get("add_msgs") or []
        if not isinstance(add_msgs, list):
            return
        for raw in add_msgs:
            if not isinstance(raw, dict):
                continue
            try:
                abm = self._convert_message(raw, robot_wxid)
                if abm is None:
                    continue
                await self.handle_msg(abm)
            except Exception as e:
                logger.error(f"转换/处理消息失败: {e}", exc_info=True)

    def _dedup(self, key: str) -> bool:
        if not key:
            return False
        if key in self._seen_ids:
            return True
        self._seen_ids[key] = None
        while len(self._seen_ids) > self._seen_max:
            self._seen_ids.popitem(last=False)
        return False

    def _str_field(self, node: Any) -> str:
        """兼容 {"string": "..."} / {"str": "..."} / 纯字符串。"""
        if node is None:
            return ""
        if isinstance(node, str):
            return node
        if isinstance(node, dict):
            if "string" in node and node["string"] is not None:
                return str(node["string"])
            if "str" in node and node["str"] is not None:
                return str(node["str"])
        return str(node)

    def _convert_message(
        self,
        raw: dict[str, Any],
        robot_wxid: str,
    ) -> AstrBotMessage | None:
        msg_type = int(raw.get("MsgType") or raw.get("msg_type") or 0)
        if self.config.get("ignore_system", True) and msg_type in (MSG_SYSTEM, MSG_REVOKE):
            return None

        from_user = self._str_field(raw.get("FromUserName") or raw.get("from_user_name"))
        to_user = self._str_field(raw.get("ToUserName") or raw.get("to_user_name"))
        content_raw = self._str_field(raw.get("Content") or raw.get("content"))
        push_content = str(raw.get("PushContent") or raw.get("push_content") or "")
        msg_source = str(raw.get("MsgSource") or raw.get("msg_source") or "")
        create_time = str(
            raw.get("CreateTime")
            or raw.get("create_time")
            or raw.get("CreateTimeStamp")
            or ""
        )

        new_msg_id = str(
            raw.get("NewMsgId")
            or raw.get("new_msg_id")
            or raw.get("MsgId")
            or ""
        )
        if not new_msg_id:
            digest = hashlib.sha1(
                f"{from_user}|{to_user}|{create_time}|{content_raw}".encode("utf-8", "replace")
            ).hexdigest()[:16]
            new_msg_id = f"syn:{from_user}:{to_user}:{create_time}:{digest}"
        if self._dedup(new_msg_id):
            return None

        if not from_user:
            return None

        # 公众号/系统号：不进 FriendMessage 流水线；gh_* 写入工具可查的缓冲
        is_group_guess = bool(_CHATROOM_RE.search(from_user)) or (
            bool(_CHATROOM_RE.search(to_user)) and robot_wxid and from_user == robot_wxid
        )
        if not is_group_guess:
            peer_guess = from_user if (not robot_wxid or from_user != robot_wxid) else to_user
            if self.config.get("ignore_official", True) and (
                _should_ignore_peer(peer_guess) or _should_ignore_peer(from_user)
            ):
                oa_id = peer_guess if is_official_account_wxid(peer_guess) else (
                    from_user if is_official_account_wxid(from_user) else ""
                )
                if oa_id:
                    nick = peer_guess
                    if push_content:
                        for sep in (" : ", ":", "在群聊中"):
                            if sep in push_content:
                                nick = push_content.split(sep, 1)[0].strip() or nick
                                break
                    OFFICIAL_INBOX.record(
                        gh_wxid=oa_id,
                        text=content_raw,
                        msg_type=msg_type,
                        message_id=new_msg_id,
                        nickname=nick,
                        push_content=push_content,
                        raw=raw,
                    )
                    logger.info(
                        "wechat_robot: buffered official-account push gh=%s (not FriendMessage; use wechat_official_* tools)",
                        oa_id,
                    )
                else:
                    logger.debug("wechat_robot: ignored system/non-OA peer %s", peer_guess or from_user)
                return None

        is_group = bool(_CHATROOM_RE.search(from_user)) or (
            bool(_CHATROOM_RE.search(to_user)) and robot_wxid and from_user == robot_wxid
        )

        sender_wxid = from_user
        text = content_raw
        group_id = ""
        if is_group:
            if ":\n" in content_raw:
                sender_wxid, text = content_raw.split(":\n", 1)
            elif ":" in content_raw:
                maybe_id, rest = content_raw.split(":", 1)
                if maybe_id.startswith("wxid_") or "@" in maybe_id:
                    sender_wxid, text = maybe_id, rest.lstrip("\n")
            group_id = from_user if _CHATROOM_RE.search(from_user) else to_user

        if self.config.get("ignore_self", True) and robot_wxid:
            if sender_wxid == robot_wxid or from_user == robot_wxid:
                return None

        nickname = sender_wxid
        if push_content:
            for sep in (" : ", ":", "在群聊中"):
                if sep in push_content:
                    nickname = push_content.split(sep, 1)[0].strip() or nickname
                    break

        abm = AstrBotMessage()
        abm.message = []
        abm.message_id = new_msg_id or str(raw.get("MsgId") or "")
        abm.raw_message = raw
        abm.self_id = robot_wxid or to_user
        abm.sender = MessageMember(user_id=sender_wxid, nickname=nickname)

        if is_group:
            abm.type = MessageType.GROUP_MESSAGE
            abm.group_id = group_id
            abm.session_id = group_id
            for uid in self._parse_atuserlist(msg_source):
                abm.message.append(At(qq=uid))
        else:
            abm.type = MessageType.FRIEND_MESSAGE
            peer = from_user if (not robot_wxid or from_user != robot_wxid) else to_user
            abm.session_id = peer
            abm.group_id = ""

        if msg_type == MSG_TEXT:
            abm.message.append(Plain(text=text or ""))
            abm.message_str = text or ""
        elif msg_type == MSG_IMAGE:
            # ImgBuf.buffer 常为缩略图 base64；完整图需另调下载接口，骨架先占位
            img_buf = raw.get("ImgBuf") or raw.get("img_buf") or {}
            b64 = ""
            if isinstance(img_buf, dict):
                b64 = str(img_buf.get("buffer") or "")
            if b64:
                abm.message.append(Image(file=f"base64://{b64}"))
            abm.message.append(Plain(text="[图片]"))
            abm.message_str = "[图片]"
        else:
            abm.message.append(Plain(text=text or f"[MsgType={msg_type}]"))
            abm.message_str = text or f"[MsgType={msg_type}]"

        return abm

    def _parse_atuserlist(self, msg_source: str) -> list[str]:
        if not msg_source:
            return []
        m = re.search(
            r"<atuserlist>\s*(?:<!\[CDATA\[)?\s*([^<\]]+?)\s*(?:\]\]>)?\s*</atuserlist>",
            msg_source,
            re.I,
        )
        if not m:
            return []
        raw = m.group(1).strip()
        if not raw:
            return []
        return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]

    async def handle_msg(self, message: AstrBotMessage) -> None:
        if self._client is None:
            logger.error("wechat_robot handle_msg: client unavailable (terminated?)")
            return
        event = WechatRobotEvent(
            message_str=message.message_str,
            message_obj=message,
            platform_meta=self.meta(),
            session_id=message.session_id,
            client=self._client,
            prefer_upload_image=bool(self.config.get("prefer_upload_image", True)),
        )
        self.commit_event(event)
