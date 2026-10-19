"""ChatOpenAI 的补丁层：保留第三方网关返回的 ``reasoning_content``（思考正文）。

``ChatOpenAI`` 只面向官方 OpenAI 规范 —— 模块 docstring 明写第三方字段
（``reasoning_content`` / ``reasoning_details``）**不提取、不保留**；流式转换
``_convert_delta_to_message_chunk`` 只搬 ``content`` / ``function_call`` / ``tool_calls``。
原始 chunk 里其实有这段文本（``delta.reasoning_content``），是 LangChain 这一层丢的。

前端「思考过程」块依赖 v3 事件的 ``reasoning-delta``，而它来自
``AIMessageChunk.content_blocks`` —— 所以这里要补**两处**：

1. ``ReasoningChatOpenAI._convert_chunk_to_generation_chunk``：从原始 chunk 的 delta
   取出 ``reasoning_content``，累加进 ``additional_kwargs``；
2. ``ReasoningAIMessageChunk.content_blocks``：把这段文本还原成 reasoning 内容块。

**只补 ``additional_kwargs`` 不够**：OpenAI 的 provider translator
（``translate_content_chunk``）只产出 text / tool_call，压根不读这个键，v3 桥接层
拿到的 ``content_blocks`` 里仍然没有思考。

流式聚合走 ``add_ai_message_chunks``，它用 ``left.__class__`` 造新消息，所以子类会
一路保留；思考文本在 ``merge_dicts`` 里按字符串拼接。

**非流式响应仍然没有思考**（``_create_chat_result`` 也走同样的丢弃逻辑）：
主智能体走流式；摘要/分类那两次调用不需要展示思考，这次不补。要补时再加一个
``ReasoningAIMessage``，两处共用同一套 reasoning 块逻辑。
"""

from __future__ import annotations

from typing import Any, cast

from langchain_core.messages import AIMessageChunk
from langchain_core.messages import content as types
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI


class ReasoningAIMessageChunk(AIMessageChunk):
    """把 ``additional_kwargs["reasoning_content"]`` 还原成 reasoning 内容块。

    OpenAI 的 provider translator 只认 text / tool_call；这里在它的结果后面补一块。
    已有的 reasoning 块不重复插（有的供应商会直接给 content block）。
    """

    @property
    def content_blocks(self) -> list[types.ContentBlock]:
        blocks = list(super().content_blocks)
        reasoning = self.additional_kwargs.get("reasoning_content")
        if (
            isinstance(reasoning, str)
            and reasoning
            and not any(block.get("type") == "reasoning" for block in blocks)
        ):
            blocks.insert(
                0,
                cast(
                    "types.ContentBlock",
                    {"type": "reasoning", "reasoning": reasoning},
                ),
            )
        return blocks


class ReasoningChatOpenAI(ChatOpenAI):
    """保留流式 ``reasoning_content`` 的 ChatOpenAI。

    只覆写**流式 chunk 转换**这一个私有钩子：先让上游照常解析 text / tool_calls，
    再把第三方的思考文本补进 ``additional_kwargs``，并把消息换成上面那个子类。
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
