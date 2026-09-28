"""能力交接：当前形态做不了这一轮请求时，把它交给另一个形态接着做。

缘起：形态是按意图裁的（``agent/profiles.py``），裁掉的能力模型**看不见** —— 于是它
只能对用户说「我这个形态做不了」。2026-09-21 那轮事故里，请求要的是「别给方案了，直接
改」，而形态早被口令钉成了 plan：模型手上没有 ``create_pr``、没有子智能体，就先复述一遍
方案再问用户要不要实施，一轮跑了 686 秒。提示词让它在做不了时**说清楚**（``_CAPABILITY_EXIT``），
这个工具则是那个出口。

工作方式与 ``task`` 像，但有三处不同：

1. **参数里没有目标形态**：交去哪儿由重判意图决定，不由模型挑 —— 模型正是那个判错的
   人，再让它挑一次形态没有意义。
2. 重判走 ``detect_intent(force_llm=True)``，**跳过口令层**：要纠正的常常正是口令层
   （句子里一句「实施」把意图钉成 coding，而真实诉求是「先出方案」）。只认模型判定
   （``SOURCE_LLM``）；模型不可用就拒绝交接 —— 那时拿到的只是关键字复读，换形态等于
   换个姿势猜。
3. 内层跑在**派生线程**上（``<会话线程>::handoff-N``）：内层是完整的一轮智能体运行，
   与原线程共用 checkpointer 的根槽位会覆盖外层自己的 checkpoint（``checkpoint_ns``
   在显式给了 ``thread_id`` 之后会被重置成 ``""``，两边就落到同一个槽）。

内层那一串事件**对外层是隐形的**（见 ``_detached``）：不隔开的话，内层模型的正文会以
空 namespace 混进外层事件流，被当成外层自己的回答下发、还写进业务库 —— 正是要避免的
那类「一轮里冒出别人的话」。用户看到的是：一张 ``handoffTask`` 工具卡，然后外层模型
据内层结果回话。

内层的工具调用仍然受边界与循环守卫约束（那些中间件照挂），所以「交接出去绕开检查」
不存在；守卫的重复计数还与外层共用（同一个 ``TurnState``），内层原地打转一样会被刹。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import var_child_runnable_config
from langchain_core.tools import BaseTool, tool

from ai_coding_harness.agent.middleware import TurnState
from ai_coding_harness.agent.profiles import profile_for
from ai_coding_harness.server.intent import SOURCE_LLM
from ai_coding_harness.tools.context import get_configurable, get_redact_terms

if TYPE_CHECKING:
    from ai_coding_harness.config import Settings
    from ai_coding_harness.session.manager import (
        Session,
        SessionManager,
        SessionRuntime,
    )

logger = logging.getLogger(__name__)

# 拒绝交接时回给模型的话。都不是错误，是「这件事该你自己做」——
# 所以不给 status="error"（模型据此换做法，而不是当成环境故障）。
NO_STATE = "当前没有轮次上下文，无法交接：请把做不了的原因和已掌握的情况直接讲给用户。"
ALREADY = "本轮已经交接过一次了：同一个请求只能交出去一次，别来回甩锅，把已知情况讲给用户。"
NO_REJUDGE = "现在无法重判意图（分类模型不可用），交接会把请求送到错误的能力形态上，请直接讲给用户。"
NOT_LLM = "重判没有走模型（分类器不可用，退回了关键字口径），不足以推翻原来的判断，请直接讲给用户。"
SAME_FORM = "重判结果仍是当前形态：这件事本来就该你做，接着做，别交接。"
EMPTY_RESULT = (
    "交接的形态这一轮跑完了但没有给出正文。别替它编内容 —— "
    "把已确认的情况讲给用户，并说明这一步没有拿到结果。"
)


@dataclass(frozen=True)
class HandoffContext:
    """交接工具绑定的会话上下文（与 ``PlanContext`` 同路子：构建时闭包捕获）。

    ``form`` 是**当前**形态：重判若还落在这个形态上就不交接（否则内层是同一个装配、
    同一件事，白跑一趟还可能自己套自己）。
    """

    manager: SessionManager
    runtime: SessionRuntime
    session: Session
    settings: Settings
    form: str


def _content_text(content: Any) -> str:
    """消息正文：可能是字符串，也可能是内容块列表（两种形态都要认）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for block in content:
            if isinstance(block, str):
                chunks.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                chunks.append(block["text"])
        return "".join(chunks)
    return ""


def final_text(result: Any) -> str:
    """取内层这一轮的最终正文：最后一条**有正文**的 AI 消息。

    内层跑完会带回一整段消息（工具往返都在里面），收尾口径与本轮一致 —— 用户看到的是
    它最后那句话，不是中途的旁白。
    """
    messages = result.get("messages") if isinstance(result, Mapping) else None
    for message in reversed(list(messages or [])):
        if isinstance(message, AIMessage) and (text := _content_text(message.content).strip()):
            return text
    return ""


def _inner_input(
    message: str, history: Sequence[str], *, form: str, reason: str, notes: str
) -> str:
    """内层那一轮的用户消息：原话 + 最近几轮 + 交接说明。

    刻意不让模型转述请求 —— 工具上没有「转述」参数，转述必丢细节。内层跑在自己的线程
    上、看不见本轮之前的历史，所以把最近几条用户消息一并带上（「就按你说的改」这种
    指代要靠它）。
    """
    parts: list[str] = []
    if history:
        recent = "\n".join(f"- {item}" for item in history)
        parts.append(f"最近几条用户消息（由远及近）：\n{recent}")
    parts.append(f"本轮消息：{message}")
    parts.append(
        f"（上面这轮请求原本派给了「{form}」形态，它判断自己做不了，理由是：{reason}。"
        "现在由你接手，把它做完。）"
    )
    if notes:
        parts.append(f"交接补充：{notes}")
    return "\n\n".join(parts)


def build_handoff_tools(ctx: HandoffContext) -> list[BaseTool]:
    """构建能力交接工具（绑定当前会话与当前形态）。"""

    @tool
    async def handoffTask(reason: str, notes: str = "") -> str:
        """把这一轮请求交给更合适的能力形态去做（自己做不了时才用）。

        只有当这件事**确实**不在当前形态的能力范围内时才调它（比如手上没有写权限、
        没有建 PR 的工具）。能自己做就自己做，不要拿它当「把活推出去」的捷径。

        Args:
            reason: 为什么当前形态做不了（一句话，写清缺的是什么能力）。
            notes: 交给对方接手的补充说明：已查清的情况、要改的文件、注意事项。
        """
        state = get_configurable("turn_state")
        if not isinstance(state, TurnState):
            return NO_STATE
        # 额度扣在**尝试**上而不是**成功**上：被拒之后还要再试的，多半是在原地甩锅
        # （重判结果不会在同一轮里翻盘），扣掉正好收住。
        ordinal = state.note_handoff()
        if ordinal is None:
            return ALREADY
        if state.rejudge is None:
            return NO_REJUDGE

        decision = await state.rejudge(state.message, state.history)
        if decision.source != SOURCE_LLM:
            logger.info(
                "交接被拒：重判未走模型 source=%s public_id=%s",
                decision.source, ctx.session.public_id,
            )
            return NOT_LLM
        target = profile_for(decision.intent)
        if target.form == ctx.form:
            logger.info(
                "交接被拒：重判仍是本形态 form=%s public_id=%s",
                ctx.form, ctx.session.public_id,
            )
            return SAME_FORM

        inner = ctx.manager.get_agent(ctx.runtime, ctx.session, target)
        # 同一会话最多一个交接线程（``max_handoffs`` 默认 1），内层下一轮交接时还看得见
        # 上一轮做过什么 —— 这是想要的，别改成每轮一个新线程。
        thread = f"{ctx.manager.thread_id(ctx.session)}::handoff-{ordinal}"
        config = RunnableConfig(
            configurable={
                "thread_id": thread,
                # 意图要与重判结果一致：指令段（``context_inject``）读的就是它
                "intent": decision.intent,
                "redact_terms": get_redact_terms(),
                # 同一个 TurnState：内层的重复计数与外层合账，``handoffTask`` 的额度也共用
                "turn_state": state,
            },
            recursion_limit=ctx.settings.agent_recursion_limit,
        )
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": _inner_input(
                        state.message, state.history,
                        form=ctx.form, reason=reason, notes=notes,
                    ),
                }
            ]
        }
        # 把环境 config 摘掉再跑内层：langchain 靠 ``var_child_runnable_config`` 这个
        # ContextVar 把回调管理器传给子 run，而 ``ensure_config`` 对 callbacks 只叠加、
        # 不改写（显式传 ``callbacks=[]`` 无效 —— 空列表被忽略）。不断开的话，内层模型
        # 的事件会带着**空 namespace** 混进外层 v3 流，被 ``V3Translator`` 当成外层
        # 自己的正文下发并落库。代价是内层这一轮不进 agent.log（日志由外层事件流驱动），
        # 下面那条 info 与用户看到的工具卡足够定位。
        token = var_child_runnable_config.set({})
        started = time.monotonic()
        try:
            result = await inner.ainvoke(payload, config=config)
        finally:
            var_child_runnable_config.reset(token)
        body = final_text(result)
        logger.info(
            "能力交接：public_id=%s %s→%s 线程=%s 用时=%.1fs 回正文 %d 字",
            ctx.session.public_id, ctx.form, decision.intent, thread,
            time.monotonic() - started, len(body),
        )
        if not body:
            return EMPTY_RESULT
        limit = ctx.settings.handoff_result_max
        if len(body) > limit:
            body = f"{body[:limit]}\n\n（结果过长，已截断）"
        return f"已交给「{decision.intent}」形态完成，它回给你的结果：\n\n{body}"

    return [handoffTask]
