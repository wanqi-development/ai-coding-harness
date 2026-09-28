"""v3 事件的构造器，形状照探针实测（``harness_probe/v3_blocks_out.txt``、
``v3_deepagents_out.txt``）。

两处不能凭直觉：``messages`` 的 ``params["data"]`` 是 ``(payload, metadata)`` **二元组**
（``index`` 在载荷顶层），而 ``tools`` 的 ``data`` 是单值且**不带 metadata** —— 工具事件
的归属只能靠 ``params["namespace"]`` 反查。

四个测试模块共用这一份，免得各自的假事件悄悄漂移。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from ai_coding_harness.stream import INTERNAL_CALL_KEY, ToolNames

MAIN_NS: tuple[str, ...] = ()
SUB_NS = ("tools:7b1e4d2a-0000-4000-8000-000000000001",)
# 主智能体在 lc_agent_name 里的占位值：是字符串 "None"，不是 None
MAIN_NAME = "None"


def sub_ns(call_id: str) -> tuple[str, ...]:
    """子智能体的 namespace：``("tools:<派发它的那次 task 调用的 id>",)``。

    树靠这个 id 把子清单挂到主清单的某一项下，所以造事件时它必须与 ``tool_start``
    的 ``call_id`` 对上 —— 用 ``SUB_NS`` 那种假 uuid 是挂不上的。
    """
    return (f"tools:{call_id}",)


def messages(
    name: str,
    *,
    ns: tuple[str, ...] = MAIN_NS,
    payload: dict[str, Any] | None = None,
    agent: str = MAIN_NAME,
    index: int | None = None,
    internal: bool = False,
) -> dict:
    """一条 ``messages`` 事件。

    ``internal=True`` 造的是**中间件自己发起**的模型调用（摘要）：真身上那个键的值是
    一次性随机 token，我们只认键在不在，所以这里随便给个值。
    """
    body: dict[str, Any] = {"event": name}
    body.update(payload or {})
    if index is not None:
        body["index"] = index
    meta = {"langgraph_node": "model", "lc_agent_name": agent}
    if internal:
        meta[INTERNAL_CALL_KEY] = "probe-token"
    return {"method": "messages", "params": {"namespace": list(ns), "data": (body, meta)}}


def message_start(
    *, ns: tuple[str, ...] = MAIN_NS, agent: str = MAIN_NAME, internal: bool = False
) -> dict:
    return messages(
        "message-start", ns=ns, agent=agent, internal=internal, payload={"role": "ai", "id": ""}
    )


def message_finish(
    *,
    ns: tuple[str, ...] = MAIN_NS,
    agent: str = MAIN_NAME,
    usage: dict | None = None,
    internal: bool = False,
) -> dict:
    return messages(
        "message-finish",
        ns=ns,
        agent=agent,
        internal=internal,
        payload={"usage": usage} if usage else None,
    )


def text(
    part: str,
    *,
    ns: tuple[str, ...] = MAIN_NS,
    agent: str = MAIN_NAME,
    index: int = 0,
    internal: bool = False,
) -> dict:
    """一段正文分片。"""
    return messages(
        "content-block-delta",
        ns=ns,
        agent=agent,
        internal=internal,
        payload={"delta": {"type": "text-delta", "text": part}},
        index=index,
    )


def reasoning(
    part: str, *, ns: tuple[str, ...] = MAIN_NS, agent: str = MAIN_NAME, index: int = 0
) -> dict:
    """一段推理分片：v3 用 ``reasoning-delta``，天然不会被当成正文。"""
    return messages(
        "content-block-delta",
        ns=ns,
        agent=agent,
        payload={"delta": {"type": "reasoning-delta", "text": part}},
        index=index,
    )


def todo_start(
    *, ns: tuple[str, ...] = MAIN_NS, agent: str = MAIN_NAME, index: int = 1, call_id: str = "c1"
) -> dict:
    return messages(
        "content-block-start",
        ns=ns,
        agent=agent,
        index=index,
        payload={
            "content": {"type": "tool_call_chunk", "id": call_id, "name": "write_todos", "args": ""}
        },
    )


def todo_delta(
    args: str, *, ns: tuple[str, ...] = MAIN_NS, agent: str = MAIN_NAME, index: int = 1
) -> dict:
    """一片 ``write_todos`` 参数。``args`` 是**累加值**，不是增量。"""
    return messages(
        "content-block-delta",
        ns=ns,
        agent=agent,
        index=index,
        payload={
            "delta": {
                "type": "block-delta",
                "fields": {
                    "type": "tool_call_chunk",
                    "name": "write_todos",
                    "args": args,
                    "index": 0,
                },
            }
        },
    )


def todo_finish(
    todos: list[dict],
    *,
    ns: tuple[str, ...] = MAIN_NS,
    agent: str = MAIN_NAME,
    index: int = 1,
    call_id: str = "c1",
) -> dict:
    """收尾事件：``args`` 已经是解析好的 dict。"""
    return messages(
        "content-block-finish",
        ns=ns,
        agent=agent,
        index=index,
        payload={
            "content": {
                "type": "tool_call",
                "id": call_id,
                "name": "write_todos",
                "args": {"todos": todos},
            }
        },
    )


def tool_start(
    name: str,
    *,
    ns: tuple[str, ...] = MAIN_NS,
    call_id: str = "c1",
    input: Any = None,  # 字段名就是 input
) -> dict:
    return {
        "method": "tools",
        "params": {
            "namespace": list(ns),
            "data": {
                "event": "tool-started",
                "tool_call_id": call_id,
                "tool_name": name,
                "input": input,
            },
        },
    }


def tool_finish(
    *, ns: tuple[str, ...] = MAIN_NS, call_id: str = "c1", output: Any = "ok"
) -> dict:
    """收尾事件：**没有** ``tool_name``，只能靠 ``tool_call_id`` 配对。"""
    return {
        "method": "tools",
        "params": {
            "namespace": list(ns),
            "data": {"event": "tool-finished", "tool_call_id": call_id, "output": output},
        },
    }


async def aiter(events: list[dict]) -> AsyncIterator[dict]:
    for event in events:
        yield event


class FakeAgent:
    """``astream_events`` 在 v3 返回的是**协程**，await 出来才是流 —— 假货也得照做。"""

    def __init__(self, events: list[dict]) -> None:
        self._events = events

    async def astream_events(self, *args: Any, **kwargs: Any) -> AsyncIterator[dict]:
        return aiter(self._events)


class ExplodingAgent:
    """跑到一半炸掉的假件：``after`` 是「吐几帧之后炸」。

    ``after=None`` 表示在 ``astream_events`` 那一步就炸 —— 这时一帧都没拿到，
    用来测「正文为空」那条记录。
    """

    def __init__(
        self,
        events: list[dict],
        *,
        after: int | None = 0,
        error: BaseException | None = None,
    ) -> None:
        self._events = events
        self._after = after
        self._error: BaseException = error if error is not None else RuntimeError("模型侧炸了")

    async def astream_events(self, *args: Any, **kwargs: Any) -> AsyncIterator[dict]:
        if self._after is None:
            raise self._error
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[dict]:
        for event in self._events[: self._after]:
            yield event
        raise self._error


class RunLogFeed:
    """照 service 的用法喂 ``AgentRunLog``：起止两帧按 ``tool_call_id`` 配对工具名。

    v3 的 ``tool-finished`` 不带工具名，配对在调用方（``stream.ToolNames``）；这里走
    同一条路，顺带把配对本身也测到。
    """

    def __init__(self, log: Any) -> None:
        self._log = log
        self._names = ToolNames()

    def llm(self, event: dict) -> None:
        self._log.on_message(event)

    def start(self, event: dict) -> None:
        self._log.on_tool_start(event, self._names.remember(event))

    def end(self, event: dict) -> None:
        self._log.on_tool_end(event, self._names.resolve(event))
