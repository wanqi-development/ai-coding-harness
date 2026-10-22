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


@dataclass(frozen=True)
class ClarificationOption:
    intent: str
    label: str
    description: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "intent": self.intent,
            "label": self.label,
            "description": self.description,
        }


@dataclass(frozen=True)
class ClarificationRequest:
    question: str
    options: tuple[ClarificationOption, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "options": [option.as_dict() for option in self.options],
        }


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
    """从 qa 回复里取出合法的澄清块；不合法返回 ``None``。

    约束是刻意收窄的：2~3 个选项、``intent`` 必须在白名单里、问题与 label 非空。
    任何一项不满足都宁可退回普通文本，也不把伪造/不可执行的选项送到前端。
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
    question = payload.get("question")
    raw_options = payload.get("options")
    if not isinstance(question, str) or not question.strip():
        return None
    if not isinstance(raw_options, list) or not 2 <= len(raw_options) <= 3:
        return None
    allowed = set(allowed_intents or CLARIFY_INTENTS)
    options: list[ClarificationOption] = []
    seen: set[str] = set()
    for item in raw_options:
        if not isinstance(item, dict):
            return None
        intent = str(item.get("intent") or "").strip().lower()
        label = str(item.get("label") or "").strip()
        description = str(item.get("description") or "").strip()
        if intent not in allowed or not label:
            return None
        if intent in seen:
            return None
        seen.add(intent)
        options.append(
            ClarificationOption(
                intent=intent,
                label=label,
                description=description,
            )
        )
    return ClarificationRequest(question=question.strip(), options=tuple(options))
