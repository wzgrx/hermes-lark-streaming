"""Evidence checker fixtures are synthetic, not real E2E acceptance."""

from copy import deepcopy

import pytest

from scripts.audit_real_gateway import audit


def fixtures():
    calls = [{"id": str(i), "function": {"name": "terminal"}} for i in (1, 2)]
    turn = {"calls": calls, "results": [
        {"tool_call_id": "1", "result": {"exit_code": 0, "output": "OK"}},
        {"tool_call_id": "2", "result": {"exit_code": 7, "output": "EXPECTED"}},
    ], "rows": [{"role": "assistant", "content": "Done"}],
        "session": {"source": "feishu", "chat_id": "chat", "thread_id": "thread"}}
    history = [{"event_key": str(i), "scope": "main", "completed": 1} for i in (1, 2, 3)]
    delivery = [{"status": "delivered", "message_id": "message", "card_id": "card"}]
    observation = {"history_events": 3, "completed_events": 3, "metrics_pid_matches": True,
                   "config_unchanged": True, "state": {"active_agents": 0, "gateway": "running"},
                   "counters": {"card.completed": 1, "footer.turn.measured": 1}}
    message = {"type": "interactive", "message_id": "message", "card_id": "card", "chat_id": "chat",
               "body": '{"title":null,"elements":[[{"tag":"text","text":"请升级至最新版本客户端\uff0c以查看内容"}]]}'}
    return turn, history, delivery, observation, message


def run(values):
    return audit(*values, success_marker="OK", failure_marker="EXPECTED", answer="Done")


def test_opaque_api_projection_does_not_invalidate_transport_evidence():
    result = run(fixtures())
    assert result["ok"]
    assert result["message_get_content_verified"] is False
    assert result["desktop_visual_verified"] is False
    assert result["snapshot_consistency_only"] is True
    assert result["messages_sent"] == result["provider_calls"] == 0


def test_readable_projection_still_does_not_certify_client_pixels():
    values = fixtures()
    values[-1]["body"] = "OK EXPECTED Done"
    result = run(values)
    assert result["ok"] and result["message_get_content_verified"]
    assert result["desktop_visual_verified"] is False


@pytest.mark.parametrize("case", [
    "duplicate_tool", "unmatched_result", "wrong_exit", "wrong_stdout", "wrong_answer",
    "duplicate_usage", "incomplete_usage", "extra_delivery", "stale_pid", "changed_config",
])
def test_independent_evidence_gates(case):
    values = deepcopy(fixtures())
    turn, history, delivery, observation, _message = values
    if case == "duplicate_tool":
        turn["calls"][1]["id"] = "1"
    elif case == "unmatched_result":
        turn["results"][1]["tool_call_id"] = "wrong"
    elif case == "wrong_exit":
        turn["results"][1]["result"]["exit_code"] = 0
    elif case == "wrong_stdout":
        turn["results"][1]["result"]["output"] = "OTHER"
    elif case == "wrong_answer":
        turn["rows"][0]["content"] = "OTHER"
    elif case == "duplicate_usage":
        history[1]["event_key"] = "1"
    elif case == "incomplete_usage":
        history[1]["completed"] = 0
    elif case == "extra_delivery":
        delivery.append(dict(delivery[0]))
    elif case == "stale_pid":
        observation["metrics_pid_matches"] = False
    elif case == "changed_config":
        observation["config_unchanged"] = False
    assert not run(values)["ok"]
