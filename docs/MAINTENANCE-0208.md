# 0.20.8: compact tool summaries and current Hermes compatibility

Date: 2026-10-04. Keep the approved V1 layout and fix reproduced presentation defects.

## Hermes source snapshot

This round selects official main
[`a4648c5f58ba`](https://github.com/NousResearch/hermes-agent/commit/a4648c5f58bad6304a0034dc3405fada207ac6a4),
three commits beyond the preceding `158fd638` snapshot:

- `ddde067010`: a forced reinstall from the same plugin source preserves user-owned
  untracked files and backs up tracked edits instead of discarding them silently.
- `2c79189699`: published launchers prefer the source tree's own Python runtime
  instead of inheriting an unrelated runtime override.
- `a4648c5f58`: retain the override as a fallback when the tree records no own store.

The 30 existing local overlays rebase without conflict; `git range-diff` reports
all 30 patch-equivalent. Reviewed local result:
`919093187f1c56ebf0374f4ea924b89d7e4acd89`.
No package lock, model setting, database schema, frontend source or Card hook
change is part of this upstream delta. Freeze this reviewed snapshot rather than
chasing later main commits during deployment.

Upstream CI on this snapshot was **cancelled**, not reported green here. Local
WSL verification through the official isolated runner passed **593 tests in 14
files**, with **14 platform skips**. It covers the changed plugin installer,
launcher publication, update completion, PM survival, Gateway restart, Feishu,
queued delivery, native streaming/TTS and configured provider profile. This is
a targeted suite, not every Hermes test or native Windows acceptance.

## Card UI fixes

- A multiline tool error could add many lines and tabs to a supposedly compact
  table row. The summary now collapses whitespace before escaping/truncating,
  while the bounded folded excerpt retains useful line structure.
- A whitespace-only structured error no longer hides its actionable plain-text
  error, in either the summary or the excerpt.
- Running tools display **…** for pending timing, not an invented **0ms**.
  Observed completed zero-duration calls still show **0ms**. Failed/stopped
  turns retain 0.20.7's unconfirmed-outcome **—** contract.

No extra rows, buttons, SDK, configuration switches or provider calls. The three
panels, four tool columns, separate answer, two-column footer, native expansion
behavior and five frozen SVGs remain unchanged. Only summary whitespace changes;
tool commands, execution, results, usage counting and ledger records are untouched.

## Research and checks

- Rechecked authenticated GitHub open issues and PRs: upstream Card is still
  `5eb7c2738eda`, with five open issues and four open PRs; the maintained fork has
  no open PRs. Existing fixes for #98, #109, #111, #116 and PRs #110/#112/#114/#115
  are not reclassified as new fixes or newly reproduced remote incidents.
- Web research included the official Hermes
  [runtime/launcher documentation](https://github.com/NousResearch/hermes-agent/blob/main/apps/desktop/BUILDING.md)
  and related [Card sizing/compaction issue](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/133).
  Reports guide inspection, not proof that the same defect exists locally.
- Five new regression cases; three reproduced failures on 0.20.7 before the fix.
  Focused reference suite: **292 passed**. Full isolated Card suite: **1517 passed**.
- Ruff, mypy (49 source files), diff whitespace checks and offline wheel build pass.
  SDK deprecation warnings remain visible. CI and runtime evidence are recorded
  separately after publication below.

README screenshots keep their original **0.20.6 synthetic native desktop** label.
This round does not send a user message, perform inference, certify pixel equality
or label a code test as a real-model turn.
