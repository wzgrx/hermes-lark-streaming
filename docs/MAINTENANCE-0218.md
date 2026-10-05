# 0.20.18 — honest terminal availability, compact partial metrics

Date: 2026-10-05 (Asia/Shanghai).

## Reproduced UX defects

1. A completed/failed/stopped card with missing metadata still said `Model pending`
   and `Context pending`. Terminal titles now say unreported; running cards still
   use pending. One known context operand stays visible (`70.3k/—`, `—/1.0M`),
   with no made-up denominator, percentage or extra row.
2. A timed-out final history read left `Loading` or `retrying later` in a frozen
   card, even though its refresh timer had stopped. Terminal text now identifies
   the unavailable snapshot and says a later message can retry. It does not
   promise an automatic update of this already completed card. Live reads keep
   their existing status. An empty ledger remains a separate no-history state.
3. API attempts and errors were both hidden when only one was absent. Each
   operand now retains its independently known value, including zero, while an
   unknown counterpart uses `—`. Existing complete-value presentation is unchanged.

The 22 new regression cases had **17 failures before the fix**. Tests cover live
and terminal states, all three terminal outcomes, localization, partial/zero/
boolean counters, immutable input, native card budgets and expansion preservation.
Older terminal-label assertions were updated to distinguish unreported metadata
and frozen history snapshots; live pending behavior retains explicit tests.

No new panel, widget, callback, sender, dependency, telemetry estimate or database
change. Native V1 IDs, two-column metric grids, four-column tool rows and bounded
excerpts remain. This release also includes the previously reviewed smoke CLI
cleanup fix in `fed31a8`.

## Upstream research and boundaries

- Frozen official Hermes main for this review:
  `af8839df1038cc026075fb254afa22edc19d911d`.
- Card upstream main: `5eb7c2738edaf9e88322ee8c805a2163a4dd1238`;
  related project main: `72bd4939810b661cfeed5940a6da0cee09b8ab27`.
- [Upstream #116](https://github.com/Cheerwhy/hermes-lark-streaming/issues/116)
  concerns compression, steering and delivery ownership. Terminal availability
  wording does not solve or claim to solve that complete lifecycle scenario.
- [Related project #361](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/361)
  asks for clearer background waiting. Do not invent task completion/ETAs from
  missing events. [#370](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/370)
  highlights discoverability: the maintained scope distinguishes live state,
  frozen snapshots and data absent from the host/provider.
- Continue using official [collapsible panels](https://open.feishu.cn/document/feishu-cards/card-components/containers/collapsible-panel)
  and [column sets](https://open.feishu.cn/document/feishu-cards/card-components/containers/column-set).
  Dependencies do not grant custom pixel control of a native client.

Full local suites, exact-SHA CI, managed deployment, native API tests and desktop
observations are separate gates. No new client screenshot or model turn is
implied by renderer tests or by an unattached CardKit entity being accepted.

## Validation evidence

- Full suite against deployed maintained Hermes `0764e9165721`: **1672 passed**.
- Full suite against frozen official main `af8839df1038`: **1672 passed**.
- Ruff passed; mypy passed for 49 source files. Both suites retain two existing
  lark-oapi deprecation warnings; they are not suppressed as repaired findings.
- Real CardKit API accepted one unattached entity and the running, completed,
  failed and stopped availability states. Its streaming mode was closed. Zero
  chat messages, provider requests or live usage-ledger entries were created.
- Initial whole-suite verification caught two stale terminal-history assertions;
  those assertions now require frozen-snapshot wording, alongside explicit live
  loading-state regressions. The final complete reruns above both passed.
- GitHub CI and local managed deployment are subsequent exact-commit gates;
  this document does not imply a new desktop screenshot or end-to-end model turn.
