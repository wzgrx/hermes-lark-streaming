# 0.20.20 — literal fenced examples, compact prose

Date: 2026-10-05 (Asia/Shanghai).

## Reproduced problems

The old formatter extracted closed backtick blocks with string placeholders,
restored them, then compressed blank lines and stripped non-`img_` images across
the whole answer. Literal examples lost their images and blank lines. Literal
`___CB_0___` strings could also collide with generated placeholders. Headings
inside code could incorrectly trigger normalization of unrelated prose headings.

The image resolver independently scanned the entire answer: an example image URL
could start a real upload, disappear while pending or change into a cached key.
The table counter recognized only naive closed triple-backtick spans, so tilde,
longer and unfinished fences could consume the table quota or get extra fences.

Twenty focused cases failed against the previous code before these fixes; the
final new regression module contains 33 cases, including additional boundary tests.

## Bounded implementation and visible behavior

A shared, dependency-free fence scanner returns original offsets. Prose-only
mapping protects top-level backtick/tilde fences of at least three characters,
0–3 spaces of indentation, same-type closers of at least the opener length, and
unclosed streaming fences through the end of input. LF and CRLF are preserved.
Short, mixed-type, overindented or text-suffixed lines do not close a block.
No placeholder substitution, database write or network operation occurs in this
scanner. Existing real-image uploads and cache reuse still work outside code.

| Location | Result |
|---|---|
| Prose headings / extra blank lines | Existing compact CardKit normalization |
| Fenced heading / blank lines | Original text retained |
| Fenced `![example](https://...)` | Literal example; zero image-upload work |
| Prose image URL | Existing upload/cache path |
| Fenced Markdown table | Literal example; no native table quota consumed |
| Prose table beyond quota | Existing readable fenced-table downgrade |

The approved V1 tools → resources → answer → model/history → identity order,
native expansion IDs, four-column tool rows, two-column metric grids and budget
gates stay unchanged. This improves code readability and copy fidelity, not the
client's font, syntax highlighting or pixel rendering.

## Research and compatibility scope

- User fork base: `a5147adbd6857863f86a82504183851b970e3ba9`.
- Official Hermes main frozen for compatibility:
  `765342435609cc82cbeb3d664dcec24126da58ee`. Test snapshot only, not a core upgrade.
- Card upstream main: `5eb7c2738edaf9e88322ee8c805a2163a4dd1238`;
  related project main: `72bd4939810b661cfeed5940a6da0cee09b8ab27`.
- Reviewed current upstream open issue/PR heads, including
  [anchor/sequence recovery #114](https://github.com/Cheerwhy/hermes-lark-streaming/pull/114)
  and [compaction/steering freeze #116](https://github.com/Cheerwhy/hermes-lark-streaming/issues/116).
  This Markdown fix neither imports unrelated PRs nor claims to close #116.
- Fence decisions follow the relevant
  [CommonMark 0.31.2 rules](https://spec.commonmark.org/0.31.2/#fenced-code-blocks).
  This lightweight scanner is not a complete CommonMark parser: inline code,
  indented code and list/quote-container parsing are outside this change.

## Acceptance boundary

Regression cases cover literal bytes, placeholder collisions, heading-gate
isolation, table offsets/quota, cached and awaited image resolution, LF/CRLF,
invalid fences, non-Markdown Unicode separators, multiple blocks and final V1
card JSON safety. Full suites against deployed Hermes and the frozen official
snapshot, exact-commit CI, managed deployment and CardKit service acceptance are
separate gates; their receipts are recorded during publication.

Long-answer chunking retains its existing 2400-character policy. This release
does not claim to repair code fences split across chunks, render every Markdown
dialect, or certify a new desktop screenshot. Historical screenshots stay tied
to their tested versions. No dependency, credential, model, core/LCM source,
usage schema or conversation-history change.

## Verified pre-publication gates

- Deployed maintained Hermes `0764e9165721`: **1721 passed**.
- Frozen official Hermes main `765342435609`: **1721 passed**.
- Ruff passed; mypy passed for 49 source files. Two existing lark-oapi deprecation
  warnings remain visible and are not reported as fixed by this release.
- One real unattached CardKit entity accepted the native V1 live structure,
  unclosed/closed streaming content and three final fence variants. Original code
  text matched in all asserted payloads; literal image uploads were zero.
- Streaming was closed; zero chat messages, provider/model calls and live usage
  records were produced by this probe. Service acceptance is not desktop visual
  or full Hermes turn acceptance. CI and managed runtime receipts follow separately.
