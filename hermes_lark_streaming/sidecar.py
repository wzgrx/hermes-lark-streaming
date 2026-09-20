"""Optional signed, process-isolated CardKit event renderer."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .controller import StreamCardController
from .metrics import metrics
from .security import ReplayGuard

MAX_BODY = 64 * 1024


class SidecarDispatcher:
    """Own one event loop and controller inside the sidecar process."""

    def __init__(self, controller: StreamCardController | None = None) -> None:
        self.controller = controller or StreamCardController()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="hls-sidecar-loop", daemon=True)
        self.thread.start()

    async def _dispatch(self, payload: dict[str, Any]) -> bool:
        event = str(payload.get("event", ""))
        message_id = str(payload.get("message_id", ""))
        chat_id = str(payload.get("chat_id", ""))
        text = str(payload.get("text", ""))
        if event == "message.started":
            self.controller.on_message_started(
                message_id=message_id,
                chat_id=chat_id,
                anchor_id=str(payload.get("anchor_id", "")) or None,
                session_key=str(payload.get("session_key", "")) or None,
            )
            return True
        if event == "thinking.delta":
            return self.controller.on_thinking(message_id=message_id, text=text)
        if event == "reasoning.delta":
            return self.controller.on_reasoning(message_id=message_id, text=text)
        if event == "answer.delta":
            return self.controller.on_answer(message_id=message_id, text=text)
        if event == "tool.updated":
            return self.controller.on_tool_update(
                message_id=message_id,
                tool_name=str(payload.get("tool_name", "tool")),
                status=str(payload.get("status", "running")),
                detail=str(payload.get("detail", "")),
            )
        if event == "approval.enter":
            self.controller.on_approval_enter(message_id=message_id)
            return True
        if event == "clarify.enter":
            self.controller.on_clarify_enter(message_id=message_id, chat_id=chat_id or None)
            return True
        if event == "clarify.exit":
            self.controller.on_clarify_exit(message_id=message_id, chat_id=chat_id or None)
            return True
        if event == "message.aborted":
            self.controller.on_aborted(message_id=message_id)
            return True
        if event == "message.completed":
            return await self.controller.on_completed_wait(
                message_id=message_id,
                answer=text,
                is_error=bool(payload.get("is_error", False)),
                duration=float(payload.get("duration", 0.0)),
                model=str(payload.get("model", "")),
                tokens=payload.get("tokens") if isinstance(payload.get("tokens"), dict) else None,
                context=payload.get("context") if isinstance(payload.get("context"), dict) else None,
                deliver_all_media=bool(payload.get("deliver_all_media", False)),
            )
        raise ValueError("unsupported sidecar event")

    def submit(self, payload: dict[str, Any], *, timeout: float = 60.0) -> bool:
        future = asyncio.run_coroutine_threadsafe(self._dispatch(payload), self.loop)
        return bool(future.result(timeout=timeout))

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2)
        self.loop.close()


def serve(host: str = "127.0.0.1", port: int = 8788) -> None:
    secret = os.environ.get("HERMES_LARK_SIDECAR_SECRET", "")
    guard = ReplayGuard()
    dispatcher = SidecarDispatcher()

    class Handler(BaseHTTPRequestHandler):
        server_version = "HermesLarkSidecar/1"

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/health":
                self._json(200, {"ok": True, "renderer": "ready"})
            elif self.path == "/metrics":
                self._json(200, metrics.snapshot())
            else:
                self._json(404, {"ok": False})

        def do_POST(self) -> None:
            if self.path != "/events" or not secret:
                self._json(404, {"ok": False})
                return
            length = min(int(self.headers.get("Content-Length", "0")), MAX_BODY)
            body = self.rfile.read(length)
            try:
                guard.verify(self.headers.get("X-Hermes-Proof", ""), body, secret=secret, domain="sidecar-event")
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError("event payload must be an object")
                event = str(payload.get("event", "unknown"))[:64]
                accepted = dispatcher.submit(payload)
                metrics.increment(f"sidecar.event.{event}")
                metrics.persist()
                self._json(202, {"ok": accepted})
            except (ValueError, json.JSONDecodeError):
                metrics.increment("sidecar.auth_reject")
                self._json(401, {"ok": False})
            except Exception:
                metrics.increment("sidecar.dispatch_error")
                self._json(503, {"ok": False})

        def log_message(self, fmt: str, *args: object) -> None:
            return

    try:
        ThreadingHTTPServer((host, port), Handler).serve_forever()
    finally:
        dispatcher.close()
