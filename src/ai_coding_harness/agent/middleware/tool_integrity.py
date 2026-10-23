"""工具调用自洽：让每条 assistant 的 tool_call 都有一条配套的 tool 响应。

provider 对历史的要求是「带 tool_calls 的 assistant 消息后面必须紧跟齐全部 tool 响应」。
实测有一条会话的这条要求被永久破坏，此后**每一次请求都 400、重试也不自愈**
（``An assistant message with 'tool_calls' must be followed by tool messages ...``，
缺 id 清单还是空的）。三个方向同时漏：

1. **模型出口**：端点会吐出 ``"id": ""`` 的 tool_call（实测一整批五条，不是 None ——
   ``ToolMessage`` 的 ``tool_call_id`` 是必填 ``str``，None 会直接 pydantic 报错，
   能写进历史的空 id 只可能是空串）。已知的一条上游成因：v3 事件流的装配器
   （``ChatModelStream._assemble_message``）对分片做浅覆盖，供应商只在首个分片带
   id、续片带空串时，真 id 就被空串冲掉（qwen 如此，也是主控换掉它的原因，见
   CHANGELOG 09-18）。**那条路我们治不了**，所以这里只堵出口，另有一层整表修复兜底。
2. **工具异常**：``ToolNode`` 默认只把参数校验失败转成 tool 响应，其它异常一律上抛，
   整个 superstep 连同同批工具结果一起丢 —— 没写回的调用就成了孤儿。
3. **上游补丁**：``PatchToolCallsMiddleware`` 用「全历史所有 tool 响应的 id 集合」判
   「答没答」。空串在这个集合里没有身份：历史里只要有过一条空 id 的响应，此后所有空 id
   调用都被当成已答而跳过。上述会话正是这样 —— 补丁每轮都跑，每轮都判「无事可做」，
   一个字节都不写。

三个钩子各堵一头，缺一不可：``ToolCallIdMiddleware`` 在模型出口就把空 id 换成真 id，
并把无法序列化的 invalid call 降级成普通文本；``ToolErrorGuardMiddleware`` 把工具异常
变成 tool 响应；``HistoryRepairMiddleware`` 把已经坏掉的历史整表修好。

**为什么是整表重写**：孤儿可能落在历史**中间**（后面还有人类消息），而往图输入里追加消息
只能加在末尾，够不着；能改中间位置的只有 ``RemoveMessage(REMOVE_ALL_MESSAGES)`` + 全量
列表，这也是上游走的那条路。

三条钩子都只实现异步版：服务端统一走 ``astream_events``（与 ``redaction`` 同因）。
子智能体不继承主智能体的中间件，三个角色各自要挂一份。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from typing import Any
from uuid import uuid4

from langchain.agents.middleware import (
    AgentMiddleware,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.errors import GraphBubbleUp
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime
from langgraph.types import Command

from ai_coding_harness.agent.middleware.guard import TurnStop
from ai_coding_harness.observability.logging import MAIN_AGENT
from ai_coding_harness.safety.redact import redact
from ai_coding_harness.tools.context import get_redact_terms

logger = logging.getLogger(__name__)

INVALID = "invalid_tool_call"
TOOL_CALL = "tool_call"

#: 异常正文写进日志的字符上限：够放一句上游的错误原文，又不至于把整段 traceback 灌进
#: agent.log。**给模型的那条响应不截断** —— 它要照着这句话决定下一步。
_ERROR_LOG_LIMIT = 500

#: invalid tool call 在模型出口最多重试几次。重试仍失败才降级为普通文本。
MAX_INVALID_TOOL_RETRIES = 3


def _clip(text: str) -> str:
    """截断到日志上限。调用方**先脱敏再进来**（与 ``run_log._safe`` 同一口径：顺序反了
    会留下横跨截断点的半截令牌，既躲过打码又没了上下文）。"""
    return text[:_ERROR_LOG_LIMIT] + ("…" if len(text) > _ERROR_LOG_LIMIT else "")

def _new_call_id() -> str:
    """与端点自己给的 id 同形（``call_`` + 24 位 hex，实测长度 29），便于并排比对。"""
    return f"call_{uuid4().hex[:24]}"


def _calls_of(message: AIMessage) -> list[dict[str, Any]]:
    """这条 AI 消息声明的全部调用（正常 + 参数坏掉的），按声明顺序。

    取副本：下面会给空 id 补真 id，直接改原字典会连带改掉历史里那条消息。
    ``type`` 显式写死（langchain 自己也是这么归一化的），``_rebuild`` 才能把两类
    各归各位 —— 只认键值，不认来源列表。
    """
    calls = [{**call, "type": TOOL_CALL} for call in message.tool_calls]
    calls += [{**call, "type": INVALID} for call in message.invalid_tool_calls]
    if isinstance(message.content, list):
        calls += [
            {**block, "type": INVALID}
            for block in message.content
            if isinstance(block, dict) and block.get("type") == INVALID
        ]
    return calls


def _rebuild(message: AIMessage, calls: list[dict[str, Any]]) -> AIMessage:
    """按改过的调用重建消息；invalid call 降级为正文，不再进入 provider payload。"""
    invalid = [call for call in calls if call.get("type") == INVALID]
    content = message.content
    if invalid:
        note = "\n".join(_invalid_note(call) for call in invalid)
        if isinstance(content, str):
            content = f"{content}\n\n{note}".strip()
        elif isinstance(content, list):
            content = [
                block
                for block in content
                if not (
                    isinstance(block, dict)
                    and block.get("type") == INVALID
                )
            ]
            content.append({"type": "text", "text": f"\n\n{note}"})
        else:
            content = f"{content}\n\n{note}"
    return message.model_copy(
        update={
            "tool_calls": [call for call in calls if call.get("type") != INVALID],
            "invalid_tool_calls": [],
            "content": content,
        }
    )


def _invalid_note(call: dict[str, Any]) -> str:
    """把坏调用变成模型和用户都能看懂、provider 也能接受的一行文本。"""
    name = call.get("name") or "unknown"
    error = str(call.get("error") or "").strip()
    suffix = f"：{error}" if error else ""
    return f"[工具调用未执行] {name} 参数不完整或格式错误{suffix}"


def _unexecuted_note(call: dict[str, Any]) -> str:
    name = call.get("name") or "unknown"
    return f"工具 {name} 未执行：上一轮在它返回结果之前就中断了。"


def repair_messages(messages: Sequence[BaseMessage]) -> list[BaseMessage] | None:
    """修好 ``messages`` 里的配对；无需改动时返回 ``None``。

    四步，顺序不能反：

    1. 给空 id（含 ``None``）的调用补真 id —— 补完才谈得上「谁答了谁」；
    2. 空 id 的响应按顺序认领**空 id 的调用**（``ToolNode`` 是按调用顺序连续写回的，
       所以空响应认领的只可能是同样空 id 的那几个，不能让它认领真 id 的调用）；
    3. 补完 id 再判「答没答」；
    4. 仍没人答的调用补一条说明性响应。

    「已答」只看**这条 AI 消息之后**的响应。用全历史判会让更早那批同 id 的响应（空 id
    尤其如此）把后面的调用一起判成已答，正是上游补丁失效的原因。
    """
    total = len(messages)
    patched: list[BaseMessage] = []
    changed = False
    discard_tool_ids: set[str] = set()
    index = 0

    while index < total:
        message = messages[index]
        if not isinstance(message, AIMessage):
            if (
                isinstance(message, ToolMessage)
                and message.tool_call_id in discard_tool_ids
            ):
                changed = True
                index += 1
                continue
            patched.append(message)
            index += 1
            continue
        calls = _calls_of(message)
        if not calls:
            patched.append(message)
            index += 1
            continue

        dirty = False
        blank = [call for call in calls if not call.get("id")]
        for call in blank:
            call["id"] = _new_call_id()
            dirty = True
        invalid_ids = {
            call["id"] for call in calls if call.get("type") == INVALID
        }
        if invalid_ids:
            dirty = True
            discard_tool_ids.update(invalid_ids)

        # 紧跟其后的那一串响应（provider 也要求这一串紧挨着 AI 消息）
        stop = index + 1
        while stop < total and isinstance(messages[stop], ToolMessage):
            stop += 1

        queue = [call for call in blank if call.get("type") != INVALID]
        responses: list[BaseMessage] = []
        for response in messages[index + 1 : stop]:
            if (
                isinstance(response, ToolMessage)
                and response.tool_call_id in invalid_ids
            ):
                dirty = True
                continue
            if not response.tool_call_id and queue:
                response = response.model_copy(
                    update={"tool_call_id": queue.pop(0)["id"]}
                )
                dirty = True
            elif not response.tool_call_id and invalid_ids:
                dirty = True
                continue
            responses.append(response)

        answered = {
            response.tool_call_id
            for response in (*responses, *messages[index + 1 :])
            if isinstance(response, ToolMessage) and response.tool_call_id
            and response.tool_call_id not in discard_tool_ids
        }
        pending = [
            call
            for call in calls
            if call.get("type") != INVALID and call["id"] not in answered
        ]

        patched.append(_rebuild(message, calls) if dirty else message)
        for call in pending:
            patched.append(
                ToolMessage(
                    content=_unexecuted_note(call),
                    name=call.get("name") or "unknown",
                    tool_call_id=call["id"],
                )
            )
            dirty = True
        patched.extend(responses)
        changed = changed or dirty
        index = stop

    return patched if changed else None


def _normalized_ids(message: Any) -> Any:
    """模型刚吐出来的响应：补空 id，并把 invalid call 降级为普通正文。"""
    if not isinstance(message, AIMessage):
        return message
    calls = _calls_of(message)
    if not calls:
        return message
    has_invalid = any(call.get("type") == INVALID for call in calls)
    if not has_invalid and all(call.get("id") for call in calls):
        return message
    for call in calls:
        if not call.get("id"):
            call["id"] = _new_call_id()
    return _rebuild(message, calls)


def _normalize_response(response: Any) -> Any:
    """三种响应形态都认（同 ``ModelRedactionMiddleware``）：漏认等于放过一批空 id。"""
    if isinstance(response, ExtendedModelResponse):
        inner = _normalize_response(response.model_response)
        if inner is response.model_response:
            return response
        return replace(response, model_response=inner)
    if isinstance(response, ModelResponse):
        messages = [_normalized_ids(message) for message in response.result]
        if messages == response.result:
            return response
        return replace(response, result=messages)
    return _normalized_ids(response)


def _response_messages(response: Any) -> list[Any]:
    """把三种模型响应形态统一成消息列表。"""
    if isinstance(response, ExtendedModelResponse):
        return _response_messages(response.model_response)
    if isinstance(response, ModelResponse):
        return list(response.result)
    return [response]


def _invalid_calls(response: Any) -> list[dict[str, Any]]:
    """取这次响应里所有无法执行的工具调用。"""
    invalid: list[dict[str, Any]] = []
    for message in _response_messages(response):
        if not isinstance(message, AIMessage):
            continue
        invalid.extend(
            call for call in _calls_of(message) if call.get("type") == INVALID
        )
    return invalid


def _retry_request(
    request: ModelRequest,
    invalid: Sequence[dict[str, Any]],
) -> ModelRequest:
    """把失败原因作为本轮附加约束，让模型重新生成完整调用。"""
    details = "；".join(
        f"{call.get('name') or 'unknown'}: "
        f"{call.get('error') or '工具参数 JSON 不完整'}"
        for call in invalid
    )
    notice = (
        "## 工具调用修复\n"
        f"上一次响应的工具调用无法解析：{details}。\n"
        "请重新生成完整、合法的工具调用 JSON。不要依赖半截 JSON；"
        "如果参数太长，先拆分任务或缩短正文，不要再次输出 invalid_tool_call。"
    )
    base = request.system_message.text if request.system_message else ""
    text = f"{base}\n\n{notice}" if base else notice
    return request.override(system_message=SystemMessage(content=text))


class ToolCallIdMiddleware(AgentMiddleware):
    """模型出口就把空 id 的 tool_call 补成真 id。

    这里补的意义是让**同一轮**内就配得上：``ToolNode`` 按 state 里那条 AI 消息的 id
    写回响应，id 在进 state 前换掉，两边就一致了。否则要等下一轮的整表修复兜，
    中间那一轮的空 id 配对虽然实测能过 provider，但等于把坏数据留在历史里。
    """

    def __init__(self, *, max_invalid_retries: int = MAX_INVALID_TOOL_RETRIES) -> None:
        self._max_invalid_retries = max(0, int(max_invalid_retries))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | ExtendedModelResponse:
        response = await handler(request)
        invalid = _invalid_calls(response)
        retries = 0
        while invalid and retries < self._max_invalid_retries:
            retries += 1
            logger.warning(
                "工具调用参数无效，重试模型 %d/%d：%s",
                retries,
                self._max_invalid_retries,
                "；".join(
                    f"{call.get('name') or 'unknown'}: "
                    f"{call.get('error') or 'JSON 不完整'}"
                    for call in invalid
                ),
            )
            response = await handler(_retry_request(request, invalid))
            invalid = _invalid_calls(response)
        return _normalize_response(response)


class ToolErrorGuardMiddleware(AgentMiddleware):
    """工具抛异常时补一条错误 tool 响应，别让它掀掉整个 superstep。

    ``ToolNode`` 的默认错误处理只认参数校验失败（``_default_handle_tool_errors``
    把 ``ToolInvocationError`` 转成 tool 响应，其它异常原样上抛）。沙箱掉线、网络抖动、
    工具自己的 bug 都属于「其它异常」—— 上抛会连同这一批工具结果一起丢，留下的就是孤儿。

    正文里的异常文本同样要过脱敏：``RedactionMiddleware`` 排在本中间件**外侧**，
    拿到的是这条响应，会照常打码。

    **这里还要记一条 WARNING**（署名 ``agent``）：这条响应是「工具为什么失败」唯一的记录，
    而它进不了运行日志 —— 委派（``task``）失败时连 ``tool-finished`` 事件都没有，日志里
    那行「子智能体完成」根本不会出现（2026-10-16 真机：28 次派发 0 条收尾行）。主控读到的
    只有一句「工具 task 执行失败」，于是换个说法再派一次，4 分钟连派 20 次，谁都查不出原因。

    ``TurnStop`` **不吞**：它是循环守卫「本轮到此为止」的语义，压成一条错误响应之后主控
    只会接着派（那轮 20 连发就是这么来的）。让它冒到 service 层 —— 那一层收下并补一条
    中断记录，用户当场看到中止原因。
    """

    def __init__(self, *, agent: str = MAIN_AGENT) -> None:
        #: 这一份守卫替谁兜（主控还是某个子智能体角色），只用于日志署名。
        self._agent = agent

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        try:
            return await handler(request)
        except GraphBubbleUp:
            # 中断不是错误：它要靠异常穿过工具节点冒到图上去
            raise
        except TurnStop:
            # 循环守卫的中止也不是工具错误：吞了它，主控只会「换个说法再派一次」
            raise
        except Exception as exc:  # noqa: BLE001
            call = request.tool_call
            detail = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "工具 %s 执行失败：%s",
                call.get("name") or "unknown",
                _clip(redact(detail, get_redact_terms())),
                extra={"agent": self._agent},
            )
            return ToolMessage(
                content=f"工具 {call.get('name')} 执行失败：{detail}",
                name=call.get("name") or "unknown",
                # 空 id 也只能写成空串（None 会让 ToolMessage 校验失败），
                # 这条配对由下一轮的整表修复接上
                tool_call_id=call.get("id") or "",
                status="error",
            )


class HistoryRepairMiddleware(AgentMiddleware):
    """每轮开工前把历史修成自洽的：孤儿补响应，空 id 补真 id。

    只在真需要时整表重写（``repair_messages`` 返回 ``None`` 就什么都不做）——
    每轮都重写会让每个 superstep 多出一次全量 checkpoint 写入。修好的历史下一轮
    就是干净的，所以它只对「已经坏掉的那一轮」付费。
    """

    async def abefore_agent(
        self, state: dict[str, Any], runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        repaired = repair_messages(state.get("messages") or [])
        if repaired is None:
            return None
        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *repaired]}
