"""凭据脱敏：工具返回值与模型输出是两条不同的出口，各挂一个 ``awrap_*``。

前者进 state 后再被模型读到，后者直接被写进 state（进而进 Postgres checkpoint），
谁都替不了谁。

两条钩子都只实现异步版：服务端统一走 ``astream_events``。只写同步版会在异步调用时
落到基类的 ``NotImplementedError``。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
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

from ai_coding_harness.safety.redact import redact
from ai_coding_harness.tools.context import get_redact_terms


def _redact_content(content: Any, terms: Sequence[str]) -> Any:
    """改写消息正文里的凭据：字符串与内容块列表两种形态都认。"""
    if isinstance(content, str):
        return redact(content, terms)
    if isinstance(content, list):
        blocks: list[Any] = []
        for block in content:
            if isinstance(block, str):
                blocks.append(redact(block, terms))
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                blocks.append({**block, "text": redact(block["text"], terms)})
            else:
                blocks.append(block)
        return blocks
    return content


def _redact_message(message: Any, terms: Sequence[str]) -> Any:
    """消息正文脱敏；没命中就返回原对象（少造一份副本，也避免状态无谓更新）。"""
    content = getattr(message, "content", None)
    if content is None:
        return message
    new_content = _redact_content(content, terms)
    if new_content == content:
        return message
    # 用 model_copy 保留 tool_call_id：丢了这条消息就没法跟 AI 的 tool call 配对了
    return message.model_copy(update={"content": new_content})


def _redact_command(command: Command, terms: Sequence[str]) -> Command:
    """``Command`` 里带的 messages 同样要脱敏（工具可以直接往状态里塞消息）。"""
    update = command.update
    messages = update.get("messages") if isinstance(update, dict) else None
    if not isinstance(messages, list):
        return command
    new_messages = [_redact_message(message, terms) for message in messages]
    if new_messages == messages:
        return command
    # Command 是 dataclass，replace 保住 goto / graph / resume
    return replace(command, update={**update, "messages": new_messages})


class RedactionMiddleware(AgentMiddleware):
    """工具返回值进对话状态前的凭据脱敏。

    这是四条出口里最关键的一条：结果一旦进 state，就会落进 Postgres checkpoint，
    下次会话读回历史时再送给模型 —— 事后再拦已经晚了。它挂在工具**最内侧**
    （deepagents 把自带的默认栈排在我们前面），所以外层只可能看到已脱敏的文本，
    连它转存到 ``/large_tool_results/`` 的副本也是脱敏后的。

    子智能体**不继承**主智能体的中间件，三个子智能体各自要挂一份（见 ``subagents.py``）：
    读写沙箱的正是 coder / reviewer，漏掉它们等于没做。
    """

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        result = await handler(request)
        terms = get_redact_terms()
        if isinstance(result, ToolMessage):
            return _redact_message(result, terms)
        if isinstance(result, Command):
            return _redact_command(result, terms)
        return result


def _redact_model_response(response: Any, terms: Sequence[str]) -> Any:
    """改写模型响应里的消息正文。

    三种形态都认：``ModelResponse``（常规）、``ExtendedModelResponse``（外层中间件顺带
    往状态里塞 ``Command`` 时用的包装）、裸 ``AIMessage``（框架允许直接返回）。认不出的
    形态原样透传，但**不能漏认** —— 漏认等于静默放行一段未脱敏的正文。
    """
    if isinstance(response, ExtendedModelResponse):
        inner = _redact_model_response(response.model_response, terms)
        if inner is response.model_response:
            return response
        return replace(response, model_response=inner)
    if isinstance(response, ModelResponse):
        messages = [_redact_message(message, terms) for message in response.result]
        if messages == response.result:
            return response
        # ModelResponse 是 dataclass，replace 保住 structured_response
        return replace(response, result=messages)
    return _redact_message(response, terms)


class ModelRedactionMiddleware(AgentMiddleware):
    """模型输出进 graph state（进而进 Postgres checkpoint）前的凭据脱敏。

    工具那条出口只管得住工具返回值；模型自己写的正文是另一条路 —— 它同样会被写进 state、
    落进 checkpoint，下次会话读回历史时还会再送给模型。实测撞见过模型把用户贴进消息里的
    凭据原样复述一遍：当时只挡了工具出口，它就一路漏到了 SSE 与业务库。

    只管 ``content``，不动 ``tool_calls`` 的参数：改写参数等于换掉真正要执行的东西，
    那是行为变更，不是脱敏。
    """

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | ExtendedModelResponse:
        response = await handler(request)
        return _redact_model_response(response, get_redact_terms())
