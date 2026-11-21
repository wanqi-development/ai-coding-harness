"""Assistant text frames are batched without delaying control/state frames."""

from __future__ import annotations

import asyncio

from ai_coding_harness.server.runs import RunHub
from ai_coding_harness.server.text_batch import TextFrameBatcher
from tests.run_fakes import GatedService, ScriptedService, StubSession, settle


def test_text_is_flushed_at_the_character_threshold() -> None:
    batcher = TextFrameBatcher(max_chars=10, flush_seconds=0)
    ready = []
    for char in "123456789":
        ready.extend(batcher.push({"type": "ai", "content": char}))
    assert ready == []

    ready.extend(batcher.push({"type": "ai", "content": "0"}))
    assert ready == [{"type": "ai", "content": "1234567890"}]


def test_non_text_and_different_runs_are_flush_boundaries() -> None:
    batcher = TextFrameBatcher(max_chars=100, flush_seconds=0)
    assert batcher.push({"type": "ai", "content": "ab"}) == []
    assert batcher.push({"type": "tool", "name": "read_file", "content": ""}) == [
        {"type": "ai", "content": "ab"},
        {"type": "tool", "name": "read_file", "content": ""},
    ]

    assert batcher.push({"type": "ai", "content": "cd", "run_id": "run-1"}) == []
    assert batcher.push({"type": "ai", "content": "ef", "run_id": "run-2"}) == [
        {"type": "ai", "content": "cd", "run_id": "run-1"}
    ]
    assert batcher.flush() == [{"type": "ai", "content": "ef", "run_id": "run-2"}]


def test_one_character_threshold_preserves_unbatched_frames() -> None:
    batcher = TextFrameBatcher(max_chars=1)
    frame = {"type": "ai", "content": "x", "parent_run_id": "p"}
    assert batcher.push(frame) == [frame]


async def test_run_hub_batches_contiguous_text_and_flushes_at_done() -> None:
    service = ScriptedService(
        [{"type": "ai", "content": char} for char in "abcdefghijkl"] + [{"type": "done"}]
    )
    hub = RunHub(
        service,
        text_batch_chars=5,
        text_batch_flush_seconds=0,
    )
    await hub.start("s", StubSession(), "hi")
    frames = [frame async for _, frame in hub.subscribe("s")]

    assert frames == [
        {"type": "ai", "content": "abcde"},
        {"type": "ai", "content": "fghij"},
        {"type": "ai", "content": "kl"},
        {"type": "done"},
    ]


async def test_slow_stream_flushes_partial_text_on_the_timer() -> None:
    service = GatedService([{"type": "ai", "content": "a"}, {"type": "done"}])
    hub = RunHub(
        service,
        text_batch_chars=10,
        text_batch_flush_seconds=0.01,
    )
    await hub.start("s", StubSession(), "hi")
    await service.advance(1)

    stream = hub.subscribe("s")
    assert await asyncio.wait_for(anext(stream), timeout=0.5) == (
        1,
        {"type": "ai", "content": "a"},
    )
    await stream.aclose()
    await service.advance(1)
    await settle()
