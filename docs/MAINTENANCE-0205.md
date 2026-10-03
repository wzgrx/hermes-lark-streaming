# 0.20.5: V1 readability and current Hermes compatibility

Date: 2026-10-04. This is a bounded maintenance release, not a new UI framework.
Preserve the approved three native collapsible panels and independent answer body.

## Reproduced fixes

| Problem | New behavior |
| --- | --- |
| An early error stays highlighted but its command/output falls out of the last-24 excerpt window | Highlighted error/running excerpts get priority; selected records remain chronological and byte-bounded |
| Empty structured error masks a useful plain error | Display the available plain error; retain escaping and secret redaction |
| 15 ms displays as 0.0 s; 68 ms as 0.1 s | Subsecond timing uses milliseconds |
| Folded model footer hides a failed/stopped turn | Native localized terminal-state prefix in the folded heading |

No extra settings or SDK, no new buttons, no additional expanded rows, no mobile work.
Selection is bounded, not a complete raw log. Many errors can still exceed the native
card budget; counts and omission labels stay visible. The immutable V1 design SVGs
and older version-labelled client screenshots are retained.

## Upstream review

- [Config hot-path PR 115](https://github.com/Cheerwhy/hermes-lark-streaming/pull/115): existing fork TTL/stat reload already covers the parse-per-delta issue; retain last-valid-config handling.
- [Anchor/sequence PR 114](https://github.com/Cheerwhy/hermes-lark-streaming/pull/114): existing fork single writer, accepted sequence and bounded reseed remain; do not layer another recovery loop.
- [Long turn / follow-up issue 116](https://github.com/Cheerwhy/hermes-lark-streaming/issues/116): the report is on upstream 0.13.0 and includes a speculative interruption cause. This fork forwards the authoritative interrupt flag, confirms queued boundaries, rotates expiring cards and retains bounded recovery. Current-source callback/queued tests are compatibility evidence, not a reproduction of that user's live incident.
- Hermes target for this round: official main `158fd638da1629c8e62caf9ade1515d162def8ab` plus retained local overlays. Adapt the names-only skill index to the new upstream tiered/duplicate resolver instead of restoring removed scanning logic.

## Evidence boundary

Eight added readability cases were red on 0.20.4 and green after the fixes;
the focused V1/config/lifecycle gate passes 292 tests. Full suite, GitHub CI and
managed deployment are separate gates, recorded below once completed.
No new user-message model turn or client pixel-equality claim is implied.

- Full Card suite: **1490 passed** against the reviewed modular Hermes snapshot.
- Ruff and mypy: pass (49 source files).
- Broader Hermes check: 102 files, 3425 passed, 2 failed, 14 skipped. One failure
  was a missing optional ACP dependency in the test environment; a fresh PM test
  environment with the declared ACP extra passes all 22 manual-compression tests.
  The remaining upstream Telegram early-cancellation regression is outside this
  Feishu deployment's enabled platform, and is retained as a known finding rather
  than silently excluded or described as an all-green core suite.

## Published and deployed

- Code commit: `51980761d2bf9b4faaadfe64a497c02d9b7dc1bc` on this fork's main.
- [Tests](https://github.com/wzgrx/hermes-lark-streaming/actions/runs/37161552505)
  and [CodeQL](https://github.com/wzgrx/hermes-lark-streaming/actions/runs/37161552509)
  succeeded on that exact code commit.
- LCM compatibility against the new Hermes snapshot: 3482 passed, 6 skipped,
  12 expected failures. LCM source revision was intentionally unchanged.
- Feishu CardKit accepted unattached complete-card entities for both failure and
  stop states. This validates JSON acceptance, not desktop rendering or a model turn.
- Double-idle guarded deployment completed. Core runtime:
  `194501c6a78574f6ea7498ae7661dc78ec36276c`, incorporating the official main target
  and 30 retained local commits. Card loads 0.20.5 from the PM-managed generation.
- Gateway running, Feishu connected; PM doctor, both plugin doctors, hook
  verify/install/status, Card doctor and dry smoke pass. All three SQLite
  databases pass read-only quick_check. Config and credential files are byte-identical.
- TUI/Web build freshness verified. The staged Web receipt initially differed
  because the runtime source retained older untracked fonts/assets. Rebuilding
  through the official Web builder preserved these files and restored freshness;
  nothing was deleted to force a passing check.
- Historical `delivery_unknown` and the pre-turn `metrics_stale` warning remain
  visible. Core doctor still reports optional unconfigured tools, including
  image generation; no keys or permissions were invented and no tools disabled.
- Current-process journal has no traceback. No new user-message model turn was
  sent; older chat cards were not rewritten. This is not a blanket zero-bug claim.

Private deployment/rollback receipt on the operator host:
`/home/wzgrx/.hermes/state/hermes-reviewed-deploy-20261004-card0205.json`.
