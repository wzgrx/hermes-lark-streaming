# 0.20.12 — live tool visibility and honest timing

Date: 2026-10-04 (Asia/Shanghai). Preserve the approved native V1 desktop
geometry, folded defaults and single CardKit writer. No new panels or dependencies.

## Reproduced and fixed

A long-running step followed by twelve failed steps disappeared from the
eight-row summary. Selection now prioritizes running/unconfirmed groups, then
recent failed groups, then other recent groups. The selected rows remain in
original chronological order; counters still include omitted/prior steps. A
live title includes the total running count. Stopped/error cards retain the
existing unconfirmed label instead of claiming a background process stopped.

Missing, negative and nonfinite finished timings previously became `0ms`.
They now show `—`. A merged poll group with one unknown duration stays unknown,
not a falsely exact sum. Observed zero stays `0ms`; live tools keep `…`.

Nine targeted tests include eight pre-fix failures and an observed-zero guard.
The many-live-steps case validates the full native builder's serialized limits.
No user messages, screenshots, SVG designs, ledger records or tracker observations
are rewritten. Tests are synthetic native JSON, not a new Feishu model turn.

## Upstream and compatibility review

- [Official collapsible panel](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-components/containers/collapsible-panel): use existing native panel/grid components rather than a raster imitation.
- [Card #116](https://github.com/Cheerwhy/hermes-lark-streaming/issues/116) and PRs 114/115/110/112 remain open upstream. Existing interruption/media/single-writer/config-cache regressions remain in the full suite; this change does not claim a new fix for every open report.
- Official Hermes snapshot `ea81748579ee1732d214ccb75f91d22208ed623d` changes local model/PM and WhatsApp paths beyond the deployed base `24b9f0f8c5df`. It does not change Card's gateway/cron hook files. Local tests use deployed overlay `0764e9165721`; hosted Card tests separately check official main. Core deployment is not part of this plugin-only update.

Retire these guards only when upstream has equivalent selection/timing behavior
and the maintained tests still pass. Exact full-suite, CI and deployment results
are recorded after those gates; historical desktop evidence is not relabelled.

## Local verification

Full isolated Card suite: **1583 passed**, two existing SDK deprecation warnings.
Ruff and mypy (49 source files) pass. Nine new targeted cases pass; eight were
red on 0.20.11 before implementation. Exact-commit hosted CI and managed
deployment remain separate gates; this document makes no new real-turn claim.

## Smoke coverage correction before deployment

Further review found `_configured_final` respected enhanced Footer settings but
omitted `presentation`, so even a reference-layout host tested the classic final
card. It now forwards the selected layout and tool/resource panel toggles, with
no fabricated host readings, usage or history. Two new regressions failed before
this correction and pass afterwards; the existing classic smoke test still
passes. The closed-stream probe creates one unattached entity, exercises the
configured final update and closes its stream; it sends no chat message.

Final full-suite and service-probe evidence is recorded separately from the
initial 1583-test pass above. An unattached server-accepted card is not a client
screenshot or a real model/Gateway round trip.

Final isolated suite after the smoke correction: **1585 passed**, two existing SDK warnings; Ruff and mypy pass. No new dependency or hook change.
