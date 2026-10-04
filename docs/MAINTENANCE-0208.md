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

## Publication and deployed runtime

Code commit: `52a3f23f43e6926c20ef7ab2485097b80bd56b30`, version **0.20.8**.
Both exact-commit GitHub workflows succeeded:
[Tests](https://github.com/wzgrx/hermes-lark-streaming/actions/runs/37169808467)
and [CodeQL](https://github.com/wzgrx/hermes-lark-streaming/actions/runs/37169808454).

The unchanged LCM source (`cfa583275ed0612cc520d11fd896908e598aa186`)
also passed **3482 tests, 6 skips, 12 expected failures** against the staged
Hermes source, in an isolated test home. LCM was not re-released or needlessly
reinstalled for this renderer change.

After two idle checks, deployment used one graceful stop/start, not a forced
termination. Live results:

- Hermes source and Gateway-reported code SHA: `919093187f1c56ebf0374f4ea924b89d7e4acd89`.
- Managed Card and runtime import: **0.20.8**, code commit `52a3f23f43e6`.
- Gateway `running`, Feishu `connected`; current-process journal has no traceback.
- Managed plugin doctors, PM doctor, Card hook verification and reinstall,
  runtime doctor checks and local smoke all pass. Generated hooks leave the
  core checkout clean.
- Existing TUI and Web build receipts are current; no redundant rebuild.
- Read-only SQLite `quick_check`: `lcm.db`, `state.db` and `card-usage.sqlite3`
  each report `ok`.
- Configuration and credential-file hashes remain unchanged. Sessions, models,
  ledger rows and PM dependency generations are retained. The update timer
  is restored to its preceding active state.

Private receipts and rollback material are retained locally, outside the public
repository. No credentials, message content or database exports are published.

Two existing runtime warnings remain explicit: metrics belong to an older
Gateway process, and one historical delivery outcome is unconfirmed. They are
not erased or automatically resent to make diagnostics appear green. No fresh
end-to-end model turn or new native-client screenshot was collected this round;
runtime health is verified, but that separate acceptance remains outstanding.
