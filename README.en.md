# Hermes Lark Streaming

**Streaming Feishu/Lark cards, per-turn telemetry, and persistent usage history for Hermes.**

[![Tests](https://github.com/wzgrx/hermes-lark-streaming/actions/workflows/test.yml/badge.svg)](https://github.com/wzgrx/hermes-lark-streaming/actions/workflows/test.yml)
![Code version](https://img.shields.io/badge/code-0.17.1-blue)
[![MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[中文](README.md) · [Install](INSTALL.md) · [Footer](docs/FOOTER-V2.md) · [History](docs/USAGE-HISTORY.md) · [Design audit](docs/FOOTER-DESIGN-AUDIT.md)

> **2026-10-03 status:** code 0.17.1 is deployed; 1058 offline tests and CardKit server probes passed. **Visual acceptance is still open and has failed the reviewed design comparison.** The current details use two columns rather than the original three-column field grid. Code version does not imply a same-version PyPI or GitHub Release publication.

![Runtime architecture](docs/assets/runtime-overview.svg)

## Scope

Hermes owns credentials, inference, routing, tools, and conversations. This plugin owns native CardKit delivery and observes public usage hooks. It is Hermes-only, not an OpenClaw plugin or a second model client.

- Stream answers, reasoning and tools in event order.
- Commit sequence numbers after success; retain stable UUIDs and delivered/not_sent/unknown receipts.
- Roll over long-running cards and preserve interruption, approval, cron and background boundaries.
- Enable Footer V2 for per-turn usage, cache, last-request context, timing and collapsed details.
- Store opt-in usage history in profile-local SQLite; query by model, provider, subscription label, day/month and timezone.
- Keep catalog coverage, protocol fixtures, server verification and real-client acceptance separate.

## Current footer structure

![Code-derived footer schematic](docs/assets/footer-current-structure.svg)

**This is a code-derived schematic with synthetic data, not a screenshot or a pixel-accurate Feishu renderer.**
The deployed implementation has two summary rows and five detail groups. Unknown cost/compression stays unknown. Some real cards currently lack a displayed reasoning setting; that is an open collection/display investigation, not evidence that model reasoning is disabled.

[Synthetic Card JSON](docs/assets/footer-example.json) · [Rebuild assets](scripts/build_readme_assets.py)

## Managed setup

Read [INSTALL.md](INSTALL.md) for source review, dependency consent, exact revision updates and rollback.

```bash
hermes plugins install wzgrx/hermes-lark-streaming --enable --force
hermes pm install
hermes plugins doctor hermes-lark-streaming --ci
hermes --run-module hermes_lark_streaming verify
hermes --run-module hermes_lark_streaming install
```

The review flag is not a scanner disable switch. Use the durable Hermes launcher, not a legacy venv. Restart only in an idle maintenance window after verification.

Merge into existing configuration without replacing credentials or provider settings:

```yaml
streaming:
  enabled: true
  width_mode: default
  footer:
    enabled: true
    mode: enhanced
    details: true
    text_size: normal
    history:
      enabled: true
```

Repository defaults remain classic footer and history disabled. History collection is independent of footer visibility.
Requirements: Python ≥3.11, a compatible Hermes host, `lark-oapi >=1.7.3`, `PyYAML >=6.0.3`, and the required Feishu application permissions. See the [CI compatibility matrix](docs/COMPATIBILITY.md). Node SDK and CLI are optional, not runtime prerequisites.

## Historical usage

```bash
hermes --run-module hermes_lark_streaming history --timezone Asia/Shanghai
hermes --run-module hermes_lark_streaming history --month 2026-10 --timezone Asia/Shanghai --json
hermes --run-module hermes_lark_streaming history --group-by model --json
hermes --run-module hermes_lark_streaming history --scope auxiliary --json
```

The database is `$HERMES_HOME/state/card-usage.sqlite3`. Queries are read-only and make no model calls. Input includes cache; context is the last request, not turn totals. Provider/subscription labels are not verified account identities. Collection starts after enablement, stores no prompts or secrets, and is not an account-wide bill. Use SQLite's backup API for online snapshots; see [history details](docs/USAGE-HISTORY.md).

## Verification and open work

```bash
hermes --run-module hermes_lark_streaming doctor --json
hermes --run-module hermes_lark_streaming status
hermes --run-module hermes_lark_streaming metrics --json
hermes --run-module hermes_lark_streaming smoke
```

Default smoke is offline. Explicit `smoke --execute --closed-stream-probe` tests a real unattached CardKit entity; it is not a screenshot test.

| Milestone | Status |
|---|---|
| Normalization, isolated turn collection, ledger, CLI reports | Implemented, automated tests pass |
| Managed 0.17.1 deployment and CardKit final update | Verified |
| Real desktop inspection of collapsed/expanded cards | Performed; design mismatch confirmed |
| Original three-column layout, title styling and spacing | Pending implementation and acceptance |
| Reliable reasoning-setting display in real requests | Open investigation |
| Mobile/dark-mode/long-running visual matrix | Pending |
| Committed LCM compression telemetry, account quotas and billing | Pending reliable data sources |
| Historical-report buttons or web dashboard | Not implemented; CLI is available |
| Every account in the 226-entry provider catalog | Not certified |

**The whole plan is not complete.** Read [plan status](docs/FOOTER-V2-PLAN.md), [validation evidence](docs/FOOTER-V2-VALIDATION.md), and [roadmap](docs/ROADMAP.md).

## SDKs do not replace client rendering

The official [Node SDK](https://github.com/larksuite/node-sdk) and [CLI](https://github.com/larksuite/cli) submit Card JSON or expose operation/inspection tools. The Feishu client renders native components. Installing another sender does not implement a missing layout. Use the [official card builder](https://open.feishu.cn/tool/cardbuilder) and real-device comparisons; preserve a single Gateway delivery owner.

Image-based summaries preserve a fixed internal composition but lose native text/interaction; a web dashboard offers HTML/CSS control with extra hosting/access-control work. Neither is silently substituted for a native interactive footer. See the [source-linked design audit](docs/FOOTER-DESIGN-AUDIT.md).

## Development

Use an isolated development environment, not the live Gateway's PM generation.

```bash
python -m pip install -e ".[dev]"
python -m ruff check hermes_lark_streaming tests
python -m mypy --explicit-package-bases hermes_lark_streaming
python -m pytest tests -q
python scripts/build_readme_assets.py
```

[Operations](docs/OPERATIONS.md) · [Provider coverage](docs/PROVIDER-COVERAGE.md) · [Changelog](CHANGELOG.md) · [Contributors](README.md#贡献者)

Thanks to the upstream community and inspiration from [openclaw-lark](https://github.com/larksuite/openclaw-lark) and [hermes-feishu-streaming-card](https://github.com/baileyh8/hermes-feishu-streaming-card).

## License

[MIT](LICENSE)
