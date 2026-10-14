from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from hermes_lark_streaming.details.ledger import UsageLedger
from hermes_lark_streaming.report import build_card, usage_section


def test_daily_report_splits_usage_by_subscription(tmp_path):
    ledger = UsageLedger(tmp_path / "u.sqlite3")
    now = time.time()
    for i, (provider, tokens) in enumerate((("opendesign", 300_000_000), ("opencode-go", 40_000))):
        ledger.record("post_api_request", {
            "session_id": "s", "turn_id": f"t{i}", "api_request_id": f"r{i}", "started_at": now - 60,
            "provider": provider, "model": "m", "usage": {"input_tokens": tokens, "output_tokens": 0},
        })
    when = datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai"))
    section = usage_section(ledger, "Asia/Shanghai", "day", when)
    today = [(m.label, m.value) for m in section.metrics if m.group == "今日"]
    assert today == [("OpenDesign", "300M"), ("Go", "40k"), ("请求", "2")]
    card = build_card([section], period="day", now=when)
    text = str(card)
    assert "📊 用量日报" in text and "'expanded': True" in text and "本月" in text
