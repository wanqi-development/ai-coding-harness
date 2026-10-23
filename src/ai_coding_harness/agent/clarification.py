"""意图澄清的结构化协议：qa 自由措辞，但必须包在 ``<CLARIFY>`` 里。

为什么不用普通文本让前端自己猜：澄清选项要变成可点击的固定输入，必须有一个
机器可读的边界。``<CLARIFY>`` + JSON 是最小协议 —— qa 仍然可以自由写问题和选项文案，
只是把可执行的那部分落在固定字段里。

这个模块不依赖 server / agent，解析器可以单独测试。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

#: 澄清选项的静态全集；运行时还会按当前 PR 状态再收一次候选。
CLARIFY_INTENTS: tuple[str, ...] = (
    "qa",
    "plan",
    "coding",
    "rework",
)

CLARIFY_OPEN = "<CLARIFY>"
CLARIFY_CLOSE = "</CLARIFY>"

_BLOCK_RE = re.compile(
    rf"{re.escape(CLARIFY_OPEN)}\s*(.*?)\s*{re.escape(CLARIFY_CLOSE)}",
    re.IGNORECASE | re.DOTALL,
)
_STEP_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")


@dataclass(frozen=True)
class ClarificationOption:
    intent: str
    label: str
    description: str = ""
    value: str = ""

    def as_dict(self) -> dict[str, str]:
        payload = {
            "intent": self.intent,
            "label": self.label,
            "description": self.description,
            "value": self.value or self.intent or self.label,
        }
        return {key: value for key, value in payload.items() if value}


@dataclass(frozen=True)
class ClarificationStep:
    id: str
    question: str
    options: tuple[ClarificationOption, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "options": [option.as_dict() for option in self.options],
        }


@dataclass(frozen=True)
class ClarificationRequest:
    steps: tuple[ClarificationStep, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"steps": [step.as_dict() for step in self.steps]}

    @property
    def question(self) -> str:
        """兼容旧调用：单题协议下的第一个问题。"""
        return self.steps[0].question

    @property
    def options(self) -> tuple[ClarificationOption, ...]:
        """兼容旧调用：单题协议下的第一组候选。"""
        return self.steps[0].options


def _strip_fence(raw: str) -> str:
    """去掉模型偶尔包上的 ```json / ```；协议本身不要求 markdown。"""
    text = raw.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_clarification(
    text: str, allowed_intents: tuple[str, ...] | None = None
) -> ClarificationRequest | None:
    """从 qa 回复里取出合法的 1~3 步澄清；不合法返回 ``None``。

    第一页负责确定意图，所以第一步的选项必须带合法且不重复的 ``intent``；
    后续页用于业务口径，选项可以只带 ``value`` / ``label``。旧版单题
    ``{question, options}`` 仍按一步协议兼容。
    """
    if not isinstance(text, str):
        return None
    match = _BLOCK_RE.search(text)
    if match is None:
        return None
    raw = _strip_fence(match.group(1))
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    allowed = set(allowed_intents or CLARIFY_INTENTS)
    raw_steps = payload.get("steps")
    if raw_steps is None:
        raw_steps = [
            {
                "id": "intent",
                "question": payload.get("question"),
                "options": payload.get("options"),
            }
        ]
    if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= 3:
        return None

    steps: list[ClarificationStep] = []
    seen_ids: set[str] = set()
    for step_index, raw_step in enumerate(raw_steps):
        if not isinstance(raw_step, dict):
            return None
        question = str(raw_step.get("question") or "").strip()
        step_id = str(raw_step.get("id") or f"step-{step_index + 1}").strip()
        raw_options = raw_step.get("options")
        if (
            not question
            or not step_id
            or _STEP_ID.fullmatch(step_id) is None
            or step_id in seen_ids
        ):
            return None
        if not isinstance(raw_options, list) or not 2 <= len(raw_options) <= 4:
            return None
        seen_ids.add(step_id)

        options: list[ClarificationOption] = []
        seen_intents: set[str] = set()
        for item in raw_options:
            if not isinstance(item, dict):
                return None
            intent = str(item.get("intent") or "").strip().lower()
            label = str(item.get("label") or "").strip()
            description = str(item.get("description") or "").strip()
            value = str(item.get("value") or "").strip()
            if not label:
                return None
            if intent and intent not in allowed:
                return None
            if step_index == 0:
                if intent not in allowed or intent in seen_intents:
                    return None
                seen_intents.add(intent)
            options.append(
                ClarificationOption(
                    intent=intent,
                    label=label,
                    description=description,
                    value=value,
                )
            )
        steps.append(
            ClarificationStep(
                id=step_id,
                question=question,
                options=tuple(options),
            )
        )
    return ClarificationRequest(steps=tuple(steps))
