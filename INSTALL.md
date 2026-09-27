# Installation and updates — hermes-lark-streaming

The current Hermes package manager creates versioned runtime environments. Installing
only into `~/.hermes/hermes-agent/venv` does not install the plugin into the Gateway's
active environment. Use the managed directory plugin instead.

## Install

```bash
hermes plugins install wzgrx/hermes-lark-streaming --enable
hermes plugins doctor hermes-lark-streaming --ci
hermes pm install
```

The repository contains `plugin.yaml` and a root `__init__.py` for Hermes plugin
discovery. Its `pyproject.toml` declares runtime dependencies and a Python entry point;
Hermes chooses the enabled directory plugin when both forms are visible.

Before restarting, verify the **Gateway environment**, not merely the checkout:

```bash
hermes plugins list
hermes pm doctor
```

Find the Python executable used by the `hermes-gateway.service` process, then run
the following with that executable:

```bash
GATEWAY_PYTHON=/path/from/running/gateway/process
"$GATEWAY_PYTHON" -c 'import hermes_lark_streaming; print(hermes_lark_streaming.__version__)'
"$GATEWAY_PYTHON" -m hermes_lark_streaming verify
"$GATEWAY_PYTHON" -m hermes_lark_streaming status
```

The native plugin provides lifecycle observers. On Hermes releases without a native
Feishu renderer, the reversible AST hooks remain the CardKit delivery path:

```bash
"$GATEWAY_PYTHON" -m hermes_lark_streaming install
"$GATEWAY_PYTHON" -m hermes_lark_streaming status
hermes gateway restart
```

Configure Feishu/Lark credentials in the existing Hermes config or protected `.env`;
avoid adding duplicate credentials. After restart, confirm Gateway health and check
its journal for import or hook errors.

## Update

```bash
hermes plugins update hermes-lark-streaming
hermes plugins doctor hermes-lark-streaming --ci
hermes pm install
```

Re-run `verify` and `status` using the **new** Gateway environment. If a Hermes source
update changed hook anchors, run `uninstall` then `install` with that environment
before the Gateway restart. Preserve the `.hermes_lark.bak` files for rollback.

## Rollback

```bash
"$GATEWAY_PYTHON" -m hermes_lark_streaming restore
hermes plugins disable hermes-lark-streaming
hermes gateway restart
```

The restore command uses the hook backups and does not remove Feishu credentials.
