# 0.20.6: preserve known cache counts

Date: 2026-10-04. A small correctness fix within the existing V1 layout.

## Reproduced issue and behavior

The renderer previously required a valid hit-rate denominator before displaying
either half of **Cache read / hit**. A known count was incorrectly hidden as
"Not reported" when input was zero, absent or the turn was partial.

| Observed input | New display | Meaning |
| --- | --- | --- |
| input 100, cached 0 | `0 / 0.0%` | Known zero, valid denominator |
| input 0, cached 0 | `0 / —` | Known zero; no division by zero |
| input absent, cached 40 | `40 / —` | Count available; rate unknown |
| input 100, cached 40, partial turn | `40 / —` plus existing partial heading | Observed count, not a claimed full-turn hit rate |
| input 100, cached 40 | `40 / 40.0%` | Complete ordinary case |
| cached absent or greater than known input | `Not reported` | Do not invent or display contradictory statistics |

This is a rendering fix, not a change to canonical usage collection. The collector
still withholds per-turn cache totals when its observed requests lack cache
metadata; the ledger's known-zero contract and stored rows are unchanged.

No added settings, rows, buttons, dependencies, hooks or database migration.
The five frozen V1 SVGs are untouched. Retain native CardKit layout and the existing
single writer; installing an additional SDK is not a visual-renderer replacement.

## Validation and deployment

- Eight new regression cases; three failed on 0.20.5 before the fix, all pass after.
- Focused readability/layout/cache suite: 278 passed. Ruff passed.
- Full local suite: **1498 passed**; Ruff and mypy (49 source files) passed.
  Exact-revision GitHub CI and deployment remain separate gates below; source
  changes are not runtime acceptance.
- The preceding reviewed Hermes and Card update is documented in
  [0.20.5 maintenance](MAINTENANCE-0205.md).
- Desktop inspection stopped after `failed to activate captured window` recurred
  during one recovery attempt. No user message was sent and no new desktop
  pixel-equivalence or real-model-turn result is claimed.
- Existing desktop screenshots remain 0.20.4; the last real user-message model
  turn remains 0.20.2. Mobile testing is outside the user's current scope.
