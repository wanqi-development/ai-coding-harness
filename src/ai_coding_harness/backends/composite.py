"""Build the hybrid composite backend: OpenSandbox default + StoreBackend 持久区。"""

from __future__ import annotations

from deepagents.backends import CompositeBackend, StoreBackend
from deepagents.backends.utils import file_data_to_string
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

#: 仓库记忆在 store 里的 namespace。**唯一口径**：``build_composite_backend`` 的路由与
#: ``read_memory`` 都取它 —— 与 ``memory_path`` 同一条纪律，两处各写一份，就会出现
#: 「写入成功、没人读得到」那种事故（内容等于丢了）。
MEMORIES_NAMESPACE: tuple[str, ...] = ("memories",)


def memory_path(
    platform: str, owner: str, repo: str, prefix: str = "/memories/"
) -> str:
    """仓库记忆文件的运行时路径 —— **记忆路径的唯一口径**，读写两侧都调它。

    历史教训：server 层读记忆、工具层写记忆各自拼过一份路径，两处口径一旦不一致，
    写入成功但没人读得到（内容等于丢了）。所以格式只在这里定义一次。

    前缀与后端路由（``CompositeBackend`` 的 ``memories_prefix``）同一口径，
    传别的值就等于写到没人读的 key 上。
    """
    return f"{prefix.rstrip('/')}/{platform.strip().lower()}/{owner}/{repo}.md"


def memory_store_key(
    platform: str, owner: str, repo: str, prefix: str = "/memories/"
) -> str:
    """仓库记忆在 store 里的实际 key：``/<platform>/<owner>/<repo>.md``。

    ``CompositeBackend`` 命中路由前缀后是**先把前缀剥掉**再交给子后端（见上游
    ``_route_for_path`` 的说明），所以 ``StoreBackend`` 看到的 key 是去前缀的那半截。
    2026-10-11 实测踩到：右栏「记忆」标签拿 ``memory_path()`` 直接 ``aget``，对**已经有记忆**
    的仓库显示「还没写过」—— 而当时的测试把错误假设抄成了断言（两边用同一个表达式，必然
    相等），所以没抓到。现在这条测试改成「经 CompositeBackend 写进去、用这个函数读回来」。

    实现上从 ``memory_path`` 剥前缀而不是再拼一遍字符串：路径形状只留 ``memory_path`` 一处定义。
    """
    return memory_path(platform, owner, repo, prefix).removeprefix(prefix.rstrip("/"))


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
    memories = StoreBackend(store=store, namespace=lambda rt: MEMORIES_NAMESPACE)
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


async def read_memory(
    store: BaseStore,
    platform: str,
    owner: str,
    repo: str,
    prefix: str = "/memories/",
) -> str | None:
    """读仓库记忆全文；这个仓库还没写过记忆时返回 ``None``。

    **走的 key 与中间件读写的那把完全一样**（``MEMORIES_NAMESPACE`` + ``memory_store_key``）。
    这里直连 store，而不是复用 ``StoreBackend.aread``：后者解析 namespace 要靠 LangGraph 的
    运行上下文（``get_runtime``），而 HTTP 接口没有那个上下文 —— 硬用会静默落到
    ``("filesystem",)`` 这个错 namespace 上，读出来永远是「没有记忆」。

    ``file_data_to_string`` 是库自己的归一化（老的 list[str] 内容也认），用它而不是直接取
    ``value["content"]``，少一份要自己维护的兼容逻辑。
    """
    item = await store.aget(
        MEMORIES_NAMESPACE, memory_store_key(platform, owner, repo, prefix)
    )
    if item is None:
        return None
    value = getattr(item, "value", None)
    if not isinstance(value, dict):
        return None
    return file_data_to_string(value)
