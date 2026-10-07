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
from hermes_lark_streaming.footer.runtime import build_runtime_footer  # noqa: E402

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
    drawing += box(24, 104, 1072, 515)
    drawing += text(48, 142, "正文保持原样 / Answer stays unchanged", 23, bold=True)
    for i, element in enumerate(elements[1:3]):
        drawing += text(52, 182 + i * 32, plain(element["i18n_content"]["zh_cn"]), 18)
    drawing += box(48, 235, 1024, 44, stroke="#b8bdc5")
    drawing += text(60, 263, "💾 后台复盘", 16, "#646a73")
    drawing += text(1044, 263, "⌄", 18, "#646a73")
    drawing += box(48, 291, 1024, 306, stroke="#b8bdc5")
    title = elements[-1]["header"]["title"]["i18n_content"]["zh_cn"]
    drawing += text(60, 319, title, 16, "#646a73")
    drawing += text(1044, 319, "⌃", 18, "#646a73")
    y = 351
    for element in elements[-1]["elements"]:
        if element["tag"] == "column_set":
            for i, cell in enumerate(element["columns"]):
                label = plain(cell["elements"][0]["i18n_content"]["zh_cn"])
                drawing += text(60 + i * 505, y, label, 18)
        else:
            label = plain(element["i18n_content"]["zh_cn"])
            for line in label.splitlines():
                drawing += text(60, y, line, 17, "#536781")
                y += 27
            continue
        y += 27
    drawing += text(32, 656, "Same native panel chrome as background review; both collapsed by default.", 17)
    (ASSETS / "footer-current-structure.svg").write_text(
        svg(1120, 686, drawing, "Shared native panel schematic, not a client screenshot"), encoding="utf-8"
    )
    live = text(32, 47, "运行中的 Footer · 原生紧凑布局", 28, bold=True)
    live += text(32, 78, "代码对应结构示意 / 合成数据 / 非飞书客户端截图", 17, "#536781")
    phases = [("answer", "01  正在回答"), ("tool", "02  执行工具"),
              ("compression", "03  整理上下文"), ("waiting", "04  等待确认"),
              ("provider_switch", "05  服务商切换"), ("failed", "06  本轮失败")]
    for i, (phase, heading) in enumerate(phases):
        x, y = 28 + (i % 2) * 554, 115 + (i // 2) * 226
        live += text(x + 5, y, heading, 21, bold=True)
        live += box(x, y + 17, 532, 177)
        data = {"runtime_phase": phase, "duration": 28, "runtime_tool": "terminal",
                "runtime_tools_done": 2, "runtime_route": ("Provider A", "Provider B"),
                "telemetry_missing": True, "compression_observed": phase == "compression"}
        if phase == "failed":
            node = build_footer(data, is_error=True)[1]
            lines = [plain(node["i18n_content"]["zh_cn"]), "正文和已有工具记录保持可见"]
        else:
            node = build_runtime_footer(data)[0]
            lines = plain(node["i18n_content"]["zh_cn"]).splitlines()
        color = "#d92d20" if phase == "failed" else "#bb7400" if i > 1 else "#2464d2"
        live += text(x + 20, y + 57, lines[0], 20, color, True)
        live += text(x + 20, y + 91, lines[1], 17, "#536781")
        live += box(x + 16, y + 122, 500, 48, stroke="#b8bdc5")
        live += text(x + 24, y + 153, "📊 本轮详情", 16, "#646a73")
        live += text(x + 490, y + 153, "⌄", 18, "#646a73")
    live += text(32, 817, "真实事件驱动；摘要返回不等于压缩提交，单次请求错误不等于本轮最终失败。", 18)
    (ASSETS / "footer-runtime-states.svg").write_text(
        svg(1120, 850, live, "Native runtime footer states, synthetic code-derived schematic"), encoding="utf-8"
    )
    print("Built 3 SVG diagrams and 1 synthetic Card JSON fixture; no live config read.")


if __name__ == "__main__":
    main()
