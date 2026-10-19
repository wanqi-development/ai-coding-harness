"""压缩前的应急工具裁剪：只保留「当前这一步」，其余换成带身份占位符。

2026-10-17 用户口径：**不要常规剪裁** —— 平时不按 40k 阈值剪，只有到达压缩线
（``CONTEXT_SUMMARY_*``）时才做一次应急裁剪，交给压缩中间件判断「剪完能不能免掉
这次摘要」。裁剪保留**当前步骤 + 模型工作笔记里 ``KEEP:`` 点名的工具返回**（2026-10-19），
不再按「最后一条 HumanMessage 之后全部保留」切。见
``agent/summarization.py::HarnessSummarizationMiddleware``。

这一步只改**这一次请求**：不删消息、不改 ``tool_call_id``、不动 checkpoint / 业务库；
消息条数与工具调用配对不变，回退只要关掉 ``CONTEXT_TRIM_TOOL_RESULTS``。

占位符**必须带身份**（哪个工具、哪个对象、原文多大），而且不能劝模型「重新读一次」——
无名的占位符会让它防御性重读，实测把一轮分析拖到递归上限（见 ``trimmed_placeholder``）。

``context_token_counter`` 是压缩与剪裁共用的中英混合口径（见 ``effective_chars_per_token``）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

#: 认「这次调用在动哪个对象」时按顺序看的入参键（文件类 → 命令类 → 检索类）。
_TARGET_KEYS: tuple[str, ...] = (
    "file_path",
    "path",
    "command",
    "pattern",
    "glob",
    "query",
    "url",
)

#: 对象渲染成一行的上限（``execute`` 的命令可能是几百字的 shell，全塞进去比原文还贵）
_TARGET_LIMIT = 120

#: 哪些键的值是**路径**（按尾部截断，见 ``_shorten_path``）
_PATH_KEYS = frozenset({"file_path", "path"})


def trimmed_placeholder(tool: str, target: str, chars: int) -> str:
    """剪掉一条工具返回之后留在原位的那句话 —— **带身份**，且不劝它重读。

    为什么不能是无名的：2026-10-16 查那轮 analysis 触顶（``recursion_limit=200`` 用完）时量到，
    模型把 ``AuthServiceImpl.java`` 读了 8 次、同一批文件用 ``execute`` 各 dump 了 6~7 次
    （160 个段落标记、去重只有 42 个），结尾还把开头读过的那批从头读了一遍。当时占位符写的是
    「此处原本是一次工具返回的内容 …… 需要时重新读一次 / 重新跑一次即可」—— **不说剪掉的是
    哪个工具、哪个文件，还明写「重新读一次」**，于是模型分不清「我读过、结论已经有了」与
    「我还没读」，只能防御性重取；而每重取一次又把别的顶出去被剪，成了跑步机。

    原文大小用 ``len()`` 而不是 token 估算：这一步每次模型调用都要对上百条重跑一遍，
    为一句提示去扫全部原文不划算（字数只是给它一个「重取代价多大」的量级感）。
    """
    return (
        f"〔已裁剪〕{tool} → {target}（原文 {chars:,} 字）。"
        "你已经读过它：先用你已经得出的结论往下走，只有确实缺关键细节时才重取。"
    )


def _one_line(text: str, limit: int) -> str:
    """压成一行并按 ``limit`` 截断（占位符必须是一行：它是替掉整份文件的那句话）。"""
    flat = " ".join(text.split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


def _shorten_path(text: str, limit: int) -> str:
    """路径按**尾部**留：认得出一个文件靠的是文件名与它上面那一两层目录。

    前缀（沙箱里的仓库根，几十条占位符里一字不差）没有信息量，而按头部截断正好把有用的
    那段切掉 —— 剩下一个既认不出、也没法拿去重读的路径，等于白写。
    """
    if len(text) <= limit:
        return text
    kept: list[str] = []
    used = len("…/")
    for part in reversed(text.split("/")):
        if not part:
            continue
        if used + len(part) + (1 if kept else 0) > limit:
            break
        kept.append(part)
        used += len(part) + 1
    if not kept:
        return "…" + text[-(limit - 1) :]
    return "…/" + "/".join(reversed(kept))


def _target_of(args: Any) -> str:
    """把工具入参压成一行「对象」：文件路径 / 命令 / 检索词，认不出就退化成截断的 JSON。"""
    if not isinstance(args, Mapping):
        return _one_line(str(args), _TARGET_LIMIT) if args is not None else "（无入参）"
    for key in _TARGET_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            if key in _PATH_KEYS:
                return _shorten_path(" ".join(value.split()), _TARGET_LIMIT)
            return _one_line(value, _TARGET_LIMIT)
    try:
        return _one_line(json.dumps(args, ensure_ascii=False, default=repr), _TARGET_LIMIT)
    except (TypeError, ValueError):
        return _one_line(repr(args), _TARGET_LIMIT)


#: 中文口径的「字符 / token」比。上游默认 4.0 是**英文**口径；中文实测 ≈1.6（GLM 上量到
#: 0.45~0.68 token/字）—— 照默认值算会把中文上下文低估约 3 倍，阈值/触发线永远不到
#: （同一个坑上游的压缩线也在踩，见 CHANGELOG 的遗留）。取偏小的值＝估得偏多＝早一点触发。
CJK_CHARS_PER_TOKEN = 1.6

#: 非中文（英文 / 代码 / JSON）的「字符 / token」比。上游那个 4.0 就是这一档的口径；
#: 实测代码约 3.5~4 字符/token，这里取 3.8。
LATIN_CHARS_PER_TOKEN = 3.8

#: 认作「中日韩」的字符范围：CJK 标点、扩展 A、基本区、兼容区，外加全角形。
#: 只用来算**比例**，不追求分词准确 —— 判错几个字符对比例没有可见影响。
_CJK_RE = re.compile("[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")

#: **永远不剪**的工具：结果都很小（剪了没收益），但它们是给模型看的进度骨架。
KEEP_TOOLS: tuple[str, ...] = (
    "write_todos",
    "updatePlanStep",
    "updatePlanStatus",
    "planExists",
    "task",
)

#: 应急裁剪时，除了「当前这一步」之外还保留几条最近的工具返回。
#: 默认 0：只保当前步骤，最大化免压概率；占位符带身份，减少模型重读。
PRETRIM_KEEP_RECENT_TOOL_RESULTS = 0

#: 工作笔记里 ``KEEP:`` 最多认几条；按模型给的顺序（它自己排的优先级）。
PRETRIM_KEEP_ITEMS = 8

#: 被 ``KEEP`` 点名的工具返回最多占多少 token；超出的按“最近优先”保留。
#: 这不是摘要的 keep 窗口 —— 只是应急裁剪时允许额外留下的原文预算。
PRETRIM_KEEP_TOKENS = 20_000


def _message_text(message: BaseMessage) -> str:
    """取一条消息里**用于计数**的文本（多模态块只取其中的 text）。"""
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return ""


def keep_items_from_messages(
    messages: Sequence[BaseMessage], *, limit: int = PRETRIM_KEEP_ITEMS
) -> tuple[str, ...]:
    """从最近的 AI 工作笔记里取 ``KEEP:`` 清单。

    笔记是模型在 90% 提醒后自己写的，格式约定见 ``summarization.PRETRIM_NOTICE``：
    优先写 ``tool_call_id``，不知道就写 ``工具名:目标``。只扫描最近三条 AI 消息，
    避免把很早以前的笔记当成当前需求。
    """
    seen = 0
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            # 一轮的边界：主控是用户消息，子智能体是任务描述。旧轮次的 KEEP 不能拿来用。
            break
        if not isinstance(message, AIMessage):
            continue
        text = _message_text(message)
        match = re.search(r"KEEP[*_ ]*\s*[:：]\s*(.+)", text, re.IGNORECASE)
        if match:
            raw = match.group(1).split("</working-note>")[0].strip()
            items: list[str] = []
            for part in re.split(r"[,，;；\n]", raw):
                item = part.strip().strip("`'\" ")
                if item and item not in items:
                    items.append(item)
            return tuple(items[:limit])
        seen += 1
        if seen >= 10:
            break
    return ()


def effective_chars_per_token(messages: Sequence[BaseMessage]) -> float:
    """按这批消息里**中文所占的比例**折出一个等效的「字符 / token」。

    一个常量喂不了两种文本：中文约 1.6 字符/token，而代码 / 英文约 3.8。上一版统一按 1.6
    算，实测把**代码高估 2.49 倍**（与中文那 2.48 倍几乎一样）—— 这个上下文里绝大部分是
    代码（``read_file`` / ``grep`` / diff），于是压缩的窗口比例**被推歪**，而且歪多少取决于
    当轮的中文/代码配比，调参没有意义。

    按「中文比 / 1.6 + 其余比 / 3.8」取调和，等价于分段计数，但**只折一个系数** ——
    于是仍能复用上游 ``count_tokens_approximately`` 那套账（每条消息的固定开销、工具
    schema、图片按张计），不必自己重写一遍。

    工具 schema 没进比例（它们是 JSON 结构 + 少量中文描述，量级远小于历史消息），按折出来
    的系数一并计 —— 这点偏差是有意的取舍，换来的是不复制上游的序列化逻辑。
    """
    text = "".join(_message_text(message) for message in messages)
    if not text:
        return LATIN_CHARS_PER_TOKEN
    ratio = len(_CJK_RE.findall(text)) / len(text)
    return 1.0 / (ratio / CJK_CHARS_PER_TOKEN + (1.0 - ratio) / LATIN_CHARS_PER_TOKEN)


def context_token_counter(
    messages: Sequence[BaseMessage], *, tools: Sequence[Any] | None = None
) -> int:
    """按**中英混合**口径估 token 数（系数见 ``effective_chars_per_token``）。

    压缩中间件用它判断触发线（含系统提示与工具 schema）；应急裁剪也用它算剪前剪后，
    两边同口径。``tools`` 是上游会传的参（工具 schema 也算上下文），照传下去。
    """
    return count_tokens_approximately(
        messages,
        chars_per_token=effective_chars_per_token(messages),
        tools=tools,
    )


def _trim_facts(messages: Sequence[BaseMessage]) -> dict[str, tuple[Any, int]]:
    """按 ``tool_call_id`` 记下「发起它的入参」与「原文有多少字」。

    只能从**剪之前**那份取：剪完之后 content 已经是占位符，而入参根本不在工具返回上、
    在发起它的那条 AI 消息里。一次扫两遍（先建 id → 入参，再配工具返回），都是字典操作。
    """
    args_by_id: dict[str, Any] = {}
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls or []:
                call_id = call.get("id")
                if call_id:
                    args_by_id[call_id] = call.get("args")
    facts: dict[str, tuple[Any, int]] = {}
    for message in messages:
        if isinstance(message, ToolMessage) and message.tool_call_id:
            facts[message.tool_call_id] = (
                args_by_id.get(message.tool_call_id),
                len(_message_text(message)),
            )
    return facts


def _tool_call_owners(messages: Sequence[BaseMessage]) -> dict[str, int]:
    """``tool_call_id`` → 发起它的那条 AI 消息的下标（清入参时用）。"""
    owners: dict[str, int] = {}
    for index, message in enumerate(messages):
        if isinstance(message, AIMessage):
            for call in message.tool_calls or []:
                call_id = call.get("id")
                if call_id:
                    owners[call_id] = index
    return owners


def _clear_tool_call_args(message: BaseMessage, call_ids: set[str]) -> BaseMessage:
    """把一条 AI 消息里指定 tool call 的 args 清空（与上游 ``clear_tool_inputs`` 同语义）。"""
    if not isinstance(message, AIMessage):
        return message
    updated: list[Any] = []
    changed = False
    for call in message.tool_calls or []:
        if call.get("id") in call_ids:
            updated.append({**call, "args": {}})
            changed = True
        else:
            updated.append(call)
    if not changed:
        return message
    metadata = dict(message.response_metadata)
    context_entry = dict(metadata.get("context_editing", {}))
    cleared_inputs = set(context_entry.get("cleared_tool_inputs", []))
    cleared_inputs.update(call_ids)
    context_entry["cleared_tool_inputs"] = sorted(cleared_inputs)
    metadata["context_editing"] = context_entry
    return message.model_copy(
        update={"tool_calls": updated, "response_metadata": metadata}
    )


def pretrim_tool_results(
    messages: Sequence[BaseMessage],
    *,
    keep_recent: int = PRETRIM_KEEP_RECENT_TOOL_RESULTS,
    clear_tool_inputs: bool = False,
    keep_items: Sequence[str] = (),
    keep_budget_tokens: int = PRETRIM_KEEP_TOKENS,
) -> tuple[list[BaseMessage], int]:
    """压缩前的**应急裁剪**：清掉「当前这一步 + KEEP 清单」以外的工具返回。

    只在压缩线处调用，不是每次模型调用都跑。保留消息列表**末尾连续的那一段 ToolMessage**
    —— 那通常是模型这一轮刚拿到的工具结果，剪掉它等于让模型当场失忆。再往前的一律换成
    带身份占位符，除非它被模型工作笔记里的 ``KEEP:`` 点名。

    ``keep_recent`` 是「当前步骤之外再保留几条」的逃生门，默认 0。
    ``keep_items`` 是模型工作笔记里的 ``KEEP:`` 清单（tool_call_id 或 ``工具名:目标``）；
    被点名的 ToolMessage 保留原文，但总额受 ``keep_budget_tokens`` 限制（最近优先）。

    返回新列表与清除条数；**不删消息、不改 ``tool_call_id``、不动状态**。
    """
    result = list(messages)
    if not result:
        return result, 0

    # 当前这一步 = 末尾连续的 ToolMessage（并行工具调用会有多条）。
    protected: set[int] = set()
    index = len(result) - 1
    while index >= 0 and isinstance(result[index], ToolMessage):
        protected.add(index)
        index -= 1

    if keep_recent > 0:
        kept = 0
        for index in range(len(result) - 1, -1, -1):
            if kept >= keep_recent:
                break
            if isinstance(result[index], ToolMessage) and index not in protected:
                protected.add(index)
                kept += 1

    facts = _trim_facts(result)
    names_by_id = {
        call.get("id"): call.get("name")
        for message in result
        if isinstance(message, AIMessage)
        for call in (message.tool_calls or [])
        if call.get("id")
    }
    wanted_ids: set[str] = set()
    wanted_targets: list[tuple[str, str]] = []
    for raw in keep_items:
        item = raw.strip().strip("`'\" ")
        if not item:
            continue
        if ":" in item:
            name, _, target = item.partition(":")
            wanted_targets.append((name.strip().lower(), target.strip().lower()))
        elif "/" in item or "\\" in item or " " in item:
            wanted_targets.append(("", item.lower()))
        else:
            wanted_ids.add(item.lower())

    def _full_target(args: Any) -> str:
        if isinstance(args, Mapping):
            for key in _TARGET_KEYS:
                value = args.get(key)
                if isinstance(value, str) and value.strip():
                    return " ".join(value.split())
        return _one_line(str(args), 500)

    def _wanted(tool_name: str, call_id: str | None, args: Any) -> bool:
        if call_id and call_id.lower() in wanted_ids:
            return True
        target = _full_target(args).lower()
        return any(
            (not name or name == tool_name.lower()) and bool(value) and value in target
            for name, value in wanted_targets
        )

    # KEEP 点名的结果单独记账；预算按“最近优先”（同一份笔记里它是按重要性排的，
    # 但这里只负责别让几份大文件把应急裁剪彻底撑爆）。
    matched: list[tuple[int, int]] = []
    for index, message in enumerate(result):
        if not isinstance(message, ToolMessage) or index in protected:
            continue
        tool_name = message.name or names_by_id.get(message.tool_call_id) or ""
        args, _chars = facts.get(message.tool_call_id, (None, 0))
        if _wanted(tool_name, message.tool_call_id, args):
            matched.append((index, context_token_counter([message])))
    kept_by_note: set[int] = set()
    used = 0
    for index, tokens in reversed(matched):
        if used + tokens <= keep_budget_tokens:
            kept_by_note.add(index)
            used += tokens
    owners = _tool_call_owners(result) if clear_tool_inputs else {}
    cleared_ids_by_owner: dict[int, set[str]] = {}
    cleared = 0
    for index, message in enumerate(result):
        if (
            not isinstance(message, ToolMessage)
            or index in protected
            or index in kept_by_note
        ):
            continue
        tool_name = message.name or names_by_id.get(message.tool_call_id) or ""
        if tool_name in KEEP_TOOLS:
            continue
        if message.response_metadata.get("context_editing", {}).get("cleared"):
            continue
        args, chars = facts.get(message.tool_call_id, (None, 0))
        result[index] = message.model_copy(
            update={
                "artifact": None,
                "content": trimmed_placeholder(
                    tool_name or "工具", _target_of(args), chars
                ),
                "response_metadata": {
                    **message.response_metadata,
                    "context_editing": {
                        "cleared": True,
                        "strategy": "pretrim",
                    },
                },
            }
        )
        cleared += 1
        if clear_tool_inputs and message.tool_call_id:
            owner = owners.get(message.tool_call_id)
            if owner is not None:
                cleared_ids_by_owner.setdefault(owner, set()).add(message.tool_call_id)

    for owner, call_ids in cleared_ids_by_owner.items():
        result[owner] = _clear_tool_call_args(result[owner], call_ids)

    return result, cleared
