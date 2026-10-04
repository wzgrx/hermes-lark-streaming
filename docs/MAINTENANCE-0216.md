# 0.20.16 — long-turn lifecycle retention

## Reproduced defect and bounded repair

At 128 records, `record_start` silently dropped new starts. `record_end` still
appended unmatched records, so the limit neither bounded record count nor kept
the command/timing evidence for later tools. A 160-call regression reproduces it.

Keep stable metadata for all calls: deleting or renumbering records would corrupt
segment boundaries, rollover offsets and footer counts. The internal `max_steps`
now limits **full completed payloads**, not starts. A deque trims the oldest
completed record after the budget fills. It keeps status, measured time and
512-byte command/error hints (redact before UTF-8 truncation), discarding full
output/duplicate code blocks. In-flight records remain intact. An unmatched
completion keeps unknown timing. Metadata remains O(total calls), active payloads
remain retained, and a single recent payload is not newly size-capped here.
Durable Hermes messages, tool-result files and usage history are not changed.

Two controller counters use O(1) `step_count` instead of rebuilding/sanitizing all
display steps. Rendering itself still uses the native V1 layout and existing
13KB tools-panel / 28KB whole-card budgets. No new row, control or panel.

## Upstream and limits

Reviewed official Hermes `af90026a`, deployed core `0764e916`, Card upstream
`5eb7c27`, and upstream [long-turn issue #116](https://github.com/Cheerwhy/hermes-lark-streaming/issues/116).
This fixes missing lifecycle evidence, **not proof that every delivery freeze in
#116 is solved**. Host `agent/tool_executor.py` start/end callbacks currently omit
unique call IDs. Same-name overlapping calls therefore still have ambiguous
correlation; retain compatibility rather than invent IDs or claim exact pairing.

Tests cover the 128 boundary, old successes/errors, active steps, completion
order, orphan events, zero payload budget, redaction and cheap counts. Automated
tests, native unattached CardKit probes and a real desktop conversation are
separate acceptance levels. Existing screenshots keep their original versions.

## Verification

Full suites: **1617 passed** against both deployed core and frozen official
`af90026a`, with two existing SDK deprecation warnings. Ruff and mypy pass.
Six original focused cases failed on old code; eight focused cases now pass.
Synthetic 2,000-tool comparison: known durations 128 -> 2,000; retained plain
output strings 8,200,890 -> 524,928 bytes. This is not process RSS or provider
latency. Exact-commit CI and managed-runtime/native API gates follow separately.
