"""意图影子路由器：把一句话拆成几个正交维度，**只记录，不决定形态**。

为什么另起一层：七选一标签把「这句话在干什么」「想要什么」「指向什么」「是不是
继续上一轮」压成一格，规则只能靠词表补丁。这里先判四个正交维度，再由策略表给出
建议意图；线上默认不采用建议，只把两者并排记进 ``INTENT_ROUTER`` 日志，等评测集
证明它更准之后再切换。

维度刻意保持小：

- ``speech_act``：新任务 / 接着上一轮 / 追问上一轮 / 闲聊
- ``wants``：改东西 / 要解释 / 要决定 / 不确定
- ``target``：代码 / PR 状态 / PR 审查 / PR 修改 / 方案 / 记忆 / 执行过程 / 一般
- ``continuation``：新任务 / 继续上一轮 / 不确定

``suggest_intent()`` 是**第一版策略表**，不是线上判据；评测脚本会把它的建议与人工
标注并排比较。改策略表时只改这一处，不用再动关键词。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from ai_coding_harness.server.intent import (
    SHORT_IMPERATIVES,
    has_continue_marker,
)

logger = logging.getLogger(__name__)

#: 查影子路由走这个关键字：grep INTENT_ROUTER logs/agent.log
ROUTER_LOG_TAG = "INTENT_ROUTER"

SPEECH_ACTS: tuple[str, ...] = ("new_task", "follow_up", "meta_question", "chat")
WANTS: tuple[str, ...] = ("change", "lookup", "analyze", "decide", "unknown")
TARGETS: tuple[str, ...] = (
    "code",
    "pr_status",
    "pr_review",
    "pr_change",
    "plan",
    "memory",
    "execution",
    "general",
    "unknown",
)
CONTINUATIONS: tuple[str, ...] = ("new", "continue_previous", "unknown")

#: 可执行意图（与 ``server/intent.py`` 的七类同名，但这里只用于“上一轮可执行意图”）
ACTIONABLE = frozenset({"coding", "rework", "plan"})

#: PR 已合并 / 已关闭之后的收尾动作归 analysis；“刚提交 / 待合并”不算。
_POST_PR_CLOSE_MARKERS: tuple[str, ...] = (
    "已合并", "合并了", "已关闭", "关闭了", "merged", "closed",
)

#: 明确的“实施方案”命令：句子里有“方案”，但动作是写/改代码，不是改方案对象。
_IMPLEMENT_PLAN_COMMANDS: tuple[str, ...] = (
    "开始实施", "按方案实施", "按照方案实施", "执行这个方案", "执行方案",
    "按这个方案做", "按这个方案实施", "照这个方案做",
)

_PLAN_WORDS: tuple[str, ...] = ("方案", "计划", "步骤", "plan")

#: 用户明确“只看判定/不要执行”时，按 analysis 解释，不要把“我需要做”当成要动手。
_OBSERVE_MARKERS: tuple[str, ...] = (
    "只想知道", "只看", "不要做", "不需要做", "判定结果", "判定我的意图",
)

#: 疑问句标记：它们是“问/讨论”，不是 change 指令。
_QUESTION_MARKERS: tuple[str, ...] = (
    "是不是", "能不能", "可不可以", "是否", "吗", "呢",
)

#: 解释型问句：问“为什么 / 根因 / 原理”。和上面的是非问句不同，这类归 analysis。
#: 注意**不要**把“怎么/如何”放进来：它们在“怎么填/怎么配”里是查资料（qa），
#: “怎么跑通/怎么排查”由 wants=analyze 那条路径兜住。
_WHY_MARKERS: tuple[str, ...] = (
    "为什么", "为何", "原理", "原因", "根因", "区别", "解释",
)

#: 明确的“问已有数据”标记：即使 router 把 wants 判成 decide，也优先 qa。
_ASK_MARKERS: tuple[str, ...] = (
    "有什么", "是什么", "有哪些", "多少", "是否", "在哪", "哪个", "什么",
)


ROUTER_SYSTEM_PROMPT = """你是对话路由器。不要直接选择智能体形态，只判断下面四个正交维度，
并只输出一个 JSON 对象（不要 Markdown、不要解释）。

维度与取值：
- speech_act：new_task（开新任务）/ follow_up（接着上一轮做）/ meta_question（追问或质疑上一轮的动作、结论）/ chat（闲聊或一般知识）
- wants：change（要改代码或改东西）/ lookup（查看、列举、是什么、有哪些、状态）/ analyze（为什么、根因、怎么跑通、影响、排查）/ decide（要一个方案或决定）/ unknown
- target：code（仓库代码）/ pr_status（查 PR 列表、状态、评论）/ pr_review（审查 PR）/ pr_change（按审查意见改 PR）/ plan（方案对象）/ memory（仓库记忆）/ execution（助手/子智能体的执行过程）/ general（一般知识）/ unknown
- continuation：new（新任务）/ continue_previous（继续上一轮）/ unknown

判据（按顺序）：
1. 先看 speech_act。“你为什么不派 coder”“上一轮不是 rework 吗”是在追问/质疑上一轮，
   不是新的分析任务；不要因为出现「为什么」就判成新的分析任务。
2. 如果用户只是在问“上一轮为什么没做/为什么说没有 task”，wants 更可能是 analyze。
3. 如果用户是在要求继续、纠正或补做上一轮的动作（“继续”“按 rework 做”“为什么不派”带明确动作），
   wants 更可能是 change/decide，continuation=continue_previous。
4. target 看对象，不看动词：“修复 PR #13”是 pr_change；“审查 PR #13”是 pr_review；
   “现在有哪些 PR”是 pr_status；“改代码/写功能”是 code；“仓库记忆里怎么说的”是 memory。
5. target=plan 只表示**方案对象本身**（查看/修改/删除方案）。「按这个方案实施」「执行这个方案」
   「开始实施」是在动代码，target=code（在 PR 上改则是 pr_change），不要因为句子里有“方案”就判 plan。
6. wants 的区分：ls 式查看/列举/是什么是 lookup（qa 倾向）；为什么/根因/怎么跑通/影响是
   analyze（analysis 倾向）；实施/修改/修复/提交是 change；出方案/建议/选哪个是 decide。
7. 对助手上一轮行为的反馈或批评（“你作为主智能体不应该自己写 code”“为什么不派 coder”）
   是 meta_question + target=execution：要解释/诊断用 analyze，要求纠正/继续用 change。
8. PR 生命周期：写代码/创建 PR 是 code（或 pr_change）；审查还开着的 PR 是 pr_review；
   按审查意见修改还开着的 PR 是 pr_change；**PR 已合并或已关闭之后的收尾动作**
   （更新记忆、同步、清理）是 pr_status 或 memory + change/decide，最终归 analysis。
   不要把“刚提交 / 待合并”的 PR 归到收尾。
9. 用户说「继续 / 接着 / 修正 / 纠正 / 重来 / 再试一次」时 continuation=continue_previous；
   策略会沿用上一轮可执行意图，而不是新开一个无关形态。
10. 短追问（「B」「确认事实」「修复吧」「继续吧」）先按上一轮话题理解；「修复 / 实施 /
    执行」在刚讨论过方案的上下文里通常是写代码，不是改方案对象。「只想知道判定结果 /
    不要做 / 不需要执行」是 meta_question + analyze + execution，不要判成 change。

输出示例：
{"speech_act":"meta_question","wants":"analyze","target":"execution","continuation":"continue_previous"}
"""

ROUTER_RETRY_PROMPT = (
    "上一次回复不是合法 JSON。只输出一个 JSON 对象，字段为 "
    "speech_act / wants / target / continuation，不要 Markdown、不要解释。"
)


@dataclass(frozen=True)
class RouterDecision:
    """一次影子路由的四个轴。"""

    speech_act: str
    wants: str
    target: str
    continuation: str

    def as_dict(self) -> dict[str, str]:
        return {
            "speech_act": self.speech_act,
            "wants": self.wants,
            "target": self.target,
            "continuation": self.continuation,
        }


def _normalize(value: Any, allowed: Sequence[str], default: str = "unknown") -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else default


def parse_router_reply(text: str) -> RouterDecision | None:
    """从模型回复里抠出 JSON；不合法就返回 ``None``（影子层绝不抛给主流程）。"""
    if not text:
        return None
    # 兼容 ```json ... ``` 与前后带解释的回复：只取第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    speech = _normalize(data.get("speech_act"), SPEECH_ACTS)
    wants = _normalize(data.get("wants"), WANTS)
    target = _normalize(data.get("target"), TARGETS)
    continuation = _normalize(data.get("continuation"), CONTINUATIONS)
    if speech == "unknown" or wants == "unknown" or target == "unknown":
        return None
    return RouterDecision(speech, wants, target, continuation)


def _user_prompt(
    message: str,
    history: Sequence[str],
    previous_reply: str | None,
    previous_intent: str | None,
    active_intent: str | None = None,
    dialogue: Sequence[Mapping[str, str]] = (),
) -> str:
    parts: list[str] = []
    if active_intent:
        parts.append(
            f"当前未完成任务：{active_intent}（最近一次可执行意图；analysis/qa 不覆盖它）。"
            "短追问优先沿用它。"
        )
    if previous_intent:
        parts.append(f"上一轮判定：{previous_intent}")
    if dialogue:
        lines = ["最近两轮对话（由远及近；用户消息全文，助手消息首尾截断）："]
        for turn in dialogue:
            role = "用户" if turn.get("role") == "user" else "助手"
            lines.append(f"[{role}] {turn.get('content', '')}")
        parts.append("\n".join(lines))
    else:
        if previous_reply:
            parts.append(f"助手上一轮回复（末尾节选）：\n{previous_reply}")
        if history:
            recent = "\n".join(f"- {item}" for item in history)
            parts.append(f"最近几条用户消息（由远及近）：\n{recent}")
    parts.append(f"本轮消息：{message}")
    return "\n\n".join(parts)


def suggest_intent(
    decision: RouterDecision,
    previous_intent: str | None = None,
    message: str = "",
    active_intent: str | None = None,
) -> str:
    """策略表：轴 → 建议意图。**只用于评测/影子日志，不改变线上形态。**

    ``active_intent`` 是会话里**最近一次可执行意图**。上一轮可能只是分析/问答，
    而真正没做完的活还在更早的轮次上 —— 所以「继续 / 修正 / 短追问」优先沿用它，
    而不是只看 ``previous_intent``。
    """
    speech, wants, target, continuation = (
        decision.speech_act,
        decision.wants,
        decision.target,
        decision.continuation,
    )
    text = message or ""
    immediate = previous_intent
    unfinished = active_intent if active_intent in ACTIONABLE else previous_intent
    # 短追问分两类：选择题/确认（「b」「确认事实」「这个」）跟**紧邻的上一轮**；
    # 动作型（「继续」「修复P2」「开始实施」）跟**未完成任务**。长消息默认按未完成
    # 任务理解 —— 上一轮可能只是 analysis/qa 的解释，活还在更早的轮次上。
    short_followup = (
        bool(text.strip()) and len(text.strip()) <= 8 and continuation == "continue_previous"
    )
    action_followup = short_followup and (
        any(word in text for word in SHORT_IMPERATIVES) or has_continue_marker(text)
    )
    context = (unfinished if action_followup else immediate) if short_followup else unfinished
    if (
        target == "execution"
        and context in ACTIONABLE
        and any(m in text for m in _OBSERVE_MARKERS)
    ):
        return "qa"
    # 长句里的“不对”后面常跟着“应该先……再派 coder 执行”：这是**新指令**，不是单纯纠正
    # 上一轮。只有短句或 wants 不是 change 时，才把“不是/不对”当沿用未完成任务的信号。
    correction_only = (
        any(m in text for m in ("不是", "不对"))
        and not any(
            m in text
            for m in ("继续", "接着", "修正", "纠正", "重来", "再试一次", "为什么不", "为什么没")
        )
    )
    if (
        context in ACTIONABLE
        and has_continue_marker(text)
        and not (correction_only and wants == "change" and len(text.strip()) > 8)
    ):
        return context
    if wants == "change" and any(m in text for m in _IMPLEMENT_PLAN_COMMANDS):
        return context if context in {"rework", "coding"} else "coding"
    # 在 plan 上要“实施/执行/改代码”＝写代码，不是再改方案对象。
    if context == "plan" and wants == "change" and target in {"execution", "code"}:
        return "coding"
    if context in ACTIONABLE and any(m in text for m in _POST_PR_CLOSE_MARKERS):
        return "qa"

    # 短追问 + 继续上一轮：target 经常漂（「b」被判成 execution、「修复P2」被判成 code），
    # 但用户只是在接着上一轮做。沿用 context（analysis/qa 也保留），不要因为 target 换形态。
    if (
        text.strip()
        and speech in {"follow_up", "meta_question"}
        and continuation == "continue_previous"
        and len(text.strip()) <= 8
        and context in ACTIONABLE
        and target in {"general", "unknown", "execution", "code"}
        and not any(word in text for word in _PLAN_WORDS)
    ):
        return context

    # 裸「为什么 / 怎么 / 根因」：即使 target=code、模型把 wants 判成 lookup，也是解释型问题。
    # 「为什么不派 / 为什么没改」这类追问上一轮动作的句子会先命中上面的 continue marker，
    # 仍按未完成任务处理，不受这条影响。
    if wants in {"lookup", "analyze"} and any(m in text for m in _WHY_MARKERS):
        return "qa"

    # 明确的“问已有数据”（有什么/是什么/有哪些/多少）：即使 wants 被判成 decide，
    # 也只是问答，不要因为上下文里有未完成任务就变成 action。
    if (
        speech in {"follow_up", "meta_question", "chat"}
        and wants in {"lookup", "analyze", "decide", "unknown"}
        and target in {"general", "code", "execution"}
        and any(m in text for m in _ASK_MARKERS)
    ):
        return "qa"

    if target in {"execution", "code"} and any(m in text for m in _QUESTION_MARKERS):
        return "qa"
    # 短追问（“B”“确认事实”“修复吧”）：不要因为 router 的 target 漂移就换形态。
    if (
        len(text) <= 8
        and context in ACTIONABLE
        and target == "plan"
        and not any(word in text for word in _PLAN_WORDS)
        and any(word in text for word in SHORT_IMPERATIVES)
    ):
        return "coding" if context == "plan" else context
    if (
        len(text) <= 8
        and context in ACTIONABLE
        and target == "plan"
        and not any(word in text for word in _PLAN_WORDS)
    ):
        return context
    if len(text) <= 8 and context in ACTIONABLE and target in {"general", "unknown"}:
        return context
    if speech == "chat":
        return "qa"
    if speech in {"meta_question", "follow_up"}:
        # 追问本身（continuation=continue_previous）不等于要动手：只有 wants 明确要改/要决定，
        # 或 wants 没判出来但用户明确在接着上一轮做，才沿用上一轮动作。
        wants_continue = wants in {"change", "decide"} or (
            wants == "unknown" and continuation == "continue_previous"
        )
        if not wants_continue:
            if target == "plan":
                # 计划类一律 plan（用户口径：任务完成多少、方案本身都归 plan）
                return "plan"
            if target == "pr_status":
                return "qa"
            if target == "pr_review":
                return "rework"
            if target == "pr_change":
                return "qa" if wants in {"lookup", "analyze"} else "rework"
            if target == "memory":
                return "qa"
            if target == "code":
                return "qa"
            return "qa"
        if target == "plan":
            return "plan"
        if target == "pr_change":
            return "rework"
        if target == "pr_review":
            return "rework"
        if target == "pr_status":
            return "qa"
        if target == "memory":
            return "qa"
        if target == "code":
            return "coding"
        # execution / unknown：上一轮是可执行意图就沿用，否则按要干活处理
        return context if context in ACTIONABLE else "coding"
    # new_task
    if target == "pr_change":
        return "rework"
    if target == "pr_review":
        return "rework"
    if target == "pr_status":
        return "qa"
    if target == "plan":
        return "plan"
    if target == "memory":
        return "qa"
    if target == "code":
        if wants == "lookup":
            return "qa"
        return "qa" if wants == "analyze" else "coding"
    if target == "general" and any(word in text for word in ("如何", "怎么", "方法", "步骤")):
        return "qa"
    if target == "general" and wants == "decide":
        return "qa"
    if wants == "lookup":
        return "qa"
    if wants == "analyze":
        return "qa"
    return "coding"


async def route(
    model: BaseChatModel,
    message: str,
    *,
    history: Sequence[str] = (),
    previous_reply: str | None = None,
    previous_intent: str | None = None,
    active_intent: str | None = None,
    dialogue: Sequence[Mapping[str, str]] = (),
    timeout: float = 5.0,
) -> RouterDecision | None:
    """问一次路由器；任何失败都返回 ``None``，调用方只记日志、不改行为。"""
    started = time.perf_counter()
    prompt = _user_prompt(
        message,
        history,
        previous_reply,
        previous_intent,
        active_intent,
        dialogue,
    )
    try:
        reply = await asyncio.wait_for(
            model.ainvoke(
                [
                    SystemMessage(content=ROUTER_SYSTEM_PROMPT),
                    HumanMessage(content=prompt),
                ]
            ),
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 —— 影子层失败不能影响主流程
        logger.info(
            "%s 路由失败：%s: %s（耗时 %.0fms）",
            ROUTER_LOG_TAG,
            type(exc).__name__,
            exc,
            (time.perf_counter() - started) * 1000,
        )
        return None
    content = _reply_text(reply)
    decision = parse_router_reply(content)
    if decision is None:
        # 只对解析失败重试一次：正常路径不多付一次模型调用。
        try:
            reply = await asyncio.wait_for(
                model.ainvoke(
                    [
                        SystemMessage(content=ROUTER_SYSTEM_PROMPT),
                        HumanMessage(content=prompt),
                        HumanMessage(content=ROUTER_RETRY_PROMPT),
                    ]
                ),
                timeout=timeout,
            )
            content = _reply_text(reply)
            decision = parse_router_reply(content)
        except Exception as exc:  # noqa: BLE001 —— 影子层失败不能影响主流程
            logger.info(
                "%s 路由重试失败：%s: %s（耗时 %.0fms）",
                ROUTER_LOG_TAG,
                type(exc).__name__,
                exc,
                (time.perf_counter() - started) * 1000,
            )
    if decision is None:
        logger.info(
            "%s 路由回复无法解析（耗时 %.0fms）：%r",
            ROUTER_LOG_TAG,
            (time.perf_counter() - started) * 1000,
            content[:200],
        )
    return decision


def _reply_text(reply: Any) -> str:
    content = getattr(reply, "content", "")
    if isinstance(content, list):
        content = "".join(
            part if isinstance(part, str) else str(part.get("text", ""))
            for part in content
            if isinstance(part, (str, dict))
        )
    return str(content)


__all__ = [
    "ACTIONABLE",
    "CONTINUATIONS",
    "ROUTER_LOG_TAG",
    "ROUTER_SYSTEM_PROMPT",
    "SPEECH_ACTS",
    "TARGETS",
    "WANTS",
    "RouterDecision",
    "parse_router_reply",
    "route",
    "suggest_intent",
]
