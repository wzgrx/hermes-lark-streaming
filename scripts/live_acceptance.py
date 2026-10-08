"""Live acceptance against the real Feishu API, without needing an inbound user message.

Sends one labelled marker message to a chat, then drives the real Controller through a full turn on a card
replying to it: streamed answer, one ok tool, one failing tool, telemetry, final details panel. CardKit's
server-side validation accepts or rejects every create/insert/patch/stream/seal call, so a clean run proves
the card JSON and the update protocol work; it cannot show how a client draws the card.

    .venv/bin/python scripts/live_acceptance.py <chat_id> [--hermes-home ~/.hermes]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path


def load_env(home: Path) -> None:
    env = home / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def report(card: dict) -> None:
    """Print one line per top-level element of the final card, so a run can be eyeballed."""
    from hermes_lark_streaming.card.render import count_elements

    size = len(json.dumps(card, ensure_ascii=False).encode())
    print(f"final card: {size} bytes, {count_elements(card)} elements")
    for element in card["body"]["elements"]:
        title = element.get("header", {}).get("title", element)
        text = title.get("i18n_content", {}).get("zh_cn") or title.get("content") or element.get("tag")
        print("  ", element.get("element_id") or element.get("tag"), "->", str(text)[:110])


async def main(chat_id: str, home: Path, *, ok_only: bool = False, hold: float = 0.0) -> int:
    load_env(home)
    from hermes_lark_streaming.session import controller as controller_module
    from hermes_lark_streaming.session.controller import Controller
    from hermes_lark_streaming.transport import DeliveryLedger

    # the simulated turn's usage must never reach the real usage ledger (累计 今日/本月/总计)
    controller_module.observe_history = lambda *args, **kwargs: False

    ctl = Controller(home)
    ctl.ledger = DeliveryLedger(Path(tempfile.mkdtemp()) / "ledger.json")
    ctl.pipeline.rt.ledger = ctl.ledger
    ctl.static._ledger = ctl.ledger
    if not ctl.enabled:
        print("controller disabled: streaming.enabled or credentials missing")
        return 2

    client = await ctl._client_for(chat_id)
    finals: list[dict] = []
    original_update = client.update_card

    async def capture(channel, card):  # keep the last full-card update for inspection
        finals.append(card)
        await original_update(channel, card)

    client.update_card = capture  # type: ignore[method-assign]
    stamp = time.strftime("%H:%M:%S")
    anchor = await client.send_text(chat_id, f"【插件自动验收 {stamp}】下面是一轮模拟对话的卡片。")
    mid = f"e2e-{anchor}"
    ctl.on_message_started(message_id=mid, chat_id=chat_id, anchor_id=anchor, session_key="live-acceptance")
    session = ctl._sessions[mid]
    if not await ctl._wait_creation(session) or not session.has_card:
        print("FAIL: card was not created")
        return 1
    print("card created:", session.channel.card_id if session.channel else None)

    with session.lock:  # reasoning display is a config switch; feed the timeline directly for the check
        session.add_thought("用户想看卡片的完整效果。先执行一个成功的命令,再执行"
                            + ("另一个命令。" if ok_only else "一个预期失败的命令。"))
    await asyncio.sleep(1.5)
    ctl.on_tool_update(message_id=mid, tool_name="terminal", status="started", detail="printf 'V1_OK\\n'")
    await asyncio.sleep(1.2)
    ok = '{"exit_code": 0, "output": "V1_OK"}'
    ctl.on_tool_update(message_id=mid, tool_name="terminal", status="completed", result=ok)
    now = time.time()
    payload = {
        "platform": "feishu", "session_id": "live", "turn_id": "t1", "api_request_id": "r1", "started_at": now,
        "provider": "opencode-go", "model": "deepseek-v4.1-flash", "request": {"body": {"reasoning_effort": "max"}},
    }
    ctl.observe("pre_api_request", payload, session_key="live-acceptance")
    chunks = (
        ("第一步已完成。", "接下来再执行一个命令,", "确认整轮顺利结束。\n\n", "- 两个命令都应成功\n- 回答完整")
        if ok_only else
        ("第一步已完成。", "接下来执行一个预期失败的命令,", "用来检查失败行的显示。\n\n",
         "- 退出码应为 7\n- 回答仍然完整")
    )
    for chunk in chunks:
        ctl.on_answer(message_id=mid, text=chunk)
        await asyncio.sleep(1.0)
    code = 0 if ok_only else 7
    ctl.on_tool_update(message_id=mid, tool_name="terminal", status="started", detail=f"sh -c 'exit {code}'")
    await asyncio.sleep(0.8 + hold)  # --hold keeps the card in its running state for a screenshot
    output = "" if ok_only else "V1_EXPECTED_FAILURE\nline two"
    failed = json.dumps({"exit_code": code, "output": output})
    ctl.on_tool_update(message_id=mid, tool_name="terminal", status="completed", result=failed)
    usage = {"prompt_tokens": 70300, "output_tokens": 1200, "cache_read_tokens": 58000}
    done = {**payload, "ended_at": time.time(), "first_chunk_at": now + 0.4, "context_length": 1_000_000,
            "response_model": "deepseek-v4.1-flash", "usage": usage}
    ctl.observe("post_api_request", done, session_key="live-acceptance")
    await asyncio.sleep(1.0)
    sent = await ctl.on_completed_wait(message_id=mid, answer="", duration=time.monotonic() - session.started,
                                       model="deepseek-v4.1-flash")
    print("completed:", sent, "| state:", session.state.value, "| delivery:", session.delivery_status.value)
    from hermes_lark_streaming.metrics import metrics

    print("metrics:", {k: v for k, v in metrics.snapshot().items() if k.startswith(("card.", "delivery.", "cardkit."))})
    if finals:
        report(finals[-1])
    return 0 if sent else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("chat_id")
    parser.add_argument("--ok", action="store_true", help="make every tool succeed (the common case)")
    parser.add_argument("--hermes-home", type=Path, default=Path.home() / ".hermes")
    parser.add_argument("--hold", type=float, default=0.0, help="seconds to stay mid-tool, for a running screenshot")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.chat_id, args.hermes_home, ok_only=args.ok, hold=args.hold)))
