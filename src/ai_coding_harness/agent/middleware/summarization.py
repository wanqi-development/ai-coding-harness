"""压缩（摘要）中间件：**我们自己构造那一份**，上游那份按类排掉。

为什么不用上游的工厂 ``deepagents.graph.create_summarization_middleware`` —— 它的默认值有
三处不合用，而且**都没暴露成参数**（签名只有 ``summary_prompt`` / ``trim_tokens_to_summarize``
/ ``token_counter``）：

1. **trigger 只有窗口比例**（``compute_summarization_defaults``：模型带 profile 时是
   ``("fraction", 0.85)``）。窗口现在按**模型**查（表 ``provider_models``），动辄 1M：
   ``qwen3.8-max`` 是 991808，比例线就落在 843k —— 而 2026-10-15 那轮 6.9M token 的
   **单次峰值只有 152k，永远压不到**。所以这里补一条**绝对上限**，与比例那条是 OR
   （上游 ``TriggerClause`` 的 list 就是 OR 语义）。绝对那条还有个好处：上游判它时会顺带看
   上一条 ``AIMessage`` 里供应商**自报**的 ``usage_metadata.total_tokens``（真值），所以即便
   我们这边的估算偏了，真值也能把它触发。
2. **``trim_tokens_to_summarize`` 被工厂静默设成 ``None``** —— 工厂默认 ``None`` 并**显式**
   传给类，把类自己的 4000 覆盖掉；langchain 那边 ``None`` = 完全不裁。于是压缩触发的那一刻
   会额外发一次「输入 ≈ 被逐出的全部历史」的调用，用的还是**主控自己那个贵模型**；而
   ``awrap_model_call`` 只对**主调用**捕 ``ContextOverflowError``，摘要调用自己溢出时是往上抛
   的 —— 恰好在系统试图自救的那一刻整轮挂掉。这里给它一个上限。
3. **keep 是 ``("fraction", 0.10)``**：1M 窗口下等于「压完还留 100k」，而旧口径触发线只有 120k，
   压了等于没压。改成绝对值。

4. **压缩前先剪工具**（2026-10-17，2026-10-19 加工作笔记）：到线后先做一次应急裁剪，
   保留「当前这一步」以及模型在 90% 提醒后写下的 ``KEEP:`` 清单点名的工具返回；剪完
   低于线就不摘要，仍高于才压。供应商自报的上一轮用量只用来触发节点，剪完后按估算值
   决定；估算偏小导致真实调用溢出时，回退压缩兜底。

位置也由我们自己定（``agent/graph.py`` / ``agent/subagents.py``）：现在**没有单独的
常规剪裁中间件**，压缩中间件自己负责到线后的应急裁剪，所以它数到的就是「真要发出去的
那一份」。

排掉上游那份用的是 ``HarnessProfile.excluded_middleware``（**按类精确匹配**，不是
``isinstance``），所以下面这个子类不会被误伤 —— 上游的 ``name`` 属性也正是这么设计的
（子类回落到 ``type(self).__name__``）。行为一概不改，改的只是构造参数与挂载位置。

最后，这一层**自己记一条账**（``CONTEXT_SUMMARY`` 那一行）：什么时候压的、压前压后各多长、
逐出的原文落到哪个文件。上游只把摘要那次调用记成一笔「内部用量」，看不出压缩到底省了多少 ——
而「省了多少」是判断触发线该不该往下调的唯一依据。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.summarization import SummarizationMiddleware

try:  # deepagents >= 0.6.12 的长名字
    from deepagents.middleware.summarization import (
        DEEPAGENTS_DEFAULT_SUMMARY_PROMPT as _DEFAULT_SUMMARY_PROMPT,
    )
except ImportError:  # deepagents 0.6.11：conda 里实际装的是这个
    from deepagents.middleware.summarization import (
        DEFAULT_SUMMARY_PROMPT as _DEFAULT_SUMMARY_PROMPT,
    )
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.exceptions import ContextOverflowError
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage

from ai_coding_harness.agent.context_calibration import CalibratedTokenCounter
from ai_coding_harness.agent.middleware.context_trim import (
    keep_items_from_messages,
    pretrim_tool_results,
)
from ai_coding_harness.config import Settings
from ai_coding_harness.observability.logging import MAIN_AGENT
from ai_coding_harness.tools.context import get_configurable

logger = logging.getLogger(__name__)

#: 查压缩走这个关键字：grep CONTEXT_SUMMARY logs/agent.log
SUMMARY_LOG_TAG = "CONTEXT_SUMMARY"

#: 查「压缩前应急裁剪」走这个关键字：grep CONTEXT_PRETRIM logs/agent.log
PRETRIM_LOG_TAG = "CONTEXT_PRETRIM"

#: 临近压缩线时注入的系统提醒：让模型在旧工具返回被剪之前，先把它读到的东西写成
#: 工作笔记 + ``KEEP:`` 清单。没有额外模型调用 —— 这段文字随本轮正常回复产出。
PRETRIM_NOTICE = """\
## 即将上下文裁剪（请先写工作笔记）
当前上下文已接近压缩线。下一轮会裁剪旧工具返回，只保留当前步骤和你在 KEEP 里点名的结果。
请在本轮回复里先用 3~6 行写一个工作笔记，放在 <working-note>...</working-note> 里：
- 从已读工具结果得到的结论/事实；
- 精确标识符（路径、命令、错误码、PR/plan ID）；
- 未完成事项/下一步；用户约束。
再用一行列出下一步仍需要的工具结果：
KEEP: tool_call_xxx, read_file:/path/File.java, execute:mvn ...
优先写 tool_call_id；不知道 ID 就写“工具名:目标”。不要重复文件全文，不要抄密钥。
"""

#: 压缩摘要的“必须保留”清单：插在默认提示词的 ``<messages>`` 之前。
#: 只写**仓库/工具里找不回来**的东西；完整文件、diff、重复输出都可以丢。
SUMMARY_MUST_KEEP = """\
## 必须保留（MUST KEEP）
你只看到被逐出的旧消息；最近一段消息会原样保留，不在你的输入里。
不要总结或复述最近消息；只保留理解它们所需的前情，以及旧消息里不可恢复的信息。

P0（优先保留；宁可少写背景，也不要丢这些）：
1. 用户显式约束/纠正：逐字引用关键短句（“不要做 X”“改成 Y”“路径是 Z”“必须先…”），
   不要改写成概括。只引用约束所在的短句，不要抄整段对话；
   若原话含 API key/token/密码/完整环境变量，必须打码，只写“已配置”。
2. 精确标识符：文件路径、函数/类签名、命令串、错误码、版本号、配置键值；原样保留，
   不要改写、缩写或“整理”。
3. 当前状态：目标/意图、plan_id、PR 编号/链接/分支/base/head/commit、合并状态、
   未完成步骤、todo 状态、下一步。
4. 决策与取舍：已确认的方案、被否决的选项及原因。

P1：
5. 进度与失败：已完成的步骤、测试/命令结果、失败原因、阻塞/风险/待确认。
6. 可恢复指针：被压缩的工具输出保留“工具名 + 目标 + tool_call_id（若有）”；
   完整原文会落盘到会话历史文件（摘要消息里会给出路径）。

P0 反向（MUST DROP，写了等于埋雷）：
- 不要保留任何“能力 / 形态 / 工具可用性”的结论（如“这轮没有 task”“只能只读”
  “工具清单里没有 X”）。它们逐轮都可能变，下一轮读摘要时会被当成现状；
  只保留这一轮实际做了什么、还差什么。

可以丢弃：完整文件内容、完整 diff、重复的工具输出、逐行日志、思考过程。
长度：800~1200 字；P0 条目可以超出，先删背景叙述。
"""


def _build_summary_prompt() -> str:
    """默认提示词 + 本项目必须保留清单；插在 ``<messages>`` 之前。"""
    marker = "<messages>"
    if marker in _DEFAULT_SUMMARY_PROMPT:
        return _DEFAULT_SUMMARY_PROMPT.replace(
            marker, f"{SUMMARY_MUST_KEEP}\n\n{marker}", 1
        )
    return f"{_DEFAULT_SUMMARY_PROMPT}\n\n{SUMMARY_MUST_KEEP}"


#: 传给 ``SummarizationMiddleware`` 的完整摘要提示词。
HARNESS_SUMMARY_PROMPT = _build_summary_prompt()


class HarnessSummarizationMiddleware(SummarizationMiddleware):
    """① **有个能被区分的身份**（见模块说明最后一段）；② 记一条压缩账；
    ③ 到压缩线先应急裁剪工具返回，剪完不到线就不压。

    账记四样：**什么时候压的**（日志行首那个时间戳）、**压前多长 / 压后多长**、
    **逐出的原文落到哪个文件**（``/conversation_history/{thread_id}.md`` —— 压完之后模型
    要回看细节只剩这条路）、以及**这一次耗时多久**（摘要是一次额外的大输入调用，慢起来
    在轮次总时长里很明显）。这四样在别处都查不到：``run_log`` 只把摘要那次调用记成
    「摘要调用完成：输入 X / 输出 Y」，既不说压前压后各多长，也不说压掉了几条。

    数字口径与**触发线同口径** ＝ 含系统提示与工具 schema（上游判触发用的就是它自己那个
    ``_count_tokens``，这里直接借它算，两边才不会各说各话），所以这一行的数能直接跟
    当前模式的触发线比。应急裁剪那一行
    （``CONTEXT_PRETRIM``）用的是同一个计数器，两行可以直接对比。

    只在**上下文真被改过**的时候才数 token：这一层每次模型调用都跑，而计数要把全部历史连
    工具 schema 一起序列化一遍。判「改没改」用对象身份逐条比 —— 上游不改状态，没动手时
    交给内层的就是进来时那一批对象。

    只实现异步版：服务端统一走 ``astream_events``（与 ``boundary.py`` 同一约定）。
    """

    def __init__(
        self,
        *args: Any,
        agent: str = MAIN_AGENT,
        mode: str = "burst",
        trigger_text: str = "-",
        pretrim: bool = True,
        pretrim_clear_inputs: bool = False,
        notice_at: int = 0,
        **kwargs: Any,
    ) -> None:
        """``agent`` 是日志 ``%(agent)s`` 那一列；``trigger_text`` 是触发线的人话版。

        触发线在**构造时**就渲染成文字（``describe_trigger``），不去读上游的私有字段：
        日志里要能看出「这次是哪条线触发的」，而 fraction 那条只有乘上窗口才有意义。

        ``pretrim`` 是「到压缩线先应急裁剪工具结果、剪完不到线就不压」这一层；它跟随
        ``CONTEXT_TRIM_TOOL_RESULTS`` 总开关（见 ``build_summarization``）。关掉时退回
        上游原来的行为：到线直接压，供应商自报的上一轮用量也能强制触发。

        ``notice_at`` 是「压缩线的 90%」那条提醒线：到这条线先让模型写工作笔记，
        下一轮真正裁剪时按笔记里的 ``KEEP:`` 清单保留；0 = 关闭。
        """
        super().__init__(*args, **kwargs)
        self._agent = agent
        self._mode = mode
        self._trigger_text = trigger_text
        self._pretrim = pretrim
        self._pretrim_clear_inputs = pretrim_clear_inputs
        self._notice_at = notice_at

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        # 上游 ``awrap_model_call`` 的前半段先复刻一遍：取有效消息、计一次数、截断老工具
        # 入参。只有先知道「到没到压缩线」，才能在压之前做应急裁剪；而上游一旦决定压，
        # 就没有「剪完再判一次」的钩子 —— 所以后半段由我们自己接管。
        effective = self._get_effective_messages(request)
        total = self._count_tokens(effective, request.system_message, request.tools)
        truncated, args_modified = self._truncate_args(effective, total)
        if args_modified:
            total = self._count_tokens(truncated, request.system_message, request.tools)

        # 到压缩线的 90%：先提醒模型写工作笔记，下一轮再按 KEEP 清单裁。
        # 如果这一轮已经过线，就先不剪，给它一次正常回复的机会；真溢出再回落。
        request, notice_sent = self._maybe_notice(request, total)
        if notice_sent and self._should_summarize_estimate(truncated, total):
            response = await self._call_without_summary(
                request, truncated, handler, estimated=total
            )
            if response is not None:
                return response

        if not self._should_summarize(truncated, total):
            response = await handler(request.override(messages=truncated))
            self._record_usage(response, total)
            return response

        # 到了压缩节点。先看是不是只有「供应商自报的上一轮用量」在撑线：正常剪裁已经把
        # 当前请求剪小了，那个旧值不该再强制压缩 —— 否则「剪完不到线就不压」永远不成立。
        if self._pretrim and not self._should_summarize_estimate(truncated, total):
            self._log_pretrim_skip(total)
            response = await self._call_without_summary(
                request, truncated, handler, estimated=total
            )
            if response is not None:
                return response
            # 真实调用溢出：落到下面的压缩路径。

        # 应急裁剪：只保留末尾连续的工具返回（当前这一步），其余换成带身份占位符。
        # 它只用来判断「能否免压」；真正继续压缩时仍用上面那份原文，别让摘要与 offload
        # 读到占位符。
        if self._pretrim:
            keep_items = keep_items_from_messages(truncated)
            if keep_items:
                logger.info(
                    "%s 工作笔记 KEEP=%s（按笔记保留这些工具返回）",
                    PRETRIM_LOG_TAG,
                    list(keep_items),
                    extra={"agent": self._agent},
                )
            pretrimmed, cleared = pretrim_tool_results(
                truncated,
                clear_tool_inputs=self._pretrim_clear_inputs,
                keep_items=keep_items,
            )
            if cleared:
                after = self._count_tokens(
                    pretrimmed, request.system_message, request.tools
                )
                still_over = self._should_summarize_estimate(pretrimmed, after)
                self._log_pretrim(total, after, cleared, still_over)
                if not still_over:
                    response = await self._call_without_summary(
                        request, pretrimmed, handler, estimated=after
                    )
                    if response is not None:
                        return response
                    # 真实调用溢出：落到下面的压缩路径。

        # 继续压缩。用预剪前的 ``request``（而不是 ``pretrimmed``）：摘要与逐出原文保留
        # 更多细节，预剪只承担「能否免压」这一次判断。
        sent: ModelRequest | None = None

        async def spy(inner: ModelRequest) -> ModelResponse:
            nonlocal sent
            sent = inner
            return await handler(inner)

        started = time.perf_counter()
        result = await super().awrap_model_call(request, spy)
        if sent is not None:
            cost = time.perf_counter() - started
            self._log_compaction(request, truncated, sent.messages, result, cost)
            # 压缩后的那一份才是真正发出去的：它的估算与 usage 才是同一个请求的两面。
            sent_tokens = self._count_tokens(
                sent.messages, sent.system_message, sent.tools
            )
            self._record_usage(result, sent_tokens)
        return result

    def _should_summarize_estimate(
        self, messages: Sequence[Any], total_tokens: int
    ) -> bool:
        """按**估算值**判断是否过线，不看供应商自报的上一轮用量。

        上游 ``_should_summarize`` 在 tokens / fraction 两条上还会看最后一条 AIMessage
        的 ``usage_metadata.total_tokens`` —— 那是**上一次请求**的真值，剪完之后当前
        请求已经变小，不能拿它强制压缩。做法是在副本里清掉最后一条 AIMessage 的
        usage_metadata，再复用上游那套 OR 判据；不改真正要发出去的消息。
        """
        copied = list(messages)
        for index in range(len(copied) - 1, -1, -1):
            message = copied[index]
            if isinstance(message, AIMessage):
                if message.usage_metadata is not None:
                    copied[index] = message.model_copy(update={"usage_metadata": None})
                break
        return self._should_summarize(copied, total_tokens)

    async def _call_without_summary(
        self,
        request: ModelRequest,
        messages: Sequence[Any],
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
        *,
        estimated: int,
    ) -> ModelResponse | None:
        """带着剪过的消息直接调模型；溢出返回 ``None``，让调用方改走压缩。"""
        try:
            response = await handler(request.override(messages=list(messages)))
        except ContextOverflowError:
            logger.info(
                "%s 预剪后估算未达压缩线，但真实调用溢出，改走压缩",
                PRETRIM_LOG_TAG,
                extra={"agent": self._agent},
            )
            return None
        self._record_usage(response, estimated)
        return response

    def _record_usage(self, response: Any, estimated: int) -> None:
        """把「这份消息的估算 vs 供应商真值」交给校准器；没有用量就什么也不做。"""
        recorder = getattr(self.token_counter, "record", None)
        if callable(recorder):
            recorder(response, estimated)

    def _maybe_notice(
        self, request: ModelRequest, total: int
    ) -> tuple[ModelRequest, bool]:
        """到提醒线时给系统消息加一段工作笔记要求；同一轮、同一智能体只加一次。"""
        if self._notice_at <= 0 or total < self._notice_at:
            return request, False
        state = get_configurable("turn_state")
        noticed = getattr(state, "pretrim_noticed", None)
        if isinstance(noticed, set):
            if self._agent in noticed:
                return request, False
            noticed.add(self._agent)
        base = request.system_message.text if request.system_message else ""
        text = f"{base}\n\n{PRETRIM_NOTICE}" if base else PRETRIM_NOTICE
        logger.info(
            "CONTEXT_PRETRIM_NOTICE agent=%s mode=%s total=%d notice_at=%d",
            self._agent,
            self._mode,
            total,
            self._notice_at,
            extra={"agent": self._agent},
        )
        return request.override(system_message=SystemMessage(content=text)), True

    def _log_pretrim_skip(self, total: int) -> None:
        logger.info(
            "%s 估算未达压缩线（%d tokens；触发线 %s），只有供应商自报的上一轮用量过线；"
            "本轮不压缩",
            PRETRIM_LOG_TAG,
            total,
            self._trigger_text,
            extra={"agent": self._agent},
        )

    def _log_pretrim(
        self, before: int, after: int, cleared: int, still_over: bool
    ) -> None:
        logger.info(
            "%s 压缩前应急裁剪工具返回：清掉 %d 条，%d → %d tokens（省 %d；触发线 %s）；%s",
            PRETRIM_LOG_TAG,
            cleared,
            before,
            after,
            before - after,
            self._trigger_text,
            "仍高于压缩线，继续压缩" if still_over else "已低于压缩线，本轮不压缩",
            extra={"agent": self._agent},
        )

    def _log_compaction(
        self,
        request: ModelRequest,
        effective: Sequence[Any],
        sent: Sequence[Any],
        result: Any,
        cost: float,
    ) -> None:
        if _same_messages(effective, sent):
            return
        before = self._count_tokens(list(effective), request.system_message, request.tools)
        after = self._count_tokens(list(sent), request.system_message, request.tools)
        event = _summarization_event(result)
        if event is None:
            # 走到这里＝只截断了老消息里的工具入参（上游那份 truncate_args_settings），没做摘要
            logger.info(
                "%s 截断历史工具入参：%d → %d tokens（省 %d；触发线 %s；消息 %d 条不变，"
                "耗时 %.1fs）",
                SUMMARY_LOG_TAG,
                before,
                after,
                before - after,
                self._trigger_text,
                len(sent),
                cost,
                extra={"agent": self._agent},
            )
            return
        summary = event.get("summary_message")
        logger.info(
            "%s 压缩上下文：%d → %d tokens（省 %d；触发线 %s）；消息 %d → %d 条、摘要 %d 字、"
            "逐出原文 → %s，耗时 %.1fs",
            SUMMARY_LOG_TAG,
            before,
            after,
            before - after,
            self._trigger_text,
            len(effective),
            len(sent),
            len(str(getattr(summary, "content", "") or "")),
            event.get("file_path") or "（没落盘）",
            cost,
            extra={"agent": self._agent},
        )


def _same_messages(before: Sequence[Any], after: Sequence[Any]) -> bool:
    """逐条比**对象身份**：上游不改状态，没动手时交给内层的就是进来那一批对象。"""
    return len(before) == len(after) and all(
        one is other for one, other in zip(before, after, strict=True)
    )


def _summarization_event(result: Any) -> dict[str, Any] | None:
    """从上游的返回值里取出这一次的压缩事件（没压就是 ``None``）。

    压完上游返回 ``ExtendedModelResponse``，``command.update`` 里带 ``_summarization_event``
    （cutoff / 摘要那条消息 / 逐出原文的落盘路径）；没压就是普通的 ``ModelResponse``。
    按属性取而不 ``isinstance``：不为一个字段把上游那个类型引进来（它挪过一次家）。
    """
    update = getattr(getattr(result, "command", None), "update", None)
    if not isinstance(update, dict):
        return None
    event = update.get("_summarization_event")
    return event if isinstance(event, dict) else None


#: 模型没有 profile 那一档的工具入参截断口径，照抄上游 ``compute_summarization_defaults``。
_ARG_TRUNCATE_BY_MESSAGES: dict[str, Any] = {
    "trigger": ("messages", 20),
    "keep": ("messages", 20),
}


def profile_window(model: Any) -> int | None:
    """模型 profile 里的 ``max_input_tokens``；没有（或不是正整数）就 ``None``。

    这一格由 ``agent/llm.py::_model_from`` 按解析出来的窗口写进去。**写不进去就没有
    fraction 可用** —— 上游 ``__init__`` 见到 fraction 而拿不到 profile 会直接 ``ValueError``，
    所以下面那份取值必须按它分档。
    """
    profile = getattr(model, "profile", None)
    if not isinstance(profile, dict):
        return None
    window = profile.get("max_input_tokens")
    return window if isinstance(window, int) and window > 0 else None


#: 两种上下文策略：稳态按窗口 70%，爆发按窗口 40%（2026-10-19 用户口径）。
CONTEXT_MODE_STEADY = "steady"
CONTEXT_MODE_BURST = "burst"
CONTEXT_MODE_LABELS = {
    CONTEXT_MODE_STEADY: "稳态",
    CONTEXT_MODE_BURST: "爆发",
}

_STEADY_AGENTS = frozenset(
    {"qa", "analysis", "coding", "orchestrate", "rework"}
)
_BURST_AGENTS = frozenset(
    {"plan", "planner", "coder", "review", "reviewer"}
)
_STEADY_FORMS = frozenset(
    {"qa", "analysis", "coding", "orchestrate", "rework"}
)
_BURST_FORMS = frozenset({"plan", "review"})


def resolve_context_mode(agent: str = MAIN_AGENT, form: str | None = None) -> str:
    """按角色 / 形态选稳态或爆发；未知角色默认爆发，避免大上下文失控。

    主控的 ``agent`` 是 ``main``，形态才是 qa / analysis / plan / review / orchestrate；
    子智能体的 ``agent`` 直接是 planner / coder / reviewer。
    """
    if form in _STEADY_FORMS:
        return CONTEXT_MODE_STEADY
    if form in _BURST_FORMS:
        return CONTEXT_MODE_BURST
    if agent in _STEADY_AGENTS:
        return CONTEXT_MODE_STEADY
    if agent in _BURST_AGENTS:
        return CONTEXT_MODE_BURST
    return CONTEXT_MODE_BURST


def summarization_defaults(
    model: Any,
    settings: Settings,
    *,
    mode: str = CONTEXT_MODE_BURST,
) -> dict[str, Any]:
    """算出 ``trigger`` / ``keep`` / ``truncate_args_settings`` 三份取值。

    ``keep`` 一律用绝对值（理由见模块说明第 3 条）。有窗口时，稳态取窗口 70%、
    爆发取窗口 40%，**再与一条绝对上限取 OR**（先到先触发）：比例线保护小窗口，
    绝对值防 1M 窗口把线推到 40 万（2026-10-20 真机：coder 194k 都没触发）。
    没有窗口时退回 ``CONTEXT_SUMMARY_TRIGGER_TOKENS``（保守兜底）。
    """
    if mode not in CONTEXT_MODE_LABELS:
        raise ValueError(f"未知上下文模式：{mode}")
    keep: Any = ("tokens", settings.context_summary_keep_tokens)
    if profile_window(model) is None:
        return {
            "trigger": ("tokens", settings.context_summary_trigger_tokens),
            "keep": keep,
            "truncate_args_settings": dict(_ARG_TRUNCATE_BY_MESSAGES),
        }
    ratio = (
        settings.context_steady_trigger_fraction
        if mode == CONTEXT_MODE_STEADY
        else settings.context_burst_trigger_fraction
    )
    cap = (
        settings.context_steady_trigger_tokens
        if mode == CONTEXT_MODE_STEADY
        else settings.context_burst_trigger_tokens
    )
    # 上游对 list 是 OR 语义：比例线或绝对上限，哪条先到都触发。
    trigger: Any = [("fraction", ratio), ("tokens", cap)]
    return {
        "trigger": trigger,
        "keep": keep,
        "truncate_args_settings": {"trigger": trigger, "keep": keep},
    }


def describe_trigger(trigger: Any, window: int | None) -> str:
    """把 ``trigger`` 取值渲染成人话（进 ``CONTEXT_SUMMARY`` 那一行日志）。

    记**算出来的数**而不是取值形状：调参的人要能一眼看出这次是哪条线触发的，而 fraction
    那条只有乘上窗口才有意义（1M 窗口的 0.85 是 843k，128k 的才是 108k）。多条是 OR 语义
    （上游 ``TriggerClause`` 的 list），所以用「或」连。
    """
    clauses = trigger if isinstance(trigger, list) else [trigger]
    parts: list[str] = []
    for clause in clauses:
        pairs = list(clause.items()) if isinstance(clause, dict) else [clause]
        for kind, value in pairs:
            if kind == "fraction":
                base = window or 0
                parts.append(f"窗口 {base}×{value}={int(base * float(value))}")
            elif kind == "tokens":
                parts.append(f"{value} tokens")
            else:
                parts.append(f"{kind} {value}")
    return " 或 ".join(parts) or "-"


def trigger_line(trigger: Any, window: int | None) -> int | None:
    """把 OR 触发的多个 clause 折成一条最低线（用于“压缩线 × 90%”提醒）。

    单个 clause 是 AND 语义，取其中最大的 token 要求；多个 clause 是 OR，取最小值。
    我们的 trigger 实际只有 ``fraction`` 与 ``tokens`` 两个单键 clause。
    """
    clauses = trigger if isinstance(trigger, list) else [trigger]
    lines: list[int] = []
    for clause in clauses:
        pairs = list(clause.items()) if isinstance(clause, dict) else [clause]
        values: list[int] = []
        for kind, value in pairs:
            if kind == "tokens":
                values.append(int(value))
            elif kind == "fraction" and window:
                values.append(int(window * float(value)))
        if values:
            lines.append(max(values))
    return min(lines) if lines else None


def build_summarization(
    model: BaseChatModel,
    backend: BackendProtocol,
    settings: Settings,
    *,
    agent: str = MAIN_AGENT,
    form: str | None = None,
    mode: str | None = None,
) -> HarnessSummarizationMiddleware | None:
    """按配置造压缩中间件；开关关掉就返回 ``None``（＝这个智能体不压）。

    ``summary_prompt`` 与工具入参截断的 ``max_length``（上游默认 2000 字符）都用上游默认值 ——
    这次只改「什么时候压 / 压完留多少 / 摘要调用最多吃多少」这三件。

    ``backend`` 要是当前会话那份混合后端：被逐出的原文由上游写到
    ``/conversation_history/{thread_id}.md``，那个前缀被 ``backends/composite.py`` 路由到
    StoreBackend（沙箱销毁后仍在）。

    ``agent`` 只喂日志的 ``%(agent)s`` 那一列（主控 ``main``、子智能体用角色名）：
    触发线是**按各自模型的窗口**算的，不标出来就对不上是哪一家的账。

    ``pretrim`` 跟随 ``CONTEXT_TRIM_TOOL_RESULTS``：到压缩线先做一次应急裁剪，剪完
    低于线就不压；关掉工具裁剪总开关时退回上游原来的「到线直接压」。
    """
    if not settings.context_summary_enabled:
        return None
    resolved_mode = mode or resolve_context_mode(agent, form)
    defaults = summarization_defaults(model, settings, mode=resolved_mode)
    window = profile_window(model)
    line = trigger_line(defaults["trigger"], window)
    # 提醒是应急裁剪的一部分：关掉工具裁剪时不要再要求模型写 KEEP（写了也不会生效）。
    ratio = settings.context_pretrim_notice_ratio
    notice_at = (
        int(line * ratio)
        if settings.context_trim_enabled and line and ratio > 0
        else 0
    )
    return HarnessSummarizationMiddleware(
        model,
        backend=backend,
        trigger=defaults["trigger"],
        keep=defaults["keep"],
        summary_prompt=HARNESS_SUMMARY_PROMPT,
        # 字符估算 × 按模型维护的真值系数（没有样本时就是 1.0，行为与之前一致）。
        token_counter=CalibratedTokenCounter(model, agent=agent),
        trim_tokens_to_summarize=settings.context_summary_input_tokens,
        truncate_args_settings=defaults["truncate_args_settings"],
        agent=agent,
        mode=resolved_mode,
        trigger_text=(
            f"{CONTEXT_MODE_LABELS[resolved_mode]}："
            f"{describe_trigger(defaults['trigger'], window)}"
        ),
        pretrim=settings.context_trim_enabled,
        pretrim_clear_inputs=settings.context_trim_clear_inputs,
        notice_at=notice_at,
    )
