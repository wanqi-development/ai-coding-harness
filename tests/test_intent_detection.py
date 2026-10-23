"""意图判定守卫：状态候选 → 口令 → JEV / 大模型 → 关键字兜底。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from ai_coding_harness.server.intent import (
    SOURCE_FALLBACK,
    SOURCE_JEV,
    SOURCE_LLM,
    TASK_TYPES,
    IntentDecision,
    _system_prompt,
    _user_prompt,
    active_task_override,
    clarification_decision,
    classify,
    detect_intent,
    gate_intent_for_state,
    match_strong,
    parse_reply,
)


class _FakeClassifier(BaseChatModel):
    reply: str = "qa\n只读回答"
    error: str = ""
    delay: float = 0.0
    calls: int = 0
    prompts: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "fake-classifier"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls += 1
        self.prompts.append("\n".join(message.text for message in messages))
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise RuntimeError(self.error)
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=self.reply))]
        )


class _FakeJev:
    def __init__(self, intent: str | None = "qa", confidence: float = 0.9) -> None:
        self.intent = intent
        self.confidence = confidence
        self.calls = 0
        self.allowed: Sequence[str] | None = None
        self.repo_state: str | None = None

    async def decide(
        self,
        message: str,
        *,
        history: Sequence[str] = (),
        hints: Sequence[str] = (),
        usage: dict[str, int] | None = None,
        previous_reply: str | None = None,
        previous_intent: str | None = None,
        active_intent: str | None = None,
        dialogue: Sequence[Mapping[str, str]] = (),
        allowed_intents: Sequence[str] | None = None,
        repo_state: str | None = None,
    ) -> tuple[str, str, float, tuple[tuple[str, float], ...]] | None:
        self.calls += 1
        self.allowed = allowed_intents
        self.repo_state = repo_state
        if self.intent is None:
            return None
        return (
            self.intent,
            "p",
            self.confidence,
            ((self.intent, self.confidence),),
        )


class TestTaskSet:
    def test_the_four_remaining_intents(self) -> None:
        assert TASK_TYPES == ("coding", "rework", "plan", "qa")

    def test_review_and_analysis_are_not_intents(self) -> None:
        assert "review" not in TASK_TYPES
        assert "analysis" not in TASK_TYPES
        assert "sync" not in TASK_TYPES


class TestStrongKeywords:
    def test_plan_and_coding_commands_are_deterministic(self) -> None:
        assert match_strong("先出个方案") == "plan"
        assert match_strong("开始实施") == "coding"
        assert match_strong("按这个方案做吧") == "coding"
        assert match_strong("写技术文档") == "coding"
        assert match_strong("更新 README 并提 PR") == "coding"
        assert match_strong("写 AGENT.md") == "coding"
        assert match_strong("写 AGENTS.md") == "coding"
        assert match_strong("给我出一个写文档的方案") is None
        assert match_strong("先讨论文档结构，不要写") is None

    def test_open_pr_review_and_fix_commands_are_rework(self) -> None:
        for message in (
            "帮我代码审查一下",
            "按评审意见修改",
            "根据审查结论改代码",
        ):
            assert match_strong(message, allowed_intents=("qa", "rework")) == "rework"

    def test_sync_is_gone_from_the_keyword_table(self) -> None:
        assert match_strong("同步仓库") is None
        assert classify("同步仓库", allowed_intents=("qa", "plan", "coding")) == "coding"

    def test_coding_command_stays_coding_and_is_blocked_later(self) -> None:
        decision = asyncio.run(
            detect_intent(
                "开始实施",
                model=_FakeClassifier(),
                allowed_intents=("qa", "rework"),
            )
        )
        assert decision.intent == "coding"
        assert decision.source == "keyword"


class TestStateRestrictedClassification:
    def test_the_prompt_only_lists_the_allowed_intents(self) -> None:
        prompt = _system_prompt(("qa", "rework"), repo_state="有 1 个 open PR")

        assert "当前仓库状态：有 1 个 open PR" in prompt
        assert "rework：" in prompt
        assert "qa：" in prompt
        assert "coding：要写" not in prompt
        assert "plan：方案这个对象本身" not in prompt

    async def test_jev_receives_the_restricted_candidate_set(self) -> None:
        jev = _FakeJev("rework")

        decision = await detect_intent(
            "这个 PR 再看看",
            jev=jev,
            allowed_intents=("qa", "rework"),
            repo_state="有 2 个 open PR",
            mode="jev",
        )

        assert decision.intent == "rework"
        assert decision.source == SOURCE_JEV
        assert jev.allowed == ("qa", "rework")
        assert jev.repo_state == "有 2 个 open PR"

    async def test_the_llm_cannot_return_a_disallowed_intent(self) -> None:
        model = _FakeClassifier(reply="coding\n想写代码")

        decision = await detect_intent(
            "帮我看一下",
            model=model,
            mode="llm",
            allowed_intents=("qa", "rework"),
        )

        assert decision.source == SOURCE_FALLBACK
        assert decision.intent == "qa"
        assert "coding：" not in model.prompts[0]

    async def test_the_llm_returns_an_allowed_intent(self) -> None:
        model = _FakeClassifier(reply="rework\n处理 PR")

        decision = await detect_intent(
            "帮我看看这个 PR 的设计",
            model=model,
            mode="llm",
            allowed_intents=("qa", "rework"),
        )

        assert decision.intent == "rework"
        assert decision.source == SOURCE_LLM

    def test_keyword_fallback_uses_the_only_safe_candidate(self) -> None:
        assert classify("一句没有口令的话", allowed_intents=("qa",)) == "qa"


class TestGate:
    def test_function_gate_blocks_coding_and_plan_when_a_pr_is_open(self) -> None:
        assert gate_intent_for_state("coding", ("qa", "rework")).intent is None
        assert gate_intent_for_state("plan", ("qa", "rework")).intent is None

    def test_function_gate_blocks_rework_without_a_pr(self) -> None:
        result = gate_intent_for_state("rework", ("qa", "plan", "coding"))
        assert result.intent is None
        assert "没有还开着的 PR" in result.reason


class TestContext:
    def test_user_prompt_carries_history_and_repo_state(self) -> None:
        text = _user_prompt(
            "继续",
            ["上一轮"],
            ["rework(评审提)"],
            previous_intent="rework",
            active_intent="rework",
            dialogue=[{"role": "assistant", "content": "要修哪条？"}],
            repo_state="有 1 个 open PR",
        )

        assert "当前仓库状态：有 1 个 open PR" in text
        assert "上一轮判定：rework" in text
        assert "最近两轮对话" in text
        assert "rework(评审提)" in text

    def test_short_continuation_keeps_an_execution_task(self) -> None:
        assert active_task_override("继续吧", "qa", "rework") == "rework"
        assert active_task_override("修复吧", "qa", "plan") == "coding"

    def test_questions_do_not_override(self) -> None:
        assert active_task_override("那是不是可以读一部分", "qa", "coding") is None

    def test_low_confidence_clarification_is_still_qa(self) -> None:
        decision = IntentDecision("rework", SOURCE_LLM, confidence=0.31)

        clarified = clarification_decision(decision, 0.55)

        assert clarified is not None
        assert clarified.intent == "qa"
        assert clarified.needs_clarification


class TestParsing:
    def test_four_line_reply(self) -> None:
        parsed = parse_reply("qa\n0.82\nplan 0.11\n只读解释")

        assert parsed is not None
        assert parsed.intent == "qa"
        assert parsed.confidence == 0.82
        assert parsed.candidates == (("plan", 0.11),)

    def test_disallowed_labels_are_rejected(self) -> None:
        assert (
            parse_reply(
                "review\n0.8\n-\n审查",
                allowed_intents=("qa", "rework"),
            )
            is None
        )

    def test_legacy_two_line_reply_still_parses(self) -> None:
        parsed = parse_reply("plan\n先给步骤")

        assert parsed is not None
        assert parsed.intent == "plan"
        assert parsed.reason == "先给步骤"


class TestFailures:
    async def test_classifier_error_falls_back(self) -> None:
        decision = await detect_intent(
            "随便看看",
            model=_FakeClassifier(error="boom"),
            mode="llm",
            allowed_intents=("qa", "rework"),
        )

        assert decision.source == SOURCE_FALLBACK
        assert decision.intent == "qa"

    async def test_jev_failure_falls_back_without_trying_the_llm(self) -> None:
        model = _FakeClassifier(reply="qa")
        decision = await detect_intent(
            "随便看看",
            jev=_FakeJev(None),
            model=model,
            mode="jev",
            allowed_intents=("qa", "rework"),
        )

        assert decision.source == SOURCE_FALLBACK
        assert model.calls == 0
