"""智能体运行日志：给 ``astream_events(version="v3")`` 的每个事件标出「执行主角」。

主智能体与子智能体的模型调用、工具调用在事件流里长得一模一样（``langgraph_node``
同为 ``model``），能把它们分开的只有两处：

* ``messages`` 事件带 metadata，其中 ``lc_agent_name`` 就是发起这次模型调用的智能体名
  （主智能体是 ``graph.py`` 那个 ``name=``，即 ``MAIN_AGENT_NAME``；子智能体是名字）。
  有些路径 / 版本不带这一格，值可能是字符串 ``"None"`` —— 那种当「没名字」，归主控。
* ``tools`` 事件**不带 metadata**，只有 ``params["namespace"]``。所以顺手把
  ``lc_agent_name`` 按 namespace 记下来（``on_message``），工具事件再按 namespace 反查；
  查不到的一律算主智能体。

（v2 时代靠「task 工具的 checkpoint_ns 就是子智能体整段运行的 ns」来推断，v3 下
``task`` 的 ``tool-started`` 落在根 namespace，那条路已经不通了。）

日志行本身走 ``extra={"agent": ...}`` 交给 ``observability.logging`` 的格式串渲染。

工具**入参**也记在这里，所以落盘前必须过一遍脱敏：入参里可能是用户粘贴的令牌，返回值里
更可能是模型刚从沙箱读出来的那份。

**同一份事件流还用来记账**（``usage_snapshot``）：每次模型调用的 token 用量按执行主角
累加，随本轮记录落到业务库，界面据此显示「这一轮的钱花在谁身上」。归属判定与日志共用
``actor()``，所以账与日志不会各说各话。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any

from ai_coding_harness.core.stream import (
    TASK_TOOL,
    event_name,
    is_internal_call,
    metadata_of,
    method_of,
    namespace_of,
    payload_of,
)
from ai_coding_harness.observability.logging import MAIN_AGENT, MAIN_AGENT_NAME
from ai_coding_harness.safety.redact import redact

logger = logging.getLogger(__name__)

# 没有名字时的占位值：有的路径 / 版本不带宽，或给的是字符串 "None"（不是 None）
_NO_AGENT = frozenset({"", "None", "none"})

#: 「这一笔是不是主控」的全部可能署名：占位名与 ``graph.py`` 给主智能体的真名。
#: 只用其中之一认不出来 —— 实测主控的 ``lc_agent_name`` 是 ``MAIN_AGENT_NAME``，
#: 于是账被归到一个英文名下，而且峰值输入恒为 0（上下文压力整块失效，2026-10-12 踩到）。
_MAIN_NAMES = frozenset({MAIN_AGENT, MAIN_AGENT_NAME})

#: 中间件自己发起的模型调用（目前只有上下文摘要）在账上的归类名。它的用量挂在
#: 「主控」名下会把这笔开销藏起来 —— 实测单次就是 7 万~8 万输入 tokens。
INTERNAL_ACTOR = "internal"

#: 意图分类那次调用的归类名。它跑在事件流之外（``server/service.py`` 直接调），
#: 用量由 service 并进同一份账，所以归类名在这里定义、两处共用。
INTENT_ACTOR = "intent"

#: 主智能体在账上的归类名（与 ``MAIN_AGENT`` 同值；账的键是给前端按固定键查的，
#: 所以单独一个常量，将来前端要改口径只动这里）。
MAIN_ACTOR = "main"

#: 「本次产出」那一行的门槛：**输出**超过这么多 token 才记。
#:
#: 这行回答的是「这次调用为什么慢」：输出 1 万 token 而可见正文只有几百字，剩下的不是
#: 思考就是被丢掉的旁白。默认只记大调用（真正的正常调用是几百 token，不该被这行淹掉）；
#: 想每次都看，把它设成 0。
DETAIL_OUTPUT_TOKENS = 2000


def account_key(actor: str) -> str:
    """把执行主角的名字归成账上的归类名：主控（真名或占位名）→ ``MAIN_ACTOR``，其余原样。

    写账与读账（``api/routes/sessions.py`` 的聚合）都用它：本功能刚上线的那几轮把主控
    记在了真名下，读的时候归一次，界面上就不会冒出一个英文名。
    """
    return MAIN_ACTOR if actor in _MAIN_NAMES else actor


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


def delegation_result_text(output: Any) -> str:
    """取**委派**（``task``）返回的正文。

    ``task`` 回的不是 ``ToolMessage`` 而是 ``Command(update={"messages": [ToolMessage(...)]})``
    —— 上游 ``deepagents`` 要把子智能体那份状态一并带回来。直接 ``result_text`` 会拿到整个
    ``Command`` 的 repr，真正的回报埋在中间；所以先把那条 ToolMessage 抠出来。
    """
    update = getattr(output, "update", None)
    if isinstance(update, dict):
        messages = update.get("messages")
        if isinstance(messages, (list, tuple)) and messages:
            return result_text(messages[-1])
    return result_text(output)


def usage_numbers(usage: Any) -> tuple[int, int] | None:
    """把一次调用的用量解成 ``(输入, 输出)``；认不出来返回 ``None``。

    字段名**两种口径都认**（``input_tokens``/``prompt_tokens``、
    ``output_tokens``/``completion_tokens``）：不同 SDK / 网关回的形状不保证一致，
    而这里的返回值要进业务库当账，认不出必须能说「不知道」而不是当成 0
    （见 ``AgentRunLog.usage_snapshot`` 的 ``missing``）。
    """
    if not isinstance(usage, dict):
        return None
    inp = usage.get("input_tokens", usage.get("prompt_tokens"))
    outp = usage.get("output_tokens", usage.get("completion_tokens"))
    if inp is None and outp is None:
        return None
    return int(inp or 0), int(outp or 0)


def reasoning_tokens_of(usage: Any) -> int | None:
    """``usage`` 里单列的**思考 token** 数；网关没给这一格就返回 ``None``。

    位置按来源分三种，都是**读上游代码**确认的，不是猜的：

    * v3 事件里那份用量是 LangChain 归一后的 ``UsageMetadata``：思考在
      ``output_token_details`` 里，键就叫 ``reasoning``（按服务档位加前缀，如
      ``priority_reasoning``）—— 见 ``langchain_openai.chat_models.base._create_usage_metadata``；
    * 有的路径直接给 OpenAI 那份原始形状 ``completion_tokens_details.reasoning_tokens``；
    * Responses 那份是 ``output_tokens_details.reasoning_tokens``。

    认不出来**不当 0** —— 「没报」与「报了 0」是两件事，这正是这一行要看的东西。
    """
    if not isinstance(usage, dict):
        return None
    details = usage.get("output_token_details")
    if isinstance(details, dict):
        for key, value in details.items():
            if str(key).endswith("reasoning") and value is not None:
                return _as_int(value)
    for key in ("completion_tokens_details", "output_tokens_details"):
        nested = usage.get(key)
        if not isinstance(nested, dict):
            continue
        value = nested.get("reasoning_tokens")
        if value is not None:
            return _as_int(value)
    return None


def _as_int(value: Any) -> int | None:
    """数字就当数字用，认不出的返回 ``None``（不猜、也不当 0）。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def usage_of(event: dict) -> str:
    """从 ``message-finish`` 事件里取 token 用量（**给日志看的那一行**）。

    v3 把用量挂在 ``message-finish`` 的载荷上（bridge 逐块累加后写 ``usage``），
    v2 那份 ``data["output"].usage_metadata`` 已经不存在了。

    记账走 ``usage_numbers``（同一份解析），这里只管渲染成人可读的一行。
    """
    payload = payload_of(event)
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not usage:
        return "（无用量信息）"
    numbers = usage_numbers(usage)
    if numbers is None:
        return str(usage)
    return f"输入 {numbers[0]} / 输出 {numbers[1]} tokens"


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
    """把一次主智能体运行的事件流翻译成带执行主角的运行日志。

    除日志外还顺手**记账**：每次 LLM 调用的 token 用量按执行主角累加
    （``usage_snapshot``），供界面把「这一轮的钱花在谁身上」显示出来。
    两件事共用同一份事件流与同一个归属判定，所以账与日志不会各说各话。
    """

    def __init__(
        self,
        *,
        arg_limit: int,
        result_limit: int,
        delegation_result_limit: int | None = None,
        terms: Sequence[str] = (),
    ) -> None:
        self._arg_limit = arg_limit
        self._result_limit = result_limit
        #: 委派收尾那行的正文上限。单独一档是因为它记的是「一个子智能体这趟干成了什么」——
        #: 那是整趟工作唯一的落盘记录，``result_limit``（300 字）连一份 PR 摘要都放不下；
        #: 而它每轮只有几条，放宽也淹不了日志。``None`` ＝ 与普通工具返回同档。
        self._delegation_result_limit = (
            result_limit if delegation_result_limit is None else delegation_result_limit
        )
        # 本轮凭据词表：日志是四条出口里唯一「写下去就撤不回」的一条
        self._terms = tuple(terms)
        # namespace → 子智能体名（由 messages 事件的 lc_agent_name 登记，工具事件靠它反查）
        self._ns_agents: dict[tuple[str, ...], str] = {}
        # task 工具调用 id → 子智能体名（委派那两行日志的夹子）
        self._delegations: dict[str, str] = {}
        # 工具调用 id → 起始时刻，用于打印耗时
        self._tool_starts: dict[str, float] = {}
        # 执行主角 → [输入 tokens, 输出 tokens, 调用次数]（``usage_snapshot`` 的原料）
        self._usage: dict[str, list[int]] = {}
        # 平台没回用量的调用次数 —— 账上的缺口要能看见，不能悄悄当成 0
        self._usage_missing = 0
        # **主控**单次调用的最大输入 tokens：上下文压力的度量。
        # 为什么不包括子智能体：压缩只作用于主控那份历史（子智能体各有自己的窗口，
        # coder 是 1M），拿它们的输入去比主控的窗口会得出「永远安全」的错结论。
        self._main_peak_input = 0
        # **主控**这一轮最后一次调用的输入 tokens。压缩过的轮次用它而不是峰值：
        # 摘要是在主控那次超标的调用里发起的，所以那一轮的**末值**才是压缩后的真实占用，
        # 用峰值会让压力条永久停在「接近压缩线」（历史其实已经掉到 10%）。
        self._main_last_input = 0
        # 本轮出现过中间件内部调用（目前只有上下文摘要）＝ 这一轮发生了压缩。
        # 它是**现成的**信号：摘要只在压缩时发起，不必去上游埋钩子。
        self._compressions = 0
        #: namespace → [可见正文字数, 思考字数]。两类 delta 都是**增量**，直接累加；
        #: 每次 ``message-finish`` 取走并清零（小调用也要清，否则会算到下一次大调用头上）。
        self._delta_chars: dict[tuple[str, ...], list[int]] = {}
        #: (namespace, block 下标) → 工具参数的最大长度。``block-delta`` 给的是**累加值**
        #: 不是增量，所以取最大值而不是求和 —— 求和会把同一份 JSON 数好几遍。
        self._arg_chars: dict[tuple[tuple[str, ...], Any], int] = {}

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

    # -- 记账 --------------------------------------------------------------
    def _accumulate(self, actor: str, numbers: tuple[int, int] | None) -> None:
        """把这次调用的用量记到 ``actor`` 名下；认不出用量（``None``）的记一笔 ``missing``。

        ``numbers`` 由调用方解一次传进来：同一条事件既有账要记、又有产出明细要判，
        解两遍容易两处各认一半（一处认得、一处认不得）。
        """
        if numbers is None:
            self._usage_missing += 1
            return
        key = account_key(actor)
        lines = self._usage.setdefault(key, [0, 0, 0])
        lines[0] += numbers[0]
        lines[1] += numbers[1]
        lines[2] += 1
        # 上下文压力只看主控那一路（见 ``__init__`` 的说明）。两个数都留着，用法见
        # ``peak_input`` / ``last_input`` 的说明：没压缩的轮次看峰值（最近一次会因为
        # 缓存命中而偏小），压缩过的轮次看末值（峰值是压缩前那一次，会虚高）。
        if key == MAIN_ACTOR:
            self._main_peak_input = max(self._main_peak_input, numbers[0])
            self._main_last_input = numbers[0]

    # -- 产出明细（「这次调用为什么慢」） ----------------------------------
    def _note_delta(self, event: dict) -> None:
        """收一片输出增量：正文与思考累加字符数，工具参数按 block 取最大值（它是累加值）。

        **思考那一格的字段名与正文不一样**：``reasoning-delta`` 把文本放在 ``reasoning``
        里，正文才是 ``text-delta`` + ``text``（见
        ``langchain_core.language_models._compat_bridge._to_content_delta``）。照着正文的
        字段名去读思考，读到的永远是 0 字 —— 第一版就是这么错的，而且测试里那份假事件把
        字段名也写成了 ``text``，于是两边一起错、测试还是绿的（2026-10-12 真机一轮才发现）。
        """
        payload = payload_of(event)
        delta = payload.get("delta") if isinstance(payload, dict) else None
        if not isinstance(delta, dict):
            return
        kind = delta.get("type")
        namespace = namespace_of(event)
        if kind in ("text-delta", "reasoning-delta"):
            text = delta.get("text") if kind == "text-delta" else delta.get("reasoning")
            if isinstance(text, str) and text:
                bucket = self._delta_chars.setdefault(namespace, [0, 0])
                bucket[0 if kind == "text-delta" else 1] += len(text)
            return
        if kind != "block-delta":
            return
        fields = delta.get("fields")
        args = fields.get("args") if isinstance(fields, dict) else None
        if isinstance(args, str) and args:
            key = (namespace, payload.get("index"))
            self._arg_chars[key] = max(self._arg_chars.get(key, 0), len(args))

    def _take_output_detail(self, event: dict) -> str | None:
        """取走这个 namespace 的产出明细并清零；没有任何增量的返回 ``None``。

        **必须每次都取走**（哪怕这次调用小到不记日志）：不清的话，一串小调用的增量会
        攒到后面某个大调用头上，那一行就成了假账。
        """
        namespace = namespace_of(event)
        text_chars, reasoning_chars = self._delta_chars.pop(namespace, [0, 0])
        # 工具参数**按 block 相加**：一次消息里可以有多个工具调用（实测一轮里三个并行
        # read_file/grep），每个 block 自己取累加值的最大值（它的 args 是累加值），
        # 但不同 block 是不同次调用，必须加总 —— 取全局最大值会漏掉并行的那些。
        arg_chars = 0
        for key in [k for k in self._arg_chars if k[0] == namespace]:
            arg_chars += self._arg_chars.pop(key)
        if not (text_chars or reasoning_chars or arg_chars):
            return None
        parts = [f"可见正文 {text_chars} 字", f"思考 {reasoning_chars} 字"]
        if arg_chars:
            parts.append(f"工具参数 {arg_chars} 字")
        usage = (payload_of(event) or {}).get("usage")
        reasoning_tokens = reasoning_tokens_of(usage)
        if reasoning_tokens is not None:
            parts.append(f"网关记的思考 token {reasoning_tokens}")
        return " / ".join(parts)

    def usage_snapshot(
        self, *, interrupted: bool = False, context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """本轮的 token 账（写进业务库记录 ``extra["usage"]`` 的那份形状）。

        分类**按执行主角**，而不是按模型 —— 模型是路由表的实现细节，用户要看的是
        「钱花在哪个环节」：主控自己、三个子智能体、上下文摘要（中间件内部调用，
        单次就是几万输入，混进主控账里等于把这笔开销藏起来）、意图分类（流外调用，
        由 ``service`` 另外并进来）。

        ``interrupted`` 标这一轮**没跑完**（用户终止 / 守卫中止 / 故障）。中断的轮次
        同样入账 —— 钱是真花了 —— 只是如实标出来，界面上能看出这段数字不完整。

        ``context`` 是上下文压力那一格，由 ``service`` 填（**窗口只有它知道** ——
        按本轮主控形态取模型配置；子智能体的窗口与主控差一个数量级，拿错会得出
        「永远安全」的错结论）。形状见 ``service._turn_context``。
        """
        by_actor: dict[str, dict[str, int]] = {}
        total_in = total_out = 0
        total_calls = 0
        for actor, (inp, outp, calls) in self._usage.items():
            by_actor[actor] = {"input": inp, "output": outp, "calls": calls}
            total_in += inp
            total_out += outp
            total_calls += calls
        snapshot: dict[str, Any] = {
            # ``calls`` 也要给：这一格以前缺，聚合接口里的「合计调用」因此恒为 0
            # （界面只能自己数 ``by_actor``）。账面上三格齐了，读的人不必再自己推。
            "totals": {"input": total_in, "output": total_out, "calls": total_calls},
            "by_actor": by_actor,
            "missing": self._usage_missing,
            "interrupted": interrupted,
        }
        if context is not None:
            snapshot["context"] = context
        return snapshot

    @property
    def peak_input(self) -> int:
        """本轮**主控**单次调用的最大输入 tokens（上下文压力的原料，0 ＝ 没测到）。"""
        return self._main_peak_input

    @property
    def last_input(self) -> int:
        """本轮**主控**最后一次调用的输入 tokens（0 ＝ 没测到）。

        与 ``peak_input`` 的差别只在「这一轮发生过压缩」时有意义：那会儿峰值是压缩前那一次，
        末值才是压完之后的真实占用（见 ``service._turn_context`` 的取法）。
        """
        return self._main_last_input

    @property
    def compressions(self) -> int:
        """本轮发生的上下文压缩次数（依据：中间件内部调用出现了几次）。"""
        return self._compressions

    # -- 事件入口 ----------------------------------------------------------
    def on_message(self, event: dict) -> None:
        """``messages`` 通道的每条事件都过这里：登记归属，收尾时记用量。

        登记必须发生在这里而不是 ``actor()`` —— 工具事件不带 metadata，只有先把
        namespace 与名字对上，之后那些工具事件才认得出是谁调的。
        """
        name = self._named(event)
        if name:
            self._ns_agents.setdefault(namespace_of(event), name)
        name = event_name(event)
        if name == "content-block-delta":
            self._note_delta(event)
            return
        if name != "message-finish":
            return
        numbers = usage_numbers((payload_of(event) or {}).get("usage"))
        # 产出明细**无条件取走**（顺带清零），只是不一定记 —— 见 ``_take_output_detail``
        detail = self._take_output_detail(event)
        # 中间件内部的调用（目前只见过摘要）单独署名：它是大输入大输出的一次调用，
        # 混在「LLM 调用完成」里查账时看不出是谁花的 —— 正文那边则由 stream 丢掉。
        if is_internal_call(event):
            self._accumulate(INTERNAL_ACTOR, numbers)
            self._compressions += 1
            self._log(self.actor(event), "摘要调用完成：%s", usage_of(event))
        else:
            self._accumulate(self.actor(event), numbers)
            self._log(self.actor(event), "LLM 调用完成：%s", usage_of(event))
        # 大调用补一行「产出明细」：输出 1 万 token 而可见正文只有几百字 —— 那 1 万
        # 花在哪（思考？被丢掉的旁白？工具参数？）只有这行能说清。
        if numbers is not None and numbers[1] >= DETAIL_OUTPUT_TOKENS:
            self._log(self.actor(event), "本次产出：%s", detail or "没有收到任何输出增量")

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
            returned = delegation_result_text(output)
            self._log(
                MAIN_AGENT,
                "子智能体完成 ← %s %.1fs，返回 %d 字：%s",
                subagent,
                cost,
                len(returned),
                self._safe(returned, self._delegation_result_limit),
            )
            return
        self._log(self.actor(event), "工具完成 ← %s %.0fms", name, cost * 1000)
        self._log(
            self.actor(event),
            "工具返回 ← %s：%s",
            name,
            self._safe(result_text(output), self._result_limit),
        )
