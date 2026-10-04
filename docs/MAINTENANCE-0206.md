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
- The initial desktop attempt stopped after `failed to activate captured window` recurred
  during one recovery attempt. No user message was sent and no new desktop
  pixel-equivalence or real-model-turn result is claimed.
- At that initial gate the screenshots were still 0.20.4. The follow-up below
  supersedes only that visual evidence, not the last real user-message model
  turn (0.20.2). Mobile testing is outside the user's current scope.

## Published and deployed

- Exact code: `d2dd2dbf5521da7ecc37c2f2bb67c6a3ca191e4d` on the user's main.
  [Tests](https://github.com/wzgrx/hermes-lark-streaming/actions/runs/37166273027)
  and [CodeQL](https://github.com/wzgrx/hermes-lark-streaming/actions/runs/37166272978)
  passed. The 1498-test full suite was also rerun in a disposable Hermes home
  against the reviewed modular core; legacy source remains checksum-pinned.
- Offline wheel built through uv's isolated cached build environment. The lean
  test interpreter lacks pip/setuptools; it was not modified to build the wheel.
- Two live idle checks preceded graceful stop and an exact-SHA managed plugin
  update. PM synchronization, both plugin doctors, Card hook round trip, PM
  doctor, Card doctor and dry smoke passed. No additional dependency consent or
  provider configuration changes were required.
- PM runtime imports **0.20.6**. Gateway running, Feishu connected; update timer
  restored. Config and credential files are byte-identical; core remains
  `194501c6a78574f6ea7498ae7661dc78ec36276c` (official `158fd638` plus overlays),
  LCM remains `cfa583275ed0612cc520d11fd896908e598aa186`, all live Git trees clean.
- Read-only `quick_check` passes for LCM, sessions and Card usage databases.
  Current-process journal has no traceback. No stored rows were rewritten or
  removed. Prior database backups and PM generations were retained.
- `metrics_stale` and historical `delivery_unknown` remain visible; no new model
  turn was sent to clear warnings cosmetically. The unrelated upstream Telegram
  regression and optional unconfigured core tools stay recorded in 0.20.5.

Private deployment/rollback receipt:
`/home/wzgrx/.hermes/state/card-reviewed-deploy-20261004-0206.json`.
This finishes this maintenance change's code and deployment work. New real-turn
and literal desktop pixel-equality acceptance remain unverified, not silently
promoted by the successful service checks. Do not add unrelated features while
those independent acceptance steps await their prerequisites.

## Desktop follow-up

The native Feishu window became operable again on 2026-10-04. Updated only the
existing bot-owned synthetic preview with the current renderer's generated JSON;
no new chat message, inference request, runtime restart or real-reply edit.
Independently expanded tools/resources/footer and captured cropped native images.
The new priority-excerpt label is visible; all three panels collapse, the answer
stays independent, and the grouped local history is readable.

See [the current desktop evidence](DESKTOP-0206-ACCEPTANCE.md) and its machine-readable
receipt. This advances client-rendering acceptance. Literal equality to the
frozen SVG and a new real-model turn are still separate, unverified requirements.
