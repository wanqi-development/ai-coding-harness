"""一轮跑到一半失败时，已产出的部分必须留在记录里。

原来只有 ``async for`` 循环**之后**那次 ``_record``，异常一跳过它就永远不执行 ——
浏览器已经逐字渲染出来的内容，刷新后从库里回放就没了。这里钉住补上的那条中断记录：
部分正文 + 中断标记 + 脱敏后的异常文本，三种情况（有正文 / 无正文 / 有 parts）都覆盖。

「用户点终止」走的是同一个出口但另一条分支：取消不是故障，措辞里不出现异常名，
也不分「有产出 / 未产出」两句。

「空转刹车」走第三个分支：守卫判定原地打转，本轮提前收尾 —— 同样是提前结束，
但既不是取消也不是故障，标记另有一套。
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from ai_coding_harness.agent.middleware import (
    ABORT_REASON,
    LOOP_ABORT_MARKER,
    TurnState,
    TurnStop,
)
from ai_coding_harness.config import Settings
from ai_coding_harness.safety.redact import REDACTED
from ai_coding_harness.server import service as service_mod
from ai_coding_harness.server.service import (
    CANCELLED_BY_USER,
    INTERRUPTED_EMPTY,
    INTERRUPTED_PARTIAL,
    HarnessService,
)
from tests.v3_events import (
    ExplodingAgent,
    FakeAgent,
    message_finish,
    message_start,
    todo_delta,
    todo_finish,
    todo_start,
)
from tests.v3_events import (
    text as text_delta,
)

TOKEN = "synth-token-0123456789"


class _FakeDb:
    @asynccontextmanager
    async def session(self):
        yield None


class _FakeManager:
    def __init__(self, agent) -> None:
        self._agent = agent

    def get_agent(self, runtime, session, profile):
        return self._agent

    def thread_id(self, session):
        return "thread-1"


@pytest.fixture
def records(monkeypatch) -> list[dict]:
    """收集落库的 kwargs（``add_record`` 打桩，不连库）。"""
    written: list[dict] = []

    async def fake_add_record(db, **kwargs):
        written.append(kwargs)

    async def no_plan(db, *, session_id):
        return None

    monkeypatch.setattr(service_mod.dao, "add_record", fake_add_record)
    monkeypatch.setattr(service_mod.dao, "get_latest_plan", no_plan)
    return written


async def _drive(
    agent, terms: tuple[str, ...] = (), *, state: TurnState | None = None
) -> list[dict]:
    """跑一轮，把 ``_run_agent`` 发出的事件收下来（抛不抛由用例自己接）。

    ``TurnState`` 平时由 ``chat()`` 每轮新建（循环守卫数重复、``handoffTask`` 重判都用
    它），这里直接调 ``_run_agent`` 就绕过了那一层，得自己补一个同形状的 —— 否则守卫
    整层直通，这几个用例测的就不是真实路径了。
    """
    svc = HarnessService(
        settings=Settings(), manager=_FakeManager(agent), database=_FakeDb()
    )
    session = SimpleNamespace(public_id=uuid.uuid4(), db_id=42)
    runtime = SimpleNamespace(sandbox=None, backend=None, agents={})
    return [
        event
        async for event in svc._run_agent(
            session, runtime, "做点事", "coding", terms,
            state=state if state is not None else TurnState(message="做点事"),
        )
    ]


async def _run_failing(agent, terms: tuple[str, ...] = ()) -> None:
    """跑一轮并断言它确实炸了 —— 异常得原样抛给 ``chat()``，错误帧只由它那一处发。"""
    with pytest.raises(RuntimeError):
        await _drive(agent, terms)


async def _run_cancelled(agent, terms: tuple[str, ...] = ()) -> None:
    """跑一轮并断言 ``CancelledError`` **原样**抛出。

    这条是关键：吞掉它的话 ``RunHub`` 就看不出这是用户终止，pump 会把这一轮当成跑完 ——
    前端「终止」之后按钮不会复位，``is_running`` 也一直为真。
    """
    with pytest.raises(asyncio.CancelledError):
        await _drive(agent, terms)


def _with_text(part: str) -> list[dict]:
    return [message_start(), text_delta(part), message_finish()]


class TestInterruptedRecord:
    async def test_partial_output_survives(self, records):
        """有正文：正文留下，尾上挂标记与错误。"""
        await _run_failing(ExplodingAgent(_with_text("前半段正文"), after=3))

        assert len(records) == 1
        rec = records[0]
        assert rec["type"] == "assistant"
        assert rec["content"].startswith("前半段正文")
        assert INTERRUPTED_PARTIAL in rec["content"]
        assert "模型侧炸了" in rec["content"]
        assert rec["summary"] == "前半段正文"
        assert rec["extra"]["partial"] is True
        assert isinstance(rec["extra"]["elapsed_ms"], int)
        assert rec["extra"]["error"]["type"] == "RuntimeError"
        assert rec["extra"]["intent"] == "coding"
        # 故障路径不带终止标记：两个分支共用一个 extra 拼装，别串味
        assert "cancelled" not in rec["extra"]

    async def test_no_output_still_records(self, records):
        """取流那一步就炸：正文为空，仍然留一条 —— 否则时间线上看不出这轮出过事。"""
        await _run_failing(ExplodingAgent([], after=None))

        assert len(records) == 1
        rec = records[0]
        assert rec["content"].startswith(INTERRUPTED_EMPTY)
        assert INTERRUPTED_PARTIAL not in rec["content"]
        assert rec["summary"] == INTERRUPTED_EMPTY
        assert rec["extra"]["partial"] is True

    async def test_exception_text_is_redacted(self, records):
        """异常文本常把工具返回值整段带上，落库前必须过脱敏。"""
        boom = RuntimeError(f"工具返回里夹着 {TOKEN}")
        await _run_failing(
            ExplodingAgent(_with_text("正文"), after=3, error=boom), (TOKEN,)
        )

        rec = records[0]
        assert TOKEN not in rec["content"]
        assert REDACTED in rec["content"]
        assert TOKEN not in rec["extra"]["error"]["message"]
        assert REDACTED in rec["extra"]["error"]["message"]

    async def test_parts_get_the_marker_appended(self, records):
        """有待办时前端按 parts 回放、content 被忽略，标记得补进块序列尾部。

        只有待办、没有任何正文，也算「已经看到过东西」—— 标记得说部分输出，
        不能说「未产出内容」，否则跟上面那个待办框自相矛盾。
        """
        events = [
            todo_start(),
            todo_delta('{"todos": [{"content": "第一步", "status": "in_progress"}]}'),
            todo_finish([{"content": "第一步", "status": "in_progress"}]),
        ]
        await _run_failing(ExplodingAgent(events, after=3))

        rec = records[0]
        parts = rec["extra"]["parts"]
        assert parts[0]["kind"] == "todos"
        assert parts[-1]["kind"] == "text"
        assert INTERRUPTED_PARTIAL in parts[-1]["content"]
        assert "模型侧炸了" in parts[-1]["content"]
        assert rec["content"].startswith(INTERRUPTED_PARTIAL)
        assert rec["summary"] == INTERRUPTED_PARTIAL

    async def test_healthy_round_is_not_marked_partial(self, records):
        """正常收尾不能被打上中断标记（守 ``partial`` 只出现在异常路径）。"""
        await _drive(FakeAgent(_with_text("整段正文")))

        assert len(records) == 1
        assert records[0]["content"] == "整段正文"
        assert "partial" not in records[0]["extra"]
        assert isinstance(records[0]["extra"]["elapsed_ms"], int)
        assert INTERRUPTED_PARTIAL not in records[0]["content"]


class TestCancelledRecord:
    """用户点了「终止」：同一个出口、另一条分支 —— 是取消，不是故障。"""

    async def test_cancelled_round_keeps_the_partial_output(self, records):
        """已有正文：正文留下，尾巴上补终止标记，**不**带错误行。"""
        await _run_cancelled(
            ExplodingAgent(
                _with_text("前半段正文"), after=3, error=asyncio.CancelledError()
            )
        )

        assert len(records) == 1
        rec = records[0]
        assert rec["type"] == "assistant"
        assert rec["content"] == f"前半段正文\n\n{CANCELLED_BY_USER}"
        assert rec["summary"] == "前半段正文"
        assert rec["extra"]["partial"] is True
        assert isinstance(rec["extra"]["elapsed_ms"], int)
        assert rec["extra"]["cancelled"] is True
        assert rec["extra"]["intent"] == "coding"
        # 「错误：CancelledError」对用户毫无意义，异常信息一律不进这条记录
        assert "error" not in rec["extra"]
        assert "CancelledError" not in rec["content"]
        assert "错误：" not in rec["content"]
        assert INTERRUPTED_PARTIAL not in rec["content"]

    async def test_cancelled_before_any_output_still_records(self, records):
        """一帧都没拿到就终止：仍然留一条 —— 终止就是终止，不再分「有产出 / 未产出」。"""
        await _run_cancelled(
            ExplodingAgent([], after=None, error=asyncio.CancelledError())
        )

        assert len(records) == 1
        rec = records[0]
        assert rec["content"] == CANCELLED_BY_USER
        assert rec["summary"] == CANCELLED_BY_USER
        assert rec["extra"]["cancelled"] is True
        assert INTERRUPTED_EMPTY not in rec["content"]

    async def test_cancelled_with_todos_appends_the_marker_to_parts(self, records):
        """有待办时前端按 parts 回放、content 被忽略，终止标记同样得补进块序列尾部。"""
        events = [
            todo_start(),
            todo_delta('{"todos": [{"content": "第一步", "status": "in_progress"}]}'),
            todo_finish([{"content": "第一步", "status": "in_progress"}]),
        ]
        await _run_cancelled(
            ExplodingAgent(events, after=3, error=asyncio.CancelledError())
        )

        rec = records[0]
        parts = rec["extra"]["parts"]
        assert parts[0]["kind"] == "todos"
        assert parts[-1] == {"kind": "text", "content": CANCELLED_BY_USER}
        assert rec["content"] == CANCELLED_BY_USER
        assert rec["summary"] == CANCELLED_BY_USER


def _stop(tool: str = "execute") -> TurnStop:
    """造一个循环守卫会抛的那种中止（``extra`` 与 ``guard.py`` 里的形状一致）。"""
    return TurnStop(
        LOOP_ABORT_MARKER,
        {"aborted": ABORT_REASON, "tool": tool, "repeats": 4},
    )


class TestLoopAbortRecord:
    """空转刹车：守卫判定原地打转，本轮提前收尾（``middleware/guard.py``）。

    两个入口都要钉住 —— 守卫抛出 ``TurnStop`` 是一条，另一条更隐蔽：异常被某层吃成了
    ``ToolMessage``，``async for`` 正常跑完，于是收尾那步必须**再看一眼** ``state.aborted``。
    不补这一眼的话这一轮会落进「智能体完成」那条正常记录里，被刹停得无声无息。
    """

    async def test_raised_stop_records_marker_and_ends_the_stream(self, records):
        """守卫抛上来了：记录带中止标记，事件流尾上再补一句，前端不用等刷新。"""
        events = await _drive(
            ExplodingAgent(_with_text("前半段正文"), after=3, error=_stop())
        )

        assert len(records) == 1
        rec = records[0]
        assert rec["content"] == f"前半段正文\n\n{LOOP_ABORT_MARKER}"
        assert rec["summary"] == "前半段正文"
        assert rec["extra"]["partial"] is True
        assert isinstance(rec["extra"]["elapsed_ms"], int)
        assert rec["extra"]["aborted"] == ABORT_REASON
        assert rec["extra"]["tool"] == "execute"
        assert rec["extra"]["intent"] == "coding"
        # 中止不是取消、也不是故障：另外两条路的标记都得干净
        assert "cancelled" not in rec["extra"]
        assert "error" not in rec["extra"]

        assert events[-1] == {
            "type": "ai",
            "content": f"前半段正文\n\n{LOOP_ABORT_MARKER}",
        }

    async def test_stop_without_output_still_records(self, records):
        """一帧都没出来就被刹：仍然留一条，否则时间线上看不出这轮出过事。"""
        await _drive(ExplodingAgent([], after=None, error=_stop("write_todos")))

        rec = records[0]
        assert rec["content"] == LOOP_ABORT_MARKER
        assert rec["summary"] == LOOP_ABORT_MARKER
        assert rec["extra"]["aborted"] == ABORT_REASON
        assert rec["extra"]["tool"] == "write_todos"
        assert INTERRUPTED_EMPTY not in rec["content"]

    async def test_swallowed_stop_still_ends_the_turn(self, records):
        """异常没抛到这一层（被吃成了 ToolMessage）：收尾看标记，照样提前收尾。

        假件把整段正文吐完、一帧不落地正常结束 —— 只有 ``state.aborted`` 上留着守卫的
        痕迹。没有那一眼，这一轮就会以「智能体完成」记进库、用户看到一句没有下文的正文。
        """
        state = TurnState(message="做点事")
        state.aborted = _stop()
        events = await _drive(FakeAgent(_with_text("整段正文")), state=state)

        assert len(records) == 1
        rec = records[0]
        assert rec["content"] == f"整段正文\n\n{LOOP_ABORT_MARKER}"
        assert rec["extra"]["aborted"] == ABORT_REASON
        assert events[-1] == {
            "type": "ai",
            "content": f"整段正文\n\n{LOOP_ABORT_MARKER}",
        }

    async def test_healthy_round_is_not_touched_by_the_flag(self, records):
        """没被刹的一轮不受影响（``aborted`` 只在中止路径上有值）。"""
        await _drive(FakeAgent(_with_text("整段正文")))

        assert records[0]["content"] == "整段正文"
        assert "aborted" not in records[0]["extra"]
