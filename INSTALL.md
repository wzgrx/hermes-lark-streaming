# Installation and updates — hermes-lark-streaming

The current Hermes package manager creates versioned runtime environments. Installing
only into `~/.hermes/hermes-agent/venv` does not install the plugin into the Gateway's
active environment. Use the managed directory plugin instead.
The community-source scan currently reports CAUTION for documentation, CI, and test
patterns; review its findings before using `--force` for this repository.

## Install

```bash
hermes plugins install wzgrx/hermes-lark-streaming --enable --force
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

Use Hermes's durable managed launcher. It selects the same PM dependency generation
as the Gateway; the raw process executable alone does not select that generation:

```bash
HERMES_LAUNCHER="$HOME/.local/bin/hermes"
"$HERMES_LAUNCHER" --run-module hermes_lark_streaming verify
"$HERMES_LAUNCHER" --run-module hermes_lark_streaming status
```

The native plugin provides lifecycle observers. On Hermes releases without a native
Feishu renderer, the reversible AST hooks remain the CardKit delivery path:

```bash
"$HERMES_LAUNCHER" --run-module hermes_lark_streaming install
"$HERMES_LAUNCHER" --run-module hermes_lark_streaming status
hermes gateway restart
```

Configure Feishu/Lark credentials in the existing Hermes config or protected `.env`;
avoid adding duplicate credentials. After restart, confirm Gateway health and check
its journal for import or hook errors.

## Update

```bash
hermes plugins install wzgrx/hermes-lark-streaming --enable --force
hermes plugins doctor hermes-lark-streaming --ci
hermes pm install
```

Re-run `verify` and `status` using the durable launcher. If a Hermes source
update changed hook anchors, run `uninstall` then `install` with that launcher
before the Gateway restart. Preserve the `.hermes_lark.bak` files for rollback.

## Rollback

```bash
"$HERMES_LAUNCHER" --run-module hermes_lark_streaming restore
hermes plugins disable hermes-lark-streaming
hermes gateway restart
```

The restore command uses the hook backups and does not remove Feishu credentials.
