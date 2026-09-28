"""``stream.py``：v3 事件的拆封、write_todos 参数串的抠取、正文与待办树的拼装。

事件形状见 ``tests/v3_events.py``（照探针实测）。
"""

from __future__ import annotations

from ai_coding_harness.stream import (
    ToolNames,
    V3Translator,
    call_id_of,
    delegation_id,
    event_name,
    finished_items,
    is_internal_call,
    metadata_of,
    namespace_of,
    payload_of,
    todo_items,
)
from tests.v3_events import (
    MAIN_NS,
    SUB_NS,
    message_finish,
    message_start,
    messages,
    reasoning,
    sub_ns,
    todo_delta,
    todo_finish,
    todo_start,
    tool_finish,
    tool_start,
)
from tests.v3_events import (
    text as text_delta,
)


def T(content: str, status: str = "pending") -> dict[str, str]:
    """待办项的简写，免得每处都写全 —— 断言里一屏能放下。"""
    return {"content": content, "status": status}


# -- 拆封 -------------------------------------------------------------------


def test_payload_of_splits_tuple_but_passes_others() -> None:
    assert payload_of(message_start())["event"] == "message-start"
    # tools 通道的 data 是单值，不能被当成二元组切一刀
    assert payload_of(tool_start("task"))["tool_name"] == "task"


def test_metadata_and_namespace() -> None:
    assert metadata_of(message_start())["lc_agent_name"] == "None"
    assert metadata_of(tool_start("task")) == {}
    assert namespace_of(message_start()) == MAIN_NS
    assert namespace_of(message_start(ns=SUB_NS)) == SUB_NS


def test_event_name_follows_the_channel() -> None:
    assert event_name(messages("content-block-delta")) == "content-block-delta"
    assert event_name(tool_finish()) == "tool-finished"


def test_call_id_of_pairs_the_two_frames() -> None:
    """起止两帧靠它配对（服务层也拿它查「这一枪派给了谁」）。"""
    assert call_id_of(tool_start("task", call_id="c1")) == "c1"
    assert call_id_of(tool_finish(call_id="c1")) == "c1"
    # 非工具事件没有这个字段，给空串而不是抛
    assert call_id_of({"method": "values", "params": {"data": {}}}) == ""
    assert call_id_of(tool_start("task", call_id="")) == ""


def test_delegation_id_only_recognizes_one_level() -> None:
    """只认一级子智能体：二级嵌套没有对应的清单位置，返回空串让调用方丢掉。"""
    assert delegation_id(sub_ns("c1")) == "c1"
    assert delegation_id(()) == ""
    assert delegation_id(("tools:c1", "tools:c2")) == ""
    assert delegation_id(("memories",)) == ""


# -- todo_items -------------------------------------------------------------


def test_items_plain_and_multiple() -> None:
    args = (
        '{"todos":[{"content":"读代码","status":"pending"},'
        '{"content":"写补丁","status":"in_progress"}]}'
    )
    assert todo_items(args) == [T("读代码"), T("写补丁", "in_progress")]


def test_items_decodes_unicode_escapes() -> None:
    # langchain 序列化时 ensure_ascii，中文到这儿是 \uXXXX 形态
    args = '{"todos": [{"content": "\\u8bfb\\u4ee3\\u7801", "status": "pending"}]}'
    assert todo_items(args) == [T("读代码")]


def test_items_decodes_escaped_quote_and_backslash() -> None:
    assert todo_items('{"content":"他说:\\"改完了\\""}') == [T('他说:"改完了"')]
    assert todo_items('{"content":"a\\\\b"}') == [T("a\\b")]


def test_items_drops_value_that_is_still_open() -> None:
    # 流到一半：第二项的引号还没闭合，先不显示，免得逐字抖动
    args = '{"todos":[{"content":"读代码","status":"pending"},{"content":"写补'
    assert todo_items(args) == [T("读代码")]


def test_items_defaults_to_pending_before_the_status_streams_in() -> None:
    """status 排在 content 后面，一帧里 content 到了 status 还没到是常态。

    此时先按 pending 占位（收尾帧会订正）；流了一半的 ``"pend`` 同样不认。
    """
    assert todo_items('{"todos":[{"content":"读代码"}') == [T("读代码")]
    assert todo_items('{"todos":[{"content":"读代码","status":"pend') == [T("读代码")]


def test_items_rejects_a_status_outside_the_three() -> None:
    assert todo_items('{"content":"a","status":"done"}') == [T("a")]


def test_items_empty_and_unrelated() -> None:
    assert todo_items("") == []
    assert todo_items('{"todos":[') == []
    assert todo_items('{"path":"src/main.py"}') == []


# -- finished_items ---------------------------------------------------------


def test_finished_items_reads_parsed_args() -> None:
    block = {
        "type": "tool_call",
        "args": {
            "todos": [
                {"content": "读代码", "status": "completed"},
                {"content": "写补丁", "status": "in_progress"},
            ]
        },
    }
    assert finished_items(block) == [T("读代码", "completed"), T("写补丁", "in_progress")]


def test_finished_items_tolerates_odd_shapes() -> None:
    assert finished_items({"args": '{"todos":[]}'}) is None  # 没解析成 dict 就不认
    assert finished_items({"args": {"todos": "x"}}) is None
    assert finished_items({"args": {"todos": [{"content": None}, "坏项"]}}) == [T("")]
    assert finished_items(None) is None


# -- ToolNames --------------------------------------------------------------


def test_tool_names_pairs_started_with_finished() -> None:
    names = ToolNames()
    assert names.remember(tool_start("write_todos", call_id="c9")) == "write_todos"
    assert names.resolve(tool_finish(call_id="c9")) == "write_todos"
    # 一次调用只收尾一次
    assert names.resolve(tool_finish(call_id="c9")) is None


def test_tool_names_ignores_unknown_id() -> None:
    names = ToolNames()
    names.remember(tool_start("task", call_id="c1"))
    assert names.resolve(tool_finish(call_id="c2")) is None
    assert names.resolve(tool_finish(call_id="c1")) == "task"


def test_tool_names_without_name_is_not_remembered() -> None:
    names = ToolNames()
    assert names.remember(tool_finish()) is None
    assert names.resolve(tool_finish()) is None


# -- V3Translator -----------------------------------------------------------


def test_text_delta_becomes_ai_part() -> None:
    tr = V3Translator()
    assert tr.feed(text_delta("我先列")) == [{"type": "ai", "content": "我先列"}]
    assert tr.feed(text_delta("个清单。")) == [{"type": "ai", "content": "个清单。"}]
    # 相邻正文并成一块
    assert tr.parts.data == [{"kind": "text", "content": "我先列个清单。"}]
    assert tr.parts.has_todos is False


def test_subagent_events_are_dropped() -> None:
    """子智能体的正文是内部编排；它的清单还没有落点（主清单都还没有）也一并丢掉。"""
    tr = V3Translator()
    assert tr.feed(text_delta("子智能体的话", ns=SUB_NS, agent="coder")) == []
    assert tr.feed(todo_start(ns=SUB_NS, agent="coder")) == []
    assert tr.parts.data == []
    assert tr.parts.has_todos is False


def test_reasoning_delta_is_not_text() -> None:
    tr = V3Translator()
    assert tr.feed(reasoning("（思考）")) == []
    assert tr.parts.data == []


class TestInternalModelCallsAreNotTheAnswer:
    """中间件自己的模型调用（摘要）整条不进答复。

    deepagents 的摘要中间件把活转包给内层 langchain 实现，那次调用**带**
    ``lc_internal_call`` 标记，但 deepagents 那个类没像 langchain 的
    ``SummarizationMiddleware`` 那样声明 ``transformers = (InternalCallTransformer,)``，
    没人把事件丢掉 —— 摘要正文就这么混进过助手正文（2026-09-21 实见）。这里钉住我们
    自己的那道过滤。
    """

    def test_all_three_frames_of_an_internal_call_produce_nothing(self) -> None:
        tr = V3Translator()
        assert tr.feed(message_start(internal=True)) == []
        assert tr.feed(text_delta("## SESSION INTENT", internal=True)) == []
        assert tr.feed(message_finish(internal=True)) == []
        assert tr.parts.data == []
        assert tr.parts.has_todos is False

    def test_an_internal_call_between_two_paragraphs_does_not_split_them(self) -> None:
        """夹在中间的内部调用要被**跳过**，而不是把正文切成两块。"""
        tr = V3Translator()
        assert tr.feed(text_delta("正文一")) == [{"type": "ai", "content": "正文一"}]
        assert tr.feed(text_delta("摘要正文", internal=True)) == []
        assert tr.feed(text_delta("正文二")) == [{"type": "ai", "content": "正文二"}]
        assert tr.parts.data == [{"kind": "text", "content": "正文一正文二"}]

    def test_a_normal_call_still_streams(self) -> None:
        """没有标记的一律照常 —— 过滤只看键在不在，别把普通调用也吞了。"""
        tr = V3Translator()
        assert tr.feed(message_start()) == []
        assert tr.feed(text_delta("正常答复")) == [{"type": "ai", "content": "正常答复"}]
        assert tr.parts.data == [{"kind": "text", "content": "正常答复"}]

    def test_a_tool_event_is_never_internal(self) -> None:
        """``tools`` 通道的 ``data`` 是单值，没有 metadata 可读，判定必须为假。"""
        assert is_internal_call(tool_start("read_file")) is False
        assert is_internal_call(tool_finish()) is False

    def test_the_predicate_follows_the_metadata_key(self) -> None:
        assert is_internal_call(text_delta("话")) is False
        assert is_internal_call(text_delta("话", internal=True)) is True


def test_todos_emitted_progressively_then_authoritative_on_finish() -> None:
    tr = V3Translator()
    assert tr.feed(todo_start()) == []
    # 半截：值没闭合，先什么都不给
    assert tr.feed(todo_delta('{"todos":[{"content":"读代')) == [
        {"type": "todos", "items": []}
    ]
    assert tr.feed(todo_delta('{"todos":[{"content":"读代码","status":"pending"}]}')) == [
        {"type": "todos", "items": [T("读代码")]}
    ]
    # 收尾那份是已解析的 dict，以它为准（这里故意与流中的不同：多一项、状态也变了）
    assert tr.feed(
        todo_finish([{"content": "读代码", "status": "completed"}, {"content": "写补丁"}])
    ) == [{"type": "todos", "items": [T("读代码", "completed"), T("写补丁")]}]
    assert tr.parts.data == [
        {"kind": "todos", "items": [T("读代码", "completed"), T("写补丁")]}
    ]
    assert tr.parts.has_todos is True


def test_todos_args_is_accumulated_not_appended() -> None:
    """``fields.args`` 每片都是**累加值**，当成增量累加会把前几项重复捞出来。"""
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(todo_delta('{"todos":[{"content":"a","status":"pending"'))
    out = tr.feed(
        todo_delta('{"todos":[{"content":"a","status":"pending"},{"content":"b","status":"pending"}]}')
    )
    assert out == [{"type": "todos", "items": [T("a"), T("b")]}]


def test_repeated_calls_replace_the_single_tree() -> None:
    """模型每次给的都是全量清单：树只有一份，整体覆盖，不追加一串过时快照。"""
    tr = V3Translator()
    tr.feed(text_delta("开工。"))
    tr.feed(todo_start(index=1))
    tr.feed(todo_finish([{"content": "第一步", "status": "completed"}], index=1))
    tr.feed(text_delta("第一步做完了。"))
    # 第二次调用：block 下标又是 1，但这次是整棵替换
    tr.feed(todo_start(index=1))
    out = tr.feed(
        todo_finish(
            [{"content": "第一步", "status": "completed"}, {"content": "第二步"}], index=1
        )
    )
    assert out == [
        {"type": "todos", "items": [T("第一步", "completed"), T("第二步")]}
    ]
    # 两次正文隔着一次 write_todos，仍然并成一块（中间没别的块了）
    assert tr.parts.data == [
        {"kind": "text", "content": "开工。第一步做完了。"},
        {"kind": "todos", "items": [T("第一步", "completed"), T("第二步")]},
    ]


def test_tree_is_last_even_when_text_follows_it() -> None:
    """树表示「现在什么状态」，不是「说到哪儿了」—— 后到的正文不许把它挤到中间。"""
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(todo_finish([{"content": "读代码", "status": "completed"}]))
    tr.feed(text_delta("读完了。"))
    assert [p["kind"] for p in tr.parts.data] == ["text", "todos"]


def test_subagent_todos_nest_under_the_running_item() -> None:
    """子智能体的 write_todos 挂到**委派时主清单里 in_progress 的那一项**下。"""
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(
        todo_finish(
            [
                {"content": "看仓库", "status": "completed"},
                {"content": "改代码", "status": "in_progress"},
                {"content": "提交", "status": "pending"},
            ]
        )
    )
    # 主智能体此刻在做「改代码」，派发出去的子智能体就挂它下面
    tr.note_delegation(tool_start("task", call_id="t1"))
    ns = sub_ns("t1")
    assert tr.feed(text_delta("Let me look.", ns=ns, agent="coder")) == []
    assert tr.feed(todo_start(ns=ns, agent="coder")) == []
    out = tr.feed(
        todo_finish([{"content": "读 diff"}, {"content": "打补丁"}], ns=ns, agent="coder")
    )
    children = [T("读 diff"), T("打补丁")]
    assert out == [
        {
            "type": "todos",
            "items": [
                T("看仓库", "completed"),
                {**T("改代码", "in_progress"), "children": children},
                T("提交"),
            ],
        }
    ]
    assert tr.parts.data[-1]["items"][1]["children"] == children


def test_children_survive_a_main_list_replacement() -> None:
    """主清单每帧整体替换，子任务按文本认亲 —— 否则主智能体一报进度子树就没了。"""
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(todo_finish([{"content": "改代码", "status": "in_progress"}]))
    tr.note_delegation(tool_start("task", call_id="t1"))
    tr.feed(todo_start(ns=sub_ns("t1"), agent="coder"))
    tr.feed(todo_finish([{"content": "读 diff"}], ns=sub_ns("t1"), agent="coder"))

    out = tr.feed(todo_finish([{"content": "改代码", "status": "completed"}]))
    assert out[0]["items"] == [
        {**T("改代码", "completed"), "children": [T("读 diff")]}
    ]


def test_children_stay_attached_when_the_parent_title_gains_a_suffix() -> None:
    """主控爱把这一项的结果追加进文字（`实施…提 PR` → `实施…提 PR（PR #9）`）。

    2026-09-27 实见：planner / coder 的子清单在跑的时候还挂在父项下，等主控更新完清单
    就掉到树尾（用户看到的是「跑完就漂移」）—— 逐字比认不出是同一项。
    """
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(todo_finish([{"content": "实施用户资料编辑代码并提 PR", "status": "in_progress"}]))
    tr.note_delegation(tool_start("task", call_id="t1", input={"subagent_type": "coder"}))
    ns = sub_ns("t1")
    tr.feed(todo_start(ns=ns, agent="coder"))
    tr.feed(todo_finish([T("写码")], ns=ns, agent="coder"))

    out = tr.feed(
        todo_finish([{"content": "实施用户资料编辑代码并提 PR（PR #9）", "status": "completed"}])
    )
    items = out[0]["items"]
    assert len(items) == 1, "改了文字不许多出容器项"
    assert items[0]["children"] == [T("写码")]

    # 再改一次后缀（挂靠已经锚在那一项上）也照样认得出
    out = tr.feed(
        todo_finish(
            [{"content": "实施用户资料编辑代码并提 PR（PR #9，已审查）", "status": "completed"}]
        )
    )
    assert out[0]["items"][0]["children"] == [T("写码")]


def test_a_longer_title_still_matches_by_prefix() -> None:
    """改写不加括号、直接续写（`…提 PR` → `…提 PR并已审查`）时按前缀认亲。"""
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(todo_finish([{"content": "实施用户资料编辑代码并提 PR", "status": "in_progress"}]))
    tr.note_delegation(tool_start("task", call_id="t1", input={"subagent_type": "coder"}))
    ns = sub_ns("t1")
    tr.feed(todo_start(ns=ns, agent="coder"))
    tr.feed(todo_finish([T("写码")], ns=ns, agent="coder"))

    out = tr.feed(
        todo_finish([{"content": "实施用户资料编辑代码并提 PR并已审查", "status": "completed"}])
    )
    assert out[0]["items"][0]["children"] == [T("写码")]


def test_the_attachment_survives_a_full_rewrite_of_the_parent_title() -> None:
    """挂上之后锚定那一项：标题被整句改写、清单条数没变时沿用原位，不漂到树尾。"""
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(todo_finish([{"content": "实施用户资料编辑代码并提 PR", "status": "in_progress"}]))
    tr.note_delegation(tool_start("task", call_id="t1", input={"subagent_type": "coder"}))
    ns = sub_ns("t1")
    tr.feed(todo_start(ns=ns, agent="coder"))
    tr.feed(todo_finish([T("写码")], ns=ns, agent="coder"))

    out = tr.feed(todo_finish([{"content": "编码：用户资料编辑（PR #9）", "status": "completed"}]))
    items = out[0]["items"]
    assert len(items) == 1, "整句改写后仍挂原项，不许多出容器"
    assert items[0]["children"] == [T("写码")]


def test_an_ambiguous_title_does_not_get_guessed() -> None:
    """两串归一到同一个键时不硬认：宁可落树尾容器，也不能挂到别的父项下面。"""
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(todo_finish([{"content": "实施用户资料编辑代码并提 PR", "status": "in_progress"}]))
    tr.note_delegation(tool_start("task", call_id="t1", input={"subagent_type": "coder"}))
    tr.feed(
        todo_finish(
            [
                {"content": "实施用户资料编辑代码并提 PR（PR #9）", "status": "completed"},
                {"content": "实施用户资料编辑代码并提 PR（PR #8）", "status": "completed"},
            ]
        )
    )
    ns = sub_ns("t1")
    tr.feed(todo_start(ns=ns, agent="coder"))
    items = tr.feed(todo_finish([T("写码")], ns=ns, agent="coder"))[0]["items"]
    assert items[0].get("children") is None
    assert items[-1]["content"] == "编码子任务"


def test_a_short_title_is_not_prefix_matched() -> None:
    """短标题不走前缀认亲：`改代码` 与 `改代码并提交` 可能是两件事，宁可落容器。"""
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(todo_finish([{"content": "改代码", "status": "in_progress"}]))
    tr.note_delegation(tool_start("task", call_id="t1", input={"subagent_type": "coder"}))
    tr.feed(todo_finish([{"content": "改代码并提交", "status": "in_progress"}]))
    ns = sub_ns("t1")
    tr.feed(todo_start(ns=ns, agent="coder"))
    items = tr.feed(todo_finish([T("写码")], ns=ns, agent="coder"))[0]["items"]
    assert items[0].get("children") is None
    assert items[-1]["content"] == "编码子任务"


def test_falls_back_to_the_last_unfinished_item() -> None:
    """没有 in_progress（模型没按规范更新状态）时，退到最后一个未完成的项。"""
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(
        todo_finish([{"content": "第一步", "status": "completed"}, {"content": "第二步"}])
    )
    tr.note_delegation(tool_start("task", call_id="t1"))
    tr.feed(todo_start(ns=sub_ns("t1"), agent="coder"))
    out = tr.feed(todo_finish([{"content": "子"}], ns=sub_ns("t1"), agent="coder"))
    assert out[0]["items"][1]["children"] == [T("子")]


def test_child_todos_without_a_parent_get_a_container() -> None:
    """主清单为空（本轮压根没建）时不再整份丢弃：树尾补容器项兜住子清单。"""
    tr = V3Translator()
    tr.note_delegation(tool_start("task", call_id="t1", input={"subagent_type": "coder"}))
    ns = sub_ns("t1")
    tr.feed(todo_start(ns=ns, agent="coder"))
    out = tr.feed(
        todo_finish([T("写码"), T("测试")], ns=ns, agent="coder")
    )
    assert out == [
        {
            "type": "todos",
            "items": [
                {
                    "content": "编码子任务",
                    "status": "pending",
                    "children": [T("写码"), T("测试")],
                }
            ],
        }
    ]
    # 前端与历史回放吃的是 parts，容器也要落进那一份
    assert tr.parts.data[-1]["items"] == out[0]["items"]
    assert tr.parts.has_todos is True


def test_container_name_falls_back_when_task_args_are_unusable() -> None:
    """两处都取不到名字时才用兜底名，容器不能没名字。

    名字有两个来源：``task`` 入参的 ``subagent_type``，与子智能体自报的
    ``lc_agent_name``（见 ``_note_child_agent``）。这里把**两个**都弄坏 —— 入参不是 dict、
    子事件也不带名字（``agent="None"`` 是主控那份占位值）。
    """
    tr = V3Translator()
    tr.note_delegation(tool_start("task", call_id="t1", input="not-a-dict"))
    ns = sub_ns("t1")
    tr.feed(todo_start(ns=ns, agent="None"))
    out = tr.feed(todo_finish([T("子")], ns=ns, agent="None"))
    assert out[0]["items"][0]["content"] == "子智能体任务"


def test_the_child_name_comes_from_the_child_itself() -> None:
    """真机上两套 id 对不上（namespace 是 langgraph 的 task id，``task`` 入参那串是
    ``call_xxx``），所以名字必须从**子智能体自报的** ``lc_agent_name`` 取 —— 它和子清单
    用的是同一个 namespace 键，天然对得上。"""
    tr = V3Translator()
    # 委派那帧的 call_id 与子智能体的 namespace id **故意不同**（真机就是这样）
    tr.note_delegation(tool_start("task", call_id="call_abc", input={"subagent_type": "coder"}))
    ns = sub_ns("uuid-1234")

    tr.feed(todo_start(ns=ns, agent="coder"))
    out = tr.feed(todo_finish([T("写码")], ns=ns, agent="coder"))

    assert out[0]["items"][0]["content"] == "编码子任务"


def test_the_child_list_attaches_even_when_the_ids_differ() -> None:
    """挂靠也不再依赖两套 id 相等：按角色认领委派（同角色 FIFO），子清单挂到
    「派它的那一项」下面，树尾不再冒出容器。"""
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(
        todo_finish(
            [
                {"content": "看仓库", "status": "completed"},
                {"content": "改代码", "status": "in_progress"},
            ]
        )
    )
    tr.note_delegation(tool_start("task", call_id="call_abc", input={"subagent_type": "coder"}))
    ns = sub_ns("uuid-1234")

    tr.feed(todo_start(ns=ns, agent="coder"))
    out = tr.feed(todo_finish([T("读 diff")], ns=ns, agent="coder"))

    assert out[0]["items"] == [
        T("看仓库", "completed"),
        {**T("改代码", "in_progress"), "children": [T("读 diff")]},
    ], "子清单挂在委派时 in_progress 的那一项下，且没有多出容器"


def test_two_delegations_of_the_same_role_are_claimed_in_order() -> None:
    """同一角色的两次委派按先后认领（FIFO）：第一次派去的那份挂第一项，第二次挂第二项。"""
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(todo_finish([{"content": "第一步", "status": "in_progress"}]))
    tr.note_delegation(tool_start("task", call_id="call_1", input={"subagent_type": "coder"}))
    ns1 = sub_ns("uuid-1")
    tr.feed(todo_start(ns=ns1, agent="coder"))
    tr.feed(todo_finish([T("子一")], ns=ns1, agent="coder"))

    tr.feed(
        todo_finish(
            [
                {"content": "第一步", "status": "completed"},
                {"content": "第二步", "status": "in_progress"},
            ]
        )
    )
    tr.note_delegation(tool_start("task", call_id="call_2", input={"subagent_type": "coder"}))
    ns2 = sub_ns("uuid-2")
    tr.feed(todo_start(ns=ns2, agent="coder"))
    out = tr.feed(todo_finish([T("子二")], ns=ns2, agent="coder"))

    assert out[0]["items"] == [
        {**T("第一步", "completed"), "children": [T("子一")]},
        {**T("第二步", "in_progress"), "children": [T("子二")]},
    ]


def test_a_role_without_a_matching_delegation_gets_a_container() -> None:
    """没有对应委派（角色对不上）就不硬认领：照旧在树尾补容器，名字仍是它自己的角色名。"""
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(todo_finish([{"content": "第一步", "status": "in_progress"}]))
    tr.note_delegation(tool_start("task", call_id="call_1", input={"subagent_type": "planner"}))
    ns = sub_ns("uuid-1")

    tr.feed(todo_start(ns=ns, agent="reviewer"))
    out = tr.feed(todo_finish([T("审出来的")], ns=ns, agent="reviewer"))

    items = out[0]["items"]
    assert items[0].get("children") is None, "没认领到就不挂靠"
    assert items[-1]["content"] == "评审子任务"
    assert items[-1]["children"] == [T("审出来的")]


def test_container_name_is_the_chinese_role_label() -> None:
    """容器那行是给用户看的，三个角色翻成中文，不摊 ``coder`` 这种内部名。"""

    def title_for(role: str) -> str:
        tr = V3Translator()
        tr.note_delegation(tool_start("task", call_id="t1", input={"subagent_type": role}))
        ns = sub_ns("t1")
        tr.feed(todo_start(ns=ns, agent=role))
        return tr.feed(todo_finish([T("子")], ns=ns, agent=role))[0]["items"][0]["content"]

    assert title_for("planner") == "规划子任务"
    assert title_for("coder") == "编码子任务"
    assert title_for("reviewer") == "评审子任务"
    # 表里还没写的角色不能塌成兜底话：名字照旧带上，看得出是什么
    assert title_for("changelog-writer") == "「changelog-writer」子任务"


def test_container_status_follows_its_children() -> None:
    """容器是展示壳，状态由子项汇总 —— 不写死 in_progress，免得收尾时永久停住。"""
    tr = V3Translator()
    tr.note_delegation(tool_start("task", call_id="t1", input={"subagent_type": "coder"}))
    ns = sub_ns("t1")
    tr.feed(todo_start(ns=ns, agent="coder"))

    def status_of(items: list[dict]) -> str:
        return tr.feed(todo_finish(items, ns=ns, agent="coder"))[0]["items"][0]["status"]

    assert status_of([T("甲"), T("乙")]) == "pending"
    assert status_of([T("甲", "in_progress"), T("乙")]) == "in_progress"
    assert status_of([T("甲", "completed"), T("乙")]) == "pending"
    assert status_of([T("甲", "completed"), T("乙", "completed")]) == "completed"


def test_container_coexists_with_the_main_list() -> None:
    """主清单已全部完成之后才委派：容器与主清单并存，且排在主清单后面。"""
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(todo_finish([T("都做完了", "completed")]))
    tr.note_delegation(tool_start("task", call_id="t1", input={"subagent_type": "coder"}))
    ns = sub_ns("t1")
    tr.feed(todo_start(ns=ns, agent="coder"))
    out = tr.feed(todo_finish([T("子")], ns=ns, agent="coder"))
    assert out[0]["items"] == [
        T("都做完了", "completed"),
        {"content": "编码子任务", "status": "pending", "children": [T("子")]},
    ]


def test_a_renamed_main_item_keeps_its_children() -> None:
    """主项被整句改写：挂上过就锚在这一项上，别再漂到树尾。

    旧口径是「文字认不出就落容器」，于是主控每改写一次标题子树就漂一次（2026-09-27 实见）。
    现在只要主清单**条数没变**就沿用挂上时的位置 —— 模型改的通常是措辞，不是把这一项删了；
    条数变了（项被拆开 / 删掉）就不敢猜，照旧走容器兜底。
    """
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(todo_finish([T("改代码", "in_progress")]))
    tr.note_delegation(tool_start("task", call_id="t1", input={"subagent_type": "coder"}))
    ns = sub_ns("t1")
    tr.feed(todo_start(ns=ns, agent="coder"))
    tr.feed(todo_finish([T("子")], ns=ns, agent="coder"))

    out = tr.feed(todo_finish([T("重构代码", "in_progress")]))
    assert out[0]["items"] == [
        {**T("重构代码", "in_progress"), "children": [T("子")]},
    ]


def test_an_empty_sub_list_gets_no_container() -> None:
    """子清单为空（参数还没流全、或模型给了空数组）：不补空壳容器。"""
    tr = V3Translator()
    tr.note_delegation(tool_start("task", call_id="t1", input={"subagent_type": "coder"}))
    ns = sub_ns("t1")
    tr.feed(todo_start(ns=ns, agent="coder"))
    assert tr.feed(todo_finish([], ns=ns, agent="coder"))[0]["items"] == []
    assert tr.parts.has_todos is True


def test_deeply_nested_child_todos_are_still_dropped() -> None:
    """两级以上的嵌套（子智能体自己又委派）：没有对应的清单位置，仍然丢掉。"""
    tr = V3Translator()
    deep = ("tools:t1", "tools:t2")
    tr.feed(todo_start(ns=deep, agent="planner"))
    assert tr.feed(todo_finish([T("孙")], ns=deep, agent="planner")) == []
    assert tr.parts.has_todos is False


def test_note_delegation_ignores_other_tools_and_nested_agents() -> None:
    """只认主智能体发的 ``task``；别的工具、子智能体自己发的 task 都不记。

    没记 = 没有落点，这份子清单于是走容器兜底 —— 关键是它**不会**挂到「做」下面。
    """
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(todo_finish([{"content": "做", "status": "in_progress"}]))
    tr.note_delegation(tool_start("execute", call_id="t1"))
    tr.note_delegation(tool_start("task", call_id="t1", ns=SUB_NS))
    tr.feed(todo_start(ns=sub_ns("t1"), agent="coder"))
    out = tr.feed(todo_finish([{"content": "子"}], ns=sub_ns("t1"), agent="coder"))
    assert out[0]["items"][0] == {"content": "做", "status": "in_progress"}
    assert out[0]["items"][1] == {
        # 名字取自子智能体自报的 lc_agent_name（这一轮它没认领到委派 —— 那次 task 是
        # 在子智能体里发的，note_delegation 按设计忽略它 —— 所以仍走容器兜底）
        "content": "编码子任务",
        "status": "pending",
        "children": [{"content": "子", "status": "pending"}],
    }


def test_message_start_resets_block_index() -> None:
    tr = V3Translator()
    tr.feed(todo_start(index=1))
    tr.feed(message_start())
    # 旧块号作废：换消息后 index=1 再来一片参数，那是另一条消息的块，不认
    assert tr.feed(todo_delta('{"todos":[{"content":"旧的"}]}', index=1)) == []
    tr.feed(todo_start(index=0))
    out = tr.feed(todo_delta('{"todos":[{"content":"新的","status":"pending"}]}', index=0))
    assert out == [{"type": "todos", "items": [T("新的")]}]
    assert tr.parts.data[-1] == {"kind": "todos", "items": [T("新的")]}


def test_other_tool_block_is_ignored() -> None:
    tr = V3Translator()
    start = messages(
        "content-block-start",
        payload={"content": {"type": "tool_call_chunk", "id": "c2", "name": "run_command"}},
        index=1,
    )
    assert tr.feed(start) == []
    assert tr.feed(todo_delta('{"cmd":"ls"}')) == []


def test_non_messages_events_are_ignored() -> None:
    tr = V3Translator()
    assert tr.feed(tool_start("write_todos")) == []
    assert tr.feed({"method": "values", "params": {"data": {}}}) == []
