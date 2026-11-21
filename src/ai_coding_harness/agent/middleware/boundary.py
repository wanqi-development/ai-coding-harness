"""按智能体的边界：不给它不该用的工具，拦下越界的调用。

两层看的是同一份策略（``safety/policy.py``），缺一层都能绕过去：

- ``awrap_model_call`` 把该角色不该用的工具从 ``request.tools`` 里摘掉 —— 模型**看不见**
  就不会选。与 ``profiles.py`` 开头那段是同一个取舍：撤掉选项比提示词劝阻有效。
- ``awrap_tool_call`` 在真要执行时再判一次。前一层管不住三件事：历史里带回来的调用、
  模型凭记忆硬写的调用名、以及 ``execute`` 里那条命令**往哪儿写**（工具名没错、参数越界）。
  这三种都只有在执行前拦。

拦下来返回一条 ``status="error"`` 的 ``ToolMessage``（与 ``middleware/tool_integrity.py``
同一条理由）：模型看得到理由、能换一种做法，图也不会因为一个被拒的调用塌掉。这条消息在
我们这一层就返回了，不再经过内侧的 ``RedactionMiddleware`` —— 理由里只有路径与规则名，
没有凭据值，本来也不需要脱敏。

只实现异步版：服务端统一走 ``astream_events``。子智能体不继承主智能体的中间件，
三个角色各自要挂一份（见 ``subagents.py``）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from ai_coding_harness.agent.turn_state import TurnStop
from ai_coding_harness.db import dao
from ai_coding_harness.safety.git_approval import (
    GitApprovalContext,
    create_git_approval,
    interrupt_payload,
    wait_for_decision,
)
from ai_coding_harness.safety.git_policy import (
    configured_protected_branches,
    evaluate_git_command,
)
from ai_coding_harness.safety.permissions import CommandSafety
from ai_coding_harness.safety.policy import AgentPolicy
from ai_coding_harness.tools.context import get_configurable


def _tool_name(tool: Any) -> str:
    """工具名。``request.tools`` 里 ``BaseTool`` 与裸 dict 两种形态都可能出现。"""
    if isinstance(tool, Mapping):
        return str(tool.get("name") or "")
    return str(getattr(tool, "name", "") or "")


class BoundaryMiddleware(AgentMiddleware):
    """把某个智能体的活动范围落到实处。

    ``safety`` 只对 ``execute`` 生效（其它工具没有命令文本）：全局底线在
    ``OpenSandboxBackend.aexecute`` 也有一份，那里是最后一道，这里是能给出**可读理由**的
    那道 —— 在中间件拒绝，模型收到的是一条能照着改的错误消息，而不是一段看起来像命令
    输出的文字（后端把拒绝理由塞在 ``ExecuteResponse.output`` 里）。
    """

    def __init__(
        self,
        policy: AgentPolicy,
        safety: CommandSafety | None = None,
        approval: GitApprovalContext | None = None,
    ) -> None:
        self._policy = policy
        self._safety = safety
        self._approval = approval

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | ExtendedModelResponse:
        hidden = self._policy.hidden_tools
        if hidden:
            kept = [tool for tool in request.tools if _tool_name(tool) not in hidden]
            if len(kept) != len(request.tools):
                request = request.override(tools=kept)
        return await handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        call = request.tool_call
        name = str(call.get("name") or "")
        args = call.get("args")
        args = args if isinstance(args, Mapping) else {}
        reason = self._policy.check_tool(name, args)
        if reason is None:
            command = args.get("command") if name == "execute" else None
            if isinstance(command, str):
                if self._safety is not None:
                    reason = self._safety.check(command)
                if reason is None:
                    git = evaluate_git_command(
                        command,
                        repo_root=(
                            self._approval.repo_dir
                            if self._approval is not None
                            else self._policy.project_root
                        ),
                        protected_branches=configured_protected_branches(),
                    )
                    if git.level == "deny":
                        reason = git.reason
                    elif git.level == "approval":
                        return await self._approval_result(
                            request,
                            handler,
                            command=command,
                            reason=git.reason,
                        )
            if reason is None:
                return await handler(request)
        return ToolMessage(
            content=f"{reason}。本轮未执行，请换一种做法。",
            name=name or "unknown",
            tool_call_id=call.get("id") or "",
            status="error",
        )

    async def _approval_result(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
        *,
        command: str,
        reason: str,
    ) -> ToolMessage | Command[Any]:
        if self._approval is None:
            return self._tool_error(
                request,
                "该命令需要人工审批，但当前运行没有审批上下文",
            )
        decision = evaluate_git_command(
            command,
            repo_root=self._approval.repo_dir,
            protected_branches=configured_protected_branches(),
        )
        thread_id = str(get_configurable("thread_id") or "")
        call_id = str(request.tool_call.get("id") or "")
        try:
            row = await create_git_approval(
                self._approval,
                command=command,
                tool_call_id=call_id,
                thread_id=thread_id,
                decision=decision,
            )
        except Exception as exc:  # noqa: BLE001 - snapshot failure must fail closed
            return self._tool_error(
                request,
                f"需要审批，但无法创建恢复快照，命令未执行：{exc}",
            )
        payload = interrupt_payload(row)
        writer = getattr(getattr(request, "runtime", None), "stream_writer", None)
        if not callable(writer):
            async with self._approval.database.session() as db:
                await dao.resolve_approval(
                    db,
                    session_id=self._approval.session_id,
                    approval_id=row.id,
                    choice="deny",
                )
            return self._tool_error(
                request,
                "该命令需要人工审批，但当前运行无法发送审批请求",
            )
        writer({"kind": "git_approval", "approval": payload})
        response = await wait_for_decision(self._approval, row.id)
        choice = str(response.get("choice") or "")
        if choice == "terminate":
            raise TurnStop(
                "〔已由用户终止：拒绝了需要审批的 Git 命令〕",
                {"aborted": "git_approval_terminated"},
            )
        if choice != "allow":
            return self._tool_error(
                request,
                f"{reason}。用户拒绝执行，请换一种安全做法。",
            )
        async with self._approval.database.session() as db:
            consumed = await dao.consume_approval(
                db,
                session_id=self._approval.session_id,
                approval_id=row.id,
            )
        if not consumed:
            return self._tool_error(
                request,
                "审批已失效或已被使用，命令没有执行；请重新发起审批。",
            )
        return await handler(request)

    @staticmethod
    def _tool_error(request: ToolCallRequest, text: str) -> ToolMessage:
        call = request.tool_call
        return ToolMessage(
            content=text,
            name=str(call.get("name") or "unknown"),
            tool_call_id=call.get("id") or "",
            status="error",
        )
