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
async def test_feishu_rejection_keeps_the_sequence_but_an_unknown_outcome_spends_it() -> None:
    from hermes_lark_streaming.transport.errors import FeishuAPIError

    channel = CardChannel("c", sequence=5)
    seen: list[int] = []

    async def rejected(seq: int) -> None:
        seen.append(seq)
        raise FeishuAPIError("bad request", 99991)  # the server answered: nothing was committed

    async def timed_out(seq: int) -> None:
        seen.append(seq)
        raise TimeoutError  # may have landed after Feishu committed it

    async def fine(seq: int) -> None:
        seen.append(seq)

    with pytest.raises(FeishuAPIError):
        await channel.write(rejected)
    with pytest.raises(TimeoutError):
        await channel.write(timed_out)
    await channel.write(fine)
    assert seen == [6, 6, 7]  # 6 reused after a rejection, 7 after the ambiguous attempt spent 6
    assert channel.sequence == 7


@pytest.mark.asyncio
async def test_sequence_conflict_skips_ahead_and_retries_once() -> None:
    from hermes_lark_streaming.transport.errors import SequenceConflictError

    channel = CardChannel("c", sequence=5)
    seen: list[int] = []

    async def behind_once(seq: int) -> None:
        seen.append(seq)
        if len(seen) == 1:
            raise SequenceConflictError("conflict", 300317)

    await channel.write(behind_once)
    assert seen == [6, 15] and channel.sequence == 15


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
