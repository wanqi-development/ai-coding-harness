"""智能体运行日志：给 ``astream_events(version="v3")`` 的每个事件标出「执行主角」。

主智能体与子智能体的模型调用、工具调用在事件流里长得一模一样（``langgraph_node``
同为 ``model``），能把它们分开的只有两处：

* ``messages`` 事件带 metadata，其中 ``lc_agent_name`` 就是发起这次模型调用的智能体名
  （主智能体是**字符串** ``"None"``，子智能体是名字）。直接可用。
* ``tools`` 事件**不带 metadata**，只有 ``params["namespace"]``。所以顺手把
  ``lc_agent_name`` 按 namespace 记下来（``on_message``），工具事件再按 namespace 反查；
  查不到的一律算主智能体。

（v2 时代靠「task 工具的 checkpoint_ns 就是子智能体整段运行的 ns」来推断，v3 下
``task`` 的 ``tool-started`` 落在根 namespace，那条路已经不通了。）

日志行本身走 ``extra={"agent": ...}`` 交给 ``observability.logging`` 的格式串渲染。

工具**入参**也记在这里，所以落盘前必须过一遍脱敏：入参里可能是用户粘贴的令牌，返回值里
更可能是模型刚从沙箱读出来的那份。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any

from ai_coding_harness.observability.logging import MAIN_AGENT
from ai_coding_harness.redact import redact
from ai_coding_harness.stream import (
    TASK_TOOL,
    event_name,
    is_internal_call,
    metadata_of,
    method_of,
    namespace_of,
    payload_of,
)

logger = logging.getLogger(__name__)

# 主智能体在 lc_agent_name 里的占位值（真名是字符串 "None"，不是 None）
_NO_AGENT = frozenset({"", "None", "none"})


def brief(value: Any, limit: int) -> str:
    """把工具参数压成一行短摘要，超过 ``limit`` 字符截断并加省略号。"""
    text = str(value).replace("\n", " ")
    return text[:limit] + ("…" if len(text) > limit else "")


def result_text(output: Any) -> str:
    """从工具返回值里取正文：``ToolMessage`` 取 ``content``，其余 ``str()``。

    直接 ``str(ToolMessage)`` 会把 content/name/tool_call_id 一起打成 repr，
    真正想看的返回值反而被埋在中间。
    """
    content = getattr(output, "content", None)
    if content is None:
        return str(output)
    return content if isinstance(content, str) else str(content)


def usage_of(event: dict) -> str:
    """从 ``message-finish`` 事件里取 token 用量。

    v3 把用量挂在 ``message-finish`` 的载荷上（bridge 逐块累加后写 ``usage``），
    v2 那份 ``data["output"].usage_metadata`` 已经不存在了。
    """
    payload = payload_of(event)
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not usage:
        return "（无用量信息）"
    if isinstance(usage, dict):
        inp = usage.get("input_tokens", usage.get("prompt_tokens", "?"))
        outp = usage.get("output_tokens", usage.get("completion_tokens", "?"))
        return f"输入 {inp} / 输出 {outp} tokens"
    return str(usage)


def _delegation_args(raw: Any) -> tuple[str, str]:
    """从 task 工具入参里取（子智能体名, 委派描述）。

    入参正常是 dict；模型给出畸形参数（不是 dict）时退回占位名，描述直接用原值，
    宁可日志难看也别在这里抛异常打断对话。
    """
    if isinstance(raw, dict):
        name = str(raw.get("subagent_type") or raw.get("name") or "?")
        return name, str(raw.get("description") or "")
    return "?", str(raw)


class AgentRunLog:
    """把一次主智能体运行的事件流翻译成带执行主角的运行日志。"""

    def __init__(
        self, *, arg_limit: int, result_limit: int, terms: Sequence[str] = ()
    ) -> None:
        self._arg_limit = arg_limit
        self._result_limit = result_limit
        # 本轮凭据词表：日志是四条出口里唯一「写下去就撤不回」的一条
        self._terms = tuple(terms)
        # namespace → 子智能体名（由 messages 事件的 lc_agent_name 登记，工具事件靠它反查）
        self._ns_agents: dict[tuple[str, ...], str] = {}
        # task 工具调用 id → 子智能体名（委派那两行日志的夹子）
        self._delegations: dict[str, str] = {}
        # 工具调用 id → 起始时刻，用于打印耗时
        self._tool_starts: dict[str, float] = {}

    def _safe(self, value: Any, limit: int) -> str:
        """先脱敏再截断。

        顺序不能反：先截断的话，横跨截断点的令牌会留下半截，既躲过脱敏又没了上下文。
        """
        return brief(redact(str(value), self._terms), limit)

    @staticmethod
    def _call_id(event: dict) -> str:
        payload = payload_of(event)
        return str(payload.get("tool_call_id") or "") if isinstance(payload, dict) else ""

    # -- 归属 --------------------------------------------------------------
    @staticmethod
    def _named(event: dict) -> str | None:
        """``messages`` 事件里自报的智能体名；主智能体那份占位值当作没名字。"""
        raw = metadata_of(event).get("lc_agent_name")
        name = "" if raw is None else str(raw)
        return name if name and name not in _NO_AGENT else None

    def actor(self, event: dict) -> str:
        """该事件的执行主角：子智能体名，或 ``MAIN_AGENT``。

        纯查表，不在这里登记：``on_message`` 负责把 namespace → 名字填进来
        （``actor`` 会在流里被反复调用，带副作用的话调用顺序就成了隐式依赖）。
        """
        if method_of(event) == "messages":
            return self._named(event) or MAIN_AGENT
        return self._ns_agents.get(namespace_of(event)) or MAIN_AGENT

    @staticmethod
    def _log(actor: str, message: str, *args: Any) -> None:
        logger.info(message, *args, extra={"agent": actor})

    # -- 事件入口 ----------------------------------------------------------
    def on_message(self, event: dict) -> None:
        """``messages`` 通道的每条事件都过这里：登记归属，收尾时记用量。

        登记必须发生在这里而不是 ``actor()`` —— 工具事件不带 metadata，只有先把
        namespace 与名字对上，之后那些工具事件才认得出是谁调的。
        """
        name = self._named(event)
        if name:
            self._ns_agents.setdefault(namespace_of(event), name)
        if event_name(event) == "message-finish":
            # 中间件内部的调用（目前只见过摘要）单独署名：它是大输入大输出的一次调用，
            # 混在「LLM 调用完成」里查账时看不出是谁花的 —— 正文那边则由 stream 丢掉。
            if is_internal_call(event):
                self._log(self.actor(event), "摘要调用完成：%s", usage_of(event))
                return
            self._log(self.actor(event), "LLM 调用完成：%s", usage_of(event))

    def on_tool_start(self, event: dict, name: str | None) -> None:
        """``name`` 由调用方从 ``tool-started`` 取好传进来。

        v3 的 ``tool-finished`` 不带工具名，收尾那条只能靠调用方配对；这里索性两个
        入口都收名字，省得一半自取一半外传。
        """
        call_id = self._call_id(event)
        self._tool_starts[call_id] = time.perf_counter()
        payload = payload_of(event) or {}
        if name == TASK_TOOL:
            subagent, description = _delegation_args(payload.get("input"))
            self._delegations[call_id] = subagent
            # 委派行记在主智能体名下：这是它的动作，也正好把子智能体那一段日志夹起来
            self._log(
                MAIN_AGENT,
                "委派子智能体 → %s：%s",
                subagent,
                self._safe(description, self._arg_limit),
            )
            return
        self._log(
            self.actor(event),
            "工具调用 → %s 参数=%s",
            name,
            self._safe(payload.get("input"), self._arg_limit),
        )

    def on_tool_end(self, event: dict, name: str | None) -> None:
        call_id = self._call_id(event)
        cost = time.perf_counter() - self._tool_starts.pop(call_id, time.perf_counter())
        subagent = self._delegations.pop(call_id, None)
        output = (payload_of(event) or {}).get("output", "")
        if subagent is not None:
            self._log(
                MAIN_AGENT,
                "子智能体完成 ← %s %.1fs，返回 %d 字",
                subagent,
                cost,
                len(str(output)),
            )
            return
        self._log(self.actor(event), "工具完成 ← %s %.0fms", name, cost * 1000)
        self._log(
            self.actor(event),
            "工具返回 ← %s：%s",
            name,
            self._safe(result_text(output), self._result_limit),
        )
