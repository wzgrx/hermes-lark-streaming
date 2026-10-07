"""Mutable per-message state. Plain data plus a few derived views; no I/O."""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from ..card.model import Footer, Phase, Section, TurnView
from ..transport.channel import CardChannel
from ..transport.flush import Flusher
from ..transport.guard import UnavailableGuard
from ..transport.images import ImageResolver
from ..transport.ledger import DeliveryStatus
from .tools import ToolTracker

if TYPE_CHECKING:
    from concurrent.futures import Future as ConcurrentFuture

    from ..transport.client import CardKitClient


class State(StrEnum):
    IDLE = "idle"
    CREATING = "creating"
    STREAMING = "streaming"
    PAUSED = "paused"  # clarify/approval prompt owns the conversation; body updates wait
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"

    @property
    def terminal(self) -> bool:
        return self in {State.COMPLETED, State.FAILED, State.ABORTED}


@dataclass(eq=False)
class Session:
    message_id: str
    chat_id: str
    loop: asyncio.AbstractEventLoop
    anchor_id: str | None = None
    session_key: str | None = None
    state: State = State.IDLE
    created_at: float = field(default_factory=time.time)
    started: float = field(default_factory=time.monotonic)

    # content of the whole turn, across cards
    tracker: ToolTracker = field(default_factory=ToolTracker)
    thoughts: str = ""
    answers: list[str] = field(default_factory=list)
    raw_answer: list[str] = field(default_factory=list)  # un-stripped deltas: MEDIA: directives live here
    last_was_answer: bool = False
    notices: list[str] = field(default_factory=list)
    tag: str = ""
    provider: str = ""  # provider the turn is actually using, once telemetry has seen a request
    telemetry: Any = None  # details.TurnTelemetry
    final_footer: Footer | None = None
    sections: tuple[Section, ...] = ()
    elapsed_s: float | None = None

    # what earlier (sealed) cards already show
    steps_offset: int = 0
    steps_offset_failed: int = 0
    answers_offset: int = 0
    thoughts_offset: int = 0

    # the card being written
    client: CardKitClient | None = None
    channel: CardChannel | None = None
    card_msg_id: str | None = None
    card_started: float = 0.0  # monotonic
    pushed: dict[str, str] = field(default_factory=dict)  # element_id -> signature last sent
    inserted: set[str] = field(default_factory=set)  # optional elements that exist server side
    process_expanded: bool | None = None
    streaming_closed: bool = False
    stream_retry_after: float = 0.0
    stream_failures: int = 0
    rollover_retry_after: float = 0.0
    handoff_in_progress: bool = False
    recovery_attempts: int = 0

    # delivery bookkeeping
    create_task: asyncio.Future[Any] | ConcurrentFuture[Any] | None = None
    delivery_key: str = ""
    delivery_generation: int = 0
    delivery_status: DeliveryStatus = DeliveryStatus.NOT_SENT
    delivery_notice_sent: bool = False
    ledger_unavailable: bool = False
    pending_split: str = ""  # "clarify" | "approval": seal after the interactive tool returns
    timer: asyncio.Task[None] | None = None

    flusher: Flusher | None = None
    guard: UnavailableGuard | None = None
    images: ImageResolver | None = None

    reviews: list[tuple[str, Any]] = field(default_factory=list)
    reviews_closed: bool = False
    reviews_lock: threading.Lock = field(default_factory=threading.Lock)
    lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def has_card(self) -> bool:
        return self.channel is not None

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def mark_failed(self) -> None:
        self.state = State.FAILED

    def raw_text(self) -> str:
        return "".join(self.raw_answer)

    def add_answer(self, text: str) -> None:
        """Append to the open answer segment, or start a new one after a tool/thought."""
        if self.answers and self.last_was_answer:
            self.answers[-1] += text
        else:
            self.answers.append(text)
        self.last_was_answer = True

    def view(self, phase: Phase, *, continued: bool = False, finished: bool = False) -> TurnView:
        """Snapshot for the renderer: only what the *current* card owns."""
        steps = self.tracker.steps()
        # tracker.count includes archived steps; ``steps_offset`` indexes live records only.
        shown = steps[max(0, self.steps_offset - self.tracker.archived):]
        footer = self.final_footer if finished and self.final_footer is not None else _live_footer(self)
        return TurnView(
            phase=phase,
            elapsed_s=self.elapsed_s if finished and self.elapsed_s is not None else self.elapsed,
            steps=shown,
            steps_before=self.steps_offset,
            steps_before_failed=self.steps_offset_failed,
            thoughts=self.thoughts[self.thoughts_offset:],
            answers=tuple(self.answers[self.answers_offset:]),
            notices=tuple(self.notices),
            footer=footer if not self.tag else _tagged(footer, self.tag),
            sections=self.sections if finished else (),
            continued=continued,
            card_key=hashlib.sha1(f"{self.message_id}:{self.delivery_generation}".encode()).hexdigest()[:6],
        )


def _live_footer(session: Session) -> Footer:
    telemetry = session.telemetry
    return telemetry.footer() if telemetry is not None else Footer()


def _tagged(footer: Footer, tag: str) -> Footer:
    from dataclasses import replace

    return replace(footer, tag=tag)
