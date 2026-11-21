"""BoundaryMiddleware pauses approval-level Git commands in the same run."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from langchain.agents.middleware import ToolCallRequest
from langchain_core.messages import ToolMessage

from ai_coding_harness.agent.middleware import boundary as boundary_module
from ai_coding_harness.agent.middleware.boundary import BoundaryMiddleware
from ai_coding_harness.agent.turn_state import TurnStop
from ai_coding_harness.safety.git_approval import GitApprovalContext
from ai_coding_harness.safety.policy import policy_for


class _Db:
    @asynccontextmanager
    async def session(self):
        yield object()


def _request(writer) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={
            "id": "call-1",
            "name": "execute",
            "args": {"command": "git reset --hard"},
        },
        tool=None,
        state=None,
        runtime=SimpleNamespace(stream_writer=writer.append),
    )


@pytest.mark.asyncio
async def test_approval_allow_pauses_and_then_runs_the_same_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer: list[dict] = []

    async def fake_create(*_args, **_kwargs):
        return SimpleNamespace(id="approval-1")

    async def fake_wait(*_args, **_kwargs):
        return {"status": "approved", "choice": "allow"}

    async def fake_consume(*_args, **_kwargs):
        return True

    monkeypatch.setattr(boundary_module, "create_git_approval", fake_create)
    monkeypatch.setattr(
        boundary_module,
        "interrupt_payload",
        lambda _row: {"approval_id": "approval-1", "command": "git reset --hard"},
    )
    monkeypatch.setattr(boundary_module, "wait_for_decision", fake_wait)
    monkeypatch.setattr(boundary_module.dao, "consume_approval", fake_consume)
    ctx = GitApprovalContext(
        database=_Db(),  # type: ignore[arg-type]
        session_id=1,
        repo_dir="/ai_coding/project/repo",
        backend=SimpleNamespace(),  # type: ignore[arg-type]
    )
    middleware = BoundaryMiddleware(
        policy_for("coder"),
        approval=ctx,
    )
    handled: list[ToolCallRequest] = []

    async def handler(request):
        handled.append(request)
        return ToolMessage(
            content="executed",
            name="execute",
            tool_call_id="call-1",
        )

    result = await middleware.awrap_tool_call(_request(writer), handler)

    assert result.content == "executed"
    assert len(handled) == 1
    assert writer[0]["kind"] == "git_approval"
    assert writer[0]["approval"]["command"] == "git reset --hard"


@pytest.mark.asyncio
async def test_approval_deny_returns_tool_error_without_executing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer: list[dict] = []

    async def fake_create(*_args, **_kwargs):
        return SimpleNamespace(id="approval-2")

    async def fake_wait(*_args, **_kwargs):
        return {"status": "denied", "choice": "deny"}

    monkeypatch.setattr(boundary_module, "create_git_approval", fake_create)
    monkeypatch.setattr(
        boundary_module,
        "interrupt_payload",
        lambda _row: {"approval_id": "approval-2", "command": "git reset --hard"},
    )
    monkeypatch.setattr(boundary_module, "wait_for_decision", fake_wait)
    ctx = GitApprovalContext(
        database=_Db(),  # type: ignore[arg-type]
        session_id=1,
        repo_dir="/ai_coding/project/repo",
        backend=SimpleNamespace(),  # type: ignore[arg-type]
    )
    middleware = BoundaryMiddleware(policy_for("coder"), approval=ctx)
    handled = False

    async def handler(_request):
        nonlocal handled
        handled = True
        raise AssertionError("denied command must not run")

    result = await middleware.awrap_tool_call(_request(writer), handler)

    assert handled is False
    assert result.status == "error"
    assert "用户拒绝执行" in result.content


@pytest.mark.asyncio
async def test_terminate_choice_ends_the_current_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_create(*_args, **_kwargs):
        return SimpleNamespace(id="approval-3")

    async def fake_wait(*_args, **_kwargs):
        return {"status": "terminated", "choice": "terminate"}

    monkeypatch.setattr(boundary_module, "create_git_approval", fake_create)
    monkeypatch.setattr(
        boundary_module,
        "interrupt_payload",
        lambda _row: {"approval_id": "approval-3", "command": "git reset --hard"},
    )
    monkeypatch.setattr(boundary_module, "wait_for_decision", fake_wait)
    ctx = GitApprovalContext(
        database=_Db(),  # type: ignore[arg-type]
        session_id=1,
        repo_dir="/ai_coding/project/repo",
        backend=SimpleNamespace(),  # type: ignore[arg-type]
    )
    middleware = BoundaryMiddleware(policy_for("coder"), approval=ctx)

    async def handler(_request):
        raise AssertionError("terminated command must not run")

    with pytest.raises(TurnStop):
        await middleware.awrap_tool_call(_request([]), handler)
