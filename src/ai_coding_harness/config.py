"""Runtime configuration loaded from environment variables / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ai_coding_harness.codeplatform import Platform


def _default_repo_view_dir() -> str:
    """代码面板缓存的默认位置：**用户个人目录**下的 `~/.ai-coding-harness/repo_cache`。

    这是跟着机器走的路径，所以用工厂算出来而不是写死字符串；`REPO_VIEW_DIR` 照旧可覆盖。
    """
    return str(Path.home() / ".ai-coding-harness" / "repo_cache")


@dataclass(frozen=True)
class ModelConfig:
    """OpenAI-compatible model configuration (model name + endpoint + key)."""

    model: str
    api_key: str
    base_url: str
    context_window: int = 0  # 上下文窗口（tokens）；0 表示未知
    # 温度是**逐家**的，不是全局常量：kimi-k2.6 只收 1，传 0.3 直接 400
    # （``invalid temperature: only 1 is allowed for this model``），而其余几家 0.3
    # 是调优结果（温度高时方案发散）。默认 0.3，例外的那家在属性里显式给。
    temperature: float = 0.3


class Settings(BaseSettings):
    """Settings for the harness service.

    Values come from environment variables (no prefix) or a ``.env`` file.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM — DeepSeek (primary) and Kimi (backup), both OpenAI-compatible.
    deepseek_api_key: str = ""
    deepseek_base_url: str = ""
    deepseek_model_name: str = ""

    kimi_api_key: str = ""
    kimi_base_url: str = ""
    kimi_model_name: str = ""

    glm_api_key: str = ""
    glm_base_url: str = ""
    glm_model_name: str = ""

    gpt_api_key: str = ""
    gpt_base_url: str = ""
    gpt_model_name: str = ""

    qwen_api_key: str = ""
    qwen_base_url: str = ""
    qwen_model_name: str = ""

    # 各模型的上下文窗口（tokens），供上下文压缩按比例计算阈值（80% 触发）。
    # glm / gpt / qwen 三家接口都**不暴露窗口**（实测 /models 只返回模型名；拿得到的
    # 131072 / 128000 是**输出**上限，不是窗口），文档也取不到，所以取保守值：偏小只是
    # 早一点触发压缩，偏大则会真的把请求撑溢出（窗口是硬上限，超了直接报错）。
    deepseek_context_window: int = int(os.getenv("DEEPSEEK_CONTEXT_WINDOW", "1048576"))
    kimi_context_window: int = int(os.getenv("KIMI_CONTEXT_WINDOW", "262144"))
    glm_context_window: int = int(os.getenv("GLM_CONTEXT_WINDOW", "128000"))
    gpt_context_window: int = int(os.getenv("GPT_CONTEXT_WINDOW", "128000"))
    qwen_context_window: int = int(os.getenv("QWEN_CONTEXT_WINDOW", "1048576"))

    # 兜底供应商：某家彻底失败（SDK 自带重试耗尽）时，临时改用这家的模型再试一次。
    # 只有**全局一条**保底（现定 deepseek），不做链式降级；角色本来就用这家的（coder
    # 用 deepseek）等于没有兜底，不重复兜。留空 = 关掉兜底。
    fallback_provider: str = "deepseek"

    # 兜底的重试有代价：智能体一轮对话要跑多次模型节点（model → tools → model …），
    # 主导死着的时候**每一轮**都得先撞死一次才轮到兜底，半路断流那种还会把半截句子重复
    # 吐进流里。所以主导连续失败到阈值就开闸，冷却期内不再撞它，直接走兜底。
    # 阈值 0 = 关掉熔断（退回每轮都撞）；冷却到点放**一次**探针，探针成功即恢复。
    # 状态按主导模型名记、进程内共享（多个 worker 各记一份，跨进程要做就得进 Redis）。
    fallback_circuit_threshold: int = int(os.getenv("FALLBACK_CIRCUIT_THRESHOLD", "3"))
    fallback_circuit_cooldown_seconds: float = float(
        os.getenv("FALLBACK_CIRCUIT_COOLDOWN_SECONDS", "60")
    )

    # Redis（**可选**）：多 worker 时的跨进程旁路状态 —— 运行缓冲（刷新后接回哪一轮、
    # 以游标重放）、会话运行权（同一会话不许两个 worker 同时跑）、模型熔断状态。
    # 留空 `REDIS_URL` = 全部留在进程内，就是 2026-09-26 之前的行为（单 worker 够用）。
    # `REDIS_REQUIRED=true` 时连不上直接启动失败 —— 多 worker 部署该开：静默退回进程内
    # 意味着「你以为共享的东西其实各 worker 一份」，比启动失败更难查（见 redis_cache.py）。
    redis_url: str = os.getenv("REDIS_URL", "")
    # 密码单独一个键（而不是塞进 URL）：① URL 会被日志原样打出来，密码进去就跟着泄；
    # ② 两边都写时以这个键为准（见 redis_cache.default_client_factory）。
    redis_password: str = os.getenv("REDIS_PASSWORD", "")
    redis_prefix: str = os.getenv("REDIS_PREFIX", "harness:")
    redis_required: bool = os.getenv("REDIS_REQUIRED", "false").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    # 单条命令的超时：Redis 半死时宁可降级，也不能让一轮对话卡在这里
    redis_timeout_seconds: float = float(os.getenv("REDIS_TIMEOUT_SECONDS", "1.0"))
    # 运行缓冲的存活时间与容量上限（超出上限从最老的帧开始丢）
    run_buffer_ttl_seconds: int = int(os.getenv("RUN_BUFFER_TTL_SECONDS", "3600"))
    run_buffer_max_frames: int = int(os.getenv("RUN_BUFFER_MAX_FRAMES", "2000"))
    # 会话运行权的 TTL（持有者进程被 kill 后的兜底）与续期间隔
    run_lock_ttl_seconds: int = int(os.getenv("RUN_LOCK_TTL_SECONDS", "120"))
    run_poll_interval_seconds: float = float(os.getenv("RUN_POLL_INTERVAL_SECONDS", "0.2"))

    # 意图分类（每轮一次）：强/弱关键字直通，其余交 LLM 判定，失败回落关键字。
    # 分类与主智能体的差别只在参数：分类必须关思考（vLLM 只认 chat_template_kwargs
    # 那个键，实测 2.8~5.0s → 0.3s），主智能体不传该参数。同一模型两套参数，故单独
    # 一套配置；三项必填，未配置直接报错，不回落主模型。
    intent_llm_enabled: bool = True
    intent_model: str = ""
    intent_base_url: str = ""
    intent_api_key: str = ""
    intent_timeout_seconds: float = 5.0
    # 分类时附带的历史用户消息条数（指代要靠它，如「就按你说的弄」）
    intent_history_turns: int = 4

    # JEV（SystemOne，决策型模型）做意图判定 —— 判定瀑布的第二关（见 server/intent.py）：
    # 口令 → **JEV** → 大模型 → 关键字口径（没命中就是 DEFAULT_INTENT = coding）。
    # SDK 是 `typesafe-sdk`，**懒加载**：没装这个包或没配 key 就跳过这一关，服务照起。
    # 键名沿用官方 `TYPESAFE_*`；模型名优先看 `TYPESAFE_MODEL_NAME`（.env 里用的就是它），
    # 再回落 SDK 的 `TYPESAFE_DEFAULT_MODEL`，最后是 SDK 默认的 `jev-latest`。
    intent_jev_enabled: bool = True
    typesafe_api_key: str = ""
    typesafe_base_url: str = ""
    typesafe_model_name: str = (
        os.getenv("TYPESAFE_MODEL_NAME")
        or os.getenv("TYPESAFE_DEFAULT_MODEL")
        or "jev-latest"
    )
    typesafe_timeout_seconds: float = float(os.getenv("TYPESAFE_TIMEOUT_SECONDS", "10"))

    # Service
    host: str = "127.0.0.1"
    port: int = 8000

    # 主智能体图的最大超级步数（LangGraph recursion_limit）。
    # deepagents 给自己绑定的是 9999，但该绑定在 astream_events 路径下不生效，
    # 不显式传入就退化为 LangGraph 默认的 25 —— 主控光探查仓库就能用掉 8 轮，
    # 远不够「探查 → 方案 → 编码 → 推送 → 建 PR → 评论」的完整链路。
    # 注：子智能体各自持有自己的步数预算（同样是 9999），不计入本值。
    agent_recursion_limit: int = int(os.getenv("AGENT_RECURSION_LIMIT", "200"))

    # 空转刹车：同一工具、同一参数在本轮出现到第 N 次就不再执行（回一条警告 ToolMessage），
    # 第 N+1 次直接中止本轮并补一条中断记录。计的是**本轮出现次数**、不要求相邻 ——
    # 2026-09-21 那次 686 秒的打转里 `git status` 与 `git log` 是交错的。0 = 关掉。
    loop_guard_repeats: int = int(os.getenv("LOOP_GUARD_REPEATS", "3"))

    # handoffTask 把内层结果交回外层时的正文上限（字符）。内层是完整的一轮智能体运行，
    # 正文可能上万字，而它在外层只是一个工具返回值 —— 全带回来既撑上下文又没意义，
    # 外层要的是结论（见 ``tools/handoff.py``）。
    handoff_result_max: int = int(os.getenv("HANDOFF_RESULT_MAX", "4000"))

    # 日志：控制台 + 接口日志 + 智能体日志（滚动文件）
    log_level: str = "INFO"
    log_dir: str = "logs"
    log_api_file: str = "api.log"
    log_agent_file: str = "agent.log"
    log_max_bytes: int = 10_000_000  # 单文件上限（约 10MB，超出即滚动）
    log_backup_count: int = 5  # 保留的历史文件数
    # 工具参数 / 委派描述写进日志时的字符上限（超出截断并加省略号）。
    # 调大能看全待办列表与委派描述，但 write_file 这类大参数会明显推高 agent.log 体积。
    log_tool_arg_max: int = 2000
    # 工具返回值写进日志时的字符上限。工具的成败常只体现在返回值里（如记忆写入被拒绝），
    # 不记就无法从日志判断「工具到底成没成」——所以单独留一个上限，比参数上限小得多。
    log_tool_result_max: int = 300

    # Persistence（LangGraph checkpointer + store，PostgreSQL；经 SSH 隧道走本地）
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_db: str = "ai_coding_harness"
    postgres_user: str = "postgres"
    postgres_password: str = ""

    # OpenSandbox
    opensandbox_domain: str = "192.168.72.128:8080"
    opensandbox_api_key: str = os.getenv("OPENSANDBOX_API_KEY", "")
    sandbox_image: str = "opensandbox/code-interpreter:v1.1.0"
    sandbox_root: str = "/ai_coding"
    sandbox_timeout_minutes: int = 60
    opensandbox_request_timeout_seconds: int = 600
    # 沙箱探活缓存：距上次探活成功不足该秒数就跳过真探（0 = 每次调用都真探，更准但更慢）
    sandbox_health_probe_ttl_seconds: float = 5.0
    # 沙箱续期：距上次续期超过该秒数就再续一次（把到期时间设为「现在 + SANDBOX_TIMEOUT_MINUTES」），
    # **0 = 关掉续期**。默认 1800（30 分钟）= 默认 TTL 的一半：活跃会话不会因为「一轮跑太久」
    # 或「用户隔一阵回来」被回收，也就少一次重建（重建会丢容器里非卷的内容）。
    sandbox_renew_interval_seconds: float = 1800

    # 沙箱运行时资产：镜像不再预置 skill / 规则 / 凭据脚本，由 SandboxProvider 在每次
    # 创建与重连时推入（容器重建会丢掉非卷文件，所以重连也要重推）。资产全部集中在仓库
    # 的 `sandbox/` 下 —— 这里三处路径都相对仓库根解析（provider._repo_path）。
    sandbox_assets_dir: str = "sandbox/deploy/sandbox"  # askpass / setup.sh 源目录
    sandbox_local_skills_dir: str = "sandbox/skills"  # skill 源目录
    sandbox_local_policies_dir: str = "sandbox/policies"  # 规则源目录
    sandbox_sbin_dir: str = "/usr/local/sbin"  # askpass / setup.sh 在沙箱内的落点
    # Maven 加速与缓存（2026-09-25）：`sandbox/maven/settings.xml` 里配了阿里云公共仓库镜像，
    # 并把本地仓库固定写到 `/ai_coding/.m2/repository`；下面三项决定这个目录是不是挂在卷上
    # —— 挂上就跨容器重建存活（不然每次构建都要重新下依赖）。
    sandbox_local_maven_dir: str = "sandbox/maven"  # settings.xml 的源目录（相对仓库根）
    sandbox_maven_dir: str = "/ai_coding/maven"  # 推入沙箱后的落点
    sandbox_maven_cache_enabled: bool = True  # 关掉则不挂缓存卷（本地仓库退化成容器内目录）
    # true = 所有会话共用一个缓存卷（跨会话也是热的；要求存储能多机同时挂：Docker 单机、
    # 或 RWX 的 PVC）；false = 按会话各挂一个（独占无并发写，代价是新会话冷启动）。
    sandbox_maven_cache_shared: bool = True
    sandbox_maven_cache_prefix: str = "ai-coding-m2"
    sandbox_maven_cache_mount_path: str = "/ai_coding/.m2"
    # 静态 git 身份（只有 commit 需要，与认证无关）
    sandbox_git_user_name: str = "ai-coding-agent"
    sandbox_git_user_email: str = "agent@example.com"

    # 主智能体回给用户的「输出形态」规则（**仓库根相对**，与上面三处同口径，但
    # **不进沙箱** —— 由 ``prompts.build_main_prompt`` 直接读盘拼进 system prompt）。
    # 只挂主智能体：它管的是「怎么对用户说话」，子智能体不直接与用户对话。
    # 读在构建期，所以改完这个文件**新建会话即生效**，不用重启后端。
    agent_output_style_file: str = "adhd/AGENT.md"

    # 工作区持久化（Volume：容器可换、坏能重挂）
    sandbox_volume_enabled: bool = True
    sandbox_volume_prefix: str = "ai-coding-ws"
    sandbox_volume_delete_on_termination: bool = False
    # 卷后端：pvc（本地命名卷 / K8s PVC）| host（NFS 等共享路径）| ossfs | none
    sandbox_volume_backend: str = "pvc"
    # host 后端：共享存储挂载根（如 NFS 挂载点），每会话用其子目录
    sandbox_volume_host_path: str = ""
    # pvc 后端（K8s 用；Docker 下留空即可）
    sandbox_volume_storage_class: str = ""
    sandbox_volume_storage: str = ""
    sandbox_volume_access_modes: str = ""  # 逗号分隔，如 "ReadWriteMany"
    # ossfs 后端
    sandbox_oss_bucket: str = ""
    sandbox_oss_endpoint: str = ""
    sandbox_oss_version: str = "2.0"
    sandbox_oss_access_key_id: str = ""
    sandbox_oss_access_key_secret: str = ""

    # 注入沙箱的令牌环境变量名（由 setup 脚本消费后写入 .secrets 并 unset）。
    # 与平台**无关**：一个会话只装一个令牌，沙箱侧不必知道它来自 Gitee 还是 GitHub ——
    # 平台差异留在工具层（见 tools/git.py 的 build_git_client）。
    sandbox_git_token_env: str = "GIT_TOKEN"
    # 注入沙箱的 git 认证用户名变量名（Gitee OAuth 用 oauth2、GitHub 用 x-access-token、
    # PAT 场景用真实用户名；默认值由 codeplatform.Platform.default_git_username 给）
    sandbox_git_username_env: str = "GIT_USERNAME"
    # 沙箱创建后由应用经 `execute` 执行的初始化脚本（由 sandbox/deploy/sandbox/setup.sh
    # 推入，读沙箱内 `$GIT_TOKEN` 写 `.secrets`；必须不阻塞，勿含 `exec tail`）
    sandbox_setup_command: str = "/usr/local/sbin/setup.sh"

    # 代码平台（Gitee / GitHub，PR 创建/评论/查询）。owner/repo 与**平台**都由请求的仓库
    # 地址解析（见 codeplatform.Platform.of_url），token 请求优先、此处兜底。
    gitee_access_token: str = ""
    gitee_username: str = ""
    gitee_base_url: str = "https://gitee.com/api/v5"
    gitee_base_branch: str = "master"

    github_access_token: str = ""
    github_username: str = ""
    github_base_url: str = "https://api.github.com"
    github_base_branch: str = "main"

    # Gitee OAuth（登录）
    gitee_oauth_client_id: str = ""
    gitee_oauth_client_secret: str = ""
    gitee_oauth_redirect_uri: str = ""

    # GitHub OAuth（登录）：与 Gitee 那三个同口径，见 api/routes/auth.py。
    # redirect_uri 必须与 GitHub OAuth App 里登记的回调地址**逐字一致**。
    github_oauth_client_id: str = ""
    github_oauth_client_secret: str = ""
    github_oauth_redirect_uri: str = ""

    # 右侧栏「代码」面板：**宿主机自己** clone 一份仓库做只读浏览（不经沙箱）。
    # 为什么要有：沙箱会没（TTL 到期、会话结束、首次创建失败），而「看看这个仓库的代码」
    # 是随时都可能想做的事；本机 clone 一份之后，面板与沙箱彻底解耦（见 repo_cache.py）。
    repo_view_enabled: bool = True
    # 缓存根目录，按 owner/repo 各一份裸仓。**默认落在用户个人目录下**
    # （`~/.ai-coding-harness/repo_cache`），不写进项目目录 —— 那是下载来的第三方代码，
    # 混在项目里既会被误当成项目文件，也会跟着项目被备份/同步走。
    # 给了相对路径就按**当前工作目录**解析（`repo_cache.py` 会把它转成绝对路径）。
    repo_view_dir: str = Field(default_factory=_default_repo_view_dir)
    # git 可执行文件；留空 = 自动探测（PATH + Windows 常见安装位置）
    repo_view_git: str = ""
    # 单条 git 命令的超时（秒）。首次 clone 可能很大，给得比探活宽得多。
    repo_view_timeout_seconds: float = float(os.getenv("REPO_VIEW_TIMEOUT_SECONDS", "300"))
    # 单个文件回给前端的字节上限；超过只回「太大」+ 大小，不把正文塞进 JSON
    repo_view_file_max_bytes: int = int(os.getenv("REPO_VIEW_FILE_MAX_BYTES", "1048576"))

    # Sandbox working-directory layout (paths under sandbox_root)
    sandbox_project_dir: str = "/ai_coding/project"
    sandbox_policies_dir: str = "/ai_coding/policies"
    sandbox_skills_dir: str = "/ai_coding/skills"
    sandbox_output_dir: str = "/ai_coding/output"
    sandbox_review_dir: str = "/ai_coding/review"
    sandbox_tmp_dir: str = "/ai_coding/tmp"

    # Memory routing prefix (files under this path go to StoreBackend)
    memories_prefix: str = "/memories/"

    @property
    def git_askpass_path(self) -> str:
        """沙箱内 askpass 脚本路径（git 经 ``GIT_ASKPASS`` 调用它取令牌）。

        与平台无关：脚本只把「用户名 + ``.secrets/git_token``」交给 git，令牌来自哪家
        平台沙箱不关心。
        """
        return f"{self.sandbox_sbin_dir}/git-askpass"

    def token_for(self, platform: Platform) -> str:
        """该平台配置里的令牌（会话自带的那份优先 —— 由调用方决定用哪个）。"""
        return (
            self.github_access_token
            if platform is Platform.GITHUB
            else self.gitee_access_token
        )

    def username_for(self, platform: Platform) -> str:
        """该平台配置里的 git 用户名（PAT 场景用真实用户名，OAuth 场景留空走默认）。"""
        return (
            self.github_username if platform is Platform.GITHUB else self.gitee_username
        )

    def api_base_for(self, platform: Platform) -> str:
        """该平台的 API 根地址。"""
        return (
            self.github_base_url if platform is Platform.GITHUB else self.gitee_base_url
        )

    def default_branch_for(self, platform: Platform) -> str:
        """该平台常见的默认基准分支：Gitee 多为 ``master``、GitHub 多为 ``main``。

        目前只作为**口径**存在（实际 base 一直由模型给工具的入参决定），放在这里是为了
        将来要读它时只有一个出处。
        """
        return (
            self.github_base_branch
            if platform is Platform.GITHUB
            else self.gitee_base_branch
        )

    @property
    def postgres_url(self) -> str:
        """PostgreSQL 连接串（psycopg 格式）。"""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_async_url(self) -> str:
        """SQLAlchemy 异步连接串（psycopg3 驱动，用于业务库 ORM / Alembic）。"""
        return self.postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)

    @property
    def deepseek_model(self) -> ModelConfig:
        """DeepSeek：编码链路。"""
        return ModelConfig(
            model=self.deepseek_model_name,
            api_key=self.deepseek_api_key,
            base_url=self.deepseek_base_url,
            context_window=self.deepseek_context_window,
        )

    @property
    def kimi_model(self) -> ModelConfig:
        """Kimi：审查链路。温度**必须**是 1 —— 该模型不收其他取值。"""
        return ModelConfig(
            model=self.kimi_model_name,
            api_key=self.kimi_api_key,
            base_url=self.kimi_base_url,
            context_window=self.kimi_context_window,
            temperature=1,
        )

    @property
    def classifier_model(self) -> ModelConfig:
        """意图分类模型：**临时**直连主模型 deepseek（本机 vLLM 分类器没启动时的降级口径）。

        ``INTENT_*`` 三项此刻被忽略、留着当回切路径：恢复分类器后把三行改回
        ``self.intent_model or self.deepseek_model_name`` 那种写法即可。
        """
        return ModelConfig(
            model=self.deepseek_model_name,
            api_key=self.deepseek_api_key,
            base_url=self.deepseek_base_url,
        )

    @property
    def glm_model(self) -> ModelConfig:
        """GLM：出方案。思考是服务端默认，不留开关（``llm.build_model`` 也不转发额外参数）。"""
        return ModelConfig(
            model=self.glm_model_name,
            api_key=self.glm_api_key,
            base_url=self.glm_base_url,
            context_window=self.glm_context_window,
        )

    @property
    def gpt_model(self) -> ModelConfig:
        """GPT：问答与分析。"""
        return ModelConfig(
            model=self.gpt_model_name,
            api_key=self.gpt_api_key,
            base_url=self.gpt_base_url,
            context_window=self.gpt_context_window,
        )

    @property
    def qwen_model(self) -> ModelConfig:
        """Qwen：**当前没有角色取用**（原为多智能体主控，因它在 v3 事件流下丢
        tool_call id 而换成 glm）。配置与 ``.env`` 键保留，换回来只需改路由表一行。
        思考在云端默认开启，同样不留开关。"""
        return ModelConfig(
            model=self.qwen_model_name,
            api_key=self.qwen_api_key,
            base_url=self.qwen_base_url,
            context_window=self.qwen_context_window,
        )


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
