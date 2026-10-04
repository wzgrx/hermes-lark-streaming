# 0.20.13 — useful expanded details, truthful poll grouping

Date: 2026-10-04 (Asia/Shanghai). Keep the approved native V1 desktop layout,
folded defaults and single CardKit writer. No SVG, screenshot, schema, callback,
hook, dependency or usage-ledger changes.

## Reproduced and fixed

0.20.12 kept an old running row visible during a burst of errors, but its
command could still disappear from the expanded excerpt when the byte limit
was reached. Excerpt eviction now follows row priority: ordinary records first,
then selected failures, then selected live/unconfirmed records. Within a class,
older records are dropped first; surviving content remains chronological.
The eight-row/24-excerpt and serialized-size limits remain authoritative, so
extremely many active commands are still bounded rather than all guaranteed.

Process polls with empty or whitespace-only details are separate observations,
not proof of one target. They no longer merge. Known nonblank matching details
retain existing grouping, success/output guards and truthful total counts.
This is a conservative absent-target guard, not new durable job-ID tracking.

Six focused tests include five failures against unchanged 0.20.12 and one
known-target compatibility guard. Live/interrupted cases use long Chinese
errors and validate the complete native card payload as well as excerpt order.
These are deterministic renderer tests, not a real Feishu model conversation.

## Upstream and compatibility

- [Native collapsible panel](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-components/containers/collapsible-panel): keep actual native components, not rasterized imitation.
- [Card issue #116](https://github.com/Cheerwhy/hermes-lark-streaming/issues/116), [recovery PR #114](https://github.com/Cheerwhy/hermes-lark-streaming/pull/114), and config/media PRs 115/110/112 were reviewed alongside retained lifecycle regressions. These excerpt fixes do not claim to close every upstream report.
- Latest reviewed official Hermes snapshot is `ea81748579ee1732d214ccb75f91d22208ed623d`. New local-model/PM and WhatsApp changes beyond deployed base `24b9f0f8c5df` do not change these Card gateway/cron hooks. Local tests target deployed overlay `0764e9165721`; hosted tests separately check official main. This is a plugin-only deployment, not a new core release.

Retirement: remove the local guards only when upstream implements equivalent
excerpt priority and missing-target behavior and these regressions still pass.
Exact-commit CI, managed deployment and native acceptance are distinct gates.
Historical desktop screenshots keep their original tested version.

## Local verification

Full isolated Card suite: **1591 passed**, two existing SDK deprecation warnings.
Ruff and mypy (49 source files) pass. Six focused cases pass, including five
pre-fix failures and a known-target grouping guard. Exact-commit hosted CI,
managed runtime and native API checks are separate following gates.
