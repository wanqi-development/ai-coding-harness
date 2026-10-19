"""兜底中间件：主导模型彻底失败时切到另一家，并留一条可查的痕。

上游 ``ModelFallbackMiddleware`` 已经实现了重试循环（``langchain/agents/middleware/
model_fallback.py:373``）：先 ``await handler(request)``，抛错就逐个
``handler(fallback_request.override(model=兜底))``，全败再把**最后一个**异常抛出。
它唯一缺的是痕迹 —— 兜底成功时整条链路一声不响，事后反查不出「这一轮到底跑在哪家
模型上」。所以这里只覆写 ``awrap_model_call``：把 ``handler`` 包一层计数闭包，第 2 次
被调用即「主导已失败、兜底接手」，此刻记一条 WARNING；重试循环仍旧交给 ``super()``。

痕迹只记**异常类型与 HTTP 状态码**，不记异常原文：供应商的报错体可能把 api key 原样
回显（``invalid api key: sk-…``），而这是条不落盘的红线。异常类型本身够判断 ——
``RateLimitError`` / ``InternalServerError`` / ``APITimeoutError`` / ``APIConnectionError``
四者已经指认了原因。

再加一层**熔断**（自己写的状态机，没引依赖）：主导连续失败到阈值就开闸，冷却期内不再
撞它 —— 省掉每一轮「先撞一次已死的模型」那段等待。这不是优化细节：智能体一轮对话要跑
多次模型节点（model → tools → model …），主导死着的时候，**每一轮**都得先撞死一次才轮到
兜底，含半路断流那种情形还会把半截句子重复吐进流里。按**主导模型名**记状态，所以同一家
挂了，用这家的所有角色一起受益；状态存哪见 ``agent/circuit.py`` —— 默认进程内（模块级
一份、跨角色共享），配了 Redis 则跨 worker 共享（多 worker 下各记一份等于没有熔断）。

只实现异步版：服务端统一走 ``astream_events``（与 ``boundary.py`` 同一约定）。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from langchain.agents.middleware import (
    ExtendedModelResponse,
    ModelFallbackMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.errors import GraphBubbleUp

from ai_coding_harness.agent.circuit import (
    CircuitState,
    CircuitStore,
    MemoryCircuitStore,
)

logger = logging.getLogger(__name__)

# 查降级走这个关键字：grep MODEL_FALLBACK logs/agent.log
FALLBACK_LOG_TAG = "MODEL_FALLBACK"
# 查熔断走这个关键字：grep MODEL_CIRCUIT logs/agent.log
CIRCUIT_LOG_TAG = "MODEL_CIRCUIT"

# 时钟留成模块级名字，单测替换它就能验「冷却到点放探针」而不用真等 60 秒
_now: Callable[[], float] = time.monotonic


def _model_name(model: BaseChatModel) -> str:
    """模型名。四家都经 ``init_chat_model`` 建成 ``ChatOpenAI``，取不到就退回类名。"""
    return str(getattr(model, "model_name", None) or type(model).__name__)


def _error_summary(exc: Exception | None) -> str:
    """异常摘要：类型 + 状态码（如 ``RateLimitError/429``），**不含异常原文**。"""
    if exc is None:
        return "-"
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return f"{type(exc).__name__}/{status}"
    return type(exc).__name__


# 默认那份状态：进程内、按**主导模型名**记、跨角色跨请求共享 —— 同一家坏了，用这家的所有
# 角色一起受益（``reviewer`` 撞死 kimi 开到闸，别的 kimi 角色也免撞）。**必须**是模块级
# 单例：每个角色各 new 一个中间件实例，状态要是跟着实例走就串不成一份了。
_MEMORY_CIRCUITS = MemoryCircuitStore()


def reset_circuits() -> None:
    """清空默认那份熔断状态（单测用：它是模块级单例，会跨用例串味）。"""
    _MEMORY_CIRCUITS.clear()


class _PrimarySkipped(Exception):
    """熔断期内占位用的「假失败」：不进模型，只为让上游的兜底路径照常接手。

    它不会被记进 ``MODEL_FALLBACK`` —— 那条痕的 ``error=`` 要填真实原因，而这里压根
    没有发生过模型调用；该轮的路由由 ``MODEL_CIRCUIT state=open`` 那行负责交代。
    """


class FallbackMiddleware(ModelFallbackMiddleware):
    """主导失败时切 ``fallback``，并记 ``MODEL_FALLBACK`` / ``MODEL_CIRCUIT`` 的痕。

    ``role`` 是路由表（``llm._AGENT_KEY``）里的角色名 —— 主控的角色名就是形态名
    （``profile.form``）；``agent`` 是日志 ``%(agent)s`` 那一列的标注（主控用
    ``MAIN_AGENT``、子智能体用角色名）。子智能体两者同值，主控不同，所以分开给：主控
    那一行既要认得出是主控（和其它运行日志一致），又要认得出是哪个形态。

    这类实例**每个角色各 new 一个**（与 ``BoundaryMiddleware`` 同理）：它持有该角色的
    角色名与兜底模型，共用一个会串台。熔断状态**不**随实例走，而是挂在模块级字典上、
    按主导模型名共享 —— 这正是要的：同一家模型的多个角色不该各记各的账。
    """

    def __init__(
        self,
        *,
        role: str,
        agent: str,
        fallback: BaseChatModel,
        circuit_threshold: int = 0,
        circuit_cooldown: float = 60.0,
        circuit: CircuitStore | None = None,
    ) -> None:
        """``fallback`` 是**兜底**模型（上游的首参语义就是「第一个兜底」）。

        ``circuit_threshold`` 为 0 即关掉熔断，退回「每轮都撞一次主导」的老行为
        （现有用例与不喜欢这层的人走这条）。

        ``circuit`` 是熔断状态的存取（默认模块级那份进程内实现；配了 Redis 时由装配处
        换成跨 worker 共享的那份）。
        """
        super().__init__(fallback)
        self._role = role
        self._agent = agent
        # 熔断期那行要报兜底是谁，而那一刻 handler 还没拿到兜底请求，所以先存下来
        self._fallback_name = _model_name(fallback)
        self._threshold = circuit_threshold
        self._cooldown = circuit_cooldown
        self._circuit = circuit or _MEMORY_CIRCUITS

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | ExtendedModelResponse:
        primary = _model_name(request.model)
        attempts = 0
        cause: Exception | None = None
        skipped = False

        async def counted(request_: ModelRequest) -> ModelResponse:
            nonlocal attempts, cause, skipped
            attempts += 1
            first = attempts == 1
            if first:
                may_try, state = await self._may_try(primary)
                if not may_try:
                    # 熔断中：这一步就是本层的全部收益 —— 不碰网络，直接落给兜底
                    skipped = True
                    cause = _PrimarySkipped()
                    self._log_skip(primary, state)
                    raise cause
            if not first and not skipped:
                # 第 2 次进来＝主导已失败、兜底正接手；再次进来＝上一个兜底也失败了
                logger.warning(
                    "%s role=%s primary=%s fallback=%s error=%s",
                    FALLBACK_LOG_TAG,
                    self._role,
                    primary,
                    _model_name(request_.model),
                    _error_summary(cause),
                    extra={"agent": self._agent},
                )
            try:
                result = await handler(request_)
            except GraphBubbleUp as exc:
                # 控制流异常（暂停/恢复/嵌套图的跳转）不是供应商故障：原样抛、不计数，
                # 但探针标志要放掉，否则这个熔断窗再也不会被探
                cause = exc
                if first:
                    await self._note_primary(primary, failed=False)
                raise
            except Exception as exc:
                cause = exc
                if first:
                    await self._note_primary(primary, failed=True)
                raise
            if first:
                await self._note_primary(primary, failed=False)
            return result

        return await super().awrap_model_call(request, counted)

    # -- 熔断状态机 ----------------------------------------------------------

    def _log_skip(self, primary: str, state: CircuitState) -> None:
        age = int(_now() - state.opened_at) if state.opened_at is not None else 0
        logger.warning(
            "%s role=%s primary=%s state=open failures=%d age=%ds fallback=%s",
            CIRCUIT_LOG_TAG,
            self._role,
            primary,
            state.failures,
            age,
            self._fallback_name,
            extra={"agent": self._agent},
        )

    async def _may_try(self, primary: str) -> tuple[bool, CircuitState]:
        """这会儿能不能撞主导，外加当时的状态（日志要用）。

        关着＝能；开着＝冷却到点后放**一次**探针。
        """
        state = await self._circuit.load(primary)
        if self._threshold <= 0:
            return True, state
        if state.failures < self._threshold:
            return True, state
        if state.probing:
            return False, state  # 探针已经放出去了，别同时放第二个
        opened_at = state.opened_at
        if opened_at is None or _now() - opened_at < self._cooldown:
            return False, state
        state.probing = True
        await self._circuit.save(primary, state)
        return True, state

    async def _note_primary(self, primary: str, *, failed: bool) -> None:
        """主导这一次的结局。``failed=False`` 覆盖「成功」与「控制流异常」两种。

        控制流异常不能算主导的账：它跟供应商是否健康无关，算进去会让熔断被无关的中断
        顶开。但探针标志得放掉，否则那一轮之后不再有探针，熔断窗就永不开闸。
        """
        if self._threshold <= 0:
            return
        state = await self._circuit.load(primary)
        was_probing, state.probing = state.probing, False
        if not failed:
            if was_probing:
                logger.warning(
                    "%s role=%s primary=%s state=closed failures=0 probe=ok",
                    CIRCUIT_LOG_TAG,
                    self._role,
                    primary,
                    extra={"agent": self._agent},
                )
            state.failures = 0
            state.opened_at = None
            await self._circuit.save(primary, state)
            return
        state.failures += 1
        if state.failures < self._threshold:
            await self._circuit.save(primary, state)
            return
        state.opened_at = _now()
        await self._circuit.save(primary, state)
        logger.warning(
            "%s role=%s primary=%s state=%s failures=%d cooldown=%gs",
            CIRCUIT_LOG_TAG,
            self._role,
            primary,
            "reopen" if was_probing else "open",
            state.failures,
            self._cooldown,
            extra={"agent": self._agent},
        )
