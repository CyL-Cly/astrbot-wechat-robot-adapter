"""wechat_robot 平台事件：把 AstrBot 回复写回 wechat-robot-client。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import At, File, Image, Plain
from astrbot.api.platform import AstrBotMessage, MessageType, PlatformMetadata

from .wechat_official import is_official_or_system_wxid

if TYPE_CHECKING:
    from .wechat_robot_client import WechatRobotHTTPClient


class WechatRobotEvent(AstrMessageEvent):
    def __init__(
        self,
        message_str: str,
        message_obj: AstrBotMessage,
        platform_meta: PlatformMetadata,
        session_id: str,
        client: WechatRobotHTTPClient,
        *,
        prefer_upload_image: bool = True,
    ) -> None:
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self.client = client
        # Companion / multi_session 用 event.bot is platform.get_client() 校验同源客户端
        self.bot = client
        self.prefer_upload_image = prefer_upload_image

    def _reply_to_wxid(self) -> str:
        if self.get_message_type() == MessageType.GROUP_MESSAGE:
            return self.message_obj.group_id or self.session_id
        return self.session_id

    async def send(self, message: MessageChain) -> None:
        to_wxid = self._reply_to_wxid()
        if is_official_or_system_wxid(to_wxid):
            logger.warning(
                "wechat_robot Event.send 拒绝向公众号/系统号回复: %s",
                to_wxid,
            )
            return
        at_list: list[str] = []
        plain_parts: list[str] = []

        for comp in message.chain:
            if isinstance(comp, At):
                qq = str(getattr(comp, "qq", "") or "")
                if qq and qq != "all":
                    at_list.append(qq)
            elif isinstance(comp, Plain):
                if comp.text:
                    plain_parts.append(comp.text)
            elif isinstance(comp, Image):
                await self._send_image(to_wxid, comp)
            elif isinstance(comp, File):
                await self._send_file(to_wxid, comp)
            else:
                logger.debug(f"wechat_robot 暂未支持的组件: {type(comp).__name__}")

        text = "".join(plain_parts).strip()
        if text or at_list:
            await self.client.send_text(to_wxid, text or " ", at=at_list or None)

        await super().send(message)

    async def _send_image(self, to_wxid: str, image: Image) -> None:
        url = getattr(image, "url", None) or ""
        file_field = getattr(image, "file", None) or ""
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            await self.client.send_image_urls(to_wxid, [url])
            return
        if isinstance(file_field, str) and file_field.startswith(("http://", "https://")):
            await self.client.send_image_urls(to_wxid, [file_field])
            return

        try:
            path = await image.convert_to_file_path()
        except Exception as e:
            logger.error(f"解析图片失败: {e}")
            return
        if not path:
            logger.warning("图片组件无可发送路径")
            return

        if self.prefer_upload_image:
            try:
                await self.client.send_image_upload(to_wxid, path)
                return
            except Exception as e:
                logger.warning(f"multipart 上传图片失败，尝试 local: {e}")

        try:
            await self.client.send_image_local(to_wxid, path)
        except Exception as e:
            logger.error(f"发送图片失败: {e}")

    async def _send_file(self, to_wxid: str, file_comp: File) -> None:
        path = None
        try:
            path = await file_comp.get_file(allow_return_url=False)
        except Exception:
            path = getattr(file_comp, "file", None) or ""
        if not path or str(path).startswith(("http://", "https://")):
            logger.warning("文件组件无可发送本地路径（file/local 要求 robot-client 可读）")
            return
        try:
            await self.client.send_file_local(to_wxid, str(path))
        except Exception as e:
            logger.error(f"发送文件失败: {e}")
