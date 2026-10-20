"""Server 服务层：意图分析 → 直接执行 / 主智能体；并把过程记录到业务库。

API 路由只做 HTTP 转换，业务逻辑集中在这一层。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from functools import partial
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import RunnableConfig

from ai_coding_harness import repo_cache
from ai_coding_harness.agent.llm import (
    build_classifier_model,
    build_model,
    resolve_model,
    resolve_window,
)
from ai_coding_harness.agent.middleware import TurnState, TurnStop, refresh_memory
from ai_coding_harness.agent.profiles import (
    ANALYSIS_FORM,
    ORCHESTRATE_FORM,
    REVIEW_FORM,
    profile_for,
)
from ai_coding_harness.backends.composite import memory_path
from ai_coding_harness.clarification import CLARIFY_INTENTS, parse_clarification
from ai_coding_harness.config import Settings
from ai_coding_harness.db import Database, dao
from ai_coding_harness.observability.logging import MAIN_AGENT
from ai_coding_harness.observability.run_log import INTENT_ACTOR, AgentRunLog
from ai_coding_harness.redact import (
    CRED_TERM,
    detect_credential,
    redact,
    secret_terms,
)
from ai_coding_harness.repo_cache import RepoSpec, RepoViewError
from ai_coding_harness.server.intent import (
    DIRECT_INTENTS,
    EXECUTION_INTENTS,
    PREVIOUS_REPLY_CHARS,
    PREVIOUS_REPLY_SLACK,
    SOURCE_ACTIVE,
    SOURCE_CLARIFY_CHOICE,
    IntentDecider,
    IntentDecision,
    active_task_override,
    clarification_decision,
    detect_intent,
    head_tail_excerpt,
    tail_excerpt,
)
from ai_coding_harness.server.intent_router import (
    ROUTER_LOG_TAG,
    route,
    suggest_intent,
)
from ai_coding_harness.session.manager import (
    Session,
    SessionManager,
    SessionRuntime,
    WorkspaceError,
)
from ai_coding_harness.stream import (
    CREATE_PR_TOOL,
    TASK_TOOL,
    ToolNames,
    V3Translator,
    call_id_of,
    event_name,
    method_of,
    payload_of,
    subagent_name,
)
from ai_coding_harness.tools.git import PrLookupError, build_git_client

logger = logging.getLogger(__name__)


_TITLE_MAX = 30

# 代码工作区建不出来时对用户说的话（原始异常只进日志与 sessions.workspace_error）
WORKSPACE_ERROR_MESSAGE = "代码工作区创建异常，请重试"

# 会改动方案的三个工具：调完就该把最新方案推给前端刷新进度面板。
# 会改动方案的那几个：跑完就重读一份快照推给前端（幂等，同一状态只推一帧）。
# ``getPlan`` 不在里面 —— 它只读，读了不该动面板。
_PLAN_TOOLS = frozenset(
    {
        "storePlan",
        "updatePlan",
        "updatePlanSteps",
        "deletePlan",
        "updatePlanStep",
        "updatePlanStatus",
    }
)

#: 收尾时该给仓库记忆打底的形态 —— 与「谁能写记忆正文」是同一批（``orchestrate`` 上的
#: coding / rework、``review``、``analysis``）：这几轮才有要沉淀的事实（改动 / 审查结论 /
#: 仓库现状），其余形态只读注入（平台侧的刷新由记忆中间件在开工前做掉）。
_MEMORY_WRITE_FORMS: frozenset[str] = frozenset(
    {ORCHESTRATE_FORM, REVIEW_FORM, ANALYSIS_FORM}
)

# 一轮跑到一半炸了时的标记：已经有正文就说明上面是部分输出，一条都没有就是未产出。
INTERRUPTED_PARTIAL = "〔本轮中断，以上为部分输出〕"
INTERRUPTED_EMPTY = "〔本轮中断，未产出内容〕"
# 用户点了「终止」：不是故障，措辞里不出现异常名（前端本地也补同一句，刷新前后一致）
CANCELLED_BY_USER = "〔已由用户终止〕"


def plan_payload(plan: Any) -> dict[str, Any]:
    """把方案行序列化成前端要的快照（SSE 的 plan 事件与 GET plan 接口共用）。

    ``steps`` 容忍历史脏数据（整列不是 list、元素不是 dict）：跳掉渲染不了的条目，
    而不是让一个坏步骤把整个面板打挂。
    """
    raw_steps = plan.steps if isinstance(plan.steps, list) else []
    steps = [
        {
            "index": step.get("index", i),
            "goal": str(step.get("goal") or ""),
            "status": step.get("status", "pending"),
            "files": step.get("files"),
            "done_criteria": step.get("done_criteria"),
        }
        for i, step in enumerate(raw_steps, 1)
        if isinstance(step, dict)
    ]
    return {
        "plan_id": plan.id,
        "title": plan.title,
        "status": plan.status,
        "steps": steps,
        "content": plan.content,
        "updated_at": plan.updated_at.isoformat(),
    }


def _main_window(settings: Settings, intent: str) -> int:
    """本轮**主控模型**的上下文窗口（tokens）；取不到配置时返回 0。

    窗口必须按主控那个模型算：压缩只作用于主控那份历史，而子智能体各有自己的窗口，
    拿子智能体的窗口去除主控的输入会得出「永远安全」。解析只有一处口径
    （``llm.resolve_window``）：**那一行填了就用它 → 出厂默认（.env 那家的窗口）→ 128k**。

    兜底是**故意的宽**：这只是一条提示，配置缺项或角色没登记都不该让这一轮跑不起来 ——
    没有压力提示远好过整轮失败。
    """
    try:
        return resolve_window(settings, profile_for(intent).form)
    except Exception:
        logger.exception("读上下文窗口失败（本轮不做压力提醒）")
        return 0


def _ratio(peak: int, window: int) -> float:
    """峰值输入占窗口的比例，保留三位小数；窗口未知（0）时返回 0。"""
    return round(peak / window, 3) if window > 0 else 0.0


def _main_model(settings: Settings, intent: str) -> str:
    """本轮**主控模型**的名字（与 ``_main_window`` 同一个解析口径）；取不到回空串。

    界面把它标在这条回答的最下边（2026-10-14 用户口径：「提供回答的模型」）。
    解析失败只是不显示那一格，不该让这一轮跑不起来。
    """
    try:
        return resolve_model(settings, profile_for(intent).form)
    except Exception:
        logger.exception("读主控模型名失败（本轮不标模型）")
        return ""


#: 跑动中重发 usage 帧的最小间隔（秒）。工具调用密集的轮次里每帧几百字节，
#: 不节流会让同一条消息在一秒内重渲染几十次；0.5 秒对「看着它长」已经足够。
_USAGE_FRAME_MIN_GAP = 0.5

#: 改动的文件清单最多列这么多条（超出只报总数）：一轮改上百个文件更像事故而不是成果，
#: 界面上也不该为它铺一屏。
_CHANGES_MAX_FILES = 200


def _pr_refs(raw: Any) -> dict[str, str] | None:
    """从 ``create_pr`` 的**入参**里取 ``base`` / ``head`` —— 两个都缺就返回 ``None``。

    拿入参而不是去查 PR 列表：这一轮的 PR 是这一轮建的，入参就是它自己的声明（没有歧义，
    也不用多打一次平台接口）。v3 的工具入参有时是 dict、有时是一段 JSON 字符串
    （与 ``subagent_name`` 同一个坑），两种都认。
    """
    data: Any = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(data, Mapping):
        return None
    base = str(data.get("base") or "").strip()
    head = str(data.get("head") or "").strip()
    return {"base": base, "head": head} if base and head else None


def _redacted_todos(items: list[dict], terms: tuple[str, ...]) -> list[dict]:
    """待办树脱敏：主项和它的 children 都得过，漏一层等于没做。

    子项只保留 content / status 两个键（树只有两级，children 不再往下递归）。
    """
    safe: list[dict] = []
    for item in items:
        clean: dict[str, Any] = {"content": redact(item["content"], terms), "status": item["status"]}
        children = item.get("children")
        if children:
            clean["children"] = [
                {"content": redact(kid["content"], terms), "status": kid["status"]}
                for kid in children
            ]
        safe.append(clean)
    return safe


def _redacted_parts(parts: list[dict], terms: tuple[str, ...]) -> list[dict]:
    """落库前把每个块过一遍脱敏。

    翻译器手上那份是**未脱敏**的原文（脱敏统一在出口做，免得同一份文本被过滤两遍、
    口径还不一致）——SSE 出口已经过了一道，落库这道不能省。
    """
    safe: list[dict] = []
    for part in parts:
        if part["kind"] == "text":
            safe.append({"kind": "text", "content": redact(part["content"], terms)})
        else:
            safe.append({"kind": "todos", "items": _redacted_todos(part["items"], terms)})
    return safe


def _title_from(message: str) -> str:
    """会话标题取首条用户消息：压成单行（含换行/多空格）再截断。"""
    line = " ".join(message.split())
    return line[:_TITLE_MAX] + ("…" if len(line) > _TITLE_MAX else "")


# 入口拒收的回执：只说命中了哪一类，绝不复述命中的内容（回执本身也要下发与落库）
_REFUSAL_HINTS = {
    CRED_TERM: "请把凭据换成占位符再发。",
    "赋值字面量": "如果是在贴代码，请把字面量换成读环境变量的写法（如 os.getenv）。",
}
_REFUSAL_HINT = "沙箱里的凭据由平台注入，不需要贴进对话。"


def credential_refusal(label: str) -> str:
    """含凭据消息的拒收回执。"""
    return (
        f"这条消息里检测到疑似凭据（{label}），已拒绝执行，也没有发送给模型。"
        f"{_REFUSAL_HINTS.get(label, _REFUSAL_HINT)}"
    )


class HarnessService:
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

    # -- 意图判定 -----------------------------------------------------------
    def _classifier_for(self) -> BaseChatModel | None:
        """分类模型实例；未启用或构建失败时返回 None（退回纯关键字判定）。"""
        if not self._settings.intent_llm_enabled:
            return None
        if self._classifier is None:
            try:
                self._classifier = build_classifier_model(self._settings)
            except Exception:
                logger.exception("意图分类模型构建失败，退回关键字判定")
                return None
        return self._classifier

    def _router_model_for(self) -> BaseChatModel | None:
        """影子路由模型：优先分类行，没配就借一条已配置的 agent 行。"""
        if self._classifier is not None:
            return self._classifier
        if not self._settings.intent_llm_enabled:
            return None
        if self._router_model is None:
            for role in ("qa", "analysis", "coding", "plan", "review"):
                model = self._build_role_model(role)
                if model is not None:
                    self._router_model = model
                    break
        return self._router_model

    def _build_role_model(self, role: str) -> BaseChatModel | None:
        try:
            return build_model(self._settings, role=role)
        except Exception:  # noqa: BLE001 —— 某一行没配就试下一行
            return None

    async def _intent_history(self, session: Session) -> list[str]:
        """最近几轮用户消息（由远及近）。读库失败只降级成「不带上下文」，不打断本轮。

        本轮的这条消息**不在**里面 —— 调用点还没落库。历史只读这一次，两个判定
        （常规判定与 ``handoffTask`` 的重判）与交接给内层的消息都吃同一份。
        """
        if self._settings.intent_history_turns <= 0:
            return []
        try:
            async with self._db.session() as db:
                return await dao.recent_user_summaries(
                    db,
                    session_id=session.db_id,
                    limit=self._settings.intent_history_turns,
                )
        except Exception:
            logger.exception("读最近用户消息失败（分类将不带上下文）")
            return []

    async def _dialogue(self, session: Session) -> list[dict[str, str]]:
        """最近两轮对话：用户消息全文、助手消息首尾截断。

        只给用户消息不足以判断「继续 / 修正 / 解释上一轮」的指代；给两轮才看得到
        上一轮 AI 到底说了什么（例如“账户没钱了”这种需要解释的回答）。
        """
        if self._settings.intent_history_turns <= 0:
            return []
        try:
            async with self._db.session() as db:
                rows = await dao.recent_dialogue(
                    db, session_id=session.db_id, limit=4
                )
        except Exception:
            logger.exception("读最近两轮对话失败（分类将只带本轮消息）")
            return []
        return [
            {
                "role": role,
                "content": content
                if role == "user"
                else head_tail_excerpt(content),
            }
            for role, content in rows
        ]

    async def _last_reply(self, session: Session) -> str | None:
        """助手上一轮回复的**末尾节选**（判定「用户在回答哪个问题」要靠它）。

        实测（2026-10-12）：助手上一轮结尾问「要不要我把方案正文修复一遍再给你确认？」，
        用户只回「修复吧」—— 指代对象不在这一句里，判定只看字面就把它判成了 coding，
        于是走了编码形态去「修方案文本」（那个形态没有写方案的工具，白折腾 25 分钟）。

        与 ``_intent_history`` 同一个窗口开关：判定上下文关了（``intent_history_turns=0``）
        就**不碰库**（库连不上时那一跳会白等一个超时）。读失败同样只降级成「不带」。
        """
        if self._settings.intent_history_turns <= 0:
            return None
        try:
            async with self._db.session() as db:
                raw = await dao.latest_assistant_tail(
                    db,
                    session_id=session.db_id,
                    chars=PREVIOUS_REPLY_CHARS + PREVIOUS_REPLY_SLACK,
                )
        except Exception:
            logger.exception("读上一轮助手回复失败（分类将只带用户消息）")
            return None
        return tail_excerpt(raw) if raw else None

    async def _active_task(self, session: Session) -> str | None:
        """最近一次可执行意图；进程重启后从最近 10 条 assistant 记录回溯。"""
        key = str(session.public_id)
        if key in self._active_tasks:
            return self._active_tasks[key]
        try:
            async with self._db.session() as db:
                intents = await dao.recent_assistant_intents(
                    db, session_id=session.db_id, limit=10
                )
        except Exception:
            logger.exception("读未完成任务失败（按无 active task 处理）")
            return None
        for intent in intents:
            if intent in EXECUTION_INTENTS:
                self._active_tasks[key] = intent
                return intent
        return None

    def _remember_active_task(self, session: Session, intent: str, message: str) -> None:
        """更新未完成任务：可执行意图覆盖；PR 合并/关闭后的收尾或关闭方案则清空。"""
        key = str(session.public_id)
        if intent == "plan" and any(
            marker in message for marker in ("关闭", "删除", "作废", "close")
        ):
            self._active_tasks.pop(key, None)
            return
        if intent in EXECUTION_INTENTS:
            self._active_tasks[key] = intent
            return
        if intent == "analysis" and any(
            marker in message
            for marker in ("已合并", "合并了", "已关闭", "关闭了", "merged", "closed")
        ):
            self._active_tasks.pop(key, None)

    async def _last_intent(self, session: Session) -> str | None:
        """上一轮的判定结果（``extra.intent``）。

        对「你为什么不派 coder」「上一轮不是 rework 吗」这类追问，它比字面里的
        「为什么」可靠：同一个窗口开关（``intent_history_turns=0`` 不碰库），读失败只降级。
        """
        if self._settings.intent_history_turns <= 0:
            return None
        try:
            async with self._db.session() as db:
                return await dao.latest_assistant_intent(
                    db, session_id=session.db_id
                )
        except Exception:
            logger.exception("读上一轮意图失败（分类将不带上一轮判定）")
            return None

    async def _shadow_route(
        self,
        message: str,
        history: Sequence[str],
        previous_reply: str | None,
        previous_intent: str | None,
        active_intent: str | None,
        dialogue: Sequence[Mapping[str, str]],
        normal: IntentDecision,
        public_id: str,
    ) -> None:
        """影子路由：只记 ``INTENT_ROUTER``，**不碰 normal 判定、不切形态**。

        跑在独立 task 里（见 ``chat()``），所以多出来的一次分类模型调用不占本轮的
        首字节时间；失败只记一行 INFO，不影响任何返回。
        """
        try:
            model = self._router_model_for()
            if model is None:
                return
            decision = await route(
                model,
                message,
                history=history,
                previous_reply=previous_reply,
                previous_intent=previous_intent,
                active_intent=active_intent,
                dialogue=dialogue,
                timeout=self._settings.intent_router_timeout_seconds,
            )
            if decision is None:
                return
            logger.info(
                "%s normal=%s source=%s confidence=%s axes=%s suggested=%s public_id=%s",
                ROUTER_LOG_TAG,
                normal.intent,
                normal.source,
                normal.confidence,
                decision.as_dict(),
                suggest_intent(
                    decision,
                    previous_intent,
                    message,
                    active_intent=active_intent,
                ),
                public_id,
            )
        except Exception:
            # 影子层是观测，不是功能；任何意外都不能变成一轮的故障点
            logger.exception("%s 影子路由异常（已忽略）", ROUTER_LOG_TAG)

    async def _detect_intent(
        self,
        message: str,
        history: Sequence[str],
        previous_reply: str | None = None,
        previous_intent: str | None = None,
        active_intent: str | None = None,
        dialogue: Sequence[Mapping[str, str]] = (),
    ) -> IntentDecision:
        """判定本轮意图：口令 → JEV → 大模型（都带最近几轮用户消息）→ 关键字口径。

        用量记进本轮的累加器（``self._intent_usage``）—— 口令层不调模型，命中时那份额
        自然是空的，正是我们想要的「这一轮没为判定花钱」。
        """
        return await detect_intent(
            message,
            history=history,
            jev=self._intent_jev,
            model=self._classifier_for(),
            timeout=self._settings.intent_timeout_seconds,
            usage=self._intent_usage,
            previous_reply=previous_reply,
            previous_intent=previous_intent,
            active_intent=active_intent,
            dialogue=dialogue,
            # 意图识别走哪一关：jev（三关，默认）/ llm（三关，不碰 JEV）—— 见 intent.detect_intent
            mode=self._settings.intent_mode,
        )

    async def _rejudge_intent(
        self,
        message: str,
        history: Sequence[str],
        *,
        session: Session | None = None,
        previous_reply: str | None = None,
        previous_intent: str | None = None,
        active_intent: str | None = None,
        dialogue: Sequence[Mapping[str, str]] | None = None,
    ) -> IntentDecision:
        """跳过口令层重判一次意图（``handoffTask`` 用，签名与它约定好）。

        与 ``_detect_intent`` 只差 ``force_llm=True``：智能体自述做不了时，要纠正的
        往往正是口令层（一句「实施」就把意图钉死），所以这一层不看关键字。模型不可用
        仍是回落，来源会标 ``fallback`` —— 调用方（工具）据此拒绝交接。

        用量并进**同一个**累加器：这是同一轮里为判定花的第二笔钱，界面按「意图分类」
        一类汇总，不需要在账上再拆一层。

        ``session`` / ``previous_reply`` 是给 ``handoffTask`` 那条路用的：回调契约是
        ``(message, history)`` 两个参数（工具那边不认别的），所以在 ``chat()`` 里用
        ``partial`` 先把这两样绑好 —— 重判同样要知道「用户在回答哪个问题」。
        ``session`` 缺省（单测直接调）就只带用户消息，与老行为一致。
        """
        if previous_reply is None and session is not None:
            previous_reply = await self._last_reply(session)
        if previous_intent is None and session is not None:
            previous_intent = await self._last_intent(session)
        if active_intent is None and session is not None:
            active_intent = await self._active_task(session)
        if dialogue is None and session is not None:
            dialogue = await self._dialogue(session)
        return await detect_intent(
            message,
            history=history,
            jev=self._intent_jev,
            model=self._classifier_for(),
            timeout=self._settings.intent_timeout_seconds,
            force_llm=True,
            usage=self._intent_usage,
            previous_reply=previous_reply,
            previous_intent=previous_intent,
            active_intent=active_intent,
            dialogue=dialogue or (),
        )

    async def pending_clarification(self, session: Session) -> dict[str, Any] | None:
        """取当前会话的待澄清状态（刷新页面时前端据此恢复弹窗）。"""
        try:
            async with self._db.session() as db:
                row = await dao.get_clarification(db, session_id=session.db_id)
        except Exception:
            logger.exception("读待澄清状态失败（按没有处理）")
            return None
        if row is None:
            return None
        return {
            "id": row.id,
            "question": row.question,
            "options": row.options or [],
            "allow_other": True,
        }

    async def _clear_clarification(self, session: Session) -> None:
        """任何普通用户消息到达时调用：旧澄清立刻失效。"""
        try:
            async with self._db.session() as db:
                await dao.delete_clarification(db, session_id=session.db_id)
        except Exception:
            logger.exception("清理待澄清状态失败（不影响本轮）")

    async def _consume_clarification(
        self,
        session: Session,
        *,
        clarification_id: str,
        choice: int | str,
    ) -> tuple[str, str, str] | None:
        """校验并消费一条结构化选择；成功返回 (原话, 意图, 选项 label)。"""
        try:
            async with self._db.session() as db:
                row = await dao.get_clarification(db, session_id=session.db_id)
                if row is None or row.id != clarification_id:
                    if row is not None:
                        await dao.delete_clarification(db, session_id=session.db_id)
                    return None
                options = row.options if isinstance(row.options, list) else []
                selected: dict[str, Any] | None = None
                try:
                    index = int(choice)
                except (TypeError, ValueError):
                    index = -1
                if 0 <= index < len(options) and isinstance(options[index], dict):
                    selected = options[index]
                else:
                    # 也允许直接回传 intent 名（调试 / 手工调用）。
                    wanted = str(choice).strip().lower()
                    for item in options:
                        if isinstance(item, dict) and str(item.get("intent")) == wanted:
                            selected = item
                            break
                if selected is None:
                    await dao.delete_clarification(db, session_id=session.db_id)
                    return None
                intent = str(selected.get("intent") or "").strip()
                label = str(selected.get("label") or intent).strip()
                if intent not in CLARIFY_INTENTS:
                    await dao.delete_clarification(db, session_id=session.db_id)
                    return None
                original = row.original_message
                await dao.delete_clarification(db, session_id=session.db_id)
                return original, intent, label
        except Exception:
            logger.exception("消费待澄清状态失败")
            return None

    async def _save_clarification(
        self,
        session: Session,
        *,
        original_message: str,
        question: str,
        options: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """qa 输出解析成功后落库；返回 SSE 帧载荷。"""
        try:
            async with self._db.session() as db:
                row = await dao.save_clarification(
                    db,
                    session_id=session.db_id,
                    original_message=original_message,
                    question=question,
                    options=options,
                )
                return {
                    "id": row.id,
                    "question": row.question,
                    "options": row.options or [],
                    "allow_other": True,
                }
        except Exception:
            logger.exception("保存待澄清状态失败（本轮退回普通文本）")
            return None

    # -- 对外：处理一条用户消息，产出事件流 ---------------------------------
    async def chat(
        self,
        session: Session,
        message: str,
        *,
        clarification_id: str | None = None,
        choice: int | str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """处理用户消息。

        事件形如 ``{"type": "ai"|"tool"|"plan"|"done"|"error", ...}``，
        由 API 层序列化为 SSE。

        出口（进状态前、落日志前、下发前、直接执行返回前）各拦一道，词表在这里算一次，
        按会话取 —— 令牌是每会话一份，不能进模块级常量。此外入口先做一次**拒收**：
        消息里含凭据就整条不收，凭据连模型都不发（见 ``detect_credential``）。

        会话这个日志维度**不在这里设** —— 设在 ``api/session_lookup.resolve_session``：
        每个会话级请求都从那儿过，连 ``/workspace`` 触发的 provisioning 也算这个会话的日志。
        """
        terms = secret_terms(
            session.repo.token,
            self._settings.gitee_access_token,
            # 模型凭据也进词表（2026-10-13）：库里/界面配的 key 同样不该出现在日志与记录里
            *self._settings.model_secret_values(),
        )
        # 结构化选择：先消费 pending；普通消息则立刻让旧澄清失效（用户口径 2026-10-19）。
        chosen: tuple[str, str, str] | None = None
        if clarification_id and choice is not None and str(choice) != "other":
            chosen = await self._consume_clarification(
                session, clarification_id=clarification_id, choice=choice
            )
            if chosen is None:
                if not message.strip():
                    yield {"type": "error", "error": "这次澄清已失效，请重新描述。"}
                    yield {"type": "done"}
                    return
                await self._clear_clarification(session)
        else:
            await self._clear_clarification(session)
        label = detect_credential(message, terms)
        if label is not None:
            # 拒收在建模之前：意图分类那一跳也会把消息发给模型，晚一步就白拦了
            await self._reject_credential(session, message, label, terms)
            yield {"type": "ai", "content": credential_refusal(label)}
            yield {"type": "done"}
            return
        history = await self._intent_history(session)
        # 助手上一轮说了什么：用户回一句「修复吧」时，指代对象只在那里
        previous_reply = await self._last_reply(session)
        # 上一轮是什么意图：追问「为什么不派 coder」时，比字面里的「为什么」可靠
        previous_intent = await self._last_intent(session)
        # 最近两轮对话：用户全文 + 助手首尾截断，判断“继续/修正/解释上一轮”要看它
        dialogue = await self._dialogue(session)
        # 最近一次可执行任务：analysis/qa 不覆盖它，短追问优先沿用它
        active_intent = await self._active_task(session)
        # 每轮一份：累加器挂在服务实例上（重判要跨调用累加），所以必须逐轮清零 ——
        # 不清就会把上一轮的判定开销带到这一轮的账上。
        self._intent_usage = {}
        self._last_usage_frame = 0.0
        if chosen is not None:
            message, chosen_intent, chosen_label = chosen
            decision = IntentDecision(
                chosen_intent,
                SOURCE_CLARIFY_CHOICE,
                "用户选择了澄清候选",
            )
        else:
            decision = await self._detect_intent(
                message,
                history,
                previous_reply,
                previous_intent,
                active_intent,
                dialogue,
            )
        override = (
            active_task_override(message, decision.intent, active_intent)
            if chosen is None
            else None
        )
        if override:
            decision = IntentDecision(
                override, SOURCE_ACTIVE, f"沿用未完成任务 {active_intent}"
            )
        # 低置信度：先转 qa 向用户确认，避免拿一个猜出来的形态直接干活。
        # active_task_override 命中时不动 —— 用户明确在继续未完成任务，那是更强的信号。
        clarification: dict[str, Any] | None = None
        if not override and (
            clarified := clarification_decision(
                decision, self._settings.intent_clarify_confidence
            )
        ) is not None:
            decision = clarified
            clarification = {
                "confidence": clarified.confidence,
                "candidates": list(clarified.candidates),
            }
        intent = decision.intent
        self._remember_active_task(session, intent, message)
        # 影子路由（默认关）：只记轴，不采用建议、不改变 intent。独立 task 不占本轮首字节。
        if self._settings.intent_router_shadow:
            shadow = asyncio.create_task(
                self._shadow_route(
                    message,
                    history,
                    previous_reply,
                    previous_intent,
                    active_intent,
                    dialogue,
                    decision,
                    str(session.public_id),
                )
            )
            self._shadow_tasks.add(shadow)
            shadow.add_done_callback(self._shadow_tasks.discard)
        logger.info(
            "意图分析：intent=%s source=%s 理由=%s public_id=%s",
            intent, decision.source, decision.reason or "-", session.public_id,
        )
        if chosen is not None:
            record_content = f"选择：{chosen_label}"
            record_extra = {
                "intent": intent,
                "intent_source": SOURCE_CLARIFY_CHOICE,
            }
        else:
            record_content = message
            record_extra = {
                "intent": intent,
                "intent_source": decision.source,
            }
        await self._record(
            session,
            type="user",
            summary=record_content[:200],
            content=record_content,
            extra=record_extra,
        )
        await self._name_session(session, message)

        # 每轮一份运行时状态：循环守卫拿它数重复，``handoffTask`` 拿它重判意图并把原话
        # 交给另一个形态（分类模型与超时都在这一层，所以回调也从这里给）。
        # 智能体是长期存活对象，逐轮变化的东西只能经 config 注入。
        state = TurnState(
            message=message,
            history=history,
            repeats=self._settings.loop_guard_repeats,
            # 回调契约是 (message, history)：把 session 与「助手上一轮末尾」先绑进去，
            # 让重判也能看到「用户在回答哪个问题」（不绑的话它只看得到用户那几句）。
            rejudge=partial(
                self._rejudge_intent,
                session=session,
                previous_reply=previous_reply,
                previous_intent=previous_intent,
                dialogue=dialogue,
            ),
        )

        try:
            # 工作区到这一跳才准备：拒收的消息上面就 return 了，不会白建一个容器
            runtime = await self._manager.ensure_runtime(session)
        except WorkspaceError as exc:
            logger.error(
                "代码工作区不可用：public_id=%s 原因=%s",
                session.public_id, redact(str(exc), terms),
            )
            yield {"type": "error", "error": WORKSPACE_ERROR_MESSAGE}
            yield {"type": "done"}
            return

        try:
            if intent in DIRECT_INTENTS:
                # 可直接执行的任务：不走智能体，直接返回（这条路径中间件够不着，得自己拦）
                result = redact(
                    await self._execute_direct(session, runtime, intent, message), terms
                )
                await self._record(
                    session, type="assistant", summary=result[:200], content=result,
                    extra={"intent": intent, "direct": True},
                )
                yield {"type": "ai", "content": result}
            else:
                async for event in self._run_agent(
                    session,
                    runtime,
                    message,
                    intent,
                    terms,
                    state=state,
                    clarification=clarification,
                ):
                    yield event
                # 轮末打底：coding / rework / review / analysis 这几轮才有要沉淀的事实，
                # 按 Gitee 实况再刷一遍。
                # 放在这一层（而不是 ``_run_agent`` 里）有三个好处：中止与正常收尾两条
                # 出口都覆盖得到、``_run_agent`` 只管事件流不新增依赖、只读轮不查 PR。
                if profile_for(intent).form in _MEMORY_WRITE_FORMS:
                    await self._refresh_repo_memory(session, runtime)
        except Exception as exc:
            # exception() 自带堆栈，别再把 exc 塞进 msg（TRY401）
            logger.exception("处理失败")
            # 异常文本可能夹带原始输出（工具报错里常把返回值整段带上）
            yield {"type": "error", "error": redact(str(exc), terms)}
        yield {"type": "done"}

    async def _reject_credential(
        self, session: Session, message: str, label: str, terms: tuple[str, ...]
    ) -> None:
        """拒收含凭据的消息，并把这次拒收留痕。

        原文不进 Postgres、不进日志、不进模型：落库的是脱敏版，``extra`` 里只留类别名。
        也**不调** ``_name_session`` —— 首条消息会变成会话标题写进库，那等于给凭据留了后门。
        """
        safe = redact(message, terms)
        await self._record(
            session,
            type="user",
            summary=safe[:200],
            content=safe,
            extra={"intent": "rejected", "credential": label},
        )
        notice = credential_refusal(label)
        await self._record(
            session, type="assistant", summary=notice, content=notice,
            extra={"rejected": label},
        )

    # -- 直接执行（sync）--------------------------------------------------
    async def _execute_direct(
        self, session: Session, runtime: SessionRuntime, intent: str, message: str
    ) -> str:
        if intent == "sync":
            return await self._direct_sync(session, runtime)
        return "（无对应的直接执行处理器）"

    async def _direct_sync(self, session: Session, runtime: SessionRuntime) -> str:
        """同步仓库：不存在 clone，已存在 pull。"""
        repo_dir = f"{self._settings.sandbox_project_dir}/{session.repo.repo}"
        exists = await runtime.sandbox.run(f"test -d {repo_dir}/.git && echo yes || echo no")
        if "yes" in exists.stdout:
            result = await runtime.sandbox.run(f"git -C {repo_dir} pull")
            action = "git pull"
        else:
            result = await runtime.sandbox.run(
                f"git clone {session.repo_url} {repo_dir}"
            )
            action = "git clone"
        out = (result.stdout or result.stderr or "").strip()
        return f"已同步仓库（{action}，exit={result.exit_code}）：\n{out[-1000:]}"

    # -- 主智能体（intent 作为 config 传入）-------------------------------
    async def _run_agent(
        self,
        session: Session,
        runtime: SessionRuntime,
        message: str,
        intent: str,
        terms: tuple[str, ...],
        *,
        state: TurnState,
        clarification: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        # 装配形态按本轮意图定：qa / analysis 只读，plan / review 单智能体（主控自己出
        # 方案 / 自己审），coding 与 rework 挂三个子智能体（两者共用一套装配，表见
        # agent/profiles.py）。同一会话换形态不影响历史 —— thread 是共享的。
        agent = self._manager.get_agent(runtime, session, profile_for(intent))
        config = RunnableConfig(
            configurable={
                "thread_id": self._manager.thread_id(session),
                "intent": intent,
                # 低置信度澄清：qa 的中间件读它，决定要不要先追问
                "clarification": clarification,
                # 智能体是长期存活对象，词表只能逐轮经 config 注入（脱敏中间件读它）
                "redact_terms": terms,
                # 同上是逐轮的东西：循环守卫数重复、handoffTask 重判与交接都用它
                "turn_state": state,
            },
            recursion_limit=self._settings.agent_recursion_limit,
        )
        logger.info(
            "派发智能体：public_id=%s intent=%s 消息 %d 字",
            session.public_id, intent, len(message),
            extra={"agent": MAIN_AGENT},
        )
        text = ""
        llm_calls = 0
        tool_calls = 0
        last_plan: dict[str, Any] | None = None
        run_log = AgentRunLog(
            arg_limit=self._settings.log_tool_arg_max,
            result_limit=self._settings.log_tool_result_max,
            delegation_result_limit=self._settings.log_delegation_result_max,
            terms=terms,
        )
        translator = V3Translator()
        names = ToolNames()
        # task 的 tool_call_id → 派的是哪个子智能体。只为一件事：工具帧上带角色，前端
        # 才说得出「正在编码」而不是笼统的「正在处理子任务」。收尾那帧也得带 —— 不带的话
        # 子智能体一返回，提示就翻回泛化文案，而主智能体还要想几秒才开口。
        delegated: dict[str, str] = {}
        # 本轮若建了 PR，这里留下它的 base / head —— 收尾时拿它去本地缓存算「这一轮改了什么」。
        # 入参在 tool-started 那一帧上（与「派给谁」同一个来源），之后拿不到。
        pr_refs: dict[str, str] | None = None

        # v3 的 astream_events 返回的是协程，得先 await 出流（v2 直接就是流）
        try:
            stream = await agent.astream_events(
                {"messages": [{"role": "user", "content": message}]},
                config=config,
                version="v3",
            )
            async for event in stream:
                method = method_of(event)
                if method == "messages":
                    run_log.on_message(event)
                    if event_name(event) == "message-finish":
                        llm_calls += 1
                        # 账变了就推一帧（节流在 _live_usage 里）—— 数字跟着跑起来长，
                        # 不用等这一轮收尾。纯文本问答没有工具调用，这里是它唯一的进度点。
                        live = self._live_usage(session, intent, run_log)
                        if live is not None:
                            yield {"type": "usage", "usage": live}
                    # 正文 / 思考 / 待办树都走这里；子智能体的正文在 translate 里被丢掉，
                    # 只有它的思考与 write_todos 会下发（思考不落库）
                    for part in translator.feed(event):
                        if part["type"] == "ai":
                            # 先脱敏再累加：这一处同时管住 SSE、业务库记录与首段摘要
                            answer = redact(part["content"], terms)
                            if answer:
                                text += answer
                                if clarification is None:
                                    yield {"type": "ai", "content": answer}
                        elif part["type"] == "reasoning":
                            # 思考只走 SSE：**不进 text / parts / 记录**。重连时能靠
                            # RunHub 的临时帧缓冲重放，库里不会留。
                            thought = redact(part["content"], terms)
                            if thought:
                                yield {
                                    "type": "reasoning",
                                    "content": thought,
                                    "agent": part.get("agent") or "main",
                                }
                        else:
                            yield {
                                "type": "todos",
                                "items": _redacted_todos(part["items"], terms),
                            }
                elif method == "tools":
                    name = event_name(event)
                    if name == "tool-started":
                        tool_calls += 1
                        # 委派要赶在子智能体的 write_todos 之前记下（树靠它认父项）
                        translator.note_delegation(event)
                        called = names.remember(event)
                        frame = {"type": "tool", "name": called, "content": ""}
                        if called == CREATE_PR_TOOL:
                            pr_refs = _pr_refs((payload_of(event) or {}).get("input")) or pr_refs
                        if called == TASK_TOOL:
                            # 「派给谁」只有这帧拿得到（入参在这儿），收尾帧回头查
                            role = subagent_name((payload_of(event) or {}).get("input"))
                            if role:
                                delegated[call_id_of(event)] = role
                                frame["agent"] = role
                        run_log.on_tool_start(event, called)
                        yield frame
                    elif name == "tool-finished":
                        called = names.resolve(event)
                        run_log.on_tool_end(event, called)
                        frame = {
                            "type": "tool",
                            "name": called,
                            # 中间件已把进状态的返回值脱敏，这里再兜一道：工具事件的
                            # output 与进 state 的那份是否同一个对象没验证过
                            "content": redact(
                                str((payload_of(event) or {}).get("output", "")), terms
                            )[:500],
                        }
                        role = delegated.pop(call_id_of(event), "")
                        if role:
                            frame["agent"] = role
                        yield frame
                        live = self._live_usage(session, intent, run_log)
                        if live is not None:
                            yield {"type": "usage", "usage": live}
                        if called in _PLAN_TOOLS:
                            # 方案刚被改过，重读一份最新快照推给前端（全量、幂等；同一状态推一次）。
                            # 前端把它渲染成聊天里那条「方案」链接（就地展开全文），不再有独立面板；
                            # ``latest`` 为 ``None`` 也照推（``plan: null``），前端据此知道方案没了。
                            latest = await self._plan_snapshot(session)
                            if latest != last_plan:
                                last_plan = latest
                                if latest is not None:
                                    # 首次生成时落一条记录，之后同 plan_id 不再追加（见该函数）
                                    await self._record_plan_once(session, latest)
                                yield {"type": "plan", "plan": latest}
        except asyncio.CancelledError:
            # 用户点了「终止」：RunHub 把这一轮的 task 取消了。补一条记录（不补的话库里
            # 就只剩一条用户消息空着，刷新后看不出这轮出过事），再原样抛出 —— 上层靠
            # 它区分「用户终止」与「故障」。这条 await 是安全的：task.cancel() 只投递
            # 一次；除非期间又被取消一次，那时这条标记会丢（丢了也只是个标记）。
            usage = self._turn_usage(session, intent, run_log, interrupted=True)
            self._log_turn_cost(
                session, intent, usage,
                llm_calls=llm_calls, tool_calls=tool_calls, chars=len(text),
            )
            await self._record_interrupted(
                session, text, translator, intent, terms, usage=usage,
            )
            raise
        except TurnStop as stop:
            # 空转刹车（``middleware/guard.py``）：守卫抛出的信号，本轮到此为止。
            # 这里只把它落到 state 上，收尾在流外统一做 —— 那条路也兜住「异常被某层
            # 吃成了 ToolMessage、根本没抛到这里」的情形。
            state.aborted = stop
        except Exception as exc:
            # 下面那次 _record 在循环之外，异常一跳过它就永远不执行 —— 先补一条中断记录，
            # 再把异常原样抛给 chat()（error 帧的产生只留它那一处）。
            usage = self._turn_usage(session, intent, run_log, interrupted=True)
            self._log_turn_cost(
                session, intent, usage,
                llm_calls=llm_calls, tool_calls=tool_calls, chars=len(text),
            )
            await self._record_interrupted(
                session, text, translator, intent, terms, exc, usage=usage,
            )
            raise

        if state.aborted is not None:
            # 提前收尾：这一轮没跑完，不能落到下面那条「智能体完成」与正常记录里
            stop = state.aborted
            usage = self._turn_usage(session, intent, run_log, interrupted=True)
            self._log_turn_cost(
                session, intent, usage,
                llm_calls=llm_calls, tool_calls=tool_calls, chars=len(text),
            )
            await self._record_interrupted(
                session, text, translator, intent, terms,
                closing=stop.marker, extra=stop.extra, usage=usage,
            )
            # 标记的写法与记录里的一致（``_record_interrupted`` 用空行分隔），
            # 这样实时看到的东西和刷新后回放的一样
            yield {
                "type": "ai",
                "content": f"{text}\n\n{stop.marker}" if text else stop.marker,
            }
            return

        clarification_frame: dict[str, Any] | None = None
        if clarification is not None:
            parsed = parse_clarification(text)
            if parsed is not None:
                clarification_frame = await self._save_clarification(
                    session,
                    original_message=message,
                    question=parsed.question,
                    options=[option.as_dict() for option in parsed.options],
                )
                if clarification_frame is not None:
                    text = parsed.question
            # 模型没按协议输出 / 落库失败：把攒着的文本补发，用户至少能看到回答。
            if clarification_frame is None and text:
                yield {"type": "ai", "content": text}

        logger.info(
            "智能体完成：public_id=%s intent=%s LLM %d 次 / 工具 %d 次 / 输出 %d 字",
            session.public_id, intent, llm_calls, tool_calls, len(text),
            extra={"agent": MAIN_AGENT},
        )
        usage = self._turn_usage(session, intent, run_log)
        self._log_turn_cost(
            session, intent, usage,
            llm_calls=llm_calls, tool_calls=tool_calls, chars=len(text),
        )
        extra: dict[str, Any] = {
            "intent": intent,
            "usage": usage,
        }
        if clarification_frame is not None:
            extra["clarification"] = clarification_frame
        # parts 只在真的出现过待办时才写：纯正文的回合，content 已经够前端回放了
        if translator.parts.has_todos:
            extra["parts"] = _redacted_parts(translator.parts.data, terms)
        # 「这一轮改了什么」：只有本轮**建过 PR** 才算数（用户口径：这就是「写完 + 提了 PR」的场景）。
        # 算不出来（本地没缓存过这个仓库 / 缺分支）就整块不出现 —— 宁可不显示，也不显示半份。
        changes = await self._changes_for_round(session, pr_refs) if pr_refs else None
        if changes is not None:
            extra["changes"] = changes
        await self._record(
            session, type="assistant", summary=text[:200], content=text, extra=extra,
        )
        if clarification_frame is not None:
            yield {"type": "clarification", "clarification": clarification_frame}
        if changes is not None:
            yield {"type": "changes", "changes": changes}
        # 最后把这一轮的账推给前端：**落在正文之后**，这样它贴在回复末尾（前端按顺序处理帧），
        # 也不必让用户刷新页面才看到本轮的消耗与上下文压力。落库那份与这一帧同源。
        yield {"type": "usage", "usage": extra["usage"]}

    # -- token 账 ----------------------------------------------------------
    def _turn_context(
        self,
        session: Session,
        intent: str,
        run_log: AgentRunLog,
        *,
        commit: bool = True,
    ) -> dict[str, Any]:
        """上下文压力那一格：峰值输入 / 窗口 / 占比 / 压缩次数。

        峰值与压缩次数都**跨轮累计**（存在 ``_context_state`` 里，按 ``public_id`` 键）：
        这一格回答的是「这个会话的历史涨到多大了」，逐轮算只能看到本轮那点量。

        ``commit=False`` ＝ **看一眼、不落账**（跑动中那些中间帧用它）：累计是 `+=`，
        同一轮里多算几次就会把压缩次数重复累加，所以中间帧一律只读。

        ``ratio`` 由服务端算好给前端（同一份口径只算一次）。``window`` 是**本轮主控模型**
        的窗口 —— 换形态就换模型（orchestrate/plan 是 GLM 128k、review 是 Kimi 256k），
        所以同一份历史在不同形态下的压力本来就不同，这是事实而不是误差。

        峰值取**跨轮累计**（只增不减），但**这一轮压过**的除外：那会儿的峰值是压缩前
        那一次，用它会让压力条永久停在「接近压缩线」——历史其实已经被压到 10%。压缩之后
        的主控输入才是现在的真实占用，所以改用本轮**末值**（``last_input``）。
        """
        key = str(session.public_id)
        # 只读那几次（``commit=False``）连一格都不建 —— 中间帧不该留下任何痕迹
        stored = self._context_state.get(key)
        peak, compressions = stored if stored is not None else (0, 0)
        if run_log.compressions and run_log.last_input > 0:
            peak = run_log.last_input
        else:
            peak = max(peak, run_log.peak_input)
        if run_log.compressions:
            compressions += run_log.compressions
        if commit:
            self._context_state[key] = [peak, compressions]
        window = _main_window(self._settings, intent)
        return {
            "max_input": peak,
            "window": window,
            "ratio": _ratio(peak, window),
            "compressed": compressions,
            "compressed_now": run_log.compressions > 0,
            # 告警线 / 危险线**随这一格发下去**：环图的变色口径只有一处出处（设置页那两格），
            # 界面不写死 0.6 / 0.8（2026-10-14）。旧记录没有这两格时前端用同样的默认值。
            "warn_ratio": float(getattr(self._settings, "context_warn_ratio", 0.6) or 0),
            "alert_ratio": float(getattr(self._settings, "context_alert_ratio", 0.8) or 0),
        }

    def _turn_usage(
        self,
        session: Session,
        intent: str,
        run_log: AgentRunLog,
        *,
        interrupted: bool = False,
        commit_context: bool = True,
    ) -> dict[str, Any]:
        """本轮 token 账：智能体链路（按主角）+ 意图判定那笔 + 上下文压力，合成一份。

        判定那一笔在这里并进来而不是由 ``AgentRunLog`` 记 —— 它跑在事件流之外、用的还是
        另一个模型（分类模型 / JEV），混进「主控」名下会让账看不懂。归类名 ``INTENT_ACTOR``。

        失败与中止的轮次照样记账（``interrupted=True``）：钱已经花了，不因为没跑完就不算。
        """
        snapshot = run_log.usage_snapshot(
            interrupted=interrupted,
            context=self._turn_context(session, intent, run_log, commit=commit_context),
        )
        snapshot["model"] = _main_model(self._settings, intent)
        if self._intent_usage.get("calls"):
            call = {
                "input": int(self._intent_usage.get("input", 0)),
                "output": int(self._intent_usage.get("output", 0)),
                "calls": int(self._intent_usage["calls"]),
            }
            snapshot["by_actor"][INTENT_ACTOR] = call
            # 总数也要跟着涨：它问的是「这一轮一共花了多少」，把判定那一笔排除在外，
            # 界面上「合计」就会小于「按角色」那些行加起来的和（自相矛盾）。
            totals = snapshot["totals"]
            totals["input"] = int(totals.get("input", 0)) + call["input"]
            totals["output"] = int(totals.get("output", 0)) + call["output"]
            totals["calls"] = int(totals.get("calls", 0)) + call["calls"]
        return snapshot

    def _live_usage(
        self, session: Session, intent: str, run_log: AgentRunLog
    ) -> dict[str, Any] | None:
        """跑动中把这一轮的账推给前端；距上一次不足 ``_USAGE_FRAME_MIN_GAP`` 秒就跳过。

        与收尾那一帧同源（同一个 ``_turn_usage``），唯一区别是**不落上下文累计**
        （中间帧可能发很多次，`+=` 会被重复累加）。
        """
        stamp = time.monotonic()
        if stamp - self._last_usage_frame < _USAGE_FRAME_MIN_GAP:
            return None
        self._last_usage_frame = stamp
        return self._turn_usage(session, intent, run_log, commit_context=False)

    def _log_turn_cost(
        self,
        session: Session,
        intent: str,
        usage: Mapping[str, Any],
        *,
        llm_calls: int,
        tool_calls: int,
        chars: int,
    ) -> None:
        """一轮结束打一条**一眼可见**的成本账；超过阈值再单独记一条 WARNING。

        逐角色的明细已经在 ``usage`` 里（库与界面都读它），这里只做汇总：2026-10-20
        那轮 1593 万输入，原先只能事后手工把日志里每一条 ``LLM 调用完成`` 加起来才看得出。
        """
        totals = usage.get("totals") or {}
        context = usage.get("context") or {}
        total_in = int(totals.get("input", 0))
        logger.info(
            "本轮成本：public_id=%s intent=%s 输入 %d / 输出 %d / 调用 %d / 工具 %d "
            "/ 主控峰值上下文 %d / 输出 %d 字",
            session.public_id,
            intent,
            total_in,
            int(totals.get("output", 0)),
            llm_calls,
            tool_calls,
            int(context.get("max_input", 0)),
            chars,
            extra={"agent": MAIN_AGENT},
        )
        if total_in < self._settings.turn_cost_warn_tokens:
            return
        by_actor = usage.get("by_actor") or {}
        top_name, top = max(
            (
                (name, row)
                for name, row in by_actor.items()
                if isinstance(row, Mapping)
            ),
            key=lambda item: int(item[1].get("input", 0)),
            default=("", {}),
        )
        logger.warning(
            "本轮 token 偏高：输入 %d ≥ 阈值 %d（%s 占 %d）；看 by_actor 明细找增长来源",
            total_in,
            self._settings.turn_cost_warn_tokens,
            top_name or "未知",
            int(top.get("input", 0)),
            extra={"agent": MAIN_AGENT},
        )

    async def _changes_for_round(
        self, session: Session, pr_refs: Mapping[str, str]
    ) -> dict[str, Any] | None:
        """这一轮的 PR 改了哪些文件 —— **在本地那份代码缓存里算**（2026-10-14 用户口径）。

        不碰沙箱、不查平台接口：``base...head`` 两支都由 PR 的入参给（``_pr_refs``），
        裸仓里有对象就能算。缓存里还没这个仓库（从没打开过代码面板）时返回 ``None``：
        一次 clone 可能很大，不该在一轮对话的收尾里顺手做掉 —— 界面上那行摘要就不出现，
        与「这一轮没改代码」看起来一样（这也是用户口径：展示的前提是写完并提了 PR）。

        本地已有缓存但缺那两支分支（coder 刚推上去的，本地还没 fetch）时先 ``sync()`` 一次
        再算 —— 这一杆也顺带把代码面板的缓存刷新了。
        """
        base = pr_refs.get("base", "")
        head = pr_refs.get("head", "")
        if not base or not head:
            return None
        spec = RepoSpec.from_session(session)
        try:
            if not repo_cache.is_cloned(self._settings, spec):
                return None
            try:
                files = await repo_cache.compare(self._settings, spec, base=base, head=head)
            except RepoViewError:
                await repo_cache.sync(self._settings, spec)
                files = await repo_cache.compare(self._settings, spec, base=base, head=head)
        except Exception:
            logger.exception("算这一轮的改动失败（本轮照常收尾）")
            return None
        payload = {
            "base": base,
            "head": head,
            "files": [
                {"path": item.path, "added": item.added, "deleted": item.deleted, "status": item.status}
                for item in files[:_CHANGES_MAX_FILES]
            ],
        }
        if len(files) > _CHANGES_MAX_FILES:
            payload["total"] = len(files)
        return payload

    # -- 仓库记忆 ----------------------------------------------------------
    async def _refresh_repo_memory(
        self, session: Session, runtime: SessionRuntime
    ) -> None:
        """轮末按 Gitee 的实际 PR 状态刷新仓库记忆（coding / review / analysis 收尾时的打底）。

        与启动闸门（``middleware/memory.py`` 的 ``refresh_memory``）是同一件事的
        两次机会，读→对账→写回与「查不到就不写」的纪律都复用那一个函数。打底只补事实
        （PR 编号 / 分支 / 状态），不替模型写散文 —— 散文由 ``save_repo_memory`` 负责。

        **失败不改本轮结论。** 记忆是副产物：查不到 PR 也好、写盘报错也好，都只记日志，
        交给下一轮（或下一次 ``save_repo_memory``）再刷。同理不建新记忆，只刷已存在的
        那份（见 ``refresh_memory``）。

        判据是**形态**（coding / review / analysis 即「这一轮有要沉淀的事实」），
        不是「本轮建出过 PR」—— 后者要对工具返回文案做字符串匹配，改一次文案就会静默失效。
        """
        path = memory_path(
            session.repo.owner, session.repo.repo, self._settings.memories_prefix
        )
        try:
            # 刷没刷由 refresh_memory 自己记（成功 info、失败 warning）
            await refresh_memory(
                runtime.backend,
                path,
                build_git_client(self._settings, session.repo).all_pulls,
                session.repo,
            )
        except PrLookupError as exc:
            logger.warning("查询仓库 PR 失败，仓库记忆本轮未刷新：%s", exc)
        except Exception:
            logger.exception("刷新仓库记忆失败（不影响本轮结论）")

    # -- 业务库记录 --------------------------------------------------------
    async def _plan_snapshot(self, session: Session) -> dict[str, Any] | None:
        """读该会话最新方案并序列化；无方案或读库失败返回 None（不打断对话）。"""
        try:
            async with self._db.session() as db:
                plan = await dao.get_latest_plan(db, session_id=session.db_id)
        except Exception:
            logger.exception("读方案失败（不影响主流程）")
            return None
        return None if plan is None else plan_payload(plan)

    async def _name_session(self, session: Session, message: str) -> None:
        """用首条用户消息命名会话（已有标题的会话不受影响）。"""
        try:
            async with self._db.session() as db:
                await dao.set_session_title_if_empty(
                    db, session.db_id, _title_from(message)
                )
        except Exception:
            logger.exception("写会话标题失败（不影响主流程）")

    async def _record(
        self,
        session: Session,
        *,
        type: str,
        summary: str | None = None,
        ref_id: int | None = None,
        content: str | None = None,
        extra: dict | list | None = None,
    ) -> None:
        try:
            async with self._db.session() as db:
                await dao.add_record(
                    db, session_id=session.db_id, type=type,
                    summary=summary, ref_id=ref_id, content=content, extra=extra,
                )
        except Exception:
            logger.exception("写会话记录失败（不影响主流程）")

    async def _record_plan_once(self, session: Session, plan: dict[str, Any]) -> None:
        """把「方案已生成」写进聊天流（``type='plan'``），**同一份方案只写一条**。

        方案落地后会被 ``updatePlan`` / ``updatePlanSteps`` 反复改；每次改都追加一条记录，
        聊天流就会变成「方案已更新」的流水。所以按 ``plan_id`` 去重，判据放在库里
        （``dao.plan_record_exists``）—— 一次新的运行里内存标记从零起步，只靠内存会重复写。

        记录里存**方案全文**：前端那条链接点开就地展开，不必再补一次请求；刷新页面走
        ``/records`` 拿到的也是同一份内容。
        """
        plan_id = int(plan["plan_id"])
        try:
            async with self._db.session() as db:
                if await dao.plan_record_exists(
                    db, session_id=session.db_id, plan_id=plan_id
                ):
                    return
        except Exception:
            logger.exception("查方案记录失败（不影响主流程）")
            return
        await self._record(
            session,
            type="plan",
            summary=plan.get("title") or "实施计划",
            ref_id=plan_id,
            content=plan.get("content"),
            extra={
                "plan_id": plan_id,
                "status": plan.get("status"),
                "steps": plan.get("steps") or [],
            },
        )

    async def _record_interrupted(
        self,
        session: Session,
        text: str,
        translator: V3Translator,
        intent: str,
        terms: tuple[str, ...],
        exc: BaseException | None = None,
        *,
        closing: str | None = None,
        extra: Mapping[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
    ) -> None:
        """一轮跑到一半断了：把已产出的部分补一条记录，别让它在历史里凭空消失。

        正文与异常文本都过脱敏（异常文本常把工具返回值整段带上），原文只进日志。
        标记与错误都写进正文，前端收到 error 帧时也是把错误文本追加在正文后面
        （``App.vue``），这样刷新后按记录回放看到的东西跟实时那次一样。

        没有正文时也记 —— 否则时间线上就是一条用户消息后面空着，看不出这轮出过事。
        「未产出内容」的判据是**用户看到过东西没有**：正文或待办树都算，只有待办时
        标记得说部分输出，说「未产出内容」会和上面那个待办框自相矛盾。

        ``exc=None`` 走的是**非故障**那条路（取消，或循环守卫中止本轮）：措辞与错误分开
        写，「错误：CancelledError」对用户毫无意义；有没有产出也不分两句 —— 中止就是中止。
        这条路默认的措辞是用户终止，另外两种情形用 ``closing`` 换措辞、``extra`` 换
        ``extra`` 里的分流标记（见 ``middleware/guard.py`` 的 ``TurnStop``）。
        """
        has_todos = translator.parts.has_todos
        if exc is None:
            head = note = closing or CANCELLED_BY_USER
            marker: dict[str, Any] = dict(extra) if extra else {"cancelled": True}
        else:
            safe_error = redact(str(exc), terms)
            head = INTERRUPTED_PARTIAL if (text or has_todos) else INTERRUPTED_EMPTY
            note = f"{head}\n错误：{safe_error}"
            marker = {
                "error": {"type": type(exc).__name__, "message": safe_error},
            }
        # 每条都是「这一条不是完整的回答」，故 partial 都在；靠 cancelled / aborted / error 再分家
        fields: dict[str, Any] = {"intent": intent, "partial": True, **marker}
        if usage is not None:
            # 中断的轮次同样入账（钱真花了），``usage.interrupted`` 已标成真
            fields["usage"] = usage
        if has_todos:
            # 有 parts 时前端按 parts 回放、content 整段被忽略，标记得补进块序列尾部
            fields["parts"] = [
                *_redacted_parts(translator.parts.data, terms),
                {"kind": "text", "content": note},
            ]
        await self._record(
            session,
            type="assistant",
            summary=text[:200] or head,
            content=f"{text}\n\n{note}" if text else note,
            extra=fields,
        )
