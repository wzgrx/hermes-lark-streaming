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
