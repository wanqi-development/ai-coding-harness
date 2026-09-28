"""仓库记忆中间件：把记忆全文注入系统提示；主控形态开工前还按 Gitee 实况刷新一遍。

「对账 + 拦本轮」那一半的缘起：「先查 PR、有未合入的就先合并再干活」原先只是
``prompts.py`` 里的一句**请求** —— 模型可以跳过、可以顺序颠倒、可以把「查不到」讲成
「没有」。这里把它挪进 ``abefore_agent``：记忆先按事实刷新，再决定放不放行，中间没有
模型的裁量空间。

四条纪律：

1. **查不到不是没有。** 查询失败（``PrLookupError``）时既不写记忆、也不放行 ——
   「无法确认有没有未合入 PR」既不能当成「有」也不能当成「没有」，而放行等于默许
   把没落地的改动记成既成事实。代价是 Gitee 抖动会让整轮不可用，这是有意的。
2. **只写回已存在的记忆。** 记忆文件由 ``save_repo_memory`` 创建，这里只为一份已有的
   记忆盖文件头那一行，不给还没写过记忆的仓库凭空造一份残篇。
3. **刷新失败不改变闸门结论。** 写回出错只记日志：拦不拦取决于 PR 的状态，与这次
   写盘成没成无关；写不进去的记忆下一轮还会再刷一遍。
4. **没事就不查**（2026-09-26 起）。记忆里没有一条要核对的 PR（``rows_to_verify``）时，
   这一轮连 PR 列表都不取 —— 不查、不写、``pending`` 为空，于是也不拦。**只有从没写过
   记忆的仓库仍然查一次**：那一档没有任何记忆可依据，是闸门唯一还独立于记忆的地方。
   代价说清楚：闸门的判据**先看记忆里记着的待合并 PR**，人工在 Gitee 上开的、或记忆里
   漏记的 PR 不会再拦住 plan / coding 轮。

**读到的全文一律注入系统消息**（``awrap_model_call``）：闸门那条路 ``abefore_agent``
本来就把记忆读到手了，注入是零额外 IO；其余形态与子智能体多花的也只是**一次 store 读**，
换来的是模型不必再自己 ``read_file`` 读一遍 —— 那次省掉的是整整一个工具调用往返（每多
一次工具调用，就是整份上下文再发一遍）。注入只作用于本次模型调用，不写进对话历史与
checkpoint。**没写过记忆的仓库不注入**：那时盘上根本没有这份文件，把「我们打算建的」当成
盘上的事实讲给模型听等于骗它。

异步钩子两个：``abefore_agent``（读 / 刷新 + 拦本轮）、``awrap_model_call``（注入）。
服务端统一走 ``astream_events``，只写同步版会被跳过。
**抛出的 ``TurnStop`` 就是用户看到的东西** —— server 层收下后把 ``marker`` 原样发成
``ai`` 帧并写进记录正文，所以文案要能独立读懂，不靠前后文。

这里的 :func:`refresh_memory` 也被**轮末打底**（``server/service.py``）复用：启动与编码
收尾是同一件事的两次机会，读→对账→写回的写法与「查不到不写」的纪律只该有一份。

**两件事分开**（2026-09-25 起）：

- **刷新**给所有主控形态（``REFRESH_FORMS``）：开工前按 Gitee 实况重算文件头、把「变更
  记录」里写着「待合并」的状态改成实际结论（已合并 / 已关闭）、摘掉两张老表。它写的是
  **平台侧事实**，与这一轮谁写正文无关 —— 其他形态（qa / analysis / review / plan）自己
  不写正文，但读到的记忆必须是新的。没有要核对的 PR 时这一步整段跳过（见纪律 4）。
- **拦本轮**只给 ``BLOCKING_FORMS`` = ``plan`` / ``orchestrate``（形态级：挂不挂这道闸），
  而**拦不拦由本轮意图判**（意图是逐轮的，闸门却是随形态建好并缓存的，所以只能在这里读
  ``configurable.intent``）：
  - ``plan`` / ``coding`` → **有未合入的 PR 就拦**：前者出方案要以 master 为准（未合入的
    改动会让方案建在错前提上），后者要在 master 上动代码。
  - ``rework``（按评审意见修改）→ **反过来：没有未合入的 PR 才拦**。它要改的就是那份还开着
    的 PR —— 有它本来也该放行（按正向判会把自己永久挡在门外，与 ``review`` 不拦同一条理由），
    而没有它就等于**没有可修的对象**：拿不到审查原文的模型只会空转或凭记忆编一份「评审意见」
    出来改。
  - ``review`` → 挂在这张表之外，**刻意不拦**：它要审的那个 PR 本身就是 open 的。
  - 意图读不到（没经 server 派发，如直接跑图）→ 一律按正向判，保守优先。

``all_prs=None``（只有三个子智能体）那一档：读一次盘上的记忆塞进系统提示，不查 PR、不写盘、
也不拦本轮 —— 同一轮里主控已经刷过了，让 planner / coder / reviewer 各再查一趟 Gitee 只是
白花钱（何况 coder 刚建完 PR、reviewer 再查一遍就会把自己挡在门外）。
子智能体各拿**自己的一份实例**（``build_subagents`` 的 ``memory_factory``）：它们可能在
同一个回合里并发跑，共用一个实例会让注入内容互相覆盖。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

from ai_coding_harness.agent.memory_reconcile import (
    OpenPull,
    Reconciled,
    reconcile,
    rows_to_verify,
)
from ai_coding_harness.agent.middleware.context_inject import now_stamp
from ai_coding_harness.agent.middleware.guard import TurnStop
from ai_coding_harness.agent.profiles import (
    ANALYSIS_FORM,
    ORCHESTRATE_FORM,
    PLAN_FORM,
    QA_FORM,
    REVIEW_FORM,
)
from ai_coding_harness.backends.composite import memory_path
from ai_coding_harness.codeplatform import platform_label
from ai_coding_harness.config import Settings
from ai_coding_harness.server.intent import REWORK_INTENT
from ai_coding_harness.tools.context import get_intent
from ai_coding_harness.tools.git import PrLookupError, RepoSpec, build_git_client

logger = logging.getLogger(__name__)

#: **挂着闸门**的形态 —— 它判的是「这个形态要不要看闸门」，不是「这一轮拦不拦」：
#: 拦不拦由**本轮意图**决定（见 ``abefore_agent`` 里的两条路）。
#: - ``plan``：方案基于 master 出，未合入的改动会让方案建在错误的前提上；
#: - ``orchestrate``：``coding`` 要在 master 上动代码，``rework`` 反过来（见下）。
#:
#: **``review`` 刻意不挂闸门**：审查轮要审的那个 PR 本身就是 open 的，按「有 open PR 就拦」
#: 判就会把自己永久挡在门外（这正是 2026-09-18 那次「闸门只挂主控」的理由之一）。
#: 它仍然刷新记忆（文件头/摘表），只是不拦。
BLOCKING_FORMS: frozenset[str] = frozenset({PLAN_FORM, ORCHESTRATE_FORM})

#: **开工前刷新记忆**的形态：所有主控形态。刷新只做平台侧那两件事（重算文件头、
#: 摘掉两张老表），不写正文 —— 正文只在 coding / review 收尾时由主控写。
#: 子智能体（``form=None``）不在内：同一轮里主控已经刷过，让 planner / coder / reviewer
#: 各再查一趟 Gitee 只是白花钱。
REFRESH_FORMS: frozenset[str] = frozenset(
    {QA_FORM, ANALYSIS_FORM, PLAN_FORM, REVIEW_FORM, ORCHESTRATE_FORM}
)

#: 拦下本轮时写进记录正文的标记（与「〔已由用户终止〕」「〔已中止：…〕」同一口径）。
GATE_MARKER = "〔本轮已停下：仓库还有未合入的 PR〕"
BLIND_MARKER = "〔本轮已停下：无法确认仓库有没有未合入的 PR〕"
NOTHING_TO_FIX_MARKER = "〔本轮已停下：仓库没有待合并的 PR，没有可修复的目标〕"

#: 中断记录 ``extra`` 里区分「哪条路中止的」。
GATE_ABORT_REASON = "pending_pulls"
NO_TARGET_ABORT_REASON = "no_pending_pull"

# 最多列几个待合入的 PR：仓库里可能攒着几十个，全列出来会把「该怎么办」淹掉
_MAX_LISTED = 5


def _pending_block(pending: Sequence[OpenPull]) -> str:
    lines = []
    for pull in pending[:_MAX_LISTED]:
        branch = f"（分支 {pull.branch}）" if pull.branch else ""
        link = f" {pull.url}" if pull.url else ""
        lines.append(f"- #{pull.number} {pull.title}{branch}{link}")
    hidden = len(pending) - _MAX_LISTED
    if hidden > 0:
        lines.append(f"- 另有 {hidden} 个未列出（共 {len(pending)} 个待合入）")
    return "\n".join(lines)


def _pending_marker(pending: Sequence[OpenPull], label: str) -> str:
    return (
        f"{GATE_MARKER}\n\n"
        f"仓库记忆的文件头已按 {label} 的实际状态刷新。下面这些 PR 还开着，改动尚未落地：\n\n"
        f"{_pending_block(pending)}\n\n"
        f"合并由人工在 {label} 上完成 —— 合并之后再重新发起这次任务。"
    )


def _blind_marker(reason: str, label: str) -> str:
    return (
        f"{BLIND_MARKER}\n\n"
        f"查询本仓库的 PR 失败：{reason}\n\n"
        "「查不到」不等于「没有未合入的 PR」，所以本轮既没有刷新仓库记忆的文件头，也没有放行。"
        f"请先确认凭据 / 网络 / {label} 可用性，再重新发起本次任务。"
    )


def _nothing_to_fix_marker(label: str) -> str:
    """``rework``（按评审意见修改）找不到可修对象时给用户看的话。"""
    return (
        f"{NOTHING_TO_FIX_MARKER}\n\n"
        f"仓库记忆的文件头已按 {label} 的实际状态刷新。这个仓库**没有还开着的 PR**，"
        "所以「按评审意见修改」这一轮没有可修的对象 —— 那个 PR 可能已经合并，"
        "也可能这一轮本来就不该走评审修复这条路。\n\n"
        "确实要改代码，请说「开始实施」或「按这个方案做」（走编码流程，会新开一个 PR）；"
        "只是想看 PR 上的评审意见，直接问即可。"
    )


#: 注入块的标题。模型靠它认出「记忆已经在上下文里了，别再读一遍」，``prompts.py``
#: 里那几处措辞认的也是「仓库记忆」这四个字。
#:
#: 标题里**不写「已按 Gitee 实际状态刷新」**：只有 plan / coding 两个主控那条路真的刷了，
#: 其余五个（qa / analysis / review 主控与三个子智能体）只是把盘上那份读出来 —— 挂一句
#: 刷新过的话，等于让它们以为自己看到的是刚核过的事实。PR 那件事由闸门的提示词说。
MEMORY_BLOCK_TITLE = "## 仓库记忆（本仓库长期记忆的当前全文）"


def _memory_block(path: str, text: str) -> str:
    """注入用的记忆块：先交代「这是什么、别再去读」，再贴全文。"""
    return (
        f"{MEMORY_BLOCK_TITLE}\n"
        f"下面是 `{path}` 的当前全文 —— **不要再用 `read_file` 读它**，也不必读第二遍：\n"
        "需要写回时直接以这份为底合并（`save_repo_memory` 是覆盖写）。\n\n"
        f"{text}"
    )


@dataclass(frozen=True)
class Refreshed:
    """一次刷新：对账结果 + 盘上原来那份全文（``None`` ＝ 这个仓库还没写过记忆）。

    两个都要：``result`` 是给模型看的最新事实，``current`` 决定**要不要注入** ——
    没有文件时盘上什么都没有，注入与否只能看 ``current``。
    """

    result: Reconciled
    current: str | None


async def _read_text(backend: BackendProtocol, path: str) -> str | None:
    """盘上那份记忆的全文；这个仓库还没写过记忆时返回 ``None``。

    ``None`` 与 ``""`` 是两件事：没文件是「从没写过」，空文件是「写过一个空文件」——
    要不要注入、要不要建档，判的都是前者。
    """
    stored = await backend.aread(path)
    return (getattr(stored, "file_data", None) or {}).get("content")


async def refresh_memory(
    backend: BackendProtocol,
    path: str,
    all_prs: Callable[[], Awaitable[list[dict]]],
    platform: object = None,
) -> Refreshed:
    """读仓库记忆 → （有必要时）按 ``all_prs()`` 对账 → 变了就整篇写回。

   返回值里的 ``result.pending`` 是**这一次查到的** PR 列表里仍未合入的那些：仓库有未合入
    的 PR 就该拦下本轮，哪怕这个仓库还从没写过记忆；而没查的那一轮它必然为空（见下）。

    **没事就不查**（2026-09-26 起）：记忆里没有一条要核对的 PR 时（``rows_to_verify`` ——
    都写着「已合并 / 已关闭」，或压根没有 PR 行），这一轮**不调 ``all_prs()``**、不写盘、
    ``pending`` 为空。省下的是一次 Gitee 往返，换来的是「闸门判据先看记忆里记着的 PR」：
    记忆里没有待合并的 PR 就不拦（用户口径）。

    **没记忆就不写**（``current is None``）：建档是 ``save_repo_memory`` + 模板的事，这里
    凭空造一份没有「仓库基本信息」的残篇会占住路径，让模型下一轮以为自己已经写过了。
    但**没记忆仍然查一次**（唯一还查的那一档）：从头一回来到这个仓库，没有任何记忆可依据，
    这是闸门唯一还独立于记忆的地方。

    ``PrLookupError`` 原样抛出 —— 两个调用方对它的处置正好相反：闸门要拦下本轮，
    轮末打底只记日志放行。处置写死在这里，必然有一处是错的。

    ``platform`` 只用于日志里那个平台名（``Gitee`` / ``GitHub``）：留空按 Gitee，
    与 ``platform_label`` 同口径。
    """
    old = await _read_text(backend, path)
    if old is not None and not rows_to_verify(old):
        # 只读、不写盘：写盘属于「这一轮核过事实」，而这一轮什么都没核（时间戳也就不动）
        logger.debug("仓库记忆里没有要核对的 PR，这一轮跳过查询：%s", path)
        return Refreshed(
            result=Reconciled(text=old, changed=False, pending=()), current=old
        )
    result = reconcile(old or "", await all_prs(), now=now_stamp())
    if old is not None and result.changed:
        await _write(backend, path, old, result.text, platform)
    return Refreshed(result=result, current=old)


async def _write(
    backend: BackendProtocol, path: str, old: str, new: str, platform: object = None
) -> None:
    """整篇替换（``aedit`` 的 old_string 取当前全文，与 ``save_repo_memory`` 同法）。

    ``StoreBackend.awrite`` 只在文件不存在时新建，已存在必走 ``aedit``。
    """
    result = await backend.aedit(path, old, new)
    error = getattr(result, "error", None)
    if error:
        logger.warning("刷新仓库记忆失败（不影响闸门判定与本轮结论）：%s", error)
    else:
        # 成功也留一条：否则「这轮到底刷没刷」只能从 ``changed`` 猜，而没记忆时
        # ``changed`` 也是真 —— 那是「算出来变了」，不是「写进去了」
        logger.info(
            "仓库记忆已按 %s 的实际状态刷新：%s", platform_label(platform), path
        )


class MemoryMiddleware(AgentMiddleware):
    """读记忆 → 注入系统提示；给了 ``all_prs`` 时还先按 Gitee 实况刷新（文件头 + PR 状态 + 摘表）。

    依赖在构造期一次给全，``abefore_agent`` 不读 state。``all_prs=None`` 是「这次没有查 PR
    的能力」：只读注入，不查也不写（三个子智能体就是这一档）。``blocking=True`` 才在刷新后
    判闸门（``BLOCKING_FORMS``）。刷新里「有没有要核对的 PR」这一问由 ``refresh_memory``
    判（记忆里没有就不查），所以这一层不必知道。

    闸门只有一处要读**本轮配置**：``configurable.intent``。意图是逐轮的，而这个中间件（连同
    它挂着的智能体）是按**形态**建好并缓存的 —— 同一份 orchestrate 中间件，``coding`` 轮要
    在有 open PR 时拦、``rework`` 轮反过来，形态级的知识分不开这两者。意图不是依赖（它每轮
    都变），所以由 ``get_intent()`` 现取；取不到就按正向判（保守）。
    """

    def __init__(
        self,
        *,
        backend: BackendProtocol,
        path: str,
        all_prs: Callable[[], Awaitable[list[dict]]] | None = None,
        blocking: bool = False,
        platform: object = None,
    ) -> None:
        self._backend = backend
        self._path = path
        self._all_prs = all_prs
        self._blocking = blocking
        #: 该会话仓库所在平台：原样留着传给 ``refresh_memory``，展示名另存一份（下面
        #: 那几处文案只用展示名，不必每次都再过一遍判断）。
        self._platform = platform
        #: 平台展示名（``Gitee`` / ``GitHub``）—— 只喂给日志与用户可见的文案，**不写死**：
        #: 同一个仓库换个平台，写死的那句话就全错了。
        self._label = platform_label(platform)
        #: 本轮要注入的记忆块；``abefore_agent`` 每轮覆盖一次，``None`` ＝ 没得注入。
        self._memory: str | None = None

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """读（或刷新）记忆 → 记下要注入的那块 → 按本轮意图判闸门，该拦的抛 ``TurnStop``。"""
        if self._all_prs is None:
            old = await _read_text(self._backend, self._path)
            self._memory = None if old is None else _memory_block(self._path, old)
            return None

        try:
            refreshed = await refresh_memory(
                self._backend, self._path, self._all_prs, self._platform
            )
        except PrLookupError as exc:
            logger.warning("查询本仓库 PR 失败，本轮拦下且不写记忆：%s", exc)
            raise TurnStop(
                _blind_marker(str(exc), self._label), {"aborted": GATE_ABORT_REASON}
            ) from exc

        # 注入的是**刷新后**的全文，不是盘上原来那份：写回失败时盘上还是旧的，而让模型
        # 照着旧事实去合并写回比不注入更糟。没有记忆文件就什么都不注入（见 ``Refreshed``）。
        self._memory = (
            None
            if refreshed.current is None
            else _memory_block(self._path, refreshed.result.text)
        )
        pending = refreshed.result.pending

        # 没挂闸门的形态（qa / analysis / review）到此为止：刷新照做，不拦。
        if not self._blocking:
            return None

        # ``rework`` 是**反向**闸门：它要改的就是那份还开着的 PR。有 → 放行（正向判会把它
        # 永久挡在门外）；没有 → 没有可修的对象，拦下本轮。
        if get_intent() == REWORK_INTENT:
            if pending:
                logger.info(
                    "本轮意图是 rework，仓库有 %d 个待合并的 PR，放行：%s",
                    len(pending),
                    [pull.number for pull in pending],
                )
                return None
            logger.info("本轮意图是 rework，但仓库没有待合并的 PR，拦下本轮：%s", self._path)
            raise TurnStop(
                _nothing_to_fix_marker(self._label),
                {"aborted": NO_TARGET_ABORT_REASON},
            )

        # 其余（plan / coding，以及读不到意图时的保守口径）：有 open PR 就拦。
        if not pending:
            return None
        logger.info(
            "仓库还有 %d 个未合入的 PR，拦下本轮：%s",
            len(pending),
            [pull.number for pull in pending],
        )
        raise TurnStop(
            _pending_marker(pending, self._label),
            {
                "aborted": GATE_ABORT_REASON,
                "pending": [pull.number for pull in pending],
            },
        )

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


def build_memory_middleware(
    settings: Settings, repo: RepoSpec, backend: BackendProtocol, form: str | None = None
) -> MemoryMiddleware:
    """按形态造仓库记忆中间件：**所有智能体都挂**。

    两档：

    - ``form=None``（子智能体）→ 只读注入，不查 PR、不写盘、不拦；
    - 主控形态 → 每轮开工前按 Gitee 实况刷新记忆（``REFRESH_FORMS``，记忆里没有要核对的
      PR 时整段跳过、连查询都不发），其中 ``plan`` / ``orchestrate`` 还会在刷新后判闸门
      （``BLOCKING_FORMS``）—— 拦不拦看本轮意图：``plan`` / ``coding`` 有未合入 PR 就拦，
      ``rework`` 反过来（没有才拦）。

    ``GiteeClient`` 是只存配置的轻量对象（每次调用各起一个 ``httpx.AsyncClient``），这里
    再建一个不会与记忆工具那个冲突。路径统一走 ``backends.composite.memory_path`` ——
    记忆路径只有那一个口径，自己拼一份就会写到没人读的 key 上。
    """
    path = memory_path(repo.owner, repo.repo, settings.memories_prefix)
    if form not in REFRESH_FORMS:
        return MemoryMiddleware(backend=backend, path=path, platform=repo)
    return MemoryMiddleware(
        backend=backend,
        path=path,
        # 平台不同就在这里分叉（build_git_client），闸门本身只认「查得到/查不到」
        all_prs=build_git_client(settings, repo).all_pulls,
        blocking=form in BLOCKING_FORMS,
        platform=repo,
    )
