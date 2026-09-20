# Compatibility matrix

| Component | Tested range | Notes |
|---|---:|---|
| Python | 3.11–3.13 | CI matrix |
| Hermes | `v2026.9.11`, `v2026.9.14`, `main` | pinned + daily main verification |
| `lark-oapi` | `>=1.7.3` | CardKit v2 and IM delivery |
| Feishu/Lark | CardKit v2 | China and Larksuite base URLs |

Hermes currently exposes package discovery but not a stable streaming renderer lifecycle API.
The package probes `register_streaming_renderer`; otherwise the fail-closed, reversible AST
compatibility adapter is used. `verify` compiles every target before `install` changes files.

Before an upgrade:

```bash
python -m hermes_lark_streaming doctor
python -m hermes_lark_streaming verify
```

