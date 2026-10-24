"""Server 轮次编排：clarify。"""


import logging
from collections.abc import Mapping, Sequence
from typing import Any

from ai_coding_harness.agent.clarification import CLARIFY_INTENTS
from ai_coding_harness.db import dao
from ai_coding_harness.session.manager import (
    Session,
)

logger = logging.getLogger(__name__)



class TurnClarifyMixin:
    """clarify 相关方法（由 HarnessService 组合）。"""

    @staticmethod
    def _clarification_steps(row: Any) -> list[dict[str, Any]]:
        """把旧版单题记录和新版 steps 统一成步骤列表。"""
        steps = getattr(row, "steps", None)
        if isinstance(steps, list) and steps:
            return [step for step in steps if isinstance(step, dict)]
        options = row.options if isinstance(row.options, list) else []
        return [
            {
                "id": "intent",
                "question": getattr(row, "question", "") or "请选择下一步",
                "options": options,
            }
        ]

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
        steps = self._clarification_steps(row)
        return {
            "id": row.id,
            "question": row.question,
            "options": row.options or [],
            "steps": steps,
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
        choice: int | str | None = None,
        answers: Sequence[Mapping[str, Any]] | None = None,
    ) -> tuple[str, str, str] | None:
        """校验并消费全部结构化选择；成功返回 (补充后原话, 意图, 选项摘要)。"""
        try:
            async with self._db.session() as db:
                row = await dao.get_clarification(db, session_id=session.db_id)
                if row is None or row.id != clarification_id:
                    if row is not None:
                        await dao.delete_clarification(db, session_id=session.db_id)
                    return None
                steps = self._clarification_steps(row)
                picked = await self._pick_clarification_answers(
                    steps, choice=choice, answers=answers
                )
                if picked is None:
                    await dao.delete_clarification(db, session_id=session.db_id)
                    return None
                first = picked[0]
                intent = str(first["selected"].get("intent") or "").strip()
                if intent not in CLARIFY_INTENTS:
                    await dao.delete_clarification(db, session_id=session.db_id)
                    return None
                labels = [str(item["selected"].get("label") or "未命名选项") for item in picked]
                await dao.delete_clarification(db, session_id=session.db_id)
                message = row.original_message
                if answers:
                    lines = [message, "", "用户在澄清弹窗中的选择："]
                    for item in picked:
                        question = str(
                            item["step"].get("question") or item["step"].get("id") or ""
                        )
                        label = str(item["selected"].get("label") or "未命名选项")
                        lines.append(f"- {question}：{label}")
                    message = "\n".join(lines)
                return message, intent, " / ".join(labels)
        except Exception:
            logger.exception("消费待澄清状态失败")
            return None

    async def _pick_clarification_answers(
        self,
        steps: list[dict[str, Any]],
        *,
        choice: int | str | None,
        answers: Sequence[Mapping[str, Any]] | None,
    ) -> list[dict[str, Any]] | None:
        """按 step_id 校验答案；旧 choice 只允许单题记录继续使用。"""
        raw_answers = list(answers or [])
        if not raw_answers:
            if choice is None or len(steps) != 1:
                return None
            first_step = steps[0]
            options = first_step.get("options") if isinstance(first_step, dict) else None
            if not isinstance(options, list):
                return None
            index = self._choice_index(options, choice)
            if index is None:
                return None
            return [{"step": first_step, "selected": options[index]}]

        by_step = {
            str(answer.get("step_id") or ""): answer.get("choice")
            for answer in raw_answers
            if isinstance(answer, Mapping)
        }
        picked: list[dict[str, Any]] = []
        for step in steps:
            step_id = str(step.get("id") or "")
            options = step.get("options")
            if not step_id or step_id not in by_step or not isinstance(options, list):
                return None
            index = self._choice_index(options, by_step[step_id])
            if index is None:
                return None
            picked.append({"step": step, "selected": options[index]})
        return picked if len(by_step) == len(steps) else None

    @staticmethod
    def _choice_index(options: list[Any], choice: Any) -> int | None:
        try:
            index = int(choice)
        except (TypeError, ValueError):
            index = -1
        if 0 <= index < len(options) and isinstance(options[index], dict):
            return index
        wanted = str(choice or "").strip().lower()
        for index, item in enumerate(options):
            if not isinstance(item, dict):
                continue
            if wanted and wanted in {
                str(item.get("intent") or "").strip().lower(),
                str(item.get("value") or "").strip().lower(),
            }:
                return index
        return None

    async def _save_clarification(
        self,
        session: Session,
        *,
        original_message: str,
        question: str,
        options: list[dict[str, Any]],
        steps: list[dict[str, Any]] | None = None,
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
                    steps=steps,
                )
                return {
                    "id": row.id,
                    "question": row.question,
                    "options": row.options or [],
                    "steps": row.steps or [],
                    "allow_other": True,
                }
        except Exception:
            logger.exception("保存待澄清状态失败（本轮退回普通文本）")
            return None
