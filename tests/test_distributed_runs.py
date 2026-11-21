"""Distributed RunHub and worker transport contracts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ai_coding_harness.server.agent_worker import AgentWorker
from ai_coding_harness.server.distributed_runs import DistributedRunHub
from ai_coding_harness.server.run_queue import AgentJob, RedisRunQueue
from ai_coding_harness.server.run_store import MemoryRunBuffer, MemoryRunLock
from ai_coding_harness.server.runs import RunInProgress


class _Queue:
    def __init__(self) -> None:
        self.reserved: set[str] = set()
        self.jobs: list[dict] = []

    async def has_job(self, session_id: str) -> bool:
        return session_id in self.reserved

    async def reserve(self, session_id: str) -> bool:
        if session_id in self.reserved:
            return False
        self.reserved.add(session_id)
        return True

    async def publish_reserved(self, **kwargs) -> None:
        self.jobs.append(kwargs)

    async def release_marker(self, session_id: str) -> None:
        self.reserved.discard(session_id)


@pytest.mark.asyncio
async def test_distributed_start_only_opens_buffer_and_enqueues() -> None:
    queue = _Queue()
    hub = DistributedRunHub(
        SimpleNamespace(),
        queue=queue,
        buffer=MemoryRunBuffer(),
        lock=MemoryRunLock(),
    )

    await hub.start(
        "session-1",
        SimpleNamespace(public_id="session-1"),
        "hello",
    )

    assert await hub._buffer.exists("session-1")
    assert queue.jobs == [
        {
            "session_id": "session-1",
            "message": "hello",
            "clarification_id": None,
            "choice": None,
            "answers": None,
        }
    ]
    assert await hub.is_running("session-1")

    with pytest.raises(RunInProgress):
        await hub.start(
            "session-1",
            SimpleNamespace(public_id="session-1"),
            "duplicate",
        )


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.stream: list[tuple[str, dict]] = []
        self.acked: list[str] = []

    async def set(self, key, value, **_kwargs):
        if key in self.values:
            return None
        self.values[key] = value
        return True

    async def exists(self, key):
        return int(key in self.values)

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)

    async def xadd(self, stream, fields):
        self.stream.append((stream, fields))
        return f"1-{len(self.stream)}"

    async def xgroup_create(self, *_args, **_kwargs):
        return True

    async def xautoclaim(self, *_args, **_kwargs):
        return ["0-0", []]

    async def xreadgroup(self, *_args, **_kwargs):
        if not self.stream:
            return []
        stream = self.stream.pop(0)
        return [[stream[0], [("1-1", stream[1])]]]

    async def xack(self, _stream, _group, message_id):
        self.acked.append(message_id)


@pytest.mark.asyncio
async def test_redis_queue_deduplicates_session_and_acks_job() -> None:
    redis = _Redis()
    queue = RedisRunQueue(
        redis,
        prefix="harness:",
        stream="jobs",
        group="workers",
    )

    assert await queue.enqueue(session_id="s1", message="hello")
    assert not await queue.enqueue(session_id="s1", message="duplicate")
    job = await queue.claim(consumer="w1", block_ms=1)

    assert job is not None
    assert job.session_id == "s1"
    assert job.message == "hello"
    await queue.ack(session_id="s1", message_id=job.message_id)
    assert redis.acked == ["1-1"]
    assert not await queue.has_job("s1")


@pytest.mark.asyncio
async def test_claimed_active_job_is_not_reclaimed_while_heartbeat_exists() -> None:
    class _ActiveRedis(_Redis):
        async def set(self, key, value, **kwargs):
            self.values[key] = value
            return True

        async def xautoclaim(self, *_args, **_kwargs):
            return [
                "0-0",
                [
                    (
                        "1-1",
                        {
                            b"job": (
                                b'{"session_id":"s1","message":"hello",'
                                b'"clarification_id":null,"choice":null,"answers":null}'
                            )
                        },
                    )
                ],
            ]

    redis = _ActiveRedis()
    queue = RedisRunQueue(
        redis,
        prefix="harness:",
        stream="jobs",
        group="workers",
    )
    await queue.activate("s1", consumer="worker-old")

    assert await queue.claim(consumer="worker-new", block_ms=1) is None


@pytest.mark.asyncio
async def test_worker_starts_run_and_acks_completed_job() -> None:
    class _WorkerQueue:
        def __init__(self):
            self.acked = False
            self.given = False

        async def claim(self, **_kwargs):
            if self.given:
                await asyncio.sleep(0)
                return None
            self.given = True
            return AgentJob(
                message_id="1-1",
                session_id="s1",
                message="hello",
            )

        async def ack(self, **_kwargs):
            self.acked = True

    class _Runs:
        def __init__(self):
            self.started = False
            self.waited = False

        async def start(self, *_args, **kwargs):
            self.started = kwargs["open_buffer"] is False

        async def wait(self, _session_id):
            self.waited = True

        async def fail_external_run(self, *_args):
            raise AssertionError("worker should succeed")

    queue = _WorkerQueue()
    runs = _Runs()

    async def load_session(_session_id):
        return SimpleNamespace(public_id="s1")

    worker = AgentWorker(
        settings=SimpleNamespace(agent_worker_concurrency=1, run_poll_interval_seconds=0.01),
        runs=runs,  # type: ignore[arg-type]
        queue=queue,  # type: ignore[arg-type]
        session_loader=load_session,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))
    for _ in range(100):
        if queue.acked:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout=2)

    assert runs.started and runs.waited and queue.acked
