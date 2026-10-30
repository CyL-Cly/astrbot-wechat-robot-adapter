# wechat_robot adapter changes

## 0.1.2-wechat.4 (2026-10-30)
- H1: `send_by_session` sends Image/Record/File (align Event.send); empty chain raises (no false delivered).
- H4: replace `assert _client` with guard; `is_closed` / `mark_terminated` reflect terminate.
- M1: empty NewMsgId → synthetic dedup key; OrderedDict truncate.
- M2-lite: `Event.send` refuses gh_/system wxid.
- H2 left unchanged (webhook token / 0.0.0.0).
