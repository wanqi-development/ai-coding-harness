"""按意图装配智能体：一份注册表决定「挂哪些子智能体、给哪些主工具、用哪套提示词」。

意图是**逐轮**判定的（``server/intent.py``），智能体则按 ``(会话, 形态)`` 缓存
（见 ``session/manager.py``）—— 所以这里管的是**构建期**差异。像「本轮先派 planner」
「等用户决定修哪些问题」那类**当轮**要求不在这里，仍由 ``prompts.intent_directive``
逐次注入。两者互补，别把一边的东西搬到另一边。

**只有一个轴：形态（form）**。形态同时定下三件事 —— 挂哪些子智能体、主控拿哪些主工具
（``tools/register.py`` 按它选）、主控用哪套角色提示词（``prompts._MAIN_PROMPTS``）。
早先拆成 ``readonly`` + ``form`` 两个字段，两处都得维护、还会配错（给了写工具却没配上
对应的角色段）；收成一个轴后不存在这种错配。

为什么按意图裁：全量装配时，问一句「这仓库是做什么的」也挂着 ``create_pr`` /
``close_pr`` 与三个子智能体 —— 这些正是「看起来能选、选了就错」的选项（实测它会拿
qa 类问题去派 planner 落方案）。裁掉比在提示词里劝阻有效。
"""

from __future__ import annotations

from dataclasses import dataclass

# 形态名：同时是缓存键、主工具子集与角色提示词的索引，三者由它一处对齐。
# 单智能体的四种形态装配相同（都不挂子智能体），但角色与主工具各不相同，所以各占一种：
# 共用一种形态，就会让某一轮拿到另一轮的口径（qa 与 analysis 曾共用，plan/review 同理）。
QA_FORM = "qa"
ANALYSIS_FORM = "analysis"
PLAN_FORM = "plan"
REVIEW_FORM = "review"
ORCHESTRATE_FORM = "orchestrate"

# 子智能体的固定顺序（deepagents 会把清单写进 `task` 工具描述，顺序稳定便于对照）
_SUBAGENT_ORDER: tuple[str, ...] = ("planner", "coder", "reviewer")


@dataclass(frozen=True)
class AgentProfile:
    """一种装配形态：``form`` 是那个唯一的轴，``subagents`` 是它挂的子智能体。"""

    form: str
    subagents: tuple[str, ...] = ()



# 键是**意图名**（与 ``server/intent.py`` 的 ``TASK_TYPES`` 同名），值才是形态
_PROFILES: dict[str, AgentProfile] = {
    # 问答：只读、自己答，产出只进回复
    "qa": AgentProfile(QA_FORM),
    # 分析：只读、自己分析，产出只进回复（output 没有回传通路，写进去用户取不到）
    "analysis": AgentProfile(ANALYSIS_FORM),
    # 出方案：主控自己出方案并 storePlan 落库（多智能体形态下这一步归 planner 子智能体），
    # 不落文件 —— 与子智能体 planner 同形
    "plan": AgentProfile(PLAN_FORM),
    # 审查：主控自己审、自己 comment_pr
    "review": AgentProfile(REVIEW_FORM),
    # 编码：全流程（唯一挂子智能体的形态）
    "coding": AgentProfile(ORCHESTRATE_FORM, subagents=_SUBAGENT_ORDER),
}

#: 与某个已有意图**共用同一份装配**的意图。唯一一例：``rework``（按评审意见修改）复用
#: ``coding`` 的全流程 —— 要改的代码一样、要走的环路一样（coder 改 → reviewer 审），差别
#: 只在**记忆闸门**（coding 有未合入 PR 就拦；rework 反过来，没有可修的对象才拦，见
#: ``agent/middleware/memory.py``）。
#:
#: 共用是**有意**的，所以写成显式登记，而不是再抄一份同形 profile：抄一份就会漂，而形态一
#: 漂，工具子集与角色提示词跟着一起漂（缓存键正是形态名）。反过来，真要给 rework 不同的
#: 工具或提示词，就得把它从这张表里摘出去、单开一种形态。
#:
#: 「刷新仓库记忆」当初也加过一条独立意图（复用 ``analysis``），2026-09-27 按用户口径撤回：
#: ``analysis`` 本来就有 `get_pr_info` 与 `save_repo_memory`，多一个意图等于多一份分类面与
#: 装配登记，收益为零 —— 现在它走 ``analysis`` 意图本身（见 ``server/intent.py`` 的 analysis
#: 规则与 ``prompts.py`` 的 analysis 指令）。
FORM_ALIASES: dict[str, str] = {"rework": "coding"}

# 别名在 ``_PROFILES`` 建好之后再挂上 —— 共用必须是**同一个对象**，不能是两条内容相同的
# 记录（``TestProfiles`` 钉着「rework 与 coding 拿到的是同一份装配」）。
_PROFILES.update({alias: _PROFILES[target] for alias, target in FORM_ALIASES.items()})

# 不知道用户要干什么时的兜底：少给能力的代价（这轮做不了）比多给更难挽回。
_FULL = _PROFILES["coding"]

# 有专属装配的意图（含 `FORM_ALIASES` 里的别名）。与 ``server/intent.py`` 的 ``TASK_TYPES``
# 对表：新增意图若忘了在这里登记，它会**静默**落到全量装配（挂着 create_pr 与三个子智能体）
# —— 正是本次要去掉的那种误选。``sync`` 走直接执行，本来就不该在这里。
PROFILED_INTENTS: frozenset[str] = frozenset(_PROFILES)


def profile_for(intent: str | None) -> AgentProfile:
    """取意图对应的 profile。

    未知意图按**全量**装配：不知道用户要干什么时，少给能力的代价（这轮做不了）比
    多给能力的代价（可能误用）更难挽回。``sync`` 走直接执行，到不了这里。

    别名意图（``FORM_ALIASES``）拿到的是被复用那一份**同一个对象** —— 形态相同即装配相同，
    而形态正是 ``SessionManager`` 的缓存键，所以同一会话里 coding 与 rework 共用同一个
    主智能体实例（提示词、工具、子智能体都对得上）。
    """
    return _PROFILES.get(intent or "", _FULL)
