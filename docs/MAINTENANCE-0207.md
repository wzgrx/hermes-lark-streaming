# 0.20.7: truthful stopped/failed cards

Date: 2026-10-04. Bounded maintenance of the existing V1 UI, not a redesign.

## Source review

- Authenticated GitHub main check: official Hermes
  [`158fd638da16`](https://github.com/NousResearch/hermes-agent/commit/158fd638da1629c8e62caf9ade1515d162def8ab).
  The installed `194501c6` already includes it plus 30 reviewed local overlays.
  There was no newer core revision at this check; no second core rebase is needed.
- Upstream Card remains `5eb7c2738eda`. Reviewed its five open issues and four
  open PR titles, plus the full reports for
  [#98](https://github.com/Cheerwhy/hermes-lark-streaming/issues/98),
  [#116](https://github.com/Cheerwhy/hermes-lark-streaming/issues/116) and
  [PR #114](https://github.com/Cheerwhy/hermes-lark-streaming/pull/114).
  Existing fork sequence/reseed, queued-follow-up and compression-observer
  protections remain; this release does not claim a new reproduction or closure
  of those remote incidents. The user's fork has no open PRs; Issues are disabled.
- Searched official Hermes Feishu reports and the official
  [Card 2.0 collapsible-panel documentation](https://open.feishu.cn/document/feishu-cards/card-json-v2-components/containers/collapsible-panel).
  The documentation page is client-rendered and the text fetch returned no body;
  it is not treated as newly verified field-level evidence. No speculative SDK,
  CSS or card-schema changes follow from that search.

## Reproduced UI defects and changes

1. A failed/stopped turn could leave unfinished tool rows and excerpts saying
   **Running**, even though the card had been finalized. V1 now says
   **Unconfirmed / 结果未确认**, including a compact count in the folded header
   and footer. Unobserved completion time is `—`, not `0ms`.
2. An empty failed/stopped answer previously rendered **Done.**, especially
   misleading with the footer and header disabled. It now uses the localized
   error/stopped status; an otherwise empty chat-list preview gets the same status.
3. The tool panel explicitly distinguishes a terminated turn from a terminated
   operating-system process. No process is killed, and no success/failure result
   is invented. Observed counts and tracker data remain untouched.

Existing answer text and its notification preview are preserved. Live cards and
sealed continuation cards retain their genuine running indicators. The normal
success layout, three native panels, answer placement, single writer and five
frozen SVG baselines stay unchanged. Long interrupted excerpts reserve another
512 bytes for localized terminal metadata rather than overflowing the existing
28 KB defensive card budget.

## Validation

- Fourteen new tests: eight reproduced the two old display defects before the
  code fix. Coverage includes footer off, both terminal states, reference/classic
  empty answers, preserved partial answers, live/continuation non-regression,
  two mocked controller close/update integrations and worst-case card budgets.
- Focused reference suite: **287 passed**.
- Full isolated suite: **1512 passed**, with explicit reviewed modular and pinned
  legacy Hermes sources; Ruff and mypy (49 source files) pass.
- Offline wheel built using the cached uv build backend. No runtime dependency,
  schema, credential, model setting or hook changes.
- SDK test-environment deprecation warnings remain recorded. These are distinct
  from failed checks; they are not suppressed to make the output look cleaner.

CI publication and managed deployment are recorded below after completion.
The existing README screenshots are explicitly **0.20.6 synthetic native desktop
captures**. They are not relabelled as 0.20.7. New real-model-turn acceptance and
literal equality to the frozen SVGs remain unverified. This renderer-only change
does not send any user message or perform inference.
