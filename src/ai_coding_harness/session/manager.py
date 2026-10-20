"""Session management — per-session workspace runtime + per-task-type agents (multiton).

**会话与工作区（沙箱）解耦**：``create_session`` 只落库（毫秒级），工作区由
``ensure_runtime`` 另行构建。原因是建工作区（建容器 ~5s + 推资产/初始化 ~6s）比用户点
「新建会话」慢一个数量级，绑在一起就意味着接口一直挂着、失败只能新建会话重来。

构建结果按 ``public_id`` **合流**：并发调用只会建一次；失败语义（重试几次、对用户说什么）
由调用方定，管理器只负责「建不出来就报失败」并把状态落库。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from deepagents.backends import BackendProtocol
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore
from opensandbox.models.sandboxes import Volume

from ai_coding_harness.agent.circuit import CircuitStore
from ai_coding_harness.agent.graph import build_agent
from ai_coding_harness.agent.middleware import (
    FallbackMiddleware,
    build_memory_middleware,
)
from ai_coding_harness.agent.profiles import AgentProfile
from ai_coding_harness.agent.prompts import build_main_prompt
from ai_coding_harness.agent.subagents import build_subagents
from ai_coding_harness.agent.middleware.summarization import build_summarization
from ai_coding_harness.backends.composite import build_composite_backend
from ai_coding_harness.backends.opensandbox_backend import OpenSandboxBackend
from ai_coding_harness.codeplatform import Platform, RepoSpec, parse_repo_url
from ai_coding_harness.config import Settings
from ai_coding_harness.db import Database, dao
from ai_coding_harness.observability.logging import MAIN_AGENT
from ai_coding_harness.safety.permissions import CommandSafety
from ai_coding_harness.safety.policy import policy_for
from ai_coding_harness.sandbox.client import SandboxClient
from ai_coding_harness.sandbox.provider import SandboxProvider
from ai_coding_harness.sandbox.proxy import OpenSandboxProxy
from ai_coding_harness.sandbox.volumes import (
    build_maven_cache_volume,
    build_workspace_volume,
    workspace_volume_name,
)
from ai_coding_harness.tools.fetch_url import build_fetch_url_tool
from ai_coding_harness.tools.handoff import HandoffContext
from ai_coding_harness.tools.plan import PlanContext
from ai_coding_harness.tools.register import build_main_tools, build_subagent_tools

logger = logging.getLogger(__name__)

# 代码工作区状态（sessions.workspace_status）—— 前端据此显示「准备中 / 可用 / 异常」
WORKSPACE_PENDING = "pending"
WORKSPACE_PROVISIONING = "provisioning"
WORKSPACE_READY = "ready"
WORKSPACE_FAILED = "failed"

# 一次 ensure 的整体上限（含重试）。正常耗时约 10s，留到 90s 是给 OpenSandbox 侧
# 慢启动的余量；再慢就按异常处理，让用户点重试，而不是把请求一直挂着。
_ENSURE_TIMEOUT_SECONDS = 90.0

class WorkspaceError(RuntimeError):
    """代码工作区（沙箱）建不出来。

    调用方据此对用户说固定措辞（见 ``service.WORKSPACE_ERROR_MESSAGE``）；本异常的
    文本是原始异常，只进日志（已脱敏）与 ``sessions.workspace_error`` 列，不下发。
    """


@dataclass
class Session:
    """一次会话的标识与仓库信息 —— 不含运行时资源（沙箱在后端是**可弃**的）。"""

    public_id: str  # 对外会话 ID（uuid）
    db_id: int  # 业务库内部主键
    repo: RepoSpec
    repo_url: str


@dataclass(frozen=True)
class CreatedSession:
    """建会话的结果。

    ``reused`` 为真表示这个仓库已经有活跃会话，本次没有新建、也没建工作区：调用方
    不要再踢 ``start_provisioning``（工作区可能早就是 ready，再踢一次等于白等一趟重连），
    直接打开既有会话即可。
    """

    session: Session
    reused: bool
    workspace_status: str


@dataclass
class SessionRuntime:
    """会话的运行时资源：沙箱 + 文件后端。

    **不再缓存主智能体**（2026-10-14 用户口径：「热生效，每次都创建一个新的 agent，不缓存 agent」）：
    模型 / 上下文窗口这些值是**建实例时读**的，缓存住就换不动 —— 于是设置页里改完要重启才生效。
    改成每轮现建之后，改完下一轮就是新的（代价是每轮多一次装配，实测这点开销换来的是「热生效」）。
    """

    sandbox: OpenSandboxProxy
    backend: BackendProtocol


@dataclass
class _Pending:
    """一次在飞的工作区构建。

    既是合流的把手（多个调用方 await 同一个 ``task``），也是**软取消**的通道：删除
    会话或整体超时时把 ``cancelled`` 置上，构建任务拿到容器后自己回收。刻意不用
    ``task.cancel()`` 硬砍 —— 砍在 ``create`` 中途就永远不知道容器 id，等于漏一个容器。
    也正因为回收由构建任务自己完成，这里不必记 ``sandbox_id``。
    """

    public_id: str
    cancelled: bool = False
    # start() 之前无意义（构建还没起）
    task: asyncio.Task[SessionRuntime] = field(init=False, repr=False)

    def start(self, coro: Coroutine[Any, Any, SessionRuntime]) -> None:
        self.task = asyncio.create_task(coro)


class SessionManager:
    """管理会话（工作区运行时 + 多例智能体）的创建与销毁。

    共享依赖（``model_for`` / web_search / store / checkpointer / database）在应用
    启动时构建一次；工作区按需构建并按会话缓存，各任务类型的智能体按需构建并缓存。

    ``model_for`` 是**按角色取模型**的工厂（路由表见 ``agent/llm.py``）：主智能体按
    形态取（``profile.form``），子智能体按角色取。模型构造很轻（只是建个客户端对象，
    不发请求），且每会话每形态只建一次 —— 不必再缓存一层。

    ``fallback_for`` 是同口径的**兜底**模型工厂，``None`` ＝ 该角色没有兜底。它由这里
    包成 ``FallbackMiddleware`` 再往下传（角色名与日志标注只有这一层同时知道）。

    ``circuit_store`` 是熔断状态的存取（见 ``agent/circuit.py``）：不传就是进程内那份
    模块级单例，配了 Redis 时由装配处换成跨 worker 共享的那份。
    """

    def __init__(
        self,
        *,
        settings: Settings,
        model_for: Callable[[str], BaseChatModel],
        fallback_for: Callable[[str], BaseChatModel | None],
        web_search_tool: BaseTool,
        store: BaseStore,
        checkpointer: BaseCheckpointSaver,
        database: Database,
        circuit_store: CircuitStore | None = None,
    ) -> None:
        self._settings = settings
        self._model_for = model_for
        self._fallback_for = fallback_for
        self._web_search = web_search_tool
        self._fetch_url = build_fetch_url_tool()
        self._store = store
        self._checkpointer = checkpointer
        self._db = database
        self._circuit_store = circuit_store
        self._provider = SandboxProvider(settings)
        self._safety = CommandSafety()
        self._runtimes: dict[str, SessionRuntime] = {}
        self._pending: dict[str, _Pending] = {}

    # -- 会话（只落库）-----------------------------------------------------
    async def create_session(
        self,
        repo_url: str,
        gitee_token: str | None = None,
        github_token: str | None = None,
    ) -> CreatedSession:
        """创建会话：只写库（拿 id + 关联仓库），工作区交给 ``ensure_runtime``。

        **一个仓库一个活跃会话**：该仓库若已有活跃会话，直接复用它（不写库、不建工作区），
        免得同一个仓库开出两个沙箱各改一份代码。用户删掉的会话不再占位，删完可以重新建。

        **平台由仓库地址判定**（gitee.com / github.com）：请求带的令牌按平台取
        （``gitee_token`` / ``github_token``），没有就回落该平台在 .env 里的令牌。
        token 来源决定 git 认证用户名（见 ``codeplatform.Platform`` 的默认值）。
        复用走库里记的那份凭据（见 ``session_from_row``）。
        """
        platform, owner, repo = parse_repo_url(repo_url)

        async with self._db.session() as db:
            # 先拿锁再查：两个并发请求（双击、两个标签页）否则会双双查空、各建一个会话
            await dao.lock_repo(db, owner=owner, name=repo)
            existing = await dao.find_active_session_by_repo(db, owner=owner, name=repo)
            if existing is not None:
                row, stored = existing
                logger.info(
                    "仓库已有活跃会话，直接复用：public_id=%s", row.public_id
                )
                return CreatedSession(
                    session=self.session_from_row(
                        public_id=str(row.public_id),
                        db_id=row.id,
                        repo_url=stored.url,
                        owner=stored.owner,
                        name=stored.name,
                        gitee_token=row.gitee_token,
                        gitee_username=row.gitee_username,
                        git_platform=row.git_platform,
                    ),
                    reused=True,
                    workspace_status=row.workspace_status,
                )

            requested = github_token if platform is Platform.GITHUB else gitee_token
            # 请求带了令牌（OAuth）就按平台默认用户名；.env 兜底那份（PAT）用配置里的
            # 真实用户名，没配才回落默认 —— 留空由 RepoSpec 自己填默认值
            repo_spec = RepoSpec(
                platform=platform,
                owner=owner,
                repo=repo,
                token=requested or self._settings.token_for(platform),
                git_username="" if requested else self._settings.username_for(platform),
            )
            public_id = uuid4()

            row = await dao.create_session(
                db,
                public_id=public_id,
                gitee_token=repo_spec.token or None,
                gitee_username=repo_spec.git_username,
                git_platform=repo_spec.platform.value,
            )
            await dao.create_repository(
                # 存用户给的原地址（代码面板按它 clone；不在这里改写他的输入）
                db, session_id=row.id, owner=owner, name=repo, url=repo_url
            )
            db_id = row.id
        logger.info("会话已落库：public_id=%s db_id=%s", public_id, db_id)
        return CreatedSession(
            session=Session(
                public_id=str(public_id),
                db_id=db_id,
                repo=repo_spec,
                repo_url=repo_url,
            ),
            reused=False,
            # 新建的会话下一步就进 provisioning（路由随即 start_provisioning）
            workspace_status=WORKSPACE_PROVISIONING,
        )

    @staticmethod
    def thread_id(session: Session) -> str:
        """会话的 checkpoint thread_id。"""
        return session.public_id

    @staticmethod
    def session_from_row(
        *,
        public_id: str,
        db_id: int,
        repo_url: str,
        owner: str,
        name: str,
        gitee_token: str | None,
        gitee_username: str | None,
        git_platform: str | None = None,
    ) -> Session:
        """由库里的行组装会话描述。

        凭据原样取库里存的那份、**不回落配置**：建会话时已经在库里记下当时生效的
        token 与用户名（见 ``create_session``），回落反而会让旧会话用上后来换过的令牌。
        ``git_platform`` 认不出（老行）按 Gitee。
        """
        return Session(
            public_id=public_id,
            db_id=db_id,
            repo=RepoSpec(
                platform=Platform.of(git_platform),
                owner=owner,
                repo=name,
                token=gitee_token or "",
                git_username=gitee_username or "",
            ),
            repo_url=repo_url,
        )

    # -- 工作区（异步建，用前等）-------------------------------------------
    def start_provisioning(self, session: Session) -> None:
        """后台开始建工作区，不等结果（``POST /sessions`` 用）。"""
        self._pending_for(session)

    async def ensure_runtime(self, session: Session) -> SessionRuntime:
        """取会话的工作区运行时；没有就建（并等它建好）。

        已有运行时直接返回。否则合流到在飞的构建上；**失败会重建一次**（首次多是
        OpenSandbox 侧瞬时故障），仍失败才抛 ``WorkspaceError`` —— 「重试」的语义只
        在这一处，构建任务自身不重试，避免对着挂掉的基础设施猛敲。
        """
        public_id = session.public_id
        runtime = self._runtimes.get(public_id)
        if runtime is not None:
            # 轮首续期：用户隔一阵回来时先把沙箱寿命续上（按 SANDBOX_RENEW_INTERVAL_SECONDS
            # 节流，失败只记日志）。运行中那一侧由代理在每次沙箱调用前兜住 —— 一轮可能跑很久，
            # 只靠这一处续期不够（见 ``sandbox/proxy.py::_maybe_renew``）。
            await runtime.sandbox.renew_if_due()
            return runtime

        loop = asyncio.get_running_loop()
        deadline = loop.time() + _ENSURE_TIMEOUT_SECONDS
        error: Exception | None = None
        for _ in range(2):  # 首次 + 重试一次
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            pending = self._pending_for(session)
            try:
                # shield：超时只放弃等待，任务继续跑（它会自己回收建出来的容器）；
                # 直接 wait_for 会在超时时 cancel 任务，正好是软取消要避免的那件事。
                runtime = await asyncio.wait_for(
                    asyncio.shield(pending.task), timeout=remaining
                )
            except TimeoutError:
                error = WorkspaceError("代码工作区创建超时")
            except WorkspaceError as exc:
                error = exc
            else:
                self._runtimes[public_id] = runtime
                return runtime
            self._abandon(pending)
        raise error or WorkspaceError("代码工作区创建失败")

    def _fallback_middleware(self, role: str, agent: str) -> FallbackMiddleware | None:
        """把 ``fallback_for(role)`` 给的模型包成兜底中间件；没兜底就是 ``None``。

        角色名与日志标注（``%(agent)s`` 那一列）只有这一层同时知道：子智能体两者同值，
        主控则是「形态名 + ``MAIN_AGENT``」。
        """
        model = self._fallback_for(role)
        if model is None:
            return None
        return FallbackMiddleware(
            role=role,
            agent=agent,
            fallback=model,
            circuit_threshold=self._settings.fallback_circuit_threshold,
            circuit_cooldown=self._settings.fallback_circuit_cooldown_seconds,
            circuit=self._circuit_store,
        )

    def get_agent(
        self, runtime: SessionRuntime, session: Session, profile: AgentProfile
    ) -> CompiledStateGraph:
        """构建本会话的主智能体 —— **每次现建，不缓存**。

        工具与子智能体按形态裁剪（``agent/profiles.py``）：只有 ``coding`` 挂子智能体，
        其余形态连 `task` 都没有；qa / analysis 另拿不到写工具。**模型也挂在同一个轴
        上**（形态名就是角色名），于是换形态必然连模型一起换掉，不会出现「用着 qa 的工具、
        跑着 plan 的模型」。thread 与 checkpointer 仍按会话共享，所以换形态后用户看到的历史是
        连续的。

        **为什么不缓存**（2026-10-14 用户口径）：模型与上下文窗口是建实例时读的，缓存住之后
        在设置页改完要重启才生效。每轮现建的代价是一次装配（毫秒级），换来的是「改完立刻生效」。
        """
        plan_ctx = PlanContext(
            session_db_id=session.db_id,
            owner=session.repo.owner,
            repo=session.repo.repo,
        )
        main_tools = build_main_tools(
            self._settings,
            self._web_search,
            self._fetch_url,
            session.repo,
            runtime.backend,
            form=profile.form,
            database=self._db,
            plan_ctx=plan_ctx,
            # 能力出口：所有形态都挂（形态裁掉的能力模型看不见，唯一出路是交接出去）
            handoff=HandoffContext(
                manager=self,
                runtime=runtime,
                session=session,
                settings=self._settings,
                form=profile.form,
            ),
        )
        # 主控的模型只建一次：压缩中间件要用**同一个实例** —— 触发线是按它 profile 上
        # 那个窗口算的（``agent/llm.py::_model_from`` 写进去的那一格）
        main_model = self._model_for(profile.form)
        subagents = build_subagents(
            tools_by_role={
                role: build_subagent_tools(
                    role, self._settings, self._web_search, self._fetch_url,
                    session.repo, self._db, plan_ctx,
                )
                for role in profile.subagents
            },
            model_for=self._model_for,
            policies={
                role: policy_for(role, self._settings) for role in profile.subagents
            },
            safety=self._safety,
            fallbacks={
                role: fb
                for role in profile.subagents
                if (fb := self._fallback_middleware(role, role)) is not None
            },
            # 子智能体也各挂一份记忆中间件（每次调用现给一个实例：它们可能并发跑）
            memory_factory=lambda: build_memory_middleware(
                self._settings, session.repo, runtime.backend
            ),
            # 压缩（含压缩线处的应急裁剪）：每个角色按**它自己那个模型**的窗口算触发线。
            # 上游那份已在 ``agent/graph.py`` 里按类排掉，所以这里不给就是「这个角色不压缩」
            summarization_for=lambda role, model: build_summarization(
                model, runtime.backend, self._settings, agent=role
            ),
        )
        agent = build_agent(
            model=main_model,
            backend=runtime.backend,
            tools=main_tools,
            store=self._store,
            checkpointer=self._checkpointer,
            system_prompt=build_main_prompt(
                session.repo_url, form=profile.form, settings=self._settings
            ),
            # 边界按**形态**取（与模型、主工具同一根轴），子智能体按角色各取一份
            policy=policy_for(profile.form, self._settings),
            safety=self._safety,
            fallback=self._fallback_middleware(profile.form, MAIN_AGENT),
            # 记忆：所有形态都挂 —— 每轮开始把记忆全文注入系统提示，模型不必再读一遍。
            # ``build_memory_middleware`` 按形态判要不要带闸门：plan / coding 两个主控
            # 才带（先查 PR、刷新记忆，有未合入的就拦下本轮），其余形态是只读注入。
            memory=build_memory_middleware(
                self._settings, session.repo, runtime.backend, profile.form
            ),
            # 压缩（同一根开关，含压缩线处的应急裁剪）：上游那份已排掉，不传就是不压
            summarization=build_summarization(
                main_model,
                runtime.backend,
                self._settings,
                agent=MAIN_AGENT,
                form=profile.form,
            ),
            subagents=subagents,
            skills=[self._settings.sandbox_skills_dir],
        )
        logger.info(
            "主智能体构建完成：public_id=%s form=%s",
            session.public_id,
            profile.form,
        )
        return agent

    async def destroy_session(self, *, public_id: str, db_id: int) -> None:
        """销毁会话：软取消在飞的构建 → 终止工作区 → 库里标记 destroyed。

        传入的是 id 而非 ``Session``：会话不在内存时（进程重启过）也走这条路径，那会儿
        手上只有库里的行，没有仓库信息可组装 —— 而销毁并不需要仓库信息。
        """
        logger.info("销毁会话：public_id=%s", public_id)
        pending = self._pending.pop(public_id, None)
        if pending is not None:
            # 构建任务拿到容器后自己回收（见 _Pending 的说明）
            pending.cancelled = True
        runtime = self._runtimes.pop(public_id, None)
        if runtime is not None:
            await runtime.sandbox.destroy()
        else:
            stored = await self._stored_sandbox_id(db_id)
            if stored:
                await self._provider.terminate(stored)
        async with self._db.session() as db:
            await dao.set_session_status(db, db_id, "destroyed")

    # -- 内部：工作区构建 --------------------------------------------------
    def _pending_for(self, session: Session) -> _Pending:
        """取（或起）该会话在飞的构建，同一会话只会有一次。"""
        pending = self._pending.get(session.public_id)
        if pending is not None:
            return pending
        pending = _Pending(public_id=session.public_id)
        pending.start(self._build_runtime(session, pending))
        pending.task.add_done_callback(self._on_build_done)
        self._pending[session.public_id] = pending
        return pending

    def _on_build_done(self, task: asyncio.Task[SessionRuntime]) -> None:
        """构建结束后：清掉在飞记录、把建成的运行时收进缓存、消费掉没人 await 的异常。

        没人 await 的情况是常态：``POST /sessions`` 起了构建就不管了，调用方只靠
        ``GET /sessions/{id}`` 看状态。不消费异常会让 asyncio 在任务被回收时打
        「exception was never retrieved」。

        **收进缓存这一步必须在这里做**：后台建成的运行时若没人接，容器虽在、库里也有
        ``sandbox_id``，但 ``_runtimes`` 里没有，下一次 ``ensure_runtime`` 会当成本进程
        没建过而重走一遍 ``reconnect`` —— 实测那趟要 6~7s，等于白建一次。
        """
        pending: _Pending | None = None
        for public_id, record in list(self._pending.items()):
            if record.task is task:
                del self._pending[public_id]
                pending = record
                break

        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.info("工作区构建未成功（详情见 _build_runtime 的日志）")
            return
        if pending is None or pending.cancelled:
            # 等待方已放弃（超时/删会话）：容器由构建任务自己回收，收进缓存等于留个死句柄
            return
        if pending.public_id not in self._runtimes:
            self._runtimes[pending.public_id] = task.result()

    def _abandon(self, pending: _Pending) -> None:
        """放弃等待一次构建（超时/失败）：置软取消标志并让位给下一次重试。"""
        pending.cancelled = True
        if self._pending.get(pending.public_id) is pending:
            del self._pending[pending.public_id]

    async def _build_runtime(
        self, session: Session, pending: _Pending
    ) -> SessionRuntime:
        """建工作区并落库状态；失败时把原因写进 ``workspace_error``。

        失败一律转成 ``WorkspaceError``：重试判定只认这一种，provider 抛什么类型的
        异常都要落到「重建一次、仍失败就报固定措辞」这条路上，而不是穿成 500。
        """
        try:
            runtime = await self._provision(session, pending)
        except WorkspaceError as exc:
            await self._mark(session, WORKSPACE_FAILED, error=str(exc))
            raise
        except Exception as exc:
            # 原始原因只进库（ops 看）与日志，对调用方统一成 WorkspaceError
            await self._mark(session, WORKSPACE_FAILED, error=str(exc))
            raise WorkspaceError(str(exc)) from exc
        await self._mark(session, WORKSPACE_READY, error=None)
        return runtime

    async def _provision(self, session: Session, pending: _Pending) -> SessionRuntime:
        await self._mark(session, WORKSPACE_PROVISIONING, error=None)
        stored = await self._stored_sandbox_id(session.db_id)
        volumes, volume_name, env = self._runtime_inputs(session)

        client = await self._provider.reconnect(stored) if stored else None
        if client is not None:
            logger.info("工作区重连成功：public_id=%s sandbox=%s", session.public_id, client.id)
        else:
            client = await self._provider.create(volumes, env)
            logger.info("工作区创建完成：public_id=%s sandbox=%s", session.public_id, client.id)

        if pending.cancelled:
            # 等待方已经放弃（删会话/超时）：这个容器刚建出来就没主了，就地回收
            await self._destroy_quietly(client)
            raise WorkspaceError("代码工作区创建已取消")

        try:
            sandbox = self._wrap_sandbox(
                client,
                public_id=session.public_id,
                db_id=session.db_id,
                volumes=volumes,
                env=env,
                volume_name=volume_name,
            )
            backend = build_composite_backend(
                OpenSandboxBackend(sandbox, safety=self._safety),
                self._store,
                self._settings.memories_prefix,
            )
        except Exception:
            await self._destroy_quietly(client)
            raise

        # 回填 sandbox_id / 卷名：既是「下次优先重连」的依据，也是删会话时的回收线索
        async with self._db.session() as db:
            await dao.set_session_sandbox(
                db, session.db_id, sandbox_id=sandbox.id, workspace_volume=volume_name
            )
        logger.info("代码工作区就绪：public_id=%s sandbox=%s", session.public_id, sandbox.id)
        return SessionRuntime(sandbox=sandbox, backend=backend)

    def _runtime_inputs(
        self, session: Session
    ) -> tuple[list[Volume] | None, str | None, dict[str, str]]:
        """建/重连沙箱的入参：工作区卷（挂 public_id 名下，重建可复用）+ 环境变量。

        环境变量里带沙箱 git 凭据：git 经 GIT_ASKPASS 取（脚本由 provider 推入），令牌
        由 setup 脚本落到 .secrets；令牌与 git 用户名只在有 token 时注入。

        Maven 本地仓库另挂一个缓存卷（``/ai_coding/.m2``）：默认跨会话共享，让「每次构建
        都重新下依赖」变成最多只下一次（见 ``sandbox/volumes.py``）。
        """
        volumes: list[Volume] = []
        volume_name = None
        if self._settings.sandbox_volume_enabled:
            volume = build_workspace_volume(self._settings, session.public_id)
            if volume is not None:
                volumes.append(volume)
                volume_name = workspace_volume_name(self._settings, session.public_id)
            cache = build_maven_cache_volume(self._settings, session.public_id)
            if cache is not None:
                volumes.append(cache)
        env = {
            "GIT_ASKPASS": self._settings.git_askpass_path,
            "GIT_TERMINAL_PROMPT": "0",
        }
        if session.repo.token:
            env[self._settings.sandbox_git_token_env] = session.repo.token
            env[self._settings.sandbox_git_username_env] = session.repo.git_username
        return (volumes or None), volume_name, env

    async def _mark(self, session: Session, status: str, *, error: str | None) -> None:
        """落库工作区状态；写库失败只记账，不能让状态记录把主流程带崩。"""
        try:
            async with self._db.session() as db:
                await dao.set_workspace_status(
                    db, session.db_id, status, error=error
                )
        except Exception:
            logger.exception(
                "写工作区状态失败：public_id=%s status=%s", session.public_id, status
            )

    async def _stored_sandbox_id(self, db_id: int) -> str | None:
        """库里记的沙箱 id（可能已被 OpenSandbox 侧回收，重连会失败再回落新建）。"""
        try:
            async with self._db.session() as db:
                row = await dao.get_session(db, db_id)
        except Exception:
            logger.exception("读会话沙箱 id 失败：db_id=%s", db_id)
            return None
        return row.sandbox_id if row is not None else None

    async def _destroy_quietly(self, client: SandboxClient) -> None:
        """回收半成品容器；失败只记账 —— 它不能盖掉真正的失败原因。"""
        try:
            await self._provider.destroy(client)
        except Exception:
            logger.exception("回收半成品沙箱失败：sandbox=%s", client.id)

    def _wrap_sandbox(
        self,
        client: SandboxClient,
        *,
        public_id: str,
        db_id: int,
        volumes: list[Volume] | None,
        env: dict[str, str] | None,
        volume_name: str | None,
    ) -> OpenSandboxProxy:
        """把裸沙箱包成可用性代理；重建后把新 sandbox_id 回填库。

        卷名由 ``public_id`` 决定、重建时不变，故回填时原样写回 ``volume_name``。
        """

        async def on_rebuild(new_sandbox_id: str) -> None:
            async with self._db.session() as db:
                await dao.set_session_sandbox(
                    db,
                    db_id,
                    sandbox_id=new_sandbox_id,
                    workspace_volume=volume_name,
                )
            logger.info(
                "沙箱重建后已回填：public_id=%s sandbox=%s", public_id, new_sandbox_id
            )

        return OpenSandboxProxy(
            settings=self._settings,
            provider=self._provider,
            client=client,
            public_id=public_id,
            volumes=volumes,
            env=env,
            on_rebuild=on_rebuild,
        )
