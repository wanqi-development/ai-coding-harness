"""按角色分配自定义工具的守卫测试。

「谁拿哪个工具」是行为契约的一部分：PR 评论的发布权只给 reviewer 与主智能体，
coder 可以**读**评论（自己拉审查原文）、但只回报、不自己评论（PR 上的回复统一由
主智能体按用户指示发出）。

``handoffTask`` 是**所有形态**的出口，所以它不在 ``_FORM_TOOLS`` 那张表里 —— 表裁的是
「这个形态有什么能力」，而交接恰恰是能力不够时的出路。这里也一并钉住。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai_coding_harness.agent.profiles import (
    ANALYSIS_FORM,
    ORCHESTRATE_FORM,
    PLAN_FORM,
    QA_FORM,
    REVIEW_FORM,
)
from ai_coding_harness.config import Settings
from ai_coding_harness.tools.git import GiteeRepo
from ai_coding_harness.tools.handoff import HandoffContext
from ai_coding_harness.tools.plan import PlanContext
from ai_coding_harness.tools.register import build_main_tools, build_subagent_tools

REPO = GiteeRepo(owner="owner", repo="repo", token="test-token")


def _fake(name: str) -> SimpleNamespace:
    """占位工具：只用于断言名字，真实构造需要联网/DB，不是本文件的关注点。"""
    return SimpleNamespace(name=name)


def _fake_backend() -> SimpleNamespace:
    """占位 backend：``save_repo_memory`` 的构造期不碰它，只在调用时才 await awrite。"""
    return SimpleNamespace(awrite=None)


def _names(tools) -> list[str]:
    return [t.name for t in tools]


def _subagent(role: str) -> list[str]:
    return _names(
        build_subagent_tools(
            role,
            Settings(),
            web_search_tool=_fake("web_search_tool"),  # type: ignore[arg-type]
            fetch_url_tool=_fake("fetch_url_tool"),  # type: ignore[arg-type]
            repo=REPO,
            database=object(),  # type: ignore[arg-type]  # 只为通过 build_plan_tools 的真值判断
            plan_ctx=PlanContext(session_db_id=1, owner="owner", repo="repo"),
        )
    )


def _main() -> list[str]:
    return _names(
        build_main_tools(
            Settings(),
            _fake("web_search_tool"),  # type: ignore[arg-type]
            _fake("fetch_url_tool"),  # type: ignore[arg-type]
            REPO,
            _fake_backend(),  # type: ignore[arg-type]
        )
    )


def _handoff(form: str) -> HandoffContext:
    """``HandoffContext`` 只是被闭包捕获，构建期不碰里面任何东西 —— 假件够用。"""
    return HandoffContext(
        manager=SimpleNamespace(),  # type: ignore[arg-type]
        runtime=SimpleNamespace(sandbox=None, backend=None, agents={}),  # type: ignore[arg-type]
        session=SimpleNamespace(public_id="pub-1"),  # type: ignore[arg-type]
        settings=Settings(),
        form=form,
    )


def _main_with_handoff(form: str) -> list[str]:
    return _names(
        build_main_tools(
            Settings(),
            _fake("web_search_tool"),  # type: ignore[arg-type]
            _fake("fetch_url_tool"),  # type: ignore[arg-type]
            REPO,
            _fake_backend(),  # type: ignore[arg-type]
            form=form,
            handoff=_handoff(form),
        )
    )


def test_main_can_read_and_post_pr_comments():
    """默认（不传 form = orchestrate）的完整清单。

    ``get_unexecuted_plan`` 是 2026-09-27 加的**只读**工具：用户明确说「直接做」时，主控
    自己先查有没有可执行的方案，省掉「派了 coder 才发现没方案、再改派 planner」那一圈。
    """
    assert _main() == [
        "web_search_tool",
        "fetch_url_tool",
        "get_pr_info",
        "list_pr_comments",
        "comment_pr",
        "close_pr",
        "get_unexecuted_plan",
        "planExists",
        "save_repo_memory",
    ]


def test_main_cannot_merge_pr():
    """合并必须由人工在 Gitee 上做：工具本身就不该存在。"""
    assert "merge_pr" not in _main()


def test_only_main_writes_repo_memory():
    """记忆写回统一由主智能体负责，子智能体只回报结论。"""
    assert "save_repo_memory" in _main()
    for role in ("planner", "coder", "reviewer"):
        assert "save_repo_memory" not in _subagent(role)


def test_coder_can_read_but_not_post_pr_comments():
    names = _subagent("coder")

    assert "create_pr" in names
    # 读评论：用来自己拉审查原文（主控只给问题编号，不整段粘贴）
    assert "list_pr_comments" in names
    # 写评论：仍归主智能体
    assert "comment_pr" not in names


def test_reviewer_can_read_and_post_pr_comments():
    assert _subagent("reviewer") == ["comment_pr", "list_pr_comments", "get_pr_info"]


def test_planner_gets_no_pr_tools():
    names = _subagent("planner")

    assert names == ["web_search_tool", "fetch_url_tool", "storePlan"]


def test_plan_form_owns_the_plan_crud_tools():
    """方案的增删改查都在 plan 形态手里（2026-09-25 加的后四个）。"""
    names = _main_with_handoff(PLAN_FORM)

    for name in ("storePlan", "getPlan", "updatePlan", "updatePlanSteps", "deletePlan"):
        assert name in names, name


def test_plan_exists_is_the_cheap_lookup():
    """``planExists`` 是只回布尔的轻量查询：两个形态都该拿到它（2026-10-13 用户口径）。

    plan 形态用它代替「为了判存在性把一万字正文读回来」，coding 形态用它做「派 coder 之前
    确认那份方案还能实施」—— 正文让 coder 自己用 ``get_unexecuted_plan`` 取。
    """
    assert "planExists" in _main_with_handoff(PLAN_FORM)
    assert "planExists" in _main_with_handoff(ORCHESTRATE_FORM)


@pytest.mark.parametrize("form", [ORCHESTRATE_FORM, QA_FORM, ANALYSIS_FORM, REVIEW_FORM])
def test_other_forms_have_no_plan_crud_tools(form):
    """别的形态不该拿到改方案的工具：qa / analysis / review 不碰方案，
    orchestrate 只管编排（实施进度由 coder 回写）。"""
    names = set(_main_with_handoff(form))

    assert not (names & {"getPlan", "updatePlan", "updatePlanSteps", "deletePlan"}), form


def test_orchestrate_can_look_up_an_unexecuted_plan_but_not_edit_one():
    """编码轮的开工判定要能查方案，但方案本身仍归 planner。

    2026-09-27 用户口径：用户明确说「直接做」时，主控**先自己查一次**有没有可执行的方案 ——
    有就直接派 coder、没有才先派 planner（省掉「派了 coder 才发现没方案、再改派 planner」）。
    查得到的只有 `get_unexecuted_plan` 这一件只读工具：`storePlan` / `updatePlan` /
    `updatePlanSteps` / `deletePlan` 一件都不给它，免得主控顺手把方案改了（那是 planner 的活，
    plan 形态才有）。
    """
    names = set(_main_with_handoff(ORCHESTRATE_FORM))

    assert "get_unexecuted_plan" in names
    assert not (
        names & {"storePlan", "getPlan", "updatePlan", "updatePlanSteps", "deletePlan"}
    )


@pytest.mark.parametrize(
    "form", [ORCHESTRATE_FORM, PLAN_FORM, QA_FORM, ANALYSIS_FORM, REVIEW_FORM]
)
def test_every_form_can_hand_off(form):
    """五个形态都拿得到 ``handoffTask``。

    形态裁掉的能力模型看不见，没有这个出口它就只能对用户说「我做不了」—— 2026-09-21
    那轮 686 秒正是这么来的（plan 形态手上没有写权限，于是复述一遍方案再问用户）。
    所以哪怕是最窄的 qa / analysis 也得有这个工具。
    """
    assert "handoffTask" in _main_with_handoff(form)


def test_handoff_is_optional():
    """不给 ``handoff`` 就不挂：脚本与单测直接建工具时不必凑一个会话上下文。"""
    assert "handoffTask" not in _main()


def test_subagents_cannot_hand_off():
    """交接是**主控**的出口：子智能体只回报结论，做不了就说做不了。"""
    for role in ("planner", "coder", "reviewer"):
        assert "handoffTask" not in _subagent(role)
