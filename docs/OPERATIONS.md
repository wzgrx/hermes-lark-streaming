# Operations and high-concurrency deployment

## Diagnostics

```bash
hermes-lark-streaming doctor
hermes-lark-streaming doctor --json
hermes-lark-streaming metrics --json
hermes-lark-streaming smoke
hermes-lark-streaming lark-cli-smoke
```

`smoke` is offline by default. A real CardKit create → stream → close → update check is explicit:

```bash
hermes-lark-streaming smoke --execute --chat-id oc_TEST_CHAT
```

The optional official `lark-cli` is used only for operational preflight; it is not a runtime
dependency. `lark-cli-smoke --execute` runs read-only auth-status/schema commands.

## Adaptive backpressure and history compaction

```yaml
streaming:
  adaptive_backpressure:
    enabled: true
    min_ms: 100
    max_ms: 1500
  history_compaction:
    compact_after: 48
    keep_recent: 24
```

Rate limiting doubles the interval within the configured bound. Successful low-latency batches
gradually return it to the minimum. Terminal cards summarize old successful tool steps, retain
all old errors and keep the newest window at full fidelity.

## Multi-bot exact routing

```yaml
streaming:
  bots:
    default: primary
    chat_bindings:
      oc_EXACT_CHAT: support
    items:
      primary:
        app_id_env: FEISHU_PRIMARY_APP_ID
        app_secret_env: FEISHU_PRIMARY_APP_SECRET
      support:
        app_id_env: FEISHU_SUPPORT_APP_ID
        app_secret_env: FEISHU_SUPPORT_APP_SECRET
        base_url: https://open.feishu.cn
```

Each bot gets a separate SDK client/token cache. Bindings are exact; an unknown chat uses the
default bot. The configuration stores only environment-variable names.

## Optional sidecar

```bash
export HERMES_LARK_SIDECAR_SECRET='generate-a-random-secret'
hermes-lark-streaming sidecar --host 127.0.0.1 --port 8788
```

`GET /health` and `GET /metrics` expose no message content. `POST /events` requires a
domain-separated HMAC proof with timestamp and nonce; the replay cache is TTL- and size-bounded.
In-process card delivery remains the default and the sidecar is not required for normal use.

