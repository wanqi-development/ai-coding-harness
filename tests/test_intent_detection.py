"""Intent classification: recognition only; task conflicts are enforced later."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any

import typesafe_sdk
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from ai_coding_harness.server.intent import (
    SOURCE_CLARIFY,
    SOURCE_JEV,
    SOURCE_LLM,
    TASK_TYPES,
    IntentDecision,
    JevDecider,
    _system_prompt,
    _user_prompt,
    clarification_decision,
    detect_intent,
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
    def test_only_current_intents_remain(self) -> None:
        assert TASK_TYPES == ("coding", "plan", "qa")
        for old in ("rework", "review", "analysis", "sync"):
            assert old not in TASK_TYPES


class TestStrongKeywords:
    def test_plan_and_coding_commands_are_deterministic(self) -> None:
        assert match_strong("先出个方案") == "plan"
        assert match_strong("开始实施") == "coding"
        assert match_strong("按这个方案做吧") == "coding"
        assert match_strong("写技术文档") == "coding"
        assert match_strong("更新 README 并提 PR") == "coding"
        assert match_strong("给我出一个写文档的方案") is None
        assert match_strong("先讨论文档结构，不要写") is None

    def test_review_and_fix_requests_are_coding(self) -> None:
        for message in (
            "帮我代码审查一下",
            "按评审意见修改",
            "根据审查结论改代码",
            "看看这个 PR 并修复P1",
        ):
            assert match_strong(message) == "coding"

    def test_sync_is_gone_from_the_keyword_table(self) -> None:
        assert match_strong("同步仓库") is None

    def test_strong_coding_command_is_not_rewritten_by_pr_state(self) -> None:
        import asyncio

        decision = asyncio.run(
            detect_intent(
                "开始实施",
                model=_FakeClassifier(),
                allowed_intents=TASK_TYPES,
                repo_state="存在 open PR",
            )
        )
        assert decision.intent == "coding"
        assert decision.source == "keyword"


class TestClassificationContext:
    def test_prompt_can_list_only_requested_candidates(self) -> None:
        prompt = _system_prompt(("qa",), repo_state="有 1 个 open PR")

        assert "当前仓库状态：有 1 个 open PR" in prompt
        assert "qa：" in prompt
        assert "coding：要写" not in prompt
        assert "plan：方案这个对象本身" not in prompt

    async def test_jev_receives_repo_state_and_candidates(self) -> None:
        jev = _FakeJev("coding")

        decision = await detect_intent(
            "这个 PR 再看看并修复",
            jev=jev,
            allowed_intents=TASK_TYPES,
            repo_state="有 2 个 open PR",
            mode="jev",
        )

        assert decision.intent == "coding"
        assert decision.source == SOURCE_JEV
        assert jev.allowed == TASK_TYPES
        assert jev.repo_state == "有 2 个 open PR"

    async def test_llm_cannot_return_a_disallowed_intent(self) -> None:
        model = _FakeClassifier(reply="coding\n想写代码")

        decision = await detect_intent(
            "帮我看一下",
            model=model,
            mode="llm",
            allowed_intents=("qa",),
        )

        assert decision.source == SOURCE_CLARIFY
        assert decision.intent == "qa"
        assert decision.needs_clarification
        assert "coding：" not in model.prompts[0]

    async def test_model_unavailable_goes_to_qa_clarification(self) -> None:
        decision = await detect_intent(
            "一句没有口令的话",
            model=None,
            mode="llm",
            allowed_intents=TASK_TYPES,
        )

        assert decision.intent == "qa"
        assert decision.source == SOURCE_CLARIFY
        assert decision.needs_clarification


class TestContext:
    def test_user_prompt_carries_history_and_repo_state(self) -> None:
        text = _user_prompt(
            "继续",
            ["上一轮"],
            ["coding(审查)"],
            previous_intent="coding",
            dialogue=[{"role": "assistant", "content": "要修哪条？"}],
            repo_state="有 1 个 open PR",
        )

        assert "当前仓库状态：有 1 个 open PR" in text
        assert "上一轮判定：coding" in text
        assert "最近三个未中断完整用户轮次" in text
        assert "以下正文未截断" in text
        assert "coding(审查)" in text

    def test_low_confidence_clarification_is_still_qa(self) -> None:
        decision = IntentDecision("coding", SOURCE_LLM, confidence=0.31)

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
        assert parse_reply("review\n0.8\n-\n审查", allowed_intents=TASK_TYPES) is None

    def test_legacy_two_line_reply_still_parses(self) -> None:
        parsed = parse_reply("plan\n先给步骤")

        assert parsed is not None
        assert parsed.intent == "plan"
        assert parsed.reason == "先给步骤"


class TestFailures:
    def test_jev_client_disables_sdk_zstd_compression(self, monkeypatch) -> None:
        seen: dict[str, Any] = {}

        class FakeTypeSafeClient:
            def __init__(self, **kwargs):
                seen.update(kwargs)

        monkeypatch.setattr(
            typesafe_sdk, "AsyncTypeSafeClient", FakeTypeSafeClient
        )

        JevDecider(
            api_key="test-key",
            base_url="https://api.ztoken.pro",
            model="jev-latest",
        )

        assert seen["model"] == "jev-latest"
        assert seen["compression"] is False

    async def test_classifier_error_goes_to_qa_clarification(self, caplog) -> None:
        decision = await detect_intent(
            "随便看看",
            model=_FakeClassifier(error="boom"),
            mode="llm",
            allowed_intents=TASK_TYPES,
        )

        assert decision.source == SOURCE_CLARIFY
        assert decision.intent == "qa"
        assert decision.needs_clarification
        assert "大模型意图判定失败" in caplog.text
        assert "RuntimeError: boom" in caplog.text

    async def test_jev_failure_goes_to_qa_without_trying_the_llm(self) -> None:
        model = _FakeClassifier(reply="qa")
        decision = await detect_intent(
            "随便看看",
            jev=_FakeJev(None),
            model=model,
            mode="jev",
            allowed_intents=TASK_TYPES,
        )

        assert decision.source == SOURCE_CLARIFY
        assert decision.intent == "qa"
        assert decision.needs_clarification
        assert model.calls == 0
