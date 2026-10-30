"""HTTP 客户端：调用 wechat-robot-client 对外发送接口。

接口对照（源码 router/router.go + dto/message.go）：
- POST /api/v1/robot/message/send/text        {"to_wxid","content","at":[]}
- POST /api/v1/robot/message/send/image/url   {"to_wxid","image_urls":[]}
- POST /api/v1/robot/message/send/image/local {"to_wxid","file_path"}
- POST /api/v1/robot/message/send/image       multipart: to_wxid + image
- POST /api/v1/robot/message/send/file/local  {"to_wxid","file_path"}
- GET  /api/v1/robot/is-loggedin
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

import aiohttp

from astrbot.api import logger


class WechatRobotHTTPClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.extra_headers = dict(extra_headers or {})
        self._session: aiohttp.ClientSession | None = None
        self._terminated = False

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._terminated:
            raise RuntimeError("wechat_robot client terminated")
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers=self.extra_headers,
            )
        return self._session

    def mark_terminated(self) -> None:
        """Adapter terminate() sets this before close/None so is_closed is honest."""
        self._terminated = True

    def is_closed(self) -> bool:
        """Companion native_client_connected 会调用。

        - terminated → closed
        - session 已 close → closed
        - 尚未建 session → 仍可用 (False)
        """
        if self._terminated:
            return True
        if self._session is None:
            return False
        return bool(self._session.closed)

    async def close(self) -> None:
        self._terminated = True
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> Any:
        session = await self._get_session()
        url = f"{self.base_url}{path}"
        async with session.request(method, url, json=json) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(
                    f"wechat-robot API {method} {path} -> HTTP {resp.status}: {text[:500]}"
                )
            try:
                return await resp.json(content_type=None)
            except Exception:
                return text

    async def is_logged_in(self) -> bool:
        try:
            data = await self._request_json("GET", "/api/v1/robot/is-loggedin")
            if isinstance(data, dict):
                # 常见包装: {"code":0,"data":true} 或直接 bool
                if "data" in data:
                    return bool(data["data"])
                return bool(data.get("logged_in") or data.get("IsLoggedIn"))
            return bool(data)
        except Exception as e:
            logger.warning(f"查询 wechat-robot 登录态失败: {e}")
            return False

    async def send_text(
        self,
        to_wxid: str,
        content: str,
        at: list[str] | None = None,
    ) -> None:
        body: dict[str, Any] = {
            "to_wxid": to_wxid,
            "content": content,
        }
        if at:
            body["at"] = at
        await self._request_json("POST", "/api/v1/robot/message/send/text", json=body)

    async def send_image_urls(self, to_wxid: str, image_urls: list[str]) -> None:
        await self._request_json(
            "POST",
            "/api/v1/robot/message/send/image/url",
            json={"to_wxid": to_wxid, "image_urls": image_urls},
        )

    async def send_image_local(self, to_wxid: str, file_path: str) -> None:
        await self._request_json(
            "POST",
            "/api/v1/robot/message/send/image/local",
            json={"to_wxid": to_wxid, "file_path": file_path},
        )

    async def send_image_upload(self, to_wxid: str, file_path: str) -> None:
        """multipart 上传图片（AstrBot 与 robot-client 不在同一文件系统时用）。"""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/robot/message/send/image"
        path = Path(file_path)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        form = aiohttp.FormData()
        form.add_field("to_wxid", to_wxid)
        form.add_field(
            "image",
            path.read_bytes(),
            filename=path.name,
            content_type=mime,
        )
        async with session.post(url, data=form) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(
                    f"上传图片失败 HTTP {resp.status}: {text[:500]}"
                )

    async def send_file_local(self, to_wxid: str, file_path: str) -> None:
        await self._request_json(
            "POST",
            "/api/v1/robot/message/send/file/local",
            json={"to_wxid": to_wxid, "file_path": file_path},
        )

    async def send_file_upload(self, to_wxid: str, file_path: str) -> None:
        """若存在 multipart 文件接口则用；否则回退 local（同机路径）。"""
        # robot-client 的 /send/file 是分片协议，跨机不稳定；优先尝试同路径 local
        await self.send_file_local(to_wxid, file_path)
