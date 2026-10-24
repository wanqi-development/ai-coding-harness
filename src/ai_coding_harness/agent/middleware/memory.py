"""仓库记忆加载 / 注入中间件。

PR 状态拦截已经移到 ``server/turn/runner.py`` 的意图后函数闸门；这里只做两件事：

1. ``abefore_agent`` 读一次盘上的记忆；
2. ``awrap_model_call`` 按形态 / 角色决定注入全文、节选还是不注入。

注入只作用于本次模型调用，不写进对话历史与 checkpoint。没写过记忆的仓库不凭空造残篇。
子智能体各拿自己的一份实例，因为它们可能在同一回合并发跑。

PR 查询、增量同步和落盘由 ``server/repo_memory_sync.py`` 负责；本中间件只加载注入。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

from ai_coding_harness.agent.middleware.memory_sections import DIGEST_KEYS, digest
from ai_coding_harness.agent.profiles import (
    QA_FORM,
)
from ai_coding_harness.backends.composite import memory_path
from ai_coding_harness.config import Settings
from ai_coding_harness.core.codeplatform import platform_label
from ai_coding_harness.tools.git import RepoSpec

logger = logging.getLogger(__name__)

#: 注入块的标题。模型靠它认出「记忆已经在上下文里了，别再读一遍」，``prompts.py``
#: 里那几处措辞认的也是「仓库记忆」这四个字。
#:
#: 标题里不冒充「刚按平台实况刷新」：中间件只读盘上那份；PR 实时状态由 server 层快照负责。
MEMORY_BLOCK_TITLE = "## 仓库记忆（本仓库长期记忆的当前全文）"


def _memory_block(path: str, text: str) -> str:
    """注入用的记忆块：先交代「这是什么、别再去读」，再贴全文。"""
    return (
        f"{MEMORY_BLOCK_TITLE}\n"
        f"下面是 `{path}` 的当前全文 —— **不要再用 `read_file` 读它**，也不必读第二遍：\n"
        "写回按本轮意图指令走：**按节增量写，不要整篇提交**。\n\n"
        f"{text}"
    )


def _digest_block(path: str, text: str) -> str:
    """精简注入块：说清「这是节选、不是没有历史」。"""
    return (
        f"{MEMORY_BLOCK_TITLE}\n"
        f"下面是 `{path}` 的**按角色节选**（不是全文）—— **不要再用 `read_file` 读它**。\n"
        "变更记录没有注入；需要历史时把问题回报主控，由主控查 PR。\n\n"
        f"{text}"
    )


async def _read_text(backend: BackendProtocol, path: str) -> str | None:
    """盘上那份记忆的全文；这个仓库还没写过记忆时返回 ``None``。

    ``None`` 与 ``""`` 是两件事：没文件是「从没写过」，空文件是「写过一个空文件」——
    要不要注入、要不要建档，判的都是前者。
    """
    stored = await backend.aread(path)
    return (getattr(stored, "file_data", None) or {}).get("content")


class MemoryMiddleware(AgentMiddleware):
    """只负责读仓库记忆并注入系统提示。

    PR 状态拦截已经移到 ``server/turn/runner.py`` 的意图后函数闸门：分类前先拿状态，
    判定完立刻决定放行还是停下，模型不会先拿到任务、再在中间件里被拦。
    """

    def __init__(
        self,
        *,
        backend: BackendProtocol,
        path: str,
        platform: object = None,
        sections: tuple[str, ...] | None = None,
    ) -> None:
        self._backend = backend
        self._path = path
        #: 该会话仓库所在平台，用于注入日志文案。
        self._platform = platform
        #: 平台展示名（``Gitee`` / ``GitHub``）—— 只喂给日志与用户可见的文案，**不写死**：
        #: 同一个仓库换个平台，写死的那句话就全错了。
        self._label = platform_label(platform)
        #: 注入哪些节：``None`` ＝ 全文；空元组 ＝ **不注入**（reviewer）；
        #: 其余 ＝ 只注入这几节（见 ``memory_sections.digest``）。
        self._sections = sections
        #: 本轮要注入的记忆块；``abefore_agent`` 每轮覆盖一次，``None`` ＝ 没得注入。
        self._memory: str | None = None

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """读一次盘上的记忆，留给 ``awrap_model_call`` 注入。"""
        if self._sections == ():
            self._memory = None
            return None
        self._memory = self._block(await _read_text(self._backend, self._path))
        return None

    def _block(self, text: str | None) -> str | None:
        """按 ``sections`` 决定注入全文、节选，还是不注入。"""
        if text is None:
            return None
        if self._sections is None:
            return _memory_block(self._path, text)
        part = digest(text, self._sections)
        return None if not part else _digest_block(self._path, part)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """把记忆块追加到系统消息末尾（与 ``IntentMiddleware`` 同一套做法）。

        注入只作用于本次模型调用，不写进对话历史与 checkpoint。``IntentMiddleware``
        排在更内侧，它读到的是这里改过的系统消息，于是时间 / 意图块仍落在最贴近模型的
        位置 —— 注入没有把「今天几号」挤到记忆后面去。
        """
        if self._memory is None:
            return await handler(request)
        base = request.system_message
        parts = (base.text if base else "", self._memory)
        text = "\n\n".join(part for part in parts if part)
        return await handler(request.override(system_message=SystemMessage(content=text)))


def memory_sections_for(
    *, form: str | None, role: str | None = None
) -> tuple[str, ...] | None:
    """按形态 / 角色决定注入哪几节（2026-10-20 用户口径）。

    - ``qa``：**全文**（它要回答「这仓库有什么 / 以前做过什么」）；
    - ``reviewer`` 子智能体：**不注入**（审 diff + policies 就够）；
    - 其余（coding / plan 主控、planner / coder）：只注入
      **基本信息 + 当前缺口 + 长期约束**，变更记录不注入。
    """
    if role == "reviewer":
        return ()
    if form == QA_FORM:
        return None
    return DIGEST_KEYS


def build_memory_middleware(
    settings: Settings,
    repo: RepoSpec,
    backend: BackendProtocol,
    form: str | None = None,
    role: str | None = None,
) -> MemoryMiddleware:
    """按形态造仓库记忆中间件：**所有智能体都挂**。

    这里只构建加载 / 注入用的中间件；PR 查询、刷新与业务拦截都在 server 层。

    ``GiteeClient`` 是只存配置的轻量对象（每次调用各起一个 ``httpx.AsyncClient``），这里
    再建一个不会与记忆工具那个冲突。路径统一走 ``backends.composite.memory_path`` ——
    记忆路径只有那一个口径，自己拼一份就会写到没人读的 key 上。
    """
    path = memory_path(repo.owner, repo.repo, settings.memories_prefix)
    sections = memory_sections_for(form=form, role=role)
    return MemoryMiddleware(backend=backend, path=path, platform=repo, sections=sections)
