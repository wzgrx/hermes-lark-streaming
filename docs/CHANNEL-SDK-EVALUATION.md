# Official Lark Channel SDK evaluation

Evaluated on 2026-09-20 against
[`larksuite/channel-sdk-python`](https://github.com/larksuite/channel-sdk-python).

## Current decision

Keep `lark-oapi>=1.7.3` as the active runtime transport in 0.17.x. It exposes the precise CardKit
entity creation, batch update, close, IM reply and upload APIs already covered by this project's
recovery state machine. Do not run a second Channel connection beside Hermes' existing Feishu
adapter, because two event consumers/delivery owners would create duplicate replies and conflicting
callback resolution.

The standalone `lark-channel-sdk` is a credible migration target: its documented `FeishuChannel`
entry point covers event normalization, policy, outbound messaging, media, card callbacks,
streaming replies and deduplication. Doctor reports whether it is present, but never installs or
activates it.

## Migration gates

1. Hermes exposes a renderer/channel owner API that can suppress its native sender and carries the
   opaque platform route (`chat_id`, anchor/message id, profile/bot identity).
2. Shadow tests pass for topic anchors, stable UUID retry, `delivered/not_sent/unknown`, media,
   approval/clarify ownership, interruption, cron/background delivery and multi-bot routing.
3. The migration uses one transport owner per profile; no parallel SDK websocket is started.
4. Real Feishu and Lark tenant E2E passes before the AST adapter is removed.

## Operator probe

```bash
hermes-lark-streaming doctor --json
```

Inspect `sdk.channel_sdk`; `available: false` is expected and healthy for the current transport.

## 2026-10-03 visual-design review

The official Node SDK and CLI were reviewed separately for the footer design mismatch.
They are transport/operation tools, not replacement client renderers. Keep the current
single owner; prioritize Card JSON layout and real-device acceptance instead of adding
a second connection. See the [source-linked audit](FOOTER-DESIGN-AUDIT.md).
