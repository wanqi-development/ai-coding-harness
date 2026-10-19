"""应急工具裁剪的纯函数测试：只保当前步骤，其余换占位符。

2026-10-17 起不再有常规剪裁中间件；这里测的是 ``pretrim_tool_results`` 与它依赖的
占位符 / 计数器。真正「到压缩线是否免压」的流程在 ``tests/test_summarization.py``。
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from ai_coding_harness.agent.middleware import (
    PRETRIM_KEEP_RECENT_TOOL_RESULTS,
    keep_items_from_messages,
    pretrim_tool_results,
)
from ai_coding_harness.agent.middleware import context_trim as trim_mod

#: 一段纯 ASCII 的代码样本（判「非中文那一档」用；刻意不带中文注释，否则比例会被带偏）
_CODE = "def f(x):\n    return x * 2\n\n\n" * 60


def _steps(count: int, *, chars: int = 4000) -> list[BaseMessage]:
    """造 ``count`` 轮「模型调一次工具 → 工具返回一大段」的历史。"""
    messages: list[BaseMessage] = []
    for index in range(count):
        call_id = f"c{index}"
        messages.append(
            AIMessage(
                content="",
                tool_calls=[
                    {"id": call_id, "name": "read_file", "args": {"path": f"/p/{index}.java"}}
                ],
            )
        )
        messages.append(
            ToolMessage(content="甲" * chars, tool_call_id=call_id, name="read_file")
        )
    return messages


def _current_step(*, chars: int = 500) -> list[BaseMessage]:
    """末尾这一步：模型刚发起一个工具调用，结果还没被任何裁剪动过。"""
    return [
        AIMessage(
            content="",
            tool_calls=[
                {"id": "now", "name": "read_file", "args": {"path": "/p/now.java"}}
            ],
        ),
        ToolMessage(
            content="当前结果" + "甲" * chars, tool_call_id="now", name="read_file"
        ),
    ]


class TestTheCounter:
    def test_the_counter_uses_the_chinese_rate_on_chinese(self):
        """纯中文按 1.6 字符/token 估 —— 上游默认 4.0 是英文口径，会低估约 2.5 倍。"""
        messages = _steps(1)

        assert trim_mod.effective_chars_per_token(messages) == pytest.approx(
            trim_mod.CJK_CHARS_PER_TOKEN, abs=0.05
        )
        assert (
            trim_mod.context_token_counter(messages)
            > count_tokens_approximately(messages) * 2
        )

    def test_the_counter_uses_the_latin_rate_on_code(self):
        """纯代码 / 英文按 3.8 估，**不再**套中文那把尺子。"""
        messages = [HumanMessage(content=_CODE)]

        assert trim_mod.effective_chars_per_token(messages) == pytest.approx(
            trim_mod.LATIN_CHARS_PER_TOKEN, abs=0.05
        )
        ours = trim_mod.context_token_counter(messages)
        upstream_default = count_tokens_approximately(messages)
        assert ours < upstream_default * 1.2, "代码不该再被高估 2.5 倍"

    def test_the_rate_follows_the_actual_mix(self):
        """中英各半时系数落在两档之间 —— 这就是「按比例折一个等效值」的意义。"""
        eff = trim_mod.effective_chars_per_token(
            [HumanMessage(content="甲" * 1000 + "a" * 1000)]
        )

        assert trim_mod.CJK_CHARS_PER_TOKEN < eff < trim_mod.LATIN_CHARS_PER_TOKEN

    def test_the_rate_falls_back_to_latin_when_there_is_no_text(self):
        """没文本可判（空消息 / 纯图片）时走非中文那一档：宁可估少，不要凭空按中文放大。"""
        assert trim_mod.effective_chars_per_token([]) == pytest.approx(
            trim_mod.LATIN_CHARS_PER_TOKEN
        )


class TestThePlaceholder:
    def test_it_names_the_tool_the_target_and_the_size(self):
        text = trim_mod.trimmed_placeholder("read_file", "/p/a.java", 12_480)

        assert "read_file" in text
        assert "/p/a.java" in text
        assert "12,480 字" in text

    def test_it_does_not_invite_a_reread(self):
        text = trim_mod.trimmed_placeholder("read_file", "/p/a.java", 100)

        assert "重新读一次" not in text
        assert "你已经读过它" in text, "明说它读过 —— 止住重读靠的就是这一句"

    def test_the_target_is_one_line_and_capped(self):
        """``execute`` 的命令可能是几百字多行的 shell：占位符必须是一行、且封顶。"""
        flat = trim_mod._target_of({"command": "cat a.java\n" * 40})

        assert "\n" not in flat
        assert len(flat) <= trim_mod._TARGET_LIMIT + 1
        assert trim_mod._target_of({"pattern": "JwtUtils"}) == "JwtUtils"
        assert trim_mod._target_of({"file_path": "/p/a.java", "offset": 96}) == "/p/a.java"
        assert trim_mod._target_of(None) == "（无入参）"

    def test_a_long_path_keeps_its_filename(self):
        """路径按**尾部**留：认得出一个文件靠的是文件名与它上面那一两层目录。"""
        deep = "/ai_coding/project/wanqi-mall/" + "mall-auth-service/" * 5 + "AuthServiceImpl.java"
        assert len(deep) > trim_mod._TARGET_LIMIT, "样本得真比上限长，否则这条断言是空的"

        target = trim_mod._target_of({"file_path": deep})

        assert target.endswith("AuthServiceImpl.java"), "文件名必须在"
        assert target.startswith("…/"), "丢掉的是前缀那一段"
        assert len(target) <= trim_mod._TARGET_LIMIT
        assert trim_mod._shorten_path("/p/a.java", 60) == "/p/a.java", "没超长就原样留着"

    def test_a_path_of_one_huge_segment_still_fits(self):
        """整条路径就一段、且比上限还长：退化成「尾部若干字」，一样封顶、一样留住后缀。"""
        got = trim_mod._shorten_path("x" * 100 + ".java", 20)

        assert len(got) == 20
        assert got.startswith("…")
        assert got.endswith(".java")


class TestPreSummaryTrim:
    """压缩前的应急裁剪：保留当前步骤 + KEEP 清单，不是按条数留最近 N 条。"""

    def test_only_the_current_step_survives(self):
        messages = _steps(6) + _current_step()

        got, cleared = pretrim_tool_results(messages)

        assert cleared == 6
        assert len(got) == len(messages), "消息条数不变：只换内容，不删"
        tools = [m for m in got if isinstance(m, ToolMessage)]
        assert tools[-1].content.startswith("当前结果"), "当前这一步原样保留"
        assert all(m.content.startswith("〔已裁剪〕") for m in tools[:-1])
        assert [m.tool_call_id for m in tools] == [f"c{i}" for i in range(6)] + ["now"]
        assert "read_file" in tools[0].content and "/p/0.java" in tools[0].content

    def test_keep_recent_is_an_escape_hatch(self):
        messages = _steps(6) + _current_step()

        got, cleared = pretrim_tool_results(messages, keep_recent=2)

        tools = [m for m in got if isinstance(m, ToolMessage)]
        assert cleared == 4
        assert all(not m.content.startswith("〔已裁剪〕") for m in tools[-3:]), (
            "当前步骤 + 最近 2 条原样"
        )
        assert all(m.content.startswith("〔已裁剪〕") for m in tools[:-3])

    def test_keep_tools_are_never_cleared(self):
        messages = _steps(4)
        messages.append(
            AIMessage(
                content="",
                tool_calls=[{"id": "t0", "name": "write_todos", "args": {}}],
            )
        )
        messages.append(ToolMessage(content="清单已更新", tool_call_id="t0", name="write_todos"))
        messages += _current_step()

        got, _ = pretrim_tool_results(messages)

        todo = next(m for m in got if isinstance(m, ToolMessage) and m.name == "write_todos")
        assert todo.content == "清单已更新"

    def test_running_it_twice_does_not_double_count(self):
        messages = _steps(4) + _current_step()

        once, first = pretrim_tool_results(messages)
        twice, second = pretrim_tool_results(once)

        assert first == 4
        assert second == 0
        assert [m.content for m in twice] == [m.content for m in once]

    def test_clearing_inputs_clears_the_originating_call(self):
        messages = _steps(3) + _current_step()

        got, _ = pretrim_tool_results(messages, clear_tool_inputs=True)

        calls = [
            call
            for m in got
            if isinstance(m, AIMessage)
            for call in m.tool_calls or []
        ]
        assert calls[0]["args"] == {}, "老调用的入参被清空"
        assert calls[-1]["args"] == {"path": "/p/now.java"}, "当前这一步不动"
        first_ai = next(m for m in got if isinstance(m, AIMessage))
        assert first_ai.response_metadata["context_editing"]["cleared_tool_inputs"] == ["c0"]

    def test_a_history_without_an_ai_message_still_keeps_the_tail(self):
        messages = [
            HumanMessage(content="问"),
            ToolMessage(content="老结果", tool_call_id="old", name="read_file"),
            ToolMessage(content="当前结果", tool_call_id="now", name="read_file"),
        ]

        got, cleared = pretrim_tool_results(messages)

        assert cleared == 0, "认不出哪条是老的，就只保末尾，别误剪"
        assert [m.content for m in got] == ["问", "老结果", "当前结果"]

    def test_an_empty_list_is_harmless(self):
        assert pretrim_tool_results([]) == ([], 0)

    def test_the_default_keeps_no_extra_result(self):
        assert PRETRIM_KEEP_RECENT_TOOL_RESULTS == 0

    def test_keeps_the_tool_result_named_by_the_working_note(self):
        messages = _steps(3) + _current_step()

        got, cleared = pretrim_tool_results(messages, keep_items=("c1",))

        old = {
            m.tool_call_id: m.content
            for m in got
            if isinstance(m, ToolMessage)
        }
        assert old["c1"] == "甲" * 4000, "KEEP 点名的结果保留原文"
        assert old["c0"].startswith("〔已裁剪〕"), "没点名 / 没在本步的结果照旧剪"
        assert old["now"].startswith("当前结果")
        assert cleared == 2

    def test_keeps_by_tool_and_target_when_the_id_is_unknown(self):
        messages = _steps(2) + _current_step()

        got, _ = pretrim_tool_results(
            messages, keep_items=("read_file:/p/1.java",)
        )

        old = {m.tool_call_id: m.content for m in got if isinstance(m, ToolMessage)}
        assert old["c1"] == "甲" * 4000
        assert old["c0"].startswith("〔已裁剪〕")

    def test_a_subagent_context_has_no_last_human_round_boundary(self):
        """子智能体只有一条任务 HumanMessage；裁剪不能被它当成“整轮都留”。"""
        messages = [HumanMessage(content="审查 PR #15"), *_steps(3), *_current_step()]

        got, cleared = pretrim_tool_results(messages)

        assert got[0].content == "审查 PR #15", "任务描述本身不是工具返回，永远保留"
        tools = [m for m in got if isinstance(m, ToolMessage)]
        assert all(m.content.startswith("〔已裁剪〕") for m in tools[:-1])
        assert tools[-1].content.startswith("当前结果")
        assert cleared == 3

    def test_reads_the_latest_working_note(self):
        messages = [
            AIMessage(content="<working-note>\nKEEP: c1, read_file:/p/2.java\n</working-note>"),
            AIMessage(content="later"),
        ]

        assert keep_items_from_messages(messages) == ("c1", "read_file:/p/2.java")

    def test_old_notes_are_not_treated_as_current_need(self):
        messages = [
            AIMessage(content="KEEP: c0"),
            HumanMessage(content="新任务"),
            AIMessage(content="x"),
            AIMessage(content="y"),
            AIMessage(content="z"),
        ]

        assert keep_items_from_messages(messages) == ()
