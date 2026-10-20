"""Server 轮次编排的共享常量与小工具。"""


import json
import logging
from collections.abc import Mapping
from typing import Any

from ai_coding_harness.agent.llm import (
    resolve_model,
    resolve_window,
)
from ai_coding_harness.agent.profiles import (
    ANALYSIS_FORM,
    ORCHESTRATE_FORM,
    REVIEW_FORM,
    profile_for,
)
from ai_coding_harness.config import Settings
from ai_coding_harness.safety.redact import (
    CRED_TERM,
    redact,
)

logger = logging.getLogger(__name__)


_TITLE_MAX = 30

# 代码工作区建不出来时对用户说的话（原始异常只进日志与 sessions.workspace_error）
WORKSPACE_ERROR_MESSAGE = "代码工作区创建异常，请重试"

# 会改动方案的三个工具：调完就该把最新方案推给前端刷新进度面板。
# 会改动方案的那几个：跑完就重读一份快照推给前端（幂等，同一状态只推一帧）。
# ``getPlan`` 不在里面 —— 它只读，读了不该动面板。
_PLAN_TOOLS = frozenset(
    {
        "storePlan",
        "updatePlan",
        "updatePlanSteps",
        "deletePlan",
        "updatePlanStep",
        "updatePlanStatus",
    }
)

#: 收尾时该给仓库记忆打底的形态 —— 与「谁能写记忆正文」是同一批（``orchestrate`` 上的
#: coding / rework、``review``、``analysis``）：这几轮才有要沉淀的事实（改动 / 审查结论 /
#: 仓库现状），其余形态只读注入（平台侧的刷新由记忆中间件在开工前做掉）。
_MEMORY_WRITE_FORMS: frozenset[str] = frozenset(
    {ORCHESTRATE_FORM, REVIEW_FORM, ANALYSIS_FORM}
)

# 一轮跑到一半炸了时的标记：已经有正文就说明上面是部分输出，一条都没有就是未产出。
INTERRUPTED_PARTIAL = "〔本轮中断，以上为部分输出〕"
INTERRUPTED_EMPTY = "〔本轮中断，未产出内容〕"
# 用户点了「终止」：不是故障，措辞里不出现异常名（前端本地也补同一句，刷新前后一致）
CANCELLED_BY_USER = "〔已由用户终止〕"


def plan_payload(plan: Any) -> dict[str, Any]:
    """把方案行序列化成前端要的快照（SSE 的 plan 事件与 GET plan 接口共用）。

    ``steps`` 容忍历史脏数据（整列不是 list、元素不是 dict）：跳掉渲染不了的条目，
    而不是让一个坏步骤把整个面板打挂。
    """
    raw_steps = plan.steps if isinstance(plan.steps, list) else []
    steps = [
        {
            "index": step.get("index", i),
            "goal": str(step.get("goal") or ""),
            "status": step.get("status", "pending"),
            "files": step.get("files"),
            "done_criteria": step.get("done_criteria"),
        }
        for i, step in enumerate(raw_steps, 1)
        if isinstance(step, dict)
    ]
    return {
        "plan_id": plan.id,
        "title": plan.title,
        "status": plan.status,
        "steps": steps,
        "content": plan.content,
        "updated_at": plan.updated_at.isoformat(),
    }


def _main_window(settings: Settings, intent: str) -> int:
    """本轮**主控模型**的上下文窗口（tokens）；取不到配置时返回 0。

    窗口必须按主控那个模型算：压缩只作用于主控那份历史，而子智能体各有自己的窗口，
    拿子智能体的窗口去除主控的输入会得出「永远安全」。解析只有一处口径
    （``llm.resolve_window``）：**那一行填了就用它 → 出厂默认（.env 那家的窗口）→ 128k**。

    兜底是**故意的宽**：这只是一条提示，配置缺项或角色没登记都不该让这一轮跑不起来 ——
    没有压力提示远好过整轮失败。
    """
    try:
        return resolve_window(settings, profile_for(intent).form)
    except Exception:
        logger.exception("读上下文窗口失败（本轮不做压力提醒）")
        return 0


def _ratio(peak: int, window: int) -> float:
    """峰值输入占窗口的比例，保留三位小数；窗口未知（0）时返回 0。"""
    return round(peak / window, 3) if window > 0 else 0.0


def _main_model(settings: Settings, intent: str) -> str:
    """本轮**主控模型**的名字（与 ``_main_window`` 同一个解析口径）；取不到回空串。

    界面把它标在这条回答的最下边（2026-10-14 用户口径：「提供回答的模型」）。
    解析失败只是不显示那一格，不该让这一轮跑不起来。
    """
    try:
        return resolve_model(settings, profile_for(intent).form)
    except Exception:
        logger.exception("读主控模型名失败（本轮不标模型）")
        return ""


#: 跑动中重发 usage 帧的最小间隔（秒）。工具调用密集的轮次里每帧几百字节，
#: 不节流会让同一条消息在一秒内重渲染几十次；0.5 秒对「看着它长」已经足够。
_USAGE_FRAME_MIN_GAP = 0.5

#: 改动的文件清单最多列这么多条（超出只报总数）：一轮改上百个文件更像事故而不是成果，
#: 界面上也不该为它铺一屏。
_CHANGES_MAX_FILES = 200


def _pr_refs(raw: Any) -> dict[str, str] | None:
    """从 ``create_pr`` 的**入参**里取 ``base`` / ``head`` —— 两个都缺就返回 ``None``。

    拿入参而不是去查 PR 列表：这一轮的 PR 是这一轮建的，入参就是它自己的声明（没有歧义，
    也不用多打一次平台接口）。v3 的工具入参有时是 dict、有时是一段 JSON 字符串
    （与 ``subagent_name`` 同一个坑），两种都认。
    """
    data: Any = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(data, Mapping):
        return None
    base = str(data.get("base") or "").strip()
    head = str(data.get("head") or "").strip()
    return {"base": base, "head": head} if base and head else None


def _redacted_todos(items: list[dict], terms: tuple[str, ...]) -> list[dict]:
    """待办树脱敏：主项和它的 children 都得过，漏一层等于没做。

    子项只保留 content / status 两个键（树只有两级，children 不再往下递归）。
    """
    safe: list[dict] = []
    for item in items:
        clean: dict[str, Any] = {"content": redact(item["content"], terms), "status": item["status"]}
        children = item.get("children")
        if children:
            clean["children"] = [
                {"content": redact(kid["content"], terms), "status": kid["status"]}
                for kid in children
            ]
        safe.append(clean)
    return safe


def _redacted_parts(parts: list[dict], terms: tuple[str, ...]) -> list[dict]:
    """落库前把每个块过一遍脱敏。

    翻译器手上那份是**未脱敏**的原文（脱敏统一在出口做，免得同一份文本被过滤两遍、
    口径还不一致）——SSE 出口已经过了一道，落库这道不能省。
    """
    safe: list[dict] = []
    for part in parts:
        if part["kind"] == "text":
            safe.append({"kind": "text", "content": redact(part["content"], terms)})
        else:
            safe.append({"kind": "todos", "items": _redacted_todos(part["items"], terms)})
    return safe


def _title_from(message: str) -> str:
    """会话标题取首条用户消息：压成单行（含换行/多空格）再截断。"""
    line = " ".join(message.split())
    return line[:_TITLE_MAX] + ("…" if len(line) > _TITLE_MAX else "")


# 入口拒收的回执：只说命中了哪一类，绝不复述命中的内容（回执本身也要下发与落库）
_REFUSAL_HINTS = {
    CRED_TERM: "请把凭据换成占位符再发。",
    "赋值字面量": "如果是在贴代码，请把字面量换成读环境变量的写法（如 os.getenv）。",
}
_REFUSAL_HINT = "沙箱里的凭据由平台注入，不需要贴进对话。"


def credential_refusal(label: str) -> str:
    """含凭据消息的拒收回执。"""
    return (
        f"这条消息里检测到疑似凭据（{label}），已拒绝执行，也没有发送给模型。"
        f"{_REFUSAL_HINTS.get(label, _REFUSAL_HINT)}"
    )
