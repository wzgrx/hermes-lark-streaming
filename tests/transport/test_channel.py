from __future__ import annotations

import asyncio

import pytest

from hermes_lark_streaming.transport.channel import CardChannel


@pytest.mark.asyncio
async def test_write_allocates_increasing_sequence_and_commits_on_success() -> None:
    channel = CardChannel("c")
    seen: list[int] = []

    async def op(seq: int) -> str:
        seen.append(seq)
        return "ok"

    assert await channel.write(op) == "ok"
    await channel.write(op)
    assert seen == [2, 3]
    assert channel.sequence == 3


@pytest.mark.asyncio
async def test_failed_write_does_not_commit_and_retry_reuses_sequence() -> None:
    channel = CardChannel("c", sequence=5)
    seen: list[int] = []

    async def boom(seq: int) -> None:
        seen.append(seq)
        raise RuntimeError("x")

    async def fine(seq: int) -> None:
        seen.append(seq)

    with pytest.raises(RuntimeError):
        await channel.write(boom)
    await channel.write(fine)
    assert seen == [6, 6]
    assert channel.sequence == 6


@pytest.mark.asyncio
async def test_writes_are_mutually_exclusive() -> None:
    channel = CardChannel("c")
    running = 0
    peak = 0

    async def op(seq: int) -> int:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.005)
        running -= 1
        return seq

    results = await asyncio.gather(*(channel.write(op) for _ in range(6)))
    assert peak == 1
    assert results == [2, 3, 4, 5, 6, 7]


@pytest.mark.asyncio
async def test_advance_burns_a_sequence() -> None:
    channel = CardChannel("c")
    assert await channel.advance() == 2

    async def op(seq: int) -> int:
        return seq

    assert await channel.write(op) == 3
