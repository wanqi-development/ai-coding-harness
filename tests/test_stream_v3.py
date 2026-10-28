"""``stream.py``：v3 事件的拆封、write_todos 参数串的抠取、正文与待办树的拼装。

事件形状见 ``tests/v3_events.py``（照探针实测）。
"""

from __future__ import annotations

from ai_coding_harness.core.stream import (
    ToolNames,
    V3Translator,
    call_id_of,
    delegation_id,
    event_name,
    finished_items,
    is_internal_call,
    last_ai_text,
    metadata_of,
    namespace_of,
    parent_run_id_of,
    payload_of,
    run_id_of,
    text_of_content,
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


def test_run_ids_can_build_a_parent_child_tree() -> None:
    event = text_delta("正文")
    event["run_id"] = "child"
    event["parent_ids"] = ["root", "handoff"]

    assert run_id_of(event) == "child"
    assert parent_run_id_of(event) == "handoff"
    part = V3Translator().feed(event)[0]
    assert part["run_id"] == "child"
    assert part["parent_run_id"] == "handoff"


def test_event_name_follows_the_channel() -> None:
    assert event_name(messages("content-block-delta")) == "content-block-delta"
    assert event_name(tool_finish()) == "tool-finished"


def test_text_of_content_keeps_only_plain_text() -> None:
    content = [
        {"type": "reasoning", "reasoning": "不要展示"},
        {"type": "text", "text": "第一段"},
        "第二段",
        {"type": "invalid_tool_call", "name": "x"},
    ]

    assert text_of_content(content) == "第一段第二段"
    assert text_of_content("纯字符串") == "纯字符串"
    assert text_of_content(None) == ""


def test_last_ai_text_skips_reasoning_and_empty_messages() -> None:
    messages = [
        {"type": "human", "content": "问题"},
        {"type": "ai", "content": [{"type": "text", "text": "旧回答"}]},
        {
            "type": "ai",
            "content": [{"type": "reasoning", "reasoning": "只有思考"}],
        },
    ]

    assert last_ai_text(messages) == "旧回答"


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


def test_reasoning_delta_is_a_transient_event_not_text() -> None:
    """思考单独下发，**不进 parts**（不落库）；主控标 ``main``。"""
    tr = V3Translator()
    assert tr.feed(reasoning("（思考）")) == [
        {"type": "reasoning", "content": "（思考）", "agent": "main"}
    ]
    assert tr.parts.data == []


def test_subagent_reasoning_carries_its_role_but_stays_out_of_parts() -> None:
    """子智能体的思考也下发（2026-10-17 用户口径），但要标出是哪个角色。"""
    tr = V3Translator()

    assert tr.feed(reasoning("先读代码", ns=SUB_NS, agent="coder")) == [
        {"type": "reasoning", "content": "先读代码", "agent": "coder"}
    ]
    assert tr.parts.data == []


def test_internal_reasoning_is_dropped() -> None:
    """摘要等中间件内部调用的思考不是助手对用户说的话。"""
    tr = V3Translator()

    assert tr.feed(reasoning("摘要的思考", internal=True)) == []
    assert tr.parts.data == []


def test_nested_subagent_reasoning_is_dropped() -> None:
    """两级以上嵌套没有展示位置，与正文/待办同一口径。"""
    tr = V3Translator()

    assert tr.feed(reasoning("深层思考", ns=("tools:c1", "tools:c2"), agent="coder")) == []
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


def test_disallowed_todos_are_ignored_even_if_the_model_hallucinates_them() -> None:
    """qa 没有 write_todos；模型伪造该调用时不能生成前端/落库用的幽灵清单。"""
    tr = V3Translator(allow_todos=False)

    assert tr.feed(todo_start()) == []
    assert tr.feed(todo_delta('{"todos":[{"content":"幽灵任务","status":"pending"}]}')) == []
    assert tr.feed(todo_finish([{"content": "幽灵任务", "status": "completed"}])) == []
    assert tr.parts.has_todos is False
    assert tr.parts.data == []


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


def _delegate(tr: V3Translator, role: str, *, call_id: str = "t1") -> None:
    """按真实形状派一次子智能体：``task`` 的入参里带 ``subagent_type``。"""
    tr.note_delegation(
        tool_start(
            "task", call_id=call_id, input={"subagent_type": role, "description": "做事"}
        )
    )


def _child_todos(
    tr: V3Translator, role: str, items: list[dict], *, call_id: str = "t1"
) -> list[dict]:
    """子智能体那一侧：自报角色（认领这次委派）→ 它的清单落进树，返回下发的帧。"""
    ns = sub_ns(call_id)
    tr.feed(text_delta("开工。", ns=ns, agent=role))
    tr.feed(todo_start(ns=ns, agent=role))
    return tr.feed(todo_finish(items, ns=ns, agent=role))


def test_a_delegation_lands_on_the_step_named_after_its_role() -> None:
    """父项按**角色**找那一步，而不是「此刻 in_progress 的那项」。

    实测（2026-10-13，真机）：主控的清单是
        [in_progress] 确认 plan_id=44 处于待执行状态
        [pending]     派 coder 按方案实施（写码/测试/提 PR）
    而 coder 的六步子树整棵挂到了「确认…」底下 —— 因为委派那一刻 in_progress 的是它。
    角色是委派那一刻就确定的事实，比主控的清单推进可靠（主控推进清单是滞后的）。
    """
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(
        todo_finish(
            [
                {"content": "确认 plan_id=44 处于待执行状态", "status": "in_progress"},
                {"content": "派 coder 按方案实施（写码/测试/提 PR）", "status": "pending"},
                {"content": "派 reviewer 审查 PR", "status": "pending"},
            ]
        )
    )
    _delegate(tr, "coder")
    out = _child_todos(tr, "coder", [{"content": "步骤 1：改存储"}])

    items = out[0]["items"]
    assert items[0].get("children") is None, "前置项（确认…）不该长出子树"
    assert items[1]["children"] == [T("步骤 1：改存储")], "挂在「派 coder 按方案实施…」下"


def test_the_planner_lands_on_the_planning_step_even_when_the_check_is_still_running() -> None:
    """同样的一类：派 planner 时 in_progress 还停在「查询是否存在未执行的方案」。"""
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(
        todo_finish(
            [
                {"content": "查询是否存在未执行的方案", "status": "in_progress"},
                {"content": "制定忘记密码与短信验证码实施方案", "status": "pending"},
                {"content": "按方案实施代码并提交 PR", "status": "pending"},
            ]
        )
    )
    _delegate(tr, "planner")
    out = _child_todos(tr, "planner", [{"content": "读码核实"}])

    items = out[0]["items"]
    assert items[0].get("children") is None
    assert items[1]["children"] == [T("读码核实")]


def test_the_running_item_is_still_the_fallback() -> None:
    """清单里没有该角色的词（自由写法）→ 回落到老规则（in_progress 那一项），
    不至于让子清单整棵消失。"""
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(
        todo_finish(
            [
                {"content": "看看这个仓库", "status": "in_progress"},
                {"content": "然后收工", "status": "pending"},
            ]
        )
    )
    _delegate(tr, "coder")
    out = _child_todos(tr, "coder", [{"content": "读 diff"}])

    assert out[0]["items"][0]["children"] == [T("读 diff")]


def test_a_completed_step_is_not_a_parent() -> None:
    """已完成的那一步不再收子清单：角色词命中的是下一步。"""
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(
        todo_finish(
            [
                {"content": "制定实施方案", "status": "completed"},
                {"content": "派 coder 按方案实施", "status": "in_progress"},
            ]
        )
    )
    _delegate(tr, "coder")
    out = _child_todos(tr, "coder", [{"content": "写码"}])

    items = out[0]["items"]
    assert items[0].get("children") is None, "已完成的那一项不该再长子树"
    assert items[1]["children"] == [T("写码")]


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


def test_a_removed_parent_drops_the_child_progress() -> None:
    """主控把“实施”改成“核对反馈”后，旧 coder 清单没有当前落点，直接丢掉。"""
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(todo_finish([{"content": "实施用户资料编辑代码并提 PR", "status": "in_progress"}]))
    tr.note_delegation(tool_start("task", call_id="t1", input={"subagent_type": "coder"}))
    ns = sub_ns("t1")
    tr.feed(todo_start(ns=ns, agent="coder"))
    tr.feed(todo_finish([T("写码")], ns=ns, agent="coder"))

    out = tr.feed(todo_finish([{"content": "核对 coder 的反馈", "status": "in_progress"}]))
    items = out[0]["items"]
    assert items == [T("核对 coder 的反馈", "in_progress")]
    assert "children" not in items[0]


def test_an_ambiguous_title_does_not_get_guessed() -> None:
    """两串归一到同一个键时不硬认：宁可丢掉子清单，也不能挂到别的父项下面。"""
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
    assert len(items) == 2, "不额外生成历史容器"


def test_a_short_title_is_not_prefix_matched() -> None:
    """短标题不走前缀认亲：`改代码` 与 `改代码并提交` 可能是两件事，宁可丢子清单。"""
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(todo_finish([{"content": "改代码", "status": "in_progress"}]))
    tr.note_delegation(tool_start("task", call_id="t1", input={"subagent_type": "coder"}))
    tr.feed(todo_finish([{"content": "改代码并提交", "status": "in_progress"}]))
    ns = sub_ns("t1")
    tr.feed(todo_start(ns=ns, agent="coder"))
    items = tr.feed(todo_finish([T("写码")], ns=ns, agent="coder"))[0]["items"]
    assert items[0].get("children") is None
    assert len(items) == 1


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


def test_child_todos_without_a_parent_are_dropped() -> None:
    """主清单为空时没有当前进度落点，子清单不显示。"""
    tr = V3Translator()
    tr.note_delegation(tool_start("task", call_id="t1", input={"subagent_type": "coder"}))
    ns = sub_ns("t1")
    tr.feed(todo_start(ns=ns, agent="coder"))
    out = tr.feed(
        todo_finish([T("写码"), T("测试")], ns=ns, agent="coder")
    )
    assert out == []
    assert tr.parts.data == []
    assert tr.parts.has_todos is False


def test_an_unusable_delegation_without_a_parent_produces_nothing() -> None:
    """委派与父项都认不出时不造兜底容器。"""
    tr = V3Translator()
    tr.note_delegation(tool_start("task", call_id="t1", input="not-a-dict"))
    ns = sub_ns("t1")
    tr.feed(todo_start(ns=ns, agent="None"))
    assert tr.feed(todo_finish([T("子")], ns=ns, agent="None")) == []


def test_a_child_without_a_current_parent_produces_nothing() -> None:
    """真机两套 id 不同时，只要父清单不存在，子清单也不产生独立节点。"""
    tr = V3Translator()
    # 委派那帧的 call_id 与子智能体的 namespace id **故意不同**（真机就是这样）
    tr.note_delegation(tool_start("task", call_id="call_abc", input={"subagent_type": "coder"}))
    ns = sub_ns("uuid-1234")

    tr.feed(todo_start(ns=ns, agent="coder"))
    assert tr.feed(todo_finish([T("写码")], ns=ns, agent="coder")) == []


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


def test_a_role_without_a_matching_delegation_is_not_attached() -> None:
    """没有对应委派（角色对不上）就不硬认领，也不另造容器。"""
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(todo_finish([{"content": "第一步", "status": "in_progress"}]))
    tr.note_delegation(tool_start("task", call_id="call_1", input={"subagent_type": "planner"}))
    ns = sub_ns("uuid-1")

    tr.feed(todo_start(ns=ns, agent="reviewer"))
    out = tr.feed(todo_finish([T("审出来的")], ns=ns, agent="reviewer"))

    items = out[0]["items"]
    assert items[0].get("children") is None, "没认领到就不挂靠"
    assert len(items) == 1


def test_no_parent_means_only_the_current_main_list_is_shown() -> None:
    """主清单全部完成后再出现无落点的子清单，只展示主清单本身。"""
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(todo_finish([T("都做完了", "completed")]))
    tr.note_delegation(tool_start("task", call_id="t1", input={"subagent_type": "coder"}))
    ns = sub_ns("t1")
    tr.feed(todo_start(ns=ns, agent="coder"))
    out = tr.feed(todo_finish([T("子")], ns=ns, agent="coder"))
    assert out[0]["items"] == [T("都做完了", "completed")]


def test_a_fully_rewritten_main_item_drops_its_children() -> None:
    """主项完全改写且没有等价文字线索时视为已取消，旧子清单不再沿用原位。"""
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(todo_finish([T("改代码", "in_progress")]))
    tr.note_delegation(tool_start("task", call_id="t1", input={"subagent_type": "coder"}))
    ns = sub_ns("t1")
    tr.feed(todo_start(ns=ns, agent="coder"))
    tr.feed(todo_finish([T("子")], ns=ns, agent="coder"))

    out = tr.feed(todo_finish([T("重构代码", "in_progress")]))
    assert out[0]["items"] == [T("重构代码", "in_progress")]


def test_an_empty_sub_list_produces_nothing_without_a_main_list() -> None:
    """子清单为空且没有主清单：不造空树或空壳容器。"""
    tr = V3Translator()
    tr.note_delegation(tool_start("task", call_id="t1", input={"subagent_type": "coder"}))
    ns = sub_ns("t1")
    tr.feed(todo_start(ns=ns, agent="coder"))
    assert tr.feed(todo_finish([], ns=ns, agent="coder")) == []
    assert tr.parts.has_todos is False


def test_deeply_nested_child_todos_are_still_dropped() -> None:
    """两级以上的嵌套（子智能体自己又委派）：没有对应的清单位置，仍然丢掉。"""
    tr = V3Translator()
    deep = ("tools:t1", "tools:t2")
    tr.feed(todo_start(ns=deep, agent="planner"))
    assert tr.feed(todo_finish([T("孙")], ns=deep, agent="planner")) == []
    assert tr.parts.has_todos is False


def test_note_delegation_ignores_other_tools_and_nested_agents() -> None:
    """只认主智能体发的 ``task``；别的工具、子智能体自己发的 task 都不记。

    没记 = 没有落点，这份子清单直接丢弃 —— 关键是它**不会**挂到「做」下面。
    """
    tr = V3Translator()
    tr.feed(todo_start())
    tr.feed(todo_finish([{"content": "做", "status": "in_progress"}]))
    tr.note_delegation(tool_start("execute", call_id="t1"))
    tr.note_delegation(tool_start("task", call_id="t1", ns=SUB_NS))
    tr.feed(todo_start(ns=sub_ns("t1"), agent="coder"))
    out = tr.feed(todo_finish([{"content": "子"}], ns=sub_ns("t1"), agent="coder"))
    assert out[0]["items"][0] == {"content": "做", "status": "in_progress"}
    assert len(out[0]["items"]) == 1


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
