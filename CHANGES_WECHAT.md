## 0.1.2-wechat.6
- Fix: robot-client `ToErrorResponse` returns HTTP 200 with JSON `code:500`; client now raises on business code != 200 for JSON + multipart (image/voice).
- Log successful voice upload (to_wxid + filename).

# wechat_robot adapter changes

## 0.1.2-wechat.5 (2026-10-31)
- Voice: `WechatRobotHTTPClient.send_voice_local` / `send_voice_upload` call real `/send/voice(/local)` APIs.
- `send_by_session`: Record uses voice API (not file); File still file.
- `WechatRobotEvent.send`: handles Record via shared `resolve_record_local_path` + voice API (prefer multipart upload like image).

## 0.1.2-wechat.4 (2026-10-30)
- H1: `send_by_session` sends Image/Record/File (align Event.send); empty chain raises (no false delivered).
- H4: replace `assert _client` with guard; `is_closed` / `mark_terminated` reflect terminate.
- M1: empty NewMsgId → synthetic dedup key; OrderedDict truncate.
- M2-lite: `Event.send` refuses gh_/system wxid.
- H2 left unchanged (webhook token / 0.0.0.0).
