"""Runtime context injection: task facts only, never an abstract intent label."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from ai_coding_harness.agent.graph import build_agent
from ai_coding_harness.agent.middleware.context_inject import (
    clarification_block,
    work_task_block,
)
from ai_coding_harness.agent.profiles import ORCHESTRATE_FORM
from ai_coding_harness.agent.turn_state import TurnState
from ai_coding_harness.safety.policy import policy_for

_SYSTEM_PROMPT = "SYS-PROMPT-哨兵"
_POLICY = policy_for(ORCHESTRATE_FORM)


class _Recorder(BaseChatModel):
    """Minimal fake model that records what the middleware actually sent."""

    seen: list[list[BaseMessage]] = Field(default_factory=list)

    def _get_ls_params(self, **kwargs: object) -> dict[str, str]:
        return {"ls_provider": "openai"}

    @property
    def _llm_type(self) -> str:
        return "recorder"

    def bind_tools(self, tools: Any, **kwargs: Any) -> _Recorder:
        return self

    def _generate(
        self, messages, stop=None, run_manager=None, **kwargs
    ) -> ChatResult:
        self.seen.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="好"))])


def _first_system_text(model: _Recorder) -> str:
    assert model.seen, "模型没被调用"
    first = model.seen[0][0]
    assert first.type == "system", f"首条不是 system 消息：{first.type}"
    return first.text


async def _run(
    *,
    state: TurnState | None = None,
    clarification: dict[str, Any] | None = None,
) -> _Recorder:
    model = _Recorder()
    agent = build_agent(
        model=model,
        backend=None,  # type: ignore[arg-type]
        tools=[],
        store=None,
        checkpointer=None,
        system_prompt=_SYSTEM_PROMPT,
        policy=_POLICY,
        subagents=None,
        skills=None,
    )
    configurable: dict[str, Any] = {
        # Kept in configurable for run bookkeeping, but it must not reach the prompt.
        "intent": "coding",
        "turn_state": state or TurnState(),
    }
    if clarification is not None:
        configurable["clarification"] = clarification
    await agent.ainvoke(
        {"messages": [{"role": "user", "content": "帮我做点什么"}]},
        config={"configurable": configurable},
    )
    return model


class TestTurnContextMiddleware:
    async def test_intent_label_is_not_injected(self) -> None:
        text = _first_system_text(await _run())

        assert _SYSTEM_PROMPT in text
        assert "本轮意图" not in text
        assert "coding（编码实施）" not in text

    async def test_no_time_block_is_injected(self) -> None:
        text = _first_system_text(await _run())

        assert "## 当前时间" not in text

    async def test_active_work_task_facts_are_injected(self) -> None:
        state = TurnState(
            work_task_payload={
                "task_id": 7,
                "status": "active",
                "current_summary": "实现模型协议适配",
                "requirement_summary": "接入新模型",
                "branch": "feat/model-adapter",
                "base_ref": "main",
                "latest_commit_sha": "a" * 40,
                "latest_head_sha": "a" * 40,
                "pull_request_id": None,
                "submissions": [],
            }
        )

        text = _first_system_text(await _run(state=state))

        assert "## 当前代码任务" in text
        assert "task_id：7" in text
        assert "feat/model-adapter" in text
        assert "实现模型协议适配" in text

    async def test_clarification_still_injects_the_ask_block(self) -> None:
        text = _first_system_text(
            await _run(
                clarification={
                    "confidence": 0.42,
                    "candidates": [["plan", 0.42], ["coding", 0.31]],
                }
            )
        )

        assert "本轮先澄清意图" in text
        assert "plan 0.42 / coding 0.31" in text
        assert "不要直接执行" in text


def test_work_task_block_contains_cumulative_summary_and_submissions() -> None:
    text = work_task_block(
        TurnState(
            work_task_payload={
                "task_id": 9,
                "status": "pr_open",
                "current_summary": "累计改动：接口、测试与文档",
                "requirement_summary": "实现接口",
                "branch": "feat/api",
                "base_ref": "main",
                "latest_commit_sha": "b" * 40,
                "latest_head_sha": "b" * 40,
                "pull_request_id": 12,
                "submissions": [
                    {
                        "commit_sha": "b" * 40,
                        "summary": "补齐测试",
                    }
                ],
            }
        )
    )

    assert "累计摘要：累计改动：接口、测试与文档" in text
    assert "PR：12" in text
    assert "bbbbbbbb 补齐测试" in text


def test_clarification_block_ranks_candidates_and_forbids_execution() -> None:
    text = clarification_block(
        {
            "confidence": 0.42,
            "candidates": [("plan", 0.42), ("coding", 0.31), ("qa", 0.27)],
        }
    )

    assert "判定置信度：0.42" in text
    assert "plan 0.42 / coding 0.31 / qa 0.27" in text
    assert "不要直接执行" in text
    assert "不要调用 handoffTask" in text
    assert "<CLARIFY>" in text
    assert '"steps"' in text
    assert '"value":"a"' in text
    assert "2~4 个选项" in text
    assert "qa / plan / coding" in text
    assert "rework" not in text


def test_no_clarification_means_no_block() -> None:
    assert clarification_block(None) == ""
