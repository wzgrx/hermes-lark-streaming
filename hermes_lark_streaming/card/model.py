"""Presentation model: everything the renderer needs, no I/O and no Hermes types.

The session layer fills a :class:`TurnView`; the details modules fill :class:`Section`.
Text fields are plain strings and are escaped by the renderer, never by the producer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Phase(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    STOPPED = "stopped"

    @property
    def terminal(self) -> bool:
        return self is not Phase.RUNNING


class StepStatus(StrEnum):
    RUNNING = "running"
    OK = "ok"
    FAILED = "failed"
    UNCONFIRMED = "unconfirmed"  # turn ended without a final result for this tool


@dataclass(frozen=True, slots=True)
class Step:
    name: str  # tool name, e.g. "Terminal"
    summary: str = ""  # one-line argument preview
    status: StepStatus = StepStatus.RUNNING
    elapsed_ms: float | None = None  # None: start/end pair not observed
    error: str = ""  # failure excerpt, shown only for FAILED


@dataclass(frozen=True, slots=True)
class Metric:
    """One labelled value. ``ratio`` (0..1) renders a text bar; ``hint`` is secondary text."""

    label: str
    value: str
    ratio: float | None = None
    hint: str = ""
    label_en: str = ""


@dataclass(frozen=True, slots=True)
class Section:
    """A block inside the details panel (usage / resources / accounts)."""

    key: str
    title: str
    metrics: tuple[Metric, ...] = ()
    notes: tuple[str, ...] = ()
    title_en: str = ""
    layout: str = "grid"  # "grid": two columns of label/value; "bars": one quota row per metric


@dataclass(frozen=True, slots=True)
class Footer:
    """The one-line summary under the answer."""

    model: str = ""
    context_used: int | None = None
    context_max: int | None = None
    cache_hit: float | None = None  # 0..1
    cache_hit_is_floor: bool = False
    partial: bool = False  # some requests in the turn reported no usage
    tag: str = ""  # identity badge, e.g. the agent name


@dataclass(frozen=True, slots=True)
class TurnView:
    phase: Phase = Phase.RUNNING
    elapsed_s: float | None = None
    steps: tuple[Step, ...] = ()
    steps_before: int = 0  # steps archived on earlier cards of the same turn
    steps_before_failed: int = 0
    thoughts: str = ""  # reasoning text, rendered inside the process panel
    answers: tuple[str, ...] = ()  # answer segments in arrival order
    notices: tuple[str, ...] = ()  # e.g. "继续自上一张卡片"
    footer: Footer = field(default_factory=Footer)
    sections: tuple[Section, ...] = ()
    continued: bool = False  # card rolled over; more cards follow, so no terminal chrome
    card_key: str = ""  # short per-card token; keeps panel ids unique so clients don't carry state across cards

    @property
    def failed_steps(self) -> int:
        return self.steps_before_failed + sum(s.status is StepStatus.FAILED for s in self.steps)

    @property
    def total_steps(self) -> int:
        return self.steps_before + len(self.steps)

    @property
    def finished_steps(self) -> int:
        done = sum(s.status is not StepStatus.RUNNING for s in self.steps)
        return self.steps_before + done


@dataclass(frozen=True, slots=True)
class RenderOptions:
    text_size: str = "normal_v2"
    width_mode: str = "default"
    process: str = "auto"  # auto: open while running or when a step failed; open; closed
    show_process: bool = True
    show_details: bool = True
    max_steps: int = 12  # rendered rows; older ordinary steps collapse into a count
