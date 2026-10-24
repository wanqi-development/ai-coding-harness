"""方案相关工具：**增删改查**一套 + 给 coder 取方案与回写进度。

分工（2026-09-25 定）：

- ``plan`` 形态的主控持有**方案对象本身的增删改查** —— ``storePlan``（增）、``getPlan``
  （查）、``updatePlan`` / ``updatePlanSteps``（改）、``deletePlan``（删，软删）。
- ``planner`` 子智能体只有 ``storePlan``：它跑的是 plan → coder → reviewer 那一整套里的
  第一段，写方案就够，不该去改 / 删已经在跑的那份。
- ``coder`` 只有 ``get_unexecuted_plan`` + ``updatePlanStatus`` / ``updatePlanStep``：
  **实施进度仍由它回写**（右侧面板的实时进度靠这个），方案内容的改动不归它。

工具按会话构建（闭包捕获会话上下文），模型无需传 owner/repo/session_id；每个写操作都带
``session_id`` 限定，模型给的 plan_id 若来自别的会话会落到「未找到」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from langchain_core.tools import BaseTool, tool

from ai_coding_harness.agent.turn_state import TurnState
from ai_coding_harness.db import Database, dao
from ai_coding_harness.tools.context import get_configurable


@dataclass(frozen=True)
class PlanContext:
    """方案工具绑定的会话上下文。"""

    session_db_id: int  # sessions.id（内部主键）
    owner: str
    repo: str


# 步骤状态：pending 待办 / running 进行中 / done 完成 / failed 失败
_STEP_STATUSES = ("pending", "running", "done", "failed")

#: 正文的硬上限（字符）。这不是审美要求，是**生成稳定性**要求：实测一份 9.5k 字的正文
#: 会在里面自我修补（同一节写两遍、带「更正稿」字样）并在中途断掉（2026-10-12，plan 41）。
#: 提示词里要的是 4000 字以内，这里留一半余量当兜底。
MAX_CONTENT_CHARS = 6000

#: 正文里出现这些字样，说明模型在**修补自己写坏的东西**而不是写一份干净的方案。
_PATCH_MARKERS = ("更正稿", "以此为准", "（更正）", "(更正)")

#: 「半句结尾」的判据：正文以这些字符收尾，说明它是在一句话中间断掉的。
_DANGLING_TAILS = ("（", "(", "、", "，", ",", "：", ":", "…")

#: 正文里的步骤小节标题：``### 步骤 3：…``（只认**标题行**，正文里顺口提到的「步骤 3」不算）。
_STEP_HEADING = re.compile(r"^#{2,}\s*步骤\s*(\d+)")


def _check_content(content: str, steps: list[dict]) -> str | None:
    """落库前给**正文**做一次结构自检；不合格返回可读理由（由调用方回给模型重写）。

    为什么正文也要校验：``steps`` 早就在校验（非空、每项要有 goal），正文却一个检查都没有 ——
    2026-10-12 实测一份 9,521 字的正文写到第 4 步（共 6 步）就断了、里面还带着「步骤 3（更正稿）」
    这种自我修补片段，因为「合法」而原样落库，下游 coder 拿到的就是一份坏方案。

    检查的都是**能确定性判断**的东西（不猜语义）：

    1. 不能空、不能太短（少于 100 字的正文不可能是方案）；
    2. 不能带自我修补字样（「更正稿」「以此为准」）；
    3. 结尾不能是半句（以「（」「、」「：」这类字符收尾）；
    4. 用了「步骤 N」编号就得覆盖到 ``len(steps)`` —— 断在最前面几步正是那次的现象；
    5. 不能超长（见 ``MAX_CONTENT_CHARS``）。

    顺序是**先结构、后长度**：既超长又带修补片段时，让模型先看到「重写一份干净的」（那也
    自然会把正文压下来），而不是只收到一句「压缩」。
    """
    body = content.strip()
    if len(body) < 100:
        return "正文太短（少于 100 字），看不出是一份方案。"
    for marker in _PATCH_MARKERS:
        if marker in body:
            return (
                f"正文里出现「{marker}」这类自我修补字样：请重新写一份**干净**的正文"
                "（同一节只写一次），不要在文档里追加更正片段。"
            )
    tail = body.rstrip()
    if tail.endswith(_DANGLING_TAILS):
        return "正文结尾像半句话（以标点/括号收尾），说明写到一半断了 —— 请补完再重新调用。"
    headings = [
        int(match.group(1))
        for line in body.splitlines()
        if (match := _STEP_HEADING.match(line.strip()))
    ]
    if headings and max(headings) < len(steps):
        return (
            f"正文只写到第 {max(headings)} 步，而 steps 有 {len(steps)} 步 —— 正文没写完"
            "（或步骤编号与 steps 对不上）。请补齐后重新调用。"
        )
    if len(body) > MAX_CONTENT_CHARS:
        return (
            f"正文 {len(body)} 字，超过上限 {MAX_CONTENT_CHARS} 字。只写 coder 需要的东西"
            "（每步的关键实现点、要动的文件、验收口径），读码过程与背景复述不要写进正文。"
        )
    return None


def _normalize_steps(raw: object) -> tuple[list[dict] | None, str | None]:
    """校验并规范化 planner 给的步骤数组。

    返回 ``(steps, None)``；不合法时返回 ``(None, 错误提示)``，由调用方回给模型
    自我修正。``index`` 由服务端按顺序赋 1..n —— 不采信模型给的编号（它常从 0 起
    或跳号），coder 回写状态时要靠这个序号定位。
    """
    if not isinstance(raw, list) or not raw:
        return None, "steps 必须是非空数组。"
    steps: list[dict] = []
    for i, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            return None, f"第 {i} 项不是对象，应为 goal / files / done_criteria 组成的对象。"
        goal = str(item.get("goal") or "").strip()
        if not goal:
            return None, f"第 {i} 项缺少 goal（这一步要做什么）。"
        step: dict = {"index": i, "goal": goal, "status": "pending"}
        if item.get("files"):
            step["files"] = item["files"]
        done_criteria = str(item.get("done_criteria") or "").strip()
        if done_criteria:
            step["done_criteria"] = done_criteria
        steps.append(step)
    return steps, None


def _format_plan(plan) -> str:
    lines = [f"# 方案（plan_id={plan.id}，状态={plan.status}）"]
    if plan.title:
        lines.append(f"标题：{plan.title}")
    lines.append("")
    lines.append(plan.content or "(无正文)")
    if plan.steps:
        lines.append("")
        lines.append("## 步骤")
        for i, step in enumerate(plan.steps, 1):
            if isinstance(step, dict):
                goal = step.get("goal", "")
                files = step.get("files")
                done = step.get("done_criteria", "")
                status = step.get("status", "pending")
                lines.append(f"{i}. [{status}] {goal}")
                if files:
                    lines.append(f"   - 涉及文件：{files}")
                if done:
                    lines.append(f"   - 完成标准：{done}")
            else:
                lines.append(f"{i}. {step}")
    return "\n".join(lines)


def build_plan_tools(database: Database, ctx: PlanContext) -> list[BaseTool]:
    """构建方案工具（绑定给定会话上下文）。"""

    @tool
    async def storePlan(
        content: str,
        steps: list,
        title: str | None = None,
    ) -> str:
        """把制定好的实施方案写入 DB（供 coder 通过 get_unexecuted_plan 取用）。

        Args:
            content: 方案全文（Markdown）。类名、注解、依赖坐标、命令这类实现细节写这里。
            steps: 结构化步骤数组，每项形如 {"goal": "...", "files": [...], "done_criteria": "..."}。
                goal 会原样显示在用户的进度面板上，必须是**一行短句、约 15 字以内、只写
                「做什么」**（如「搭建工程骨架」「实现 Service 层」）；实现细节不要写进 goal，
                放 content 与 done_criteria。序号与状态由系统生成，不要自己编号。
            title: 方案标题（可选）。
        """
        normalized, error = _normalize_steps(steps)
        if error:
            return f"步骤列表不合法：{error}请修正后重新调用 storePlan（方案未写入）。"
        content_error = _check_content(content, normalized)
        if content_error:
            return f"正文不合法：{content_error}（方案未写入，请重写后重新调用 storePlan。）"
        async with database.session() as db:
            row = await dao.store_plan(
                db,
                session_id=ctx.session_db_id,
                owner=ctx.owner,
                repo=ctx.repo,
                content=content,
                steps=normalized,
                title=title,
            )
            plan_id = row.id
        state = get_configurable("turn_state")
        if isinstance(state, TurnState):
            state.plan_id = plan_id
        return f"方案已写入（plan_id={plan_id}，共 {len(normalized)} 步）。"

    @tool
    async def getPlan(plan_id: int | None = None) -> str:
        """查看方案：给 plan_id 取那一条，不给则取本会话最新的一份（含已删除的）。

        Args:
            plan_id: 方案 ID（storePlan 的返回、或上一轮对话里出现过的编号）。不传就取最新。
        """
        async with database.session() as db:
            if plan_id is None:
                plan = await dao.get_latest_plan(db, session_id=ctx.session_db_id)
            else:
                plan = await dao.get_plan(
                    db, session_id=ctx.session_db_id, plan_id=plan_id
                )
        if plan is None:
            if plan_id is None:
                return "本会话还没有方案。"
            return f"未找到方案 {plan_id}（或它不属于本会话）。"
        return _format_plan(plan)

    @tool
    async def planExists(plan_id: int) -> bool:
        """按 id 判定这份方案**还能不能拿去实施**：存在、且状态是 pending → ``True``。

        方案正文是大对象（实测一份一万字），而主控在派 coder 之前要确认的只有一件事：
        这份方案还在、还没被执行过。所以这个工具**只回一个布尔、不拉正文** ——
        正文让 coder 自己用 `get_unexecuted_plan` 取（那才是唯一权威的那份）。

        ``False`` 的三种情形：方案不存在 / 不属于本会话、已软删（closed）、已执行完（done）。

        Args:
            plan_id: 方案 ID（上一轮对话里出现过的编号、或 `storePlan` 的返回）。
        """
        async with database.session() as db:
            status = await dao.plan_status(
                db, session_id=ctx.session_db_id, plan_id=plan_id
            )
        return status == "pending"

    @tool
    async def updatePlan(
        plan_id: int,
        content: str | None = None,
        title: str | None = None,
    ) -> str:
        """修改方案的正文 / 标题（不动步骤与状态；步骤改动见 updatePlanSteps）。

        Args:
            plan_id: 方案 ID。
            content: 新的方案全文（Markdown）。不传则不改正文。
            title: 新的方案标题。不传则不改标题。
        """
        if content is None and title is None:
            return "content 与 title 至少要给一个（本次未改动）。"
        async with database.session() as db:
            plan = await dao.get_plan(db, session_id=ctx.session_db_id, plan_id=plan_id)
            if plan is None:
                return f"未找到方案 {plan_id}（或它不属于本会话）。"
            if plan.status == "closed":
                return f"方案 {plan_id} 已删除（closed），不再修改。"
            if content is not None:
                # 改正文与写正文同一套自检：坏正文从哪条路进来都不该落库
                content_error = _check_content(content, list(plan.steps or []))
                if content_error:
                    return (
                        f"正文不合法：{content_error}（方案未改动，请重写后重新调用 updatePlan。）"
                    )
            await dao.update_plan_content(
                db,
                plan_id=plan_id,
                session_id=ctx.session_db_id,
                content=content,
                title=title,
            )
        changed = "、".join(
            name
            for name, value in (("正文", content), ("标题", title))
            if value is not None
        )
        return f"方案 {plan_id} 的{changed}已更新。"

    @tool
    async def updatePlanSteps(plan_id: int, steps: list) -> str:
        """整份替换方案的步骤数组（**会把步骤状态重置为 pending**）。

        Args:
            steps: 步骤数组，每项形如 {"goal": "...", "files": [...], "done_criteria": "..."}。
                序号与状态由系统生成，不要自己编号、不要写 status。
        """
        normalized, error = _normalize_steps(steps)
        if error:
            return f"步骤列表不合法：{error}请修正后重新调用 updatePlanSteps（方案未改动）。"
        async with database.session() as db:
            plan = await dao.get_plan(db, session_id=ctx.session_db_id, plan_id=plan_id)
            if plan is None:
                return f"未找到方案 {plan_id}（或它不属于本会话）。"
            if plan.status == "closed":
                return f"方案 {plan_id} 已删除（closed），不再修改。"
            if plan.status == "executing":
                return (
                    f"方案 {plan_id} 正在执行（executing），改步骤会让 coder 手上的计划与"
                    "库里的对不上 —— 等这一轮跑完再改，或另出一份新方案。"
                )
            await dao.set_plan_steps(
                db, plan_id=plan_id, session_id=ctx.session_db_id, steps=normalized
            )
        return f"方案 {plan_id} 的步骤已整份替换（共 {len(normalized)} 步，状态重置为 pending）。"

    @tool
    async def deletePlan(plan_id: int) -> str:
        """删除方案（**软删**：状态改为 closed，记录仍留在库里）。

        删除前必须先向用户说明是哪一份方案（plan_id / 标题）并拿到明确同意 —— 不得自行删除。

        Args:
            plan_id: 方案 ID。
        """
        async with database.session() as db:
            plan = await dao.get_plan(db, session_id=ctx.session_db_id, plan_id=plan_id)
            if plan is None:
                return f"未找到方案 {plan_id}（或它不属于本会话）。"
            if plan.status == "closed":
                return f"方案 {plan_id} 之前已经删过（closed）。"
            await dao.set_plan_status(
                db, plan_id, "closed", session_id=ctx.session_db_id
            )
        return f"方案 {plan_id} 已删除（状态改为 closed）。"

    @tool
    async def get_unexecuted_plan() -> str:
        """取出本会话上一次未执行（pending）的方案（结果里带 plan_id）。"""
        async with database.session() as db:
            plan = await dao.get_unexecuted_plan(db, session_id=ctx.session_db_id)
            if plan is None:
                return "没有未执行的方案。"
            state = get_configurable("turn_state")
            if isinstance(state, TurnState):
                state.plan_id = plan.id
            return _format_plan(plan)

    @tool
    async def updatePlanStatus(plan_id: int, status: str) -> str:
        """更新方案状态（coder 开始 / 完成 / 失败时回写）。

        Args:
            plan_id: 方案 ID（get_unexecuted_plan 返回结果里带）。
            status: 新状态，取值 executing / done / failed。
        """
        if status not in ("executing", "done", "failed"):
            return f"非法状态：{status}（应为 executing / done / failed）"
        async with database.session() as db:
            await dao.set_plan_status(db, plan_id, status)
        return f"方案 {plan_id} 状态已更新为 {status}。"

    @tool
    async def updatePlanStep(plan_id: int, index: int, status: str) -> str:
        """回写方案中某一步的状态（coder 每步开始 / 完成 / 失败时调用）。

        Args:
            plan_id: 方案 ID（get_unexecuted_plan 返回结果里带）。
            index: 步骤序号，从 1 开始（get_unexecuted_plan 里的编号）。
            status: 步骤状态，取值 pending / running / done / failed。
        """
        if status not in _STEP_STATUSES:
            return f"非法状态：{status}（应为 {' / '.join(_STEP_STATUSES)}）。"
        if index < 1:
            return f"非法步骤序号：{index}（从 1 开始）。"
        async with database.session() as db:
            steps = await dao.set_plan_step_status(
                db,
                plan_id=plan_id,
                session_id=ctx.session_db_id,
                index=index,
                status=status,
            )
        if steps is None:
            return (
                f"未更新方案 {plan_id} 的第 {index} 步：方案不存在、不属于本会话、"
                f"序号越界，或该方案没有结构化步骤。"
            )
        finished = sum(
            1 for s in steps if isinstance(s, dict) and s.get("status") == "done"
        )
        unfinished = [
            s.get("index", i)
            for i, s in enumerate(steps, 1)
            if not isinstance(s, dict) or s.get("status") != "done"
        ]
        return (
            f"第 {index}/{len(steps)} 步已标记为 {status}；"
            f"已完成 {finished} 步，未完成：{unfinished or '无'}。"
        )

    return [
        storePlan,
        getPlan,
        planExists,
        updatePlan,
        updatePlanSteps,
        deletePlan,
        get_unexecuted_plan,
        updatePlanStatus,
        updatePlanStep,
    ]
