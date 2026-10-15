"""Background jobs the agent detaches from its own turn (``systemd-run``, ``nohup``, ``setsid``, ``… &``).

Hermes tracks the processes its terminal tool starts in the background, but a job launched detached
outlives the turn unseen: on 2026-10-15 one ran 128 parallel requests for an hour on the subscription key
the chat itself uses. Such a launch is shown on the card with a stop button, and the quota watch lists
the ones still running.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

_DETACH = re.compile(
    r"\b(?:systemd-run|nohup|setsid|disown)\b"
    r"|\btmux\s+new(?:-session)?\b[^|;&]*\s-d\b"
    r"|\bscreen\s+-d?m"
    r"|(?<![&>|])&\s*(?:$|;|\n)"  # a trailing "cmd &" (not "&&", "2>&1" or "|&")
)
_UNIT = re.compile(r"--unit[= ]([\w@.:-]+)")
_SCRIPT = re.compile(r"([\w./~$-]+\.(?:sh|py|js|ts|rb|pl))\b")
_WORKERS = re.compile(r"(?:--workers|--concurrency|--jobs|-j)[= ]?(\d+)")


@dataclass
class Job:
    chat_id: str
    command: str
    unit: str = ""
    script: str = ""  # basename of the script it runs: how its processes are found again
    workers: int | None = None
    started: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    stopped: bool = False

    @property
    def label(self) -> str:
        return self.unit or self.script or self.command.split()[0][:40]

    def describe(self) -> str:
        parts = [self.label]
        if self.unit and self.script:
            parts.append(self.script)
        if self.workers:
            parts.append(f"{self.workers} 并发")
        return " · ".join(parts)


def detect(command: str, chat_id: str) -> Job | None:
    """A job when ``command`` detaches something from the turn; ``None`` for ordinary commands."""
    if not command or not _DETACH.search(command):
        return None
    unit = _UNIT.search(command)
    script = _SCRIPT.search(command)
    workers = _WORKERS.search(command)
    return Job(
        chat_id=chat_id,
        command=command[:500],
        unit=unit.group(1) if unit else "",
        script=Path(script.group(1)).name if script else "",
        workers=int(workers.group(1)) if workers else None,
    )


async def _run(*argv: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    return proc.returncode or 0, out.decode(errors="replace").strip()


def _processes() -> dict[int, tuple[int, list[str], float]]:
    """``pid -> (parent pid, argv, start time)`` for this user's processes."""
    boot = time.time() - float(Path("/proc/uptime").read_text().split()[0])
    tick = os.sysconf("SC_CLK_TCK")
    table = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            argv = (entry / "cmdline").read_bytes().split(b"\0")
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            table[int(entry.name)] = (int(fields[1]), [a.decode(errors="replace") for a in argv if a],
                                      boot + int(fields[19]) / tick)
        except (OSError, ValueError, IndexError):
            continue
    return table


def _matching_pids(job: Job) -> list[int]:
    """The job's processes and everything they started. A process belongs to the job when one of its
    arguments IS the job's script (``bash …/l3-run-all.sh``), not when a shell line merely mentions it,
    and it started after the job; this gateway and its ancestors are never included."""
    if not job.script:
        return []
    table = _processes()
    protected, pid = set(), os.getpid()
    while pid in table and pid not in protected:  # this process and its ancestors
        protected.add(pid)
        pid = table[pid][0]
    roots = [p for p, (_, argv, start) in table.items()
             if p not in protected and start >= job.started - 10 and any(Path(a).name == job.script for a in argv[1:])]
    found, queue = set(roots), list(roots)
    while queue:  # descendants
        parent = queue.pop()
        for child, (ppid, _, _) in table.items():
            if ppid == parent and child not in found and child not in protected:
                found.add(child)
                queue.append(child)
    return sorted(found)


async def alive(job: Job) -> bool:
    if job.unit:
        _, out = await _run("systemctl", "--user", "is-active", job.unit)
        return out.strip() in {"active", "activating", "reloading"}
    return bool(await asyncio.to_thread(_matching_pids, job))


async def stop(job: Job) -> str:
    """Stop the job; a short Chinese outcome for the confirmation."""
    if job.unit:
        code, out = await _run("systemctl", "--user", "stop", job.unit)
        job.stopped = code == 0
        return f"已停止 {job.unit}" if code == 0 else f"停止 {job.unit} 失败 · {out[:120]}"
    pids = await asyncio.to_thread(_matching_pids, job)
    if not pids:
        job.stopped = True
        return f"{job.label} 已经不在运行"
    for pid in pids:  # the job's own processes only: never a whole process group the agent's shell shares
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue
    job.stopped = True
    return f"已停止 {job.label} · {len(pids)} 个进程"


async def running_units() -> list[tuple[str, str]]:
    """``(unit, description)`` of this user's running ``systemd-run`` services: jobs started detached
    by anyone, the agent included, even before this gateway started."""
    try:
        _, out = await _run("systemctl", "--user", "list-units", "--type=service", "--state=running",
                               "--no-legend", "--plain")
    except (OSError, TimeoutError):
        return []
    units = []
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) == 5 and parts[4].startswith("[systemd-run]"):
            units.append((parts[0].removesuffix(".service"), parts[4][len("[systemd-run]"):].strip()))
    return units
