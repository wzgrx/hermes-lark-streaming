# Security policy

## Supported releases

Security fixes are applied to the newest minor release. Runtime compatibility is listed in
[`docs/COMPATIBILITY.md`](docs/COMPATIBILITY.md).

## Secret handling

- Keep Feishu/Lark credentials in Hermes secret scope or environment files outside Git.
- Multi-bot configuration contains environment-variable **names**, never credential values.
- Doctor, metrics, sidecar and E2E output contain only booleans, counters, latency and shortened IDs.
- The delivery ledger is mode `0600`, atomically replaced, retention/size bounded, and stores
  SHA-256 logical-key fingerprints plus required opaque CardKit/message ids—never message bodies,
  credentials, user ids or chat ids.
- Bind the optional sidecar to `127.0.0.1`; remote ingress requires TLS at a reverse proxy and
  `HERMES_LARK_SIDECAR_SECRET`.

## Reporting

Use GitHub private vulnerability reporting. Include the plugin version, Hermes version and the
sanitized output of `hermes-lark-streaming doctor --json`; remove message text and credentials.

