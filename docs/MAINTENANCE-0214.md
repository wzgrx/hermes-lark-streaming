# 0.20.14 — truthful tool timing from collection to final rendering

2026-10-04 (Asia/Shanghai). Keep the approved V1 native panels, dimensions,
folded defaults, eight-row selection and byte/element limits. No new dependency,
writer, hook, callback, SVG or historical-message rewrite.

## Reproductions and changes

An unmatched end callback inside an existing tracker previously synthesized
elapsed_ms=0. The renderer correctly handled explicit missing durations, but
this collector never supplied one. It now keeps None for an unobserved start/end
pair; success/error state and available output remain visible, while V1 shows
the unknown marker. A real measured zero still remains zero.
For integrations consuming display-step dictionaries, `elapsed_ms` is now
`float | None`; check for None before arithmetic. This is telemetry absence,
not a zero-duration observation. Both maintained render paths tolerate it.

Tool and tracker elapsed clocks now use time.monotonic, not time.time. Synthetic
forward/backward wall-clock jumps no longer alter measured durations. This is
limited to tool timing; stored calendar timestamps and usage-period boundaries
are unchanged. The existing timing test now patches the clock actually used.

Terminal history compaction no longer calls float(None) or treats malformed,
negative/nonfinite timing as an exact total. It sums only when every included
observation is valid, otherwise retaining an unknown total. It preserves original
steps, old errors and the recent window. Excerpt labels now accurately describe
the existing pending/error priority. No wider layout redesign is involved.

Ten focused cases include nine actual failures on unchanged 0.20.13 and a
measured-zero/complete-total compatibility guard. They cover collection through
native V1 rows as well as history compaction and both clock-jump directions.

## Research and scope

- [Native collapsible panel](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-components/containers/collapsible-panel) remains the component contract.
- Reviewed Card issues #116/#98 and PRs #114/#115/#110/#112, plus official Hermes #49334 and related delivery reports. Existing routing/sequence/media tests remain; these timing fixes do not claim every upstream report is resolved.
- Latest reviewed Hermes `9cf7960f274ea2bdfe672d64d26389e9954dfeb6` adds the process_registry/systemd command-expansion fix after `ea81748579ee`, changing only process_registry and its tests. Card/LCM hooks remain unchanged. Local tests use deployed overlay `0764e9165721`; hosted Card tests check official main separately. No core deployment in this plugin-only round.
- Current-process log inspection found no traceback, SQLite-lock warning or 300313/300309 code in the six-line startup journal. This idle snapshot is not a real long-running task acceptance.

Retirement: upstream equivalent observed/missing timing semantics plus passing
collector/history/native-row regressions. Do not relabel earlier desktop
screenshots or synthetic API probes as a new real model conversation.

## Local verification

Full isolated Card suite: **1601 passed**, two existing SDK deprecation warnings.
Ruff and mypy (49 source files) pass. Ten new cases pass, including nine failures
on 0.20.13. Exact-commit hosted CI, managed runtime and native API checks are
separate following gates, not desktop screenshots or a model conversation.
