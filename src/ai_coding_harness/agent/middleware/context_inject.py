"""把本轮意图与澄清状态转成指令，注入主智能体的模型调用。

意图是每轮请求的（``configurable.intent``，由 server 层注入），而智能体是每轮现建的
（``SessionManager.get_agent``，不缓存），所以意图不能写进构建期的 system prompt ——
只能在这里逐次注入。注入只作用于本次模型调用，不写入对话历史与 checkpoint。

**能力不在这里注入。** 每轮的工具与子智能体确实不同，但把「本轮有什么」写成运行期
文本只是给 ``task`` 打补丁：模型照样会拿历史里的旧结论当真（2026-10-19 真机：
块里写着「task：有」，它仍在思考里说工具清单里没有）。现在统一由
``prompts._BASE_HEAD`` 的通用规则约束（历史轮次的工具可用性不作数），具体动作由
``intent_directive`` 给出。当前时间也不再注入 —— 仓库记忆的文件头由平台写，
散文里的日期让模型用 ``execute date`` 取。

只实现异步钩子 ``awrap_model_call``：服务端统一走 ``astream_events``。只写同步版
（``wrap_model_call``）的话，异步调用会落到基类的 ``NotImplementedError``。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

from ai_coding_harness.agent.clarification import CLARIFY_CLOSE, CLARIFY_INTENTS, CLARIFY_OPEN
from ai_coding_harness.agent.prompts import intent_directive
from ai_coding_harness.tools.context import (
    get_clarification,
    get_configurable,
    get_intent,
)

# 平台面向国内 Gitee 用户，日志与记忆里的日期都按这个时区口径
_TIMEZONE = ZoneInfo("Asia/Shanghai")


def now_stamp() -> str:
    """``YYYY-MM-DD HH:mm`` —— 写进记忆「更新于」列的那个口径。"""
    return f"{datetime.now(_TIMEZONE):%Y-%m-%d %H:%M}"


def clarification_block(
    info: Mapping[str, Any] | None,
    allowed_intents: Sequence[str] | None = None,
) -> str:
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
    choices = tuple(allowed_intents or CLARIFY_INTENTS)
    allowed = " / ".join(choices)
    lines += [
        "- 先用只读工具了解必要上下文；可以按你的判断改写候选，不要机械照抄后端候选。",
        "- 不要直接执行、不要改文件、不要创建方案、不要调用 handoffTask。",
        "- 只输出下面这一种结构，不要 Markdown、不要解释文字；问题和选项文案可以自由写：",
        CLARIFY_OPEN,
        (
            '{"steps":[{"id":"intent","question":"先确认意图",'
            '"options":[{"intent":"qa","label":"...","description":"..."},'
            '{"intent":"plan","label":"...","description":"..."}]},'
            '{"id":"scope","question":"再确认业务口径",'
            '"options":[{"value":"a","label":"...","description":"..."},'
            '{"value":"b","label":"...","description":"..."}]}]}'
        ),
        CLARIFY_CLOSE,
        "- steps 为 1~3 个。第一步负责选择意图，2~4 个选项必须带 intent；",
        f"  intent 只能是：{allowed}，按可能性从高到低。",
        "- 后续步骤只问真正影响执行且无法从仓库推断的业务口径；每步 2~4 个选项，",
        "  用 value + label 表示选择，不要重复问意图。",
        "- label 面向用户、要具体；description 用一句话说明这个选项会做什么。",
    ]
    return "\n".join(lines)


def repo_state_block(state: Any) -> str:
    """本轮 PR 快照与状态闸门的结果说明。"""
    if state is None:
        return ""
    known = bool(getattr(state, "pr_state_known", True))
    pulls = tuple(getattr(state, "open_prs", ()) or ())
    lines = ["## 本轮仓库状态（平台已经查过，不要重复查）"]
    if not known:
        lines.append("- PR 状态查询失败：本轮不能安全区分 coding 与 rework。")
    elif not pulls:
        lines.append("- 当前没有 open PR。")
    else:
        lines.append(f"- 当前有 {len(pulls)} 个 open PR：")
        for pull in pulls[:5]:
            number = str(pull.get("number") or "?").strip()
            title = str(pull.get("title") or "").strip()
            branch = ""
            head = pull.get("head")
            if isinstance(head, Mapping):
                branch = str(head.get("ref") or "").strip()
            suffix = f"（分支 {branch}）" if branch else ""
            lines.append(f"  - #{number} {title}{suffix}".rstrip())
    return "\n".join(lines)


class IntentMiddleware(AgentMiddleware):
    """在系统消息末尾追加本轮意图指令与澄清块。"""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        base = request.system_message
        intent = get_intent()
        state = get_configurable("turn_state")
        allowed = getattr(state, "allowed_intents", ()) or None
        parts = (
            base.text if base else "",
            repo_state_block(state),
            intent_directive(intent),
            # 澄清轮放最最后：它要压过 qa 的常规“回答问题”指令
            clarification_block(get_clarification(), allowed),
        )
        text = "\n\n".join(part for part in parts if part)
        return await handler(request.override(system_message=SystemMessage(content=text)))
