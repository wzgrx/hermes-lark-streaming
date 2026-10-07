from __future__ import annotations

from hermes_lark_streaming.transport.limits import MAX_JSON_BYTES, compact_card, inspect_card


def test_card_limits_compact_large_markdown_without_mutation() -> None:
    card = {"schema": "2.0", "body": {"elements": [{"tag": "markdown", "content": "x" * 50_000}]}}
    compacted = compact_card(card)
    assert len(card["body"]["elements"][0]["content"]) == 50_000
    assert inspect_card(compacted).json_bytes <= MAX_JSON_BYTES
    assert "compacted" in compacted["body"]["elements"][0]["content"]


def test_card_limits_compact_structural_overhead_and_preserve_latest_answer() -> None:
    panels = [
        {
            "tag": "collapsible_panel",
            "expanded": False,
            "header": {"tag": "plain_text", "content": f"Tool {index}"},
            "elements": [
                {"tag": "div", "text": {"tag": "plain_text", "content": "d" * 180}},
                {"tag": "div", "text": {"tag": "lark_md", "content": "r" * 180}},
            ],
        }
        for index in range(64)
    ]
    card = {
        "schema": "2.0",
        "body": {
            "elements": [
                *panels,
                {"tag": "markdown", "content": "the newest answer must survive"},
                {"tag": "hr"},
                {"tag": "markdown", "content": "completed · model"},
            ]
        },
    }

    assert not inspect_card(card).safe
    compacted = compact_card(card)
    inspection = inspect_card(compacted)

    assert inspection.safe
    assert inspection.elements <= 200
    assert inspection.json_bytes <= MAX_JSON_BYTES
    assert "compacted" in compacted["body"]["elements"][0]["content"]
    assert any(element.get("content") == "the newest answer must survive" for element in compacted["body"]["elements"])


def test_card_limits_compact_byte_heavy_short_tool_panels() -> None:
    panels = [
        {
            "tag": "collapsible_panel",
            "expanded": False,
            "header": {"tag": "plain_text", "content": f"Tool {index}"},
            "elements": [
                {
                    "tag": "div",
                    "margin": "0px 0px 0px 22px",
                    "text": {"tag": "plain_text", "content": "x" * 170},
                    "metadata": "m" * 400,
                }
            ],
        }
        for index in range(45)
    ]
    card = {
        "schema": "2.0",
        "config": {"summary": {"content": "done"}},
        "body": {"elements": [*panels, {"tag": "markdown", "content": "final answer"}]},
    }

    assert inspect_card(card).elements < 200
    assert inspect_card(card).json_bytes > MAX_JSON_BYTES
    compacted = compact_card(card)

    assert inspect_card(compacted).safe
    assert any(element.get("content") == "final answer" for element in compacted["body"]["elements"])


def test_card_limits_tight_budget_terminates_and_keeps_latest_answer() -> None:
    card = {
        "schema": "2.0",
        "body": {
            "elements": [
                {"tag": "markdown", "content": "old " * 300},
                {"tag": "markdown", "content": "latest answer"},
                {"tag": "hr"},
                {"tag": "markdown", "content": "completed · model"},
            ]
        },
    }

    compacted = compact_card(card, max_bytes=300)

    assert inspect_card(compacted).json_bytes <= 300
    assert any(element.get("content") == "latest answer" for element in compacted["body"]["elements"])
    assert "compacted" in compacted["body"]["elements"][0]["content"]
