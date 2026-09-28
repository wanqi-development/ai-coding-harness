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

from ai_coding_harness.safety.permissions import CommandSafety
from ai_coding_harness.safety.policy import AgentPolicy


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
        self, policy: AgentPolicy, safety: CommandSafety | None = None
    ) -> None:
        self._policy = policy
        self._safety = safety

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
        reason = self._refusal(name, args if isinstance(args, Mapping) else {})
        if reason is None:
            return await handler(request)
        return ToolMessage(
            content=f"{reason}。本轮未执行，请换一种做法。",
            name=name or "unknown",
            tool_call_id=call.get("id") or "",
            status="error",
        )

    def _refusal(self, name: str, args: Mapping[str, Any]) -> str | None:
        if name == "execute" and self._safety is not None:
            command = args.get("command")
            if isinstance(command, str):
                reason = self._safety.check(command)
                if reason is not None:
                    return reason
        return self._policy.check_tool(name, args)
