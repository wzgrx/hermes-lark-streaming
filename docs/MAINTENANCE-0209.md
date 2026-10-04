# 0.20.9 — keep partial cache evidence visible

Date: 2026-10-04. Bounded follow-up to one explicitly confirmed 0.20.8 test;
no extra user messages, unrelated chat writes or model calls are part of this fix.

## Actual 0.20.8 acceptance and discovered issue

The one approved message was sent as the user into the existing independent V1
test topic. Both inbound and interactive reply were read back from Feishu:
same chat/root topic, one delivery, two terminal calls, exits **0 / 7**, expected
stdout markers and final answer. Two main API requests, no main API error,
no CardKit API errors or current-process traceback. Gateway was not restarted
for that test. No extra tools edited files or memories.

The Windows native client was inspected with tools and Footer expanded:
compact error summary, millisecond durations, multiline error details, separate
answer, model/requested effort and two-column footer were present. Input/output,
last context and local-history totals matched read-only records. These are real
0.20.8 observations, not synthetic data or pixel-identical certification.

That test also exposed a defect: a positive cache observation in one API request
was hidden because another request's canonical cache bucket was zero. The
earlier conservative rule correctly avoided inventing a zero-percent hit rate,
but also discarded useful positive evidence for the whole turn.

## Fix and data contract

- Keep canonical zero-as-unknown handling: the upstream usage contract can
  zero-fill missing optional fields; presence is not established by zero alone.
- Aggregate positive, internally consistent cache observations even if another
  request lacks a cache observation. Add `cache_read_partial` independently of
  `usage_partial`; complete input/output totals stay complete.
- Both V1 and classic enhanced details mark the cache count as **partial** and
  **≥ observed count**. The hit rate stays **— / Not reported**, not a fabricated
  percentage. All-positive complete coverage retains the ordinary exact ratio.
- Failed/unknown attempts retain the same lower-bound rule. No known cache
  evidence still means unknown, not a made-up zero. Cache remains inside input.
- No additional rows, provider client, dependency, schema, hook or configuration
  change. Existing cards and historical ledger rows are not rewritten.

For example, two 100-token requests with canonical cache `[0, 40]` now show
`≥40 / —`, not `Not reported` and not an unjustified `40 / 20%`.

## Verification

- Fifteen new cases; eleven failed before the correction. Tests cover cold/zero,
  absent, invalid, out-of-range and reversed request order, failed attempts,
  complete positive coverage and all-unknown buckets, across both layouts.
- Focused suite: **342 passed**. Full local suite: **1554 passed**; two existing
  lark-oapi deprecation warnings remain visible. Ruff and mypy pass.
- Cohere V2 usage adapters from the preceding tested commit are included in this
  code version. They are offline normalization, not a new Hermes transport or a
  claim of real Cohere account acceptance.
- Runtime deployment and post-restart replay are recorded separately below;
  the 0.20.8 message is not relabelled as a fresh 0.20.9 model turn.

Private native screenshots, message identities and database records remain local.
Public docs contain only the bounded verification scope and synthetic examples.
The five frozen V1 SVGs and labelled 0.20.6 public screenshots remain unchanged.

## Upstream contract

The reviewed Hermes source remains
[`a4648c5f58ba`](https://github.com/NousResearch/hermes-agent/commit/a4648c5f58bad6304a0034dc3405fada207ac6a4)
plus the already-tested local overlays (`919093187f1c`). No further core update
is needed for this presentation correction. Local source review checks the
canonical usage pipeline, not just provider-specific response fields.

## Publication and runtime evidence

Reviewed code commit: `a54fbae6ebdb5a8e657b36cf87bb365af7bcb014`.
Exact-commit [Tests](https://github.com/wzgrx/hermes-lark-streaming/actions/runs/37172004237)
and [CodeQL](https://github.com/wzgrx/hermes-lark-streaming/actions/runs/37172004233)
completed successfully. Tests include Python 3.11/3.12/3.13 and the Hermes
main/tag compatibility matrix. The isolated local suite also passed 1554 tests.

After two idle checks, the managed updater published that exact reviewed commit,
then PM synchronized the runtime. Card and LCM plugin doctors, PM doctor, hook
verify/uninstall/install/status, frontend freshness and local smoke passed.
One graceful Gateway stop/start loaded **0.20.9**; Gateway reports `running` and
Feishu `connected`. Hermes core remains `919093187f1c`, LCM remains unchanged.
The core and both managed plugin checkouts are clean. The update timer is restored.

Read-only `quick_check` passes for the conversation, LCM and usage databases.
Configuration and credential-file hashes match the backup; no historical rows
or cards were rewritten. Current-process journal has no traceback. Existing
`metrics_stale` and historical `delivery_unknown` warnings remain visible rather
than being erased or causing automatic resends.

The managed **0.20.9 runtime** then replayed only the saved scalar measurements
from the approved **0.20.8** test. Both footer layouts retained positive cache
evidence with a partial lower-bound label and no invented ratio; input/output
totals stayed unchanged. This replay made **zero provider calls and zero sends**.
It is post-restart package/renderer validation, not a second real-model turn or
a new server-rendered card. Private receipts and rollback material remain local.
