"""Separate Agent worker process consuming Redis Stream jobs."""

from __future__ import annotations

import asyncio
import logging
import os
import socket

from ai_coding_harness.config import Settings
from ai_coding_harness.server.run_queue import AgentJob, RedisRunQueue
from ai_coding_harness.server.runs import RunHub, RunInProgress

logger = logging.getLogger(__name__)

WORKER_ERROR = "Agent worker 无法启动本轮，请稍后重试。"


class AgentWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        runs: RunHub,
        queue: RedisRunQueue,
        session_loader,
        refresh_settings=None,
    ) -> None:
        self._settings = settings
        self._runs = runs
        self._queue = queue
        self._load_session = session_loader
        self._refresh_settings = refresh_settings
        self._refresh_lock = asyncio.Lock()
        self.consumer = (
            f"{socket.gethostname()}-{os.getpid()}-{id(self):x}"
        )

    async def run(self, stop: asyncio.Event) -> None:
        logger.info(
            "Agent worker 已启动：consumer=%s concurrency=%d",
            self.consumer,
            self._settings.agent_worker_concurrency,
        )
        slots = asyncio.Semaphore(self._settings.agent_worker_concurrency)
        tasks: set[asyncio.Task] = set()
        while not stop.is_set():
            await slots.acquire()
            if stop.is_set():
                slots.release()
                break
            job = await self._queue.claim(
                consumer=self.consumer,
                block_ms=int(self._settings.run_poll_interval_seconds * 1000),
            )
            if job is None:
                slots.release()
                continue
            task = asyncio.create_task(
                self._execute(job, slots),
                name=f"agent-job:{job.session_id}:{job.message_id}",
            )
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Agent worker 已停止：consumer=%s", self.consumer)

    async def _execute(self, job: AgentJob, slots: asyncio.Semaphore) -> None:
        heartbeat = asyncio.create_task(
            self._heartbeat(job.session_id),
            name=f"agent-job-heartbeat:{job.session_id}",
        )
        try:
            if self._refresh_settings is not None:
                async with self._refresh_lock:
                    await self._refresh_settings()
            session = await self._load_session(job.session_id)
            if session is None:
                await self._runs.fail_external_run(job.session_id, WORKER_ERROR)
                await self._queue.ack(
                    session_id=job.session_id,
                    message_id=job.message_id,
                )
                return
            try:
                await self._runs.start(
                    job.session_id,
                    session,
                    job.message,
                    clarification_id=job.clarification_id,
                    choice=job.choice,
                    answers=job.answers,
                    open_buffer=False,
                )
            except RunInProgress:
                logger.warning(
                    "重复 Agent job 遇到运行权占用，稍后重投：session=%s msg=%s",
                    job.session_id,
                    job.message_id,
                )
                return
            try:
                await self._runs.wait(job.session_id)
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise
                # The run itself was cancelled by the user. The RunHub already
                # persisted the interrupted record; acknowledge the job so it
                # is not replayed.
            await self._queue.ack(
                session_id=job.session_id,
                message_id=job.message_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Agent job 执行失败：session=%s msg=%s",
                job.session_id,
                job.message_id,
            )
            await self._runs.fail_external_run(job.session_id, WORKER_ERROR)
            await self._queue.ack(
                session_id=job.session_id,
                message_id=job.message_id,
            )
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            slots.release()

    async def _heartbeat(self, session_id: str) -> None:
        while True:
            await asyncio.sleep(30)
            await self._queue.touch(session_id, consumer=self.consumer)


__all__ = ["AgentWorker"]
