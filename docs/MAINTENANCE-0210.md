# 0.20.10 — accurate compact labels and reviewed Hermes update

Date: 2026-10-04. Bounded maintenance of the approved V1 desktop card.
No additional user message, provider request, dependency, database schema,
generated hook or frozen SVG change is part of this correction.

## Reproduced UI defects

1. A partial cache lower bound of 48,384 tokens was rounded up to `≥48.4k`,
   overstating the available evidence. Lower bounds now abbreviate downward:
   `≥48.3k`. Ordinary complete totals retain nearest rounding; classic details
   retain exact integer lower bounds. No cache ratio is invented.
2. The first-response label always said "including retry", even when wall and
   responding-attempt latency were identical. The label now mentions waiting
   only when both measurements establish an additional interval. It does not
   claim every earlier interval was a failed retry. Measurements stay unchanged.
3. Missing model/context and partial-title labels mixed Chinese and English in
   the same native title. Each locale now receives its own compact title.
4. Missing duration left a dangling separator after completion status. Unknown
   timing now omits the separator instead of inventing zero seconds.

Tool/resource/answer/footer ordering, collapsed defaults, two-column density,
single-writer delivery, status semantics and frozen V1 designs are unchanged.
These are presentation correctness fixes, not a new mockup or a pixel-identical
client certification. Public earlier screenshots retain their version labels.

## Official Hermes and upstream review

The selected official snapshot is
[`24b9f0f8c5df`](https://github.com/NousResearch/hermes-agent/commit/24b9f0f8c5df5ec6d3d5c10ad9b27c3346bbc925),
16 commits beyond the previously deployed `a4648c5f58ba` base. Source review
covered shared launchers and temporary/borrowed `HERMES_HOME`, PM ownership,
one-shot Relay session finalization and WhatsApp per-profile bridge ownership.
These changes do not redesign the Feishu schema or add a new footer event.
All 30 existing local overlays rebased without conflicts and were checked as
patch-equivalent; the tested local result is `0764e9165721`.

The community Card upstream remains `5eb7c2738eda` (0.13.0). Rechecked issues
116, 111, 109, 98 and 82, and PRs 115, 114, 112 and 110; no new upstream commit
was imported. The [missing-element report](https://github.com/Cheerwhy/hermes-lark-streaming/issues/98)
remains relevant to existing reseed/single-writer tests, not a new failure
reproduced in this round. No claim is made that every open upstream issue has
just been resolved. The user fork is maintained directly on main, without a PR.

## Automated evidence

- Eleven new cases; ten failed before correction. Seven abbreviation boundaries
  include near-unit rollover and large integer totals. Three timing cases cover
  no extra wait, observed extra wait and absent attempt evidence.
- Focused tests: **58 passed**. Complete local Card suite: **1565 passed**,
  retaining two existing lark-oapi deprecation warnings. Ruff and mypy pass.
- Official Hermes test runner: **21 files, 680 passed, 0 failed, 15 skipped**
  on the staged snapshot. Includes all seven changed upstream test files plus
  plugin install/update, PM, launcher, Feishu, ledger and provider regressions.
  Platform skips are retained; these are not Windows runtime acceptance.
- LCM against the staged Hermes snapshot: **3482 passed, 6 skipped, 12 xfailed**.
  LCM source remains unchanged. Exact-commit GitHub CI and runtime results are
  recorded separately below after completion.

Tests use isolated Hermes homes and bounded workers. No credentials, message
identities, raw conversation content or production database rows are published.

## Deployment scope

Deployment is separately gated on exact-commit Tests/CodeQL, clean checkouts,
two idle checks, rollback refs and protected config/PM metadata backups.
Use Hermes-managed plugin update and PM, refresh/verify hooks, gracefully
restart Gateway, verify frontend freshness, Feishu connection, doctors, smoke,
runtime version and read-only database quick checks. Preserve configuration,
all sessions, historical usage and the unchanged LCM revision.

Post-restart replay may use saved scalar measurements from the already-approved
0.20.8 turn without new model calls or messages. Such a replay validates loaded
code, not a new live model turn or a fresh desktop screenshot. Existing stale
metrics or historical unknown-delivery warnings stay visible and are not erased
or automatically resent. Runtime publication is not claimed by this section;
the concrete receipt is appended once completed.
