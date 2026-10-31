"""ChatOpenAI 的补丁层：保留第三方网关的 ``reasoning_content``（思考正文）。

``ChatOpenAI`` 只面向官方 OpenAI 规范 —— 模块 docstring 明写第三方字段
（``reasoning_content`` / ``reasoning_details``）**不提取、不保留**；流式转换
``_convert_delta_to_message_chunk`` 只搬 ``content`` / ``function_call`` / ``tool_calls``。
原始 chunk 里其实有这段文本（``delta.reasoning_content``），是 LangChain 这一层丢的。

前端「思考过程」块依赖 v3 事件的 ``reasoning-delta``，而它来自
``AIMessageChunk.content_blocks`` —— 所以这里要补**三处**：

1. ``ReasoningChatOpenAI._convert_chunk_to_generation_chunk``：从原始 chunk 的 delta
   取出 ``reasoning_content``，累加进 ``additional_kwargs``；
2. ``ReasoningAIMessageChunk.content_blocks``：把这段文本还原成 reasoning 内容块。
3. ``ReasoningChatOpenAI._get_request_payload``：下一轮把历史 assistant 消息里的
   ``reasoning_content`` 写回请求。只收不发的模型会看到“有工具调用、没有思考”，
   国产推理模型的工具续轮会因此丢上下文。

**只补 ``additional_kwargs`` 不够**：OpenAI 的 provider translator
（``translate_content_chunk``）只产出 text / tool_call，压根不读这个键，v3 桥接层
拿到的 ``content_blocks`` 里仍然没有思考。

流式聚合走 ``add_ai_message_chunks``，它用 ``left.__class__`` 造新消息，所以子类会
一路保留；思考文本在 ``merge_dicts`` 里按字符串拼接。

非流式响应也经过 ``_create_chat_result`` 的同一套丢弃逻辑，这里一并换成
``ReasoningAIMessage``，避免摘要、探测或降级调用在不同传输方式下行为不一致。
"""

from __future__ import annotations

from typing import Any, cast

from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.messages import content as types
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI


def _reasoning_text(message: AIMessage | AIMessageChunk) -> str | None:
    """从消息对象取第三方思考正文；``content_blocks`` 与旧 kwargs 形状都兼容。"""
    raw = message.additional_kwargs.get("reasoning_content")
    if isinstance(raw, str) and raw:
        return raw

    content = message.content
    if not isinstance(content, list):
        return None
    chunks = [
        block.get("reasoning")
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "reasoning"
        and isinstance(block.get("reasoning"), str)
        and block["reasoning"]
    ]
    return "".join(chunks) or None


def _with_reasoning_block(
    message: AIMessage | AIMessageChunk,
    blocks: list[types.ContentBlock],
) -> list[types.ContentBlock]:
    """把 ``reasoning_content`` 还原成 reasoning 块；已有协议块时不重复。"""
    reasoning = _reasoning_text(message)
    if reasoning and not any(block.get("type") == "reasoning" for block in blocks):
        blocks.insert(
            0,
            cast(
                "types.ContentBlock",
                {"type": "reasoning", "reasoning": reasoning},
            ),
        )
    return blocks


class ReasoningAIMessageChunk(AIMessageChunk):
    """把 ``additional_kwargs["reasoning_content"]`` 还原成 reasoning 内容块。

    OpenAI 的 provider translator 只认 text / tool_call；这里在它的结果后面补一块。
    已有的 reasoning 块不重复插（有的供应商会直接给 content block）。
    """

    @property
    def content_blocks(self) -> list[types.ContentBlock]:
        return _with_reasoning_block(self, list(super().content_blocks))


class ReasoningAIMessage(AIMessage):
    """非流式响应的 reasoning 块版本，与 ``ReasoningAIMessageChunk`` 同口径。"""

    @property
    def content_blocks(self) -> list[types.ContentBlock]:
        return _with_reasoning_block(self, list(super().content_blocks))


class ReasoningChatOpenAI(ChatOpenAI):
    """保留并回传第三方 ``reasoning_content`` 的 ChatOpenAI。

    流式钩子先让上游照常解析 text / tool_calls，再把第三方的思考文本补进
    ``additional_kwargs``。请求钩子负责把历史里的这段文本继续交给模型，形成完整往返。
    """

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict[str, Any],
        default_chunk_class: type,
        base_generation_info: dict[str, Any] | None,
    ) -> ChatGenerationChunk | None:
        choices = chunk.get("choices") or (chunk.get("chunk") or {}).get("choices", [])
        delta = (choices[0].get("delta") if choices else None) or {}
        if isinstance(delta, dict) and not delta.get("role"):
            # **必须补**：父类 ``_convert_delta_to_message_chunk`` 只在
            # ``role == "assistant"`` 或 ``default_class == AIMessageChunk``（精确相等）
            # 时才解析 ``tool_call_chunks``。我们返回子类后，``_stream`` 会把
            # ``default_chunk_class`` 设成子类；后续 tool-call chunk 的 role 是 None，
            # 于是走最后的兜底分支 —— 正文还在，tool_calls 被静默丢掉（2026-10-19 真机
            # 表现：模型明明要调两个 execute，应用里却是「空正文 + 0 工具」）。
            delta["role"] = "assistant"

        generation = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation is None:
            return None
        message = generation.message
        if not isinstance(message, AIMessageChunk):
            return generation

        reasoning = delta.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            message.additional_kwargs["reasoning_content"] = (
                message.additional_kwargs.get("reasoning_content", "") + reasoning
            )

        # 每一段都换成子类：第一段决定整条流的 ``left.__class__``，聚合后才不会掉类型。
        return ChatGenerationChunk(
            message=ReasoningAIMessageChunk(**message.model_dump()),
            generation_info=generation.generation_info,
        )

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """把 assistant 历史里的思考写回 Chat Completions 的 ``reasoning_content``。

        父类会丢掉这个第三方字段；工具调用后的下一次请求因此看不到模型上一段的推理。
        Responses API 的 reasoning item 已由上游按内容块序列化，这里只处理 messages 形态。
        """
        messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(messages, stop=stop, **kwargs)
        wire_messages = payload.get("messages")
        if not isinstance(wire_messages, list):
            return payload

        for message, wire_message in zip(messages, wire_messages):
            if (
                not isinstance(message, AIMessage)
                or not isinstance(wire_message, dict)
                or wire_message.get("role") != "assistant"
            ):
                continue
            reasoning = _reasoning_text(message)
            if reasoning:
                wire_message["reasoning_content"] = reasoning
        return payload

    def _create_chat_result(
        self,
        response: Any,
        generation_info: dict | None = None,
    ) -> ChatResult:
        """保留非流式响应里的 ``reasoning_content``，与流式路径保持同一种消息形状。"""
        result = super()._create_chat_result(response, generation_info)
        response_dict = (
            response
            if isinstance(response, dict)
            else response.model_dump(warnings=False)
        )
        choices = response_dict.get("choices") or []

        for generation, choice in zip(result.generations, choices):
            reasoning = (choice.get("message") or {}).get("reasoning_content")
            if not isinstance(reasoning, str) or not reasoning:
                continue
            message = generation.message
            if not isinstance(message, AIMessage):
                continue
            dumped = message.model_dump()
            dumped["additional_kwargs"] = {
                **message.additional_kwargs,
                "reasoning_content": reasoning,
            }
            generation.message = ReasoningAIMessage(**dumped)
        return result
