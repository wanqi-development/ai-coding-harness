"""Server 轮次编排：clarify。"""


import logging
from typing import Any

from ai_coding_harness.agent.clarification import CLARIFY_INTENTS
from ai_coding_harness.db import dao
from ai_coding_harness.session.manager import (
    Session,
)

logger = logging.getLogger(__name__)



class TurnClarifyMixin:
    """clarify 相关方法（由 HarnessService 组合）。"""

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
