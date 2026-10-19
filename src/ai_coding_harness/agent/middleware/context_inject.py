"""把本轮意图、能力与当前时间转成指令，注入主智能体的模型调用。

意图是每轮请求的（``configurable.intent``，由 server 层注入），而智能体是每轮现建的
（``SessionManager.get_agent``，不缓存），所以意图不能写进构建期的 system prompt ——
只能在这里逐次注入。注入只作用于本次模型调用，不写入对话历史与 checkpoint。

除了意图，还要注入**本轮能力**（形态 / 有没有 ``task`` / 可派哪些子智能体）：
实测 rework 轮里模型会沿用上一轮 analysis 的「我是只读形态、没有 task」结论，
明明当前工具列表里有 ``task`` 也不去用。能力块以**实际绑定的工具**为准（``request.tools``），
并明写「上一轮的结论不适用于本轮」。

当前时间同理必须逐次注入（智能体是长期存活对象，构建期取的日期会一路陈旧）：模型写
记忆、写变更记录都要用它，而沙箱里的日期它不一定想得到去取 —— 实测中就编出过一个
并不存在的日期。故不区分意图，每次调用都注入。

只实现异步钩子 ``awrap_model_call``：服务端统一走 ``astream_events``。只写同步版
（``wrap_model_call``）的话，异步调用会落到基类的 ``NotImplementedError``。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

from ai_coding_harness.agent.profiles import profile_for
from ai_coding_harness.agent.prompts import intent_directive
from ai_coding_harness.clarification import CLARIFY_CLOSE, CLARIFY_INTENTS, CLARIFY_OPEN
from ai_coding_harness.tools.context import get_clarification, get_intent

# 平台面向国内 Gitee 用户，日志与记忆里的日期都按这个时区口径
_TIMEZONE = ZoneInfo("Asia/Shanghai")


def current_time_block() -> str:
    """注入用的当前时间块（精确到分钟，够填记忆模板的 ``{YYYY-MM-DD HH:mm}``）。"""
    now = datetime.now(_TIMEZONE)
    return f"## 当前时间\n- {now:%Y-%m-%d %H:%M}（Asia/Shanghai）"


def now_stamp() -> str:
    """``YYYY-MM-DD HH:mm`` —— 写进记忆「更新于」列的那个口径。

    与注入的时间块同一时区、同一精度：两处各取一次时钟会差出几十毫秒无所谓，
    差一个时区就是记忆里的日期整体错一天。
    """
    return f"{datetime.now(_TIMEZONE):%Y-%m-%d %H:%M}"


def _tool_names(tools: object) -> set[str]:
    """从本次模型调用的工具列表里取名字（``BaseTool`` 或 OpenAI 风格的 dict 都认）。"""
    names: set[str] = set()
    for tool in tools or []:  # type: ignore[union-attr]
        name = getattr(tool, "name", None)
        if not name and isinstance(tool, dict):
            name = tool.get("name") or (tool.get("function") or {}).get("name")
        if name:
            names.add(str(name))
    return names


def capability_block(intent: str | None, tools: object) -> str:
    """本轮能力的权威说明：形态、有没有 ``task``、可派哪些子智能体。

    ``task`` 是否可用以 ``request.tools`` 为准，不按意图猜 —— 这样即使装配出了问题，
    模型看到的也是真实能力；同时明写「上一轮的结论不适用于本轮」，压住历史锚定。
    """
    profile = profile_for(intent)
    has_task = "task" in _tool_names(tools)
    subagents = (
        " / ".join(profile.subagents) if has_task and profile.subagents else "无"
    )
    return (
        "## 本轮能力（以这一行为准，不要沿用上一轮结论）\n"
        f"- 意图：{intent or '未指定'}；形态：{profile.form}\n"
        f"- `task`：{'有' if has_task else '没有'}；可派子智能体：{subagents}\n"
        "- 上一轮如果说过「没有 task」「只读」或「不能改代码」，那是上一轮的形态，"
        "不适用于本轮。"
    )


def clarification_block(info: Mapping[str, Any] | None) -> str:
    """低置信度澄清轮：让 qa 先追问，不直接执行。

    这个块放在所有注入内容的**最后** —— qa 的常规指令是“回答问题”，这里要压过它。
    """
    if not info:
        return ""
    lines = ["## 本轮先澄清意图（不要直接执行）"]
    confidence = info.get("confidence")
    if isinstance(confidence, (int, float)):
        lines.append(
            f"- 判定置信度：{float(confidence):.2f}（低于阈值，先确认再动手）"
        )
    ranked: list[str] = []
    for item in (info.get("candidates") or [])[:3]:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        name, value = item
        try:
            ranked.append(f"{name} {float(value):.2f}")
        except (TypeError, ValueError):
            continue
    if ranked:
        lines.append("- 候选（按可能性从高到低）：" + " / ".join(ranked))
    else:
        lines.append("- 候选：按你从上下文判断的可能性自行排序（最多 3 个）")
    allowed = " / ".join(CLARIFY_INTENTS)
    lines += [
        "- 先用只读工具了解必要上下文；可以按你的判断改写候选，不要机械照抄后端候选。",
        "- 不要直接执行、不要改文件、不要创建方案。",
        "- 只输出下面这一种结构，不要 Markdown、不要解释文字；问题和选项文案可以自由写：",
        CLARIFY_OPEN,
        (
            '{"question":"用一句话问用户想做什么",'
            '"options":[{"intent":"qa","label":"...","description":"..."},'
            '{"intent":"plan","label":"...","description":"..."}]}'
        ),
        CLARIFY_CLOSE,
        f"- options 必须 2~3 个，按你的判断从高到低；intent 只能是：{allowed}。",
        "- label 面向用户、要具体；description 用一句话说明这个选项会做什么。",
    ]
    return "\n".join(lines)


class IntentMiddleware(AgentMiddleware):
    """在系统消息末尾追加当前时间、本轮意图指令与本轮能力。"""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        base = request.system_message
        intent = get_intent()
        parts = (
            base.text if base else "",
            current_time_block(),
            intent_directive(intent),
            # 放最后：能力块要压过历史里上一轮的自我描述
            capability_block(intent, request.tools),
            # 澄清轮放最最后：它要压过 qa 的常规“回答问题”指令
            clarification_block(get_clarification()),
        )
        text = "\n\n".join(part for part in parts if part)
        return await handler(request.override(system_message=SystemMessage(content=text)))
