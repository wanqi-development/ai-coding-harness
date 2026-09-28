"""v3 流式协议的拆封与拼装。

``astream_events(..., version="v3")`` 吐的是 ``ProtocolEvent``：
``{type, event_id, seq, method, params}``，真正的内容在 ``params["data"]`` 里，而
``messages`` 方法的 ``data`` 是个 **2 元组** ``(payload, metadata)`` —— 其余方法是单值。
这个形状差异只在这里解释一次，别让调用方各自去 ``[0]`` / ``[1]``。

第二个职责是把 ``messages`` 事件拼成落库用的 ``parts``：正文块按在流里出现的先后
排列，待办收成**一棵树**挂在最后，前端与历史回放都吃这一份。
"""

from __future__ import annotations

import json
import re
from typing import Any

# deepagents 内置的待办工具
TODO_TOOL = "write_todos"
# 委派子智能体的工具（``run_log`` 也认这一个名字）
TASK_TOOL = "task"

# 中间件自己的模型调用会带上这个 metadata 键（值是一次性随机 token，见
# ``langchain/agents/middleware/internal_call_transformer.py``）。langchain 靠
# ``InternalCallTransformer`` 把这类事件从流里丢掉，但那要求中间件**类**声明
# ``transformers = (InternalCallTransformer,)`` —— deepagents 的摘要中间件只把活转包给
# 内层 langchain 实现（``deepagents/middleware/summarization.py`` 的 ``_lc_helper``），
# 自己那条声明没有，于是没人丢：摘要正文顺着根层的 text-delta 流成了助手正文
# （2026-09-21 实见，前 5293 字是 ``## SESSION INTENT`` 那一整段）。
INTERNAL_CALL_KEY = "lc_internal_call"

# 子智能体的事件 namespace：``("tools:<task 的 tool_call_id>",)``
_NS_PREFIX = "tools:"

# deepagents 的待办状态取值（`langchain/agents/middleware/todo.py` 的 Todo.status）
STATUSES = ("pending", "in_progress", "completed")
DEFAULT_STATUS = "pending"

# 从（可能被截断的）JSON 片段里抓一项待办：content 必到，status 紧跟在它后面、**可能还
# 没流到**（args 是累加的 JSON 片段，content 先闭合）。``(?:\\.|[^"\\])*`` 是「吃转义对
# 或普通字符」——待办内容里带引号、反斜杠都常见。
_TODO_ITEM = re.compile(
    r'"content"\s*:\s*"((?:\\.|[^"\\])*)"'
    r'(?:\s*,\s*"status"\s*:\s*"(pending|in_progress|completed)")?'
)


def _status(value: Any) -> str:
    """状态只认三种取值；认不出来（含流式中「还没到」）一律按 pending。"""
    return value if value in STATUSES else DEFAULT_STATUS


def method_of(event: dict) -> str | None:
    """事件走的是哪条通道：``values`` / ``messages`` / ``tools`` …"""
    return event.get("method")


def namespace_of(event: dict) -> tuple[str, ...]:
    """事件所在的图层级。根层为空元组，子智能体是 ``("tools:<id>",)``。

    主/子智能体**只能**靠它区分：两者的 ``langgraph_node`` 同为 ``model``。
    """
    return tuple((event.get("params") or {}).get("namespace") or ())


def delegation_id(namespace: tuple[str, ...]) -> str:
    """子智能体 namespace 里那次 ``task`` 调用的 id；不是一级子智能体就给空串。

    形如 ``("tools:<tool_call_id>",)``。两级以上（子智能体自己又委派）没有对应的
    清单位置，返回空串让调用方丢掉它。
    """
    if len(namespace) != 1 or not namespace[0].startswith(_NS_PREFIX):
        return ""
    return namespace[0][len(_NS_PREFIX) :]


def payload_of(event: dict) -> Any:
    """事件载荷。``messages`` 的 ``data`` 是 ``(payload, metadata)``，取前半。"""
    data = (event.get("params") or {}).get("data")
    return data[0] if isinstance(data, tuple) else data


def metadata_of(event: dict) -> dict:
    """``messages`` 事件的 metadata（``lc_agent_name`` / ``run_id`` 等）；其余通道为空。"""
    data = (event.get("params") or {}).get("data")
    if isinstance(data, tuple) and len(data) > 1 and isinstance(data[1], dict):
        return data[1]
    return {}


def is_internal_call(event: dict) -> bool:
    """这条事件是不是**中间件自己**发起的模型调用（摘要、PII 检查之类）。

    判据只有 ``INTERNAL_CALL_KEY`` 的存在性：那个键的**值**是一次性随机 token，只有
    langchain 内部拿得到，我们不需要比对值 —— 外部没人往这个键上写东西。

    ``tools`` 通道的 ``data`` 是单值（不是二元组），``metadata_of`` 对它返回空字典，
    所以这个判定对工具事件天然为假，不必再判通道。
    """
    return bool(metadata_of(event).get(INTERNAL_CALL_KEY))


def event_name(event: dict) -> str | None:
    """通道内的具体事件名：``content-block-delta`` / ``tool-started`` / …"""
    payload = payload_of(event)
    return payload.get("event") if isinstance(payload, dict) else None


def call_id_of(event: dict) -> str:
    """工具事件的 ``tool_call_id``：把 ``tool-started`` 与 ``tool-finished`` 配对用的键。"""
    payload = payload_of(event)
    return str(payload.get("tool_call_id") or "") if isinstance(payload, dict) else ""


def todo_items(args: str) -> list[dict[str, str]]:
    """从 ``write_todos`` 的参数串里抠出每一项的 content 与 status。

    这份 args 是流式累加的 JSON 片段，**随时可能半截**，不能 ``json.loads``。只认
    **值已闭合**的项：半截的 content 先不显示，免得最后一项逐字抖动。status 排在
    content 后面，流到一帧的后半段才出现也是常态 —— 那时先按 ``pending`` 占位，收尾的
    ``content-block-finish`` 帧会订正成权威值。

    抓到的片段仍用 ``json.loads`` 反转义 —— langchain 序列化时会 ``ensure_ascii``，
    所以真实中文与 ``\\uXXXX`` 转义两种形态都可能在，交给 JSON 自己的规则最省事。
    """
    items: list[dict[str, str]] = []
    for raw, status in _TODO_ITEM.findall(args):
        try:
            content = json.loads(f'"{raw}"')
        except ValueError:
            content = raw
        items.append({"content": content, "status": _status(status)})
    return items


class ToolNames:
    """按 ``tool_call_id`` 给工具调用配对工具名。

    v3 把工具名只放在 ``tool-started`` 上，``tool-finished`` 只剩 ``output`` 与
    ``tool_call_id`` —— 收尾那条事件想标出是谁的返回值，只能回头查。
    """

    def __init__(self) -> None:
        self._names: dict[str, str] = {}

    def remember(self, event: dict) -> str | None:
        """记下 ``tool-started`` 的工具名并返回它。"""
        payload = payload_of(event)
        name = payload.get("tool_name") if isinstance(payload, dict) else None
        if name:
            self._names[call_id_of(event)] = str(name)
        return str(name) if name else None

    def resolve(self, event: dict) -> str | None:
        """取回工具名并忘掉这条配对（一次调用只收尾一次）。"""
        return self._names.pop(call_id_of(event), None)


class StreamParts:
    """一次运行要落库的两种块：正文按先后排列，待办收成**一棵树**放在最后。

    ``{"kind": "text", "content": ...}`` /
    ``{"kind": "todos", "items": [{"content": ..., "status": ..., "children": [...]}]}``

    待办只有一份、每帧整体覆盖：模型每次 ``write_todos`` 给的都是全量清单，追加式的
    块序列只剩一串过时快照。树排在最后是因为它表示「现在什么状态」，不是「说到哪儿
    了」—— 它跟正文不是并列关系。
    """

    def __init__(self) -> None:
        self._parts: list[dict] = []
        self._todos: list[dict] | None = None

    def add_text(self, text: str) -> None:
        if not text:
            return
        last = self._parts[-1] if self._parts else None
        if last is not None and last["kind"] == "text":
            last["content"] += text
        else:
            self._parts.append({"kind": "text", "content": text})

    def set_todos(self, items: list[dict]) -> None:
        """整体替换那棵树（第一次调用即建树）。"""
        self._todos = items

    @property
    def data(self) -> list[dict]:
        parts = list(self._parts)
        if self._todos is not None:
            parts.append({"kind": "todos", "items": self._todos})
        return parts

    @property
    def has_todos(self) -> bool:
        """有没有待办 —— 决定要不要往库里写 ``parts``（纯正文的回合不必写）。"""
        return self._todos is not None


def finished_items(block: Any) -> list[dict[str, str]] | None:
    """取 ``content-block-finish`` 上那份**已解析**的待办清单。

    收尾时 args 已经是 dict，比流中的正则更准，落库以它为准。
    """
    if not isinstance(block, dict):
        return None
    args = block.get("args")
    todos = args.get("todos") if isinstance(args, dict) else None
    if not isinstance(todos, list):
        return None
    return [
        {"content": str(todo.get("content") or ""), "status": _status(todo.get("status"))}
        for todo in todos
        if isinstance(todo, dict)
    ]


def subagent_name(raw: Any) -> str:
    """从 ``task`` 工具入参里取子智能体名，认不出就给空串。

    与 ``run_log._delegation_args`` 取的是同一个字段（那里的用途是日志署名，这里是
    给挂不上主清单的子清单起容器名），畸形入参不抛异常。
    """
    if isinstance(raw, dict):
        return str(raw.get("subagent_type") or raw.get("name") or "")
    return ""


# 子智能体角色 → 任务列表里那一行的中文标签。角色名出自 ``task`` 的 ``subagent_type``，
# 权威清单在 ``agent/subagents.py`` 的 ``_ROLES``；这里刻意**不 import** —— ``stream`` 是
# 协议层，不反向依赖 ``agent`` 层。表里没有的角色照旧原样显示它的名字。
_ROLE_LABELS = {"planner": "规划", "coder": "编码", "reviewer": "评审"}

# ``lc_agent_name`` 里的占位值（主智能体那份是字符串 "None"，不是 None）。
# 与 ``observability/run_log._NO_AGENT`` 同一口径 —— 但**不能 import** 那个常量：
# ``run_log`` 反过来 import 本模块，会成环。
_NO_AGENT_NAMES = frozenset({"", "None", "none"})


def _rollup(items: list[dict[str, str]]) -> str:
    """容器项的状态：全部完成才算完成，有人在做的就是进行中，其余待办。"""
    statuses = {item["status"] for item in items}
    if statuses == {"completed"}:
        return "completed"
    return "in_progress" if "in_progress" in statuses else DEFAULT_STATUS


# 条目文字的挂靠键：模型习惯把这一项的**结果**追加进文字（`实施…提 PR` →
# `实施…提 PR（PR #9）`），逐字比对就认不出是同一项了 —— 归一化时把结尾的补充说明
# 和空白一起抹掉。（2026-09-27 实见：planner / coder 的子清单跑完就掉到树尾。）
_PAREN_TAIL = re.compile(r"[（(\[【][^（()）\[\]【】]{0,40}[)）\]】]$")
_SPACES = re.compile(r"[\s\u3000]+")
_TRIM_CHARS = "·-—:：,，。.、；;"

# 前缀认亲的最短长度。太短会误挂，所以只认够长、且**唯一命中**的（见 ``_locate``）。
_MIN_PREFIX = 4


def _todo_text_key(text: str) -> str:
    """条目文字的挂靠键：去空白 + 去结尾的补充说明（「（plan_id=36）」「（PR #9）」这类）。"""
    key = _SPACES.sub("", str(text))
    while True:
        trimmed = _PAREN_TAIL.sub("", key)
        if trimmed == key:
            break
        key = trimmed
    return key.strip(_TRIM_CHARS)


class V3Translator:
    """把事件流翻译成前端事件，同时攒 ``parts``。

    子智能体的**正文**一律丢掉（内部编排），但它的 ``write_todos`` 挂进主清单那一项
    下当子任务 —— 委派到哪一项，靠 ``note_delegation`` 在 ``task`` 开始时记下来。
    挂不上（主清单为空 / 已全部完成 / 那一项的文字被改没了）时，不丢，改在树尾补一个
    容器项兜住。

    产出的正文是**未脱敏**的原文 —— 脱敏在出口做（SSE 与落库各一处），
    免得同一份文本被过滤两遍、口径还不一致。
    """

    def __init__(self) -> None:
        self.parts = StreamParts()
        # 主清单（顶层；子项在 tree() 里现挂）
        self._main: list[dict[str, str]] = []
        # (namespace, block 下标) → 这个块是不是 write_todos。下标按消息编号，所以必须
        # 带上 namespace：主智能体与子智能体的块号会撞。
        self._todo_blocks: set[tuple[tuple[str, ...], Any]] = set()
        # task 的 tool_call_id → 挂到主清单哪一项（那一项的 content）
        self._parents: dict[str, str | None] = {}
        # 同上 → 该子智能体最新那份清单
        self._sub_items: dict[str, list[dict[str, str]]] = {}
        # 同上 → 子智能体名（挂不上主清单时，容器项要用它起名）
        self._names: dict[str, str] = {}
        # 还没有子智能体来认领的委派：(角色, 委派时主清单里 in_progress 的那一项)。
        # 见 ``note_delegation`` 的注释：两套 id 配不上，只能靠「角色」这张表牵线。
        self._pending: list[tuple[str, str | None]] = []
        # 已经认领过委派的子智能体（同一个子智能体只认一次，认完就固定挂靠）
        self._claimed: set[str] = set()
        # call_id → 上一次成功挂靠的锚：(父项当时的文字, 那一项的下标, 当时主清单的条数)。
        # 挂上就记住它 —— 父项随后被改写时先按这串认（它比委派那一刻那串更接近现状），
        # 实在认不出还能靠位置沿用（见 ``_locate``）。
        self._anchors: dict[str, tuple[str, int, int]] = {}

    def feed(self, event: dict) -> list[dict]:
        """吃一条 v3 事件，吐出要下发的 SSE 事件（可能为空）。"""
        if method_of(event) != "messages":
            return []
        # 中间件内部的模型调用（摘要等）整条丢掉：它不是助手在对用户说话。丢在这里而
        # 不是 service 里，是因为「哪些事件算正文」是事件形状的知识，归这个模块；也顺带
        # 免了它那条 message-start 把主智能体的待办块登记清掉。
        if is_internal_call(event):
            return []
        namespace = namespace_of(event)
        if namespace:
            # 子智能体的事件里登记它自己（名字 + 认领委派）。放在最前面：同一条事件
            # 紧接着可能就要用这份登记去建容器 / 挂子树。
            self._note_child_agent(namespace, event)
        payload = payload_of(event)
        if not isinstance(payload, dict):
            return []
        name = payload.get("event")
        if name == "message-start":
            # 下标是按消息编号的，换一条消息就重新数；只清这条通道自己的登记
            self._todo_blocks = {k for k in self._todo_blocks if k[0] != namespace}
            return []
        if name == "content-block-start":
            self._on_block_start(namespace, payload)
            return []
        if name == "content-block-delta":
            return self._on_block_delta(namespace, payload)
        if name == "content-block-finish":
            return self._on_block_finish(namespace, payload)
        return []

    def note_delegation(self, event: dict) -> None:
        """记一次 ``task`` 委派：它的子智能体那棵清单该挂在主清单的哪一项下。

        挂**委派发生时主清单里 in_progress 的那一项** —— deepagents 的 write_todos
        工具描述硬性要求「除非全部完成，否则始终至少有一项 in_progress」，所以那一项
        就是主智能体此刻在做的事。没有就退到最后一个未完成的项；再没有（清单还没出来、
        或已全部完成）记 ``None``，那份子清单就不进树。
        """
        if method_of(event) != "tools" or namespace_of(event):
            return
        payload = payload_of(event)
        if not isinstance(payload, dict) or payload.get("tool_name") != TASK_TOOL:
            return
        call_id = str(payload.get("tool_call_id") or "")
        if call_id:
            parent = self._running_item()
            role = subagent_name(payload.get("input"))
            self._parents[call_id] = parent
            self._names[call_id] = role
            # 记进「待认领」表：子智能体的 namespace 用的是 langgraph 自己的 task id
            # （``tools:<uuid>``），与这里这个 ``call_id``（模型的 ``call_xxx``）**不是一套**
            # —— 拿真会话查过，UUID 在主控状态里 0 命中。所以光靠 id 永远配不上，得等
            # 子智能体自报角色时按角色认领（同角色 FIFO）。
            self._pending.append((role, parent))

    def _note_child_agent(self, namespace: tuple[str, ...], event: dict) -> None:
        """登记一个子智能体：它的名字，以及它认领的那次委派。

        名字只能从**子智能体这一侧**取（``metadata["lc_agent_name"]``）：它与 ``_sub_items``
        用的是同一个 namespace 键，天然对得上；而 ``task`` 入参那串 ``call_xxx`` 与 namespace
        里的 task id 是两套 id（真会话查过，0 命中），配不上。
        """
        call_id = delegation_id(namespace)
        if not call_id:
            return
        raw = metadata_of(event).get("lc_agent_name")
        name = "" if raw is None else str(raw)
        if name and name not in _NO_AGENT_NAMES:
            self._names.setdefault(call_id, name)
        if call_id in self._claimed:
            return
        index = self._pending_index(name)
        if index is None:
            return
        _role, parent = self._pending.pop(index)
        self._claimed.add(call_id)
        self._parents[call_id] = parent

    def _pending_index(self, role: str) -> int | None:
        """第一个还没被认领、且角色相同的委派；角色取不到就不认领（照旧走容器兜底）。"""
        if not role or role in _NO_AGENT_NAMES:
            return None
        for index, (pending_role, _parent) in enumerate(self._pending):
            if pending_role == role:
                return index
        return None

    def tree(self) -> list[dict]:
        """主清单 + 各子智能体的清单：子项挂到它那次委派对应的那一项下。

        挂靠的线索是**文字**，但不逐字比：主控每帧重写整份清单，还习惯把结果追加进条目
        文字里，逐字比会让子树突然掉到树尾（用户看到的就是「子任务跑完就漂移」）。
        认亲规则见 ``_locate``。

        实在挂不上落点的子清单（主清单为空、已全部完成、那一项被整条删掉）不丢，改为在
        树尾补一个容器项兜住 —— 否则本轮唯一的进度信息就整份消失了。
        """
        texts = [item["content"] for item in self._main]
        attached: set[str] = set()
        kids_at: dict[int, list[dict[str, str]]] = {}
        for call_id, items in self._sub_items.items():
            # items 为空（参数还没流全、或模型给了空数组）时别补空壳容器
            if not items:
                continue
            index = self._locate(call_id, texts)
            if index is None:
                continue
            # 同一项被委派多次：后一次覆盖前一次（子智能体每次给的也是全量清单）
            kids_at[index] = items
            attached.add(call_id)
            # 挂上就记住这一项现在长什么样：下一帧先按它认，父项再被改写也不至于失联
            self._anchors[call_id] = (texts[index], index, len(texts))
        tree: list[dict] = []
        for index, item in enumerate(self._main):
            kids = kids_at.get(index)
            tree.append({**item, "children": kids} if kids else dict(item))
        for call_id, items in self._sub_items.items():
            if items and call_id not in attached:
                tree.append(self._container(call_id, items))
        return tree

    def _locate(self, call_id: str, texts: list[str]) -> int | None:
        """子清单该挂到主清单的第几项；认不出来返回 ``None``（调用方落树尾容器）。

        认亲顺序：**锚**（上次挂上那一项的文字）→ **委派那一刻**那串，各自先逐字比，
        再归一化比（``_todo_text_key``，抹掉「（plan_id=36）」「（PR #9）」这类结尾补充），
        再前缀比；都认不出时，如果主清单**结构没变**（条数与挂上那一刻相同），就沿用锚
        记下的位置 —— 模型改的通常只是措辞，不是把这一项删了。

        归一化与前缀都要求**唯一命中**：主清单里两串归一到同一个键时宁可不挂，也不能
        把子项挂到别的父项下面去。
        """
        anchor = self._anchors.get(call_id)  # (文字, 下标, 当时的条数)
        wants = [anchor[0] if anchor else "", self._parents.get(call_id) or ""]
        for want in wants:
            if not want:
                continue
            for index, text in enumerate(texts):
                if text == want:
                    return index
        keys = [_todo_text_key(text) for text in texts]
        for want in wants:
            if not want:
                continue
            key = _todo_text_key(want)
            if not key:
                continue
            same = [i for i, other in enumerate(keys) if other == key]
            if len(same) == 1:
                return same[0]
            if same:
                # 归一到同一个键的项不止一个：只认逐字比那一次，剩下的交给容器
                continue
            near: list[int] = []
            for i, other in enumerate(keys):
                short, long = (key, other) if len(key) <= len(other) else (other, key)
                if len(short) >= _MIN_PREFIX and long.startswith(short):
                    near.append(i)
            if len(near) == 1:
                return near[0]
        if anchor and len(texts) == anchor[2] and anchor[1] < len(texts):
            return anchor[1]
        return None

    def _container(self, call_id: str, items: list[dict[str, str]]) -> dict:
        """给挂不上主清单的子清单补的容器项。

        它是**纯展示壳**，不是主智能体写过的计划项，所以状态由子项汇总 —— 容器自己
        没有独立进度，写死 ``in_progress`` 会在收尾时永久停在那儿。

        名字那一行是给用户看的（这就是任务列表里那行文字），所以认识的三个角色翻成中文
        （「编码子任务」），不把 ``coder`` 这种内部角色名摊到他面前。名字优先取子智能体
        自报的那个（见 ``_note_child_agent``），取不到才回退到 ``task`` 入参记下的那份，
        两处都没有才用兜底话。
        """
        name = self._names.get(call_id)
        label = _ROLE_LABELS.get(name, "")
        if label:
            title = f"{label}子任务"
        else:
            # 表里没有的角色照旧把名字带上 —— 将来新增角色时，好过塌成一句看不出是什么
            # 的兜底话；连名字都取不到（task 入参畸形）才用兜底话。
            title = f"「{name}」子任务" if name else "子智能体任务"
        return {"content": title, "status": _rollup(items), "children": items}

    def _running_item(self) -> str | None:
        for item in self._main:
            if item["status"] == "in_progress":
                return item["content"]
        for item in reversed(self._main):
            if item["status"] != "completed":
                return item["content"]
        return None

    def _on_block_start(self, namespace: tuple[str, ...], payload: dict) -> None:
        block = payload.get("content")
        if not isinstance(block, dict):
            return
        if block.get("type") != "tool_call_chunk" or block.get("name") != TODO_TOOL:
            return
        self._todo_blocks.add((namespace, payload.get("index")))

    def _on_block_delta(self, namespace: tuple[str, ...], payload: dict) -> list[dict]:
        delta = payload.get("delta")
        if not isinstance(delta, dict):
            return []
        if delta.get("type") == "text-delta":
            # 推理走的是 reasoning-delta，天然进不来；子智能体的旁白是内部编排
            if namespace:
                return []
            text = delta.get("text")
            if not isinstance(text, str) or not text:
                return []
            self.parts.add_text(text)
            # if namespace:
            #     print("sub text:", text)
            # else:
            #     print("main text:", text)

            return [{"type": "ai", "content": text}]
        index = payload.get("index")
        if delta.get("type") != "block-delta" or (namespace, index) not in self._todo_blocks:
            return []
        args = (delta.get("fields") or {}).get("args")
        if not isinstance(args, str):
            return []
        # 分片里的 args 是**累加值**（bridge 已把 tool_call_chunks 按 index 拼好），
        # 不是增量 —— 整串拿来当当前值。当成增量去 += 会把前几项重复捞出来一遍。
        return self._apply(namespace, todo_items(args))

    def _on_block_finish(self, namespace: tuple[str, ...], payload: dict) -> list[dict]:
        if (namespace, payload.get("index")) not in self._todo_blocks:
            return []
        items = finished_items(payload.get("content"))
        if items is None:
            return []
        return self._apply(namespace, items)

    def _apply(self, namespace: tuple[str, ...], items: list[dict[str, str]]) -> list[dict]:
        """把一份清单落到它该在的位置，回吐整棵树；没有落点就什么都不吐。"""
        if not namespace:
            self._main = items
        else:
            call_id = delegation_id(namespace)
            if not call_id:
                # 两级以上的嵌套（子智能体自己又委派）：没有对应的清单位置，丢掉
                return []
            self._sub_items[call_id] = items
        # 每次全量下发（和 plan 事件一个惯用法），前端整体替换那棵树；落库那份也换掉：
        # 子智能体的进度也是在 _apply 里进来的，最后存下的那棵一定是最新的。
        tree = self.tree()
        self.parts.set_todos(tree)
        return [{"type": "todos", "items": tree}]
