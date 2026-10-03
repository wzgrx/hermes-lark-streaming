"""Build deterministic documentation diagrams, not Feishu client screenshots.

Run with the project's development interpreter from the repository root.
Uses synthetic data only; never reads Hermes config, credentials or history.
"""
# ruff: noqa: RUF001
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hermes_lark_streaming.footer.render import build_footer  # noqa: E402

ASSETS = ROOT / "docs/assets"
ASSETS.mkdir(parents=True, exist_ok=True)


def text(x: int, y: int, value: str, size: int = 18, color: str = "#18273e", bold: bool = False) -> str:
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
            f'font-weight="{700 if bold else 400}">{html.escape(value)}</text>')


def box(x: int, y: int, width: int, height: int, fill: str = "white", stroke: str = "#d5e0ec") -> str:
    return f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="12" fill="{fill}" stroke="{stroke}"/>'


def svg(width: int, height: int, content: str, title: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">'
            f'<title>{html.escape(title)}</title><style>text{{font-family:Arial,"Microsoft YaHei",sans-serif}}</style>'
            f'<rect width="100%" height="100%" fill="#f4f7fc"/>{content}</svg>\n')


def plain(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value).replace("**", "").replace("\\", "")


def main() -> None:
    overview = text(40, 55, "HERMES / LARK STREAMING", 29, bold=True)
    overview += text(40, 88, "Native cards + per-turn telemetry + local usage history", 19, "#536781")
    for x, title, lines in [
        (40, "Hermes", ["Model / provider / tools", "Public lifecycle hooks"]),
        (400, "Card plugin", ["One delivery owner", "Streaming + Footer V2"]),
        (760, "Feishu client", ["Card JSON 2.0", "Native layout / interaction"]),
    ]:
        overview += box(x, 125, 320, 132)
        overview += text(x + 22, 161, title, 23, "#2464d2", True)
        for i, line in enumerate(lines):
            overview += text(x + 22, 200 + i * 28, line, 18)
    for x in (366, 726):
        overview += text(x, 202, "→", 28, "#2464d2")
    overview += box(400, 291, 320, 110, "#edf3ff")
    overview += text(423, 326, "Local SQLite ledger", 22, "#2464d2", True)
    overview += text(423, 360, "Month / provider / model", 18)
    overview += text(423, 387, "CLI + JSON • no account polling", 16)
    overview += text(551, 286, "↓", 24, "#2464d2")
    overview += text(40, 453, "SDK / CLI send data. The client renders it. Concept art is not runtime proof.", 18)
    (ASSETS / "runtime-overview.svg").write_text(
        svg(1120, 490, overview, "Hermes Card runtime architecture"), encoding="utf-8"
    )

    fixture = {
        "duration": 18.6, "provider": "example-provider", "requested_model": "example-model",
        "response_model": "example-model", "model": "example-model", "reasoning": "max",
        "api_mode": "chat_completions", "input_tokens": 12000, "output_tokens": 420,
        "cache_read_tokens": 9000, "context_used": 8000, "context_max": 128000,
        "api_calls": 2, "tool_calls": 1, "retries": 0, "first_response": 1.2,
        "routes": ["example-provider"],
    }
    elements = build_footer(fixture, text_size="normal")
    card = {"schema": "2.0", "body": {"elements": elements}}
    (ASSETS / "footer-example.json").write_text(
        json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    drawing = text(32, 47, "COMPACT TURN DETAILS", 27, bold=True)
    drawing += text(32, 77, "Code-derived schematic • synthetic values • NOT a Feishu screenshot", 17, "#536781")
    drawing += box(24, 104, 1072, 456)
    drawing += text(48, 142, "正文保持原样 / Answer stays unchanged", 23, bold=True)
    for i, element in enumerate(elements[1:3]):
        drawing += text(52, 182 + i * 32, plain(element["i18n_content"]["zh_cn"]), 18)
    drawing += text(52, 262, "▼ 本轮详情（默认折叠 / Collapsed by default）", 20, "#2464d2", True)
    y = 293
    for element in elements[-1]["elements"]:
        if element["tag"] == "column_set":
            for i, cell in enumerate(element["columns"]):
                label = plain(cell["elements"][0]["i18n_content"]["zh_cn"])
                drawing += text(52 + i * 510, y, label, 18)
        else:
            label = plain(element["i18n_content"]["zh_cn"])
            for line in label.splitlines():
                drawing += text(52, y, line, 17, "#536781")
                y += 27
            continue
        y += 27
    drawing += text(32, 602, "Four two-cell metric rows. Same model ID appears once; differences remain explicit.", 17)
    (ASSETS / "footer-current-structure.svg").write_text(
        svg(1120, 632, drawing, "Compact footer schematic, not a client screenshot"), encoding="utf-8"
    )
    print("Built 2 SVG diagrams and 1 synthetic Card JSON fixture; no live config read.")


if __name__ == "__main__":
    main()
