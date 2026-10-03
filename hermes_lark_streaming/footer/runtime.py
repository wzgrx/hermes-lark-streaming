"""Content-free live phases and native, compact running footer elements."""

# ruff: noqa: RUF001
from __future__ import annotations

import threading
import time
from typing import Any

from .layout import markdown
from .render import build_footer, safe
from .state import label, seconds
from .usage import count

LOADING_ID = "loading_icon"  # also the existing body insertion anchor
DETAILS_ID = "footer_details"


class RuntimeStatus:
    """A per-card-session observer: no payloads, prompts, error text or tool args."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.created_at = time.time()
        self._identity: tuple[str, str] | None = None
        self._latest = 0.0
        self._resumed_at = 0.0
        self._phase = "processing"
        self._provider = ""
        self._route: tuple[str, str] | None = None
        self._aux: dict[tuple[str, float], bool] = {}
        self._summary_result = ""

    def signal(self, phase: str) -> None:
        if phase not in {"answer", "thinking", "processing"}:
            return
        with self._lock:
            self._phase = phase
            if phase in {"answer", "thinking"}:
                # A native main-stream delta proves work resumed, including
                # when compression rotated the storage session ID. It does not
                # prove a compression commit or authorize identity remapping.
                self._resumed_at = time.time()
                self._summary_result = ""

    def observe(self, event: str, payload: dict[str, Any]) -> bool:
        if payload.get("platform") not in {"feishu", "lark"}:
            return False
        sid, tid, rid = (payload.get(k) for k in ("session_id", "turn_id", "api_request_id"))
        started = seconds(payload.get("started_at"))
        if not all(isinstance(v, str) and 0 < len(v) <= 512 for v in (sid, tid, rid)):
            return False
        if started is None or started < self.created_at:
            return False
        auxiliary = event in {"pre_auxiliary_call", "post_auxiliary_call"}
        if auxiliary and payload.get("aux_task") != "compression":
            return False
        if not auxiliary and event not in {"pre_api_request", "post_api_request", "api_request_error"}:
            return False
        with self._lock:
            identity = (str(sid), str(tid))
            if self._identity is None:
                if event not in {"pre_api_request", "pre_auxiliary_call"}:
                    return False
                self._identity = identity
            if identity != self._identity:
                return False
            if auxiliary:
                key = (str(rid), started)
                if event == "pre_auxiliary_call":
                    if key in self._aux or len(self._aux) >= 256 or started < max(self._latest, self._resumed_at):
                        return False
                    self._aux[key] = True
                    self._summary_result = ""
                else:
                    if not self._aux.get(key):
                        return False
                    self._aux[key] = False
                    if started >= max(self._latest, self._resumed_at):
                        self._summary_result = (
                            "failed" if payload.get("error_type") or payload.get("error") else "returned"
                        )
                return True
            if started < self._latest:
                return False
            if event == "pre_api_request":
                # Replayed pre events must not reset a more recent stream phase.
                if started == self._latest:
                    return False
                self._latest = started
                provider = label(payload.get("provider"))
                self._route = (
                    (self._provider, provider) if self._provider and provider and provider != self._provider else None
                )
                self._provider = provider
                self._phase = "provider_switch" if self._route else "processing"
                self._summary_result = ""
            elif event == "api_request_error":
                self._phase = "request_error"
            return True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            phase = self._phase
            if any(active and started >= max(self._latest, self._resumed_at)
                   for (_, started), active in self._aux.items()):
                phase = "compression"
            elif self._summary_result:
                phase = "summary_" + self._summary_result
            return {"runtime_phase": phase, "runtime_route": self._route, "compression_observed": bool(self._aux)}


def runtime_signature(data: dict[str, Any]) -> tuple[Any, ...]:
    """Phase changes bypass the display throttle; token/time deltas do not."""
    return data.get("runtime_phase"), data.get("runtime_tool"), data.get("runtime_route")


def build_runtime_footer(
    data: dict[str, Any], *, text_size: str = "notation", details: bool = True,
) -> list[dict[str, Any]]:
    phase = data.get("runtime_phase", "processing")
    color, icon = "blue", "info_outlined"
    title_en, title_zh = "Processing", "处理中"
    note_en, note_zh = "Waiting for the next event; usage pending", "等待后续事件 · 用量待返回"
    if phase == "answer":
        note_en, note_zh = "Receiving the answer; final usage pending", "正在接收回答 · 最终用量待返回"
    elif phase == "thinking":
        title_en, title_zh = "Thinking", "正在思考"
        note_en, note_zh = "Receiving reasoning; final usage pending", "正在接收思考 · 最终用量待返回"
    elif phase == "tool":
        title_en, title_zh, icon = "Running tools", "执行工具", "setting_outlined"
        name, done = safe(data.get("runtime_tool")) or "tool", count(data.get("runtime_tools_done")) or 0
        note_en, note_zh = f"Current: {name} · {done} completed", f"当前：{name} · {done} 项已完成"
    elif phase == "waiting":
        title_en, title_zh, color = "Waiting for confirmation", "等待确认", "orange"
        note_en, note_zh = "Use Hermes's confirmation prompt to continue", "请在 Hermes 的确认消息中操作后继续"
    elif phase == "compression":
        title_en, title_zh, color = "Organizing context", "整理上下文", "orange"
        note_en, note_zh = "Summary request running; commit pending", "摘要请求进行中 · 压缩提交待确认"
    elif phase == "summary_returned":
        title_en, title_zh, color = "Organizing context", "整理上下文", "orange"
        note_en, note_zh = "Summary returned; awaiting the next main request", "摘要已返回 · 等待后续主请求"
    elif phase == "summary_failed":
        title_en, title_zh, color = "Summary request failed", "摘要请求失败", "orange"
        note_en, note_zh = "Waiting for Hermes to decide the next step", "等待 Hermes 决定后续处理"
    elif phase == "provider_switch":
        title_en, title_zh, color = "Provider changed · continuing", "已切换服务商 · 继续处理中", "orange"
        route = data.get("runtime_route")
        note_en = note_zh = " → ".join(safe(v) for v in route) if isinstance(route, (list, tuple)) else ""
    elif phase == "request_error":
        title_en, title_zh, color = "Request failed · awaiting next step", "请求失败 · 等待后续处理", "orange"
        note_en, note_zh = "Existing answer retained; turn not yet final", "已有回答保留 · 本轮尚未结束"
        if error_type := safe(data.get("last_error_type")):
            note_en, note_zh = f"{error_type} · turn not yet final", f"{error_type} · 本轮尚未结束"
    duration = seconds(data.get("duration"))
    elapsed = f" · {duration:.0f}s" if duration is not None else ""
    # Two native markdown lines retain the nonempty, stable insertion anchor.
    status = markdown(
        f"<font color='{color}'>**{title_en}{elapsed}**</font>\n<font color='grey'>{note_en}</font>",
        f"<font color='{color}'>**{title_zh}{elapsed}**</font>\n<font color='grey'>{note_zh}</font>", text_size,
    )
    status.update(element_id=LOADING_ID, icon={"tag": "standard_icon", "token": icon, "color": color})
    elements = [status]
    if details:
        panel = build_footer(data, text_size=text_size)[-1]
        panel["elements"].append(markdown(
            "Live values cover completed API attempts; not the final turn total.",
            "运行中统计仅含已返回请求，尚非本轮最终总量。", "notation",
        ))
        elements.append(panel)
    return elements


def runtime_actions(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions = []
    for element in elements:
        if element.get("element_id") not in {LOADING_ID, DETAILS_ID}:
            continue
        # Updating contents only keeps the reader's expanded/collapsed state.
        update = ({"elements": element["elements"]} if element["element_id"] == DETAILS_ID
                  else {k: element[k] for k in ("content", "i18n_content", "icon")})
        actions.append({"action": "partial_update_element", "params": {
            "element_id": element["element_id"], "partial_element": update,
        }})
    return actions
