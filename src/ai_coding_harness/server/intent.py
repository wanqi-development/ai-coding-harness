"""用户意图分析 —— 任务类型定义 + 四关判定（口令 → JEV → 大模型 → 关键字口径）。

四关顺序不可颠倒（见 ``detect_intent``）：

1. **强关键字（口令级）直通**：命中即定，不调任何模型。这类词是明确指令（「出方案」
   「实施」「同步仓库」），没有歧义。``sync`` 只会从这一关产出 —— 它走**直接执行**
   路径（不经智能体、直接在沙箱里跑命令），误判的后果不是「这轮少条指令」，
   所以永远不交给模型猜。
2. **JEV（SystemOne，决策型模型）判定**：口令没命中时问它。把「意图清单 + 分界规则」
   原样搬进一个 ``Choice``（6 个可选意图，**故意不含 sync**），拿回选项、置信度与各候选
   概率 —— 见 ``JevDecider``。这是 2026-09-27 换上的主判定器。
3. **大模型判定**：JEV 不可用（没配 / 超时 / 报错 / 回了认不出的标签）时，退回原来那套
   「让对话模型回一个词 + 一句理由」的判定（``_system_prompt`` + ``parse_reply``）。
4. **回落 ``classify()``**：两个模型都判不出来时退回纯关键字口径（强 + 弱 + 排除词）；
   连关键字也没命中就是 ``DEFAULT_INTENT``。**默认值是 ``coding``**（2026-09-27 用户口径：
   判不出来时按「用户多半是要干活」处理；升级前是 ``qa``）。

**弱关键字**（只作第 2/3 关的「线索」）不单独定案：命中它们的消息恰恰是歧义最大的那批
（现在靠排除词「怎么实现」「设计模式」打补丁就是证据）。它们的作用有二：作为线索写进
判定输入，以及在第 4 关充当兜底依据。

另有 ``force_llm=True`` 的变体：跳过第 1 关直接问判定器，供「智能体自述做不了、
要纠正口令误判」那条路使用（见 ``detect_intent`` 与 ``tools/handoff.py``）。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

# 任务类型（新架构命名）
TASK_TYPES: tuple[str, ...] = (
    "coding",    # 编码：写/改/重构/修复
    "review",    # 代码审查 / PR 评审
    "rework",    # 按评审意见修改：已审出结论，按其中点名的问题改代码
    "plan",      # 计划 / 方案 / 设计
    "qa",        # 提问（默认兜底）
    "analysis",  # 分析 / 排查
    "sync",      # 同步仓库
)

#: 「按评审意见修改」。它要改的就是那份**还开着**的 PR，因此在记忆闸门上是唯一
#: **反向**的一种：有未合入的 PR 才放行、没有就拦下（见 ``middleware/memory.py``）。
#: 装配上与 ``coding`` 共用一份 profile（``agent/profiles.py`` 的 ``FORM_ALIASES``）。
REWORK_INTENT = "rework"

# 可直接执行（不经智能体）的任务类型
DIRECT_INTENTS: frozenset[str] = frozenset({"sync"})

#: 会形成“未完成任务”的意图；analysis/qa 是解释/查看，不覆盖它。
EXECUTION_INTENTS: frozenset[str] = frozenset({"coding", "rework", "plan", "review"})

#: 明确的“未完成轮次”标记：出现这些词时沿用上一轮/未完成任务。
CONTINUE_MARKERS: tuple[str, ...] = (
    "继续", "接着", "修正", "纠正", "重来", "再试一次",
    "为什么不", "为什么没", "不是", "不对",
)

#: 短追问里的动作词：上一轮是 plan 时，这些通常表示实施/修复代码，而不是改方案文本。
SHORT_IMPERATIVES: tuple[str, ...] = (
    "修复", "实施", "执行", "开始", "做", "改", "落地", "上", "来",
)

#: 四关全判不出来时的默认值。**2026-09-27 从 ``qa`` 改成 ``coding``**（用户口径：判不出来
#: 时按「多半是让干活」处理）。注意它同时是 ``classify()`` 的兜底 —— 关键字口径没命中时
#: 就是这个值。
DEFAULT_INTENT = "coding"


@dataclass(frozen=True)
class IntentRule:
    """一条意图规则：命中任一关键词且未命中任何 excludes 才算成立。

    ``strong`` 是口令级词（命中即定），``weak`` 是线索级词（只提示、只兜底）。
    """

    intent: str
    strong: tuple[str, ...] = ()
    weak: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()


# 规则顺序敏感：越具体越靠前，先成立的胜出。
_RULES: tuple[IntentRule, ...] = (
    IntentRule(
        "sync",
        strong=(
            "同步仓库", "同步代码", "拉取代码", "拉代码", "更新代码", "同步一下", "同步",
            "代码拉取到本地", "拉取到本地",
            "git pull", "git clone", "git fetch",
        ),
        weak=("sync",),
        # 「同步」在并发/数据话题里是知识问答，不是同步仓库；带「记忆」的也在说记忆
        # （「同步一下仓库记忆」要的是刷新记忆 → analysis，而 sync 是**直接执行**路径，
        # 不经智能体、被它抓走就再也不会去碰记忆）
        excludes=("同步机制", "同步锁", "同步原理", "同步器", "数据同步", "记忆"),
    ),
    # 「按评审意见改」必须排在 review **之前**：句子里的「评审」「审查」是 review 的强
    # 口令，而 review 的排除词只挡得住「修改 / 修复 / 修正」这几种写法（「改一下」「修一下」
    # 挡不住）。实测：「按评审意见修改一下」原先判成 coding、「根据审查结论把代码改了」
    # 被判成 review（跑去审，不改）。
    IntentRule(
        "rework",
        # 强口令一律**带动词**（怎么改、改哪里），因为「评审 / 审查」本身不是改码的意思
        strong=(
            "按评审意见", "根据评审意见", "按评审结论", "根据评审结论",
            "按审查意见", "根据审查意见", "按审查结论", "根据审查结论",
            "评审意见修改", "评审意见修复", "评审意见改", "评审意见修",
            "审查意见修改", "审查意见修复", "审查意见改", "审查意见修",
            "评审结论修改", "评审结论改", "审查结论修改", "审查结论改",
            "按 review 意见", "按review意见", "review 意见修改", "review意见修改",
            "修复评审意见", "修复审查意见",
            "返工",
        ),
        # 弱线索只作提示与兜底。**不放光秃秃的「评审意见」**：「看看有哪些评审意见」
        # 是查现状，该留在 review —— 放进来会把一次查看变成一次改码（review 形态带
        # `comment_pr` 的教训同形，只是这次会带上写工具）。
        weak=("评审提", "审查提"),
    ),
    IntentRule(
        "review",
        strong=("代码审查", "代码评审", "审查", "评审", "pr 审查", "pr审查"),
        weak=("review", "diff", "pull request"),
        # 「评审提的 / 评审提了 / 评审提出」都是**引用别人的审查输出**，不是在下达审查命令：
        # 这类句子要的是「按它改」（rework）还是「只是看看」（qa），关键字层分不出来，
        # 挡住 review 这一条、交给模型判。
        excludes=("审查报告模板", "修改", "修复", "修正", "评审提", "审查提"),
    ),
    IntentRule(
        "plan",
        strong=(
            "实现方案", "技术方案", "设计方案", "做方案", "出方案", "出个方案",
            "给个方案", "计划", "规划",
        ),
        weak=("plan",),
        # 「设计模式」是知识话题，不是要方案
        excludes=("设计模式",),
    ),
    IntentRule(
        "coding",
        strong=(
            "实施", "动手改", "开始改", "按这个方案做", "按方案做", "写代码", "修改代码",
            "改代码", "提 pr", "提个 pr", "提个pr", "开 pr", "开pr", "提交 pr", "创建 pr",
        ),
        weak=(
            "实现", "开发", "重构", "修复", "新增",
            "功能", "bug", "feature", "改一下", "加上", "去掉", "加一个",
        ),
        # 这些是「问怎么实现」，属于问答而非让你动手写
        excludes=("怎么实现", "如何实现", "实现原理", "功能介绍", "功能是什么", "功能有哪些"),
    ),
    IntentRule(
        "analysis",
        # 「按 Gitee 实况刷新仓库记忆」也归这一档（2026-09-27 用户口径：不新开意图）——
        # 那一形态的工具正好够（`get_pr_info` 拉列表 + `save_repo_memory` 写回），而且是
        # **只读形态、不在记忆闸门里**：刷新记忆恰恰发生在仓库还有未合入 PR 的时候。
        # 必须排在 sync 之后靠 excludes 保住：「同步一下仓库记忆」里的「同步」不是重点，
        # sync 那条靠 excludes 里的「记忆」让开。
        strong=(
            "刷新仓库记忆", "刷新记忆", "刷新一下仓库记忆", "刷新一下记忆",
            "更新仓库记忆", "更新记忆",
            "同步仓库记忆", "同步一下仓库记忆", "同步记忆", "同步一下记忆",
            "整理仓库记忆",
        ),
        weak=("分析", "排查", "定位", "为什么", "原因", "根因", "怎么回事"),
        # 「你为什么不派 coder」「为什么没改代码」是在追问上一轮的动作，不是新的分析
        # 任务；裸的「为什么」留给「为什么这里会超时」这类真正的排查问题。
        excludes=("分析报告模板", "为什么不", "为什么没", "为什么没有", "怎么没"),
    ),
)


@dataclass(frozen=True)
class IntentSpec:
    """给分类模型看的意图说明（意图清单只有这一份；判定要点另见 ``_BOUNDARY``）。"""

    intent: str
    summary: str


# 只列 LLM 可以判定的意图：sync 是直接执行路径，必须由关键字独占，
# 所以根本不告诉模型有它 —— 从源头堵死「LLM 猜出个直接执行」。
_INTENT_SPECS: tuple[IntentSpec, ...] = (
    # plan = **方案这个对象本身**（planner 那一档）。2026-10-12 用户口径：方案的增删改查都要落到
    # 这一档 —— 只有 plan 形态的主控手里有 getPlan / updatePlan / updatePlanSteps / deletePlan；
    # coding 形态对方案是只读的（`get_unexecuted_plan`），它想改方案只能让 planner 另存一份
    # （实测就是这么造出 plan 42 / 43 两份"修复版"的，白折腾 25 分钟）。
    IntentSpec(
        "plan",
        "方案这个对象本身的增删改查（都还没动代码）："
        "出方案 —— 要计划 / 设计 / 步骤；"
        "查方案 —— 最新的是哪一份、某份方案的正文与步骤、某一步写了什么、"
        "任务/计划完成了多少；"
        "改方案 —— 改正文 / 改标题 / 换步骤、修掉方案里的乱码、过时或重复的内容；"
        "删方案 —— 删掉 / 作废某一份方案。"
        "常说的样子：「出个方案」「最新那份方案是什么」「把方案里的第 3 步改掉」"
        "「方案正文有乱码，修一下」「删掉那份方案」。"
        "**只管方案文本本身**：一旦要动代码（按方案实施、改代码、提 PR）就是 coding。",
    ),
    # 2026-10-20 用户口径：**需求讨论归 qa**。句子里带功能名词、在补需求、在问「这样做
    # 行不行」，都不是 coding —— coding 的判据是有没有让动手。
    IntentSpec(
        "coding",
        "要写、改、修、重构代码，或按已定方案实施。"
        "**讨论需求本身不是 coding**（要不要做、做成什么样、补什么条件、换个说法）——"
        "那是 qa。",
    ),
    # 收紧成「只做审查」：原先只写「PR 评审」，「给我看看有哪些未关闭的 PR」这类**查现状**
    # 的请求就落到这里了 —— 而 review 形态的主控带 `comment_pr`（写工具），一次查看被配上
    # 了回评权限。现在明说它不改代码、不出方案，查看类请求归 qa。
    IntentSpec(
        "review",
        "对已有的 PR 或代码改动做审查、给结论、回评（只做审查：不改代码、不出方案）",
    ),
    # review 的**下游**：审查结论已经在那儿了，用户点着其中几条让你改。它改的是那份还开着的
    # PR，所以既不是 review（那只要结论）也不是 coding（那是按新需求 / 按方案开工）。
    IntentSpec(
        "rework",
        "已有 PR 上审出的意见 / 结论，要按其中某些问题修改代码（改的是那份还开着的 PR）",
    ),
    # 「按 Gitee 实况刷新仓库记忆」也在这一档（2026-09-27）：它要 `get_pr_info` 拉列表 +
    # `save_repo_memory` 写回，正好是 analysis 形态的工具子集；与 qa 的分界是「看一眼」
    # 还是「要求更新」（见 ``_BOUNDARY``）。
    IntentSpec(
        "analysis",
        "分析现有代码、排查故障原因、定位根因，或**要求更新仓库记忆**"
        "（把 Gitee / GitHub 上的 PR 补进记忆、按实况刷新状态）；也包括"
        "**PR 已合并 / 已关闭之后的收尾动作**（更新记忆、同步、清理等）与解释功能/用户疑问。",
    ),
    # qa 原先只写「基础问题（概念、用法、原理、环境、工具的提问）」，全是知识问答；查仓库
    # 现状（有哪些未关闭的 PR / 某个文件写了什么 / 仓库记忆里怎么说的）无处可去。
    IntentSpec(
        "qa",
        "对**已有数据**做问答：仓库已具备哪些功能、有哪些 PR、某个文件/提交/记忆里写了什么，"
        "或回答基础问题（概念、用法、原理、环境、工具）。"
        "**需求讨论 / 需求补充 / 需求澄清**（把要做什么说清楚，还没让动手）也归 qa。"
        "**计划、方案、任务完成度一律归 plan**，不要因为是在“问”就归 qa。",
    ),
)

_LLM_INTENTS: frozenset[str] = frozenset(spec.intent for spec in _INTENT_SPECS)

# 判定来源，写进日志与业务记录，用于事后核对准确率：
# keyword = 口令直通或（未启用判定器时的）关键字口径；jev = JEV 判定；llm = 大模型判定；
# fallback = 两个模型都判不出来，回落到关键字口径
SOURCE_KEYWORD = "keyword"
SOURCE_JEV = "jev"
SOURCE_LLM = "llm"
SOURCE_FALLBACK = "fallback"
SOURCE_ACTIVE = "active"
SOURCE_CLARIFY = "clarify"
SOURCE_CLARIFY_CHOICE = "clarification_choice"


@dataclass(frozen=True)
class IntentDecision:
    """一次判定的结果：意图 + 来源 + 理由（理由只进日志）。"""

    intent: str
    source: str
    reason: str = ""
    #: 判定器给的置信度（JEV 的 ``ChoiceAnswer.confidence``，0~1）；口令直通与关键字口径
    #: 没有这个概念，留 ``None``。
    confidence: float | None = None
    #: 低置信度时的候选（意图, 概率），按可能性从高到低；只用于 qa 的澄清追问。
    candidates: tuple[tuple[str, float], ...] = ()
    #: 本轮是否转成 qa、先向用户确认意图。
    needs_clarification: bool = False


def should_clarify(decision: IntentDecision, threshold: float) -> bool:
    """低置信度是否转 qa 澄清；口令/关键字/active 没有可信置信度，不进这条。"""
    if threshold <= 0 or decision.confidence is None:
        return False
    return decision.source in {SOURCE_JEV, SOURCE_LLM} and decision.confidence < threshold


def clarification_decision(
    decision: IntentDecision, threshold: float
) -> IntentDecision | None:
    """低置信度 → qa 澄清决策；不需要澄清返回 ``None``。"""
    if not should_clarify(decision, threshold):
        return None
    confidence = float(decision.confidence or 0.0)
    return IntentDecision(
        "qa",
        SOURCE_CLARIFY,
        f"置信度低（{confidence:.2f}），先向用户确认意图",
        confidence=confidence,
        candidates=decision.candidates,
        needs_clarification=True,
    )


def has_continue_marker(message: str) -> bool:
    """「不是」不能命中「是不是」——后者是疑问句，不是纠正。"""
    return any(
        marker in message and not (marker == "不是" and "是不是" in message)
        for marker in CONTINUE_MARKERS
    )


def active_task_override(
    message: str, intent: str, active_intent: str | None
) -> str | None:
    """短追问的兜底：当前判成 analysis/qa，但用户明确在继续一个未完成任务。

    只在显式 continuation/correction 标记出现时生效；正常问句不碰。上一轮是 plan 而
    用户说“修复/实施/执行”时，落到 coding（实施/修复代码，不是改方案文本）。
    """
    if active_intent not in EXECUTION_INTENTS:
        return None
    if intent in EXECUTION_INTENTS:
        return None
    if (
        active_intent == "plan"
        and "方案" not in message
        and len(message.strip()) <= 8
        and any(word in message for word in SHORT_IMPERATIVES)
    ):
        return "coding"
    if has_continue_marker(message):
        return active_intent
    short = message.strip()
    if len(short) <= 5 and not any(
        word in short for word in ("为什么", "怎么", "吗", "呢", "是不是")
    ):
        return active_intent
    return None


# 否定语境：口令被否定就不算口令 ——「先不要动手改代码」不是在让你动手改。
_NEGATORS: tuple[str, ...] = ("不", "无", "别", "勿", "甭", "毋", "莫", "免")
_NEGATION_WINDOW = 4


def _hit(text: str, word: str) -> bool:
    """``word`` 是否在**非否定**的位置出现过（``text`` 需已 lower）。

    只看关键词前紧邻的一小段：窗口开大容易把「不要动 A，动手改 B」这种真指令也吞掉。
    真被吞掉也不可怕 —— 退回 LLM 判定本来就是自然措辞该走的那条路（词表只管口令）。
    """
    start = 0
    while (at := text.find(word, start)) != -1:
        if not any(n in text[max(0, at - _NEGATION_WINDOW) : at] for n in _NEGATORS):
            return True
        start = at + 1
    return False


def match_strong(message: str) -> str | None:
    """口令级命中 → 意图；没命中 → None。``sync`` 只能从这里产出。

    被否定的口令不算命中（见 ``_hit``）——「先不要动手改代码」本来是在**叫停**，
    不认否定就会命中 ``动手改`` 与 ``改代码``，一路判成 coding。
    """
    text = message.lower()
    for rule in _RULES:
        if any(e.lower() in text for e in rule.excludes):
            continue
        if any(_hit(text, word) for word in rule.strong):
            return rule.intent
    return None


def weak_hints(message: str) -> list[str]:
    """命中的弱关键字（形如 ``coding(实现)``），只作提示与兜底，不单独定案。"""
    text = message.lower()
    hints: list[str] = []
    for rule in _RULES:
        if any(e.lower() in text for e in rule.excludes):
            continue
        hit = [word for word in rule.weak if word in text]
        if hit:
            hints.append(f"{rule.intent}({', '.join(hit)})")
    return hints


def classify(message: str) -> str:
    """纯关键字判定（强 + 弱 + 排除词）→ 任务类型，无匹配则 ``qa``。

    这是 LLM 不可用时的回落口径，也是升级前的老行为。
    """
    text = message.lower()
    for rule in _RULES:
        if any(e.lower() in text for e in rule.excludes):
            continue
        if any(_hit(text, word) for word in (*rule.strong, *rule.weak)):
            return rule.intent
    return DEFAULT_INTENT


# -- LLM 判定 ---------------------------------------------------------------

# 判定要点。分类模型栽得最多的就是「句子里出现功能名词就判 coding」——「请给这个仓库
# 出一个增加 GET /hello 接口的方案，先不要动手改代码」被判成了 coding。光列意图说明
# 治不了它（coding 的说明里本来就写着「写、改」），得把分界写成规则：看这一轮要的
# 交付物，不看提到了什么功能。
_BOUNDARY: tuple[str, ...] = (
    "判定要点：",
    (
        "- plan 与 coding 的分界，看这一轮用户要的是什么，不看句子里提到了什么功能："
        "要一份步骤或方案是 plan，要一个改动结果是 coding。提到了要做什么功能，不改变这个判断。"
    ),
    (
        "- plan 的常见说法：出一个…方案、帮我规划一下、先说怎么做、列个步骤、"
        "先不要动手改代码、按这个思路给个设计。"
    ),
    (
        # 2026-10-20 用户口径：方案设计与方案讨论都归 plan（不等到「出方案」这种口令）
        "- 方案设计 / 方案讨论也归 plan：比较几种做法、这个方案行不行、这一步该怎么落地、"
        "先把思路说清楚 —— 只要这一轮要的是**思路**而不是改动结果，就是 plan。"
    ),
    (
        # 2026-10-20 用户口径：需求讨论从 coding 里挪出来，归 qa
        "- 需求讨论与 coding 的分界：用户只是在**说需求**（补充条件、改口径、问「这样做行不行」"
        "「还能不能再加一个」「先做成 X 那样」）是 qa；明确让你**动手改代码 / 提 PR** 才是 coding。"
        "判据只有一条：这一轮有没有下达动手指令，不看句子里提到了多少功能。"
    ),
    (
        "- plan 管的是**方案这个对象本身**，增删改查都归它：出方案（要计划 / 设计 / 步骤）、"
        "查方案（最新的是哪一份、某份方案的正文与步骤）、改方案（改正文 / 标题 / 步骤、修掉乱码"
        "或过时内容）、删方案（删掉 / 作废某一份）。分界：**查仓库里的文件 / PR / 提交归 qa**；"
        "**按方案去改代码、提 PR 归 coding**；方案文本本身的增删改只有 plan 这一档有工具。"
    ),
    "- coding 的常见说法：按这个方案做、直接改了吧、改完提个 PR。",
    (
        "- rework 与 coding 的分界：**指着一份已经审过的 PR（或它上面的评审清单 / 评审原文）**"
        "要求按其中的意见改代码，是 rework；按新需求从零写、或按方案实施，是 coding。"
    ),
    "- rework 与 review 的分界：要真的去**改**代码是 rework，只看代码、只给结论是 review。",
    (
        "- analysis 与 qa 的第二条分界（除「分析代码」之外）：**要求更新**仓库记忆"
        "（把 Gitee / GitHub 上的 PR 补进记忆、按实况刷新状态）归 analysis；只是**查看**记忆里写了"
        "什么、有哪些 PR，归 qa。"
    ),
    (
        "- analysis 与 qa 的第三条分界：**解释功能 / 解释用户疑问 / 排查为什么**归 analysis；"
        "对**已有数据**的查问（已具备哪些功能、有哪些 PR、某个文件写了什么）归 qa。"
        "**计划、方案、任务完成度一律归 plan**，哪怕是“完成了多少”这种问法。"
    ),
    (
        "- PR 生命周期：写代码 / 创建 PR 是 coding；审查还开着的 PR 是 review；按审查意见"
        "修改还开着的 PR 是 rework；**PR 已合并或已关闭之后的收尾动作**（更新记忆、同步、清理）"
        "是 analysis。不要把“刚提交/待合并”的 PR 归到 analysis。"
    ),
    (
        "- 用户说「继续 / 接着 / 修正 / 纠正 / 重来 / 再试一次」时，按**未完成轮次**处理："
        "沿用上一轮的可执行意图，不要新开一个无关形态。"
    ),
    (
        "- 对上一轮动作的追问（「你为什么不派 coder」「上一轮不是 rework 吗」"
        "「为什么没改代码」）不是新的分析任务：先看上一轮判定与回复，判断用户是要继续 /"
        "纠正上一轮动作，还是只要解释；**不要仅因为出现「为什么」就判 analysis**。"
    ),
)


#: 助手上一轮回复的**末尾节选**长度（字符）：判定要的是「上一轮问了你什么」，
#: 那句反问几乎总在末尾，所以取尾部而不是开头（记录的 summary 是前 200 字，正好会切掉它）。
PREVIOUS_REPLY_CHARS = 300

#: 读库时的余量：多读一段，好在句末标点处切开，尽量不留半句。
PREVIOUS_REPLY_SLACK = 100

#: 两轮对话里助手消息的首尾保留长度（用户消息保留全文）。
DIALOGUE_HEAD_CHARS = 200
DIALOGUE_TAIL_CHARS = 300


def tail_excerpt(text: str, *, chars: int = PREVIOUS_REPLY_CHARS) -> str:
    """取末尾 ``chars`` 字左右的一段，尽量从**句末标点之后**开始（不留半句）。

    只在开头那一小段里找句末标点（最多丢 ``PREVIOUS_REPLY_SLACK`` 字）：找得到就从那儿
    重新开始，找不到就原样用 —— 一整段没有标点的长句，切掉它只会把要说的话也切没。
    """
    text = text.strip()
    if len(text) <= chars:
        return text
    window = text[-(chars + PREVIOUS_REPLY_SLACK) :]
    cut = -1
    for index, char in enumerate(window[:PREVIOUS_REPLY_SLACK]):
        if char in "。！？!?\n":
            cut = index
    return window[cut + 1 :].strip() if cut >= 0 else window[-chars:]


def head_tail_excerpt(
    text: str,
    *,
    head: int = DIALOGUE_HEAD_CHARS,
    tail: int = DIALOGUE_TAIL_CHARS,
) -> str:
    """助手消息截断：前留 ``head`` 字、后留 ``tail`` 字，中间用省略号。

    用户消息要全文；助手消息通常很长，但“上一轮问了什么/结论是什么”往往在开头与
    末尾，取首尾比只取尾部更不容易丢指代。
    """
    text = text.strip()
    if len(text) <= head + tail:
        return text
    return f"{text[:head]}\n…（中间省略）…\n{text[-tail:]}"


def _system_prompt() -> str:
    lines = ["你是任务意图分类器，判断用户最新一条消息属于哪一类。", ""]
    lines.extend(f"- {spec.intent}：{spec.summary}" for spec in _INTENT_SPECS)
    lines += ["", *_BOUNDARY]
    options = " / ".join(spec.intent for spec in _INTENT_SPECS)
    lines += [
        "",
        # 2026-10-12 实测：用户在回答助手上一轮的反问时只回一句「修复吧」，那一刻的
        # 指代对象（「要不要我把方案正文修复一遍？」）不在这一句里 —— 只看字面会判成 coding。
        (
            "若本轮消息只是一个很短的肯定 / 否定 / 指代（如「修复吧」「好的」「来吧」「继续」），"
            "它很可能是在**回答助手上一轮的那个问题** —— 按那个问题所指的动作归类，"
            "不要只看这一句的字面。"
        ),
        "只输出四行，不要 Markdown、不要额外解释：",
        f"第一行：一个词（{options}）",
        "第二行：置信度（0~1 的小数，保留两位；拿不准可以低于 0.55）",
        (
            "第三行：候选（最多 3 个，按可能性从高到低，格式 `plan 0.42, coding 0.31`；"
            "拿不准就写 `-`）"
        ),
        "第四行：不超过 20 字的理由",
    ]
    return "\n".join(lines)


def _user_prompt(
    message: str,
    history: Sequence[str],
    hints: Sequence[str],
    previous_reply: str | None = None,
    previous_intent: str | None = None,
    active_intent: str | None = None,
    dialogue: Sequence[Mapping[str, str]] = (),
) -> str:
    parts: list[str] = []
    if active_intent:
        parts.append(
            f"当前未完成任务：{active_intent}（最近一次可执行意图；之后的 analysis/qa 不覆盖它）。"
            "如果本轮是继续 / 修正 / 短追问，优先沿用这个任务。"
        )
    if previous_intent:
        parts.append(
            f"上一轮判定：{previous_intent}\n"
            "如果本轮只是对上一轮动作的追问 / 质疑，先按上一轮意图理解；"
            "不要仅因为出现「为什么」就判 analysis。"
        )
    if dialogue:
        lines = ["最近两轮对话（由远及近；用户消息全文，助手消息首尾截断）："]
        for turn in dialogue:
            role = "用户" if turn.get("role") == "user" else "助手"
            lines.append(f"[{role}] {turn.get('content', '')}")
        parts.append("\n".join(lines))
    else:
        if history:
            recent = "\n".join(f"- {item}" for item in history)
            parts.append(f"最近几条用户消息（由远及近）：\n{recent}")
        if previous_reply:
            # 老调用方只给尾部节选；「修复吧」这类短句的指代对象就在这里
            parts.append(f"助手上一轮回复（末尾节选）：\n{previous_reply}")
    parts.append(f"本轮消息：{message}")
    if hints:
        # 关键字只作线索：命中它们的大多是泛指词（「实现」「功能」），所以明确打折
        parts.append(
            "关键字线索（自动匹配所得，可能不准，仅供参考）：" + "、".join(hints)
        )
    return "\n\n".join(parts)


def _reply_text(reply: Any) -> str:
    """取模型回复的正文（内容可能是字符串，也可能是内容块列表）。"""
    content = getattr(reply, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = []
        for block in content:
            if isinstance(block, str):
                chunks.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                chunks.append(block["text"])
        return "".join(chunks)
    return str(content)


@dataclass(frozen=True)
class ParsedReply:
    """大模型分类回复：意图 / 理由 / 自报置信度 / 候选（按概率降序）。"""

    intent: str
    reason: str = ""
    confidence: float | None = None
    candidates: tuple[tuple[str, float], ...] = ()


def _parse_confidence(text: str) -> float | None:
    """解析 0~1 的置信度；兼容 ``72%``。不合法返回 ``None``（按旧两行格式处理）。"""
    token = text.strip()
    percent = token.endswith("%")
    try:
        value = float(token.rstrip("%").strip())
    except ValueError:
        return None
    if percent:
        value /= 100.0
    return value if 0.0 <= value <= 1.0 else None


def _parse_candidates(text: str) -> tuple[tuple[str, float], ...]:
    """解析 ``plan 0.42, coding 0.31``；只认词表里的意图，去重后取前 3。"""
    best: dict[str, float] = {}
    for name, raw in re.findall(r"([a-z_]+)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)", text.lower()):
        if name not in _LLM_INTENTS:
            continue
        value = float(raw)
        if 0.0 <= value <= 1.0:
            best[name] = max(best.get(name, 0.0), value)
    return tuple(sorted(best.items(), key=lambda item: item[1], reverse=True)[:3])


def parse_reply(text: str) -> ParsedReply | None:
    """解析大模型分类回复；首词不在清单里则返回 None。

    兼容两种格式：

    * 新四行：意图 / 置信度 / 候选 / 理由；
    * 旧两行：意图 / 理由（第二行不是 0~1 的数字时按旧格式处理）。
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    intent = lines[0].strip("`。.：:　 ").lower()
    if intent not in _LLM_INTENTS:
        return None
    if len(lines) == 1:
        return ParsedReply(intent)
    confidence = _parse_confidence(lines[1])
    if confidence is None:
        return ParsedReply(intent, lines[1][:60])
    candidates: tuple[tuple[str, float], ...] = ()
    reason = ""
    if len(lines) > 2:
        candidates = _parse_candidates(lines[2])
        if candidates or lines[2].strip() in {"-", "无", "none", "n/a"}:
            reason = lines[3][:60] if len(lines) > 3 else ""
        else:
            reason = lines[2][:60]
    return ParsedReply(intent, reason, confidence, candidates)


# --------------------------------------------------------------------------- JEV 判定


#: 兼容三种返回：旧的 ``(意图, 理由, 置信度)`` 与新的「再带候选」四元组。
JevDecision = (
    tuple[str, str, float | None]
    | tuple[str, str, float | None, tuple[tuple[str, float], ...]]
)


class IntentDecider(Protocol):
    """判定器：判得出来给 ``(意图, 理由, 置信度)``，判不出来给 ``None``（交给下一关）。"""

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
    ) -> JevDecision | None: ...


class JevDecider:
    """用 JEV（SystemOne）判意图：一个 ``Choice`` 问题，候选就是能判的那 6 个意图（**不含 sync**）。

    ``Choice(instructions=…, criteria={标签: 描述})`` 拿回 ``.choice`` / ``.confidence`` /
    ``.probabilities`` —— 置信度与各候选概率都进日志，比大模型自述的一句理由更硬。

    ``instructions`` 直接复用 ``_system_prompt()``：JEV 那关与大模型那关判的必须是同一件事，
    口径一漂就会出现「换个模型结论就变」，事后从日志里也看不出是哪一层判的。

    **任何失败都只是「这一关判不出来」**：没配、超时、报错、回了认不出的标签，一律返回
    ``None`` 交给下一关（大模型 → 关键字口径），绝不打断这一轮对话。

    SDK（``typesafe-sdk``）**在构造时才 import**：没装这个包的部署不该因此起不来 ——
    与 ``tools/web_search.py`` 里那几处懒加载同一个理由。
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "",
        model: str = "",
        timeout: float = 10.0,
    ) -> None:
        from typesafe_sdk import AsyncTypeSafeClient, Choice, RetryPolicy

        self._timeout = timeout
        self._question = Choice(
            instructions=_system_prompt(),
            criteria={spec.intent: spec.summary for spec in _INTENT_SPECS},
        )
        self._client = AsyncTypeSafeClient(
            api_key=api_key,
            base_url=base_url or None,
            model=model or None,
            timeout=timeout,
            # 偶发 5xx/429 让 SDK 自己重试一次；整体超时仍由 timeout 兜住
            retry=RetryPolicy(max_retries=1),
        )

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
        response = await self._client.system_one(
            state=_user_prompt(
                message,
                history,
                hints,
                previous_reply,
                previous_intent,
                active_intent,
                dialogue,
            ),
            questions={"intent": self._question},
            timeout=self._timeout,
        )
        # 请求成功就先把用量记下 —— 这一段之后还有解析与「认不出的标签」，那些路径会
        # 返回 None 交给下一关，但**这次调用是真的花了钱**，不能因为判定没结论就漏账。
        _record_usage(usage, getattr(response, "usage", None))
        answer = response.answers.get("intent")
        choice = str(getattr(answer, "choice", "") or "")
        if choice not in _LLM_INTENTS:
            # 认不出的标签（服务端换了词、SDK 加了新原语）：不猜，交给下一关
            logger.warning("JEV 返回了认不出的意图：%r", choice)
            return None
        confidence = float(getattr(answer, "confidence", 0.0) or 0.0)
        probabilities = getattr(answer, "probabilities", None) or {}
        candidates = tuple(
            sorted(
                (
                    (str(name), float(value))
                    for name, value in probabilities.items()
                    if str(name) in _LLM_INTENTS
                ),
                key=lambda item: item[1],
                reverse=True,
            )[:3]
        )
        runner_up = sorted(
            ((float(value), name) for name, value in probabilities.items() if name != choice),
            reverse=True,
        )[:1]
        reason = f"p={confidence:.2f}"
        if runner_up:
            reason += f" 次高 {runner_up[0][1]}={runner_up[0][0]:.2f}"
        response_usage = getattr(response, "usage", None)
        logger.info(
            "JEV 判定：intent=%s %s model=%s tokens=%s/%s",
            choice,
            reason,
            getattr(response, "model", "-"),
            getattr(response_usage, "input_tokens", None),
            getattr(response_usage, "output_tokens", None),
        )
        return choice, reason, confidence, candidates

    async def aclose(self) -> None:
        """释放底层 HTTP 客户端（进程退出时调）。"""
        await self._client.aclose()


def build_jev_decider(settings: Any) -> JevDecider | None:
    """按配置造 JEV 判定器；没启用 / 没配齐 / 装不上 SDK 时返回 ``None``（跳过这一关）。

    凭据来自界面上的「意图识别（JEV）」那一行（2026-10-15 起 ``TYPESAFE_*`` 三个 .env 键
    作废）：**地址与 key 两样都要**才认为这一侧可用 —— 只填了 key 会打到 SDK 的默认地址上，
    那多半不是用户想连的那家（踩过一次：默认地址与自建网关不是一回事）。

    构造失败**不让服务起不来**：JEV 只是二选一里的一侧，缺了它还有关键字口径兜底。
    """
    if not getattr(settings, "intent_jev_enabled", False):
        return None
    api_key = str(getattr(settings, "typesafe_api_key", "") or "")
    if not api_key:
        logger.info("「意图识别（JEV）」那一行没配 api_key，跳过 JEV")
        return None
    base_url = str(getattr(settings, "typesafe_base_url", "") or "")
    if not base_url:
        logger.info("「意图识别（JEV）」那一行没配 base_url，跳过 JEV")
        return None
    try:
        return JevDecider(
            api_key=api_key,
            base_url=base_url,
            model=str(getattr(settings, "typesafe_model_name", "") or ""),
            timeout=float(getattr(settings, "typesafe_timeout_seconds", 10.0)),
        )
    # 判定器缺一层不影响服务启动
    except Exception:
        logger.exception("JEV 判定器构建失败，意图判定跳过这一关")
        return None


def _record_usage(usage: dict[str, int] | None, raw: Any) -> None:
    """把一次判定的 token 用量并进调用方给的累加器（``usage`` 为 ``None`` 就不记）。

    两种形状都认：``usage_metadata``（LangChain 归一，``input_tokens``/``output_tokens``）
    与 SDK 原始对象（``input_tokens``/``output_tokens`` 属性，JEV 那侧是这种）。
    认不出来的**不记** —— 账上留空比记成 0 好，0 会让「没读到」看起来像「没花钱」。
    """
    if usage is None:
        return
    if isinstance(raw, dict):
        inp = raw.get("input_tokens", raw.get("prompt_tokens"))
        outp = raw.get("output_tokens", raw.get("completion_tokens"))
    else:
        inp = getattr(raw, "input_tokens", None)
        outp = getattr(raw, "output_tokens", None)
    if inp is None and outp is None:
        return
    usage["input"] = usage.get("input", 0) + int(inp or 0)
    usage["output"] = usage.get("output", 0) + int(outp or 0)
    usage["calls"] = usage.get("calls", 0) + 1


async def detect_intent(
    message: str,
    *,
    history: Sequence[str] = (),
    jev: IntentDecider | None = None,
    model: BaseChatModel | None = None,
    timeout: float = 5.0,
    force_llm: bool = False,
    usage: dict[str, int] | None = None,
    previous_reply: str | None = None,
    previous_intent: str | None = None,
    active_intent: str | None = None,
    dialogue: Sequence[Mapping[str, str]] = (),
    mode: str = "jev",
) -> IntentDecision:
    """判定本轮意图。**两道瀑布按 ``mode`` 二选一**（2026-10-13 用户口径，2026-10-15 收紧为
    **严格二选一**）：

    * ``mode="jev"``（**三关**）：口令 → **JEV** → 关键字口径。**选了 JEV 就只有 JEV**：
      JEV 没配齐（``jev is None``）或判不出来（超时 / 认不出标签）都直接落关键字口径 ——
      不借另一侧的模型（要借就是没做选择，用户口径 2026-10-15）。
    * ``mode="llm"``（**三关**）：口令 → **大模型** → 关键字口径（不碰 JEV）。

    ``force_llm=True``（``handoffTask`` 的重判）不看 ``mode``：重判要纠正的正是口令层，
    只能问大模型。判定是增强而不是新的故障点：每一关的结论都如实标在 ``source`` 上
    （keyword / jev / llm / fallback），事后能按来源核对准确率。

    ``force_llm`` 跳过**口令直通**那一层，直接问模型（``interrupt`` 路径用）。
    智能体自述「这活我做不了」时，要纠正的往往正是口令层 —— 撞上口令就算定了案，
    而消息里的真实诉求是另一件事（2026-09-21 那次：一轮 ``execute git status`` 打转，
    口令层早早就把它钉成了 coding）。判定器不可用仍是回落，且来源照样标 ``fallback``：
    这时候的判定**不是**重判，调用方得看得出来（见 ``tools/handoff.py`` 的拒绝分支）。

    ``usage`` 是调用方给的累加器（``{"input":…, "output":…}``）：判定本身也要花 token，
    而且花的是**另一个模型**，所以由调用方决定要不要单独归一类（见 ``_record_usage``）。
    不传就不记 —— 判定不该因为记账与否改变行为。

    ``previous_reply`` 是助手上一轮回复的末尾节选（见 ``tail_excerpt``）：用户回一句
    「修复吧」时，指代对象只在上一轮那句反问里 —— 两条判定路径（JEV / 大模型）共用同一个
    提示词，所以两处一起看到它。不传就是不带（老行为）。

    ``previous_intent`` 是上一轮的判定结果：对「你为什么不派 coder」「上一轮不是 rework
    吗」这类**追问**，它比字面里的「为什么」可靠得多。两条判定路径同样共用。

    ``dialogue`` 是最近两轮的完整对话（用户消息全文、助手消息首尾截断）：只给用户消息
    往往看不出“上一轮 AI 为什么这么说”，给两轮才够判断“继续 / 修正 / 解释”的指代。

    ``active_intent`` 是会话里最近一次**可执行**意图（coding/rework/plan/review）；
    analysis/qa 不覆盖它。短追问的指代优先看它。
    """
    if not force_llm:
        strong = match_strong(message)
        if strong is not None:
            return IntentDecision(strong, SOURCE_KEYWORD, "命中口令关键字")

    hints = weak_hints(message)

    # JEV 那一关：只在「配置选了它」且不是重判时才走（三关里的第二关）。
    # 严格二选一：选了 JEV 但没配齐 / 判不出来，直接落关键字口径，**不借大模型**。
    if mode == "jev" and not force_llm:
        if jev is not None:
            try:
                # 用量直接记进调用方给的累加器：判定花钱，且花在另一个模型上，所以由调用方
                # 决定怎么归类（见 ``_record_usage``）。JEV 自己不留账 —— 留一份实例上的
                # 副本没人读，还会跨轮累积。
                decided = await jev.decide(
                    message,
                    history=history,
                    hints=hints,
                    usage=usage,
                    previous_reply=previous_reply,
                    previous_intent=previous_intent,
                    active_intent=active_intent,
                    dialogue=dialogue,
                )
            except Exception as exc:  # noqa: BLE001 —— JEV 是外部服务，失败只降级
                logger.warning(
                    "JEV 判定失败（三关：落关键字口径）：%s: %s",
                    type(exc).__name__,
                    exc,
                )
                decided = None
            if decided is not None:
                intent, reason, confidence = decided[:3]
                candidates = decided[3] if len(decided) > 3 else ()
                return IntentDecision(
                    intent,
                    SOURCE_JEV,
                    reason,
                    confidence=confidence,
                    candidates=candidates,
                )
        # 三关就是三关：JEV 判不出来（超时 / 认不出标签 / 没配齐）**不再回退大模型** ——
        # 用户口径是「二选一」，而「判不出来」不等于「没有 JEV」。
        return IntentDecision(
            classify(message),
            SOURCE_FALLBACK,
            "JEV 不可用或判不出来（三关：落关键字口径）",
        )

    if model is not None:
        try:
            reply = await asyncio.wait_for(
                model.ainvoke(
                    [
                        SystemMessage(content=_system_prompt()),
                        HumanMessage(
                            content=_user_prompt(
                                message,
                                history,
                                hints,
                                previous_reply,
                                previous_intent,
                                active_intent,
                                dialogue,
                            )
                        ),
                    ]
                ),
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001  # 分类失败绝不能打断本轮
            return IntentDecision(classify(message), SOURCE_FALLBACK, str(exc) or type(exc).__name__)
        _record_usage(usage, getattr(reply, "usage_metadata", None))
        parsed = parse_reply(_reply_text(reply))
        if parsed is None:
            return IntentDecision(
                classify(message), SOURCE_FALLBACK, "大模型返回了无法识别的结果"
            )
        return IntentDecision(
            parsed.intent,
            SOURCE_LLM,
            parsed.reason,
            confidence=parsed.confidence,
            candidates=parsed.candidates,
        )

    # 走到这里只有两种情况：没配大模型；或 ``force_llm`` 重判但没有可用模型。
    # 后者必须标 ``fallback`` —— ``handoffTask`` 靠来源判断「这次不是重判，别交接」。
    return IntentDecision(
        classify(message),
        SOURCE_FALLBACK if force_llm else SOURCE_KEYWORD,
        "重判没有可用模型" if force_llm else "无可用判定器",
    )
