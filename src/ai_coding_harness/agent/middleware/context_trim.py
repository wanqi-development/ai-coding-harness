"""上下文裁剪事件：checkpoint 保留原文，模型请求按稳定 ID 做动态投影。

2026-10-17 用户口径：**不要常规剪裁** —— 平时不按 40k 阈值剪，只有到达压缩线
（``CONTEXT_SUMMARY_*``）时才做一次应急裁剪，交给压缩中间件判断「剪完能不能免掉
这次摘要」。裁剪保留**当前步骤 + 模型工作笔记里 ``KEEP:`` 点名的工具返回**（2026-10-19），
不再按「最后一条 HumanMessage 之后全部保留」切。见
``agent/summarization.py::HarnessSummarizationMiddleware``。

事件本身写进 checkpoint 的私有字段 ``_context_trim_event``，但只保存 ``tool_call_id``；
``messages`` 永远是原文。每次请求按“摘要事件 → 裁剪事件”重新拼出模型可见消息，因此不删
消息、不改 ``tool_call_id``、不动业务原文。回退只要关掉 ``CONTEXT_TRIM_TOOL_RESULTS``。

占位符**必须带身份**（哪个工具、哪个对象、原文多大），而且不能劝模型「重新读一次」——
无名的占位符会让它防御性重读，实测把一轮分析拖到递归上限（见 ``trimmed_placeholder``）。

``context_token_counter`` 是压缩与剪裁共用的中英混合口径（见 ``effective_chars_per_token``）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

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
    "get_unexecuted_plan"
)

#: 应急裁剪时，除了「当前这一步」之外还保留几条最近的工具返回。
#: 默认 0：只保当前步骤，最大化免压概率；占位符带身份，减少模型重读。
PRETRIM_KEEP_RECENT_TOOL_RESULTS = 0

#: 工作笔记里 ``KEEP:`` 最多认几条；按模型给的顺序（它自己排的优先级）。
PRETRIM_KEEP_ITEMS = 8

#: 被 ``KEEP`` 点名的工具返回最多占多少 token；超出的按“最近优先”保留。
#: 这不是摘要的 keep 窗口 —— 只是应急裁剪时允许额外留下的原文预算。
PRETRIM_KEEP_TOKENS = 20_000

#: 工具结果小于这个字符数时不裁剪：普通命令输出和短状态值的节省抵不过占位符开销。
PRETRIM_MIN_RESULT_CHARS = 2_000

#: 工具入参中的单个字符串小于这个字符数时不压缩；路径、offset、timeout 等短字段永远保留。
PRETRIM_MIN_ARG_CHARS = 2_000

#: 压缩历史入参时保留的开头长度。它只用于识别调用，不承担恢复原始内容。
_ARG_PREVIEW_CHARS = 120

#: 不论阈值多小都保留的参数键：它们定义「调用的是哪个对象」，通常也很短。
_ARG_PRESERVE_KEYS = frozenset(
    {
        "file_path",
        "path",
        "pattern",
        "glob",
        "query",
        "url",
        "offset",
        "limit",
        "timeout",
    }
)

#: 裁剪事件版本。以后改变字段语义时递增，旧版本按“不应用”处理。
CONTEXT_TRIM_EVENT_VERSION = 1


class ContextTrimEvent(TypedDict):
    """checkpoint 里的裁剪投影；只存稳定 ID，不复制原文或占位符。"""

    version: int
    trimmed_result_ids: list[str]
    compacted_arg_ids: list[str]


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


def _compact_arg_value(value: str) -> str:
    """把一个超大历史入参压成可识别的一行，不保留正文主体。"""
    preview = " ".join(value[:_ARG_PREVIEW_CHARS].split())
    return f"{preview}〔历史入参已压缩：原 {len(value):,} 字〕"


def _compact_tool_call_args(
    message: BaseMessage,
    call_ids: set[str],
    *,
    min_arg_chars: int,
) -> BaseMessage:
    """只压缩指定 tool call 中的超大字符串字段，保留参数结构与关键字段。"""
    if not isinstance(message, AIMessage):
        return message
    updated: list[Any] = []
    changed = False
    for call in message.tool_calls or []:
        if call.get("id") in call_ids:
            args = call.get("args")
            if isinstance(args, Mapping):
                compacted = {
                    key: (
                        _compact_arg_value(value)
                        if isinstance(value, str) and len(value) > min_arg_chars
                        and key not in _ARG_PRESERVE_KEYS
                        else value
                    )
                    for key, value in args.items()
                }
                if compacted != args:
                    updated.append({**call, "args": compacted})
                    changed = True
                else:
                    updated.append(call)
            else:
                updated.append(call)
        else:
            updated.append(call)
    if not changed:
        return message
    metadata = dict(message.response_metadata)
    context_entry = dict(metadata.get("context_editing", {}))
    compacted_inputs = set(context_entry.get("compacted_tool_inputs", []))
    compacted_inputs.update(call_ids)
    context_entry["compacted_tool_inputs"] = sorted(compacted_inputs)
    metadata["context_editing"] = context_entry
    return message.model_copy(
        update={"tool_calls": updated, "response_metadata": metadata}
    )


def _event_sets(
    event: Mapping[str, Any] | ContextTrimEvent | None,
) -> tuple[set[str], set[str]]:
    """读事件里的两个 ID 集合；未知版本按空事件处理。"""
    if not isinstance(event, Mapping):
        return set(), set()
    if event.get("version") != CONTEXT_TRIM_EVENT_VERSION:
        return set(), set()
    results = event.get("trimmed_result_ids", [])
    args = event.get("compacted_arg_ids", [])
    return (
        {str(item) for item in results if item},
        {str(item) for item in args if item},
    )


def merge_context_trim_event(
    event: Mapping[str, Any] | ContextTrimEvent | None,
    *,
    result_ids: Sequence[str] = (),
    arg_ids: Sequence[str] = (),
) -> ContextTrimEvent | None:
    """把新 ID 并入已有事件；没有任何内容时返回 ``None``。"""
    results, args = _event_sets(event)
    results.update(item for item in result_ids if item)
    args.update(item for item in arg_ids if item)
    if not results and not args:
        return None
    return {
        "version": CONTEXT_TRIM_EVENT_VERSION,
        "trimmed_result_ids": sorted(results),
        "compacted_arg_ids": sorted(args),
    }


def apply_context_trim_event(
    messages: Sequence[BaseMessage],
    event: Mapping[str, Any] | ContextTrimEvent | None,
) -> list[BaseMessage]:
    """把裁剪事件确定性地投影成模型可见消息，不修改原始消息。"""
    result = list(messages)
    trimmed_ids, compacted_ids = _event_sets(event)
    if not result or (not trimmed_ids and not compacted_ids):
        return result

    facts = _trim_facts(result)
    names_by_id = {
        call.get("id"): call.get("name")
        for message in result
        if isinstance(message, AIMessage)
        for call in (message.tool_calls or [])
        if call.get("id")
    }
    for index, message in enumerate(result):
        if (
            isinstance(message, ToolMessage)
            and message.tool_call_id
            and message.tool_call_id in trimmed_ids
        ):
            tool_name = message.name or names_by_id.get(message.tool_call_id) or "工具"
            args, chars = facts.get(message.tool_call_id, (None, 0))
            result[index] = message.model_copy(
                update={
                    "artifact": None,
                    "content": trimmed_placeholder(
                        tool_name, _target_of(args), chars
                    ),
                    "response_metadata": {
                        **message.response_metadata,
                        "context_editing": {
                            "cleared": True,
                            "strategy": "pretrim_event",
                        },
                    },
                }
            )

    for index, message in enumerate(result):
        if isinstance(message, AIMessage):
            result[index] = _compact_tool_call_args(
                message,
                compacted_ids,
                # 选入事件的 ID 已经过配置阈值判断；投影时不再依赖当前进程的阈值，
                # 否则同一 checkpoint 换一个配置后会得到不同上下文。
                min_arg_chars=0,
            )
    return result


def prune_context_trim_event(
    event: Mapping[str, Any] | ContextTrimEvent | None,
    messages: Sequence[BaseMessage],
    cutoff_index: int,
) -> ContextTrimEvent | None:
    """摘要后移除 cutoff 之前已经看不见的 ID，控制事件体积。"""
    results, args = _event_sets(event)
    if not results and not args:
        return None
    hidden_ids: set[str] = set()
    for message in messages[: max(0, cutoff_index)]:
        if isinstance(message, ToolMessage) and message.tool_call_id:
            hidden_ids.add(message.tool_call_id)
        elif isinstance(message, AIMessage):
            hidden_ids.update(
                call["id"]
                for call in (message.tool_calls or [])
                if call.get("id")
            )
    results.difference_update(hidden_ids)
    args.difference_update(hidden_ids)
    if not results and not args:
        return None
    return {
        "version": CONTEXT_TRIM_EVENT_VERSION,
        "trimmed_result_ids": sorted(results),
        "compacted_arg_ids": sorted(args),
    }


def plan_context_trim_event(
    messages: Sequence[BaseMessage],
    *,
    existing: Mapping[str, Any] | ContextTrimEvent | None = None,
    keep_recent: int = PRETRIM_KEEP_RECENT_TOOL_RESULTS,
    compact_tool_inputs: bool = False,
    keep_items: Sequence[str] = (),
    keep_budget_tokens: int = PRETRIM_KEEP_TOKENS,
    min_result_chars: int = PRETRIM_MIN_RESULT_CHARS,
    min_arg_chars: int = PRETRIM_MIN_ARG_CHARS,
) -> tuple[ContextTrimEvent | None, int, int]:
    """按大小阈值选出新的结果/入参 ID，并与已有事件合并。"""
    source = list(messages)
    if not source:
        return merge_context_trim_event(existing), 0, 0

    # 当前这一步 = 末尾连续的 ToolMessage（并行工具调用会有多条）。
    protected: set[int] = set()
    index = len(source) - 1
    while index >= 0 and isinstance(source[index], ToolMessage):
        protected.add(index)
        index -= 1

    if keep_recent > 0:
        kept = 0
        for index in range(len(source) - 1, -1, -1):
            if kept >= keep_recent:
                break
            if isinstance(source[index], ToolMessage) and index not in protected:
                protected.add(index)
                kept += 1

    facts = _trim_facts(source)
    names_by_id = {
        call.get("id"): call.get("name")
        for message in source
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

    matched: list[tuple[int, int]] = []
    for index, message in enumerate(source):
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

    protected_call_ids = {
        source[index].tool_call_id
        for index in protected
        if isinstance(source[index], ToolMessage) and source[index].tool_call_id
    } | {
        source[index].tool_call_id
        for index in kept_by_note
        if source[index].tool_call_id
    }
    for message in source:
        if (
            isinstance(message, ToolMessage)
            and message.name in KEEP_TOOLS
            and message.tool_call_id
        ):
            protected_call_ids.add(message.tool_call_id)

    old_results, old_args = _event_sets(existing)
    new_result_ids: set[str] = set()
    for index, message in enumerate(source):
        if (
            not isinstance(message, ToolMessage)
            or index in protected
            or index in kept_by_note
            or not message.tool_call_id
            or message.tool_call_id in protected_call_ids
            or message.tool_call_id in old_results
            or message.name in KEEP_TOOLS
        ):
            continue
        if len(_message_text(message)) <= min_result_chars:
            continue
        new_result_ids.add(message.tool_call_id)

    new_arg_ids: set[str] = set()
    if compact_tool_inputs:
        for message in source:
            if not isinstance(message, AIMessage):
                continue
            for call in message.tool_calls or []:
                call_id = call.get("id")
                args = call.get("args")
                if (
                    not call_id
                    or call_id in protected_call_ids
                    or call_id in old_args
                    or call.get("name") in KEEP_TOOLS
                    or not isinstance(args, Mapping)
                ):
                    continue
                if any(
                    isinstance(value, str) and len(value) > min_arg_chars
                    for value in args.values()
                ):
                    new_arg_ids.add(call_id)

    merged = merge_context_trim_event(
        existing,
        result_ids=tuple(new_result_ids),
        arg_ids=tuple(new_arg_ids),
    )
    return merged, len(new_result_ids), len(new_arg_ids)


def pretrim_tool_results(
    messages: Sequence[BaseMessage],
    *,
    keep_recent: int = PRETRIM_KEEP_RECENT_TOOL_RESULTS,
    clear_tool_inputs: bool = False,
    keep_items: Sequence[str] = (),
    keep_budget_tokens: int = PRETRIM_KEEP_TOKENS,
    min_result_chars: int = PRETRIM_MIN_RESULT_CHARS,
    min_arg_chars: int = PRETRIM_MIN_ARG_CHARS,
) -> tuple[list[BaseMessage], int]:
    """兼容纯函数入口：计划一次新事件并返回投影后的消息与新裁结果数。"""
    event, new_results, _new_args = plan_context_trim_event(
        messages,
        keep_recent=keep_recent,
        compact_tool_inputs=clear_tool_inputs,
        keep_items=keep_items,
        keep_budget_tokens=keep_budget_tokens,
        min_result_chars=min_result_chars,
        min_arg_chars=min_arg_chars,
    )
    return apply_context_trim_event(messages, event), new_results
