"""Read-only consistency check of saved real-turn evidence, not an E2E runner.

No network, Gateway control, model calls, message sends or database writes.
Inputs are private operator-captured snapshots; stdout excludes their contents.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def audit(
    turn: dict[str, Any], history: list[dict[str, Any]], delivery: list[dict[str, Any]],
    observation: dict[str, Any], message: dict[str, Any], *,
    success_marker: str, failure_marker: str, answer: str,
) -> dict[str, Any]:
    calls = turn.get("calls", [])
    results = turn.get("results", [])
    ids = [call.get("id") for call in calls]
    result_ids = [result.get("tool_call_id") for result in results]
    completed = observation.get("counters") or {}
    checks = {
        "two_terminal_calls": len(calls) == 2 and all(
            call.get("function", {}).get("name") == "terminal" for call in calls
        ),
        "unique_matched_tool_results": len(results) == 2 and len(set(ids)) == 2
        and all(ids) and result_ids == ids,
        "expected_exit_codes": [result.get("result", {}).get("exit_code") for result in results] == [0, 7],
        "expected_stdout": len(results) == 2 and bool(success_marker) and bool(failure_marker)
        and success_marker in str(results[0].get("result", {}).get("output", ""))
        and failure_marker in str(results[1].get("result", {}).get("output", "")),
        "exact_final_answer": bool(answer) and any(
            row.get("role") == "assistant" and row.get("content") == answer for row in turn.get("rows", [])
        ),
        "completed_unique_main_usage": bool(history)
        and all(row.get("scope") == "main" and row.get("completed") == 1 for row in history)
        and all(row.get("event_key") for row in history)
        and len({row.get("event_key") for row in history}) == len(history)
        and len(history) == observation.get("history_events") == observation.get("completed_events"),
        "one_confirmed_card": len(delivery) == 1 and delivery[0].get("status") == "delivered"
        and bool(delivery[0].get("message_id")) and bool(delivery[0].get("card_id"))
        and delivery[0].get("message_id") == message.get("message_id")
        and delivery[0].get("card_id") == message.get("card_id") and message.get("type") == "interactive",
        "same_chat_and_thread_session": turn.get("session", {}).get("source") in {"feishu", "lark"}
        and bool(turn.get("session", {}).get("chat_id")) and bool(turn.get("session", {}).get("thread_id"))
        and turn.get("session", {}).get("chat_id") == message.get("chat_id"),
        "current_process_metrics": observation.get("metrics_pid_matches") is True
        and completed.get("card.completed") == 1 and completed.get("footer.turn.measured") == 1,
        "configuration_preserved": observation.get("config_unchanged") is True,
        "idle_after_turn": observation.get("state", {}).get("active_agents") == 0
        and observation.get("state", {}).get("gateway") == "running",
    }
    body = message.get("body", "")
    projection = isinstance(body, str) and all(
        value and value in body for value in (success_marker, failure_marker, answer)
    )
    return {
        "ok": all(checks.values()), "checks": checks, "snapshot_consistency_only": True,
        "desktop_visual_verified": False, "message_get_content_verified": bool(projection),
        "usage_request_count": len(history), "messages_sent": 0, "provider_calls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence_dir", type=Path)
    parser.add_argument("--success-marker", required=True)
    parser.add_argument("--failure-marker", required=True)
    parser.add_argument("--answer", required=True)
    args = parser.parse_args()
    try:
        values = [json.loads((args.evidence_dir / name).read_text(encoding="utf-8")) for name in (
            "db-turn-private.json", "new-history.json", "new-delivery.json", "observation.json", "message.json",
        )]
        report = audit(
            *values, success_marker=args.success_marker, failure_marker=args.failure_marker, answer=args.answer,
        )
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        # Private paths, prompts and transport bodies do not belong in stdout.
        report = {"ok": False, "snapshot_consistency_only": True, "error_type": type(exc).__name__}
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
