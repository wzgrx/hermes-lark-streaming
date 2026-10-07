"""Source text injected into Hermes, one builder per hook.

Each builder returns the block body without indentation or markers; ``engine`` wraps it. Semantics are
battle-tested against real Hermes: every guard (``_run_still_current``, ``already_streamed``,
``stream_deltas_enabled``, ``locals().get``) and the follow-up/completion-id carrying must stay verbatim.
Injected code imports bridge functions from ``hermes_lark_streaming.hooks.bridge`` only.
"""

from __future__ import annotations

BRIDGE = "hermes_lark_streaming.hooks.bridge"


def _guard(name: str, indent: str = "") -> list[str]:
    return [
        f"{indent}except Exception:",
        f"{indent}    import logging as _lark_logging",
        f'{indent}    _lark_logging.getLogger("hermes_lark_streaming").exception("injected hook failed: {name}")',
    ]


def normalize() -> list[str]:
    return [
        "try:",
        f"    from {BRIDGE} import on_feishu_normalize",
        "    on_feishu_normalize(",
        "        message_id=event.message_id,",
        "        source=source,",
        "        event=event,",
        "        reply_anchor_id=self._reply_anchor_for_event(event),",
        "    )",
        *_guard("normalize"),
    ]


def start() -> list[str]:
    return [
        "try:",
        "    if source.platform.value.lower() in ('feishu', 'lark'):",
        f"        from {BRIDGE} import on_message_started",
        "        _lark_anchor_id = self._reply_anchor_for_event(event)",
        "        on_message_started(",
        "            message_id=event.message_id,",
        "            chat_id=source.chat_id,",
        "            anchor_id=_lark_anchor_id,",
        "            session_key=locals().get('session_key') or locals().get('_quick_key'),",
        "        )",
        *_guard("start"),
    ]


def complete() -> list[str]:
    return [
        "try:",
        f"    from {BRIDGE} import (",
        "        on_message_completed_wait,",
        "        on_message_needs_text_fallback,",
        "    )",
        "    _lark_completion_id = agent_result.get('_hermes_lark_completion_id') or event.message_id",
        "    _lark_card_sent = await on_message_completed_wait(",
        "        message_id=_lark_completion_id,",
        "        answer=response,",
        "        is_error=bool(agent_result.get('failed')),",
        "        duration=_turn_seconds,",
        "        model=agent_result.get('model', ''),",
        "        tokens={",
        "            'input_tokens': agent_result.get('input_tokens', 0),",
        "            'output_tokens': agent_result.get('output_tokens', 0),",
        "        },",
        "        context={",
        "            'used_tokens': agent_result.get('last_prompt_tokens', 0),",
        "            'max_tokens': agent_result.get('context_length', 0),",
        "        },",
        "    )",
        "    if _lark_card_sent:",
        "        agent_result['already_sent'] = True",
        "        _footer_line = ''",
        "        if agent_result.get('failed'):",
        "            response = ''",
        "    elif on_message_needs_text_fallback(message_id=_lark_completion_id):",
        "        agent_result.pop('already_sent', None)",
        *_guard("complete"),
    ]


def followup_complete() -> list[str]:
    return [
        "try:",
        f"    from {BRIDGE} import on_queued_followup_boundary",
        "    _lark_delivery_result = response if isinstance(response, dict) else result",
        "    _lark_followup_sent = await on_queued_followup_boundary(",
        "        message_id=turn_ctx.event_message_id, result=_lark_delivery_result,",
        "        interrupted=bool(result.get('interrupted')),",
        "    )",
        "    if _lark_followup_sent and _lark_delivery_result is not result:",
        "        result['response_previewed'] = True",
        "        result['already_sent'] = True",
        "        result['final_response'] = ''",
        *_guard("followup_complete"),
    ]


def followup_result() -> list[str]:
    return [
        "try:",
        f"    from {BRIDGE} import on_queued_followup_result",
        "    _lark_followup_completion_id = next_message_id or getattr(pending_event, 'message_id', None)",
        "    if _lark_followup_completion_id:",
        "        on_queued_followup_result(",
        "            message_id=_lark_followup_completion_id,",
        "            followup_result=followup_result,",
        "        )",
        *_guard("followup_result"),
    ]


def tool() -> list[str]:
    return [
        "try:",
        f"    from {BRIDGE} import on_tool_updated",
        "    try:",
        "        _lark_ctx = ctx",
        "    except NameError:",
        "        try:",
        "            _lark_ctx = self._ctx",
        "        except (NameError, AttributeError):",
        "            _lark_ctx = None",
        "    if _lark_ctx is not None:",
        "        _lark_message_id = _lark_ctx.event_message_id",
        "        _lark_run_current = _lark_ctx._run_still_current",
        "    else:",
        "        _lark_message_id = event_message_id",
        "        _lark_run_current = _run_still_current",
        "    if _lark_run_current() and event_type in ('tool.started', 'tool.completed'):",
        "        if on_tool_updated(",
        "            message_id=_lark_message_id,",
        "            tool_name=tool_name or '',",
        "            status='started' if event_type == 'tool.started' else 'completed',",
        "            detail=preview or '',",
        "            result=locals().get('kwargs', {}).get('result'),",
        "            is_error=locals().get('kwargs', {}).get('is_error'),",
        "        ):",
        "            _lark_log_queue = getattr(_lark_ctx, 'log_queue', None) if _lark_ctx is not None else None",
        "            if _lark_ctx is None:",
        "                try:",
        "                    _lark_log_queue = log_queue",
        "                except NameError:",
        "                    pass",
        "            if _lark_log_queue is not None and event_type == 'tool.started' and tool_name != '_thinking':",
        "                from datetime import datetime as _lark_datetime",
        "                _lark_timestamp = _lark_datetime.now().strftime('%Y-%m-%d %H:%M:%S')",
        "                _lark_preview = f' \"{preview}\"' if preview else ''",
        "                _lark_log_queue.put(f'{_lark_timestamp}  {tool_name}:{_lark_preview}'.rstrip())",
        "            return",
        *_guard("tool"),
    ]


def answer() -> list[str]:
    return [
        "try:",
        f"    from {BRIDGE} import on_answer_delta",
        "    try:",
        "        _lark_message_id = ctx.event_message_id",
        "        _lark_run_current = ctx._run_still_current",
        "    except NameError:",
        "        _lark_message_id = event_message_id",
        "        _lark_run_current = _run_still_current",
        "    if text and _lark_run_current() and on_answer_delta(message_id=_lark_message_id, text=text):",
        "        # CardKit consumed this delta, so Hermes' native consumer was deliberately not fed.",
        "        # Mark it interim-only to suppress the core's false duplicate-send warning at turn end.",
        "        try:",
        "            _lark_native_consumer = ctx.stream_consumer_holder[0]",
        "        except (NameError, AttributeError, IndexError, TypeError):",
        "            _lark_native_consumer = None",
        "        if _lark_native_consumer is not None:",
        "            _lark_native_consumer.stream_deltas_enabled = False",
        "        try:",
        "            _lark_stts_consumer = stts",
        "        except NameError:",
        "            _lark_stts_consumer = None",
        "        if _lark_stts_consumer is not None:",
        "            try:",
        "                _lark_stts_consumer.on_delta(text)",
        "            except Exception:",
        "                import logging as _lark_logging",
        '                _lark_logging.getLogger("hermes_lark_streaming").exception(',
        '                    "injected hook failed: streaming_tts"',
        "                )",
        "        return",
        *_guard("answer"),
    ]


def thinking() -> list[str]:
    return [
        "try:",
        f"    from {BRIDGE} import on_thinking_delta",
        "    try:",
        "        _lark_message_id = ctx.event_message_id",
        "        _lark_run_current = ctx._run_still_current",
        "    except NameError:",
        "        _lark_message_id = event_message_id",
        "        _lark_run_current = _run_still_current",
        "    if (text and not already_streamed and _lark_run_current()",
        "            and on_thinking_delta(message_id=_lark_message_id, text=text)):",
        "        if stts is not None:",
        "            stts.on_delta(None)",
        "            stts.on_delta(text)",
        "            stts.on_delta(None)",
        "        return",
        *_guard("thinking"),
    ]


def reasoning() -> list[str]:
    return [
        "def _reasoning_cb(text):",
        "    try:",
        "        try:",
        "            _lark_message_id = ctx.event_message_id",
        "            _lark_run_current = ctx._run_still_current",
        "        except NameError:",
        "            _lark_message_id = event_message_id",
        "            _lark_run_current = _run_still_current",
        "        if text and _lark_run_current():",
        f"            from {BRIDGE} import on_reasoning_delta",
        "            on_reasoning_delta(message_id=_lark_message_id, text=text)",
        *_guard("reasoning", indent="    "),
        "agent.reasoning_callback = _reasoning_cb",
    ]


def background_review() -> list[str]:
    return [
        "try:",
        f"    from {BRIDGE} import on_background_review_message",
        "    _lark_bg_review_sender = agent.background_review_callback",
        "    def _lark_bg_review_callback(message):",
        "        try:",
        "            _lark_message_id = ctx.event_message_id",
        "        except NameError:",
        "            _lark_message_id = event_message_id",
        "        _lark_bg_review_deferred = on_background_review_message(",
        "            message_id=_lark_message_id,",
        "            text=message,",
        "            sender=_lark_bg_review_sender,",
        "        )",
        "        if not _lark_bg_review_deferred:",
        "            _lark_bg_review_sender(message)",
        "    agent.background_review_callback = _lark_bg_review_callback",
        *_guard("background_review"),
    ]


def abort() -> list[str]:
    return [
        "try:",
        f"    from {BRIDGE} import on_message_aborted",
        "    on_message_aborted(message_id=event.message_id)",
        *_guard("abort"),
    ]


def stop() -> list[str]:
    return [
        "try:",
        "    if source.platform.value.lower() in ('feishu', 'lark'):",
        f"        from {BRIDGE} import on_session_aborted",
        "        await on_session_aborted(",
        "            session_key=locals().get('quick_key') or locals().get('_quick_key') or '',",
        "        )",
        *_guard("stop"),
    ]


def interrupt() -> list[str]:
    return [
        "try:",
        "    if source.platform.value.lower() in ('feishu', 'lark'):",
        f"        from {BRIDGE} import (",
        "            on_message_aborted, on_message_interrupted, on_message_started,",
        "        )",
        "        _lark_next_message_id = getattr(pending_event, 'message_id', None) or next_message_id",
        "        _lark_next_anchor_id = next_message_id",
        "        if result.get(\"interrupted\") and _lark_next_message_id:",
        "            on_message_interrupted(",
        "                message_id=turn_ctx.event_message_id,",
        "                new_message_id=_lark_next_message_id,",
        "                chat_id=source.chat_id,",
        "                anchor_id=_lark_next_anchor_id,",
        "                session_key=locals().get('next_session_key') or locals().get('session_key'),",
        "            )",
        "        elif result.get(\"interrupted\"):",
        "            on_message_aborted(message_id=turn_ctx.event_message_id)",
        "        elif pending_event is not None and _lark_next_message_id:",
        "            on_message_started(",
        "                message_id=_lark_next_message_id,",
        "                chat_id=getattr(next_source, 'chat_id', source.chat_id),",
        "                anchor_id=_lark_next_anchor_id,",
        "                session_key=locals().get('next_session_key') or locals().get('session_key'),",
        "            )",
        *_guard("interrupt"),
    ]


def bg_deliver() -> list[str]:
    return [
        "try:",
        "    if source.platform.value.lower() in ('feishu', 'lark') and response:",
        f"        from {BRIDGE} import on_background_deliver",
        "        _bg_preview = prompt[:60] + ('...' if len(prompt) > 60 else '')",
        "        if await on_background_deliver(",
        "            chat_id=source.chat_id,",
        "            preview=_bg_preview,",
        "            content=text_content,",
        "            reply_to_message_id=event_message_id,",
        "        ):",
        "            text_content = ''",
        "            if not images and not media_files:",
        "                return",
        *_guard("background_deliver"),
    ]


def approval() -> list[str]:
    return [
        "try:",
        f"    from {BRIDGE} import on_approval_enter",
        "    on_approval_enter(message_id=self._ctx.event_message_id)",
        *_guard("approval"),
    ]


def clarify() -> list[str]:
    return [
        "try:",
        "    import functools",
        f"    from {BRIDGE} import on_clarify_enter, on_clarify_exit",
        "    _lark_clarify_orig = agent.clarify_callback",
        "    @functools.wraps(_lark_clarify_orig)",
        "    def _lark_clarify_wrapper(*args, **kwargs):",
        "        try:",
        "            _lark_clarify_msg_id = ctx.event_message_id",
        "            _lark_clarify_chat_id = ctx._status_chat_id",
        "            _lark_clarify_sk = ctx.session_key",
        "        except NameError:",
        "            _lark_clarify_msg_id = event_message_id",
        "            _lark_clarify_chat_id = None",
        "            _lark_clarify_sk = None",
        "        on_clarify_enter(",
        "            message_id=_lark_clarify_msg_id,",
        "            chat_id=_lark_clarify_chat_id,",
        "            session_key=_lark_clarify_sk,",
        "        )",
        "        try:",
        "            return _lark_clarify_orig(*args, **kwargs)",
        "        finally:",
        "            on_clarify_exit(",
        "                message_id=_lark_clarify_msg_id,",
        "                chat_id=_lark_clarify_chat_id,",
        "                session_key=_lark_clarify_sk,",
        "            )",
        "    agent.clarify_callback = _lark_clarify_wrapper",
        *_guard("clarify"),
    ]


def cron_deliver() -> list[str]:
    return [
        "try:",
        "    if (t.platform_name.lower() in ('feishu', 'lark')",
        "            and not getattr(t.transport, 'is_relay', False)",
        "            and not t.in_channel_surface and not t.thread_id):",
        f"        from {BRIDGE} import on_cron_deliver",
        "        _hermes_lark_cron_receipt = on_cron_deliver(",
        "            chat_id=t.chat_id, content=cleaned_delivery_content.strip(),",
        "            loop=loop, task_name=job.get('name', ''),",
        "            run_time=job.get('next_run_at', ''), job_id=job.get('id', ''),",
        "            media_files=locals().get('media_files') or [])",
        "        if _hermes_lark_cron_receipt:",
        "            if (isinstance(_hermes_lark_cron_receipt, dict)",
        "                    and _hermes_lark_cron_receipt.get('delivery_outcome') == 'unknown'):",
        "                unverified_targets.append(t.where)",
        "                continue",
        "            _maybe_mirror_cron_delivery(",
        "                job, t.platform_name, t.chat_id, t.mirror_text, thread_id=t.thread_id,",
        "                user_id=t.origin_user_id, enabled=t.mirror_this_target)",
        "            if not (isinstance(_hermes_lark_cron_receipt, dict)",
        "                    and _hermes_lark_cron_receipt.get('message_id')):",
        "                unverified_targets.append(t.where)",
        "            continue",
        *_guard("cron_deliver"),
    ]
