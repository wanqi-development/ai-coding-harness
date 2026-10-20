"""Server 服务层：意图分析 → 直接执行 / 主智能体；并把过程记录到业务库。

API 路由只做 HTTP 转换，业务逻辑集中在这一层。
"""


import asyncio

from langchain_core.language_models.chat_models import BaseChatModel

from ai_coding_harness.config import Settings
from ai_coding_harness.db import Database, dao
from ai_coding_harness.infra import repo_cache
from ai_coding_harness.server.intent import IntentDecider, detect_intent
from ai_coding_harness.session.manager import SessionManager

from .turn import changes as turn_changes
from .turn import intent as turn_intent
from .turn import records as turn_records
from .turn import usage as turn_usage
from .turn.changes import TurnChangesMixin
from .turn.clarify import TurnClarifyMixin
from .turn.common import (
    _CHANGES_MAX_FILES,
    _PLAN_TOOLS,
    _REFUSAL_HINT,
    _REFUSAL_HINTS,
    _TITLE_MAX,
    _USAGE_FRAME_MIN_GAP,
    CANCELLED_BY_USER,
    INTERRUPTED_EMPTY,
    INTERRUPTED_PARTIAL,
    WORKSPACE_ERROR_MESSAGE,
    _main_model,
    _main_window,
    _pr_refs,
    _ratio,
    _redacted_parts,
    _redacted_todos,
    _title_from,
    credential_refusal,
    plan_payload,
)
from .turn.intent import TurnIntentMixin
from .turn.records import TurnRecordsMixin
from .turn.runner import TurnRunnerMixin
from .turn.usage import TurnUsageMixin


class HarnessService(
    TurnRunnerMixin,
    TurnIntentMixin,
    TurnClarifyMixin,
    TurnRecordsMixin,
    TurnUsageMixin,
    TurnChangesMixin,
):

    """会话编排：意图分类、直接执行、主智能体调用、业务库记录。"""

    def __init__(
        self,
        *,
        settings: Settings,
        manager: SessionManager,
        database: Database,
        intent_jev: IntentDecider | None = None,
    ) -> None:
        self._settings = settings
        self._manager = manager
        self._db = database
        #: 判定瀑布的第二关（口令 → **JEV** → 大模型 → 关键字口径）。``None`` ＝ 没配 / SDK
        #: 装不上，这一关直接跳过 —— 判定器缺一层不该影响服务可用性。
        self._intent_jev = intent_jev
        # 分类模型懒建一次即可：每会话每轮都建一个新实例会攒下未关闭的连接
        self._classifier: BaseChatModel | None = None
        #: 影子路由用的模型：优先「意图识别（大模型）」那一行；没配就退到任一条
        #: 已配置的 agent 行（原型阶段，分类任务不挂工具）。
        self._router_model: BaseChatModel | None = None
        #: 本轮意图判定的 token 累加器（``{"input":…, "output":…, "calls":…}``）。
        #: 判定跑在事件流之外，用量没法从 ``AgentRunLog`` 里拿到，所以在这里攒、
        #: 轮末并进本轮那份账（归类名 ``INTENT_ACTOR``，单独一项而不是并进主控）。
        #: ``_rejudge_intent`` 是工具在**同一轮的运行中**回调进来的，正是靠这个字段
        #: 才能把重判那次也算上。
        self._intent_usage: dict[str, int] = {}
        #: 会话 → [主控峰值输入 tokens, 累计压缩次数]。**跨轮**，所以按会话键在实例上留一份：
        #: 上下文压力是「这个会话的历史涨到多大了」，逐轮清零就永远只看到本轮那点量。
        #: 进程重启后从零开始（仅影响提示的准确度，不是账，不为此引入一张表）。
        self._context_state: dict[str, list[int]] = {}
        #: 上一次推 usage 帧的时刻（``time.monotonic()``；0 ＝ 本轮还没推过）。
        #: 逐轮清零，好让每轮的第一个进度点就能把账推出去。
        self._last_usage_frame = 0.0
        #: 影子路由器的后台 task（只记日志、不改变本轮形态）；持有引用防止被 GC。
        self._shadow_tasks: set[asyncio.Task[None]] = set()
        #: 会话 → 最近一次可执行意图（coding/rework/plan/review）；analysis/qa 不覆盖。
        #: 进程重启后从最近 assistant 记录回溯，不引入新表。
        self._active_tasks: dict[str, str] = {}


__all__ = [
    "CANCELLED_BY_USER",
    "INTERRUPTED_EMPTY",
    "INTERRUPTED_PARTIAL",
    "WORKSPACE_ERROR_MESSAGE",
    "_CHANGES_MAX_FILES",
    "_PLAN_TOOLS",
    "_REFUSAL_HINT",
    "_REFUSAL_HINTS",
    "_TITLE_MAX",
    "_USAGE_FRAME_MIN_GAP",
    "HarnessService",
    "_main_model",
    "_main_window",
    "_pr_refs",
    "_ratio",
    "_redacted_parts",
    "_redacted_todos",
    "_title_from",
    "credential_refusal",
    "dao",
    "detect_intent",
    "plan_payload",
    "repo_cache",
    "turn_changes",
    "turn_intent",
    "turn_records",
    "turn_usage",
]
