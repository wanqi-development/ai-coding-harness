"""空转刹车：同一工具、同一参数在本轮反复调用时，先劝、再停。

缘起：2026-09-21 一轮编码任务跑了 686 秒，日志里从头到尾是 ``execute git status`` 与
同一份 ``write_todos`` —— 模型每一圈都得到「同样的输入、同样的输出」，却把这当成进展
接着转。提示词层面的「别重复」拦不住这种循环，得在执行前数。

计的是**本轮出现次数**，不要求相邻：那次事故里 ``git status`` 与 ``git log`` 是交错的，
只数「连续重复」会漏。

计数**按执行主角分账**（``LoopGuardMiddleware`` 的 ``agent`` 那格）：主控与三个子智能体
各数各的。共用一份的后果实测过（2026-10-16 真机）：``TurnState`` 经 ``configurable`` 注入，
而上游会把父级 configurable 原样传进子智能体（``deepagents/middleware/subagents.py`` 的
``task``），于是一个 coder 把某条探针命令的额度用满之后，**下一个 coder 第一次调用就直接
触发中止**；主控只看到「工具 task 执行失败」，换个说法再派一次 —— 那轮 4 分钟里连派了
20 次。同一角色的多次派发**仍然合账**，那正是要拦的（同一件事反复派同一个角色）。

拦下与中止各记一条 WARNING（署名就是 ``agent`` 那格）：这两条路以前**一行日志都没有**，
模型那头只看得到一句「同样的调用不会有新结果」，事后排查连「是哪条工具、第几次」都无从查起。

- 第 N 次（``LOOP_GUARD_REPEATS``，默认 3）：**不执行**这次调用，回一条 ``status="error"``
  的 ``ToolMessage``。与 ``boundary.py`` 同一个取舍 —— 模型看得到理由、能换做法，
  图也不会因为一个被拒的调用塌掉；这一条不再经过内侧的脱敏中间件，理由里没有凭据值。
- 第 N+1 次：抛 ``TurnStop``，本轮到此为止（service 层收下，补一条中断记录）。

阈值 0 ＝ 关掉。``TurnState`` 由 server 层每轮建一个、经 ``configurable`` 注入；**没有
state 就整层直通**（单测、脚本直接跑图时不受影响）。

只实现异步版：服务端统一走 ``astream_events``。子智能体不继承主智能体的中间件，
三个角色各自要挂一份（见 ``subagents.py``）。

「派子智能体前必须先写清单」不在这里 —— 那是**执行前置条件**，不是空转刹车，
见 ``delegation_gate.py``。两者共用同一份 ``TurnState``，但开关与职责各自独立。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from ai_coding_harness.observability.logging import MAIN_AGENT
from ai_coding_harness.tools.context import get_configurable

logger = logging.getLogger(__name__)

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
        #: 能力交接内层那一轮把用量交回来（runner 在拿到 ``AgentRunLog`` 后挂上）；
        #: 没有它（单测 / 非服务路径）就只记日志、不并账。
        self.usage_sink: Callable[[Mapping[str, Any]], None] | None = None
        #: 被守卫记下的中止（守卫抛出的那个异常对象本身）。service 层在流结束后再看一眼：
        #: 万一某层把异常吃成了 ToolMessage，这一轮也得落下来。
        self.aborted: TurnStop | None = None
        #: 已经提醒过写工作笔记的智能体（main / planner / coder / reviewer）：
        #: 90% 提醒线按**各自上下文**各发一次，不能因为主控发过就把子智能体的提醒吃掉。
        self.pretrim_noticed: set[str] = set()

    @property
    def enabled(self) -> bool:
        return self.repeats > 0

    def note_call(self, name: str, args: Any, agent: str = "") -> int:
        """记一次调用，返回它是本轮的第几次（1 起算）。

        ``agent`` 是执行主角（主控 / 某个子智能体角色）：计数按它分账，理由见模块头。
        默认空串＝「没署名」，自成一本账，不与任何角色混算。
        """
        key = f"{agent}\x00{name}\x00{_call_key(args)}"
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

    def __init__(self, *, agent: str = MAIN_AGENT) -> None:
        #: 这一份守卫替谁数（主控还是某个子智能体角色）：既是计数的分账键，也是日志署名。
        #: 子智能体那三份在 ``subagents.py`` 里按角色各建一个实例。
        self._agent = agent

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        state = get_configurable("turn_state")
        if not isinstance(state, TurnState):
            return await handler(request)

        call = request.tool_call
        name = str(call.get("name") or "")
        if not state.enabled:
            return await handler(request)

        count = state.note_call(name, call.get("args"), self._agent)

        if count < state.repeats:
            return await handler(request)
        if count == state.repeats:
            logger.warning(
                "循环守卫拦下 `%s`：本轮第 %d 次同样的调用（阈值 %d），不执行，回一条错误响应",
                name or "该工具",
                count,
                state.repeats,
                extra={"agent": self._agent},
            )
            return ToolMessage(
                content=_warning(name or "该工具", count),
                name=name or "unknown",
                tool_call_id=call.get("id") or "",
                status="error",
            )
        logger.warning(
            "循环守卫中止本轮：`%s` 本轮第 %d 次同样的调用（阈值 %d）",
            name or "该工具",
            count,
            state.repeats,
            extra={"agent": self._agent},
        )
        raise state.abort(name, count)
