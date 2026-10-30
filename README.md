# astrbot_plugin_wechat_robot

**仓库：** [`CyL-Cly/astrbot-wechat-robot-adapter`](https://github.com/CyL-Cly/astrbot-wechat-robot-adapter) · 对接客户端 [hp0912/wechat-robot-client](https://github.com/hp0912/wechat-robot-client)

把 [hp0912/wechat-robot-client](https://github.com/hp0912/wechat-robot-client)（iPad 微信机器人客户端）接到 [AstrBot](https://github.com/AstrBotDevs/AstrBot)。

**结论先说：**

1. 装到 AstrBot 插件目录 → 仪表盘启用平台 `wechat-robot`
2. 适配器会起一个 webhook HTTP 服务收消息
3. 在 wechat-robot **管理后台 → 系统设置 → webhook_url** 填该地址
4. 回复走客户端 `POST /api/v1/robot/message/send/*`

```
微信 ↔ wechat-ipad(协议服务端) ↔ wechat-robot-client ↔ [webhook] ↔ 本适配器 ↔ AstrBot
                                                      ↔ [HTTP 发送 API] ↩
```

---

## 安装

把本目录复制（或软链）到 AstrBot 插件目录，目录名建议：

```text
AstrBot/data/plugins/astrbot_plugin_wechat_robot/
```

需要包含：

```text
main.py
metadata.yaml
requirements.txt
wechat_robot_adapter.py
wechat_robot_event.py
wechat_robot_client.py
__init__.py
```

依赖：`aiohttp`（见 `requirements.txt`）。AstrBot 仪表盘 → 日志 → 安装 Pip 包，或：

```bash
pip install -r requirements.txt
```

重启 / 重载插件后，在 **消息平台** 里应出现 `wechat-robot`。

---

## 配置（AstrBot 平台项）

| 字段 | 默认 | 说明 |
|------|------|------|
| `wechat_client_base_url` | `http://127.0.0.1:9001` | wechat-robot-client 的 HTTP 根地址（容器内请用服务名，如 `http://wechat-client:9001`） |
| `webhook_host` | `0.0.0.0` | 适配器监听地址 |
| `webhook_port` | `6196` | 适配器监听端口 |
| `webhook_path` | `/wechat-robot/callback` | 回调路径 |
| `webhook_token` | 空 | 若填写，要求请求头匹配（配合客户端 webhook headers） |
| `webhook_token_header` | `X-Webhook-Token` | Token 头名 |
| `robot_wxid` | 空 | 兜底机器人 wxid；正常由回调 query `robot_wxid` 带上 |
| `ignore_self` | `true` | 忽略自己发出的消息（避免与客户端内置 AI 双回） |
| `ignore_system` | `true` | 忽略系统/撤回类 MsgType |
| `prefer_upload_image` | `true` | 发图优先 multipart `/send/image`，失败再试 local |
| `request_headers` | `{}` | 调客户端发送 API 时附加的 Header |

启用平台、保存后适配器会监听例如：

```text
http://<AstrBot可达地址>:6196/wechat-robot/callback
```

健康检查：`GET .../wechat-robot/callback/health`

---

## 在 wechat-robot 里接线

1. 打开 **管理后台 → 系统设置**
2. `webhook_url` 填适配器完整 URL（必须是 **client 容器能访问到的地址**，不要填 `127.0.0.1` 除非二者同网络命名空间）
3. 可选：`webhook_headers` 配 `{"X-Webhook-Token":"你的密钥"}`，与适配器 `webhook_token` 一致
4. **强烈建议关掉** wechat-robot 客户端自己的 AI 自动回复，只让 AstrBot 回，否则会双回

客户端推送时会自动在 URL 上追加：

```text
?robot_id=...&robot_code=...&robot_wxid=...
```

Body 为整包 `SyncMessage`（字段 `AddMsgs`），不是协议文档里的单条 `TypeName=AddMsg` 外壳。

---

## 已对接的 API

### 入站（webhook）

- 方法：`POST`
- Body：`{"AddMsgs":[ Message, ... ], ...}`（见 client `pkg/robot/message.go`）
- `FromUserName` / `Content` 等为 `{"string":"..."}` 形态
- 群聊 `Content` 常见：`sender_wxid:\n正文`
- 自消息：`FromUserName` / 群内前缀 == `robot_wxid`

### 出站（client HTTP）

| 用途 | 方法路径 | Body |
|------|----------|------|
| 文本 | `POST /api/v1/robot/message/send/text` | `{"to_wxid","content","at":[]}` |
| 远程图 | `POST /api/v1/robot/message/send/image/url` | `{"to_wxid","image_urls":[]}` |
| 本机图 | `POST /api/v1/robot/message/send/image/local` | `{"to_wxid","file_path"}` |
| 上传图 | `POST /api/v1/robot/message/send/image` | multipart：`to_wxid` + `image` |
| 本机文件 | `POST /api/v1/robot/message/send/file/local` | `{"to_wxid","file_path"}` |
| 登录态 | `GET /api/v1/robot/is-loggedin` | — |

客户端 HTTP **未见强制鉴权中间件**；若你在反代层加了鉴权，用 `request_headers` 带上。

---

## 能力与限制

| 能力 | 状态 |
|------|------|
| 私聊/群聊文本 | ✅ |
| 群 @ 解析（MsgSource atuserlist） | ✅ |
| 回复文本 / @ | ✅ |
| 回复图片（URL / multipart / local） | ✅ |
| 入站图片 | ⚠️ 缩略图 base64 可进 `Image`；原图需另调下载接口（未做） |
| 文件发送 | ⚠️ 仅 local 路径（AstrBot 与 client **同机或共享卷**） |
| 语音/视频/卡片 | ❌ 骨架未实现 |
| 流式回复 | ❌ `support_streaming_message=False` |

---

## 假设（对照 AstrBot 源码）

- 插件适配器构造：`(platform_config, platform_settings, event_queue)`，与内置 wecom 一致；文档里 `super().__init__(event_queue)` 已过时，实际为 `super().__init__(config, event_queue)`。
- Webhook 使用 `astrbot.core.platform.webhook_server.FastAPIWebhookServer`（`add_url_rule` + `run_task`），非旧版 queue API。
- `PlatformMetadata` 必须带 `id`。
- 注册名 / `type`：`wechat_robot`。

---

## 排障

1. **收不到消息**：在 client 所在网络 `curl -X POST` 打适配器 URL；检查防火墙与 `webhook_url` 是否对 client 可达。
2. **能收不回**：看 AstrBot 日志里对 `wechat_client_base_url` 的请求；确认 client 已登录（`/api/v1/robot/is-loggedin`）。
3. **双回复**：关 client 内置 AI，并保持 `ignore_self=true`。
4. **重复处理**：适配器按 `NewMsgId` 去重；重启后短期可能再处理历史同步消息。

---

## 目录

```text
astrbot-wechat-robot-adapter/
├── README.md
├── metadata.yaml
├── requirements.txt
├── main.py
├── __init__.py
├── wechat_robot_adapter.py   # Platform + webhook
├── wechat_robot_event.py     # 回复发送
└── wechat_robot_client.py    # aiohttp 封装发送 API
```

## 公众号（官方账号 / gh_*）

- 默认 **不** 把 `gh_*` 公众号推送当成私聊 FriendMessage。
- 近期推送进入进程内缓冲，仅工具可查：`wechat_official_list`、`wechat_official_lookup`。
- 禁止向 `gh_*` `send_by_session`（主动发消息也会被拒绝）。
- Companion / Memory 的微信 fork 同样拒绝把公众号绑成私聊窗口。
