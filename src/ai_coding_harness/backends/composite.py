"""Build the hybrid composite backend: OpenSandbox default + StoreBackend 持久区。"""

from __future__ import annotations

from deepagents.backends import CompositeBackend, StoreBackend
from langgraph.store.base import BaseStore

from ai_coding_harness.backends.opensandbox_backend import OpenSandboxBackend

# deepagents 的摘要中间件把两类「溢出产物」写在 ``{backend.artifacts_root}/…`` 下：
#   - ``/conversation_history/<thread_id>.md`` —— 被压缩掉的原始消息（每次压缩追加一节）；
#   - ``/large_tool_results/…`` —— ``ContextOverflowError`` 时过大的工具结果。
# 这两样以前都只落在沙箱里，而沙箱**会话结束就销毁、卷只挂 ``/ai_coding/project``**：
# 摘要消息里那句「完整历史已存到 <路径>，需要细节时可回看」在沙箱重建后是一句空话
# （2026-09-21 那次 `/conversation_history/…md` 404 就是这么来的 —— 它还被沙箱代理当成
# 「沙箱没了」触发了一次重建）。所以两条前缀都路由到 StoreBackend，与仓库记忆同一个
# 持久 store，沙箱换掉、进程重启都还在。
#
# 前缀必须与 deepagents 算出来的一致：它取 ``artifacts_root``（默认 ``"/"``）再拼
# ``"/conversation_history"`` / ``"/large_tool_results"``。构造 ``CompositeBackend`` 时
# 显式传 ``artifacts_root="/"`` 把这个契约钉在我们这一侧，`tests/test_composite_backend.py`
# 另有一条断言直接比对中间件实例上的两个前缀（上游改了默认值当场红）。
HISTORY_PREFIX = "/conversation_history/"
LARGE_TOOL_RESULTS_PREFIX = "/large_tool_results/"


def memory_path(owner: str, repo: str, prefix: str = "/memories/") -> str:
    """仓库记忆文件的运行时路径 —— **记忆路径的唯一口径**，读写两侧都调它。

    历史教训：server 层读记忆、工具层写记忆各自拼过一份路径，两处口径一旦不一致，
    写入成功但没人读得到（内容等于丢了）。所以格式只在这里定义一次。

    前缀与后端路由（``CompositeBackend`` 的 ``memories_prefix``）同一口径，
    传别的值就等于写到没人读的 key 上。
    """
    return f"{prefix.rstrip('/')}/{owner}/{repo}.md"


def build_composite_backend(
    sandbox_backend: OpenSandboxBackend,
    store: BaseStore,
    memories_prefix: str = "/memories/",
) -> CompositeBackend:
    """Wire the hybrid backend.

    ``default`` routes every path to the OpenSandbox backend (files + shell in
    the sandbox)；其余三条前缀各路由到一个持久 ``StoreBackend``：仓库记忆
    （``memories_prefix``）、会话压缩流水（``HISTORY_PREFIX``）与过大的工具结果
    （``LARGE_TOOL_RESULTS_PREFIX``）—— 后两者是**会话级**数据（key 里带 thread_id），
    与记忆同库不同 namespace，互不干扰。
    """
    memories = StoreBackend(store=store, namespace=lambda rt: ("memories",))
    history = StoreBackend(store=store, namespace=lambda rt: ("conversation_history",))
    large_results = StoreBackend(store=store, namespace=lambda rt: ("large_tool_results",))
    return CompositeBackend(
        default=sandbox_backend,
        routes={
            memories_prefix: memories,
            HISTORY_PREFIX: history,
            LARGE_TOOL_RESULTS_PREFIX: large_results,
        },
        artifacts_root="/",
    )
