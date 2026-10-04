# 0.20.17 — terminal truth and excerpt redaction

Date: 2026-10-05 (Asia/Shanghai).

## Defects and behavior

1. A successful answer is not proof that every started tool returned. Previously
   only aborted/failed cards rendered missing results as unconfirmed. A normal
   terminal card could retain blue Running rows forever while its footer claimed
   answer completion. All terminal V1 builds now show missing results as orange
   Unconfirmed / 结果未确认, an unknown duration and a short explanation. Headers,
   rows, bounded excerpts and footer agree. Completed/failed counts are not changed;
   no tool is cancelled or invented as successful. Live streaming remains blue.
   Rollover sealing explicitly identifies a continuing turn and preserves the
   observed running snapshot; closing a CardKit entity is not turn completion.
2. Truncating text before redacting quoted values removes the closing quote and
   can prevent matching of sensitive assignments, flags and JSON fields. Redact
   the full supplied string first, then retain the existing escaped UTF-8 byte
   limit. Tests use synthetic canaries, never real credentials. This protects
   well-formed source values; already-malformed or pre-truncated input is not
   magically reconstructed, and arbitrary secrets without recognizable keys are
   not claimed to be detected.

Native four-column geometry, nested expansion IDs, 8-row priorities, 24-excerpt
limit and whole-card size/element checks stay unchanged. There are no extra
senders, callback streams, dependencies, hooks or schema/ledger changes.

## Research and scope

- GitHub main snapshots reviewed: Hermes `af90026aa09949579bd423d24def3d38f743cde0`,
  Card upstream `5eb7c2738edaf9e88322ee8c805a2163a4dd1238` and LCM upstream
  `8d1b1e6d3d63f5fc7b209e8d7ec1dc9b814f2e54`.
- [Upstream #116](https://github.com/Cheerwhy/hermes-lark-streaming/issues/116)
  concerns compression/steering/delivery ownership, not merely a stale label.
  This maintenance does not claim to solve that entire scenario or guess tool
  identities from completion order. Existing lifecycle/rollover tests remain gates.
- Keep [native panels](https://open.feishu.cn/document/feishu-cards/card-components/containers/collapsible-panel)
  and [column sets](https://open.feishu.cn/document/feishu-cards/card-components/containers/column-set).
  An SDK/environment change is not a layout fix. Frozen V1 illustrations are not
  regenerated as evidence of a new desktop observation.

## Verification

New focused tests: 23 cases; the original 19 cases failed 15 on old code. Full suites against
deployed and frozen latest Hermes, Ruff/mypy, exact-SHA CI, managed deployment and
runtime/API probes are recorded separately. No new real Feishu message or desktop
visual result is implied by a local or native API success.

### Automated gate results

Both deployed-core and frozen official `af90026aa` suites pass: **1640 passed**
each, with two existing SDK deprecation warnings. Ruff and mypy (49 source files)
pass. Full regression caught the rollover/terminal distinction: the real seal
caller now supplies an explicit continuation flag and an integration test keeps
the original live-status invariant. One exploratory version-mismatch failure
came from changing metadata while tests were running; the final frozen-source
suites were rerun after all source/version changes. No assertions were dropped.
