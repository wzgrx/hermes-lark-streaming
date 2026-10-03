# Installation and updates — hermes-lark-streaming

The current Hermes package manager creates versioned runtime environments. Installing
only into `~/.hermes/hermes-agent/venv` does not install the plugin into the Gateway's
active environment. Use the managed directory plugin instead.
The community-source scan currently reports CAUTION for documentation, CI, and test
patterns; review its findings before using `--force` for this repository.

## Install

Run the install command in an interactive terminal and approve the displayed
Python dependencies. `--enable` enables the plugin; it does not grant dependency
consent. Without a TTY, Hermes declines to replace an active plugin when it has
not received dependency consent, leaving the prior installation untouched.
For an already installed plugin with unchanged dependencies, use the managed
update command below instead of reinstalling it.

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

For the existing managed installation, keep its provenance and dependency
consent with the update command:

```bash
hermes plugins update hermes-lark-streaming
hermes plugins doctor hermes-lark-streaming --ci
hermes pm install
```

If the installation is pinned with `--ref`, ordinary reinstall **retains** the
saved pin, even when the new command omits `--ref`. An explicit full-SHA `--ref`
moves an immutable pin; it does not switch to main tracking.

To resume main tracking, first preserve the installed checkout and any
`plugins.entries` configuration/grants, then use managed removal followed by
interactive installation from the fork **without** `--ref`. Removal clears the
installer-owned pin record and the plugin's enable/grant entries; restore any
custom entries with Hermes config APIs afterwards. Keep Gateway stopped or idle
through this operation. Verify the installed SHA and `pinned: false` in
`$HERMES_HOME/plugins/.install-metadata.json` before restarting.

```bash
hermes plugins remove hermes-lark-streaming
hermes plugins install wzgrx/hermes-lark-streaming --enable --force
```

### Scan confirmation on update

Changed executable content must still pass the scanner. Current upstream update
CLI can stop at CAUTION findings without offering the install path's confirmation.
The maintained Hermes overlay supplies interactive confirmation and an explicit
`hermes plugins update NAME --force` review flag (feature-detect it with
`hermes plugins update --help`). This accepts **CAUTION only** for a reviewed
custom Git update. Dangerous findings, source blocklists, pins and newly declared
Python dependency consent remain enforced. Do not disable global scanning.
An unchanged Git revision and unchanged non-Git tree publish nothing and do not
repeat dependency/scanner admission.

For this maintainer's installation, after reviewing the candidate and its CI:

```bash
hermes plugins update hermes-lark-streaming --force
hermes plugins doctor hermes-lark-streaming --ci
hermes pm install
```

Re-run `verify` and `status` using the durable launcher. If Hermes changed hook
anchors **or the plugin changed generated hook content**, run `uninstall` then
`install` while Gateway is idle/stopped before restarting. Idempotent `install`
alone retains existing markers; it does not upgrade their content. **0.20.1
requires this refresh** to forward completion `result` / `is_error`.
Preserve the `.hermes_lark.bak` files and the old managed plugin for rollback.

```bash
hermes gateway stop  # first confirm no active task
hermes --run-module hermes_lark_streaming uninstall
hermes --run-module hermes_lark_streaming install
hermes --run-module hermes_lark_streaming status
hermes gateway start
```

On a maintenance checkout whose updater requires a clean tree, review and record
only the generated hook diff in a **local maintenance commit**, not an upstream
core push. Preserve the previous commit for rollback. Do not mix unrelated local
edits into that commit or delete historical cards/receipts to produce a clean report.

## Rollback

```bash
"$HERMES_LAUNCHER" --run-module hermes_lark_streaming restore
hermes plugins disable hermes-lark-streaming
hermes gateway restart
```

The restore command uses the hook backups and does not remove Feishu credentials.

### Exact reviewed snapshot (maintained Hermes overlay)

```bash
hermes plugins update hermes-lark-streaming --force --expected-revision REVIEWED_40_CHARACTER_SHA
```

This guard aborts before publication if the remote moves beyond the reviewed
commit. It applies to full custom Git checkouts; catalog and subdirectory
installs use their own provenance workflow. `--force` accepts only CAUTION
findings and does not grant new Python dependency or capability consent.
