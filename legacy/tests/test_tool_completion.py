from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest

from hermes_lark_streaming.patch import on_tool_updated
from hermes_lark_streaming.streaming.tool_result import normalize_tool_completion
from hermes_lark_streaming.streaming.tooluse import redact_inline_secrets
from tests.test_patcher import _build_tool_hook_runner


@pytest.mark.parametrize("modern", [True, False])
def test_generated_hook_consumes_real_hermes_completion_kwargs(modern):
    ctx = MagicMock(event_message_id="event", log_queue=None)
    ctx._run_still_current.return_value = True
    callback = _build_tool_hook_runner(use_turn_context=modern)(ctx)
    data = {"output": "EXPECTED_FAILURE", "exit_code": 7}
    with patch("hermes_lark_streaming.patch.on_tool_updated", return_value=True) as observed:
        if modern:
            callback("tool.completed", "terminal", None, result=data, is_error=True)
        else:
            callback("tool.completed", "event", lambda: True, "terminal", None, result=data, is_error=True)
    assert observed.call_args.kwargs["result"] is data
    assert observed.call_args.kwargs["is_error"] is True


@pytest.mark.parametrize("metadata", [True, False, None])
def test_terminal_exit_code_is_not_rendered_as_success(metadata):
    source = {"output": "EXPECTED_FAILURE", "exit_code": 7}
    before = deepcopy(source)
    status, detail = normalize_tool_completion("terminal", "completed", "", result=source, is_error=metadata)
    assert status == "error" and "EXPECTED_FAILURE" in detail and "Exit code 7" in detail
    assert source == before


def test_patch_maps_completion_without_changing_controller_contract():
    ctrl = MagicMock()
    on_tool_updated.__wrapped__(ctrl=ctrl, message_id="event", tool_name="terminal", status="completed",
                               result='{"output":"OUTPUT_OK", "exit_code":0}', is_error=False)
    ctrl.on_tool_update.assert_called_once_with(
        message_id="event", tool_name="terminal", status="completed", detail="OUTPUT_OK",
    )


@pytest.mark.parametrize("name", ["read_file", "browser", "web_fetch", "vision_analyze", "write_file"])
def test_file_browser_media_payloads_are_not_projected(name):
    status, text = normalize_tool_completion(name, "completed", "", result={"output": "PRIVATE_BODY"})
    assert status == "completed" and text == ""


@pytest.mark.parametrize("flag", ["true", "false", 1, [], {}])
def test_error_flag_is_strictly_boolean(flag):
    assert normalize_tool_completion("terminal", "completed", "", is_error=flag) == ("completed", "")


def test_empty_explicit_failure_still_marks_error():
    assert normalize_tool_completion("terminal", "completed", "", is_error=True) == ("error", "Tool reported failure")


def test_started_and_legacy_callbacks_preserve_details():
    assert normalize_tool_completion("terminal", "started", "printf OK", result="ignored", is_error=True) == (
        "started", "printf OK",
    )
    assert normalize_tool_completion("search", "completed", "legacy summary") == ("completed", "legacy summary")


@pytest.mark.parametrize("secret", [
    "--api-key FIXTURESECRET", "--secret=FIXTURESECRET", '--password "FIXTURESECRET"',
    '"api_key": "FIXTURESECRET"', "oc_sk_FIXTURESECRET", "sk-FIXTURESECRET", "ghp_FIXTURESECRET",
])
def test_projected_credentials_are_removed_not_just_appended(secret):
    text = redact_inline_secrets(secret)
    assert "FIXTURESECRET" not in text and "[redacted]" in text
    _, text = normalize_tool_completion("terminal", "completed", "", result={"output": secret, "exit_code": 0})
    assert "FIXTURESECRET" not in text


def test_result_budget_and_unknown_objects():
    _, text = normalize_tool_completion("terminal", "completed", "", result={"output": "x" * 50000})
    assert len(text) <= 4096 and "truncated" in text
    assert normalize_tool_completion("terminal", "completed", "", result=object()) == ("completed", "")
    assert "size limit" in normalize_tool_completion("terminal", "completed", "", result='{"output":"'+"x"*40000)[1]
