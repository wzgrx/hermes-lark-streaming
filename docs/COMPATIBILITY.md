# Compatibility matrix

| Component | Tested range | Notes |
|---|---:|---|
| Python | 3.11–3.13 | CI matrix |
| Hermes | `v2026.9.11`, `v2026.9.14` (`0.21.3`), `v2026.9.21` (`0.21.4`), `main` | pinned matrix + daily main verification |
| `lark-oapi` | `>=1.7.3` | active CardKit v2 and IM transport; feature-probed by doctor |
| `lark-channel-sdk` | optional evaluation target | reported by doctor; not installed or activated automatically |
| Feishu/Lark | CardKit v2 | China and Larksuite base URLs |

Hermes 0.21.3, 0.21.4 and current `main` expose observer-only streaming hooks through
`PluginContext.register_hook()`: `on_stream_start`, `on_stream_delta`, `on_stream_end` and
`on_interim_message`. Their callback returns are ignored, and their payloads do not contain a
Feishu `chat_id`/`message_id`; therefore they are used only for privacy-preserving lifecycle
metrics. The fail-closed, reversible AST adapter remains the sole CardKit delivery owner.

The package also probes the future `register_streaming_renderer` owner protocol. If Hermes adds
that API, native rendering takes precedence and observer/AST ownership is retired rather than run
in parallel.

Before an upgrade:

```bash
python -m hermes_lark_streaming doctor --json
python -m hermes_lark_streaming verify
```

If doctor reports missing `lark-oapi` constructors, repair only the active Hermes interpreter:

```bash
~/.hermes/hermes-agent/venv/bin/python -m hermes_lark_streaming repair-sdk
```
