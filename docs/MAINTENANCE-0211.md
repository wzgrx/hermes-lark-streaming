# 0.20.11 — partial resource snapshots without visual noise

Date: 2026-10-04. Keep the approved V1 desktop design and improve observable
resource labels rather than adding more panels, polling or a second writer.

## Fixes and scope

- A process count disappeared whenever uptime was missing, and vice versa.
  Each available observation now survives independently; unknown fields do not
  become invented zeroes.
- Uptime under a day previously displayed zero days and truncated hours. Short
  uptime now uses hours/minutes, minutes/seconds or seconds. Multi-day compact
  display remains unchanged.
- Without GPU data, the collapsed row previously consumed most of its width
  with missing-value placeholders. It now prioritizes available CPU/RAM.
  Temperature without utilization retains the GPU label. No observations uses
  a localized pending title; an attempted sample with no metrics is labelled
  unavailable instead of incorrectly claiming it was never sampled.
- A complete GPU/VRAM/RAM snapshot keeps the exact previous title ordering.
  The expanded two-row/two-column grid, unknown-value markers, snapshot scope,
  sampled timestamp, folded default and five frozen SVGs are unchanged.

Nine new test cases include six failures reproduced against 0.20.10. Full local
suite: **1574 passed**, with two existing SDK deprecation warnings. Ruff and
mypy pass. Tests cover complete snapshots as a compatibility guard, not only
the new partial states. Source changes do not add dependencies, configuration,
schemas, hook patches or resource collection frequency.

## Upstream review

The official Hermes main checked this round remains
[`24b9f0f8c5df`](https://github.com/NousResearch/hermes-agent/commit/24b9f0f8c5df5ec6d3d5c10ad9b27c3346bbc925),
already deployed with reviewed overlays as `0764e9165721`. No redundant core
update or overlay rewrite is needed. Card upstream remains `5eb7c2738eda`.
Reviewed open issues 116, 111, 109, 98, 82 and PRs 115, 114, 112, 110 against
the maintained config-cache, interruption, media and single-writer recovery
tests. These open reports are not a claim of a newly reproduced local incident.

LCM upstream PRs 663, 659, 655, 657, 502 and 540 were separately inspected;
the paired LCM maintenance focuses on persisted-output replay lookup (#655).
SQLite lock protections already present in the fork stay intact. Broader UID,
tool-budget and provider policy changes require their own gates; they were not
blindly merged into this UI maintenance.

## Verification boundaries

The native JSON builder is tested, not a browser-painted imitation. No new
Feishu message or model request is sent by these tests. Previous 0.20.8 real
turn and older desktop screenshots keep their historical labels; this release
does not claim a new real-model turn or pixel-identical visual acceptance.
Exact-commit CI and deployment receipts are appended after their separate gates.


## Verified publication and deployment

Reviewed Card code: `1c199d68ba3aa6cd903aed3bea48b94068b7033a` (**0.20.11**).
Reviewed LCM code: `5d0c7768227218200f18fd9c75195d2f4fef9739` (maintenance after **1.0.0-rc.2**, no new tag).
Exact-commit hosted gates passed: [wzgrx/hermes-lark-streaming CodeQL](https://github.com/wzgrx/hermes-lark-streaming/actions/runs/37175026423); [wzgrx/hermes-lark-streaming Tests](https://github.com/wzgrx/hermes-lark-streaming/actions/runs/37175026352); [wzgrx/hermes-lcm CI](https://github.com/wzgrx/hermes-lcm/actions/runs/37175273052).

After two idle checks, one graceful Gateway stop/start loaded both exact plugin
commits through Hermes managed update and PM. Official Hermes remains the
already-current reviewed `24b9f0f8c5df` plus local overlays (`0764e9165721`).
Card/LCM plugin doctors, PM doctor, hook verification/reinstallation, frontend
freshness, Card smoke and runtime import checks passed. Gateway is running and
Feishu connected; source checkouts are clean and the updater timer is restored.

Private pre-update snapshots were made through SQLite's online backup API,
including committed WAL state, and verified for each database. Configuration
and credential-file hashes match protected backups; conversations, model
settings and historical usage were preserved. Read-only quick checks of all
three live databases pass. Current-process journal has no traceback. Existing
`metrics_stale` on idle and historical `delivery_unknown` remain visible;
no old message was automatically resent or old ledger row erased.

Synthetic checks in the selected PM runtime confirmed the new partial-resource
renderer and the installed LCM damaged-entry, fresh-content and session-scoped
lookup contracts. Zero provider calls, zero messages and zero live database
writes were made by that check. This is not a new model turn or native-client
visual acceptance. Private rollback refs, plugin copies and PM inputs remain
local. This receipt is a documentation-only follow-up to the tested code.
