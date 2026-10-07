"""Bounded, thread-safe telemetry for one logical turn; output is a :class:`Footer` plus a plain snapshot."""

from __future__ import annotations

import re
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any

from ..card.model import Footer
from .reasoning import requested_reasoning
from .usage import Usage, cache_ratio, normalize_usage
from .values import count, label, mapping, model_display, seconds


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
    error_type: str = ""


class TurnTelemetry:
    """Bound to one card session; no process-wide 'latest session' fallback."""

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
                error_type = mapping(payload.get("error")).get("type") or payload.get("error_type")
                if (isinstance(error_type, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{0,95}", error_type)
                        and label(error_type) == error_type):
                    request.error_type = error_type
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
            return deepcopy(self._sealed)

    def snapshot(self) -> dict[str, Any]:
        """Read live counters without sealing the turn or retaining body content."""
        with self._lock:
            return deepcopy(self._sealed) if self._sealed is not None else self._snapshot()

    def footer(self) -> Footer:
        """The one-line summary values. ``tag`` is the session layer's to set."""
        data = self.snapshot()
        ratio, floor = cache_ratio(data)
        return Footer(
            model=model_display(data.get("model")),
            context_used=count(data.get("context_used")),
            context_max=count(data.get("context_max")),
            cache_hit=ratio,
            cache_hit_is_floor=floor,
            partial=bool(data.get("usage_partial")),
        )

    def _snapshot(self) -> dict[str, Any]:
        requests = list(self._requests.values())
        data: dict[str, Any] = {"duration": max(0.0, time.monotonic() - self.started), "usage_scope": "turn"}
        if not requests:
            data["telemetry_missing"] = True
            return data
        requests.sort(key=lambda r: r.started or 0)
        last = requests[-1]
        failures = [r for r in requests if r.failed and r.error_type]
        if failures:
            data["last_error_type"] = failures[-1].error_type
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
        cached = [r for r in measured if r.usage.cache_read is not None]
        if cached:
            # A cold/unknown request must not erase a later positive observation.
            # Canonical zero still lacks presence information: expose a lower
            # bound, not an invented full-turn hit rate, when coverage differs.
            data["cache_read_tokens"] = sum(r.usage.cache_read or 0 for r in cached)
            data["cache_read_partial"] = self._overflow or len(cached) != len(requests)
        if last.usage.prompt is not None:
            data["context_used"] = last.usage.prompt
        if last.context_max:
            data["context_max"] = last.context_max
        responded = [r for r in requests if r.first_response is not None and r.started is not None]
        if responded:
            first = min(responded, key=lambda r: (r.started or 0) + (r.first_response or 0))
            # First response is wall time since this turn's first API attempt,
            # including failed attempts/backoff. Keep the responding attempt's
            # own first-chunk latency separately; neither is a UI-render time.
            data["first_response"] = max(
                0.0, (first.started or 0) + (first.first_response or 0) - (requests[0].started or 0)
            )
            data["first_response_attempt"] = first.first_response
        routes: list[str] = []
        for request in requests:
            if request.provider and (not routes or routes[-1] != request.provider):
                routes.append(request.provider)
        data["routes"] = routes[:12]
        data["route_count"] = len(routes)
        return data
