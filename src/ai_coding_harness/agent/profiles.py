"""按意图装配智能体：一份注册表决定「挂哪些子智能体、给哪些主工具、用哪套提示词」。

意图是**逐轮**判定的（``server/intent.py``），智能体则按 ``(会话, 形态)`` 缓存
（见 ``session/manager.py``）—— 所以这里管的是**构建期**差异。像「本轮先派 planner」
「等用户决定修哪些问题」那类**当轮**要求不在这里；动态任务状态由服务端注入。

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
# 单智能体形态都不挂子智能体，但 qa / plan 的工具与角色提示词不同，所以各占一种。
QA_FORM = "qa"
PLAN_FORM = "plan"
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
    # 出方案：主控自己出方案并 storePlan 落库（多智能体形态下这一步归 planner 子智能体），
    # 不落文件 —— 与子智能体 planner 同形
    "plan": AgentProfile(PLAN_FORM),
    # 编码：全流程（唯一挂子智能体的形态）
    "coding": AgentProfile(ORCHESTRATE_FORM, subagents=_SUBAGENT_ORDER),
}

# 不知道用户要干什么时的兜底：少给能力的代价（这轮做不了）比多给更难挽回。
_FULL = _PROFILES["coding"]

# 有专属装配的意图。与 ``server/intent.py`` 的 ``TASK_TYPES`` 对表：新增意图若忘了
# 在这里登记，它会**静默**落到全量装配（挂着 create_pr 与三个子智能体）—— 正是本次
# 要去掉的那种误选。
PROFILED_INTENTS: frozenset[str] = frozenset(_PROFILES)


def profile_for(intent: str | None) -> AgentProfile:
    """取意图对应的 profile。

    未知意图按**全量**装配：不知道用户要干什么时，少给能力的代价（这轮做不了）比
    多给能力的代价（可能误用）更难挽回。

    所有写代码、审查 PR 和修复 PR 的工作都进入 coding 形态；主控根据 work task
    决定派 reviewer 还是 coder。
    """
    return _PROFILES.get(intent or "", _FULL)
