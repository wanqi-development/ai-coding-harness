"""意图指令：按 ``configurable.intent`` 注入到模型真正收到的系统消息。

不跑真 LLM：用一个只记录入参的假模型，直接检查它拿到什么。
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from ai_coding_harness.agent.graph import build_agent
from ai_coding_harness.agent.middleware.context_inject import clarification_block
from ai_coding_harness.agent.profiles import ORCHESTRATE_FORM
from ai_coding_harness.safety.policy import policy_for

_SYSTEM_PROMPT = "SYS-PROMPT-哨兵"
# 这组用例只看意图指令，边界取最宽的那个形态（不隐藏任何工具）以免混入别的变量
_POLICY = policy_for(ORCHESTRATE_FORM)


class _Recorder(BaseChatModel):
    """最小假模型：记录每次调用收到的消息，不做生成。"""

    seen: list[list[BaseMessage]] = Field(default_factory=list)

    def _get_ls_params(self, **kwargs: object) -> dict[str, str]:
        """报 ``openai`` provider —— 与线上一致，harness profile 才会关掉默认子智能体。"""
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
    intent: str | None,
    subagents: list[dict] | None = None,
    *,
    clarification: dict[str, Any] | None = None,
) -> _Recorder:
    model = _Recorder()
    agent = build_agent(
        model=model,
        backend=None,  # type: ignore[arg-type]  # 用 deepagents 默认后端
        tools=[],
        store=None,
        checkpointer=None,
        system_prompt=_SYSTEM_PROMPT,
        policy=_POLICY,
        subagents=subagents,
        skills=None,
    )
    configurable: dict[str, Any] = {} if intent is None else {"intent": intent}
    if clarification is not None:
        configurable["clarification"] = clarification
    config: dict[str, Any] = {"configurable": configurable}
    await agent.ainvoke(
        {"messages": [{"role": "user", "content": "帮我做点什么"}]}, config=config
    )
    return model


class TestIntentMiddleware:
    async def test_plan_intent_reaches_model(self):
        """plan 意图要把「你自己出方案、这轮派不了 task」送到模型手里（构建期 prompt 之外另加）。"""
        text = _first_system_text(await _run("plan"))
        assert _SYSTEM_PROMPT in text, "原有系统提示词被覆盖了"
        assert "你自己" in text and "派不了 `task`" in text, "plan 指令没注入"

    async def test_coding_intent_reaches_model(self):
        text = _first_system_text(await _run("coding"))
        assert "确认实施" in text and "coder" in text

    async def test_rework_intent_reaches_model(self):
        """PR 跟进轮要分流「只审查」与「要修改」，改码后还要复查。"""
        text = _first_system_text(await _run("rework"))
        assert "PR 跟进" in text
        assert "只审查 / 只回评" in text
        assert "要修改代码" in text
        assert "必须再派一次 `reviewer` 复查" in text

    async def test_analysis_is_merged_into_qa_and_has_no_directive(self):
        """analysis 不再是意图；旧值不会注入任何独立指令。"""
        text = _first_system_text(await _run("analysis"))
        assert "本轮意图" not in text

    async def test_qa_intent_adds_no_directive(self):
        """无指令的意图（qa）不塞意图指令，也不注入时间。"""
        text = _first_system_text(await _run("qa"))
        assert "本轮意图" not in text
        assert "## 当前时间" not in text

    async def test_missing_intent_adds_no_directive(self):
        text = _first_system_text(await _run(None))
        assert "本轮意图" not in text

    async def test_no_time_block_is_injected(self):
        """时间不再注入：记忆头行由平台写，散文日期让模型自己跑 date。"""
        for intent in ("plan", "coding", "rework", "qa", None):
            text = _first_system_text(await _run(intent))
            assert "## 当前时间" not in text

    async def test_directive_survives_subagent_middleware(self):
        """带子智能体时（它自己也会重写系统消息），两条内容都得在。"""
        subagents = [
            {
                "name": "planner",
                "description": "SUBAGENT-哨兵",
                "system_prompt": "你是 planner",
                "tools": [],
            }
        ]
        text = _first_system_text(await _run("plan", subagents=subagents))
        assert "SUBAGENT-哨兵" in text, "子智能体描述没进系统消息"
        assert "`task`" in text, "意图指令被子智能体中间件挤掉了"

    async def test_clarification_intent_injects_the_ask_block(self):
        text = _first_system_text(
            await _run(
                "qa",
                clarification={
                    "confidence": 0.42,
                    "candidates": [["plan", 0.42], ["coding", 0.31]],
                },
            )
        )

        assert "本轮先澄清意图" in text
        assert "plan 0.42 / coding 0.31" in text
        assert "不要直接执行" in text


def test_clarification_block_ranks_candidates_and_forbids_execution():
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
    assert '"options"' in text
    assert "qa / plan / coding / rework" in text


def test_no_clarification_means_no_block():
    assert clarification_block(None) == ""
