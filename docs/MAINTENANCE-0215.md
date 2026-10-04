# 0.20.15 — actionable tool rows and unresolved-history preservation

2026-10-04, Asia/Shanghai. A bounded maintenance change, not another layout rewrite.

## UI and behavior

Native V1 keeps its four columns, eight selected groups, default collapsed panels,
error highlighting, native i18n, one writer and byte/element gates. A successful or
running tool name now
has a short grey inline command/target hint (64 UTF-8 bytes plus ellipsis after
redaction/escaping and newline collapse). This avoids a second expansion just to
distinguish identical tool names. The existing bounded excerpt still holds longer
details. Error rows prioritize their actionable cause rather than adding another
hint. No extra row, element, button, renderer service or dependency is added.
Clients may wrap text according to their available width; no pixel identity with
historical screenshots is claimed and frozen design SVGs remain unchanged.

Terminal history compaction previously dropped old running steps and could label
that prefix successful. It now retains pending work and errors in original order,
includes a pending count and avoids fabricated success. This fixes the legacy
compacted path; V1 continues to use the original reference snapshot for exact
counts and numbering. No tool is stopped, retried or treated as cancelled.

The final eight-case suite produces six failures and two passing guards with the
old history/renderer implementations in an isolated source copy. It covers
unresolved/error ordering, nonmutation, all three row statuses, redaction/escaping,
empty hints and UTF-8 bounds. Initial fixture-import/missing-data failures are
retained separately and not counted as product regression evidence.
The additional budget regression verifies the complete localized tools panel stays inside
its existing 13 KB allocation. The first full run caught a 147-byte whole-card
overflow after adding hints; the fix measures real panel wrappers before dropping
lower-priority excerpts, rather than weakening the 28 KB whole-card limit. Error
rows keep their prior cause-first presentation so optional hints do not displace
the existing active-command plus latest-error excerpt guarantee.

## Research and compatibility

- [Official native panel contract](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-components/containers/collapsible-panel).
- Reviewed [Card #116](https://github.com/Cheerwhy/hermes-lark-streaming/issues/116),
  [#98](https://github.com/Cheerwhy/hermes-lark-streaming/issues/98),
  [#114](https://github.com/Cheerwhy/hermes-lark-streaming/pull/114) and
  [#115](https://github.com/Cheerwhy/hermes-lark-streaming/pull/115).
  Existing sequence recovery, interruption ownership and config caching remain.
- Freeze official Hermes `af90026aa09949579bd423d24def3d38f743cde0` for compatibility
  inspection. Relevant deltas cover longer-request native compaction, Home
  Assistant catalog migration, gateway liveness and approval metadata; none is a
  reason to blindly replace the user's core overlays. This round updates plugins,
  not the running core (`0764e9165721`).

No new chat message, live model turn or desktop visual acceptance is implied by
pytest or an unattached CardKit API probe. Remaining larger work includes bounded
tracker retention with exact cumulative counts and reliable identity for same-name
parallel tools. Do not silently discard steps or invent a call-id contract.

## Local verification

Full suites against deployed core and frozen official `af90026a`: **1609 passed**
each, two existing SDK deprecation warnings. Ruff and mypy (49 files) pass.
Exact-commit hosted CI and managed-runtime/native API checks are separate gates.
