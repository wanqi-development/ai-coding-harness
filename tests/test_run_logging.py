"""运行日志归属：主智能体 / 子智能体按 ``lc_agent_name`` + ``namespace`` 分开。

不跑真 agent、不联网：按 ``astream_events(version="v3")`` 的真实事件形状（已用离线探针
取过）手工造事件，直接喂给 ``AgentRunLog``。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import ToolMessage

from ai_coding_harness.observability.logging import (
    _DATEFMT,
    _FORMAT,
    MAIN_AGENT,
    _TraceIdFilter,
)
from ai_coding_harness.observability.run_log import AgentRunLog, brief, result_text
from tests.v3_events import (
    MAIN_NS,
    SUB_NS,
    RunLogFeed,
    message_finish,
    tool_finish,
    tool_start,
)

_LOGGER = "ai_coding_harness.observability.run_log"

# 另一个子智能体的 ns：没被 on_message 登记过，认不出来就该退回主智能体
_OTHER_NS = ("tools:1a2b",)

_PLANNER_DESC = "去查一下仓库结构"


def _llm_end(ns: tuple[str, ...] = MAIN_NS, agent: str = "None") -> dict[str, Any]:
    return message_finish(ns=ns, agent=agent, usage={"input_tokens": 120, "output_tokens": 8})


def _tool_start(
    name: str, *, ns: tuple[str, ...] = MAIN_NS, call_id: str = "t1", **input_: Any
) -> dict[str, Any]:
    return tool_start(name, ns=ns, call_id=call_id, input=input_ or {"file_path": "/p/a.py"})


def _tool_end(
    *, ns: tuple[str, ...] = MAIN_NS, call_id: str = "t1", output: Any = "ok"
) -> dict[str, Any]:
    return tool_finish(ns=ns, call_id=call_id, output=output)


def _task_start(call_id: str = "task-1") -> dict[str, Any]:
    return tool_start(
        "task",
        call_id=call_id,
        input={"description": _PLANNER_DESC, "subagent_type": "planner"},
    )


def _task_end(call_id: str = "task-1", output: Any = "方案已定") -> dict[str, Any]:
    return tool_finish(call_id=call_id, output=output)


def _messages(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records]


class TestAttribution:
    """事件归谁：task 之前是主智能体，子智能体那一跳是它自己，收工后又回主智能体。"""

    def test_actor_switches_only_inside_task_ns(self, caplog):
        caplog.set_level(logging.INFO, logger=_LOGGER)
        feed = RunLogFeed(AgentRunLog(arg_limit=2000, result_limit=300))

        feed.llm(_llm_end())  # 派发前
        feed.start(_tool_start("read_file"))
        feed.start(_task_start())  # 委派 planner
        # planner 的模型调用：归属直接从 lc_agent_name 读出来，并顺手登记它的 ns
        feed.llm(_llm_end(SUB_NS, agent="planner"))
        # 它的工具事件不带 metadata，只能靠上一步登记的 ns 反查
        feed.start(_tool_start("write_file", ns=SUB_NS, call_id="t2"))
        feed.end(_tool_end(ns=SUB_NS, call_id="t2"))
        feed.end(_task_end())  # planner 收工
        feed.llm(_llm_end())  # 回到主智能体

        # 只看动作行：`工具返回` 是紧随其后的返回值摘要，归属与动作行一致，不必重复断言
        records = [r for r in caplog.records if not r.getMessage().startswith("工具返回 ←")]
        assert [r.agent for r in records] == [
            MAIN_AGENT,
            MAIN_AGENT,
            MAIN_AGENT,
            "planner",
            "planner",
            "planner",
            MAIN_AGENT,
            MAIN_AGENT,
        ]

    def test_events_without_metadata_are_main(self, caplog):
        """没有 metadata / 拿不到 ns 时退回主智能体，而不是崩掉。"""
        caplog.set_level(logging.INFO, logger=_LOGGER)
        log = AgentRunLog(arg_limit=2000, result_limit=300)
        log.on_tool_start(tool_start("read_file", input={}), "read_file")
        (record,) = caplog.records
        assert record.agent == MAIN_AGENT

    def test_actor_is_a_pure_query(self):
        """actor() 不该有副作用：tools 事件的归属只有 on_message 登记过才认得出。"""
        log = AgentRunLog(arg_limit=2000, result_limit=300)
        planner = _llm_end(SUB_NS, agent="planner")
        # messages 自带名字，本来就查得到
        assert log.actor(planner) == "planner"
        # tools 只有 namespace，还没登记过 → 退回主智能体
        sub_tool = _tool_start("write_file", ns=SUB_NS)
        assert log.actor(sub_tool) == MAIN_AGENT
        log.on_message(planner)
        assert log.actor(sub_tool) == "planner"
        # 换个没登记过的 ns 仍是主智能体（actor 自己不会顺手登记）
        assert log.actor(_tool_start("read_file", ns=_OTHER_NS)) == MAIN_AGENT

    def test_main_agent_name_placeholder_is_main(self):
        """主智能体的 lc_agent_name 是字符串 "None"（占位值），不能被当成名字。"""
        log = AgentRunLog(arg_limit=2000, result_limit=300)
        assert log.actor(_llm_end()) == MAIN_AGENT


class TestDelegationLines:
    """委派要单独成行，不能混在通用工具行里。"""

    def test_delegation_start_and_end(self, caplog):
        caplog.set_level(logging.INFO, logger=_LOGGER)
        feed = RunLogFeed(AgentRunLog(arg_limit=2000, result_limit=300))
        returned = "方案已定"
        feed.start(_task_start())
        feed.end(_task_end(output=returned))
        lines = _messages(caplog)
        assert f"委派子智能体 → planner：{_PLANNER_DESC}" in lines
        assert any(
            line.startswith("子智能体完成 ← planner ")
            and f"返回 {len(returned)} 字" in line
            for line in lines
        ), lines

    def test_task_does_not_use_generic_tool_lines(self, caplog):
        caplog.set_level(logging.INFO, logger=_LOGGER)
        feed = RunLogFeed(AgentRunLog(arg_limit=2000, result_limit=300))
        feed.start(_task_start())
        feed.end(_task_end())
        lines = _messages(caplog)
        assert not any(line.startswith("工具调用 → task") for line in lines), lines
        assert not any(line.startswith("工具完成 ← task") for line in lines), lines
        # 子智能体的返回值可能整篇是它的结论，全文入日志会把 agent.log 撑爆
        assert not any(line.startswith("工具返回 ← task") for line in lines), lines

    def test_delegation_arguments_are_tolerated_when_malformed(self, caplog):
        """模型给出畸形入参时也要留痕，不能因此抛异常打断对话。"""
        caplog.set_level(logging.INFO, logger=_LOGGER)
        feed = RunLogFeed(AgentRunLog(arg_limit=2000, result_limit=300))
        # 入参整个不是 dict（模型写成了字符串）
        feed.start(tool_start("task", call_id="task-9", input="planner"))
        # 是 dict 但缺 description / subagent_type
        feed.start(_tool_start("task", call_id="task-10"))
        assert _messages(caplog) == [
            "委派子智能体 → ?：planner",
            "委派子智能体 → ?：",
        ]


class TestBrief:
    def test_truncates_at_limit(self):
        assert brief("字" * 10, 4) == "字字字字…"

    def test_keeps_short_values_intact(self):
        assert brief("短", 4) == "短"

    def test_flattens_newlines(self):
        assert brief("一\n二", 4) == "一 二"

    def test_tool_args_honour_configured_limit(self, caplog):
        caplog.set_level(logging.INFO, logger=_LOGGER)
        feed = RunLogFeed(AgentRunLog(arg_limit=8, result_limit=300))
        feed.start(_tool_start("write_file", content="x" * 50))
        (record,) = caplog.records
        assert record.getMessage().endswith("…"), record.getMessage()


class TestToolResultLines:
    """工具的成败常只体现在返回值里（如记忆写入被后端拒绝），日志必须留痕。"""

    def test_failure_return_value_is_logged(self, caplog):
        caplog.set_level(logging.INFO, logger=_LOGGER)
        feed = RunLogFeed(AgentRunLog(arg_limit=2000, result_limit=300))
        feed.start(_tool_start("save_repo_memory"))
        feed.end(_tool_end(output="写入仓库记忆失败：已存在"))
        assert "工具返回 ← save_repo_memory：写入仓库记忆失败：已存在" in _messages(caplog)

    def test_tool_message_content_is_unwrapped(self, caplog):
        """事件里的 output 一般是 ToolMessage，直接 str() 得到的是 repr。"""
        caplog.set_level(logging.INFO, logger=_LOGGER)
        feed = RunLogFeed(AgentRunLog(arg_limit=2000, result_limit=300))
        feed.start(_tool_start("read_file"))
        feed.end(_tool_end(output=ToolMessage(content="文件正文", tool_call_id="c1")))
        assert "工具返回 ← read_file：文件正文" in _messages(caplog)

    def test_result_line_honours_configured_limit(self, caplog):
        caplog.set_level(logging.INFO, logger=_LOGGER)
        feed = RunLogFeed(AgentRunLog(arg_limit=2000, result_limit=5))
        feed.start(_tool_start("grep"))
        feed.end(_tool_end(output="字" * 20))
        assert "工具返回 ← grep：字字字字字…" in _messages(caplog)


class TestUsage:
    def test_usage_read_from_message_finish(self, caplog):
        caplog.set_level(logging.INFO, logger=_LOGGER)
        log = AgentRunLog(arg_limit=2000, result_limit=300)
        log.on_message(_llm_end())
        assert "LLM 调用完成：输入 120 / 输出 8 tokens" in _messages(caplog)

    def test_missing_usage_says_so(self, caplog):
        caplog.set_level(logging.INFO, logger=_LOGGER)
        log = AgentRunLog(arg_limit=2000, result_limit=300)
        log.on_message(message_finish())
        assert "LLM 调用完成：（无用量信息）" in _messages(caplog)

    def test_other_messages_events_do_not_log_usage(self, caplog):
        caplog.set_level(logging.INFO, logger=_LOGGER)
        log = AgentRunLog(arg_limit=2000, result_limit=300)
        log.on_message(_llm_end())  # 先登记一次
        caplog.clear()
        log.on_message(message_finish())  # 只有 message-finish 才记用量
        assert _messages(caplog) == ["LLM 调用完成：（无用量信息）"]


class TestInternalCallLines:
    """摘要那次调用单独署名 —— 它的 token 花销不能被记成主智能体的正文调用。

    正文那边由 ``stream`` 丢掉，日志这边**故意不丢**：一次十万输入的调用正是要能查账
    的，只是得看得出是谁花的。
    """

    def test_summary_call_gets_its_own_label(self, caplog):
        caplog.set_level(logging.INFO, logger=_LOGGER)
        log = AgentRunLog(arg_limit=2000, result_limit=300)
        log.on_message(
            message_finish(internal=True, usage={"input_tokens": 106752, "output_tokens": 3506})
        )
        assert _messages(caplog) == ["摘要调用完成：输入 106752 / 输出 3506 tokens"]

    def test_a_normal_call_keeps_the_plain_label(self, caplog):
        caplog.set_level(logging.INFO, logger=_LOGGER)
        log = AgentRunLog(arg_limit=2000, result_limit=300)
        log.on_message(_llm_end())
        assert _messages(caplog) == ["LLM 调用完成：输入 120 / 输出 8 tokens"]

    def test_the_actor_is_still_attributed(self, caplog):
        """换的是动作词，归属照旧从 ``lc_agent_name`` 来。"""
        caplog.set_level(logging.INFO, logger=_LOGGER)
        log = AgentRunLog(arg_limit=2000, result_limit=300)
        log.on_message(message_finish(ns=SUB_NS, agent="planner", internal=True))
        (record,) = caplog.records
        assert record.agent == "planner"


class TestResultText:
    def test_tool_message_yields_content(self):
        assert result_text(ToolMessage(content="abc", tool_call_id="c")) == "abc"

    def test_plain_values_fall_back_to_str(self):
        assert result_text(42) == "42"


class TestAgentField:
    """格式串里的 %(agent)s 必须有值，否则每条日志都会渲染失败。"""

    def test_filter_defaults_agent_to_dash(self):
        record = logging.LogRecord("n", logging.INFO, __file__, 1, "m", None, None)
        _TraceIdFilter().filter(record)
        assert record.agent == "-"

    def test_filter_keeps_explicit_agent(self):
        record = logging.LogRecord("n", logging.INFO, __file__, 1, "m", None, None)
        record.agent = "planner"
        _TraceIdFilter().filter(record)
        assert record.agent == "planner"

    def test_format_renders_agent(self):
        formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)
        record = logging.LogRecord("n", logging.INFO, __file__, 1, "m", None, None)
        _TraceIdFilter().filter(record)
        assert "[-]" in formatter.format(record)
        record.agent = "coder"
        assert "[coder]" in formatter.format(record)
