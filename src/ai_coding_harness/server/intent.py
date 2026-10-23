"""用户意图分析 —— 状态候选 + 四关判定（口令 → JEV → 大模型 → 关键字口径）。

四关顺序不可颠倒（见 ``detect_intent``）：

1. **按仓库状态限制候选**：没有 open PR 时是 qa / plan / coding；有 open PR 时是
   qa / rework。候选集同时喂给关键字、JEV、大模型与兜底，模型不会选一个当前场景
   根本不可执行的意图。
2. **强关键字（口令级）直通**：命中即定，不调任何模型。这类词是明确指令（「出方案」
   「实施」），没有歧义。
3. **JEV（SystemOne，决策型模型）判定**：口令没命中时问它。``Choice`` 的 criteria
   只放当前候选，拿回选项、置信度与各候选概率 —— 见 ``JevDecider``。
4. **大模型判定 / 回落 ``classify()``**：模型不可用时退回纯关键字口径；关键字也没命中
   就取候选里的默认项。没有 open PR 时优先沿用历史上的 coding 兜底；有 open PR 时
   默认 qa，避免把一句模糊话直接变成 rework。

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

# 任务类型：`review` 主控形态已删除，PR 审查并入 rework 的子智能体流程；
# `analysis` 与 qa 合并；`sync` 删除，本地仓库由用户手动刷新。
TASK_TYPES: tuple[str, ...] = (
    "coding",    # 编码：写/改/重构/修复
    "rework",    # PR 跟进：审查 open PR，或按意见 / 用户新要求继续修改
    "plan",      # 计划 / 方案 / 设计
    "qa",        # 提问（默认兜底）
)

#: 「PR 跟进」。它要处理的就是那份**还开着**的 PR；装配上与 coding 共用 profile。
REWORK_INTENT = "rework"

#: 会形成“未完成任务”的意图；qa 是解释/查看，不覆盖它。
EXECUTION_INTENTS: frozenset[str] = frozenset({"coding", "rework", "plan"})

#: 明确的“未完成轮次”标记：出现这些词时沿用上一轮/未完成任务。
CONTINUE_MARKERS: tuple[str, ...] = (
    "继续", "接着", "修正", "纠正", "重来", "再试一次",
    "为什么不", "为什么没", "不是", "不对",
)

#: 短追问里的动作词：上一轮是 plan 时，这些通常表示实施/修复代码，而不是改方案文本。
SHORT_IMPERATIVES: tuple[str, ...] = (
    "修复", "实施", "执行", "开始", "做", "改", "落地", "上", "来",
)

#: 无状态限制时的历史默认。动态候选会把不可用的项过滤掉：没有 open PR 时它仍是
#: coding，有 open PR 时实际落到 qa。
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
    # PR 跟进：既能按评审意见改，也可以在 open PR 上按用户的新想法继续改。
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
            "代码审查", "代码评审", "审查一下", "评审一下",
            "审查 pr", "审查pr", "评审 pr", "评审pr",
            "返工",
        ),
        # 弱线索只作提示与兜底。**不放光秃秃的「评审意见」**：「看看有哪些评审意见」
        # 是查现状，该留在 qa；只有带修改动作时才由模型判 rework。
        weak=("评审提", "审查提", "代码审查", "代码评审", "审查", "评审"),
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
            "写文档", "补文档", "更新文档", "修改文档", "改文档", "写技术文档",
            "补技术文档", "修改技术文档", "更新 readme", "更新readme", "写 readme",
            "写readme", "修改 readme", "修改readme", "更新 agent.md", "更新agent.md",
            "写 agent.md", "写agent.md", "更新 agents.md", "更新agents.md",
            "写 agents.md", "写agents.md",
        ),
        weak=(
            "实现", "开发", "重构", "修复", "新增",
            "功能", "bug", "feature", "改一下", "加上", "去掉", "加一个",
        ),
        # 这些是「问怎么实现」，属于问答而非让你动手写
        excludes=(
            "怎么实现", "如何实现", "实现原理", "功能介绍", "功能是什么", "功能有哪些",
            "文档方案", "写文档的方案", "文档提纲", "文档结构", "怎么写文档", "如何写文档",
            "不要写",
        ),
    ),
)


@dataclass(frozen=True)
class IntentSpec:
    """给分类模型看的意图说明（意图清单只有这一份；判定要点另见 ``_BOUNDARY``）。"""

    intent: str
    summary: str


# LLM 可判定的意图全集；每轮还会再按 PR 状态裁候选。
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
        "要写、改、修、重构代码，或按已定方案实施；也包括直接新增 / 修改仓库里的文档文件"
        "（README、AGENTS.md、docs 等纯文档改动）。"
        "**讨论需求本身不是 coding**（要不要做、做成什么样、补什么条件、换个说法）——"
        "那是 qa。",
    ),
    # open PR 的后续都在这一档：审查、回评、按评审意见修、按用户新想法继续改。
    # 真正的执行动作由 rework 指令与 reviewer 子智能体分流。
    IntentSpec(
        "rework",
        "跟进一个还开着的 PR：审查 / 评审 / 回评，按评审意见修复，"
        "或按用户在这份 PR 上提出的新设计问题继续修改。只处理 open PR 的既有分支，"
        "不新建分支、不新建 PR。",
    ),
    # qa 合并原 analysis：所有只读问答、解释、排查、仓库现状查询都归这里。
    IntentSpec(
        "qa",
        "只读问答、解释与排查：对已有数据分析（仓库有哪些功能 / PR / 提交、"
        "某个文件或记忆写了什么）、解释用户疑问、排查原因、回答基础问题。"
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
SOURCE_STATE = "state"


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


@dataclass(frozen=True)
class StateGateResult:
    """函数式 PR 状态闸门的结果：放行时给最终意图，否则给阻塞原因。"""

    intent: str | None
    reason: str = ""


def gate_intent_for_state(
    intent: str, allowed_intents: Sequence[str]
) -> StateGateResult:
    """判定后的最后一道状态闸门。

    分类器已经只看到候选，但强关键字、旧 active task 与 handoff 重判仍可能给出过时意图。
    这里统一收口，不把“能不能执行”留给提示词。
    """
    allowed = set(allowed_intents)
    if intent in allowed:
        return StateGateResult(intent)
    if intent == "coding" and "coding" not in allowed:
        return StateGateResult(
            None,
            "当前仓库还有未合并的 PR。coding 已暂停；如果要继续处理这份 PR，"
            "请明确说审查或修改当前 PR。",
        )
    if intent == "rework" and "coding" in allowed:
        return StateGateResult(
            None,
            "当前仓库没有还开着的 PR，不能走 PR 跟进；如果要新开一个改动，"
            "请明确说要开始实施。",
        )
    if intent == "plan" and "plan" not in allowed:
        return StateGateResult(
            None,
            "当前仓库还有未合并的 PR。按本项目的状态约束，先审查或修改这份 PR，"
            "合并 / 关闭后再出新的实施方案。",
        )
    return StateGateResult(
        None,
        "当前无法确认仓库的 PR 状态，不能安全地区分 coding 与 rework。"
        "这次先只做只读问答；需要改代码时请稍后重试。",
    )


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


def match_strong(
    message: str, *, allowed_intents: Sequence[str] | None = None
) -> str | None:
    """口令级命中 → 意图；没命中 → None。

    被否定的口令不算命中（见 ``_hit``）——「先不要动手改代码」本来是在**叫停**，
    不认否定就会命中 ``动手改`` 与 ``改代码``，一路判成 coding。
    """
    allowed = set(allowed_intents or TASK_TYPES)
    text = message.lower()
    for rule in _RULES:
        if rule.intent not in allowed:
            continue
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


def classify(
    message: str, *, allowed_intents: Sequence[str] | None = None
) -> str:
    """纯关键字判定（强 + 弱 + 排除词）→ 任务类型。

    这是 LLM 不可用时的回落口径，也是升级前的老行为。
    """
    allowed = tuple(allowed_intents or TASK_TYPES)
    allowed_set = set(allowed)
    text = message.lower()
    for rule in _RULES:
        if rule.intent not in allowed_set:
            continue
        if any(e.lower() in text for e in rule.excludes):
            continue
        if any(_hit(text, word) for word in (*rule.strong, *rule.weak)):
            return rule.intent
    return DEFAULT_INTENT if DEFAULT_INTENT in allowed_set else (allowed[0] if allowed else "qa")


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
        "- 文档写入的分界：用户明确让你写 / 补 / 修改 / 更新 README、AGENTS.md、docs 下的"
        "技术文档，交付物是仓库里的文档改动时，属于 coding（后续可走纯文档直做）；只是在讨论"
        "文档要写什么、怎么组织、先列提纲，仍分别归 qa / plan。"
    ),
    (
        "- rework 与 coding 的分界：**指着一份已经审过的 PR（或它上面的评审清单 / 评审原文）**"
        "要求审查、回评或继续修改，是 rework；按新需求从零写、或按方案实施，是 coding。"
    ),
    (
        "- qa 已合并原来的 analysis：解释功能、解释用户疑问、排查为什么、查已有数据，"
        "全部归 qa。"
        "**计划、方案、任务完成度一律归 plan**，哪怕是“完成了多少”这种问法。"
    ),
    (
        "- PR 生命周期：没有 open PR 时写代码 / 创建 PR 是 coding；存在 open PR 时，"
        "对它的审查、回评、修复或继续修改都归 rework。PR 已合并 / 已关闭后先刷新缓存，"
        "没有 open PR 再回到 coding。"
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


def _normalise_allowed(allowed_intents: Sequence[str] | None) -> tuple[str, ...]:
    known = {spec.intent for spec in _INTENT_SPECS}
    if not allowed_intents:
        return tuple(spec.intent for spec in _INTENT_SPECS)
    result = tuple(item for item in allowed_intents if item in known)
    return result or tuple(spec.intent for spec in _INTENT_SPECS)


def _system_prompt(
    allowed_intents: Sequence[str] | None = None,
    *,
    repo_state: str | None = None,
) -> str:
    allowed = set(_normalise_allowed(allowed_intents))
    specs = tuple(spec for spec in _INTENT_SPECS if spec.intent in allowed)
    lines = ["你是任务意图分类器，判断用户最新一条消息属于哪一类。", ""]
    if repo_state:
        lines += [f"当前仓库状态：{repo_state}", ""]
    lines.extend(f"- {spec.intent}：{spec.summary}" for spec in specs)
    lines += ["", *_BOUNDARY]
    options = " / ".join(spec.intent for spec in specs)
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
    repo_state: str | None = None,
) -> str:
    parts: list[str] = []
    if repo_state:
        parts.append(f"当前仓库状态：{repo_state}")
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


def _parse_candidates(
    text: str, *, allowed_intents: Sequence[str] | None = None
) -> tuple[tuple[str, float], ...]:
    """解析 ``plan 0.42, coding 0.31``；只认词表里的意图，去重后取前 3。"""
    allowed = set(_normalise_allowed(allowed_intents))
    best: dict[str, float] = {}
    for name, raw in re.findall(r"([a-z_]+)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)", text.lower()):
        if name not in allowed:
            continue
        value = float(raw)
        if 0.0 <= value <= 1.0:
            best[name] = max(best.get(name, 0.0), value)
    return tuple(sorted(best.items(), key=lambda item: item[1], reverse=True)[:3])


def parse_reply(
    text: str, *, allowed_intents: Sequence[str] | None = None
) -> ParsedReply | None:
    """解析大模型分类回复；首词不在清单里则返回 None。

    兼容两种格式：

    * 新四行：意图 / 置信度 / 候选 / 理由；
    * 旧两行：意图 / 理由（第二行不是 0~1 的数字时按旧格式处理）。
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    intent = lines[0].strip("`。.：:　 ").lower()
    allowed = set(_normalise_allowed(allowed_intents))
    if intent not in allowed:
        return None
    if len(lines) == 1:
        return ParsedReply(intent)
    confidence = _parse_confidence(lines[1])
    if confidence is None:
        return ParsedReply(intent, lines[1][:60])
    candidates: tuple[tuple[str, float], ...] = ()
    reason = ""
    if len(lines) > 2:
        candidates = _parse_candidates(lines[2], allowed_intents=allowed_intents)
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
        allowed_intents: Sequence[str] | None = None,
        repo_state: str | None = None,
    ) -> JevDecision | None: ...


class JevDecider:
    """用 JEV（SystemOne）判意图；``Choice`` 的候选每轮按仓库状态裁。

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
        self._choice_cls = Choice
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
        allowed_intents: Sequence[str] | None = None,
        repo_state: str | None = None,
    ) -> tuple[str, str, float | None, tuple[tuple[str, float], ...]] | None:
        allowed = set(_normalise_allowed(allowed_intents))
        question = self._choice_cls(
            instructions=_system_prompt(allowed_intents, repo_state=repo_state),
            criteria={
                spec.intent: spec.summary
                for spec in _INTENT_SPECS
                if spec.intent in allowed
            },
        )
        response = await self._client.system_one(
            state=_user_prompt(
                message,
                history,
                hints,
                previous_reply,
                previous_intent,
                active_intent,
                dialogue,
                repo_state,
            ),
            questions={"intent": question},
            timeout=self._timeout,
        )
        # 请求成功就先把用量记下 —— 这一段之后还有解析与「认不出的标签」，那些路径会
        # 返回 None 交给下一关，但**这次调用是真的花了钱**，不能因为判定没结论就漏账。
        _record_usage(usage, getattr(response, "usage", None))
        answer = response.answers.get("intent")
        choice = str(getattr(answer, "choice", "") or "")
        if choice not in allowed:
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
                    if str(name) in allowed
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
    allowed_intents: Sequence[str] | None = None,
    repo_state: str | None = None,
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
    candidates_allowed = _normalise_allowed(allowed_intents)
    if not force_llm:
        strong = match_strong(message, allowed_intents=candidates_allowed)
        if strong is None:
            # 不在候选里的强口令仍要保留下来交给状态闸门，不能静默降级成别的意图。
            strong = match_strong(message, allowed_intents=TASK_TYPES)
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
                    allowed_intents=candidates_allowed,
                    repo_state=repo_state,
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
            classify(message, allowed_intents=candidates_allowed),
            SOURCE_FALLBACK,
            "JEV 不可用或判不出来（三关：落关键字口径）",
        )

    if model is not None:
        try:
            reply = await asyncio.wait_for(
                model.ainvoke(
                    [
                        SystemMessage(
                            content=_system_prompt(
                                candidates_allowed, repo_state=repo_state
                            )
                        ),
                        HumanMessage(
                            content=_user_prompt(
                                message,
                                history,
                                hints,
                                previous_reply,
                                previous_intent,
                                active_intent,
                                dialogue,
                                repo_state,
                            )
                        ),
                    ]
                ),
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001  # 分类失败绝不能打断本轮
            return IntentDecision(
                classify(message, allowed_intents=candidates_allowed),
                SOURCE_FALLBACK,
                str(exc) or type(exc).__name__,
            )
        _record_usage(usage, getattr(reply, "usage_metadata", None))
        parsed = parse_reply(
            _reply_text(reply), allowed_intents=candidates_allowed
        )
        if parsed is None:
            return IntentDecision(
                classify(message, allowed_intents=candidates_allowed),
                SOURCE_FALLBACK,
                "大模型返回了无法识别的结果",
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
        classify(message, allowed_intents=candidates_allowed),
        SOURCE_FALLBACK if force_llm else SOURCE_KEYWORD,
        "重判没有可用模型" if force_llm else "无可用判定器",
    )
