# 0.20.21 — standalone final-card code pieces

Date: 2026-10-05 (Asia/Shanghai).

## Reproduction and bounded fix

The former 2400-character splitter cut raw Markdown at paragraphs/newlines and
stripped all leading newlines from the remainder. Even a short code block could
be divided when adjacent prose filled the first chunk. A long block's later
pieces lost their opener/language, so literal headings, tables or image syntax
could be rendered as prose. Source blank lines at boundaries disappeared.

Thirteen focused cases failed before this fix. Nineteen final new cases cover
LF/CRLF, backtick/tilde/indented fences, small-block atomicity, nested/inline marker
runs, unclosed long blocks, hard line cuts, literal whitespace, nonpositive limits,
pathological overhead, immutable source text and V1/cron/background builders.

The shared top-level fence scanner now feeds a final-card chunker:

- Keep a block within the character limit together, including its original delimiters.
- For an oversized block, reserve opener/closer overhead; repeat the original info
  string and close each display piece independently. Prefer existing line/paragraph
  boundaries. Keep every original body character; never strip the remainder.
- A line longer than the available budget is displayed in multiple pieces. Added
  fence delimiters and necessary display line endings are presentation scaffolding,
  not changes to the stored answer. Copying one piece is not copying the whole source.
- Wrappers exceed same-character literal marker runs in the body, so a hard split
  cannot accidentally turn an inline run into a closing fence.
- Prose splitting uses offsets and preserves separators. CRLF pairs stay together
  when the limit allows both characters. A nonpositive limit fails immediately.
- If exceptional metadata/marker overhead leaves no balanced-wrapper budget,
  retain bounded raw text losslessly. No infinite loop or silent content deletion;
  this exceptional branch does not promise independent code rendering.

Approved V1 container order, panel IDs/expansion, four-column tool rows, two-column
metrics and the 2400-character strategy remain intact. No new parser dependency,
provider call, hook, routing, database schema or conversation update.

## Current-source research

- Fork base: `be096bdfc5509d391a5033741eef3199cf61b5d8`.
- Official Hermes main test snapshot:
  `765342435609cc82cbeb3d664dcec24126da58ee`; not a core deployment.
- Reviewed the official [Markdown chunk helpers](https://github.com/NousResearch/hermes-agent/blob/765342435609cc82cbeb3d664dcec24126da58ee/gateway/platforms/helpers.py),
  which separate atomic blocks and independently balanced display chunks. This
  plugin uses its own lightweight scanner, preserving tilde/longer-fence support
  without relying on a new private Hermes helper or naive triple-backtick toggles.
- Card upstream remains `5eb7c2738edaf9e88322ee8c805a2163a4dd1238`;
  related main remains `72bd4939810b661cfeed5940a6da0cee09b8ab27`.
  Current open issue/PR heads were checked; no unrelated PR was blindly merged.
- The official [CardKit Markdown component](https://open.feishu.cn/document/feishu-cards/card-json-v2-components/content-components/rich-text)
  page was reached, but its dynamic document body was not extractable. Native
  API acceptance is checked separately, not inferred from that empty extraction.

## Explicit remaining scope

This changes the shared final-card builder path, not the live stream callback or
CardKit rollover state machine. Total-card compaction at the existing 28 KB JSON
budget can still shorten/remove content; that is a separate policy, not solved
by balanced 2400-character pieces. Inline/indented/container Markdown parsing is
outside this fence scanner. Native service acceptance is distinct from a new
desktop screenshot, pixel comparison or full Hermes/Feishu message round trip.

Local test matrices, exact-commit CI, reviewed-source and managed-runtime API
probes, deployment and rollback receipts are separate publication gates.

## Verified pre-publication gates

- Maintained deployed Hermes `0764e9165721`: **1740 passed**.
- Frozen official Hermes main `765342435609`: **1740 passed**.
- Ruff passed and mypy passed for 49 source files. The two existing lark-oapi
  deprecation warnings remain visible, not relabeled as fixed.
- A real unattached CardKit entity accepted the V1 live structure, unclosed and
  closed streaming content, and three final large-fence variants. Each final
  variant contained two independently fenced pieces within 2400 characters;
  reconstructing their fixture bodies matched the original body exactly.
- Streaming was closed, literal image uploads were zero, and no chat message,
  provider/model call or live usage-ledger record was produced by the probe.
  Exact-commit CI and managed-source acceptance remain separate receipts.
