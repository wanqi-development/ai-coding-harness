"""Server 轮次编排：intent。"""


import logging
from collections.abc import Mapping, Sequence

from langchain_core.language_models.chat_models import BaseChatModel

from ai_coding_harness.agent.llm import build_classifier_model, intent_mode_for
from ai_coding_harness.db import dao
from ai_coding_harness.server.intent import (
    PREVIOUS_REPLY_CHARS,
    PREVIOUS_REPLY_SLACK,
    IntentDecision,
    build_jev_decider,
    detect_intent,
    tail_excerpt,
)
from ai_coding_harness.server.repo_state import (
    PullSnapshot,
    load_pull_snapshot,
)
from ai_coding_harness.session.manager import (
    Session,
)

logger = logging.getLogger(__name__)



class TurnIntentMixin:
    """intent 相关方法（由 HarnessService 组合）。"""

    def _classifier_for(self, user_id: int | None) -> BaseChatModel | None:
        """分类模型实例；未启用或构建失败时返回 None（退回纯关键字判定）。"""
        if not self._settings.intent_llm_enabled:
            return None
        if user_id is None:
            if self._classifier is None:
                try:
                    self._classifier = build_classifier_model(self._settings)
                except Exception:
                    logger.exception("意图分类模型构建失败，退回关键字判定")
                    return None
            return self._classifier
        cached = self._classifiers_by_user.get(user_id)
        if cached is None:
            try:
                cached = build_classifier_model(self._settings, user_id=user_id)
                self._classifiers_by_user[user_id] = cached
            except Exception:
                logger.exception("意图分类模型构建失败，退回关键字判定")
                return None
        return cached

    def _jev_for(self, user_id: int | None):
        if self._intent_jev is not None:
            return self._intent_jev
        if user_id is None:
            return None
        cached = self._intent_jev_by_user.get(user_id)
        if cached is None:
            cached = build_jev_decider(self._settings, user_id=user_id)
            if cached is not None:
                self._intent_jev_by_user[user_id] = cached
        return cached

    async def _repo_snapshot(self, session: Session) -> PullSnapshot | None:
        """每轮实时查一次 PR 列表；失败时只保留只读问答候选。"""
        try:
            settings = self._manager.settings_for(session)
            return await load_pull_snapshot(
                settings,
                session.repo,
                database=self._db,
                session_id=session.db_id,
            )
        except Exception:
            logger.exception("仓库 PR 状态查询失败（本轮只保留只读问答候选）")
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
        """最近三个用户轮次内的完整会话：user / assistant / plan 均保留全文。"""
        if self._settings.intent_history_turns <= 0:
            return []
        try:
            async with self._db.session() as db:
                rows = await dao.recent_dialogue(
                    db,
                    session_id=session.db_id,
                    rounds=self._settings.intent_history_turns,
                )
        except Exception:
            logger.exception("读最近三轮对话失败（分类将只带本轮消息）")
            return []
        return [
            {
                "role": role,
                "content": content,
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

    async def _detect_intent(
        self,
        message: str,
        history: Sequence[str],
        previous_reply: str | None = None,
        previous_intent: str | None = None,
        dialogue: Sequence[Mapping[str, str]] = (),
        allowed_intents: Sequence[str] | None = None,
        repo_state: str | None = None,
        user_id: int | None = None,
    ) -> IntentDecision:
        """判定本轮意图：口令 → JEV → 大模型（都带最近几轮用户消息）→ 关键字口径。

        用量记进本轮的累加器（``self._intent_usage``）—— 口令层不调模型，命中时那份额
        自然是空的，正是我们想要的「这一轮没为判定花钱」。
        """
        return await detect_intent(
            message,
            history=history,
            jev=self._jev_for(user_id),
            model=self._classifier_for(user_id),
            timeout=self._settings.intent_timeout_seconds,
            usage=self._intent_usage,
            previous_reply=previous_reply,
            previous_intent=previous_intent,
            dialogue=dialogue,
            allowed_intents=allowed_intents,
            repo_state=repo_state,
            # 意图识别走哪一关：jev（三关，默认）/ llm（三关，不碰 JEV）—— 见 intent.detect_intent
            mode=intent_mode_for(self._settings, user_id),
        )

    async def _rejudge_intent(
        self,
        message: str,
        history: Sequence[str],
        *,
        session: Session | None = None,
        previous_reply: str | None = None,
        previous_intent: str | None = None,
        dialogue: Sequence[Mapping[str, str]] | None = None,
        allowed_intents: Sequence[str] | None = None,
        repo_state: str | None = None,
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
        if dialogue is None and session is not None:
            dialogue = await self._dialogue(session)
        user_id = getattr(session, "user_id", None) if session is not None else None
        return await detect_intent(
            message,
            history=history,
            jev=self._jev_for(user_id),
            model=self._classifier_for(user_id),
            timeout=self._settings.intent_timeout_seconds,
            force_llm=True,
            usage=self._intent_usage,
            previous_reply=previous_reply,
            previous_intent=previous_intent,
            dialogue=dialogue or (),
            allowed_intents=allowed_intents,
            repo_state=repo_state,
            mode=intent_mode_for(self._settings, user_id),
        )
