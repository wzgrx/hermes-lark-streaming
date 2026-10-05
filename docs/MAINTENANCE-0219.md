# 0.20.19 — preserve tool-result evidence across card handoffs

Date: 2026-10-05 (Asia/Shanghai).

## Reproduced problem and bounded fix

Clarify/approval handoffs retain cumulative `tool_calls_prior`, `tools_done_prior`
and `tools_failed_prior`, then replace the active display tracker. The V1 tools
panel and terminal Footer counted missing results only in that new tracker.
An earlier card's unresolved steps disappeared from the unconfirmed count,
although its total remained in the denominator. Repeated handoffs compounded it.

Use the already recorded nonnegative difference `tools_prior - done_prior` for
earlier-card missing results, plus current running steps only when rendering a
terminal count. While live, archived unknown results and active running steps
are independent labels. Neither is converted to success, failure or proof of a
background process stopping. No tracker state or late-callback ownership changes.

Example: earlier cards contain three steps (one success, one failure, one missing
result), and the current card has one running step:

| State | V1 tools summary | Terminal Footer |
|---|---|---|
| Live | `2/4 结束 · 1 失败 · 1 结果未确认 · 1 运行中` | Still the live runtime status |
| Completed, failed or stopped | `2/4 结束 · 1 失败 · 2 结果未确认` | `1 成功 / 1 失败 / 2 结果未确认` |

The existing earlier-card line also says `前卡 3 步（1 结果未确认）` so the origin
of the gap is visible without adding another panel or metric grid. The four-column
tool rows, two-column metrics, element IDs and expansion-preserving update path
stay unchanged. Missing results do not reopen old cards or trigger tool retries.

## Research scope

- User fork base: `0ccd0d9b30701db46fbae1606535e8bfdab3cef1`.
- Official Hermes main frozen for compatibility checks:
  `15cf1417e4c53ebea9d415abb5bcd6af8b1577d3`. This is a test snapshot, not a core deployment.
- Card upstream main remains `5eb7c2738edaf9e88322ee8c805a2163a4dd1238`.
  [#116](https://github.com/Cheerwhy/hermes-lark-streaming/issues/116) concerns
  compaction, steering and delivery ownership; this count fix does not resolve
  or claim to resolve that whole scenario. Open PR titles and heads were checked,
  not blindly merged into this different maintained branch.
- Related project main: `72bd4939810b661cfeed5940a6da0cee09b8ab27`.
  [#359](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/359) reports
  completed-answer loss across a queued-followup reset in its sidecar architecture.
  It motivates careful handoff review but is a different bug, not an upstream fix
  imported by this release. This plugin retains its existing single-writer owner.
- Native [collapsible panels](https://open.feishu.cn/document/feishu-cards/card-components/containers/collapsible-panel)
  remain the V1 container; no new rendering dependency or custom CSS layer.

## Regression scope

16 new cases cover normal completion, failure, stop, live prior/current separation,
zero/complete counts, nonnegative archived gaps, repeated real controller handoff
methods with mocked transport, new-turn isolation, immutable renderer input and
the existing 13 KB tools-panel budget. Mocked transport exercises code logic; it
is distinct from CardKit API acceptance and from a real Gateway/client turn.

No dependency, credential, model, database schema, core-hook or LCM change.
Full local suites, exact-commit CI, managed deployment and native API acceptance
are separate gates. Historical screenshots and deliveries are not new-version
desktop acceptance evidence.

## Verified pre-publication gates

- Deployed maintained Hermes `0764e9165721`: **1688 passed**.
- Frozen official Hermes main `15cf1417e4c5`: **1688 passed**.
- Ruff passed; mypy passed for 49 source files. Two existing lark-oapi deprecation
  warnings remain visible, not hidden as fixed issues.
- A real unattached CardKit entity accepted live, completed, failed and stopped
  handoff-count payloads. Streaming was closed; zero chat messages, model/provider
  calls or live usage-ledger records were produced by this probe.
- Exact-commit CI and managed runtime publication are recorded separately; the
  API probe is not a desktop screenshot or a full Hermes message round trip.
