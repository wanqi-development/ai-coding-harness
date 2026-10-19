"""意图判定的行为守卫：口令直通 → （JEV **或** 大模型，二选一）→ 回落关键字。

不联网：分类模型用假实现（回预设文本、可抛错、可拖时间）。

为什么这些边界必须钉住：
- ``sync`` 会走**直接执行**路径（不经智能体、直接在沙箱跑命令），
  只能由关键字产出 —— 模型猜出一个直接执行，后果不是「这轮少条指令」。
- JEV 是**外部服务**（SystemOne）：**严格二选一**（2026-10-15 用户口径）——
  选了 JEV 就只有 JEV，它挂了 / 没配齐 / 回了认不出的标签都落关键字口径，**不借大模型**；
  选了「大模型」就整个不碰 JEV。
- 分类失败绝不能变成新的故障点：任何异常都要退回升级前的关键字口径。
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from ai_coding_harness.agent import middleware
from ai_coding_harness.config import Settings
from ai_coding_harness.server import service as service_mod
from ai_coding_harness.server.intent import (
    DEFAULT_INTENT,
    DIRECT_INTENTS,
    PREVIOUS_REPLY_CHARS,
    PREVIOUS_REPLY_SLACK,
    SOURCE_CLARIFY,
    SOURCE_FALLBACK,
    SOURCE_JEV,
    SOURCE_LLM,
    TASK_TYPES,
    IntentDecision,
    ParsedReply,
    _system_prompt,
    _user_prompt,
    active_task_override,
    clarification_decision,
    classify,
    detect_intent,
    match_strong,
    parse_reply,
    should_clarify,
    tail_excerpt,
)

#: 测试里的 ``detect_intent``：**未显式给 mode 时按桩件推断** —— 传了 ``jev`` 就当在验 JEV
#: 那一侧，否则当在验大模型那一侧。
#:
#: 为什么需要这一层：严格二选一（2026-10-15）之后 ``mode`` 默认是 ``jev``，而本文件里绝大多数
#: 用例验的是**大模型**那一关（假分类器）—— 不显式选它就会直接落关键字口径，用例全红。
#: 显式传了 ``mode=`` / ``force_llm=True`` 的一律照传，不受影响。
_real_detect_intent = detect_intent


async def detect_intent(
    message: str,
    *,
    mode: str | None = None,
    jev: Any = None,
    **kw: Any,
) -> Any:
    return await _real_detect_intent(
        message, mode=mode or ("jev" if jev is not None else "llm"), jev=jev, **kw
    )


class _FakeClassifier(BaseChatModel):
    """假分类模型：记录调用与提示词，返回预设回复（可抛错 / 可拖时间）。"""

    reply: str = "qa\n没看懂"
    error: str = ""
    delay: float = 0.0
    calls: int = 0
    prompts: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "fake-classifier"

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        self.calls += 1
        self.prompts.append("\n".join(message.text for message in messages))
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise RuntimeError(self.error)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])


class TestStrongKeywordShortCircuits:
    """口令级命中直接定案 —— 判定要零延迟、零成本、可复现。"""

    async def test_direct_intents_come_only_from_keywords(self):
        model = _FakeClassifier(reply="coding")
        for message, expected in (
            ("同步一下仓库", "sync"),
            ("git pull", "sync"),
        ):
            decision = await detect_intent(message, model=model)
            assert decision.intent == expected, message
            assert decision.source == "keyword"
        assert model.calls == 0, "口令命中还调模型就是白花钱"

    async def test_act_phrases_are_now_keywords(self):
        """「实施」「按这个方案做」「提 PR」原来都不在词表里，自然措辞只能掉进 qa。"""
        model = _FakeClassifier(reply="qa")
        for message, expected in (
            ("开始实施", "coding"),
            ("按这个方案做吧", "coding"),
            ("你提个 PR", "coding"),
            ("先出个方案", "plan"),
            ("帮我代码审查一下", "review"),
        ):
            decision = await detect_intent(message, model=model)
            assert decision.intent == expected, message
        assert model.calls == 0

    async def test_excludes_still_beat_the_keyword(self):
        """排除词先否决规则，再谈关键词 —— 「同步机制」不是同步仓库。"""
        model = _FakeClassifier(reply="qa")
        decision = await detect_intent("讲讲数据同步的原理", model=model)

        assert match_strong("讲讲数据同步的原理") is None
        assert model.calls == 1, "被排除后应交给模型判"
        assert decision.source == "llm"

    async def test_rework_phrases_are_keywords_now(self):
        """「按评审意见改」原先两条路都走岔：判成 coding（接着撞 PR 闸门 —— 要改的就是那份
        开着的 PR）或判成 review（跑去审，不改）。这两句都得是口令级的 rework。"""
        model = _FakeClassifier(reply="qa")
        for message in (
            "按评审意见修改一下",
            "根据审查结论把代码改了",
            "按审查意见把第 2 点改掉",
            "修复评审意见里的第 3 条",
            "先把评审意见里提到的问题返工一遍",
        ):
            decision = await detect_intent(message, model=model)
            assert decision.intent == "rework", message
            assert decision.source == "keyword", message
        assert model.calls == 0, "口令命中还调模型就是白花钱"

    async def test_review_commands_are_not_taken_over_by_rework(self):
        """新意图排在 review **之前**，但不能把 review 的活抢走：「评审 / 审查」仍是审查的
        口令，查现状与下审查命令的句子都该留在 review（rework 形态带写工具）。"""
        model = _FakeClassifier(reply="qa")
        for message in ("帮我审查一下这个 PR", "看看有哪些评审意见", "代码审查一下"):
            decision = await detect_intent(message, model=model)
            assert decision.intent == "review", message
        assert model.calls == 0


class TestReworkNeedsTheModelForNaturalPhrasing:
    """「评审提的…」这类**引用别人的审查输出**的句子，关键字层不定案，交给模型。

    钉这条是因为两个方向的误判都出现过：「评审提的几个问题都修一下」原先判成 review
    （跑去审、不改），而「看看有哪些评审意见」是查现状、必须留在 review。两句只差一个字，
    子串匹配分不开，所以关键字只给线索（``rework(评审提)``），由模型按分界规则判 ——
    ``_BOUNDARY`` 里为此补了 rework vs coding / rework vs review 两条。
    """

    async def test_the_keyword_layer_steps_aside(self):
        for message in ("评审提的几个问题都修一下", "看看评审提了哪些意见"):
            assert match_strong(message) is None, message

    async def test_the_hint_reaches_the_model(self):
        model = _FakeClassifier(reply="rework\n按评审意见改")
        await detect_intent("评审提的几个问题都修一下", model=model)
        assert "rework(评审提)" in model.prompts[0]

    async def test_the_model_can_pick_rework(self):
        model = _FakeClassifier(reply="rework\n按评审意见改")
        decision = await detect_intent("评审提的几个问题都修一下", model=model)
        assert decision.intent == "rework"
        assert decision.source == SOURCE_LLM

    async def test_the_specs_and_the_boundary_lines_explain_it(self):
        model = _FakeClassifier(reply="qa")
        await detect_intent("随便说点什么", model=model)
        prompt = model.prompts[0]
        assert "rework：" in prompt
        assert "rework 与 coding 的分界" in prompt
        assert "rework 与 review 的分界" in prompt

    def test_the_model_is_never_offered_a_direct_intent(self):
        """rework 是普通意图，不碰「直接执行」那条线。"""
        assert "rework" not in DIRECT_INTENTS

    def test_the_fallback_keeps_both_sides_straight(self):
        """分类模型不可用时的兜底口径：带修正意味的引用句落到 rework，纯查看那句留在 review。"""
        assert classify("评审提的几个问题都修一下") == "rework"
        assert classify("看看有哪些评审意见") == "review"

    def test_the_new_intent_is_a_task_type_and_parsable(self):
        assert "rework" in TASK_TYPES
        assert parse_reply("rework\n按评审意见改") == ParsedReply(
            "rework", "按评审意见改"
        )


class TestRefreshingMemoryIsAnAnalysisRound:
    """「刷新仓库记忆」**不新开意图**，归 ``analysis``（2026-09-27 用户口径）。

    ``analysis`` 形态本来就有 `get_pr_info`（拉 PR 列表）与 `save_repo_memory`（写回），
    而且不在记忆闸门里 —— 刷新记忆恰恰发生在仓库还有未合入 PR 的时候。钉三条：

    - 口令直通到 ``analysis``（别落到 qa：那一形态没有写记忆的工具，2026-09-27 实测它拉了
      列表却写不进去，想交接还被「重判仍是本形态」挡回来）；
    - 带「同步」的写法（「同步一下记忆」）**不能**被 ``sync`` 的**直接执行**路径抓走 ——
      那条路不经智能体，记忆一个字都不会被动；
    - 只是**查看**记忆内容仍归 ``qa``（那一轮不需要写工具）。
    """

    async def test_the_explicit_phrases_are_keywords(self):
        model = _FakeClassifier(reply="qa")
        for message in (
            "刷新仓库记忆",
            "帮我刷新一下仓库记忆",
            "更新仓库记忆",
            "同步仓库记忆",
            "整理仓库记忆",
        ):
            decision = await detect_intent(message, model=model)
            assert decision.intent == "analysis", message
            assert decision.source == "keyword", message
        assert model.calls == 0, "口令命中还调模型就是白花钱"

    def test_the_sync_phrasings_do_not_land_on_the_direct_path(self):
        """「同步…记忆」不能被 sync 抓走：那条路不经智能体，记忆不会被刷新。"""
        assert match_strong("同步一下记忆") == "analysis"
        assert match_strong("同步一下仓库记忆") == "analysis"
        assert classify("同步一下仓库记忆") == "analysis"
        # 「同步仓库」本体仍是直接执行那条路，没被顺手改掉
        assert match_strong("同步仓库") == "sync"

    def test_a_repo_sync_is_still_a_repo_sync(self):
        """排除词只挡「提到记忆」的那些：正常同步与「记忆」无关，照旧。"""
        assert match_strong("同步代码") == "sync"
        assert match_strong("拉取代码") == "sync"

    def test_looking_at_the_memory_is_still_qa(self):
        """查看记忆这类句子**不该被词表抢走**（更新记忆才是 analysis）—— 词表不命中，
        判定器可用时由 JEV/大模型判成 qa；两个模型都不在时落到默认值（``coding``，
        2026-09-27 起；此前是 ``qa``）。"""
        for message in ("仓库记忆里怎么说的", "记忆里有哪些 PR", "看看仓库记忆"):
            assert match_strong(message) is None, message
            assert classify(message) == DEFAULT_INTENT, message

    def test_no_new_intent_was_added(self):
        """它就是 ``analysis``：任务类型表里不该多出一个意图来。"""
        assert "refresh_memory" not in TASK_TYPES
        assert "analysis" in TASK_TYPES and "analysis" not in DIRECT_INTENTS

    async def test_the_model_is_told_the_boundary(self):
        """自然措辞（「把 Gitee 上的 PR 补进记忆」）走 LLM 那一档，分界要写进提示词。"""
        model = _FakeClassifier(reply="qa")
        await detect_intent("把 Gitee 上还没记的 PR 补进仓库记忆", model=model)

        prompt = model.prompts[0]
        assert "analysis：" in prompt
        assert "analysis 与 qa 的第二条分界" in prompt


class TestNegatedCommandsAreNotCommands:
    """词表不认否定，于是「别改代码」自己成了「改代码」的口令。

    实测样本：「请给这个仓库出一个增加 GET /hello 接口的方案，先不要动手改代码。」
    命中 ``动手改`` 与 ``改代码``，两个都在否定句里 —— 判成 coding，而且因为命中的是
    **强**关键字，连 LLM 都没问过。实际把它交给模型，模型判的是 plan。
    """

    def test_negative_phrases_do_not_match(self):
        for message in (
            "先不要动手改代码",
            "先别改代码",
            "不要提 PR",
            "无需动手改",
            "先不用同步仓库",
        ):
            assert match_strong(message) is None, message

    def test_a_real_command_is_not_swallowed_by_a_nearby_negation(self):
        """窗口只有 4 个字：「不要动 A，动手改 B」里后一个口令离否定词够远。"""
        assert match_strong("不要动 A，动手改 B") == "coding"

    def test_plain_commands_still_match(self):
        for message, expected in (
            ("动手改一下", "coding"),
            ("开始改代码", "coding"),
            ("同步仓库", "sync"),
        ):
            assert match_strong(message) == expected, message

    def test_the_keyword_fallback_agrees(self):
        """回落口径用的是同一份词表，否定守卫也得管住它。"""
        assert classify("先不要动手改代码") == DEFAULT_INTENT, "词表不命中 → 默认值"
        assert classify("动手改一下") == "coding"

    async def test_the_model_gets_asked_instead(self):
        model = _FakeClassifier(reply="plan\n要方案，不是要改代码")
        decision = await detect_intent("先不要动手改代码", model=model)

        assert model.calls == 1, "被否定的口令不该直通，得交给模型"
        assert decision.intent == "plan"
        assert decision.source == "llm"


class TestModelDecidesTheAmbiguous:
    """泛词与自然措辞交模型：命中弱关键字也不直通。"""

    async def test_weak_keyword_goes_to_the_model(self):
        model = _FakeClassifier(reply="qa\n只是随口一评")
        decision = await detect_intent("这个功能挺有意思的", model=model)

        assert model.calls == 1, "弱关键字命中也要交模型，否则歧义词永远判不准"
        assert decision.intent == "qa"
        assert decision.source == "llm"
        assert decision.reason == "只是随口一评"

    async def test_these_cases_used_to_fall_into_qa(self):
        """回归样本：自然措辞原来一律判成 qa，门禁从不触发。"""
        for message, history, expected in (
            ("你先把步骤列一下", (), "plan"),
            ("登录那块昨天说的就按现在这样弄", ("改懒刷新就行",), "coding"),
            ("把上面那个也一起处理了", ("方案我同意，先改 service.ts",), "coding"),
            ("这段代码为什么要在循环里加锁", (), "analysis"),
        ):
            model = _FakeClassifier(reply=expected)
            decision = await detect_intent(message, history=history, model=model)
            assert decision.intent == expected, message

    async def test_history_and_hints_reach_the_prompt(self):
        model = _FakeClassifier(reply="coding\n按上轮方案实施")
        await detect_intent(
            "把那个也加上", history=["方案我同意，先改 service.ts"], model=model
        )

        prompt = model.prompts[0]
        assert "方案我同意，先改 service.ts" in prompt, "指代要靠历史才判得出来"
        assert "把那个也加上" in prompt
        assert "关键字线索" in prompt and "coding(加上)" in prompt

    async def test_direct_intents_are_not_offered_to_the_model(self):
        """sync 连选项都不给模型 —— 从源头堵死「猜出个直接执行」。"""
        model = _FakeClassifier(reply="qa")
        await detect_intent("随便说点什么", model=model)

        prompt = model.prompts[0]
        for name in DIRECT_INTENTS:
            assert name not in prompt


class TestIntentSpecsDrawTheQaBoundary:
    """qa 曾只写「基础问题（概念、用法、原理、环境、工具的提问）」，全是知识问答。

    「给我看看最近没关闭的 PR」「代码是合并了还是还在 PR 里」这类**查仓库现状**的请求因此
    无处可去，被判成 review —— 而 review 形态的主控带着 `comment_pr`（写工具），一次查看
    就被配上了回评权限。现在 qa 明确覆盖「仓库相关信息的查看」，review 收紧成「只做审查」。
    """

    async def _prompt(self) -> str:
        model = _FakeClassifier(reply="qa")
        await detect_intent("随便说点什么", model=model)
        return model.prompts[0]

    async def test_qa_covers_looking_things_up_in_the_repo(self):
        prompt = await self._prompt()
        assert "对**已有数据**做问答" in prompt
        for item in ("已具备哪些功能", "有哪些 PR", "文件", "提交", "记忆"):
            assert item in prompt, item
        assert "计划、方案、任务完成度一律归 plan" in prompt

    async def test_qa_still_answers_plain_questions(self):
        prompt = await self._prompt()
        assert "基础问题" in prompt
        for item in ("概念", "用法", "原理", "环境", "工具"):
            assert item in prompt, item

    async def test_review_is_scoped_to_reviewing_only(self):
        prompt = await self._prompt()
        assert "只做审查" in prompt
        assert "不改代码" in prompt

    async def test_the_old_narrow_wording_is_gone(self):
        prompt = await self._prompt()
        assert "代码审查 / PR 评审 / 对已提的 PR 回评" not in prompt
        assert "基础问题（概念、用法、原理、环境、工具的提问）" not in prompt


class TestPlanVsCodingBoundaryIsSpelledOut:
    """回归样本：「请给这个仓库出一个增加 GET /hello 接口的方案，先不要动手改代码」判成了 coding。

    模型看见句子里有「增加…接口」就往 coding 走。光列意图说明治不了 —— coding 的说明里
    本来就写着「写、改」，所以要把分界写成规则：看这一轮要的交付物，不看提到了什么功能。
    """

    async def _prompt(self) -> str:
        model = _FakeClassifier(reply="qa")
        await detect_intent("随便说点什么", model=model)
        return model.prompts[0]

    async def test_the_rule_looks_at_the_deliverable_not_the_nouns(self):
        prompt = await self._prompt()
        assert "不看句子里提到了什么功能" in prompt
        assert "提到了要做什么功能，不改变这个判断" in prompt

    async def test_plan_variants_are_listed(self):
        prompt = await self._prompt()
        for item in ("出一个…方案", "帮我规划一下", "先说怎么做", "列个步骤", "先不要动手改代码"):
            assert item in prompt, item

    async def test_plan_covers_the_whole_crud_set(self):
        """方案的**增删改查都归 plan 这一档**（2026-10-12 用户口径）。

        只有 plan 形态的主控手里有 getPlan / updatePlan / updatePlanSteps / deletePlan；
        coding 形态对方案只读 —— 让它去改方案，只能让 planner 另存一份（实测就是这么造出
        plan 42 / 43 两份"修复版"的）。所以判据里必须把四件事都写上，而不是只说「要方案」。
        """
        prompt = await self._prompt()

        for item in ("出方案", "查方案", "改方案", "删方案"):
            assert item in prompt, item
        assert "方案这个对象本身的增删改查" in prompt

    async def test_the_crud_boundary_names_what_is_not_plan(self):
        prompt = await self._prompt()

        assert "查仓库里的文件 / PR / 提交归 qa" in prompt
        assert "按方案去改代码、提 PR 归 coding" in prompt
        assert "只管方案文本本身" in prompt

    async def test_coding_variants_are_listed(self):
        prompt = await self._prompt()
        for item in ("按这个方案做", "直接改了吧", "改完提个 PR"):
            assert item in prompt, item


class TestFailureFallsBack:
    """分类是增强，不是新的故障点：出任何问题都退回关键字口径。"""

    async def test_unrecognised_reply_uses_keywords(self):
        model = _FakeClassifier(reply="我也说不好")
        decision = await detect_intent("修复登录报错", model=model)

        assert decision.source == "fallback"
        assert decision.intent == "coding", "弱关键字「修复」应兜住"
        assert decision.reason

    async def test_model_cannot_smuggle_a_direct_intent(self):
        model = _FakeClassifier(reply="sync\n猜的")
        decision = await detect_intent("把那个东西弄一下", model=model)

        assert decision.source == "fallback"
        assert decision.intent not in DIRECT_INTENTS

    async def test_error_falls_back(self):
        model = _FakeClassifier(error="网关 502")
        decision = await detect_intent("修复登录报错", model=model)

        assert decision.source == "fallback"
        assert decision.intent == "coding"
        assert "网关 502" in decision.reason

    async def test_timeout_falls_back_with_a_reason(self):
        model = _FakeClassifier(reply="coding", delay=0.5)
        decision = await detect_intent("随便说点什么", model=model, timeout=0.05)

        assert decision.source == "fallback"
        assert decision.intent == DEFAULT_INTENT, "词表不命中 → 默认值"
        assert decision.reason, "TimeoutError 的 str 是空的，理由不能也跟着空"

    async def test_without_a_model_it_is_keyword_only(self):
        decision = await detect_intent("修复登录报错", model=None)

        assert decision.intent == "coding"
        assert decision.source == "keyword"


class TestKeywordFallbackIsUnchanged:
    """回落口径就是升级前的老行为，词表一个都不能丢。

    注意最后几条：它们**没有**命中任何关键字，判的就是默认值 —— 2026-09-27 起默认值是
    ``coding``（此前 ``qa``）。判定器在的时候这类句子由 JEV/大模型判成 qa（别处的用例钉着），
    只有「两个模型都没配 / 都失败」时才会看到这里的结论。
    """

    def test_every_old_keyword_still_lands(self):
        for message, expected in (
            ("同步仓库", "sync"),
            ("git clone 一下", "sync"),
            ("代码审查", "review"),
            ("review 一下这个 diff", "review"),
            ("出个方案", "plan"),
            ("看看仓库记忆", "coding"),
            ("修复这个 bug", "coding"),
            ("帮我重构一下", "coding"),
            ("为什么这里会超时", "analysis"),
            ("随便聊聊", "coding"),
            ("这个功能怎么实现", "coding"),
            ("讲讲设计模式", "coding"),
        ):
            assert classify(message) == expected, message

    def test_a_meta_question_is_not_analysis_by_the_bare_why(self):
        """「为什么不派 coder」是追问上一轮动作，不是新的排查任务。"""
        assert classify("你为什么不派coder执行") != "analysis"


class TestInspectIsGoneEntirely:
    """inspect 曾是个「直接执行」意图：固定 `ls -la` + 读仓库记忆，模型完全不参与。

    「看下工作区」「有哪些项目」这类问题其实要解释，qa 形态的本就有 ls / grep / read_file，
    答得比固定模板好。撤掉直接执行后最要紧的是**别再有任何一条路产出这个名字** ——
    ``profile_for`` 对未知意图兜底成全量装配（create_pr + 三个子智能体），一个残留的
    「inspect」会静默挂上一整套编码权限。
    """

    def test_direct_execution_is_down_to_sync(self):
        assert DIRECT_INTENTS == frozenset({"sync"})

    def test_the_name_is_not_a_task_type(self):
        assert "inspect" not in TASK_TYPES

    def test_the_old_keywords_no_longer_short_circuit(self):
        for message in ("看下工作区", "列出文件", "检查工作区", "仓库记忆里是怎么说的"):
            assert match_strong(message) is None, message
            assert classify(message) == DEFAULT_INTENT, message

    async def test_workspace_questions_now_go_to_the_model(self):
        model = _FakeClassifier(reply="qa\n查仓库现状")
        decision = await detect_intent("看下工作区", model=model)

        assert model.calls == 1, "撤掉口令后要交模型判，而不是落回固定模板"
        assert decision.intent == "qa"
        assert decision.source == "llm"


class TestForceLlmRejudges:
    """``force_llm``：跳过口令层，只认模型 —— ``handoffTask`` 的重判走这条。

    要纠正的常常正是口令层。2026-09-21 那轮 686 秒就是这么来的：请求本是「别给方案了，
    直接改」，口令层却被早先一句话钉成了 plan —— 形态手上没有 ``create_pr``，模型只能
    复述一遍方案再问用户要不要实施。重判要是还看口令，就永远翻不了案。
    """

    async def test_strong_keywords_no_longer_decide(self):
        """命中强关键字也照问模型 —— 这就是 ``force_llm`` 的全部意义。"""
        model = _FakeClassifier(reply="plan\n他其实是要方案")
        assert match_strong("开始实施") == "coding", "先确认这条平时是口令直通的"

        decision = await detect_intent("开始实施", model=model, force_llm=True)

        assert model.calls == 1, "跳过口令层等于必然问模型"
        assert decision.intent == "plan"
        assert decision.source == SOURCE_LLM

    async def test_it_never_reports_the_keyword_source(self):
        """来源里不能出现 ``keyword``：调用方靠它区分「重判过了」与「只是把口令复读一遍」。"""
        model = _FakeClassifier(reply="coding\n要改代码")
        for message in ("同步仓库", "开始实施", "代码审查"):
            decision = await detect_intent(message, model=model, force_llm=True)
            assert decision.source == SOURCE_LLM, message

    async def test_without_a_model_it_is_a_fallback_not_a_keyword_hit(self):
        """模型不可用时回落，**且如实标 fallback** —— 这不是一次重判，别让它冒充。

        注意 ``intent`` 仍会命中词表（``sync`` 是直接执行意图）：拦住它的是**来源**，
        不是词表。这条正是 ``handoffTask`` 只认 ``SOURCE_LLM`` 的原因。
        """
        decision = await detect_intent("同步仓库", model=None, force_llm=True)

        assert decision.source == SOURCE_FALLBACK
        assert decision.intent == "sync"

    async def test_a_direct_intent_cannot_be_smuggled_in_either(self):
        """模型自己猜一个 ``sync`` 出来也不行（``_LLM_INTENTS`` 里没有它）。"""
        model = _FakeClassifier(reply="sync\n猜的")
        decision = await detect_intent("随便说点什么", model=model, force_llm=True)

        assert decision.source == SOURCE_FALLBACK
        assert decision.intent not in DIRECT_INTENTS

    async def test_error_and_timeout_still_fall_back(self):
        """出错与超时在两条路上是同一套口径 —— 重判也绝不能变成新的故障点。"""
        for model in (
            _FakeClassifier(error="网关 502"),
            _FakeClassifier(reply="coding", delay=0.5),
        ):
            decision = await detect_intent(
                "随便说点什么", model=model, timeout=0.05, force_llm=True
            )
            assert decision.source == SOURCE_FALLBACK
            assert decision.reason, "理由不能是空的"

    async def test_history_still_reaches_the_prompt(self):
        """重判也在指代语境里判：「就按你说的改」离了历史什么都判不出来。"""
        model = _FakeClassifier(reply="coding\n按上轮方案实施")
        await detect_intent(
            "就按你说的改", history=["先出个方案"], model=model, force_llm=True
        )
        assert "先出个方案" in model.prompts[0]

    async def test_the_default_path_is_unchanged(self):
        """不传 ``force_llm`` 时行为一字不变：口令层照旧直通、模型一次都不问。"""
        model = _FakeClassifier(reply="plan")
        decision = await detect_intent("开始实施", model=model)

        assert decision.intent == "coding" and decision.source == "keyword"
        assert model.calls == 0


class TestParseReply:
    def test_first_word_wins(self):
        assert parse_reply("plan\n用户还没动手") == ParsedReply(
            "plan", "用户还没动手"
        )

    def test_decorations_are_stripped(self):
        for text in ("`coding`", "PLAN。", "coding：\n理由"):
            assert parse_reply(text) is not None, text

    def test_only_the_first_reason_line_is_kept(self):
        assert parse_reply("qa\n第一行\n第二行") == ParsedReply("qa", "第一行")

    def test_four_lines_carry_confidence_and_candidates(self):
        got = parse_reply(
            "plan\n0.42\nplan 0.42, coding 0.31, qa 0.27\n用户还在讨论方案"
        )

        assert got is not None
        assert got.intent == "plan"
        assert got.confidence == 0.42
        assert got.candidates[:2] == (("plan", 0.42), ("coding", 0.31))
        assert got.reason == "用户还在讨论方案"

    def test_three_lines_without_candidates_keep_the_reason(self):
        got = parse_reply("qa\n0.40\n需要先确认范围")

        assert got is not None
        assert got.confidence == 0.40
        assert got.candidates == ()
        assert got.reason == "需要先确认范围"

    def test_a_placeholder_candidate_line_keeps_the_fourth_line_as_reason(self):
        got = parse_reply("qa\n0.40\n-\n需要先确认范围")

        assert got is not None
        assert got.candidates == ()
        assert got.reason == "需要先确认范围"

    def test_unknown_word_is_rejected(self):
        for text in ("", "我也说不好", "sync\n猜的", "plan_and_coding"):
            assert parse_reply(text) is None, text


class TestServiceWiring:
    """service 层：一轮只判一次，且把历史喂进去。"""

    class _FakeDb:
        @asynccontextmanager
        async def session(self):
            yield None

    async def test_one_detection_per_turn_with_history(self, monkeypatch):
        """历史上限取自配置、按会话读一次，然后交给判定 —— 与 ``chat()`` 的顺序一致。

        读历史与判定是两步（``_intent_history`` / ``_detect_intent``）：一次请求里
        它们只该各跑一次，且吃的是同一份历史。
        """
        model = _FakeClassifier(reply="coding\n接着上一轮的方案")
        monkeypatch.setattr(service_mod, "build_classifier_model", lambda settings: model)

        seen: dict[str, Any] = {}

        async def fake_recent(db, *, session_id, limit):
            seen["session_id"] = session_id
            seen["limit"] = limit
            return ["方案我同意，先改 service.ts"][-limit:]

        monkeypatch.setattr(service_mod.dao, "recent_user_summaries", fake_recent)

        svc = service_mod.HarnessService(
            settings=Settings(intent_mode="llm", intent_history_turns=2),
            manager=None,  # type: ignore[arg-type]
            database=self._FakeDb(),  # type: ignore[arg-type]
        )
        session = SimpleNamespace(db_id=7, public_id="p")
        history = await svc._intent_history(session)
        decision = await svc._detect_intent("就按你说的弄吧", history)

        assert model.calls == 1, "一轮只能判一次（中间件里判会变成每次模型调用一次）"
        assert decision.intent == "coding" and decision.source == "llm"
        assert seen == {"session_id": 7, "limit": 2}
        assert "方案我同意，先改 service.ts" in model.prompts[0]

    async def test_history_is_skipped_when_the_window_is_zero(self, monkeypatch):
        """窗口配成 0 就不该去碰库 —— 库连不上时那一跳会白等一个超时。"""
        async def explode(db, *, session_id, limit):
            raise AssertionError("窗口为 0 时不该读历史")

        monkeypatch.setattr(service_mod.dao, "recent_user_summaries", explode)

        svc = service_mod.HarnessService(
            settings=Settings(intent_mode="llm", intent_history_turns=0),
            manager=None,  # type: ignore[arg-type]
            database=self._FakeDb(),  # type: ignore[arg-type]
        )

        assert await svc._intent_history(SimpleNamespace(db_id=7, public_id="p")) == []

    async def test_history_failure_only_degrades(self, monkeypatch):
        """读历史失败只降级成「不带上下文」，不能打断本轮。"""
        async def boom(db, *, session_id, limit):
            raise RuntimeError("库又停了")

        monkeypatch.setattr(service_mod.dao, "recent_user_summaries", boom)

        svc = service_mod.HarnessService(
            settings=Settings(intent_mode="llm", intent_history_turns=2),
            manager=None,  # type: ignore[arg-type]
            database=self._FakeDb(),  # type: ignore[arg-type]
        )

        assert await svc._intent_history(SimpleNamespace(db_id=7, public_id="p")) == []

    async def test_the_previous_reply_is_read_once_with_the_same_window(self, monkeypatch):
        """助手上一轮读一次、取尾部；窗口配成 0 时**不碰库**（与用户历史同一个开关）。"""
        seen: dict[str, Any] = {}

        async def fake_tail(db, *, session_id, chars):
            seen.update(session_id=session_id, chars=chars)
            return "…" * 100 + "。" + "要不要我把方案正文修复一遍再给你确认？"

        monkeypatch.setattr(service_mod.dao, "latest_assistant_tail", fake_tail)
        svc = service_mod.HarnessService(
            settings=Settings(intent_mode="llm", intent_history_turns=4),
            manager=None,  # type: ignore[arg-type]
            database=self._FakeDb(),  # type: ignore[arg-type]
        )

        reply = await svc._last_reply(SimpleNamespace(db_id=7, public_id="p"))

        assert reply is not None
        assert reply.endswith("要不要我把方案正文修复一遍再给你确认？")
        assert seen == {
            "session_id": 7,
            "chars": PREVIOUS_REPLY_CHARS + PREVIOUS_REPLY_SLACK,
        }

    async def test_the_window_zero_skips_the_reply_too(self, monkeypatch):
        async def explode(*_a, **_kw):
            raise AssertionError("窗口为 0 时不该读上一轮回复")

        monkeypatch.setattr(service_mod.dao, "latest_assistant_tail", explode)
        svc = service_mod.HarnessService(
            settings=Settings(intent_mode="llm", intent_history_turns=0),
            manager=None,  # type: ignore[arg-type]
            database=self._FakeDb(),  # type: ignore[arg-type]
        )

        assert await svc._last_reply(SimpleNamespace(db_id=7, public_id="p")) is None

    async def test_a_failed_reply_read_only_drops_that_context(self, monkeypatch):
        """库读不到就只当「没有上一轮」—— 判定与这一轮都照跑。"""
        async def boom(*_a, **_kw):
            raise RuntimeError("库挂了")

        monkeypatch.setattr(service_mod.dao, "latest_assistant_tail", boom)
        svc = service_mod.HarnessService(
            settings=Settings(intent_mode="llm", intent_history_turns=4),
            manager=None,  # type: ignore[arg-type]
            database=self._FakeDb(),  # type: ignore[arg-type]
        )

        assert await svc._last_reply(SimpleNamespace(db_id=7, public_id="p")) is None

    async def test_the_detection_carries_it_into_the_classifier_prompt(self, monkeypatch):
        model = _FakeClassifier(reply="plan\n他在回答上一轮的反问")
        monkeypatch.setattr(service_mod, "build_classifier_model", lambda settings: model)
        svc = service_mod.HarnessService(
            settings=Settings(intent_mode="llm", intent_history_turns=4),
            manager=None,  # type: ignore[arg-type]
            database=self._FakeDb(),  # type: ignore[arg-type]
        )

        await svc._detect_intent("修复吧", [], "要不要我把方案正文修复一遍？")

        assert "要不要我把方案正文修复一遍？" in model.prompts[0]

    async def test_the_rejudge_callback_still_takes_two_arguments(self, monkeypatch):
        """``handoffTask`` 的回调契约是 ``(message, history)``：把 session 与上一轮绑进去
        之后（``chat()`` 用 ``functools.partial`` 就是这么塞进 ``TurnState`` 的），
        仍然按两个参数调用，且重判同样看得到上一轮那句反问。"""
        model = _FakeClassifier(reply="plan\n他在回答上一轮的反问")
        monkeypatch.setattr(service_mod, "build_classifier_model", lambda settings: model)
        svc = service_mod.HarnessService(
            settings=Settings(intent_mode="llm", intent_history_turns=0),
            manager=None,  # type: ignore[arg-type]
            database=self._FakeDb(),  # type: ignore[arg-type]
        )
        callback = partial(svc._rejudge_intent, session=None, previous_reply="要不要我修？")

        decision = await callback("修复吧", ())

        assert decision.source == SOURCE_LLM
        assert "要不要我修？" in model.prompts[0]

    async def test_disabled_switch_skips_the_model(self, monkeypatch):
        model = _FakeClassifier(reply="coding")
        monkeypatch.setattr(service_mod, "build_classifier_model", lambda settings: model)

        svc = service_mod.HarnessService(
            settings=Settings(intent_mode="llm", intent_llm_enabled=False, intent_history_turns=0),
            manager=None,  # type: ignore[arg-type]
            database=self._FakeDb(),  # type: ignore[arg-type]
        )
        decision = await svc._detect_intent("修复登录报错", [])

        assert model.calls == 0
        assert decision.intent == "coding" and decision.source == "keyword"

    async def test_rejudge_skips_the_keyword_layer(self, monkeypatch):
        """``handoffTask`` 走的就是这条：口令说 coding、模型说 plan —— 得听模型的。

        同一条消息在两条路上给出不同答案，是这套设计成立的前提；一样的话，重判就没有
        任何意义，形态也永远换不掉。
        """
        model = _FakeClassifier(reply="plan\n他要的是方案，不是改代码")
        monkeypatch.setattr(service_mod, "build_classifier_model", lambda settings: model)

        svc = service_mod.HarnessService(
            settings=Settings(intent_mode="llm", intent_history_turns=0),
            manager=None,  # type: ignore[arg-type]
            database=self._FakeDb(),  # type: ignore[arg-type]
        )
        session = SimpleNamespace(db_id=7, public_id="p")

        normal = await svc._detect_intent("开始实施", await svc._intent_history(session))
        rejudged = await svc._rejudge_intent("开始实施", ())

        assert normal.intent == "coding" and normal.source == "keyword"
        assert rejudged.intent == "plan" and rejudged.source == "llm"
        assert model.calls == 1, "常规那次是口令直通，压根没问模型"

    async def test_rejudge_degrades_to_fallback_when_the_model_is_off(self, monkeypatch):
        """分类模型没启用时重判只能是回落 —— 工具那边据此拒绝交接。"""
        svc = service_mod.HarnessService(
            settings=Settings(intent_mode="llm", intent_llm_enabled=False, intent_history_turns=0),
            manager=None,  # type: ignore[arg-type]
            database=self._FakeDb(),  # type: ignore[arg-type]
        )
        decision = await svc._rejudge_intent("开始实施", ())

        assert decision.source == SOURCE_FALLBACK
        assert decision.source != "keyword", "回落与口令直通必须能区分开"

    def test_classification_never_lives_in_the_middleware(self):
        """中间件是每次模型调用跑一遍（一轮 5~10 次），分类绝不能挪进去。

        ``middleware`` 是包，源码散在多个模块里，故整包拼接后再断言 —— 只读
        ``__init__.py`` 的话，逻辑搬进子模块也能测过，这条防线就白设了。
        """
        source = "".join(
            path.read_text(encoding="utf-8")
            for path in sorted(Path(middleware.__file__).parent.glob("*.py"))
        )
        assert "detect_intent" not in source
        assert "build_classifier_model" not in source


class _FakeJev:
    """假 JEV 判定器：回预设结论 / 装死（抛错）/ 记下调用参数。"""

    def __init__(
        self,
        *,
        intent: str | None = "plan",
        confidence: float = 0.9,
        reason: str = "p=0.90",
        candidates: tuple[tuple[str, float], ...] = (),
        error: Exception | None = None,
    ) -> None:
        self.intent = intent
        self.confidence = confidence
        self.reason = reason
        self.candidates = candidates
        self.error = error
        self.calls = 0
        self.histories: list[list[str]] = []
        self.hints: list[list[str]] = []
        #: 最近一次调用拿到的用量累加器（钉「判定那一笔账真的传下来了」）
        self.usage: dict[str, int] | None = None
        #: 最近一次调用拿到的「助手上一轮末尾节选」
        self.previous_reply: str | None = None
        #: 最近一次调用拿到的「上一轮判定」
        self.previous_intent: str | None = None
        #: 最近一次调用拿到的「两轮对话」
        self.dialogue: list[dict[str, str]] = []
        #: 最近一次调用拿到的「未完成任务」
        self.active_intent: str | None = None

    async def decide(
        self,
        message: str,
        *,
        history: Sequence[str] = (),
        hints: Sequence[str] = (),
        usage: dict[str, int] | None = None,
        previous_reply: str | None = None,
        previous_intent: str | None = None,
        active_intent: str | None = None,
        dialogue: Sequence[Mapping[str, str]] = (),
    ) -> tuple[str, str, float | None, tuple[tuple[str, float], ...]] | None:
        self.calls += 1
        self.usage = usage
        self.previous_reply = previous_reply
        self.previous_intent = previous_intent
        self.active_intent = active_intent
        self.dialogue = [dict(turn) for turn in dialogue]
        self.histories.append(list(history))
        self.hints.append(list(hints))
        if self.error is not None:
            raise self.error
        if self.intent is None:
            return None
        return self.intent, self.reason, self.confidence, self.candidates


class TestClarification:
    """低置信度只在 JEV/LLM 两条模型路上触发；口令/关键字/active 不进。"""

    def test_only_low_model_confidence_is_clarified(self):
        assert should_clarify(IntentDecision("qa", SOURCE_JEV, confidence=0.40), 0.55)
        assert not should_clarify(
            IntentDecision("qa", SOURCE_JEV, confidence=0.60), 0.55
        )
        assert not should_clarify(
            IntentDecision("qa", SOURCE_LLM, confidence=None), 0.55
        )
        assert not should_clarify(
            IntentDecision("qa", "keyword", confidence=0.40), 0.55
        )
        assert not should_clarify(
            IntentDecision("qa", SOURCE_JEV, confidence=0.40), 0.0
        )

    def test_low_confidence_becomes_a_qa_clarification_decision(self):
        got = clarification_decision(
            IntentDecision(
                "coding",
                SOURCE_JEV,
                confidence=0.40,
                candidates=(("coding", 0.40), ("plan", 0.31)),
            ),
            0.55,
        )

        assert got is not None
        assert got.intent == "qa"
        assert got.source == SOURCE_CLARIFY
        assert got.needs_clarification is True
        assert got.candidates == (("coding", 0.40), ("plan", 0.31))
        assert (
            clarification_decision(
                IntentDecision("coding", SOURCE_JEV, confidence=0.90), 0.55
            )
            is None
        )

    async def test_jev_candidates_are_kept_for_the_question(self):
        jev = _FakeJev(
            intent="plan",
            confidence=0.40,
            candidates=(("plan", 0.40), ("coding", 0.31), ("qa", 0.27)),
        )

        decision = await detect_intent("这个需求先看一下", jev=jev, mode="jev")

        assert decision.intent == "plan"
        assert decision.confidence == 0.40
        assert decision.candidates[:2] == (("plan", 0.40), ("coding", 0.31))


class TestThePreviousReplyIsPartOfTheContext:
    """用户回一句「修复吧」时，指代对象只在**助手上一轮那句反问**里。

    实测（2026-10-12）：助手结尾问「要不要我把方案正文修复一遍再给你确认？」，用户回
    「修复吧」→ 判定只看这一句，判成 coding（p=0.96），于是走了编码形态去「修方案文本」
    —— 那个形态没有写方案的工具，白折腾 25 分钟、还多造了一份方案。这一组钉住「助手上一轮
    的末尾节选进了判定输入」，两条判定路径（JEV / 大模型）都要看到它。
    """

    def test_a_short_reply_is_returned_as_is(self):
        assert tail_excerpt("要不要我把方案正文修复一遍？") == "要不要我把方案正文修复一遍？"

    def test_the_excerpt_keeps_the_tail_not_the_head(self):
        """取尾部：那句反问在末尾（记录的 summary 是前 200 字，正好会把它切掉）。"""
        text = "开头一堆说明。" + "中" * 400 + "要不要我把方案正文修复一遍再给你确认？"

        got = tail_excerpt(text)

        assert got.endswith("要不要我把方案正文修复一遍再给你确认？")
        assert len(got) <= PREVIOUS_REPLY_CHARS

    def test_the_excerpt_starts_after_a_sentence_end(self):
        """不留下半句：开头那段残句切掉（只在开头那一小段余量里找句末标点）。"""
        text = "甲" * 400 + "。" + "乙" * 350

        got = tail_excerpt(text)

        assert got == "乙" * 350, "从句号之后开始，不留半句"

    def test_a_long_sentence_without_punctuation_is_not_mangled(self):
        """整段没有标点的长句：切开头只会把要说的话一起切没，那就原样留尾部。"""
        assert tail_excerpt("无" * 1000) == "无" * PREVIOUS_REPLY_CHARS

    def test_the_prompt_carries_it_before_the_message(self):
        prompt = _user_prompt(
            "修复吧",
            ["我需要查询下最新的方案"],
            (),
            "…要不要我把方案正文修复一遍再给你确认？",
        )

        assert "助手上一轮回复（末尾节选）" in prompt
        assert "要不要我把方案正文修复一遍再给你确认？" in prompt
        assert prompt.index("助手上一轮回复") < prompt.index("本轮消息")

    def test_no_previous_reply_no_section(self):
        assert "助手上一轮" not in _user_prompt("修复吧", [], ())

    def test_the_system_prompt_tells_the_classifier_how_to_read_short_answers(self):
        assert "回答助手上一轮的那个问题" in _system_prompt()

    async def test_the_jev_gate_gets_the_excerpt(self):
        jev = _FakeJev(intent="plan")

        await detect_intent(
            "修复吧", jev=jev, model=_FakeClassifier(reply="plan"), previous_reply="要不要我修？"
        )

        assert jev.previous_reply == "要不要我修？"

    async def test_the_jev_gate_gets_the_previous_intent(self):
        """追问「为什么不派 coder」时，上一轮意图比字面里的「为什么」可靠。"""
        jev = _FakeJev(intent="rework")

        await detect_intent(
            "你为什么不派 coder 执行", jev=jev, previous_intent="rework"
        )

        assert jev.previous_intent == "rework"

    async def test_the_jev_gate_gets_two_rounds_of_dialogue(self):
        """只看用户消息看不出「上一轮 AI 为什么这么说」，两轮对话要一起给判定器。"""
        jev = _FakeJev(intent="rework")
        dialogue = [
            {"role": "user", "content": "修复 PR 的审查问题"},
            {"role": "assistant", "content": "上一轮 AI 的回复"},
        ]

        await detect_intent("继续", jev=jev, dialogue=dialogue)

        assert jev.dialogue == dialogue

    async def test_the_jev_gate_gets_the_active_task(self):
        jev = _FakeJev(intent="coding")

        await detect_intent("继续吧", jev=jev, active_intent="coding")

        assert jev.active_intent == "coding"

    def test_the_user_prompt_carries_the_previous_intent(self):
        text = _user_prompt(
            "你为什么不派 coder 执行", [], [], previous_intent="rework"
        )

        assert "上一轮判定：rework" in text
        assert "不要仅因为出现「为什么」就判 analysis" in text

    def test_the_user_prompt_renders_two_rounds(self):
        text = _user_prompt(
            "继续",
            [],
            [],
            dialogue=[
                {"role": "user", "content": "用户第一轮"},
                {"role": "assistant", "content": "助手第一轮"},
            ],
        )

        assert "最近两轮对话" in text
        assert "[用户] 用户第一轮" in text
        assert "[助手] 助手第一轮" in text

    def test_the_user_prompt_carries_the_active_task(self):
        text = _user_prompt("继续吧", [], [], active_intent="coding")

        assert "当前未完成任务：coding" in text


class TestActiveTaskOverride:
    """短追问只有在明确继续/短选择时才沿用未完成任务。"""

    def test_continue_inherits_the_active_task(self):
        assert active_task_override("继续吧", "analysis", "coding") == "coding"

    def test_short_fix_after_plan_means_coding(self):
        assert active_task_override("修复吧", "analysis", "plan") == "coding"

    def test_short_selection_inherits_the_active_task(self):
        assert active_task_override("B", "qa", "coding") == "coding"

    def test_a_long_suggestion_is_not_an_implementation_command(self):
        assert (
            active_task_override(
                "短信模板你有什么建议，只需要回答问题即可，不需要做计划",
                "qa",
                "plan",
            )
            is None
        )

    def test_a_question_does_not_inherit(self):
        assert active_task_override("那是不是可以读一部分", "analysis", "coding") is None

    def test_an_execution_intent_is_left_alone(self):
        assert active_task_override("继续吧", "coding", "coding") is None

    async def test_the_llm_gate_gets_the_excerpt_too(self):
        jev = _FakeJev(intent=None)
        model = _FakeClassifier(reply="plan\n先改方案文本")

        # 配置选「大模型」那一档才走得到它（默认的 JEV 三关里，JEV 判不出来就落关键字）
        await detect_intent(
            "修复吧", jev=jev, model=model, previous_reply="要不要我修？", mode="llm"
        )

        assert "要不要我修？" in model.prompts[0]


class TestTheFourGateWaterfall:
    """四关顺序：口令 → JEV → 大模型 → 关键字口径（没命中就是 ``DEFAULT_INTENT``）。

    2026-09-27 用户口径：JEV（SystemOne）替掉原来「LLM 直接判」那一档；默认值同时从
    ``qa`` 改成 ``coding``。顺序错一处就会变味：口令被模型抢走 = 一句「实施」也可能不施工；
    JEV 挂了不往下走 = 这一轮直接没意图。
    """

    def test_the_default_is_coding_now(self):
        assert DEFAULT_INTENT == "coding"
        assert classify("这句话里一个关键字都没有") == "coding"

    async def test_the_keyword_gate_wins_over_jev(self):
        """口令命中就不问模型 —— 既省一次外部调用，也保证「实施」永远是 coding。"""
        jev = _FakeJev(intent="plan")

        decision = await detect_intent("实施", jev=jev, model=_FakeClassifier(reply="qa"))

        assert (decision.intent, decision.source) == ("coding", "keyword")
        assert jev.calls == 0, "口令命中还问 JEV 就是白花钱"

    async def test_jev_decides_when_the_keyword_gate_misses(self):
        jev = _FakeJev(intent="analysis", confidence=0.93, reason="p=0.93")
        model = _FakeClassifier(reply="qa")

        decision = await detect_intent("帮我把这块理一理", jev=jev, model=model)

        assert (decision.intent, decision.source) == ("analysis", SOURCE_JEV)
        assert decision.confidence == 0.93
        assert jev.calls == 1
        assert model.calls == 0, "JEV 判出来了就不该再问大模型"

    async def test_the_jev_gate_gets_the_usage_accumulator(self):
        """调用方给的累加器要**传进 JEV 那一关**：判定花钱，账得有人接住。

        2026-10-12：以前 JEV 把用量记在自己实例上的一个 dict 里，没人读也不 reset ——
        面板里「意图分类」那一项因此少一笔（见 ``tests/test_token_usage.py`` 的对照用例）。
        """
        jev = _FakeJev(intent="analysis")
        acc: dict[str, int] = {}

        await detect_intent("帮我把这块理一理", jev=jev, usage=acc)

        assert jev.usage is acc, "判定器拿到的必须是调用方那一份累加器"

    async def test_a_dead_jev_lands_on_the_keyword_gate(self):
        """三关（2026-10-13 用户口径）：JEV 挂了＝这一关判不出来 → **落关键字口径**，
        不再回退大模型。它仍是「增强不是故障点」：本轮照常跑，只是退到最保守的那一档。"""
        jev = _FakeJev(error=RuntimeError("connection refused"))
        model = _FakeClassifier(reply="plan\n要方案")

        decision = await detect_intent("帮我理一理仓库", jev=jev, model=model)

        assert decision.source == SOURCE_FALLBACK, "落关键字口径（来源如实标 fallback）"
        assert jev.calls == 1
        assert model.calls == 0, "三关：JEV 判不出来不该再问大模型"

    async def test_an_unrecognized_jev_choice_lands_on_the_keyword_gate(self):
        """JEV 回了清单外的标签（服务端换词）同样是「判不出来」→ 落关键字口径。"""
        jev = _FakeJev(intent=None)
        model = _FakeClassifier(reply="review\n只审不改")

        decision = await detect_intent("帮我瞅瞅这个仓库", jev=jev, model=model)

        assert decision.source == SOURCE_FALLBACK
        assert model.calls == 0

    async def test_llm_mode_never_asks_jev(self):
        """配置选「大模型」时，JEV 那一关整个不碰（三关里的第二关换人）。"""
        jev = _FakeJev(intent="plan")
        model = _FakeClassifier(reply="review\n只审不改")

        decision = await detect_intent(
            "帮我瞅瞅这个仓库", jev=jev, model=model, mode="llm"
        )

        assert (decision.intent, decision.source) == ("review", SOURCE_LLM)
        assert jev.calls == 0

    async def test_both_models_failing_lands_on_the_default(self):
        """两个模型都不行 → 回到关键字口径；关键字也不命中就是默认值（coding）。"""
        jev = _FakeJev(error=RuntimeError("jev down"))

        class _Broken(_FakeClassifier):
            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                raise RuntimeError("llm down")

        decision = await detect_intent(
            "帮我把这块理一理", jev=jev, model=_Broken(reply="qa")
        )

        assert decision.intent == DEFAULT_INTENT
        assert decision.source == SOURCE_FALLBACK

    async def test_the_history_and_the_hints_reach_jev(self):
        jev = _FakeJev(intent="coding")

        await detect_intent(
            "顺便把这个功能也加上", history=["上一轮在改商品模块"], jev=jev
        )

        assert jev.histories == [["上一轮在改商品模块"]]
        assert jev.hints and jev.hints[0], "弱词要作为线索交给 JEV（它自己做判断）"

    async def test_force_llm_goes_straight_to_the_model(self):
        """``handoffTask`` 的重判：跳过口令那一关、也**不问 JEV** —— 要纠正的正是口令误判，
        而 JEV 与口令层判的是同一件事（同一套 criteria），再问它一次等于让原来的结论投自己
        一票。所以重判固定走大模型（与 ``mode`` 无关）。"""
        jev = _FakeJev(intent="plan", confidence=0.8)

        decision = await detect_intent(
            "实施", jev=jev, model=_FakeClassifier(reply="qa"), force_llm=True
        )

        assert (decision.intent, decision.source) == ("qa", SOURCE_LLM)
        assert jev.calls == 0

    async def test_without_jev_it_lands_on_the_keyword_gate(self):
        """**严格二选一**（2026-10-15 用户口径）：选了 JEV 但这一侧没配齐（``None``）→
        直接落关键字口径，**不借大模型** —— 要借就等于没做选择。"""
        model = _FakeClassifier(reply="review\n只审不改")

        decision = await detect_intent("帮我瞅瞅", jev=None, model=model, mode="jev")

        assert decision.source == SOURCE_FALLBACK
        assert model.calls == 0, "选了 JEV 就不该去问大模型"

    def test_the_real_jev_decider_builds_against_the_sdk(self):
        """真 SDK 造一次判定器（**不发请求**）：``Choice`` 与客户端能建起来。

        这条钉的是「我按 SDK 文档写的那几个参数名真的对」—— 构造期 5 个 kwarg 错一个，
        线上就是「JEV 那一关永远静默跳过」，而单测（用假判定器）根本看不出来。
        """
        pytest.importorskip("typesafe_sdk")
        from ai_coding_harness.server.intent import JevDecider

        decider = JevDecider(
            api_key="dummy-key",
            base_url="https://example.invalid",
            model="jev-latest",
            timeout=1.0,
        )

        assert decider is not None

    def test_a_missing_key_skips_the_jev_gate(self):
        """没配 key 就是「跳过这一关」，不是启动失败。"""
        from ai_coding_harness.config import Settings
        from ai_coding_harness.server.intent import build_jev_decider

        settings = Settings(
            _env_file=None, typesafe_api_key="", intent_jev_enabled=True
        )
        assert build_jev_decider(settings) is None

        disabled = Settings(
            _env_file=None, typesafe_api_key="dummy", intent_jev_enabled=False
        )
        assert build_jev_decider(disabled) is None
