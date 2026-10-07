# Security policy

## Supported releases

Security fixes are applied to the latest release. The plugin targets the modular Hermes gateway (0.21.3 or newer);
`python -m hermes_lark_streaming verify` reports whether the installed Hermes can be patched.

## Secret handling

- Keep Feishu/Lark credentials in Hermes' secret scope or `.env` outside Git. Multi-bot configuration holds
  environment-variable **names**, never values.
- Text that reaches a card (tool arguments, errors, reasoning) is redacted for credentials first, then escaped.
- The Lark SDK logger is filtered so websocket `access_key`/`ticket` values never reach the gateway log.
- The delivery ledger is mode `0600`, atomically replaced and size bounded. It stores SHA-256 fingerprints of logical
  keys plus the opaque CardKit and message ids, never message bodies, credentials, user ids or chat ids.
- Account and usage data is read-only: the plugin starts no background process and never switches accounts.
- Account quotas are shown only in chats listed under `streaming.details.accounts.allowed_chats`.

## Reporting

Use GitHub private vulnerability reporting. Include the plugin and Hermes versions and the output of
`python -m hermes_lark_streaming status`; remove message text and credentials first.
