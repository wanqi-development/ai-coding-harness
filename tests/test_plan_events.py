"""``_run_agent`` 的事件流处理：方案进度推送、正文/待办的下发与落库。

不跑真 LLM、不连库：agent 事件流用假的喂进去，方案读取打桩。
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ai_coding_harness.agent.middleware import TurnState
from ai_coding_harness.config import Settings
from ai_coding_harness.server import service as service_mod
from ai_coding_harness.server.service import HarnessService, plan_payload
from tests.v3_events import (
    SUB_NS,
    FakeAgent,
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


def _plan(**over) -> SimpleNamespace:
    base = {
        "id": 1,
        "title": "测试方案",
        "status": "executing",
        "updated_at": datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
        "steps": [{"index": 1, "goal": "第一步", "status": "running"}],
    }
    base.update(over)
    return SimpleNamespace(**base)


class TestPlanPayload:
    def test_maps_fields(self):
        payload = plan_payload(
            _plan(
                steps=[
                    {
                        "index": 2,
                        "goal": "改 A",
                        "status": "done",
                        "files": ["a.py"],
                        "done_criteria": "测试过",
                    }
                ]
            )
        )
        assert payload["plan_id"] == 1
        assert payload["title"] == "测试方案"
        assert payload["status"] == "executing"
        assert payload["updated_at"].startswith("2026-09-15T12:00:00")
        assert payload["steps"] == [
            {
                "index": 2,
                "goal": "改 A",
                "status": "done",
                "files": ["a.py"],
                "done_criteria": "测试过",
            }
        ]

    def test_drops_unrenderable_steps(self):
        """历史脏数据里的非 dict 条目直接跳过，别让一个坏步骤打挂整个面板。"""
        payload = plan_payload(_plan(steps=["纯文本", {"index": 1, "goal": "好的一步"}]))
        assert [s["goal"] for s in payload["steps"]] == ["好的一步"]

    @pytest.mark.parametrize("steps", [None, "不是数组", 42])
    def test_non_list_steps_becomes_empty(self, steps):
        assert plan_payload(_plan(steps=steps))["steps"] == []

    def test_missing_goal_and_status_get_defaults(self):
        payload = plan_payload(_plan(steps=[{"index": 3}]))
        assert payload["steps"] == [
            {"index": 3, "goal": "", "status": "pending", "files": None, "done_criteria": None}
        ]


class _FakeDb:
    @asynccontextmanager
    async def session(self):
        yield None


class _FakeManager:
    def __init__(self, events: list[dict]) -> None:
        self._events = events

    def get_agent(self, runtime, session, profile):
        return FakeAgent(self._events)

    def thread_id(self, session):
        return "thread-1"


def _tool_call(name: str, *, output: str = "ok", call_id: str = "t1") -> list[dict]:
    """一次工具调用的两帧：v3 把工具名放在 started，收尾那帧只有 output。"""
    return [tool_start(name, call_id=call_id), tool_finish(call_id=call_id, output=output)]


@pytest.fixture
def plan_reads(monkeypatch) -> list[int]:
    """打桩 dao.get_latest_plan，返回调用次数计数（记录每次读库都发生了）。"""
    reads: list[int] = []
    plan = _plan()

    async def fake_get_latest_plan(db, *, session_id):
        reads.append(session_id)
        return plan

    async def fake_add_record(db, **kwargs):
        return None

    monkeypatch.setattr(service_mod.dao, "get_latest_plan", fake_get_latest_plan)
    monkeypatch.setattr(service_mod.dao, "add_record", fake_add_record)
    return reads


async def _run(events: list[dict], terms: tuple[str, ...] = ()) -> list[dict]:
    svc = HarnessService(
        settings=Settings(), manager=_FakeManager(events), database=_FakeDb()
    )
    session = SimpleNamespace(public_id=uuid.uuid4(), db_id=42)
    runtime = SimpleNamespace(sandbox=None, backend=None, agents={})
    # 逐轮状态平时由 ``chat()`` 新建（循环守卫数重复、``handoffTask`` 重判都用它），
    # 这里直接调私有方法就绕过那一层，得自己补一个同形状的
    return [
        e
        async for e in svc._run_agent(
            session, runtime, "做点事", "coding", terms,
            state=TurnState(message="做点事"),
        )
    ]


class TestPlanEventWiring:
    async def test_emits_plan_snapshot_after_plan_tool(self, plan_reads):
        out = await _run(_tool_call("updatePlanStep"))
        plans = [e for e in out if e["type"] == "plan"]
        assert len(plans) == 1
        assert plans[0]["plan"]["plan_id"] == 1
        assert plans[0]["plan"]["steps"][0]["status"] == "running"
        assert plan_reads == [42]

    async def test_dedupes_identical_snapshots(self, plan_reads):
        """同一状态连推两次工具调用，只发一帧（读还是每次都读，读的是最新值）。"""
        out = await _run([*_tool_call("updatePlanStep"), *_tool_call("updatePlanStep", call_id="t2")])
        assert len([e for e in out if e["type"] == "plan"]) == 1
        assert len(plan_reads) == 2

    @pytest.mark.parametrize("name", ["execute", "read_file", "write_todos", "create_pr"])
    async def test_ignores_other_tools(self, name, plan_reads):
        out = await _run(_tool_call(name))
        assert [e for e in out if e["type"] == "plan"] == []
        assert plan_reads == []

    async def test_tool_event_still_emitted_for_plan_tools(self, plan_reads):
        """加钩子不能把原来的 tool 事件吃掉（起止各一帧）。"""
        out = await _run(_tool_call("storePlan"))
        assert [e["type"] for e in out] == ["tool", "tool", "plan"]

    @pytest.mark.parametrize(
        "name",
        ["storePlan", "updatePlan", "updatePlanSteps", "deletePlan", "updatePlanStep"],
    )
    async def test_every_plan_writing_tool_pushes_a_snapshot(self, name, plan_reads):
        """改方案的每一个工具都要推快照 —— 漏一个，前端面板就停在旧状态上。"""
        out = await _run(_tool_call(name))

        assert [e for e in out if e["type"] == "plan"], name

    async def test_a_read_only_plan_tool_pushes_nothing(self, plan_reads):
        """``getPlan`` 只读：读了不该动面板。"""
        out = await _run(_tool_call("getPlan"))

        assert [e for e in out if e["type"] == "plan"] == []
        assert plan_reads == []

    async def test_a_deleted_plan_pushes_a_null_snapshot(self, monkeypatch, plan_reads):
        """软删之后库里可能一份方案都不剩：这时也得推一帧 ``plan: null`` ——
        不推的话前端会一直显示那份已经不存在的方案。"""
        reads: list[object] = []

        async def fake(db, *, session_id):
            reads.append(session_id)
            return _plan() if len(reads) == 1 else None

        monkeypatch.setattr(service_mod.dao, "get_latest_plan", fake)

        out = await _run(
            [*_tool_call("storePlan"), *_tool_call("deletePlan", call_id="t2")]
        )

        plans = [e for e in out if e["type"] == "plan"]
        assert len(plans) == 2, "写入推一帧、删除再推一帧"
        assert plans[0]["plan"] is not None
        assert plans[1]["plan"] is None

    async def test_no_plan_row_means_no_event(self, monkeypatch, plan_reads):
        async def no_plan(db, *, session_id):
            return None

        monkeypatch.setattr(service_mod.dao, "get_latest_plan", no_plan)
        out = await _run(_tool_call("updatePlanStep"))
        assert [e for e in out if e["type"] == "plan"] == []

    async def test_read_failure_does_not_break_the_stream(self, monkeypatch, plan_reads):
        async def boom(db, *, session_id):
            raise RuntimeError("库挂了")

        monkeypatch.setattr(service_mod.dao, "get_latest_plan", boom)
        out = await _run(_tool_call("updatePlanStep"))
        assert [e["type"] for e in out] == ["tool", "tool"]

    async def test_finished_frame_carries_the_name_resolved_from_started(self, plan_reads):
        """v3 的 tool-finished 不带工具名，得靠 tool_call_id 从 started 那帧配对回来。"""
        out = await _run(_tool_call("storePlan"))
        assert [e["name"] for e in out if e["type"] == "tool"] == ["storePlan", "storePlan"]


# ---------------------------------------------------------------------------
# 用户可见流：正文 / 待办两条通道，子智能体整条不出
# ---------------------------------------------------------------------------


@pytest.fixture
def recorded(monkeypatch) -> list[dict]:
    """收集落库记录（顺带把方案读取打桩成「没有方案」）。"""
    records: list[dict] = []

    async def fake_add_record(db, **kwargs):
        records.append(kwargs)

    async def no_plan(db, *, session_id):
        return None

    monkeypatch.setattr(service_mod.dao, "add_record", fake_add_record)
    monkeypatch.setattr(service_mod.dao, "get_latest_plan", no_plan)
    return records


class TestUserVisibleStream:
    async def test_subagent_stream_is_fully_suppressed(self, plan_reads):
        """coder 的旁白属于内部编排，一帧都不推给用户。"""
        out = await _run(
            [
                *_tool_call("task"),
                text_delta("Let me look at the repo first.", ns=SUB_NS, agent="coder"),
                reasoning("先看代码。", ns=SUB_NS, agent="coder"),
            ]
        )
        assert [e for e in out if e["type"] == "ai"] == []

    async def test_subagent_todos_without_a_main_list_get_a_container(self, plan_reads):
        """主清单没建时不再整份吞掉：树尾补容器兜住，SSE 上要看得见。"""
        ns = sub_ns("t1")  # 与 task 的 call_id 对上，排除「没记成委派」
        out = await _run(
            [
                tool_start("task", call_id="t1", input={"subagent_type": "coder"}),
                tool_finish(call_id="t1", output="ok"),
                todo_start(ns=ns, agent="coder"),
                todo_delta(
                    '{"todos":[{"content":"子任务","status":"pending"}]}',
                    ns=ns,
                    agent="coder",
                ),
                todo_finish([{"content": "子任务"}], ns=ns, agent="coder"),
            ]
        )
        frames = [e for e in out if e["type"] == "todos"]
        assert frames, "容器帧至少要下发一次"
        for frame in frames:
            assert frame["items"] == [
                {
                    "content": "编码子任务",
                    "status": "pending",
                    "children": [{"content": "子任务", "status": "pending"}],
                }
            ]

    async def test_subagent_todos_land_under_the_running_item(self, recorded):
        """一条链路走通：主清单 → 委派 → 子清单，SSE 与落库都该是嵌套的样子。"""
        ns = sub_ns("t1")
        out = await _run(
            [
                todo_start(),
                todo_finish(
                    [
                        {"content": "看仓库", "status": "completed"},
                        {"content": "改代码", "status": "in_progress"},
                    ]
                ),
                *_tool_call("task"),
                todo_start(ns=ns, agent="coder"),
                todo_finish([{"content": "读 diff"}], ns=ns, agent="coder"),
            ]
        )
        tree = [
            {"content": "看仓库", "status": "completed"},
            {
                "content": "改代码",
                "status": "in_progress",
                "children": [{"content": "读 diff", "status": "pending"}],
            },
        ]
        assert [e["items"] for e in out if e["type"] == "todos"][-1] == tree
        assert recorded[-1]["extra"]["parts"][-1] == {"kind": "todos", "items": tree}

    async def test_task_frame_still_emitted_for_the_frontend(self, plan_reads):
        """过滤的是文本，不是工具帧 —— 前端仍要知道「委派了子智能体」。"""
        out = await _run(
            [*_tool_call("task"), text_delta("Let me look.", ns=SUB_NS, agent="coder")]
        )
        assert [e["type"] for e in out] == ["tool", "tool"]

    async def test_delegation_frames_carry_the_subagent_role(self, plan_reads):
        """``task`` 起止两帧都带上角色 —— 前端靠它说「正在编码」而不是「正在处理子任务」。

        收尾帧也带：不带的话子智能体一返回，提示就翻回泛化文案，而主智能体还要想几秒。
        """
        out = await _run(
            [
                tool_start("task", call_id="t1", input={"subagent_type": "coder"}),
                tool_finish(call_id="t1", output="ok"),
            ]
        )
        assert [e["agent"] for e in out if e["type"] == "tool"] == ["coder", "coder"]

    @pytest.mark.parametrize("bad_input", ["not-a-dict", {}, {"subagent_type": ""}])
    async def test_delegation_without_a_role_carries_no_agent_key(self, bad_input, plan_reads):
        """取不到角色就不带这个键（前端那层再退回泛化文案），别推一个空串过去。"""
        out = await _run(
            [
                tool_start("task", call_id="t1", input=bad_input),
                tool_finish(call_id="t1", output="ok"),
            ]
        )
        assert [e for e in out if e["type"] == "tool"]
        for frame in out:
            assert "agent" not in frame

    async def test_other_tool_frames_carry_no_role(self, plan_reads):
        """普通工具帧形状不变：没有 ``agent`` 这个键。"""
        out = await _run(_tool_call("read_file"))
        assert [e for e in out if e["type"] == "tool"]
        for frame in out:
            assert "agent" not in frame

    async def test_main_agent_answer_passes_through(self, plan_reads):
        out = await _run([text_delta("我先看一下仓库。")])

        assert out == [{"type": "ai", "content": "我先看一下仓库。"}]

    async def test_reasoning_never_reaches_the_user(self, plan_reads):
        """思考结果不再下发，流里只剩正文。"""
        out = await _run([reasoning("先核对文件。"), text_delta("方案如下。")])

        assert out == [{"type": "ai", "content": "方案如下。"}]

    async def test_todos_progress_is_streamed_then_closed(self, plan_reads):
        out = await _run(
            [
                text_delta("先列清单。"),
                todo_start(),
                todo_delta('{"todos":[{"content":"读代码","status":"pending"}]}'),
                todo_finish([{"content": "读代码", "status": "completed"}]),
                text_delta("开始动手。"),
            ]
        )
        kinds = [(e["type"], e.get("items") or e.get("content")) for e in out]
        # 收尾那帧是权威值：状态从 pending 订正成 completed；同一棵树，整体替换
        assert kinds == [
            ("ai", "先列清单。"),
            ("todos", [{"content": "读代码", "status": "pending"}]),
            ("todos", [{"content": "读代码", "status": "completed"}]),
            ("ai", "开始动手。"),
        ]

    async def test_record_keeps_only_the_answer_and_no_parts_without_todos(self, recorded):
        """纯正文的回合不写 parts：content 已经够回放，别给每条记录都挂一份镜像。"""
        await _run(
            [
                *_tool_call("task"),
                reasoning("子智能体的推理", ns=SUB_NS, agent="coder"),
                reasoning("先核对文件。"),
                text_delta("已拿到方案。"),
            ]
        )

        record = recorded[-1]
        assert record["content"] == "已拿到方案。"
        assert record["summary"] == "已拿到方案。"
        assert record["extra"] == {"intent": "coding"}

    async def test_subagent_narration_is_not_persisted(self, recorded):
        """子智能体的文本也不进落库内容，否则重新打开会话又冒出来。"""
        await _run(
            [
                *_tool_call("task"),
                text_delta("Let me inspect the sandbox.", ns=SUB_NS, agent="coder"),
                text_delta("已拿到方案，见右侧面板。"),
            ]
        )

        assert recorded[-1]["content"] == "已拿到方案，见右侧面板。"

    async def test_record_keeps_text_in_order_and_the_tree_last(self, recorded):
        await _run(
            [
                text_delta("先列清单。"),
                todo_start(),
                todo_finish([{"content": "读代码", "status": "completed"}]),
                text_delta("开始动手。"),
            ]
        )

        assert recorded[-1]["extra"] == {
            "intent": "coding",
            "parts": [
                {"kind": "text", "content": "先列清单。开始动手。"},
                {"kind": "todos", "items": [{"content": "读代码", "status": "completed"}]},
            ],
        }

    async def test_parts_and_frames_are_redacted(self, recorded):
        """翻译器手上是原文，两个出口（SSE / 落库）都得自己过一遍脱敏。"""
        secret = "glpat-abcdefghijklmnop"
        out = await _run(
            [
                text_delta(f"令牌是 {secret}"),
                todo_start(),
                todo_finish([{"content": f"用 {secret} 登录"}]),
            ],
            terms=(secret,),
        )

        assert secret not in str(out)
        assert secret not in str(recorded[-1])
        assert recorded[-1]["extra"]["parts"][1] == {
            "kind": "todos",
            "items": [{"content": "用 [已脱敏] 登录", "status": "pending"}],
        }
