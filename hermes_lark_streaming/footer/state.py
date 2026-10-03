"""Bounded, thread-safe telemetry for one transport message / logical turn."""

from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any

from .usage import Usage, count, mapping, normalize_usage


def label(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", value.strip()[:160])
    if re.search(r"(?:sk[-_]|gh[pousr]_|github_pat_|Bearer\s|://)", value, re.I):
        return "[redacted]"
    return " ".join(value.split())


def seconds(value: Any) -> float | None:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def requested_reasoning(payload: dict[str, Any]) -> str:
    """Read scalar request controls only; never retain the request or thinking text."""
    envelope = mapping(payload.get("request"))
    request = mapping(envelope.get("body")) or envelope
    extra = mapping(request.get("extra_body"))
    for source in (request, extra, mapping(request.get("additionalModelRequestFields"))):
        effort = source.get("reasoning_effort") or mapping(source.get("reasoning")).get("effort")
        effort = effort or mapping(source.get("output_config")).get("effort")
        if isinstance(effort, str) and effort.lower() in {"none", "minimal", "low", "medium", "high", "xhigh", "max"}:
            return effort.lower()
        thinking = mapping(source.get("thinking"))
        budget = count(thinking.get("budget_tokens"))
        if budget is not None:
            return f"budget:{budget}"
        if thinking.get("type") in {"enabled", "disabled", "adaptive"}:
            return str(thinking["type"])
        generation = mapping(source.get("generationConfig")) or mapping(source.get("generation_config"))
        google = mapping(source.get("google"))
        thinking = (
            mapping(generation.get("thinkingConfig"))
            or mapping(generation.get("thinking_config"))
            or mapping(google.get("thinking_config"))
        )
        level = thinking.get("thinkingLevel", thinking.get("thinking_level"))
        if isinstance(level, str) and level.lower() in {"minimal", "low", "medium", "high"}:
            return level.lower()
        budget = count(thinking.get("thinkingBudget", thinking.get("thinking_budget")))
        if budget is not None:
            return f"budget:{budget}"
        enabled = source.get("enable_thinking", mapping(source.get("chat_template_kwargs")).get("enable_thinking"))
        if isinstance(enabled, bool):
            return "enabled" if enabled else "disabled"
    return ""


@dataclass
class Request:
    provider: str = ""
    model: str = ""
    response_model: str = ""
    api_mode: str = ""
    reasoning: str = ""
    reasoning_source: str = ""
    request_truncated: bool = False
    started: float | None = None
    first_response: float | None = None
    ended: float | None = None
    context_max: int | None = None
    usage: Usage = field(default_factory=Usage)
    finished: bool = False
    failed: bool = False


class TurnFooter:
    """Bound to its CardSession; no process-wide 'latest session' fallback."""

    def __init__(self) -> None:
        self.started = time.monotonic()
        self.created_at = time.time()
        self._lock = threading.RLock()
        self._identity: tuple[str, str] | None = None
        self._requests: dict[tuple[str, float], Request] = {}
        self._sealed: dict[str, Any] | None = None
        self._overflow = False

    def observe(self, event: str, payload: dict[str, Any]) -> bool:
        if payload.get("platform") not in {"feishu", "lark"} or payload.get("aux_task"):
            return False
        sid, tid, rid = (payload.get(k) for k in ("session_id", "turn_id", "api_request_id"))
        if not all(isinstance(s, str) and 0 < len(s) <= 512 for s in (sid, tid, rid)):
            return False
        started = seconds(payload.get("started_at"))
        if started is None or started < self.created_at:
            return False
        with self._lock:
            if self._sealed is not None:
                return False
            identity = (str(sid), str(tid))
            if self._identity is None:
                if event != "pre_api_request":
                    return False
                self._identity = identity
            if identity != self._identity:
                # Compression can rotate the storage session during the same logical
                # turn. Without a proven remap, keep prior measurements visibly partial.
                if tid == self._identity[1]:
                    self._overflow = True
                return False
            key = (str(rid), started)  # same logical call may have several physical attempts
            if key not in self._requests:
                if event != "pre_api_request":
                    return False
                if len(self._requests) >= 2048:
                    self._overflow = True
                    return False
                self._requests[key] = Request(started=started)
            request = self._requests[key]
            if request.finished:
                return False
            request.provider = label(payload.get("provider")) or request.provider
            request.model = label(payload.get("model")) or request.model
            request.api_mode = label(payload.get("api_mode")) or request.api_mode
            if event == "pre_api_request" and request.reasoning_source != "llm_execution":
                request.reasoning = requested_reasoning(payload) or request.reasoning
                if request.reasoning:
                    request.reasoning_source = "pre_api_request"
                request.request_truncated = mapping(payload.get("request")).get("_truncated") is True
            elif event == "api_request_error":
                request.failed = True  # do not retain the error message/body
            elif event == "post_api_request":
                request.finished = True
                request.response_model = label(payload.get("response_model"))
                request.ended = seconds(payload.get("ended_at"))
                request.context_max = count(payload.get("context_length"))
                first = seconds(payload.get("first_chunk_at"))
                if first is not None and first >= started:
                    request.first_response = first - started
                usage = normalize_usage(payload.get("usage"), "hermes")
                # Both payload.usage and response.usage are canonical in current Hermes.
                # Zero-filled optional buckets lose presence information upstream. Only
                # positive cache evidence is reliable; do not invent a reported 0%.
                request.usage = replace(
                    usage, cache_read=usage.cache_read or None, cache_write=usage.cache_write or None
                )
            elif event != "pre_api_request":
                return False
            return True

    def observe_execution(self, payload: dict[str, Any]) -> bool:
        """Read scalar controls from a uniquely matched, active execution attempt.

        Hermes's pre-request observer may truncate the entire body. Execution
        middleware still sees structured kwargs. Retain no body or preview, and
        never synthesize a request when the earlier identity event is missing.
        """
        if payload.get("platform") not in {"feishu", "lark"} or payload.get("aux_task"):
            return False
        sid, tid, rid = (payload.get(k) for k in ("session_id", "turn_id", "api_request_id"))
        if not all(isinstance(value, str) and 0 < len(value) <= 512 for value in (sid, tid, rid)):
            return False
        # Exact, unshortened route identity: equal redacted/truncated labels are
        # not evidence that an execution belongs to the observed attempt.
        route = [payload.get(k) for k in ("provider", "model", "api_mode")]
        if not all(isinstance(v, str) and 0 < len(v) < 160 and label(v) == v for v in route):
            return False
        with self._lock:
            if self._sealed is not None or self._identity != (sid, tid):
                return False
            matches = [
                request for (request_id, _), request in self._requests.items()
                if request_id == rid and not request.finished and not request.failed
                and [request.provider, request.model, request.api_mode] == route
            ]
            if len(matches) != 1:
                return False
            request = matches[0]
            request.reasoning = requested_reasoning(payload)
            request.reasoning_source = "llm_execution"
            request.request_truncated = False
            return True

    def finish(self) -> dict[str, Any]:
        with self._lock:
            if self._sealed is None:
                self._sealed = self._snapshot()
            return dict(self._sealed)

    def _snapshot(self) -> dict[str, Any]:
        requests = list(self._requests.values())
        data: dict[str, Any] = {"duration": max(0.0, time.monotonic() - self.started), "usage_scope": "turn"}
        if not requests:
            data["telemetry_missing"] = True
            return data
        requests.sort(key=lambda r: r.started or 0)
        last = requests[-1]
        measured = [r for r in requests if r.usage.prompt is not None and r.usage.output is not None]
        data.update(
            provider=last.provider,
            model=last.response_model or last.model,
            requested_model=last.model,
            response_model=last.response_model,
            api_mode=last.api_mode,
            reasoning=last.reasoning,
            reasoning_source=last.reasoning_source,
            api_calls=len(requests),
            retries=sum(r.failed for r in requests),
            usage_partial=self._overflow or len(measured) != len(requests),
        )
        if not last.reasoning and last.request_truncated:
            data["reasoning_missing_reason"] = "request_truncated"
        if measured:
            data["input_tokens"] = sum(r.usage.prompt or 0 for r in measured)
            data["output_tokens"] = sum(r.usage.output or 0 for r in measured)
        if len(measured) == len(requests) and all(r.usage.cache_read is not None for r in measured):
            data["cache_read_tokens"] = sum(r.usage.cache_read or 0 for r in measured)
        if last.usage.prompt is not None:
            data["context_used"] = last.usage.prompt
        if last.context_max:
            data["context_max"] = last.context_max
        first = requests[0].first_response
        if first is not None:
            data["first_response"] = first
        routes: list[str] = []
        for request in requests:
            if request.provider and (not routes or routes[-1] != request.provider):
                routes.append(request.provider)
        data["routes"] = routes[:12]
        data["route_count"] = len(routes)
        return data
