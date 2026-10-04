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

## Verified publication and deployment

Reviewed/runtime code is `cf9caccb05e638087ae463dcb056dd924d9195d6`.
Exact-commit [Tests](https://github.com/wzgrx/hermes-lark-streaming/actions/runs/37176802425)
and [CodeQL](https://github.com/wzgrx/hermes-lark-streaming/actions/runs/37176802421)
passed. Tests include current official Hermes source and the maintained legacy
patch round-trip matrix, separately from local deployed-host tests.

The paired LCM target is `f31fcb3e3931b76508d005b48198497548624ddc`;
its [CI](https://github.com/wzgrx/hermes-lcm/actions/runs/37176787396) and complete
3510-pass local/low-FD release gates passed. Two idle checks preceded managed
plugin updates, PM dependency synchronization, Card hook verification and one
graceful Gateway stop/start. Plugin/PM doctors, installed version import, source
cleanliness and read-only checks of all three databases passed. Configuration
and credential-file hashes match the protected pre-update backups.

An unattached native CardKit entity accepted the configured V1 final update
after stream closure. It sent no chat message, called no model and wrote no
usage history. Selected-PM-runtime synthetic checks also exercised active-row
visibility/unknown timing and LCM whole-group budgeting/state reset in a
temporary database. These are distinct from a real Gateway model turn.

At the 2026-10-04 12:32 Asia/Shanghai audit, Gateway was running, Feishu connected
and active agents were zero; the update timer was restored. Test/release log
hashes still matched their recorded evidence. Official Hermes main remained
`ea81748579ee`, source-reviewed but not deployed; core stayed `0764e9165721`.
Idle `metrics_stale` and historical `delivery_unknown` remain visible rather
than erasing history or resending old messages. Existing 0.20.8 desktop content
was observed read-only and retains that version label. No new 0.20.12 real-turn,
desktop pixel or mobile acceptance is claimed.

This receipt and the corrected plan/roadmap are documentation-only follow-ups.
The installed tested code remains the exact commit above; documentation alone
does not require another service restart. Broader roadmap features and open
upstream reports remain independently scoped rather than declared fixed.
