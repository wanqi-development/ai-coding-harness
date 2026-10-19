"""影子路由器：解析、策略表与“只建议不决定”的边界。"""

from __future__ import annotations

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from ai_coding_harness.server.intent_router import (
    RouterDecision,
    parse_router_reply,
    route,
    suggest_intent,
)


def R(
    speech: str = "new_task",
    wants: str = "lookup",
    target: str = "code",
    continuation: str = "new",
) -> RouterDecision:
    return RouterDecision(speech, wants, target, continuation)


class TestParse:
    def test_plain_json(self):
        got = parse_router_reply(
            '{"speech_act":"meta_question","wants":"analyze",'
            '"target":"execution","continuation":"continue_previous"}'
        )

        assert got == R("meta_question", "analyze", "execution", "continue_previous")

    def test_fenced_json_and_surrounding_text(self):
        got = parse_router_reply(
            '```json\n{"speech_act":"new_task","wants":"change",'
            '"target":"pr_change","continuation":"new"}\n```'
        )

        assert got == R("new_task", "change", "pr_change", "new")

    def test_unknown_values_are_rejected(self):
        assert parse_router_reply('{"speech_act":"???","wants":"analyze","target":"code"}') is None
        assert parse_router_reply("not json") is None
        assert parse_router_reply("") is None

    def test_missing_continuation_defaults_to_unknown(self):
        got = parse_router_reply(
            '{"speech_act":"new_task","wants":"change","target":"code"}'
        )

        assert got == R("new_task", "change", "code", "unknown")


class TestPolicy:
    """第一版策略表：只依赖轴，不依赖具体措辞。"""

    def test_meta_question_about_execution_explains(self):
        """「你为什么不派 coder」只是在问上一轮时，落到 analysis 解释，不强行改码。"""
        assert (
            suggest_intent(
                R("meta_question", "analyze", "execution", "continue_previous"),
                previous_intent="rework",
            )
            == "analysis"
        )

    def test_meta_question_that_asks_to_continue_keeps_rework(self):
        assert (
            suggest_intent(
                R("meta_question", "change", "execution", "continue_previous"),
                previous_intent="rework",
            )
            == "rework"
        )

    def test_explicit_continue_marker_keeps_previous_intent(self):
        """「继续 / 修正 / 为什么不 / 不是…吗」按未完成轮次处理。"""
        assert (
            suggest_intent(
                R("meta_question", "analyze", "execution", "continue_previous"),
                previous_intent="rework",
                message="你为什么不派 coder 执行",
            )
            == "rework"
        )
        assert (
            suggest_intent(
                R("meta_question", "analyze", "execution", "continue_previous"),
                previous_intent="rework",
                message="上一轮，你的意图不是 rework 吗",
            )
            == "rework"
        )

    def test_a_diagnosis_question_is_not_treated_as_continue(self):
        assert (
            suggest_intent(
                R("meta_question", "analyze", "execution", "continue_previous"),
                previous_intent="coding",
                message="你执行的任务，我怎么感觉没有向前推进呢",
            )
            == "analysis"
        )

    def test_post_merge_or_close_is_analysis(self):
        """PR 已合并/已关闭之后的收尾动作归 analysis；刚提交/待合并不算。"""
        assert (
            suggest_intent(
                R("follow_up", "change", "pr_status", "continue_previous"),
                previous_intent="analysis",
                message="pr7 已合并，更新记忆",
            )
            == "analysis"
        )

    def test_plan_always_wins_even_for_a_question(self):
        assert suggest_intent(R("follow_up", "analyze", "plan"), previous_intent="plan") == "plan"

    def test_general_decide_is_qa(self):
        """一般性的“有什么建议”是问答；真正的方案请求 target 会是 plan。"""
        assert suggest_intent(R("new_task", "decide", "general")) == "qa"

    def test_implementing_a_plan_is_coding(self):
        assert (
            suggest_intent(
                R("follow_up", "change", "plan", "continue_previous"),
                previous_intent="plan",
                message="按方案实施",
            )
            == "coding"
        )

    def test_start_implementing_keeps_rework_when_that_was_the_task(self):
        assert (
            suggest_intent(
                R("follow_up", "change", "pr_change", "continue_previous"),
                previous_intent="rework",
                message="开始实施",
            )
            == "rework"
        )

    def test_short_followups_inherit_the_previous_intent(self):
        assert (
            suggest_intent(
                R("follow_up", "decide", "plan", "continue_previous"),
                previous_intent="coding",
                message="确认事实",
            )
            == "coding"
        )
        assert (
            suggest_intent(
                R("follow_up", "decide", "plan", "continue_previous"),
                previous_intent="plan",
                message="B",
            )
            == "plan"
        )

    def test_a_short_fix_after_a_plan_means_implementation(self):
        assert (
            suggest_intent(
                R("follow_up", "change", "plan", "continue_previous"),
                previous_intent="plan",
                message="修复吧",
            )
            == "coding"
        )

    def test_observe_only_is_analysis(self):
        assert (
            suggest_intent(
                R("follow_up", "change", "execution", "continue_previous"),
                previous_intent="coding",
                message="你继续做，我只想知道判定结果",
            )
            == "analysis"
        )

    def test_a_discussion_question_is_not_a_change(self):
        assert (
            suggest_intent(
                R("follow_up", "change", "execution", "continue_previous"),
                previous_intent="coding",
                message="那是不是可以读一部分，总结一部分",
            )
            == "analysis"
        )

    def test_a_general_how_to_question_is_analysis(self):
        assert (
            suggest_intent(
                R("new_task", "decide", "general"),
                message="如何初始化数据库以及 nacos、redis",
            )
            == "analysis"
        )

    def test_a_general_suggestion_is_not_forced_into_analysis(self):
        assert (
            suggest_intent(
                R("new_task", "decide", "general"),
                message="短信模板有什么建议，不需要做计划",
            )
            == "qa"
        )

    def test_active_task_wins_over_previous_intent_for_short_followups(self):
        """上一轮可能只是 analysis 的解释；真正没做完的 rework 不能被 target 漂移盖掉。"""
        assert (
            suggest_intent(
                R("follow_up", "change", "code", "continue_previous"),
                previous_intent="analysis",
                message="开始实施",
                active_intent="rework",
            )
            == "rework"
        )
        assert (
            suggest_intent(
                R("follow_up", "change", "code", "continue_previous"),
                previous_intent="analysis",
                message="修复P2",
                active_intent="rework",
            )
            == "rework"
        )
        assert (
            suggest_intent(
                R("follow_up", "lookup", "execution", "continue_previous"),
                previous_intent="analysis",
                message="b",
                active_intent="coding",
            )
            == "analysis"
        )

    def test_a_short_followup_after_a_review_keeps_review(self):
        assert (
            suggest_intent(
                R("follow_up", "analyze", "pr_review", "continue_previous"),
                previous_intent="coding",
                message="这个",
                active_intent="review",
            )
            == "review"
        )

    def test_a_bare_why_question_is_analysis_even_when_wants_is_lookup(self):
        """「为什么 nacos 之后还要 bootstrap.yaml」是解释，不是 ls 式查资料。"""
        assert (
            suggest_intent(
                R("meta_question", "lookup", "code", "continue_previous"),
                previous_intent="coding",
                message="为什么nacos之后还要bootstrap.yaml？",
            )
            == "analysis"
        )

    def test_why_did_you_not_delegate_still_keeps_the_task(self):
        """「为什么不派」是追问上一轮动作，不是新分析任务。"""
        assert (
            suggest_intent(
                R("meta_question", "analyze", "execution", "continue_previous"),
                previous_intent="coding",
                message="你为什么不派 coder",
                active_intent="rework",
            )
            == "rework"
        )

    def test_a_long_correction_with_a_new_instruction_is_a_change(self):
        """「也不对，你应该先查记忆、再派 coder 执行」是新指令，不要被“不对”拦成 plan。"""
        assert (
            suggest_intent(
                R("meta_question", "change", "execution", "continue_previous"),
                previous_intent="plan",
                message="也不对，你应该直接先查记忆，然后派coder执行，最后总结记忆",
                active_intent="plan",
            )
            == "coding"
        )

    def test_new_task_change_on_pr_is_rework(self):
        assert suggest_intent(R("new_task", "change", "pr_change")) == "rework"

    def test_new_task_change_on_code_is_coding(self):
        assert suggest_intent(R("new_task", "change", "code")) == "coding"

    def test_new_task_analyze_on_code_is_analysis(self):
        assert suggest_intent(R("new_task", "analyze", "code")) == "analysis"

    def test_new_task_lookup_on_code_is_qa(self):
        assert suggest_intent(R("new_task", "lookup", "code")) == "qa"

    def test_new_task_decide_on_plan_is_plan(self):
        assert suggest_intent(R("new_task", "decide", "plan")) == "plan"

    def test_pr_status_is_qa_not_review(self):
        assert suggest_intent(R("new_task", "lookup", "pr_status")) == "qa"

    def test_chat_is_qa(self):
        assert suggest_intent(R("chat", "unknown", "general")) == "qa"


class TestRoute:
    async def test_a_fake_model_is_parsed(self):
        model = GenericFakeChatModel(
            messages=iter(
                [
                    AIMessage(
                        content='{"speech_act":"new_task","wants":"change",'
                        '"target":"pr_change","continuation":"new"}'
                    )
                ]
            )
        )

        got = await route(model, "修复 PR #13", previous_intent="rework")

        assert got == R("new_task", "change", "pr_change", "new")

    async def test_a_broken_reply_is_none(self):
        model = GenericFakeChatModel(messages=iter([AIMessage(content="我觉得是 coding")]))

        assert await route(model, "随便") is None

    async def test_a_broken_reply_is_retried_once(self):
        model = GenericFakeChatModel(
            messages=iter(
                [
                    AIMessage(content="我觉得是 coding"),
                    AIMessage(
                        content='{"speech_act":"new_task","wants":"lookup",'
                        '"target":"code","continuation":"new"}'
                    ),
                ]
            )
        )

        got = await route(model, "有哪些功能")

        assert got == R("new_task", "lookup", "code", "new")
