"""空转刹车：同一工具、同一参数在本轮反复调用时，先劝、再停。

缘起：2026-09-21 一轮编码任务跑了 686 秒，日志里从头到尾是 ``execute git status`` 与
同一份 ``write_todos`` —— 模型每一圈都得到「同样的输入、同样的输出」，却把这当成进展
接着转。提示词层面的「别重复」拦不住这种循环，得在执行前数。

计的是**本轮出现次数**，不要求相邻：那次事故里 ``git status`` 与 ``git log`` 是交错的，
只数「连续重复」会漏。

- 第 N 次（``LOOP_GUARD_REPEATS``，默认 3）：**不执行**这次调用，回一条 ``status="error"``
  的 ``ToolMessage``。与 ``boundary.py`` 同一个取舍 —— 模型看得到理由、能换做法，
  图也不会因为一个被拒的调用塌掉；这一条不再经过内侧的脱敏中间件，理由里没有凭据值。
- 第 N+1 次：抛 ``TurnStop``，本轮到此为止（service 层收下，补一条中断记录）。

阈值 0 ＝ 关掉。``TurnState`` 由 server 层每轮建一个、经 ``configurable`` 注入；**没有
state 就整层直通**（单测、脚本直接跑图时不受影响）。

只实现异步版：服务端统一走 ``astream_events``。子智能体不继承主智能体的中间件，
三个角色各自要挂一份（见 ``subagents.py``）。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from ai_coding_harness.tools.context import get_configurable

#: 中止这一轮时写进记录正文的标记（与「〔已由用户终止〕」同一位置、同一口径）。
LOOP_ABORT_MARKER = "〔已中止：同一工具重复调用，本轮未取得进展〕"

#: 中断记录 ``extra`` 里区分「哪条路中止的」。
ABORT_REASON = "repeated_tool"


def _warning(name: str, count: int) -> str:
    return (
        f"`{name}` 本轮已经用同样的参数调用过 {count} 次（含这次），本次不再执行。"
        "同样的调用不会有新结果：换个做法，或者把卡住的地方、已掌握的情况直接讲给用户。"
    )


def _call_key(args: Any) -> str:
    """把入参压成一个可比较的字符串。``default=repr`` 兜住不可序列化的值。"""
    payload = dict(args) if isinstance(args, Mapping) else {"__args__": args}
    try:
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=repr)
    except (TypeError, ValueError):
        return repr(payload)


class TurnStop(Exception):
    """本轮到此为止 —— 由循环守卫抛出，service 层收下并补一条中断记录。

    继承 ``Exception`` 而不是 ``BaseException``：它是我们自己发起的中止，
    不是取消，不该混进 ``CancelledError`` 那条路。

    ``marker`` 与 ``extra`` 由 ``TurnState`` 现成给出，service 层原样写进记录。
    """

    def __init__(self, marker: str, extra: Mapping[str, Any] | None = None) -> None:
        super().__init__(marker)
        self.marker = marker
        self.extra: dict[str, Any] = dict(extra or {})


class TurnState:
    """一轮的运行时状态：原始请求、历史、重复计数、交接次数、中止标记。

    每轮新建一个（server 层 ``chat()`` 里），经 ``configurable["turn_state"]`` 注入 ——
    智能体是长期存活对象，逐轮变化的东西不能挂在图或中间件实例上。

    ``message`` / ``history`` / ``rejudge`` 都是给 ``handoffTask`` 的：交接要把**用户
    这一轮的原话**（连同最近几轮）交给另一个形态 —— 工具上没有「转述」参数，让模型
    自己转述必丢细节；而重判意图要走模型（分类模型与超时配置都在 server 层），所以
    回调由那边给。内层那一轮跑在自己的线程上，拿不到本轮之前的历史，一并喂进去。
    """

    def __init__(
        self,
        *,
        message: str = "",
        history: Sequence[str] = (),
        repeats: int = 5,
        max_handoffs: int = 1,
        rejudge: Callable[[str, Sequence[str]], Awaitable[Any]] | None = None,
    ) -> None:
        self.message = message
        self.history: tuple[str, ...] = tuple(history)
        self.repeats = repeats
        self.max_handoffs = max_handoffs
        #: 重判意图的回调（``force_llm`` 那条路，超时与模型都在 server 层）。签名
        #: ``(消息, 历史) -> IntentDecision``（读 ``.intent`` / ``.source``）；这里不
        #: import 那个类型 —— 中间件层不该反过来依赖 server 层。
        #: 没有它就拒绝交接：拿关键字口径去猜，正是要纠正的那个错。
        self.rejudge = rejudge
        self._counts: dict[str, int] = {}
        self._handoffs = 0
        #: 被守卫记下的中止（守卫抛出的那个异常对象本身）。service 层在流结束后再看一眼：
        #: 万一某层把异常吃成了 ToolMessage，这一轮也得落下来。
        self.aborted: TurnStop | None = None

    @property
    def enabled(self) -> bool:
        return self.repeats > 0

    def note_call(self, name: str, args: Any) -> int:
        """记一次调用，返回它是本轮的第几次（1 起算）。"""
        key = f"{name}\x00{_call_key(args)}"
        count = self._counts.get(key, 0) + 1
        self._counts[key] = count
        return count

    def abort(self, name: str, count: int) -> TurnStop:
        """记下中止并返回异常对象，由守卫抛出。"""
        stop = TurnStop(
            LOOP_ABORT_MARKER,
            {"aborted": ABORT_REASON, "tool": name, "repeats": count},
        )
        self.aborted = stop
        return stop

    def note_handoff(self) -> int | None:
        """记一次交接；本轮已经交过手了则返回 ``None``（不重复甩锅）。

        返回的是**序号**而不是布尔：内层那一轮跑在自己的线程上，线程 id 拿它拼
        （``…::handoff-1``）。同一轮的第二次交接本来就拒了，但跨轮重跑时序号还得往前走。
        """
        if self._handoffs >= self.max_handoffs:
            return None
        self._handoffs += 1
        return self._handoffs


class LoopGuardMiddleware(AgentMiddleware):
    """按本轮出现次数拦重复调用。阈值读自 ``TurnState``（本身就来自配置项）。"""

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        state = get_configurable("turn_state")
        if not isinstance(state, TurnState) or not state.enabled:
            return await handler(request)

        call = request.tool_call
        name = str(call.get("name") or "")
        count = state.note_call(name, call.get("args"))

        if count < state.repeats:
            return await handler(request)
        if count == state.repeats:
            return ToolMessage(
                content=_warning(name or "该工具", count),
                name=name or "unknown",
                tool_call_id=call.get("id") or "",
                status="error",
            )
        raise state.abort(name, count)
