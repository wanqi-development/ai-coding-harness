"""一轮运行的公共状态与中止信号。

`TurnState` 不是某个中间件的私有配置：图、工具、主控、子智能体和 server 收尾都会读它。
因此它放在 agent 层，不放在 ``agent.middleware`` 里。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

#: 中止这一轮时写进记录正文的标记（与「〔已由用户终止〕」同一位置、同一口径）。
LOOP_ABORT_MARKER = "〔已中止：同一工具重复调用，本轮未取得进展〕"

#: 中断记录 ``extra`` 里区分「哪条路中止的」。
ABORT_REASON = "repeated_tool"


def _call_key(args: Any) -> str:
    """把入参压成一个可比较的字符串。"""
    payload = dict(args) if isinstance(args, Mapping) else {"__args__": args}
    try:
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=repr)
    except (TypeError, ValueError):
        return repr(payload)


class TurnStop(Exception):
    """本轮到此为止 —— 由循环守卫抛出，service 层收下并补一条中断记录。"""

    def __init__(self, marker: str, extra: Mapping[str, Any] | None = None) -> None:
        super().__init__(marker)
        self.marker = marker
        self.extra: dict[str, Any] = dict(extra or {})


class TurnState:
    """一轮的运行时状态：请求、仓库快照、交接、PR、方案与中止标记。

    每轮新建一个，经 ``configurable["turn_state"]`` 注入。工具可以写入本轮产生的事实：

    - ``create_pr`` 成功 / 复用时写 ``pr``；
    - ``storePlan`` 与 ``get_unexecuted_plan`` 写 ``plan_id``。

    这些字段只活一轮，用于后续中间件判断“本轮是否已经建 PR / 正在执行哪份方案”。
    """

    def __init__(
        self,
        *,
        message: str = "",
        history: Sequence[str] = (),
        repeats: int = 5,
        max_handoffs: int = 1,
        rejudge: Callable[[str, Sequence[str]], Awaitable[Any]] | None = None,
        open_prs: Sequence[Mapping[str, Any]] = (),
        pr_state_known: bool = True,
        allowed_intents: Sequence[str] = (),
        plan_id: int | None = None,
        pr: int | None = None,
    ) -> None:
        self.message = message
        self.history: tuple[str, ...] = tuple(history)
        self.repeats = repeats
        self.max_handoffs = max_handoffs
        #: 本轮仓库状态快照：中间件只读，不负责刷新或拦业务。
        self.open_prs: tuple[dict[str, Any], ...] = tuple(dict(pull) for pull in open_prs)
        self.pr_state_known = pr_state_known
        self.allowed_intents: tuple[str, ...] = tuple(allowed_intents)
        #: 本轮真实创建 / 复用的 PR 编号；没有就是 ``None``。
        self.pr: int | None = pr
        #: 本轮正在处理的方案编号；由 plan 工具写入。
        self.plan_id: int | None = plan_id
        #: 重判意图的回调（``handoffTask`` 使用）。
        self.rejudge = rejudge
        self._counts: dict[str, int] = {}
        self._handoffs = 0
        #: 能力交接内层那一轮把用量交回来。
        self.usage_sink: Callable[[Mapping[str, Any]], None] | None = None
        #: 被守卫记下的中止。
        self.aborted: TurnStop | None = None
        #: 已经提醒过写工作笔记的智能体。
        self.pretrim_noticed: set[str] = set()

    @property
    def enabled(self) -> bool:
        return self.repeats > 0

    @property
    def has_pr(self) -> bool:
        """本轮已创建 PR，或轮首快照里存在仍在等待收尾的 PR。"""
        return self.pr is not None or bool(self.open_prs)

    def note_call(self, name: str, args: Any, agent: str = "") -> int:
        """记一次调用，返回它是本轮的第几次（1 起算）。"""
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
        """记一次交接；本轮已经交过手了则返回 ``None``。"""
        if self._handoffs >= self.max_handoffs:
            return None
        self._handoffs += 1
        return self._handoffs
