"""API request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    repo_url: str
    # 平台由 repo_url 判定；令牌**不在这里传** —— 统一取设置页「代码平台」那两格
    # （2026-10-20 用户口径：取消 OAuth 登录，令牌改成配置项）


class SessionResponse(BaseModel):
    """建会话 / 准备代码工作区的回执。``workspace_status`` 见 ``SessionDetail``。

    ``reused`` 为真表示这个仓库已有活跃会话，本次落到既有会话上（没新建也没建工作区），
    前端据此还原历史而不是当新会话；准备代码工作区的回执里恒为假。
    """

    session_id: str
    workspace_status: str
    reused: bool = False


class SessionDetail(BaseModel):
    """会话详情：前端据此显示代码工作区进度（准备中 / 可用 / 异常）。

    ``workspace_error`` 不外发 —— 那是沙箱侧原始异常文本，只给运维看（见 DB 注释）。
    ``running`` 表示本会话有一轮对话正在跑：页面刷新后据它决定要不要接回直播流。
    """

    session_id: str
    title: str | None = None
    owner: str | None = None
    repo: str | None = None
    repo_url: str | None = None
    status: str
    workspace_status: str
    running: bool = False
    created_at: datetime


class SessionListItem(BaseModel):
    session_id: str
    title: str | None = None
    owner: str | None = None
    repo: str | None = None
    repo_url: str | None = None
    status: str
    created_at: datetime


class MemorySnapshot(BaseModel):
    """仓库记忆（StoreBackend 上那份 Markdown）。

    ``content`` 为 ``None`` ＝ 这个仓库**还没写过**记忆 —— 与「写过一个空文件」是两件事，
    前端据此显示空态而不是一片空白。
    """

    path: str
    content: str | None = None


class LogLineItem(BaseModel):
    """一行日志。``text`` 是原始整行（前端直接显示），``level`` 供着色（认不出就是空串）。"""

    ts: str = ""
    level: str = ""
    #: 打这行的 logger 名（认不出就是空串）；也给前端一个按来源分辨的依据
    logger: str = ""
    text: str


class LogPage(BaseModel):
    """该会话的日志尾部。``total`` 是命中的总行数（可能大于 ``lines`` 的长度）。"""

    total: int = 0
    lines: list[LogLineItem] = Field(default_factory=list)


class PullItem(BaseModel):
    """一条 PR（平台差异已归一，见 ``tools/pr_client.pull_summary``）。

    ``state`` 是**状态码**：``open`` / ``merged`` / ``closed`` / ``unknown`` —— 中文标签交给
    前端，接口不掺展示口径。``unknown`` 是「平台回了个我们不认识的状态」，**不当成没开**。
    """

    number: str
    title: str
    state: str
    url: str = ""
    branch: str = ""
    author: str = ""
    created_at: str = ""
    updated_at: str = ""


class PullList(BaseModel):
    """本仓库的 PR 列表。``open_count`` ＝ **未合并**的条数（主区顶部那个角标用它）。"""

    open_count: int = 0
    pulls: list[PullItem] = Field(default_factory=list)


class ModelConfigEffective(BaseModel):
    """一个角色**现在真的用**什么（设置页的「生效值」）。

    ``source`` 三态：``row``（这一行自己配的）/ ``unified``（被「统一模型」顶掉）/ ``unset``
    （没配 —— 用到它的角色会当场报错）。
    """

    role: str
    #: 真正取值的那一行（``unified`` 覆盖时就是 ``unified``）
    row: str = ""
    model: str = ""
    #: 解析之后的窗口（行内手填 → 按模型查 → 保守兜底值）
    window: int = 0
    #: 那个窗口**哪来的**：`row`（行内手填）/ `model`（按模型查到的）/ `fallback`（我们的保守值，
    #: **不是**厂商给的数）/ `none`（这一行没配）—— 界面只回显 row / model 两种真值
    window_source: str = "none"
    base_url: str = ""
    has_key: bool = False
    source: str = "unset"


class CodingGroupRole(BaseModel):
    """「编码」那一组的**一个角色**（按角色展示，而不是按行）：落在哪一行、是不是复用别人的行。"""

    role: str
    label: str
    row: str
    reused: bool = False


class ModelConfigRow(BaseModel):
    """「模型配置」里的一行：按**智能体**配（2026-10-13）。

    ``api_key`` 只回**打码值**（``sk-…e2b7``），明文永不出口。

    ``source`` 说的是**这一行自己**填过没有：``db``（库里有一行）/ ``unset``（没配过）；
    **一个模型都没配的行是空的**，没有任何「出厂默认」可回显（2026-10-15 用户口径：
    干掉出厂默认 —— 解析层也不再回落到 ``.env``）。

    ``effective`` 才是「现在真的用谁」：那一行可能没配、也可能被「统一模型」顶掉。
    ``roles`` 是这一行**服务哪些角色**（界面靠它把「编码」那一组按角色画出来），
    ``provider_locked`` 为真时界面上「供应商」那一栏不可改（现在只有 JEV 那一行）。
    """

    key: str
    label: str
    #: 属于左菜单里哪一栏（``agents`` / ``global`` / ``intent``，见 ``config.MODEL_SECTIONS``）
    section: str = "agents"
    #: 这一行服务哪些角色（``orchestrate`` / ``planner`` / ``coder`` / ``reviewer`` …）
    roles: list[str] = Field(default_factory=list)
    #: 界面「提供商」那一栏现在选中的是谁（按 base_url 反推；**不存库**，纯界面提示）
    provider: str = "custom"
    #: 供应商锁死（界面不让改）：JEV 那一行锁 ``typesafe``
    provider_locked: bool = False
    base_url: str = ""
    model: str = ""
    api_key_masked: str = ""
    has_key: bool = False
    context_window: int = 0
    source: str = "unset"
    #: 现在真的用谁（没配就是 ``source=unset``）
    effective: ModelConfigEffective | None = None


class ProviderModelItem(BaseModel):
    """一家供应商下的**一个模型**（表 ``provider_models`` 的一行）。

    ``context_window`` 就是「这个模型的上下文长度」——窗口按**模型**查的依据；0 ＝ 还不知道
    （界面照实显示成「未知」，不假装是 0）。
    """

    provider: str
    model: str
    #: chat / embedding / rerank / image / video / audio / other（见 ``model_types.py``）
    model_type: str = "chat"
    context_window: int = 0
    #: 这个窗口/类型哪来的：probe（刷新时读到的）/ manual / seed
    source: str = "manual"
    status: int = 1


class ProviderModelList(BaseModel):
    """一家的模型清单（GET 与刷新都回这一份）。"""

    provider: str
    items: list[ProviderModelItem] = Field(default_factory=list)
    #: 这次刷新从厂商那儿读回来几条（GET 时是 0）
    refreshed: int = 0
    #: 刷新时的话（例如「入口没回窗口」）；正常是空串
    note: str = ""


class ProviderModelUpsert(BaseModel):
    """手填 / 手改一个模型（只给要改的字段：不传＝保持原值）。"""

    model: str
    model_type: str | None = None
    context_window: int | None = None
    status: int | None = None


class RuntimeSettingItem(BaseModel):
    """一格运行期配置（代码缓存 / 上下文窗口那两类里的一项）。

    ``source`` 说的是**现在生效的这个值是哪来的**：``db``（在界面里改过）还是 ``default``
    （``.env`` / 代码默认值）。界面据此标一格「已改过」，与模型配置那套口径一致。

    ``kind="secret"`` 的格子额外带 ``has_value``：``value`` 只在**打开设置页时**回掩码，
    保存之后的响应里是空串（用户口径 2026-10-20：配过就有提示，保存完不再回显）。
    """

    key: str
    label: str
    group: str = ""
    #: 值的类型：``str`` / ``int`` / ``float``（界面据此选输入框与校验）
    kind: str = "str"
    value: Any = None
    #: 这一格是否已配置值（仅 ``secret`` 有意义；空串 + 它才是「配过但没回显」）
    has_value: bool = False
    source: str = "default"


class RuntimeSettingsSnapshot(BaseModel):
    """运行期配置的全貌（GET / PUT 都回这一份，界面一次刷新到位）。"""

    items: list[RuntimeSettingItem] = Field(default_factory=list)


class RuntimeSettingsUpdate(BaseModel):
    """保存请求：**只有给了的键才会被写**（不传＝不改）。"""

    items: dict[str, Any] = Field(default_factory=dict)


class ProviderOption(BaseModel):
    """厂商目录里的一项。

    目录本身存在库里（表 ``model_providers``，新库按 ``config/model_providers.toml`` 灌一次），
    **每次请求现读**：改库 / 在设置页「提供商」那一栏改，刷新界面就生效，不用重启后端。

    2026-10-15 起，设置页的「提供商」栏要能**编辑整行**，所以把 ``models_path`` /
    ``auth_header`` / ``auth_prefix`` / ``status`` / ``is_dynamic_model_list`` 也一起下发。
    **列表接口给的是全量（含停用）**，快照里那份仍是"只含启用"（下拉用）。
    同日起**不再有内置候选**（`default_models` 那份种子清单已删）：模型名只认「刷新清单」
    拉到的那份（表 `provider_models`）。

    **「测试连接」可不可点只看两件事**：这一行的 ``base_url`` 有没有、``api_key`` 有没有
    （库里那把或界面上刚填的那把，见 ``ModelConfigRow.has_key``）。所以这里**不**给界面任何
    「这家支不支持探测」的开关 —— 目录行怎么写都不拦。
    """

    key: str
    label: str
    base_url: str = ""
    #: 下拉里的分组名（空＝不分组）
    ui_group: str = ""
    #: 组内排序（小的在前）—— 后端已排好，界面照画即可
    sort_order: int = 0
    #: 拉清单的路径：相对路径拼在 ``base_url`` 后面，**也可以写绝对 URL**（qwen 的清单在另一个根下）
    models_path: str = "/models"
    #: 鉴权头怎么写（多数是 ``Authorization`` + ``Bearer ``；有的网关是 ``x-api-key``）
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "
    #: 1 启用 / 0 停用（停用的不进下拉，但**在「提供商」栏里看得到**，可以重新启用）
    status: int = 1
    #: 这家官方有没有 ``/models``（只是标注；「测试连接」不看它）
    is_dynamic_model_list: bool = True


class ProviderUpsert(BaseModel):
    """写一行提供商目录（**整行覆盖**，2026-10-15）。

    键（``provider``）在路径里，**不能改**：它是对外键目标（模型行、agent 行都按它挂）。
    新增一行时键由用户填（小写字母 / 数字 / 下划线 / 连字符）。
    """

    label: str
    base_url: str = ""
    ui_group: str = ""
    sort_order: int = 0
    models_path: str = "/models"
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "
    status: int = 1
    is_dynamic_model_list: bool = True


class ModelConfigSnapshot(BaseModel):
    """模型配置的全貌（GET / PUT / DELETE 都回这一份，界面一次刷新到位）。"""

    rows: list[ModelConfigRow] = Field(default_factory=list)
    #: 内置厂商目录（key / 中文名 / base_url）：界面「先选提供商」用它带出地址
    providers: list[ProviderOption] = Field(default_factory=list)
    #: 左菜单这一栏里的小节（key + 中文名）—— 界面按它分组，不自己硬编码行名
    sections: list[SectionOption] = Field(default_factory=list)
    #: 「编码」那一组按角色展示：``(角色, 落在哪一行, 是否复用)``
    coding_group: list[CodingGroupRole] = Field(default_factory=list)
    #: 意图识别走哪一关：``jev`` / ``llm``（**严格二选一**，不互相兜底）
    intent_mode: str = "jev"
    #: JEV 这一侧配齐了没有（地址 + key 两样）；false 时界面说明「这一侧不可用，会落关键字口径」
    jev_configured: bool = False


class SectionOption(BaseModel):
    """左菜单里的一小节（``MODEL_SECTIONS`` 的一项）。"""

    key: str
    label: str


class ModelConfigRowUpdate(BaseModel):
    """一行的写入：**只有给了的字段才会被写**（不传＝不改；空串＝清掉）。"""

    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    context_window: int | None = None


class ModelConfigUpdate(BaseModel):
    """保存请求：多行一起写 + 可选改意图识别那一关。"""

    rows: dict[str, ModelConfigRowUpdate] = Field(default_factory=dict)
    intent_mode: str | None = None


class ProviderProbe(BaseModel):
    """「测试连接」的入参：拿这套 base_url / key 去问厂商的 ``/models``。"""

    base_url: str
    api_key: str | None = None
    #: 没重填 key 时，从哪一行的现有配置里取（明文不出后端）
    key_from: str | None = None
    #: 界面上选中的提供商（用来取那一家的路径 / 鉴权头 / 内置候选；不认识就当自定义）
    provider: str | None = None


class ProviderProbeResult(BaseModel):
    """探测结果：``ok=false`` 时 ``error`` 是给人看的短句（不是 500）。"""

    ok: bool
    models: list[str] = Field(default_factory=list)
    #: 上面那份清单是哪来的：``live``＝刚从厂商拉的；``builtin``＝库里的内置候选（可能过时）
    source: str = "live"
    #: 读到的窗口（0 ＝ 没读到，界面提示用户自己填）
    context_window: int = 0
    note: str = ""
    error: str = ""


class UsageTokens(BaseModel):
    """一组输入 / 输出计数（一轮的合计、或某个执行主角的累计）。"""

    input: int = 0
    output: int = 0
    calls: int = 0


class UsageContext(BaseModel):
    """上下文压力：**最近一次主控调用**的输入离模型的窗口还有多远。

    ``window`` 是**该轮主控模型**的窗口（orchestrate / plan 是 GLM 128k、review 是
    Kimi 256k），所以同一份历史在不同形态下的 ``ratio`` 本来就不同 —— 这是事实而非误差。
    ``input`` 是**最近一次主控 LLM 调用**的输入 tokens（2026-10-20 用户口径：环图看
    「现在多长」，不看历史峰值）。压缩之后它自然就是压完的长度。
    ``max_input`` 是旧记录里的字段名（当时取的是峰值），保留只为读老数据。

    ``warn_ratio`` / ``alert_ratio`` 是**服务端认的**两条线（设置页可改）—— 环图的变色口径
    由它们给，界面不写死 0.6 / 0.8。旧记录里没有这两格时用同样的默认值。
    """

    input: int = 0
    #: 旧口径（历史峰值）：只用于读本功能改版之前的记录
    max_input: int = 0
    window: int = 0
    ratio: float = 0.0
    compressed: int = 0
    compressed_now: bool = False
    warn_ratio: float = 0.6
    alert_ratio: float = 0.8


class UsageDetail(BaseModel):
    """**一轮**的消耗明细（每条 assistant 记录一份，也是 SSE ``usage`` 帧的载荷）。

    与 ``UsageSnapshot``（会话级聚合视图）是两个东西：这个是原始账、那个是把它加起来。
    两边共用 ``UsageTokens`` / ``UsageActor``，免得同一个概念长出两种字段名。
    """

    totals: UsageTokens = Field(default_factory=UsageTokens)
    by_actor: dict[str, UsageTokens] = Field(default_factory=dict)
    #: 这一轮**主控**解析出来的模型名（界面标在这条回答的最下边）。空 ＝ 解析不到，不显示那一格。
    #: **只标解析值**：真走了兜底这里不体现 —— 兜底是运行时的第二跳，账与调用都记在同一个角色名下。
    model: str = ""
    #: 平台没回用量的调用次数（账上的缺口，如实显示而不是当 0）
    missing: int = 0
    #: 这一轮没跑完（用户终止 / 守卫中止 / 故障）—— 钱照样算，但要标出来
    interrupted: bool = False
    context: UsageContext | None = None


class UsageActor(BaseModel):
    """一个执行主角在本会话的累计用量。

    ``key`` 是固定归类名（``main`` / ``planner`` / ``coder`` / ``reviewer`` /
    ``internal`` / ``intent``），中文标签留在前端 —— 与 PR 的 ``state`` 码同一口径：
    接口不掺展示文案。``internal`` 是中间件自己的调用（上下文摘要），单次就是几万输入，
    所以单列；``intent`` 是判定那笔（另一个模型）。
    """

    key: str
    input: int = 0
    output: int = 0
    calls: int = 0
    total: int = 0


class UsageSnapshot(BaseModel):
    """本会话的 token 消耗（右栏「Token」标签）。

    这是一个**聚合视图**，数据源是各轮 assistant 记录 ``extra.usage``；本功能上线前
    的历史记录没有那份数据，所以：

    - ``rounds`` ＞ ``rounds_with_usage`` 时界面必须说「其中 N 轮没有用量数据」，
      **不能把缺数据的轮次当成 0 消耗**（那是最容易糊弄过去、也最容易看错的一种错）；
    - ``missing`` 是「平台没回用量的调用次数」，同样如实外传而不是抹掉。

    **不含上下文压力**（2026-10-20 用户口径）：那一格挪到输入框右边的环图，
    面板只留 token 账。
    """

    totals: UsageActor = Field(default_factory=lambda: UsageActor(key="total"))
    actors: list[UsageActor] = Field(default_factory=list)
    #: **主控按形态**拆出来的用量（qa / analysis / plan / review / coding / rework）；
    #: 子智能体不在里面 —— 它们本来就是独立的执行主角（见 ``actors``）。
    by_form: dict[str, UsageActor] = Field(default_factory=dict)
    rounds: int = 0
    rounds_with_usage: int = 0
    rounds_interrupted: int = 0
    missing: int = 0


class RecordItem(BaseModel):
    id: int
    type: str
    summary: str | None = None
    content: str | None = None
    extra: Any | None = None
    #: 这一轮的 token 账（只有 assistant 记录有；本功能上线前的记录为 ``None``）。
    #: 从 ``extra["usage"]`` 提出来单列：聊天里那条消息的角标读它，前端不必知道内层形状。
    usage: UsageDetail | None = None
    created_at: datetime


class RecordPage(BaseModel):
    """历史记录的一页：``items`` 已按时间正序，``next_before_id`` 是继续向前翻的游标。"""

    items: list[RecordItem] = Field(default_factory=list)
    has_more: bool = False
    next_before_id: int | None = None


class ChatRequest(BaseModel):
    message: str = ""
    #: 前端点候选时回传；为空表示普通消息（普通消息会立刻让旧澄清失效）。
    clarification_id: str | None = None
    #: 候选下标（0 开始）或 ``"other"``。
    choice: int | str | None = None


class ClarificationOption(BaseModel):
    intent: str
    label: str
    description: str = ""


class ClarificationInfo(BaseModel):
    """SSE ``clarification`` 帧 / GET 待澄清接口的同一份结构。"""

    id: str
    question: str
    options: list[ClarificationOption] = Field(default_factory=list)
    allow_other: bool = True


class PlanStepItem(BaseModel):
    """方案里的一步。历史数据可能带脏字段，故只保证 index/goal，其余给默认。"""

    index: int
    goal: str
    status: str = "pending"
    files: Any | None = None
    done_criteria: str | None = None


class PlanInfo(BaseModel):
    """方案快照：SSE 的 plan 事件与 GET /sessions/{id}/plan 共用同一份结构。"""

    plan_id: int
    title: str | None = None
    status: str
    steps: list[PlanStepItem] = Field(default_factory=list)
    #: 方案全文（Markdown）。聊天里那条「方案」链接点开就地展开的就是它 ——
    #: 带上它之后，实时帧与刷新后读记录拿到的是同一份数据，前端不必再补一次请求。
    content: str | None = None
    updated_at: datetime


# --------------------------------------------------------------------------- 代码面板
# 宿主机侧的仓库只读缓存（``infra/repo_cache.py``）：这几个结构只服务右侧栏「代码」标签。


class RepoBranch(BaseModel):
    """一支分支。``is_default`` 由裸仓的 HEAD 判定（前端据此把它排在/标在最前）。"""

    name: str
    sha: str
    updated_at: str = ""
    subject: str = ""
    is_default: bool = False


class RepoStatus(BaseModel):
    """缓存状态 + 分支清单 —— 打开面板先看它，据此决定显示「下载仓库」还是直接浏览。

    ``cloned`` 为假时 ``branches`` 一定是空的；``fetched_at`` 是上次**成功**同步的时间。
    """

    cloned: bool
    default_branch: str = ""
    branches: list[RepoBranch] = Field(default_factory=list)
    fetched_at: str | None = None
    git: str | None = None


class RepoSyncResult(RepoStatus):
    """同步回执：比 ``RepoStatus`` 多一个「这次是克隆还是更新」。"""

    cloned_now: bool = False


class RepoTreeEntry(BaseModel):
    name: str
    path: str
    type: str
    size: int | None = None


class RepoTree(BaseModel):
    ref: str
    path: str = ""
    entries: list[RepoTreeEntry] = Field(default_factory=list)


class RepoFile(BaseModel):
    """一个文件的正文或「为什么不给正文」：``too_big`` / ``binary`` 为真时 ``text`` 为空。"""

    path: str
    ref: str
    text: str | None = None
    size: int = 0
    binary: bool = False
    too_big: bool = False
