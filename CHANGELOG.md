# 项目修改记录

本文档按时间倒序记录项目的每次改动。约定：

- 每完成一个阶段，在「修改记录」最上方追加一条（最新在最上）。
- 每条记录尽量写清：**改动内容 / 涉及文件 / 关键决策 / 遗留事项**。

## 进度总览

| 模块 | 状态 |
| --- | --- |
| 脚手架 + 配置 | ✅ 完成 |
| sandbox/ 沙箱层 | ✅ 完成 |
| backends/ 后端层 | ✅ 完成 |
| tools/ 工具层 | ✅ 完成 |
| agent/ 智能体核心 | ✅ 完成 |
| session/ 会话管理 | ✅ 完成 |
| memory/ 记忆 | ❌ 不做（StoreBackend + 仓库记忆文件已覆盖） |
| api/ HTTP 接口 | ✅ 完成 |
| safety/ 安全 | ✅ 完成（全局底线 + 按智能体的工具 / 目录边界） |
| observability/ 可观测 | ✅ 完成 |
| tests/ 测试 | ✅ 完成（692 条） |

## 修改记录

### 2026-09-28

#### 平台名不再写死：加 `platform_label()`，7 处运行期文案 + 4 处提示词清零

- **动机**（用户口径）：全仓搜「固定打印 gitee / github」的地方，做一个**取平台名字的工具**，
  那些写死的地方统一改用它。
- **搜索结论**（只读排查，全仓 `rg` 过一遍）：
  - **运行期文案里写死的 7 处**：`agent/middleware/memory.py` 5 处（那条
    `logger.info("仓库记忆已按 Gitee 的实际状态刷新")`，以及三个用户可见 marker ——
    闸门拦下 / 查不到 PR / rework 没有可修对象）；两个客户端的
    「Gitee / GitHub access token 未配置」各 1 处。
    **GitHub 仓库跑这些路，文案一律说「Gitee」——真写错了**，会让人去错的地方找 PR。
  - **提示词 / 分类器里写死的 4 处**：`agent/prompts.py` 2 处、`server/intent.py` 2 处。
  - **1 处兜底域名**：`repo_cache.py` 的 `urlparse(spec.url).netloc or "gitee.com"` ——
    不是打印，但 URL 畸形时会给 GitHub 仓库的认证头挂上 gitee.com 域名，等于**静默失效**。
  - **不动的**：`codeplatform.py` 的 `Platform` / `_HOSTS` / `_LABELS`（唯一的源头）、
    `config.py` 的 base_url、`db/models.py` 的列默认值、`gitee_token` 这类**字段名与列名**
    （改名要动 DB/API，不在本次）、`redact.py` 的令牌前缀、`auth.py` 的端点与路由路径
    （`/auth/gitee/*` 必须叫这个）；以及大量**注释 / docstring** 里的设计说明（按用户口径
    不动 —— 改了是纯噪音）。
- **改动**：
  - `codeplatform.py`（叶子模块，谁都 import 得动）新增 **`platform_label(source)`**：接
    `Platform` / `RepoSpec` / 库里的字符串（`"gitee"`/`"github"`）/ `None`，认不出的取值一律
    按 Gitee（与 `Platform.of` 同口径），**永不抛异常** —— 它服务的都是日志与给用户看的文案，
    那里最不该因为一个畸形取值再炸一次。
  - `memory.py`：`MemoryMiddleware` / `build_memory_middleware` / `refresh_memory` 增加
    `platform` 参数（`build_memory_middleware` 手上本来就有 `repo`，一路往下传），三个 marker
    与那条日志改用 `platform_label`。
  - `tools/pr_client.py` 新增 `missing_token_message(repo)`：两个客户端的「令牌未配置」文案
    由**一处**生成，平台名与两个字段名都从 `repo` 派生 ——
    `<平台小写>_token`（建会话入参）与 `<平台大写>_ACCESS_TOKEN`（.env 兜底键）。
  - `repo_cache.RepoSpec` 增加 `platform` 字段（`from_session` 带上），兜底域名改成
    `spec.platform.host`。
- **关键决策**：
  - **提示词改成中性措辞，而不是按平台渲染**（用户点）：`prompts.py` 的两处改成「按 **PR 的
    实况**」，`intent.py` 的两处写成「Gitee / GitHub」并列。理由：提示词是**静态文本、建会话
    时渲染一次**，中性写法永远正确、也不动渲染链路（按平台渲染要把平台接进
    `build_main_prompt`，收益只是措辞更贴切）。分类器那两处并列两个平台，是为了让用户说
    「把 GitHub 上的 PR 补进记忆」时也能命中。
  - `platform_label` 放在 `codeplatform.py`：它是**叶子模块**（`config` / `session` / `tools`
    都依赖它、它不依赖任何人），不会成环。
- **验证**：
  - 新增 **6 条**用例：`test_memory_gate.py` 3 条（闸门 / 盲区 / rework 无可修对象三条文案
    各自验「GitHub 仓库说 GitHub、且不出现 Gitee」，两条平台都跑）、`test_codeplatform.py`
    2 条（`platform_label` 认各种输入且不抛；`missing_token_message` 的平台名与字段名）、
    `test_repo_cache.py` 1 条（认证头的兜底域名跟着平台走）。
  - 全量 `pytest` **1052 passed**；`ruff check` 改动文件干净；提示词动过，故
    `scripts/check_prompts.py` **冲突扫描 0 条**。
  - 顺带确认 `sandbox/` 那批资产（skills / policies / setup.sh / git-askpass）本来就是
    「Gitee / GitHub」并列或平台无关的写法，不用改。
- **遗留**：注释与 docstring 里仍写着 Gitee 的地方按约定**不动**（那是设计说明）；
  `gitee_token` / `gitee_username` 这类**字段名与 DB 列名**仍带平台前缀（历史命名，
  改名要动 API 与迁移，本次不做）。

#### GitHub OAuth 登录（后端）：与 Gitee 对称的两个路由，顺带给两边都加上 `state`

- **动机**（用户口径）：GitHub 也要能登录；范围点：**只做后端这一半（A）**，前端登录入口
  先不做、**记成遗留项**；其余未点的按之前的推荐来（→ 加 `state`、GitHub scope 用 `repo`）。
- **改动**：
  - `api/routes/auth.py`：原来只有 Gitee 两个路由，且是「逐字一份」的写法。改成一份
    `_OAuthApp` 描述（端点常量 + 三件套）承载公共逻辑，四个薄入口各一行 —— 加平台不必
    再复制一遍拼参/换码/错误归一。新增 `/auth/github/authorize`、`/auth/github/callback`。
  - **`state` 两边都加**（防 CSRF）：`<时间戳>.<随机串>.<HMAC>`，**自签、不落存储**。
    不存服务端是权衡过的：多个 worker 之间没有共享内存，而 Redis 是可选件 —— 自签的
    state 无状态、跨进程跨重启都能验，代价只是多一个 HMAC。签名密钥用**该平台的
    `client_secret`**，于是 Gitee 签发的 state 在 GitHub 回调上也过不了，不必再加「这份
    state 属于哪个平台」的字段。有效期 10 分钟；校验失败**统一回一句 400**（不区分签名错 /
    过期 / 格式错 —— 分开说等于给探测的人送情报），而且**先验 state 再换码**，不把可疑的
    授权码外发。
  - `config.py` 加 `github_oauth_client_id` / `_secret` / `_redirect_uri`；`.env.example`
    补三行，并把「怎么建 GitHub OAuth App、回调地址必须一字不差、开发期建议填
    `http://localhost:5173/auth/github/callback`（vite 已把 `/auth` 代理到后端）」写在注释里。
  - `README.md`：API 表补上 GitHub 两个路由与回调参数；「GitHub 目前只支持
    `GITHUB_ACCESS_TOKEN`」那句改成实况（两个平台后端都就绪、**前端还没接**）。
- **关键决策**：
  - **GitHub 换 token 必须显式带 `Accept: application/json`**，否则回的是
    `application/x-www-form-urlencoded` 文本；而且授权码无效时它**回 200**、错误在 body 里
    （`{"error": "bad_verification_code"}`）—— **只看状态码会拿着 `access_token=None`
    往下走**。两条都单独处理，并各有用例钉着。响应不是 JSON 时回 502 且话里点明原因。
  - 两个平台的返回体**同一个形状**（`access_token` / `scope` / `expires_in`），前端不必按
    平台分支；GitHub 不回 `expires_in`，那里就是 `None`。
  - **`authorize` 也要求配齐 `client_secret`**（原来只要求 client_id + redirect_uri）：
    没有 secret 就签不出 state，跳出去也只会卡在回调那一步。这是本次唯一的**行为变更**，
    而现实中没有任何调用方（前端一直没接），所以没有兼容问题。
- **验证**：
  - 新增 `tests/test_auth_routes.py`（**14 例**，全程离线：路由里建客户端的那一步留了可替换
    的工厂，用例挂 `httpx.MockTransport` 截获）：两个平台的 authorize 跳转（地址 / scope /
    response_type / redirect_uri / state 都在）、三件套没配时的 500 措辞、GitHub 换码
    （断言请求头要了 JSON、表单字段齐全）、Gitee 不额外要 JSON、**200 带 error 当失败**、
    非 JSON 回 502、端点 4xx 透传、伪造 state / 跨平台 state / 过期 state 一律 400 且
    **不外发请求**、每次 authorize 的 state 都不同。
  - 全量 `pytest` **1046 passed**；`ruff check` 改动文件干净。
  - 走真实 `create_app` 确认四条 `/auth/*` 都挂上了（整站 lifespan 要连 Postgres，离线机器
    上跑不起来，所以只做到挂载这一层）。
- **遗留**：
  - **前端登录入口没做（本次明确不做）**：两个平台都没有按钮、没有 token 存储、建会话时
    也没带 `gitee_token` / `github_token`。所以**眼下实际生效的还是 `.env` 里的
    `GITEE_ACCESS_TOKEN` / `GITHUB_ACCESS_TOKEN`（兜底那份）**，OAuth 这条路目前只有
    curl 能走通。接前端时要一起定的两件事：token 存哪（`localStorage` / 只存内存）、
    以及回调页形态（现在回调直接回 JSON，弹窗里看不懂，得改成一段会
    `window.opener.postMessage` 的极小 HTML）。
  - **生产（Linux）的 `redirect_uri` 怎么填还没定**：后端**不托管前端**（没有 StaticFiles
    挂载），所以要么 nginx 反代 `/auth`、要么把 redirect 指到后端 8000 并处理跨源。
    开发期按 `.env.example` 里写的 `http://localhost:5173/auth/github/callback` 走。
  - GitHub 侧要用户自己建 OAuth App 并把凭据填进 `.env`，回调地址必须与
    `GITHUB_OAUTH_REDIRECT_URI` **逐字一致**（否则 GitHub 回 `redirect_uri_mismatch`）。
  - `state` 目前**没有绑用户会话**（自签即验、一次性靠过期）。等前端接登录、有了会话概念
    之后可以再收紧成「与本次会话的 state 一一对应」——现在的形态防的是「别人构造回调 URL
    骗你点」，跨站伪造那一步已经挡住了。

#### Redis 旁路状态：降级告警落盘 + 修掉「边写边读」时的帧号错位

- **动机**（用户报障）：控制台总是出现「Redis 上的 运行缓冲 不可用，本进程后续退回进程内
  实现」，但 Redis 明明连得上；想在日志里查，又**一个字都搜不到**。
- **排查结论**（只读 + 真机实测，脚本留在 `_bench/probe_redis_*.py`）：
  - 这句话有两个版本：启动期 `build_redis` 的「Redis 不可用（addr）：…；本次退回进程内实现」
    与运行期 `RedisBacked.run` 的「Redis 上的 <what> 不可用…」。**"运行缓冲" 两处都出现**，
    用户引的句子是两者的混合。
  - 落不了盘的原因在 `observability/logging.py`：文件 handler 按 **logger 名前缀**分流，
    `ai_coding_harness.redis_cache` 既不在 api 前缀也不在 agent 前缀里 —— 它只走控制台，
    窗口一关证据就没了。两个日志文件全文搜过，确实一条都没有。
  - 「连得上却降级」的真相是**认证**：本机 Redis 要 AUTH，不带密码的 `ping` 报
    `AuthenticationError: HELLO must be called with the client already authenticated…`。
    实测 ①不发 AUTH → 失败 ②带 `.env` 里的密码 → 成功 ③错密码 → 失败。
    `.env` 是进程启动时读一次，**补了 `REDIS_PASSWORD` 之后没重启**的后端会一直用旧那份。
- **改动**：
  - `observability/logging.py`：`_AGENT_PREFIXES` 补上 `ai_coding_harness.redis_cache` ——
    运行缓冲 / 熔断状态的连接与降级告警从此进 `agent.log`（不进 api.log）。
  - `server/run_store.py`：**帧的「取号 + 入列」改成一次事务**（`INCR`+`RPUSH`+`LTRIM`+
    两条 `EXPIRE` 走一个 `pipeline(transaction=True)`），**读侧的「长度 + 号」也放进一个事务**。
    并给 `_open_redis`（清旧帧 + 置号）加了事务，避免订阅者撞进「帧没了、号也没了」而被
    误判成「本轮已结束」。
- **关键决策**：这是**真 bug，不是告警的副作用**。读侧靠 `首帧号 = 号 - 长度 + 1` 反推，
  写侧两条命令之间那道缝（或读侧两条读之间夹进一次完整写入）都会让首帧号偏一号，游标跟着
  跳过去、**尾巴上的帧被丢掉**。真机实测过：订阅者拿到 `(2, 首帧)、(3, 次帧)`，`done` 帧不见了。
  - 没引 Lua：`MULTI/EXEC` 就能把「读到的两个值属于同一瞬间」这件事解决，少一层复杂度。
  - **已知窄缝（写在 `_read_redis` 的 docstring 里）**：`LRANGE` 仍在事务外（它是追加语义，
    晚读只会多拿新帧）。若正好在这次读之间触发截断（要一轮攒够 `RUN_BUFFER_MAX_FRAMES`，
    默认 2000 帧），首帧号会算高、下一轮轮询补齐 —— 截断与入列同在写事务里，窗口是微秒级，
    按当前用法不值当再引 Lua。
- **验证**：
  - 先写回归用例、**确认它在旧代码上失败**：`test_frame_numbers_survive_reading_while_a_frame_is_being_written`
    报 `[(2,'0'),(3,'1'),(4,'3'),(5,'4'),(6,'5')]` —— 帧号整体偏一，`'2'` 那帧真丢了。
  - 改完再跑：该用例通过；真机探针（`_bench/probe_redis_runhub.py`，连 127.0.0.1:6379）
    订阅期间拿到 **(1,2,3) 完整且编号正确**，`degraded=False`。
  - `tests/fake_redis.py` 补了两样语义：`round_trip=True`（每条命令让出一次事件循环，
    模拟网络往返 —— 不加这个，命令之间根本没有交错点，这类 bug 在测试里复现不出来）与
    `pipeline()`（命令排队、`execute()` 一口气跑完，模拟 `MULTI/EXEC` 的原子性）。
  - `tests/test_logging_pipeline.py` 新增 2 例：降级告警**必须**进 `agent.log`、**不许**进 `api.log`。
  - 全量 `pytest` **1032 passed**；`ruff check` 改动文件干净。
- **遗留**：
  - `.env` 里 `REDIS_PASSWORD` 已配好，**重启后端**才会生效；启动时若还看到那句告警，
    现在可以直接翻 `logs/agent.log` 看 `：` 后面的异常名与文本。
  - 顺手记一条相关的坑：`Settings` 的 `env_file=".env"` 是**相对进程 CWD** 解析的，
    所以后端必须从仓库根启动（`start.bat` / `start.sh` 都 `cd` 过去了）；换成从别处启动的
    部署方式（systemd 之类）要显式指定工作目录，否则 `.env` 读不到、Redis 会**静默地**不启用
    （那条路径连告警都不会打）。

#### 回退：整套「项目配置」（会话那份 + Nacos 远端 + 配置闸门）撤掉，改记遗留项

- **动机**（用户口径）：这套配置链路先不做了 —— 「配置这个做成遗留事项吧，先把本次和配置
  有关的代码回退掉」。范围点：① 三层一起撤；② 加一条反向迁移把三列 drop 掉；③ Go / Node
  的依赖源与缓存**也一并撤**，回退之后**单独重做**（用户原话：「回退之后的单独做 go/node 这部分」）。
- **撤掉的东西**：
  - **注入机制**：删 `sandbox/project_env.py`；`SandboxProvider` 去掉 `project_env` 参数与
    `_install_project_env`；`OpenSandboxProxy` / `OpenSandboxBackend.aexecute` 去掉
    「每条命令前 source `/ai_coding/.sandbox.env`」；`safety` 去掉该文件的读写保护；`Settings`
    去掉 `sandbox_env_path`。
  - **远端配置（Nacos）**：删 `sandbox/nacos.py`；`Settings` 去掉 `config_nacos_*`；
    `.env.example` 去掉那一段。
  - **会话字段与接口**：`sessions` 去掉 `sandbox_env` / `config_source` / `config_confirmed_at`；
    删 `dao.set_session_config` / `find_last_sandbox_env` / `find_last_config_source` /
    `repo_has_confirmed_config`；`Session` 去掉三个字段；`CreateSessionRequest` 去掉两个字段；
    `SessionDetail` 去掉 `sandbox_env_keys` / `remote_config`；删 `GET /sessions/sandbox-env`
    与 `PATCH /sessions/{id}/sandbox-env`（连同 `SandboxEnvHint` / `UpdateSandboxEnvRequest`）。
  - **配置闸门**：删 `sandbox/config_keys.py` 与 `server/service.py` 里的
    `CONFIG_GATE_*` / `_pending_config_request` / `_config_requirements` / `_scan_config_keys` /
    `_config_gate_message`；`need_config` 帧不再下发。
  - **前端**：新建对话页去掉「项目配置」文本域与 Nacos 四格；删配置弹框与 `need_config` 分支；
    `api.ts` 去掉 `createSession` 的两个可选参数、`getSandboxEnvKeys`、`updateSandboxEnv`、`ConfigItem`。
  - **Go / Node**：`Settings` 去掉 `sandbox_go_*` / `sandbox_npm_*`；`volumes.py` 去掉
    `build_go_cache_volume` / `build_npm_cache_volume`；`_runtime_inputs` 不再挂这两个卷。
- **保留的东西**：Maven 那一整套（`settings.xml` 阿里云镜像 + `.m2` 缓存卷 + setup.sh 摆位）
  原样保留；GitHub 支持、右侧栏代码面板、会话复用、记忆闸门等与本次无关的改动一律不动。
- **关键决策**：
  - **三条老迁移文件不删**（`d1e2b3c4a5f6` / `e2f3a4b5c6d7` / `f3a4b5c6d7e8`）：库里的
    `alembic_version` 已经指向它们，删文件会让 `alembic upgrade` 直接报错。改为**在上面加一条
    反向迁移** `b5c6d7e8f9a0`（drop 三列，downgrade 加回来；列能回来、**值回不来**）。
  - **本仓库没有 git**（`.git` 不存在），回退是手工改的 —— 动手前把待删的 8 个文件
    （3 个模块 + 1 份文档 + 4 个测试）备份到了工作区外
    `…\visualizations\…\_config-revert-backup\`，以防要捡回来。
  - `dataclasses` 的 `field` / `db.dao` 的 `Any` / `service` 的 `REWORK_INTENT` 等**因此变成未使用**
    的导入一并清掉（`REWORK_INTENT` 自身还在，`middleware/memory.py` 仍用它）。
- **验证**：
  - 全量 `pytest` **1029 passed**（撤掉 4 个测试文件、适配 8 个：卷、代理、provider、会话复用、
    记忆闸门、脱敏、workspace runtime）。
  - `ruff check` 改动文件：只剩 `db/models.py` 的 4 条**既有**告警（`__table_args__` 与引号注解），
    本次没新增。
  - `npm --prefix frontend run build`（含 `vue-tsc`）通过；`scripts/check_prompts.py` 冲突扫描 0 条。
  - 全仓再扫一遍 `sandbox_env` / `nacos` / `project_env` / `need_config` / `conda` 这些关键字，
    只剩 alembic 迁移文件按设计提到列名。
- **遗留**：
  - **项目配置（含远端配置中心与配置闸门）整体待重做**：这次的做法是「会话上存值 + 可选 Nacos
    拉一份 + 拼成 `.sandbox.env` 每命令 source + 没配过就拦」；重做时按新设计重新加列，不复活
    这三列。注意用户此前明确过的两条口径仍然成立：**配置由用户提供、平台不代为保管文件**；
    真要做远端，优先**配置中心**而不是往代码仓里塞文件。
  - **Go / Node 的依赖源与缓存也待重做**：原来是 `GOPROXY` / `npm_config_registry` + go/npm
    缓存卷，走的是 `.sandbox.env` 这套通道，跟着一起撤了。重做时建议直接落成**容器环境变量**
    （它们是静态值，不像用户配置那样会变），缓存卷仍照 Maven 的 `_cache_volume_name` 口径写。
  - 需要 `alembic upgrade head`（到 `b5c6d7e8f9a0`）与**重启后端**才生效。用户 `.env` 里残留的
    `CONFIG_NACOS_*` 因 `Settings` 是 `extra="ignore"` 而无害，可自行删掉。

#### 配置弹框保存 500 修复：`remote_config` 声明窄成 `dict[str, str]`，带 `keys` 列表就校验失败

> ⚠️ **本条随下面的配置回退一并撤销**（`remote_config` 连字段一起删了）。留在这里是记录当时
> 的排查结论：PATCH 的值已写库成功，炸在「回详情」那一步的 Pydantic 校验上。

- **动机**（用户报障）：配置闸门弹框里填完信息点保存，后端 **500**。
- **定位**（只读排查，`logs/api.log`）：`PATCH /sessions/{id}/sandbox-env` 的**值已经写库成功**
  （上一行日志「会话配置已更新…值 6 项」），炸在末尾「回详情」那一步 ——
  `sessions.py:126` 构造 `SessionDetail(remote_config=manager.remote_config_status(...))` 时
  Pydantic 报 `remote_config.keys: Input should be a valid string, input_value=[], input_type=list`。
  即 `api/schemas.py` 把 `remote_config` 声明成 `dict[str, str]`，而产出方
  `session/manager.py::remote_config_status()` 里 `keys` 是**键名列表**。用户看到的是「保存失败」，
  实际已经存进去了。
- **改动**：
  - `api/schemas.py`：`SessionDetail.remote_config` → `dict[str, Any] | None`（`keys` 是列表，
    `error` 可缺省），注释补上 `keys` 的含义。
  - `frontend/src/api.ts`：同一字段 → `Record<string, unknown> | null`（`App.vue` 只读 `.status`
    / `.error`，放宽不影响）。
- **关键决策**：**放宽响应模型**而不是把 `keys` 从状态里删掉 —— 配置弹框要拿它标
  「Nacos 里已经有这些」，删了就少了这条信息；值仍然不回显。
- **验证**：
  - 新增回归用例 `tests/test_config_gate.py::test_patch_survives_remote_status_with_keys_list`
    （远端状态带 `keys` 列表时 PATCH 仍 200、键名原样返回、值不回显）。**此前 9 例全绿是因为
    `FakeManager` 一律返回 `None`，走不到带 `keys` 的那条路**。
  - 全量 `pytest` **1070 passed**；`ruff` 改动文件干净；`npm --prefix frontend run build` 通过。
- **遗留**：无（需 `alembic upgrade head` 与重启后端才生效）。

#### 配置闸门：coding 时没配置过就拦下弹框，展示「要配哪些」并自动重发

> ⚠️ **本条已随上面的配置回退整体撤销**（闸门、弹框、`PATCH /sandbox-env` 都已删除）。
> 留在这里是记录当时的口径与判据，重做时可直接参考。

- **动机**（用户口径）：配置内容要让用户知道 —— Nacos 这些"**需要 coding 的时候**给用户
  **弹框配置**，并**展示要配置的内容**"。
- **口径**（用户点）：① 会话没配置过就拦；② **后端拦**；③ 保存后**自动重发**；④ 标记**来源**。
- **改动**：
  - `server/service.py`：`chat()` 在运行时就绪后、派发之前加**配置闸门**。intent ∈
    {`coding`, `rework`} 且「会话没填过值 ∧ 远端没拉到 ∧ 这个仓库从没确认过」→ 拦下本轮：
    记一条 assistant 记录 → 先发说明文本（`CONFIG_GATE_MARKER` 开头）→ 再发
    `{"type": "need_config", "config": [...]}` → 结束本轮（与记忆闸门同一套"停下并说清"）。
  - 新增 `sandbox/config_keys.py`：**只读**扫描命令（grep/find，扫 `application*.yml` 的
    `${VAR}`/`${VAR:默认值}` 与 `.env.example` 类模板）+ 解析成清单（**没有默认值 = 必填**、
    `.env.example` 里空值 = 必填、同一个键取严的那条）。
  - 弹框清单 = 扫到的模板键 + 标记「必填 / 远端已有 / 已设置」（**只有键名与出处，没有值**）。
  - `sessions` 加 `config_confirmed_at`（alembic `f3a4b5c6d7e8`）：用户填过值或点过
    「不需要配置」都盖章；**按仓库继承**（`dao.repo_has_confirmed_config`）—— 同一仓库的
    后续会话不再被弹，避免每开一个会话问一次。
  - 新增 `PATCH /sessions/{id}/sandbox-env`：会话内合并保存值 / 远端位置（`confirm_only=true`
    表示"这个项目不需要配置"），保存后**内存里那份会话对象同步更新**（下一轮闸门与注入直接读它）。
  - 前端：`need_config` 事件 → 弹框（逐项：键名 / 出处 / 必填标记 / 远端已有标记；口令类
    输入框按密码显示）+ 「补充其他键」文本框 + 「不需要配置 / 稍后 / 保存并继续」；
    保存后**自动重发**刚才那条消息（`send()` 里记下原文）。
- **关键决策**：
  - 「配置过」= 会话上有值 ∨ 远端拉到 ∨ 该仓库确认过（含"不需要配置"）：一次确认是对
    **这个仓库**的表态，不必每会话重问。
  - 清单来源优先**扫项目模板**（唯一可信来源），扫不到就空表 + 手填框；
    **扫模板失败不影响拦不拦**（判据是"配置过没有"）。
  - 值仍然**不回显、不进仓库**；`config_confirmed_at` 只记时刻、不记内容。
- **验证**：
  - 新增 `tests/test_config_gate.py`（9 例：模板解析与必填判定、扫描命令只读且安全引用目录名、
    四种"已配置过"不拦、缺配置时清单带标记、扫描失败仍拦、闸门文案、PATCH 合并 /
    confirm_only / 值不回显）。
  - 新增 `tests/conftest.py`：测试**统一关掉远端配置中心**（开发者本机 `.env` 开了 Nacos 时，
    provisioning 会真去连一次，把 20ms 的用例拖挂 —— 实测踩到过）。
  - 全量 `pytest` **1069 passed**；`ruff` 改动文件干净；`npm --prefix frontend run build` 通过。
  - ⚠️ 三条迁移要一起跑：`d1e2b3c4a5f6`（会话上的项目配置）、`e2f3a4b5c6d7`（远端位置）、
    `f3a4b5c6d7e8`（确认时刻）。
- **遗留**：
  - 会话内**手动**改配置的 UI 入口还没做（`PATCH` 已就绪）—— 现在两个入口是"新建对话时填"
    与"闸门弹框"；已确认过"不需要配置"的仓库想再配，暂时只能新建会话。
  - 弹框清单依赖沙箱里的工作区（扫模板要它）；工作区没建好时清单为空，只能手填或点"不需要配置"。

### 2026-09-27

#### 远端配置中心（Nacos）：平台按会话去拉，与「会话上那份」合并后注入沙箱

- **动机**（用户口径）：项目配置的归宿最好是**远端配置中心**，平台去拉；选型定 **Nacos**，
  并要求确认 Python / Go / JS-TS 是否支持、以及账号密码怎么让用户告知。
- **结论（先确认，再动手）**：
  - **语言不构成障碍**：平台拉回来注入的是**环境变量**（Java `${DB_HOST:…}`、Python `.env`/`os.environ`、
    Go `os.Getenv`、Node/TS `process.env` 都吃得到），所以不需要各语言都有 Nacos 客户端 ——
    只有"让被测项目自己去连 Nacos"那种用法才吃客户端，而 Python / Node 的客户端成熟度确实参差。
  - **平台侧不引 SDK，走 HTTP OpenAPI**：平台的活只是"按位置拉一份文本"，少一个依赖、少一个版本坑。
  - **凭据分两类**（用户口径：混合）：Nacos **账号密码由平台 `.env` 配（只读账号）**，
    用户只告知"配置放哪"（地址 / 命名空间 / 分组 / dataId，都可留空用平台推的默认）。
- **实测（用户的 Nacos 是 3.x，`/nacos/` 回 "Console default port is 8081"）**：
  - `GET /nacos/v1/cs/configs`、`/nacos/v2/cs/config` → **404 `No endpoint`**（3.x 里已不存在）；
  - `GET /nacos/v3/client/cs/config` → **200**，可**匿名**读；远端没有这份时回
    `{"code":20004,"message":"resource not found"}`；
  - `GET /nacos/v3/admin/cs/config` → 403（admin 接口要账号）；客户端接口**只读**（POST/DELETE 405）；
  - 登录入口：3.x `/nacos/v3/auth/user/login`（403=账号不对）、2.x `/nacos/v1/auth/users/login`、
    1.x `/nacos/v1/auth/login` 都还在。
  - 综上：**实现按「v3 优先、v1 兜底」**；多数 3.x 部署读配置**根本不需要账号**，账号只在
    "客户端读也被锁"时才用得上。
- **改动**：
  - 新增 `sandbox/nacos.py`：`NacosSource`（位置，不含凭据）、`resolve_source()`（平台按会话推
    默认、会话可覆盖）、`fetch_config()`（v3 优先 → v1 兜底；`20004`/404 = 远端确实没这份 → 空；
    401/403/5xx/连不上 = 失败抛 `NacosConfigError`）、`clean_config_source()`（只认四个字段，多写报错）、
    登录按 v3 → v1 依次尝试（**只有 404 才换下一个**，凭据被拒当场报错）。
  - `config.py` + `.env.example`：`CONFIG_NACOS_ENABLED / _ADDR / _USERNAME / _PASSWORD / _DATA_ID / _TIMEOUT_SECONDS`
    （默认关闭；**ADDR 是 API 端口 8848，不是 3.x 控制台的 8081**）。
  - `sessions` 加一列 `config_source`（JSONB，位置四个字段；alembic `e2f3a4b5c6d7`）；
    同仓库下一次建会话沿用上次那份（`dao.find_last_config_source`）。
  - `session/manager._compose_project_env()`：**平台默认 → Nacos → 会话上那份**（后者覆盖前者）；
    拉取失败只跳过远端那份并把原因记进 `remote_config_status`（`SessionDetail.remote_config` 可见，
    前端在状态条上提示一句）。
  - API：`POST /sessions` 收 `config_source`（多余字段 400）；`SessionDetail` 回 `remote_config`。
  - 前端：新建对话加「远端配置中心（可选）」四个输入（地址/命名空间/分组/dataId），
    placeholder 按仓库地址实时推默认值（`gitee__owner` / `repo` / `sandbox-env`），全留空即平台默认。
  - 文档：`docs/project-env-keys.md` 增「放到远端配置中心（Nacos）」一节（命名约定、控制台怎么建、
    环境靠命名空间、优先级与失败纪律）；README 增远端说明。
- **关键决策**：
  - 位置按会话推（`namespace=<平台>__<owner>`、`group=<repo>`、`dataId=sandbox-env`），
    带平台前缀是为了同名 owner 在 Gitee/GitHub 上不撞车；要分环境就换命名空间。
  - 账号只在平台 `.env`（只读），**不进会话、不进沙箱、不回显**；用户填的那四个位置字段本身不含凭据。
  - 失败纪律与 PR 查询一致：**拉不到 ≠ 远端没有**，宁可吵也不静默降级。
- **验证**：
  - 新增 `tests/test_nacos.py`（13 例）：位置推导与覆盖格式、禁用即整条跳过、字段校验、
    v3 读成功并解析、`20004` 视为"远端没这份"、v3 404 → v1 兜底、两个版本都缺 = 空、
    登录取 token 且口令只走表单体、登录被拒即报错（不跨版本掩盖）、连不上/5xx 必抛错、读被拒带凭据提示。
  - **真机验证**（用户的 Nacos 3.x，经 ssh 隧道 `127.0.0.1:8848`）：平台推的位置正是
    `ns=gitee__zzpfordevelopment group=wanqi-mall dataId=sandbox-env`；会话覆盖命名空间生效；
    远端没有这份时返回 **0 个键**（而不是报错）。"远端有这份"的路径等控制台建好配置后再跑一次即可。
  - 全量 `pytest` **1060 passed**；`ruff` 改动文件干净；`npm --prefix frontend run build` 通过。
  - ⚠️ 要跑 `alembic upgrade head`（`d1e2b3c4a5f6` + `e2f3a4b5c6d7`）后端才能建会话。
- **遗留**：
  - 远端那份的**解析结果没有缓存**：Nacos 挂了这一轮就只用另外两份（日志与详情里会写明）。
    要不要"缓存上次拉到的内容、挂了也能起"待定。
  - 平台读的是**已发布的配置**；Nacos 的配置变更通知（长轮询 / 推送）没接 —— 现有链路是
    "建会话 / 重连沙箱时拉一次"，够用。
  - 之前记的「远端配置中心」优化点由本条落地；仍待办的是"把配置**加密**托管"（库里那份仍是明文，
    与 token 同一口径）。

#### 项目配置改由**用户提供**并存在会话上（平台不保管文件）+ 记「远端配置中心」优化点

- **动机**（用户口径，连着三条）：① 那份配置**不能放平台代码仓库**；② 它是**用户的**测试库与
  测试数据，**不能由平台维护**；③ 理想形态是放**远端**、平台去拉，但眼下没有远端服务 ——
  先按「用户填、随会话走」实现，远端那条记成优化点。
- **改动**：
  - **数据**：`sessions` 加一列 `sandbox_env`（JSONB，可空；明文，与 token 同一口径），
    alembic `d1e2b3c4a5f6`（`server_default` 不涉及，老会话零迁移）。
  - **API**：`POST /sessions` 增 `sandbox_env`（用户填的那份）；新增
    `GET /sessions/sandbox-env?repo_url=…` 只回**键名**（供「新建对话」提示"上次填过哪些键"）；
    `SessionDetail` 增 `sandbox_env_keys`（同样只回键名）。键名不合法直接 400。
  - **沿用上次**：建会话时用 `dao.find_last_sandbox_env(owner, repo)` 取该仓库**上一次**那份打底，
    用户这次填的覆盖它 —— 口令这类不必每次重打；值只在服务端用，**不回显给前端**。
  - **沙箱注入**：`compose_env(settings, user_env)` = 平台默认（Go/npm 源与缓存路径） + 用户那份
    （后者覆盖前者）；`session.manager._provision` 从会话上取，provider/proxy 照旧一路带到
    容器里那份 `/ai_coding/.sandbox.env`。空值**丢弃**（不注入成空串）——
    `${DB_HOST:127.0.0.1}` 这类占位符只在变量根本不存在时才用默认值。
  - **前端**：新建对话加「项目配置（可选）」输入框（一行一条 `KEY=value`）+ 上次键名提示；
    地址填完 `@blur` 拉一次提示。
  - **删掉上一版的设计**：宿主机 `.env.sandbox` / `.env.sandbox.d/<owner>__<repo>.env`
    与 `SANDBOX_ENV_FILE` / `SANDBOX_ENV_DIR` 全部移除（`.gitignore` 里那两条也撤），
    `.env.sandbox.example` 挪成 `docs/project-env-keys.md`（**只讲键名与怎么捞**，不含值）。
- **关键决策**：
  - 归属：值由用户提供、存在会话上；平台只做「校验 → 合成 → 送进沙箱」，不保管宿主机文件。
  - 沿用而不是回显：同仓库上次那份在**服务端**打底（用户只填要改的），避免口令进前端。
  - 空值即"不注入"（也即"沿用上次"）：把"用项目默认值"这条留给项目自己。
  - 键名非法一律 400：静默丢弃会把用户的笔误变成"配了但没生效"。
- **验证**：
  - 新增 `tests/test_project_env.py`（10 例：键名校验与空值语义、单引号转义、平台默认为 Go/npm、
    用户覆盖平台、**用 Git 自带 bash 真跑**生成的片段、`aexecute` 包装与"检查跑在原命令上"）
    与 `tests/test_sandbox_env_route.py`（3 例：**提示只回键名、口令不出现**、空结果、非法地址 400）；
    `tests/test_session_reuse.py` 的假件补上 `sandbox_env` / `find_last_sandbox_env`。
  - 全量 `pytest` **1047 passed**；`ruff` 改动文件干净；`npm --prefix frontend run build` 通过。
  - ⚠️ **要跑 `alembic upgrade head`**（`d1e2b3c4a5f6`）后端才能建会话 —— 模型里有这一列。
- **遗留 / 优化点**：
  - **远端配置中心**（用户点名记下）：理想形态是把用户的测试环境清单放远端、平台按仓库去拉 ——
    多台部署共用一份、不用逐台填。眼下没有远端服务，先落在会话上；将来接上远端时，
    只要把 `compose_env` 的"用户那份"换成拉取结果即可，注入链路不用动。
  - 库里的这份是明文（与 token 同一口径）；要加密/托管到密钥服务一并算远端那条。
  - **会话内改配置**没做（本期只做"建会话时填"）：要改就新建会话（会自动沿用上次那份）。

#### 沙箱支持 Go / Node 项目：国内源 + 依赖缓存卷 + 项目配置按环境变量注入

- **动机**（用户两条口径）：① 被测项目不止 Python / Java，**Go 与 JS/TS 也要能在沙箱里顺畅构建、
  测试** —— 镜像自带 Go 1.25.5 / Node 22，但每次构建都从国外源拉依赖（与当初 Maven 那个问题同源）；
  ② 项目配置要**按 example 建好后以环境变量注入沙箱**，且对四种语言都成立（Python 用 `.env`、
  Java 用 `application.yml` 的 `${DB_HOST:…}`、Go 用 `os.Getenv`、Node/TS 用 `process.env`）。
- **改动**：
  - **依赖源与缓存**（与 Maven 那套对称）：`config.py` 新增 `SANDBOX_GO_PROXY`（`goproxy.cn`）、
    `SANDBOX_NPM_REGISTRY`（`registry.npmmirror.com`）与 Go / npm 各自的
    `*_CACHE_ENABLED / SHARED / PREFIX / MOUNT_PATH`；`sandbox/volumes.py` 抽出
    `_cache_volume_name` 并新增 `build_go_cache_volume`（挂 `/ai_coding/go`）、
    `build_npm_cache_volume`（挂 `/ai_coding/.npm`），默认跨会话共享；
    `session/manager._runtime_inputs` 一并挂上 —— 现在建沙箱挂四个卷：`project` + `.m2` + `go` + `.npm`。
  - **项目配置注入（口径 C）**：新增 `sandbox/project_env.py`（解析 `.env` 风格文本 →
    `platform_env()` 给 Go/npm 源与缓存路径 → 与宿主机 `.env.sandbox` 合成 `compose_env()`
    （**后者覆盖前者**）→ `render_source_file()` 渲染成 `export K='v'`）；
    `SandboxProvider._install_project_env()` 在 create 与 reconnect 都写 `/ai_coding/.sandbox.env`（0400）；
    `OpenSandboxBackend.aexecute()` 在**每条命令前** `. <该文件> 2>/dev/null || true;`
    —— 权限检查仍在**原命令**上做，前缀在检查之后才加。
  - **安全边界**：`/ai_coding/.sandbox.env` 进 `PROTECTED_READ` / `PROTECTED_WRITE`，
    命令层加 `\.sandbox\.env` 拒绝规则（模型自己去 `cat` 会被拦，且不会发往沙箱）。
  - 资产与文档：新增 `.env.sandbox.example`（**含「怎么填」的四步说明** + 按 Java / Python / Go /
    Node 分段）、`.gitignore` 加 `.env.sandbox` 与 `.env.sandbox.d/`、`.env.example` 补
    `SANDBOX_ENV_*` 与 Go/npm 项；`docs/workspace-directories.md` 与 `docs/sandbox-image-guide.md`
    同步（新目录、缓存卷、凭据流程）；提示词通用规则加一条（配置由平台注入、四种语言怎么读、
    不要自己造配置文件、不要把值写进仓库、依赖源已配好）。
  - **多仓库怎么分**（用户口径 a）：项目配置两层 —— 全局 `.env.sandbox` 打底，
    `.env.sandbox.d/<owner>__<repo>.env` 覆盖它（`project_env_files()` 决定顺序，
    `session/manager._provision` 按**本会话仓库**取；provider/proxy 把合成结果当成
    `env` 的同级参数往下传，沙箱重建时照原样重写）。
- **关键决策**：
  - 项目配置走**「写文件 + 每条命令 source」**，不塞容器 env：容器 env 建好就固定，改配置不生效
    （与这次 GitHub 令牌踩到的是同一类坑）；写文件后**改完重连即生效**，不必重建容器。
  - 平台默认与用户配置**合成同一份**文件（用户键写在后面即覆盖），少一层机制、少一处漂移。
  - 缓存卷默认**跨会话共享**（与 Maven 一致）；存储不支持多机同挂就把 `*_CACHE_SHARED=false`。
  - 关掉某个生态的缓存开关时**不设**它的路径变量（用镜像默认），避免「以为在共享缓存、其实在容器里」。
  - 平台注入的 registry / GOPROXY **优先于仓库自带的 `.npmrc`**（环境变量 > 项目配置），
    这是用户确认接受的加速口径。
  - 按仓库分文件（而不是把值塞进请求或前端表单）：多仓库各填各的、平台侧维护，改动最小；
    将来要开放给端用户，再把值改成建会话时传入即可（同一条合成链路）。
- **验证**：
  - 新增 `tests/test_project_env.py`（10 例）：解析口径（注释 / 空行 / `export` / 引号 / 值里带 `=` /
    非法键）、单引号转义、`load`（未配或文件不存在都返回空）、平台默认含 Go/npm 源、
    用户覆盖平台、**用 Git 自带 bash 真跑**生成的片段（值含空格、`$`、单引号仍原样取出）、
    `aexecute` 包装、无配置时不包装、权限检查跑在原命令上（去 `cat` 那份文件会被拦且不发往沙箱）。
  - `tests/test_sandbox_volumes.py` +2、`tests/test_sandbox_provider.py` +2（写文件与 chmod、
    无配置时什么都不写）、`tests/test_workspace_runtime.py` 更新为「四个卷 + 项目配置随建/重连带上」、
    `tests/test_sandbox_proxy.py` 加「重建时项目配置一并重写」。
  - 全量 `pytest` **1043 passed**；`ruff check` 改动文件干净；`scripts/check_prompts.py` 冲突扫描 **0 条**。
- **遗留**：
  - **沙箱内的真实验证待你在有网环境跑**（我这边外网被挡）：`go env GOPROXY GOMODCACHE`、
    `npm config get registry`，以及 `go mod download` / `npm ci` 在**容器重建后**是否仍命中缓存。
  - 只认配置文件、完全不读环境变量的工程（个别 Go / Java 项目），仍需 agent 自己按仓库里的 example
    生成配置文件（值从环境变量取）；平台只保证「值已在环境变量里」+「依赖源与缓存已配好」。

#### 代码平台双支持：仓库地址判平台，PR 工具内部按 Gitee / GitHub 分流

- **动机**（用户口径）：支持 GitHub。git 工具**还是同一套**，内部按平台分类 —— gitee 走 gitee
  的逻辑、github 走 github 的（工具名、入参、返回文案对模型完全一致）。
- **改动**：
  - 新增 `codeplatform.py`（**叶子模块**，不 import config/tools，避免成环）：
    `Platform` 枚举（`GITEE` / `GITHUB`，带 `host` / `pr_segment` / `default_git_username`，
    认不出的取值一律回落 Gitee）+ `RepoSpec`（owner/repo/token/git_username/platform，
    `platform` 默认 Gitee、`git_username` 留空按平台填默认）+ `parse_repo_url` /
    `parse_pr_url`（按域名判平台；PR 链接段 `pulls`（Gitee）与 `pull`（GitHub）都认）。
  - PR 客户端拆三层：`tools/pr_client.py`（共用：`PrLookupError`、`PrClient` 协议、
    `pulls_or_raise`、`find_open_pull`、三个可读格式化），`tools/gitee_client.py`（原
    `GiteeClient` 搬来），`tools/github_client.py`（新）。
  - `tools/git.py`：五个工具 `create_pr` / `comment_pr` / `list_pr_comments` /
    `get_pr_info` / `close_pr` 的**名字、入参、返回文案一律不变**，内部只认
    `build_git_client(settings, repo)` 这一个分叉点；`get_pr_info` 贴链接时**按链接判平台**
    （可以查另一个平台的 PR，用该平台配置里的令牌）；`GiteeRepo` 保留为 `RepoSpec` 的旧名
    （既有 import 与测试无需改动）。
  - GitHub 的差异全封在客户端里：`Authorization: Bearer` + `Accept` + `X-GitHub-Api-Version`；
    **评论走 issues 端点**（`/issues/{n}/comments`，GitHub 的 `/pulls/{n}/comments` 是 diff 行级
    review 评论）；关闭 PR 用 **body** 传 `{"state":"closed"}`；`state` 没有 `merged` —— 要合并态
    就查 `closed` 再按 `merged_at` 本地过滤；`mergeable=null` 如实回「未知（平台还在计算）」。
  - 会话与库：`sessions` 新增 `git_platform`（`server_default='gitee'`，老会话零数据迁移，
    alembic `c7d1a54e8f21`）；`POST /sessions` 支持 `github_token`；`manager.create_session`
    按平台取令牌（请求优先、`.env` 兜底）与 git 用户名。
  - **沙箱层做成平台无关**：环境变量 `GIT_TOKEN` / `GIT_USERNAME`、单个 `git-askpass`、
    凭据文件 `.secrets/git_token`（替换原 `GITEE_TOKEN` / `GITEE_GIT_USERNAME` / `gitee-askpass` /
    `.secrets/gitee_token`）；`safety/permissions.py` 的 askpass 保护名与
    `docs/sandbox-image-guide.md` 同步。
  - 记忆链路（`MemoryMiddleware` 的闸门、`server/service.py` 的收尾回填、`save_repo_memory`
    的前置检查）只换装配点，逻辑一行未改。
  - 文案中性化：`agent/prompts.py`（「平台边界」段重写）、两个 SKILL、两份必须逐字一致的
    记忆模板、`tools/memory.py`、`memory_reconcile` 注释、README、`.env.example`（新增
    `GITHUB_*`）、前端新建会话的 placeholder；`docs/gitee-pr-api-reference.md` →
    `docs/code-platform-pr-api.md`（补 GitHub 差异表）。
- **关键决策**：
  - 平台由**仓库地址**判定并记在会话行上（一个会话一个平台）；PR 链接另行判定（工具入参级）。
  - 只加一列 `git_platform`（用户口径）：token / username 继续用既有两列（注释改为平台通用）
    —— 少一列、少一次数据迁移，老行由 `server_default` 兜住。
  - 沙箱**不感知平台**：一个会话只装一个令牌，askpass 与凭据文件保持单一；平台差异只留在
    工具层，避免两套脚本各写一遍。
  - GitHub 凭据本次只走 `.env`（请求也可带 `github_token`），**不做 OAuth 登录**（用户口径）。
- **验证**：
  - 新增 `tests/test_codeplatform.py`（平台判定回落、域名解析、`RepoSpec` 默认值、工厂分派）
    与 `tests/test_github_client.py`（7 个方法的 method+path+认证头、**issues 评论端点**、关闭用
    body、`merged` 本地过滤、fork 同名分支过滤、查 PR 失败必抛 `PrLookupError`、无令牌回可读
    错误、`merged_at` / `mergeable=null` 的渲染口径）。
  - 全量 `pytest` **1029 passed**（基线 1007 + 新增 22）；`ruff check` 全部改动文件干净；
    `scripts/check_prompts.py` 冲突扫描 **0 条**；`npm --prefix frontend run build` 通过。
  - 数据库现状：`alembic current` = `b41d7e2c9f30`，本次只差 `c7d1a54e8f21`
    —— **必须跑 `alembic upgrade head` 才能建会话**（模型已带非空列）。
- **遗留**：
  - **GitHub OAuth 登录**：按用户口径本次不做。GitHub 凭据只来自 `.env` 的
    `GITHUB_ACCESS_TOKEN`（或请求带 `github_token`），前端暂无 GitHub 登录入口。
  - **真实联调未做**：github.com 的真实 API（建 PR / 评论 / 读评论 / 关 PR）需要在有网环境
    用测试仓库走一遍；本机只用 `httpx.MockTransport` 钉了客户端行为。
  - **代码面板（repo_cache）仍不带令牌**：私有仓库（Gitee / GitHub 同样）在右侧栏「代码」
    面板里 clone 不下来 —— 现状如此，本次未变。
  - 自建实例（GitHub Enterprise / Gitee 私有部署）不在支持范围，域名认不出会直接报错。

#### 已完成的待办清单不再占地方（跑完就不画，没做完才留）

- **动机**（用户要求）：针对已经完成的任务，`write_todos` 的任务列表不再展示在界面上 ——
  每轮跑完气泡下面都留着一份全绿的清单，读过一次就是噪音。
- **改动**：
  - 新增 `frontend/src/todos.ts`：`todosAllDone()`（整棵树含子项都 completed）+ `shouldShowTodos(items, finished)`。
  - `frontend/src/App.vue`：待办块那行的 `v-if` 从 `todosPart(m).length` 换成 `showsTodos(m)`
    （= `shouldShowTodos(todosPart(m), Boolean(m.at))`）。
  - 顺带修一处既有 bug：折叠箭头的 `:class` 用的是 `isTodoOpen(m, k, i)`，而 `:aria-expanded`
    用的是 `hiddenCount + i` —— 会话被 `visibleTail` 截断时（hiddenCount > 0）箭头与展开状态
    对不上，统一成绝对下标。
- **关键决策**：
  - 口径：整棵树（主项 + 子项）全完成 **且这一轮已收尾** 才不画；只要还有一项 pending /
    in_progress 就照旧显示 —— 那是「哪儿没做完」的唯一线索。
  - 为什么带「已收尾」这个条件：只看全绿的话，模型中途把全部标成 completed 的那一瞬间列表会
    闪一下消失，随后又加一项再冒出来。收尾时间（`Message.at`）在流结束后才落上、历史消息一律
    带着它，所以实时与历史是同一套判定。
  - 数据层不动：后端照旧把待办树写进 `extra.parts`（刷新回放、记忆、审计都要），只在展示层不画。
- **验证**：
  - 判定真值表 18 项（Node 24 直跑 `frontend/src/todos.ts`）：空树、运行中全绿（不许闪）、
    收尾全绿（不画）、收尾有 pending / in_progress / 孙辈没完 / 父项没完（都画）、未知状态不当作
    完成，并用这一轮真实落库的那棵树（record 369）正反两面各验一次。
  - 真实页面核对：可见窗口（最后 60 条消息）里共 12 份待办树、其中 9 份全完成 —— 改后页面只渲染
    **3 份**，与数据侧预测一致，剩下的正是没做完的那几轮。
  - `npm --prefix frontend run build`（含 `vue-tsc`）通过；全量 `pytest` **1007 passed**。
- **遗留**：无。

#### 子任务挂靠不再因主控改写条目文字而漂移（认亲改「归一化 + 锚定」）

- **动机**（用户反馈）：最近一轮里 `write_todos` 子任务开始时挂在对应父任务下，跑完就漂移。
  查真实日志与落库记录（record 367 / 369）定位：`stream.py::tree()` 用**父项文字逐字相等**
  当挂靠键，而主控每次 `write_todos` 都是全量重写、还习惯把结果追加进条目文字
  （`生成用户资料编辑实施方案` → `…（plan_id=36）`；`实施用户资料编辑代码并提 PR` → `…（PR #9）`）。
  子智能体一返回主控就更新清单，文字一变匹配失败 → 子清单被当成「挂不上落点」追加成**树尾容器**
  （`规划子任务` / `编码子任务`），界面上就是「跑完就漂移」。文字始终没变的那一项
  （`代码审查并呈报审查结论`）一直挂得住 —— 正好反证机制。
- **改动**（`src/ai_coding_harness/stream.py`）：
  - 新增 `_todo_text_key()`：条目文字的挂靠键 —— 去空白 + 去结尾的补充说明（连续多层的
    `（…）` / `[…]` / `【…】`）+ 去首尾标点。
  - `tree()` 改为按**下标**挂靠，新增 `_locate()`：认亲顺序 = **锚**（上次挂上那一项的文字）→
    **委派那一刻**那串，各自先 逐字 → 归一化（**唯一命中**才算）→ 前缀（短的那串 ≥4 字、
    **唯一命中**才算）；都认不出时，若主清单**条数没变**，沿用锚记下的位置。
  - 挂上就记锚 `(那一项当时的文字, 下标, 当时条数)` —— 父项随后再被改写也不失联。
  - 真正认不出（主清单为空、已全部完成、那一项被整条删掉、条数变了、归一化撞键）照旧走
    树尾容器兜底：宁可位置难看，也不能把子项挂到别的父项下。
- **关键决策**：
  - 「唯一命中」是硬条件：两串归一到同一个键时不猜，落容器。
  - 前缀认亲设 4 字下限，避免 `改代码` 被当成 `改代码并提交` 的父项。
  - 只在条数没变时按位置沿用 —— 项被拆开/删掉就不猜。
  - **行为变更**：主项被整句改写（`改代码` → `重构代码`）时不再落容器，改为沿用原位
    （旧口径见 `tests/test_stream_v3.py::test_child_todos_survive_a_renamed_main_item`，
    本次按新口径改写为 `test_a_renamed_main_item_keeps_its_children`）。
- **验证**：
  - 新增 5 个用例（尾部补充、无括号续写按前缀、整句改写沿用原位、同键歧义不猜、短标题不走
    前缀）+ 按新口径更新 1 个既有用例；`test_stream_v3.py` + `test_plan_events.py` +
    `test_chat_stream_route.py` **98 项全过**。
  - 用这一轮**真实的**标题 / 委派序列回放修复后的 translator（16:26 → 17:12）：最终树是 5 条
    主项，planner / coder / reviewer 三份子清单各归其父项，**兜底容器 0 个**；而当时实际落库的
    record 367 里 `规划子任务` / `编码子任务` 是漂在树尾的。
  - 全量 `pytest` **1007 passed**；`ruff check` 两个改动文件干净。
- **遗留**：新一轮里主控「先委派、后落清单」时（17:01 那轮 17:03 委派 reviewer、17:10 才写
  自己那份清单），子清单仍会以树尾容器出现 —— 那是另一条（委派时主清单为空，没有父项可挂），
  本次按用户选择（只做 a+b）未处理。

#### 前端流式卡顿：正文分块渲染 + 追底不再硬拽 + 长会话只渲染最近 60 条

- **动机**（用户反馈）：「每次开始执行任务的时候，前端界面都比较卡，上下拉、点按钮都会这样」。
  只读排查后定位到根因：流式期间每 100ms 会把「正在长的那条回复」**整段重渲染**一遍
  （marked 解析 → hljs 高亮 → DOMPurify 消毒 → 整段 innerHTML 替换），紧接着
  `scrollTop = scrollHeight` 又强制一次重排版。实测一帧 15~17ms（其中强制重排版 8~9ms、
  且随会话变长上涨），按 10 帧/秒折算每秒 150ms 以上都花在这一件事上 —— 滚动与点击撞上这些帧
  就掉帧。另外 `flush()` 里是**无条件**追底：用户往上翻历史时每 100ms 被拽回底部。
- **改动**：
  - 新增 `frontend/src/streamblocks.ts`：流式正文分块。块长到 `BLOCK_SOFT_LIMIT`（1500 字符）
    就在**安全切点**封口（空行；且不落在未闭合的代码围栏里、不切在列表项/表格行/引用段之前、
    右侧不缩进）；封口块的内容不再变，模板上的 `v-memo` 自然跳过它，每帧只重渲染还在长的
    那一小块。同时导出 `fenceClosed()` 供正文渲染判定「围栏还没闭合」。
  - `frontend/src/App.vue`：`Part` 的 text 支加 `sealed`（`pushText` 只往未封口的块续写，
    `flush()` 里调 `splitLongText`）；新增 `textGroups()` —— **连续**的 text 块合成一组渲染成
    一个气泡，切开之后视觉上仍是一条连续正文（块间距与段间距一致），待办隔开的仍是两个气泡
    （与改动前一致）；`scrollToBottom()` 加 `nearBottom()` 判断（离底部 48px 内才追）与同 tick
    合并，`flush()` 先读「是否贴底」再决定追不追；会话只渲染最近 `VISIBLE_TAIL = 60` 条，更早的
    折叠为顶部「显示更早的 N 条」按钮（`key` 与待办折叠 key 一律用**绝对**下标，避免窗口滑动时
    状态串位；切会话时展开量复位）。
  - `frontend/src/markdown.ts`：围栏**没闭合**（流式中的代码块）先按纯文本渲染，闭合那一帧再
    上色 —— 否则每帧都是 hljs 缓存未命中（实测 4.6ms/块/帧）。
  - `frontend/src/style.css`：`.md-block` 的首尾边距规则、`.load-earlier` 按钮样式。
- **关键决策**：
  - 切点宁可少切也不错切：找不到安全点就让这一块继续长（切进围栏/表格/列表会渲染错，而流式
    视图是用户正在读的东西）。
  - 分块只改渲染、不改数据：`textGroups()` 保证「切开」与「没切开」观感一致；落库与刷新回放
    路径完全不变。
  - 阈值取 1500：实测单帧中位 3000→1.40ms、1500→0.80ms、1000→0.70ms、600→0.60ms，再往下收益
    就平了，而块数持续上涨（13KB 正文：3000→5 块、1500→12 块、600→15 块）。
  - 节流仍保持 100ms：分块之后单帧已经很轻，调到 200ms 只会让流式显得更顿。
- **验证**：
  - 分块逻辑 30 项断言（Node 24 直跑 `frontend/src/streamblocks.ts`）：围栏开闭判定、各类不安全
    切点、切完内容一字不丢、连续流式 400 段后每块围栏成对。
  - 真实数据对比（把真实会话的 180 条消息铺进 DOM，其中一条 13 KB 回答按每帧 40 字符喂入）：
    普通回答整条重渲染中位 **4.90ms → 1.40ms**（合计 1680ms → 537ms）；代码型回答（尾部是一个
    还在长的 Java 代码块，43KB/1104 帧）中位 **17.30ms → 2.60ms**，单帧 >16ms 的掉帧帧数
    **594/1104 → 5/1104**，合计 19.5s → 3.3s。
  - `npm --prefix frontend run build`（含 `vue-tsc`）通过；全量 `pytest` **1002 passed**。
- **遗留**：
  - 没用 `content-visibility: auto`：它按估算尺寸参与滚动高度，而每个 flush 都要读 `scrollHeight`
    追底，容易让滚动位置跳；改用「只渲染最近 60 条」这条确定性路径。
  - 日常仍跑 Vite dev（`start.bat` / `start.sh` 走 `npm run dev`）：dev 运行时比生产包贵一截，
    是否改成 `build + preview`（会失去 HMR）待定。
  - `/records` 仍是一次性全量返回（这次会话 500 KB 级 JSON），首屏还得等它。

#### 意图判定换四关瀑布：口令 → JEV（SystemOne）→ 大模型 → 关键字口径（默认值改 `coding`）

- **动机**（用户口径）：用 **JEV**（决策型模型，SDK：`typesafe-sdk`）替掉原来「大模型直接判」那一档；
  顺序定成 **关键字 → JEV → 大模型 → 默认值**，且**默认值从 `qa` 改成 `coding`**。
- **改动**：
  - `server/intent.py`：`detect_intent` 改成四关（新增 `jev=` 参数）。新增 `IntentDecider` 协议与
    `JevDecider`：一个 `Choice` 问题，候选就是能判的那 6 个意图（`_LLM_INTENTS`，**不含 sync**），
    `instructions` **直接复用 `_system_prompt()`**（与第三关逐字同一份口径）；拿回 `.choice` /
    `.confidence` / `.probabilities` 并把置信度与次高候选记进日志。`IntentDecision` 加 `confidence`；
    新增 `SOURCE_JEV="jev"`；`DEFAULT_INTENT` 由 `qa` 改 **`coding`**；新增 `build_jev_decider(settings)`
    （没启用 / 没配 key / 装不上 SDK → `None`，跳过这一关，服务照起）。SDK **懒加载**。
  - `config.py` + `.env.example`：`INTENT_JEV_ENABLED`、`TYPESAFE_API_KEY`、`TYPESAFE_BASE_URL`、
    `TYPESAFE_MODEL_NAME`（回落 SDK 的 `TYPESAFE_DEFAULT_MODEL`，再回落 `jev-latest`）、
    `TYPESAFE_TIMEOUT_SECONDS`（默认 10）。
  - `server/service.py`：`HarnessService(intent_jev=…)`；`_detect_intent` 与 `_rejudge_intent`（`handoffTask`
    的重判）都把 JEV 传进去 —— 重判仍跳过口令档，先问 JEV 再问大模型。
  - `api/app.py`：lifespan 里造判定器，退出时 `aclose()`（SDK 的异步客户端要收）。
  - 文档：README 补「四关瀑布」与环境变量清单；`docs/business-flow.md` §二 从「三档」改成「四关」。
- **关键决策**：
  - **用 `Choice` 而不是 `Score` / `Noul`**：7 个意图是互斥枚举，`Choice` 直接给标签 + 置信度 +
    每个候选的概率（都进日志，比大模型自述的一句话更硬）。
  - **候选里没有 `sync`**：与第三关同一口径 —— 直接执行路径（不经智能体、直接在沙箱跑命令）
    只能由口令产出，从源头堵死「模型猜出个直接执行」。
  - **`instructions` 复用 `_system_prompt()`**：两关判的必须是同一件事，口径一漂就会
    「换个模型结论就变」，事后从日志也看不出是哪一层判的。
  - **任何失败都只降级**：JEV 没配 / 超时 / 报错 / 回认不出的标签 → 交给下一关；构造失败也不让
    服务起不来（SDK 懒加载，与 `tools/web_search.py` 同一态度）。
  - **默认值改 `coding` 的连带影响**（这是用户明确要的口径，写在这里免得日后被当成 bug）：
    两个模型都判不出来**且**关键字没命中时，这一轮按 `coding` 走 —— 也就是 `orchestrate` 形态、
    要过记忆闸门、可能真的派 planner/coder；升级前这种句子落 `qa`（只答不做）。
    关键字口径本身没变：`classify()` 命中弱词仍按词表判，只有「一条都没命中」才落默认值。
- **验证**：全量 `pytest` **1002 条全过**（改前 991，新增 11 条）；`ruff check` 改动文件干净；
  `scripts/check_prompts.py` **冲突 0 条**（本次不动提示词，跑一遍确认没被牵连）。新增用例：
  口令命中不问 JEV / JEV 判出来不再问大模型 / JEV 抛错退到大模型 / JEV 回认不出的标签同样退 /
  两个模型都挂 → 关键字口径 → 默认值 `coding` / 弱词与最近几轮历史会传给 JEV / `force_llm` 跳过口令
  但仍走 JEV / 没配 JEV 时是升级前那两条路 / 真 SDK 造一次判定器（钉住构造参数名）/ 没 key 或关掉时
  返回 `None`。**并用真 `.env` 实测**：`build_jev_decider(Settings())` 拿到 `JevDecider`
  （`model=jev-latest`、base 已配、timeout=10），说明包在、参数名与真 SDK 对得上。
- **遗留**：
  - **真调 JEV 没在沙箱里跑通**（我这边网络受限，只有本地回环可用）：JEV 那一关只验到「判定器能造出来 +
    失败能干净降级」。真 key 的联调在你那边 —— 重启后端跑一轮，日志里应出现
    `意图分析：intent=… source=jev …` 与 `JEV 判定：intent=… p=0.93 次高 plan=0.05 model=… tokens=…/…`。
  - **低置信度照用**（只记日志，不做额外动作）；要改成「低置信度再问一次大模型」是后续一步的事。
  - 配置改动要**重启后端**才生效。

#### 加 `docs/coding-sequence.drawio`：给 draw.io 原生打开的那份时序图（含首版打不开的修正）

- **动机**（用户要求）：Mermaid 源码只有装了插件的编辑器才看得到图；用户要一份**能用 draw.io 打开/编辑**的。
- **改动**：
  - 新增 `scripts/make_drawio.py`：把 coding 时序图（与 `docs/business-flow.md` §五 同源的数据）算成
    draw.io 的 mxGraphModel XML —— 8 条泳道（表头 + 虚线生命线）、41 条消息（调用实线 / 返回虚线、
    自调用带折线）、6 条阶段灰带、3 个 `alt` / `loop` 方框（`else` 用虚线分隔）。`--out` 可改落点。
  - 新增产物 `docs/coding-sequence.drawio`：draw.io 桌面版/网页版直接打开即可编辑，
    也能在 draw.io 里另存为 `.drawio.svg` 嵌文档。
  - `docs/business-flow.md` §五 末尾加一条互链：改图时 Mermaid 与 `.drawio` 一起改。
- **首版打不开的修正**（用户实测反馈「不好使」，2026-09-27 同日改）：本机找不到 draw.io Desktop
  （注册表 / 常见安装路径 / Start Menu 都没有），网页版在后台标签里跑不起来，**没能在这里复现用户看到的现象**；
  按最可能的三个原因改掉并重新生成（1330×2348，119 个 mxCell）：
  1. **消息文字不再当边标签**：draw.io 的边标签默认压在线上，41 条消息按 34px 行距会互相压 —— 现在每条消息的
     文字是**独立一格**（白底、无边框，落在箭头上方），与 Mermaid 的排法一致；行距 34 → **44**。
  2. **补文件头**：加 `<?xml version="1.0" encoding="UTF-8"?>` 与 `<mxfile version=…>`，避免不同版本/工具
     对编码与格式的猜测。
  3. `else` 分隔线改成 `endArrow=none`（原来会多画一个箭头）。
- **兜底路线**（不依赖我生成的 XML）：draw.io 里 `Insert → Advanced → Mermaid…`，把
  `docs/business-flow.md` §五 那段贴进去，draw.io 自己会转成可编辑图形。
- **关键决策**：
  - **几何用脚本算，不手写坐标**：41 条消息的 y、阶段带的跨度、分组框的包围盒都由同一份数据推出来 ——
    手写 XML 迟早错一行，而错一行在 draw.io 里就是一条线跑偏。
  - **用「浮动边 + 显式端点」画消息**（`sourcePoint` / `targetPoint`，自调用再加 `Array` 折点），
    而不是把边挂在两个表头之间：前者能把每条消息钉在**指定的 y**，后者会被 draw.io 自动路由拉走。
  - **数据两处同源、人工同步**：Mermaid（给人读）与 `.drawio`（给 draw.io 打开）各存一份，改图时一起改；
    没有做「从 Mermaid 反解」的自动转换 —— 那要写个 Mermaid 解析器，收益不抵复杂度。
- **验证**：`scripts/make_drawio.py` 跑通（首版 78 → 修正后 **119 个 mxCell**）；产物用 `xml.etree` 解析：
  格式合法、页面 1330×2348、66 个顶点（8 表头 + 8 生命线端点 + 6 阶段带 + 3 分组框 + **41 个消息文字格**）
  ＋ 51 条边（8 生命线 + 2 个 `else` 分隔 + **41 条消息**，与图上的消息数一致）、坐标齐全；
  `ruff check scripts/make_drawio.py` 干净；全量 `pytest` **991 条全过**（只加脚本与文档，未动运行时代码）。
- **遗留**：**没有在 draw.io 里真打开验证过** —— 本机找不到 draw.io Desktop（可能是 VS Code 的
  Draw.io 插件、或装在我枚举不到的位置），`app.diagrams.net` / `viewer.diagrams.net` 在这个环境的
  后台标签里起不来（受限上下文中 `fetch` 都不可用，无法驱动其前端）。**下一步要看用户打开后的具体现象**
  （打不开报什么错 / 打开了但排版乱 / 中文乱码）才能对症再改。

#### `docs/business-flow.md` 增补：coding 模式时序图（从用户访问到结束）

- **动机**（用户要求）：总览图与 coding 环路图回答的是「有哪几条分支」，看不出**谁在什么时候找谁**、
  以及 SSE 与运行是两条线这件事。补一张时序图。
- **改动**：`docs/business-flow.md` 新增 §五「coding 模式时序图」，Mermaid `sequenceDiagram`，
  参与者从左到右是：用户（浏览器）→ 前端 → 后端 API → RunHub → service → 中间件 → 主控模型 →
  子智能体 → 工具 → 沙箱 → Postgres → Gitee。六段（`rect` 分组）：
  ① 建会话（复用活跃会话 / 后台建沙箱 / 轮询到 ready）② 发消息（凭据拒收、`RunHub.start`、
  同会话并发 409、SSE 按序号订阅）③ 开工前（意图分类 → coding、记忆刷新与闸门拦放行、按形态裁工具）
  ④ 跑这一轮（模型 ↔ 工具循环 → `get_unexecuted_plan` 分叉：有方案直接 coder / 没有先 planner
  且**不等确认** → coder 改码提 PR → reviewer 回评）⑤ 收尾（先呈结论，再 `save_repo_memory`，
  平台轮末打底与落库，最后 done）⑥ 之后（终止 / 刷新接回 / 删会话 / 人工合并 / rework 另起一轮）。
  图后附三点读图提醒（只有 ① 是「用户访问」、SSE 与运行两条线、平台与模型各写哪部分）。
  为放这张图，原 §五～§七 顺延为 §六～§八，正文里的交叉引用（`§五` → `§六`）一并改掉。
  同时修正了 §四 环路图：两条入口（「要方案」vs「直接做」）画清楚 —— 后者先查方案、没有就 planner
  落库后**不等确认**，与前一轮刚改的 coding 口径对齐。
- **验证**：代码块围栏 3 对已核对；章节号与交叉引用（§二/§三/§四/§六）全对上；图中每条消息都对着
  实际调用复核过（`sessions.py` 的建会话与 workspace 轮询、`chat.py` 的 start/stream、`RunHub` 的
  409、`middleware/memory.py` 的闸门、`register.py` 的 `get_unexecuted_plan`、coder 的 create_pr、
  reviewer 的 comment_pr、`save_repo_memory` 的写盘打底）。本次只改文档，未动代码。
- **遗留**：本机没有 Mermaid 渲染器（要联网 + Chromium），**没有渲染出图看过** —— 只做了语法与
  逻辑复核；在 Gitee 仓库页或带 Mermaid 插件的编辑器里打开 `docs/business-flow.md` 即可确认观感。

#### coding 意图改口径：明确要动手时主控先查方案，没有就 planner→coder→reviewer 一路跑完

- **动机**（用户口径）：原来「明确要动手」那条路是**跳过 planner、直接派 coder**，由 coder 自己
  `get_unexecuted_plan`；取不到方案才回报、主控改派 planner，然后**停下等用户确认** —— 明明用户
  已经说了「直接做」，却还要再确认一次。改成：**主控先查方案**，有就直接派 coder，没有就先派
  planner 出方案、**落库后不等确认**，接着 coder → reviewer，这一轮跑完为止。
- **改动**：
  - `tools/register.py`：`ORCHESTRATE_FORM` 的工具表加**只读的** `get_unexecuted_plan`
    （主控据此判「直接派 coder」还是「先派 planner」）；`build_main_tools` 里 plan 工具池的构建
    条件从「有 `storePlan`」放宽成「有 plan 类工具」（`_PLAN_TOOL_NAMES`），池子整口建、按表挑。
    **方案的增删改仍不给主控**：`storePlan` / `getPlan` / `updatePlan` / `updatePlanSteps` /
    `deletePlan` 一件都不在表里（那是 planner 与 plan 形态的活）。
  - `agent/prompts.py` 的 `coding` 指令重写：**明确要动手** → ① 主控先 `get_unexecuted_plan`
    查一次；② 有方案 → 直接派 `coder`；③ 没有 → 派 `planner` 落库（**不停下等确认**）→ 接着派
    `coder` 实施到提 PR → 再派 `reviewer` 审查 → 结论呈给用户，本轮结束（不自动回环修复）。
    **没明确要动手**那条路**保持不变**：planner 出方案 → 停下等「确认实施」→ coder → reviewer。
    coder 的兜底也不变（取不到方案就停下回报，不硬做；主控据此改派 planner）。
- **关键决策**：
  - **查方案由主控做、而不是让 coder 先撞一次**：省掉「派了 coder → 它说没有方案 → 再改派 planner」
    那一圈；`get_unexecuted_plan` 是纯只读查询，给主控没有越权风险。
  - **planner 落库后不确认**：用户已经用「实施 / 直接做 / 确认实施」表达过「现在就去改」，
    再停一次等于把同一句话说两遍。方案照旧落库（`storePlan`），方案面板上用户仍看得到它。
  - 非明确路径不动：只是描述需求或问怎么做时，仍然「先出方案、等确认」——那是唯一能让用户
    在动手前拦一道的地方。
- **验证**：全量 `pytest` **991 条全过**（改前 989，新增 2 条）；`ruff check` 改动文件干净；
  `scripts/check_prompts.py` **冲突 0 条**。新增/更新的用例：`orchestrate` 的完整工具清单里多出
  `get_unexecuted_plan`（`test_profiles` / `test_tool_registry` 两处精确清单都跟着更新）；
  新用例钉住「主控能查未执行方案、但拿不到 `storePlan` 等方案 CRUD」；`test_prompts` 里把
  coding 两条分支的断言改成新口径（先查方案 / 落库不等确认 / 接着 coder / 再派 reviewer /
  中途不停下来问用户），并保留「没明确 → 停下等确认」那条。
- **遗留**：
  - 提示词工具子集是**进程启动时装配**的：改完要**重启后端**；已在跑的老会话建议新开一个。
  - 「明确要动手」现在会一路跑到 PR 建好 + 审查完，中途没有人能拦一下那份方案 —— 这是用户
    明确要的口径；要反悔就把它改回「planner 落库后停下等确认」。
  - 顺手修了一处**外部引入的语法错误**：`server/intent.py` 末尾多出一个半截空函数
    （`async def detect_intent_by_jev():`，函数体为空），会让整个包 import 失败 —— 已删除。

#### 新增 `docs/business-flow.md`：从建会话到一轮结束的流程图（含各处分叉）

- **动机**：端到端链路散在 `api/routes/`、`server/service.py`、`server/intent.py`、
  `agent/middleware/`、`agent/profiles.py` 五处，读代码才能拼出「一条消息进来会走哪条路、
  在哪些地方分叉」。用户要一张业务视角的流程图。
- **改动**：新增 `docs/business-flow.md` ——
  ① 一张 Mermaid 总览图（建会话 → 发消息 → 凭据拒收 / 意图分类 → 运行宿主与 409 → 工作区 →
  直接执行(sync) 或派智能体 → 记忆闸门 → 模型↔工具循环 → 子智能体编排 → 收尾落库与记忆打底 →
  结束后的四种去向：继续问 / 终止 / 刷新接回 / 删会话，以及「人工合并后下一轮刷新状态」）；
  ② 三张速查表：意图判定三档 + 七个意图的形态/工具/子智能体、记忆闸门四种组合、仓库记忆四个写入时机；
  ③ coding / rework 的编排环路小图（planner → coder → reviewer → 用户点问题 → rework 回路）；
  ④ 失败与降级分支表（含「消息含凭据」「同会话并发」「沙箱建不出来」「Gitee 查不到」「模型全败/熔断」
  「本轮越界」「Redis 不可用」「DB 不可用（含 130s 超时这条已知问题）」「刷新接回」「用户终止」）；
  ⑤ 按流程顺序的文件索引（哪一步落在哪段代码）。README 的 `docs/` 说明同步提到它。
- **关键决策**：
  - **画「代码实况」而不是「设计意图」**：图里每一步都标了落在哪个文件，改流程时连同本文一起改。
  - **用 Mermaid 而不是 ASCII**：Gitee 仓库页与带 Mermaid 插件的编辑器能直接渲染；同时刻意写成
    **扁平 flowchart**（不用 subgraph）—— 跨 subgraph 的前向引用在部分渲染器里会把节点摆错组。
- **验证**：`docs/business-flow.md` 的代码块配对已核对（2 对围栏）；文中每个分叉都对着代码复核过
  （`service.chat` 的出口与分流、`_direct_sync`、`RunHub.start` 的 409、`middleware/memory.py` 的闸门表、
  `profiles.py` + `register.py` 的形态/工具表、`_MEMORY_WRITE_FORMS` 的打底口径）。
  本次只加文档，未动代码：全量 `pytest` **989 条全过**（与改动前一致）。
- **遗留**：本机没装 Mermaid 渲染器（要联网 + Chromium），所以只做了语法与逻辑复核、**没有渲染出图看过**；
  有 Mermaid 插件的编辑器里打开即可确认观感。

#### Redis 支持密码：`REDIS_PASSWORD`（+ URL 缺 scheme 的兜底与日志脱敏）

- **动机**：用户指出 Redis 配置里少了密码项（.env 里已加 `REDIS_PASSWORD`）。核对时另外查出两件事：
  - `REDIS_URL=127.0.0.1:6379` 这种**没写 scheme** 的写法会让 redis-py 的 `parse_url` 直接抛
    `ValueError: Redis URL must specify one of the following schemes (redis://, rediss://, unix://)`
    → 被 `build_redis` 接住 → **静默退回进程内**（只在日志里留一行）——「配了 Redis 其实没生效」
    这类问题最难发现；
  - 实测 `from_url(url, password=…)` **不会覆盖 URL 里的密码**（URL 里那个赢），不处理就会出现
    「改了 `REDIS_PASSWORD` 却不生效」。
- **改动**：
  - `config.py`：新增 `redis_password`（`REDIS_PASSWORD`）。
  - `redis_cache.py`：新增 `normalize_url`（缺 scheme 补 `redis://`）、`strip_password`、`mask_url`；
    `default_client_factory` 的口径定死：**显式 `REDIS_PASSWORD` 优先**（两边都写就先把 URL 里那份剥掉），
    **没配就不传 `password`**（空串会被 redis-py 当成真密码去 AUTH）；日志与报错里的地址统一走
    `mask_url` —— **密码绝不进日志**。
  - `.env.example`：补 `REDIS_PASSWORD=`，并写明「URL 不写 scheme 也行」「密码别塞进 URL（URL 会进日志）」。
- **关键决策**：
  - **密码单独一个键、优先级高于 URL**：显式的键更好排障，也避免「两个地方都能写」带来的歧义；
    只写在 URL 里的那种老写法仍然有效（没配 `REDIS_PASSWORD` 时就用 URL 里的）。
  - **缺 scheme 自动补而不是报错**：那是很自然的写法，为它让人读一段 redis-py 的报错不值当；
    归一化后地址会打码进日志，看得到实际用了什么。
- **验证**：`tests/test_redis_cache.py` 从 6 条扩到 **14 条**；全量 `pytest` **990 条全过**
  （改前 982，新增 8 条）；`ruff check` 改动文件干净。新增用例：密码确实进 `connection_kwargs`；
  两边都写时以 `REDIS_PASSWORD` 为准且**用户名保留**；只写在 URL 里的密码不丢；缺 scheme 能被归一化
  （用真 redis-py 断言 host/port）；没配就不传 `password`；`strip_password` / `mask_url` 的边界
  （IPv6 方括号、没有用户名、解析不了的原样返回）；**URL 带密码时日志里只有 `***`**、明文一个字节都不出现。
  - **并用真 `.env` 实测**：`build_redis(Settings())` 连上了用户本机的 Redis（`connected = True`），
    日志那行是 `已连上 Redis，运行缓冲与熔断状态走 redis://127.0.0.1:6379`（不含密码）。
  - 顺手修了一处**测试卫生**问题：`_settings()` 改成 `Settings(_env_file=None)` —— 默认会读本机 `.env`，
    实测把真实密码渗进了用例（断言失败时被原样打进 pytest 输出）。
- **遗留**：
  - **要重启后端**才生效（配置在进程启动时读）；重启后运行缓冲 / 会话运行权 / 熔断状态三样都走 Redis。
  - 密码是明文放在 `.env` 里的（与 POSTGRES_* 同一口径，不进版本库）；Redis 侧的 ACL / TLS（`rediss://`）
    没做，需要的话再说。

#### 仓库记忆两个刷新点：收尾按本轮 PR 回执记账 + 刷新记忆归入 `analysis`

- **动机**（两条都是当天真实故障，日志可查）：
  - **收尾漏记**：PR #7 在 `00:03:19` 建好（`create_pr` 回执里就有 `…/pulls/7（编号 7）`），
    但那一轮主控**一次 `save_repo_memory` 都没调**。隔天 `10:08` 用户说「按 P2 评审意见修改」，
    记忆闸门照记忆判定「仓库没有待合并的 PR」→ 把 `rework` 拦下了（闸门只看记忆里记着的 PR，
    记忆里没有 #7，于是判成「没有可修的对象」）。
  - **用户点名刷新没人接**：`10:10:36` 那条 15 字消息被判成 `intent=qa`（理由「刷新仓库记忆属于
    查看仓库信息」）→ qa 形态**没有 `save_repo_memory`** → 模型拉完 `get_pr_info` 想交接，被
    「重判仍是本形态」挡回来，整轮对记忆什么都没做。
- **改动**：
  - **刷新点 1（coding / review 收尾）**：`agent/prompts.py` 的 `_MEMORY_WRITE_BACK`（coding /
    review / rework 共用）收紧两条 —— ①「变更记录」的**编号与链接一律取本轮真实来源并原样引用**
    （coder 回报里的 `create_pr` 回执 `PR 已创建：<链接>（编号 N）` / `PR 已存在，直接复用：…
    （编号 N，未新建）`，审查轮取 `get_pr_info` 的返回），**不许凭印象编号、不许自己拼链接**；
    ② 新增**收尾自检**：本轮建过 PR 而提交的记忆里没有那一条 = 这轮记忆没写完；回报里缺编号/链接
    时先补齐再写（coding 轮用 `task` 让 coder 把回执原样重发一次，审查轮用 `get_pr_info`）。
  - **刷新点 2（用户主动要求）**：**归入 `analysis` 意图**（`server/intent.py`）——
    把「刷新仓库记忆 / 刷新记忆 / 更新仓库记忆 / 同步仓库记忆 / 整理仓库记忆」等口令挂到
    analysis 规则的 ``strong`` 上；`sync` 的 excludes 加「记忆」（与既有「同步机制 / 同步锁」
    同一口径），否则「同步一下记忆」会被 sync 的**直接执行**路径抓走（那条路不经智能体，
    记忆一个字都不会动）；LLM 那一档的 analysis 说明与分界也补上「**要求更新**记忆归 analysis、
    只是**查看**记忆归 qa」。`agent/prompts.py` 的 analysis 指令加一节「用户点名刷新记忆时」：
    先 `get_pr_info(<仓库地址>, state="all")` 拉全量 → 记忆里没有的 PR 补进「变更记录」（编号/链接
    取列表原文、日期取创建日期、写不实的留空不编）→ `save_repo_memory` 整篇写回 → 回复里给账并
    点名「还开着而记忆里原本没有」的 PR；同时给「analysis 不写变更记录」那条纪律留一个显式例外。
- **关键决策**：
  - **刷新点 1 不额外查 Gitee**：PR 信息就在 `create_pr` 的回执里（已核对 8 次真实调用，形状稳定），
    coding 轮**零额外调用**；只有回执缺项时才让 coder 重发一次（一次 `task`，比猜或漏记便宜）。
  - **不新开意图/形态，归入 analysis**：analysis 本来就有 `get_pr_info` 与 `save_repo_memory`
    这件组合，而且天然只读、天然不在记忆闸门里（刷新记忆恰恰发生在仓库还有未合入 PR 的时候 ——
    那时 plan / coding 会被拦下，这一轮必须放行）。
    （先加过一版独立的 `refresh_memory` 意图 + `FORM_ALIASES["refresh_memory"] = "analysis"`，
    **同日按用户口径撤回**：多一个意图等于多一份分类面与装配登记，收益为零。）
  - **「查看」与「更新」的分界写进两层**（口令词表 + LLM 分界说明）：只是**看**记忆内容的仍归 qa，
    只有**要求更新**才走这一档 —— 否则「仓库记忆里怎么说的」会被配上写工具。
  - 平台侧仍不做「强制」（比如 `save_repo_memory` 拒绝写一份缺本轮 PR 的记忆）：那要在平台侧
    跟踪「本轮建过 PR」，比提示词复杂得多；本次按提示词层做，硬保证留作后续选项。
- **验证**：全量 `pytest` **982 条全过**（改前 972，新增 10 条）；`ruff check` 改动文件干净；
  `scripts/check_prompts.py` **冲突 0 条**。新增用例：口令命中「刷新仓库记忆 / 刷新一下仓库记忆 /
  更新仓库记忆 / 同步仓库记忆 / 整理仓库记忆」→ 全部 `analysis`（且 `source=keyword`，
  一次模型都不调）；「同步一下记忆 / 同步一下仓库记忆」不再落到 `sync` 的直接执行路径，而
  「同步仓库」「同步代码」「拉取代码」仍是 `sync`；「仓库记忆里怎么说的 / 记忆里有哪些 PR」
  仍是 `qa`；**任务类型表里没有多出任何意图**（`refresh_memory` 不存在），analysis 不在
  `DIRECT_INTENTS` 也不在 `BLOCKING_FORMS`；分类提示词里带上与 qa 的第二条分界；三条收尾指令里都钉了
  「编号/链接取 `create_pr` 回执」「不许凭印象编号」「收尾自检：本轮的 PR 必须落到变更记录里」。
  README 的意图表与 `docs/workspace-directories.md` 的记忆写入形态同步更新。
- **遗留**：
  - **提示词与意图规则是进程启动时加载的**：改完要**重启后端**；已在跑的老会话建议新开一个。
  - 自然措辞（「把 Gitee 上没记的 PR 补进记忆」）走 LLM 那一档；**分类模型不可用**时回落到关键字
    口径，只有那 10 个口令能被认出来 —— 其余写法会落回 qa（那一轮又写不了记忆）。
  - 刷新点 1 仍是**提示词层的要求**：模型若整轮不调 `save_repo_memory`，本轮 PR 依然会漏记
    （平台轮末只刷文件头，不补正文）。要硬保证得做上面那条「平台侧强制」。

### 2026-09-26

#### 引入 Redis（可选）：多 worker 下的运行缓冲 / 会话运行权 / 熔断状态

- **动机**：兑现 CHANGELOG 里那两条「上多 worker 时这份缓冲要挪到 Redis」（RunHub 那条与熔断
  那条）。三样旁路状态原先都在进程内：RunHub 的帧缓冲（刷新后落到别的 worker 会拿到空流、
  `cancel` 也找不到那一轮）、`RunInProgress` 的**进程内**互斥（两个 worker 能同时跑同一会话 ——
  两个容器写同一个工作区）、模型熔断计数（各记一份等于没有熔断）。
- **改动**：
  - 新增 `redis_cache.py`：`build_redis`（`REDIS_URL` 留空 = 不启用；建好先 `ping` 验一次；
    **建客户端失败（没装 redis 包 / 地址写错）与连不上同一口径**：没声明必需就记 warning 返回
    `None`，`REDIS_REQUIRED=true` 才启动失败）+ `RedisBacked`（「一次失败即永久退回进程内」的
    包装 —— 否则 Redis 半死时每帧都白等一次超时）。
  - 新增 `server/run_store.py`：`RunBuffer` / `RunLock` 两个协议 + 内存/Redis 两套实现。键
    `harness:run:<session_id>:{seq,frames,done,cancel,lock}`，全部带 TTL；**帧号由 `INCR` 发**
    （`LTRIM` 丢老帧之后 `LLEN` 与帧号当场脱钩，拿下标当帧号前端续传就会重复或跳帧）；运行权
    `SET NX EX` + 续期；跨 worker 终止靠 `cancel` 标记，由持有者轮询到之后自己取消。
  - `server/runs.py`：帧改存 buffer；`task` 句柄仍在本地注册表（取消与退出必须够得着），
    **本进程的轮次仍走事件驱动**（不加轮询延迟），只有订阅别的 worker 的轮次才轮询；`start` /
    `cancel` / `is_running` / `shutdown` 改成 async（路由相应 `await`）。
  - 新增 `agent/circuit.py`：`CircuitStore` + 内存/Redis 实现。状态机仍只有 `middleware/fallback.py`
    一份（`_may_try` / `_note_primary` 改成 async），默认那份**仍是模块级单例** —— 各角色各 new
    一个中间件实例，状态跟着实例走就串不成一份了。
  - 装配：`api/app.py` lifespan 里建客户端并注入（**三样一起换**：只换一半更危险）；`config.py`
    与 `.env.example` 加 `REDIS_*` / `RUN_*`；`pyproject.toml` 加 `redis>=5`。
- **关键决策**：
  - **Redis 是可选件**：`REDIS_URL` 留空 = 与之前逐字相同的行为（现有用例语义一条没改）。
  - **降级优先于报错**：旁路状态存不进去不该弄丢一轮对话；第一次失败即整体降级（不再每条命令
    重试）。`REDIS_REQUIRED=true` 给多 worker 部署留了「宁可起不来」的口径 —— 静默退回进程内
    意味着「你以为共享的东西其实各 worker 一份」，比启动失败更难查。
  - **取消分两条路**：本进程的立即 `cancel`（用户不用等轮询）；跨 worker 的只能留标记，由持有者
    在 `_watch` 里轮询到后自己取消，外加丢掉那轮的缓冲（与本地取消对齐）。默认 0.2s 一轮。
  - **缓冲有上限与 TTL**（默认 2000 帧 / 1 小时），键全带前缀，不会无限堆积。
  - 已知窄缝写在 `RedisRunLock` 的 docstring 里：续期是「`GET` 看是不是我的 → `EXPIRE`」两条命令，
    理论上可能刚好过期并被别人抢走后、把**别人**的锁续一次（后果是接管推迟最多一个 TTL）；
    持有者进程被 kill 后锁最多存活一个 TTL（默认 120s）。
- **验证**：全量 `pytest` **972 条全过**（改前 951，新增 21 条）；`ruff check` 改动文件干净；
  `scripts/check_prompts.py` 不涉及（未改提示词）。替身见 `tests/fake_redis.py`（内存版 Redis +
  手推时钟），**两个 `RunHub` 实例共用一份 = 两个 worker**，于是跨进程语义在没装 Redis 的机器上
  也能验：跨 worker 追流、游标接回、第二个 worker 被拒、锁过期后能接管、另一个 worker 看得见
  running、**跨 worker 终止真的停住且丢掉缓冲**、帧号在截断后仍连续、缓冲 TTL 到期读作空闲、
  Redis 报错降级到进程内；熔断侧另钉了副本语义、跨实例共享、降级、以及「别的 worker 开的闸
  这一轮连主导都不撞」。`build_redis` 的四条路径（没配 / 连不上 / 必需 / 没装包）用注入的假
  工厂验。
  - **`redis` 包已装**（conda 环境 `aicoding`，实测 8.1.0），并用**真客户端**跑了一次
    `build_redis`：本机没有 Redis 实例，链接 1s 超时 → 记一条 warning → 返回 `None`
    （即「退回进程内」这条降级路径在真库上也是这个行为）。
- **遗留**：
  - **没有真 Redis 的端到端验证**：本机没有 Redis 实例，所以「连上之后」的读写、
    以及「两个进程各跑一个 worker」的真场景只在替身（`tests/fake_redis.py`）上验过。
    有实例之后建议手动冒烟一次：
    `REDIS_URL=redis://127.0.0.1:6379/0` 起服务 → 发一轮 → reload 页面看能不能接回。
  - **本次不含 sticky 路由**：沙箱句柄仍在各 worker 里（同一会话落到别的 worker 会另建一个容器，
    工作区靠命名卷共享），真要横向扩容还得配 sticky 或者「谁持锁谁干活」的约束（README 已写明）。
  - 熔断状态是**近似共享**：并发读改写会丢一次计数、探针可能被两个 worker 同时放出去 —— 它只是
    省等待的启发式，不影响正确性。

#### 仓库记忆：PR 行的状态按 Gitee 实况回填；没有要核对的 PR 就不再查 Gitee

- **动机**：用户要求 `MemoryMiddleware` 除了文件头，还维护「变更记录」里 PR 行的**状态** ——
  写着「待合并」的行，线上已经合并 / 已关闭的要改成实际结论；并且如果仓库记忆里**没有待合并的
  PR**，就完全不必查 PR 列表；闸门也跟着「没有待合并的 PR 就不拦」。
- **改动**：
  - `agent/memory_reconcile.py`：新增「变更记录」行扫描（`_PrRow` / `_row_of` / `_pr_rows` /
    `_is_change_section`，只扫标题以「变更记录」开头的那一节）、`rows_to_verify()`（列出要核对的
    编号）、`_states_by_number()`、`_restate()`（**只替换状态那一格**），并新增 `strip_legacy()`
    给「不必查 PR」的调用方（只摘两张老表，头行一个字不碰）。`reconcile()` 现在会回填状态，
    `Reconciled` 多一个 `restated`（这轮改了哪几个编号，供日志与断言）。状态词统一成模版口径
    `待合并 / 已合并 / 已关闭`（三个常量的值原来还是「待合入 / 已合入 / 已废弃」，与模版两套词）。
  - `agent/middleware/memory.py`：`refresh_memory` 读盘后先看 `rows_to_verify()` —— **没有要核对
    的 PR 就不调 `all_prs()`、不写盘**，返回 `pending=()`，于是 plan / coding 不拦（`rework`
    照旧是「没有待合并的 PR 才拦」）。**没写过记忆的仓库仍然查一次**（用户选的 1b：那一档没有
    任何记忆可依据，是闸门唯一还独立于记忆的地方）。
  - `tools/memory.py`：同一条纪律 —— 提交内容里没有要核对的 PR 时省掉那次查询，落盘前只摘老表 +
    盖时间戳；回执里会说明「N 条变更记录的状态已按实际结果更新」。
  - 口径同步（模型读的那几处）：`agent/prompts.py` 的「仓库记忆文件规则」与 `_MEMORY_WRITE_BACK`、
    `docs/repo-memory-template.md` 与 `sandbox/policies/repo-memory-template.md`（逐字一致）、
    `docs/workspace-directories.md` 里那段闸门说明。
- **关键决策**（用户拍的三点：1b / 2a / 一起做）：
  - **「要核对」= 状态不是「已合并 / 已关闭」的行**（含老格式里没写状态那一格的）——「不确定」
    不能当成「已经完事」，宁可多查一次。
  - **只改写着「待合并」的行，且只改状态那一格**：人工改过的结论不回改；线上状态认不出、或列表里
    没有这个编号时**保持原样**（不猜）；认不出状态字段位置的行只触发查询、不改写。状态那一格
    **加不加粗都认**（认的是这一格，不是排版），替换的是「状态值」本身 —— 与下一个字段之间的
    全角空格、以及同行后面的字段一个字节都不动。
  - **跳过查询的代价写在这里**：文件头的「最近 PR」保持原样（没查就不知道新值，也不写成「—」）；
    闸门判据从「Gitee 实况」变成「记忆里记着的待合并 PR」—— 人工在 Gitee 上开的 PR、或记忆漏记的
    PR 不再拦住 plan / coding 轮（这是省掉每轮一次 Gitee 往返换来的，用户已知悉）。
- **验证**：`npm` 无关，后端改动。全量 `pytest` **951 条全过**（改前 927，新增 24 条）；
  `ruff check` 改动文件干净；`scripts/check_prompts.py` **冲突 0 条**。新增用例覆盖：只扫变更记录节 /
  没写状态算要核对 / 没编号跳过、已合并与已关闭两向回填、仍开着不动、认不出的状态不动、
  人工写死的结论不回改、状态格不加粗也认且不吞全角空格、**只改状态也算「变了」**（否则盘上永远是旧状态）、回填幂等、
  `strip_legacy` 无老表时逐字恒等；闸门侧钉了「没有要核对的 PR 时 `all_prs` 一次都不被调用」
  （桩故意抛异常）、「线上已合入 → 回填且不拦」、「还开着 → 拦」、「没记忆仍查一次」。
- **遗留**：
  - 老格式（只写了 PR、没写状态那一格）的记忆每轮都会多查一次 Gitee —— 那一行认不出字段位置改不了，
    要等下一轮 coding / review 把这条改写成新格式（或人工补一格）才会停下。
  - 跳过查询那几轮不再刷新「最近 PR」；要看最新的就等一次有待核对 PR 的轮次。
  - 提示词与沙箱模版是**建会话时**读 / 推的：改完要**重启后端 + 新建会话**才生效。

#### 代码展示区上下铺满（去掉 60vh 上限，滚动收进代码块）

- **动机**：用户反馈右侧栏「代码」的文件内容框太小（一条条文只占中间一小块），要求**上下方向铺满**。
- **改动**（`frontend/src/RepoPanel.vue` + `style.css`，纯前端）：
  - 文件视图里把「太大 / 二进制」提示与 `<pre class="repo-code">` 包进新的 `.repo-file` 容器。
  - `.repo-panel` 加 `flex: 1`（撑满 `panel-body`），`.repo-file` 与 `.repo-code` 都加 `flex: 1; min-height: 0`；`.repo-code` **删掉 `max-height: 60vh`**。
  - 净效果：**文件头（`← 路径 大小`）固定在上面，正文吃掉剩下全部高度，滚动发生在代码块内部**（原来是整块面板跟着滚、代码只有 60vh）。
- **关键决策**：
  - **用 flex 撑满、不给 `max-height` 换个大数字**：面板高度是可变的（拖宽会换行、全屏会变成整窗），写死任何一个 vh 都会在某个高度下不贴合。
  - **`.repo-panel` 必须一起 `flex: 1`**：`.panel-body` 是列向 flex，父层不长大，子层的 `flex: 1` 就没有可比的高度（这是「铺满」能不能生效的关键一步）。
  - **目录视图不动**：只改文件视图；目录 / 面包屑仍按原来的方式滚（`.panel-body` 是 `overflow-y: auto`）。
- **验证**：`npm.cmd --prefix frontend run build` 通过（含 `vue-tsc`）。在 5173 真实页面上用 Playwright 量几何（不是看图）：
  - 打开 `deploy/nacos/README.md`：`panel-body` 高 **897**（`scrollHeight` 也是 897，说明没有外层滚动）、代码块 `top=101 / bottom=877 / h=775 / scrollHeight=2002` —— 正好从文件头下方铺到面板底部（底部 20px 是 `panel-body` 的 `padding-bottom`）。
  - 对照改前：`max-height: 60vh` 会把代码块卡在 **≈538px**，与面板高度无关。
  - 全屏态：`.right-panel.fullscreen` 是 `inset: 0`，面板高度等于窗口高度（本机同样 897），所以纵向铺满逻辑与上面一致；Esc 退出后类名回到 `right-panel`、按钮标题回到「全屏」、原先的宽度与标签状态照旧。
  - 目录视图回归：根目录 19 项、逐层进到 `.../product/repository/impl` 都正常渲染，`panel-body` 高度与 `scrollHeight` 相等（未出现异常滚动条或被裁切）。
- **遗留**：
  - **目录列表没做自己的滚动容器**：本仓最大的目录只有 13 项（远不够撑满面板），所以「超长目录由面板整体滚动」这条路没在真实页面上触发过；换到单目录几百个文件的仓库时，滚动会落在面板而不是列表上（能用，但不如文件视图那样「头部固定」）。
  - 代码块高度随面板走，**面板很矮时代码区也会很矮**（不再有 60vh 兜底）；窄屏靠拖拽宽度解决。

#### 右侧栏加「全屏」按钮（面板盖满窗口，Esc 退出）

- **动机**：用户要求右侧栏加一个全屏按钮 —— 看方案步骤 / 读代码时能让面板占满窗口。
- **改动**（`frontend/src/App.vue` + `style.css`，纯前端）：
  - 标签条里「收起/展开」按钮下面再加一个同款 32×32 方按钮，图标是**内联 SVG**（四角向外＝进入、四角向内＝退出；不用字体里的 ⛶ 这类符号，免得缺字变方块）。
  - 新增 `panelFullscreen` 状态 + `togglePanelFullscreen()`：进入时若面板是收起的先展开（否则「全屏」只剩一条 44px 条，看不出全屏）；全屏期间**隐藏拖拽热区**、内联宽度让位；**Esc 也能退出**（监听只在全屏期间挂，平时不留全局 keydown）。
  - `style.css`：`.right-panel.fullscreen { position: fixed; inset: 0; z-index: 20; border-left: none }`；`.panel-toggle-btn.active` 高亮当前态。
- **关键决策**：
  - **用 `position: fixed` 覆盖，不把宽度设成 100vw**：后者会把左侧会话栏与对话区挤出视口（它们都是 `flex-shrink: 0`），还得动拖拽宽度的状态。覆盖方案退出后原宽度原样回来 —— 实测退出时拖拽热区（`Value: 260`）确实回来了。
  - **全屏状态不持久化**：它是一次临时操作，刷新即退出（与「左栏收起不记、宽度记」那套偏好分开）。
  - **图标用 SVG**：跨平台稳，且能跟着 `currentColor` 变 hover/激活色。
- **验证**：`npm.cmd --prefix frontend run build` 通过（含 `vue-tsc`）。在 5173 用无障碍树实测：按钮在标签条第二位（`button 收起面板` 下面）；点一下 → 标题变**退出全屏（Esc）**、**拖拽热区消失**（说明 `fullscreen` 类真的生效）；再点一下 → 标题回到**全屏**、热区恢复（`splitter ... Value: 260`）。
- **遗留**：
  - **Esc 退出没被自动化验到**（CUA 的 `pressKey` 需要最新快照里的节点 id，两次都判 stale）；代码就三行（`keydown` → `Escape` → 关），你手点一下按钮进来、按 Esc 试一次即可。
  - 全屏时面板是**盖住**左侧栏与对话区的（不是把它们挤开）：这是有意的（看代码时更专注），要「并排更宽」那种就用拖拽改宽度。

#### 代码缓存搬到用户个人目录（默认 `~/.ai-coding-harness/repo_cache`）

- **动机**：用户指出右侧栏「代码」面板在本机拉的那份仓库缓存落到了**项目目录**里（`D:\python-project\ai-coding-harness\repo_cache`）—— 那是下载来的第三方代码，混在项目里既会被误当成项目文件，也会跟着项目被备份/同步走。要求缓存到**用户个人目录**，用的时候从本地加载。
- **改动**：
  - `src/ai_coding_harness/config.py`：`repo_view_dir` 的默认值从相对路径 `"repo_cache"` 改成**用户目录** `~/.ai-coding-harness/repo_cache`（用 `Field(default_factory=_default_repo_view_dir)` 算出来 —— 这是跟着机器走的路径，写不成字面量）。`REPO_VIEW_DIR` 照旧可覆盖；**给了相对路径仍按当前工作目录解析**（`repo_cache.cache_root()` 会转绝对路径，上一轮那个「相对路径被解析两次」的坑仍被盯着）。
  - `src/ai_coding_harness/repo_cache.py`：模块说明里把默认位置写清（不是项目目录），并保留那条 cwd 教训的注释。
  - `README.md`：代码面板那条把默认写成的路径。
  - **删掉了项目目录里那份旧缓存** `D:\python-project\ai-coding-harness\repo_cache\`（≈700KB，含之前错位的那份嵌套克隆；纯缓存、可重新下载）。删除前核对过：解析后的绝对路径正好是工作区下的 `repo_cache`，不是工作区根。
- **关键决策**：
  - **按用户目录、不按系统规范**：Linux 的规矩是 `~/.cache/...`、Windows 是 `%LOCALAPPDATA%`，但那要维护两套规则、排障时还得多想一步；`~/.ai-coding-harness/…` 一套写法两个平台都好找（用户也选了这条）。
  - **默认值用工厂算**：路径随机器变（还要吃 `HOME`/`USERPROFILE`），所以不能写成 `log_dir` 那种字面量默认值。
- **验证**：`Settings().repo_view_dir` 实测为 `C:\Users\52387\.ai-coding-harness\repo_cache`，`repo_cache.cache_root()` 解析一致；`REPO_VIEW_DIR=D:/x/cache` 覆盖仍有效；`tests/test_repo_cache.py` / `test_repo_routes.py` 等 **81 条全过**（它们用临时目录，不受默认值影响）；全量 `pytest` **927 条全过**；`ruff check` 改动文件干净。
- **遗留**：
  - **要重启后端**才用新默认值；重启后第一次点「同步」会在用户目录下重新 clone 一份（旧的已删，所以是全新下载）。
  - 缓存仍然**不会自动清理**（多仓库会持续占磁盘）：现在要清就是删 `~/.ai-coding-harness/repo_cache` 下对应目录。
  - 若把 `HOME`/`USERPROFILE` 指到别处（少见），缓存会跟着走 —— 这是预期行为，写绝对路径到 `REPO_VIEW_DIR` 可钉死。

#### 收起 / 展开按钮挪到两侧栏顶部，做成 32px 方按钮

- **动机**：用户反馈右侧栏的收起按钮在**最下边**（`margin-top: auto` 顶到底），不好找；要求挪到上边、做大一点，左侧一起改。
- **改动**（`frontend/src/App.vue` + `style.css`，纯前端）：
  - 右侧：按钮从竖标签条**最下边**挪到**最上面**（`v-for` 之前的第一项），去掉 `margin-top: auto`。
  - 两侧统一成同一个 `.panel-toggle-btn`：**32×32**（原来右栏 26、左栏是无边框的 16px 字符）、带边框与圆角、悬停变强调色；方向语义不变（箭头指向面板会往哪边缩：左栏展开 `‹` / 收起 `›`，右栏展开 `›` / 收起 `‹`）。
  - 左侧收起态：rail 顶部也放同一个按钮（原来只有竖排「会话」文字可点）；「会话」竖排文字保留（仍是可点的第二入口），「＋」跟在它下面。
- **验证**：`npm.cmd --prefix frontend run build` 通过（含 `vue-tsc`）；在 5173 上用无障碍树确认两侧按钮都在**最上面**（左栏首个节点 `button › 展开会话栏`、右栏标签条首个节点 `button › 收起面板`）。
- **遗留**：无（像素观感请你自己扫一眼：32px 是否够显眼、箭头方向是否符合直觉）。

#### 初次加载恢复上次的页面；左栏每次默认展开

- **动机**：用户要求两件事 —— ① 界面初次加载时**恢复上次打开的页面**；② 左侧会话栏**默认加载**（展开），而不是继承上次收起的状态。
- **改动**：
  - `frontend/src/App.vue`：新增两个 localStorage 键 —— `ui.lastSessionId`（上次打开的会话）与 `ui.newChatPending`（上次停在「新建对话」页）。`openSession()` 记下会话、`startNewChat()` 清掉会话并记下「新建对话」、新建成功与删除当前会话时同步维护。`onMounted` 从 `onMounted(reloadSessions)` 改成「先 `reloadSessions()`，再 `restoreLastView()`」：有会话就 `await openSession(id)`（**复用现成那套恢复逻辑**：历史 + 方案 + 「本轮还在跑就接回直播流」+ 工作区准备），上次停在新建对话页就回到那一页。
  - 恢复失败（会话已删 / 已失效）**清掉记录、复位到欢迎页并给一条轻提示**（「上次打开的会话已失效，已回到列表」）—— 不让用户停在打不开的页面上，也不每次刷新再试一遍同一个死会话。
  - `frontend/src/splitter.ts`：`usePanelWidth` 新增 `persistCollapsed`（默认 `true`）；左栏传 `false` —— **收起状态不记**（每次加载都是展开的），宽度仍然记住。右栏保持记住。
  - localStorage 读写都包了 try/catch：隐私模式 / 被禁时读不到、写不进都只当「没记住」，不影响功能。
- **关键决策**：
  - **恢复直接复用 `openSession`**，不另写一套「轻量恢复」：那一条路径已经把「历史 / 方案 / 接回直播 / 工作区准备」都处理好了，另写一套必然漂（何况「刷新接回直播」本来就是它设计的用途）。
  - **左栏不记收起、右栏记**：左栏是导航（每次打开都该看到会话列表），右栏是内容面板（收起往往是刻意的）。
  - **失败要留痕**：清记录 + 一条 6 秒自动消失的提示，比静默跳回欢迎页更容易让人明白发生了什么。
- **验证**：
  - `npm.cmd --prefix frontend run build` 通过（含 `vue-tsc`）。
  - **在真前端（5173）用新标签页模拟「初次加载」**：先在一个标签里打开会话（`你好`）并把左栏**收起**；再开一个新标签 → 它自动回到该会话（历史 1128 项与右侧计划面板 8/8 步都渲染出来），左栏是**展开**的（显示「收起会话栏」与会话列表），宽度 235px 也被记住（该值是被窄屏 `45vw` 夹取后的结果 —— 夹取也顺带验到了）。
- **遗留**：
  - 恢复会**自动触发一次工作区准备**（`POST /sessions/{id}/workspace`）：沙箱不在时要等几秒到十几秒（界面显示现成的「代码工作区准备中…」）。与「点一下会话」是同一条路径，不新增等待。
  - localStorage 那几个键（`ui.lastSessionId` / `ui.newChatPending` / splitter 的两组）都是**按浏览器**存的：换浏览器或清站点数据就回到欢迎页。多标签页同开时各自维护自己这边的键（最后一个动作覆盖），没做跨标签同步（`storage` 事件），暂不做。

#### 左右侧栏可收起、可拖宽；右侧标签改成竖排（加标签只改一个数组）

- **动机**：用户要三件事 —— 左侧会话栏能收起；右侧的标签**竖着排**（现有计划 / 代码，以后加第三个只需往下加）；左右两条侧栏都能**手动调宽**。
- **改动**：
  - `frontend/src/splitter.ts`（**新增**）：左右共用的宽度逻辑 —— 指针拖拽（`setPointerCapture`，拖动期间给 `body` 挂 `resizing` 免得选中文字）、**双击复位**、**方向键 ±16（Shift ±48）**、宽度与收起状态写 localStorage；夹取上限跟窗口走（`min(上限, 45vw)`），窄屏不会把中间的对话列挤没。
  - `frontend/src/App.vue`：左栏加 rail（竖排「会话」+「＋」）与收起按钮；右侧从「横标签 + 另一套折叠 rail」两套状态改成**一套** —— `[竖标签条][内容]`，「收起」＝收内容、标签留着（点标签就展开到那一个）。标签由 `panelTabs` **数组**驱动（`[{计划, 状态点?}, {代码}]`），加第四个标签只往数组里加一项；两条 6px 拖拽热区带 `role="separator"` + `aria-valuemin/max/now`；`resetPlan()` 不再重置面板收起状态（现在是记住的偏好，切会话不会把它拉回来）。
  - `frontend/src/style.css`：竖标签条 / rail / 拖拽热区 / 内容区（滚动归内容区，标签条不跟着滚）；删掉旧的横标签 `.panel-head`、旧 `.panel-rail` 与窄屏写死宽度那两条规则（宽度已由拖拽 + 45vw 夹取接管）。
- **关键决策**：
  - **收起只收内容**：标签条永远在，点标签即展开到该标签 —— 原来「折叠 rail」与「横标签」两套状态并存，用户得先猜点哪儿。
  - **竖排文字只加在文字上**（`.panel-tab-text`）：写在按钮上会让 flex 主轴跟着旋转，标签旁的色点位置就不可控。
  - **宽度与收起状态持久化**（`ui.sidebar.*` / `ui.right.*`）：隐私模式下写失败只影响记忆，不影响功能。
- **验证**：
  - `npm.cmd --prefix frontend run build` 通过（含 `vue-tsc`）。
  - **用无障碍树在真前端（5173，你那个 dev server）上走了一遍**：左栏收起 → 只剩「会话」「＋」，再点「会话」展开；右侧竖标签「计划 / 代码 / ›」在，切到「代码」后出现分支下拉（master + 7 支）、「同步」按钮、「上次同步 2026-09-26 15:50」、面包屑与目录树（带文件大小）；两条 separator 的 `Value` 分别是 260 / 360。
  - 走查顺带抓到并修掉一个真 bug：separator 只给了 `aria-valuenow` 没给 `min/max`，无障碍层按百分比处理，260 被截成 **100**；补上后正确显示 260 / 360。
  - 顺带确认上一批那个缓存修复**在真机生效**：面板显示「上次同步 2026-09-26 15:50」，分支与文件树都读得出来。
- **遗留**：
  - **拖拽与方向键这两条路径没有自动化验证到**：CUA 的无障碍快照是**增量 diff**，热区的节点 id 拿不到（点它会被判 stale）。手感请手动试：拖那条 6px 缝隙、点一下缝隙再按 ←/→、双击缝隙复位。
  - 我用应用内浏览器开了个标签停在 5173 做走查，**没有关闭 API**，你顺手关掉即可。

#### 代码字号抬一档：面板 12→14px，聊天代码 13→14px

- **动机**：用户反馈「代码的字体有点小」。查下来确实偏小、而且**两处不一致**：代码面板正文是 12px，聊天里的代码块与行内代码是 13px。
- **改动**（`frontend/src/style.css`，纯样式）：
  - `:root` 新增 `--code-size: 14px`，**面板正文**（`.repo-code`）与**聊天里的代码**（`.markdown code`，代码块里的 `code.hljs` 也走这条）共用它 —— 以后再调只改一处；行高 1.5 → 1.6。
  - 面板里的目录条目 / 路径 / 面包屑 / 分支下拉 / 同步按钮 / 提示文案：12px → **13px**；次要信息（文件大小、上次同步时间、提示条、错误文案）：11px → **12px**。
  - 右侧栏宽度 340 → **360px**（窄屏那条媒体查询 240 → 260px）：字号变大后长行更容易横向滚动，宽 20px 换回一点可读性。
- **验证**：`npm.cmd --prefix frontend run build` 通过（含 `vue-tsc`，37 个模块）；纯 CSS 改动，后端与 `pytest` 不受影响（未重跑）。样式表里 `--code-size` 只出现在 `:root` 与那两处使用点上。
- **遗留**：14px 是按「面板 + 聊天一起统一」定的；嫌大或嫌小改 `--code-size` 一处即可（次要信息那几档是写死的，要一起调再说）。面板宽度的 20px 是顺手的，不想要就回 340。

#### 代码缓存落到了嵌套目录：相对路径被「换 cwd 跑 git」解析了两次

- **动机**：用户报第二处错 —— `fatal: destination path 'repo_cache\zzpfordevelopment\wanqi-mall' already exists and is not an empty directory.` 看磁盘发现克隆其实**成功了**，只是落在了
  `repo_cache\zzpfordevelopment\repo_cache\zzpfordevelopment\wanqi-mall`（连 551KB 的 pack 都在）。
- **真因**（两个，都在 `repo_cache.py`）：
  1. **相对路径被解析两次**：`REPO_VIEW_DIR` 默认值 `repo_cache` 是相对路径，而每条 git 命令都是「换 `cwd` 再跑」（克隆用 `cwd=目录父级`），于是作为**目标**传进去的相对路径被 git 相对那个 cwd 又拼了一次 → 落到嵌套目录。紧接着按 cwd 找 `HEAD` 找不到（`is_cloned` 为假）→ 又克隆一次 → 嵌套路径已在 → 报「already exists」。**两处各自都对，合起来错。**
  2. **清残留会静默失败**：`shutil.rmtree(directory, ignore_errors=True)` 在 Windows 上删不掉 git 的**只读**打包对象（实测属性 `-ar--`），于是「先清掉半成品再克隆」这一步没有真的清掉，兜底也变成报「already exists」。
- **改动**（`src/ai_coding_harness/repo_cache.py`）：
  - `cache_root()` 把配置里的路径解析成**绝对路径**（`expanduser().resolve()`），并把这个坑写进注释 —— 换 cwd 跑命令时，相对路径会被解释两次。
  - 新增 `_remove_tree()` 替代 `shutil.rmtree(..., ignore_errors=True)`：先整棵清掉只读位再删；**真删不掉就抛可读错误并指出路径**（让人手动处理），而不是留半个仓库继续往下跑。克隆前清残留、克隆失败后清半成品都改用它。
- **验证**：
  - 新增两条回归用例：`test_a_relative_cache_dir_is_resolved_once`（`REPO_VIEW_DIR=cache` + chdir，断言落点是 `<cwd>/cache/acme/demo`、**没有**再套一层同名目录、分支能列出来）与 `test_a_leftover_directory_with_readonly_files_is_cleared`（先造一个含只读文件的残留目录，断言能清掉并克隆成功）。**这两条在修前都会红** —— 之前那批用例全用绝对临时路径，所以一直没碰到。
  - 全量 `pytest` **927 条全过**（上一批 925，净增 2）；改动文件 `ruff check` 干净。
- **遗留**：
  - 真机上那份**错位的克隆**要手工删：`repo_cache\zzpfordevelopment\repo_cache\`（只占几百 KB，删掉即可；下次点「同步」会克隆到正确位置 `repo_cache\zzpfordevelopment\wanqi-mall`，旁边一个 `wanqi-mall.json` 记上次同步时间）。
  - 这一类「相对路径 × 换 cwd」的坑只有真机验证能抓到（单测当时全用绝对路径），与上一版 `create_subprocess_exec` 那个是同一批教训：**新功能上线前先拿真环境点一遍**。

#### 代码面板「同步失败 500」：git 改走线程，不再依赖事件循环的子进程支持

- **动机**：用户报「界面处的代码是空的，显示同步失败 500」。`api.log` 里一行就够定位：
  `repo.py:100 → repo_cache.py:341 → repo_cache.py:279 raise NotImplementedError`。
- **真因**：`repo_cache._run` 用的是 `asyncio.create_subprocess_exec`，而它要求**事件循环支持子进程**；Windows 上只有 `ProactorEventLoop` 支持。本项目的后端在 Windows 上**故意**用 `SelectorEventLoop`（`main.selector_loop_factory`：uvicorn 0.36+ 在 Windows 默认 Proactor，会忽略事件循环策略，而 psycopg 异步要 Selector），于是这条调用在真机上必然抛 `NotImplementedError`，路由只捕 `RepoViewError`，于是 500。**这是我上一版没在真机验证留下的洞**：单测跑在 pytest 自己的 Proactor 循环上，永远碰不到。
- **改动**（`src/ai_coding_harness/repo_cache.py`）：`_run` 换成 `asyncio.to_thread(subprocess.run, …)` —— 与事件循环无关，两个平台同一条代码路径；超时改由 `subprocess.run(timeout=…)` 负责（`TimeoutExpired` → 可读文案），`OSError` 仍映射成「git 启动失败」。模块注释里把这段来龙去脉写清楚，免得以后有人「优化」回 `create_subprocess_exec`。
- **验证**：
  - 新增回归用例 `tests/test_repo_cache.py::TestEventLoopIndependence`：**在一个真的 `SelectorEventLoop` 里**跑一次完整同步（正是后端那种循环）。改前必红（`NotImplementedError`），改后绿。
  - 另拿真仓库跑了一次探针（会话 `78780d7a…` 绑的 `zzpfordevelopment/wanqi-mall`，凭据从库里取、在 Selector 循环里跑）：git **确实被执行**（错误已经是 git 自己的输出），这一侧只差网络 —— 探针所在环境连不上 `gitee.com:443`（`Could not connect to server`），所以 Gitee 的认证那一段仍需你在真机上点一次「同步」确认。报错文案可读、且已按令牌脱敏。
  - 全量 `pytest` **925 条全过**（上一批 924，净增 1）；改动文件 `ruff check` 干净。
- **遗留**：
  - **重启后端**才会加载新代码（工具与路由都是进程内对象）；重启后点「同步」应能跑通，失败也会是 409 + 一句人话，而不是 500。
  - 线程方案下**超时杀不掉子 git 进程**（`subprocess.run` 只杀它直接拉起的那个）：`git fetch` 超时时可能留下一个孤儿 git 进程，几分钟后自己结束。真要收干净得自己 `kill` 进程组，暂不做。

#### 仓库记忆定型四节：分析结论进记忆，analysis 轮也能写回

- **动机**：用户把记忆的内容口径一次说清 —— 记忆 = **分析出来的仓库信息 + 变更记录**；`repo-bootstrap-analysis` skill 的结论不再另存文件，而是**沉淀进记忆**；顺带给 analysis 轮开记忆写权限（否则「只问不做」的那一轮分析完就丢了，而它产出的正是记忆的正文）。上一版方案里那个「仓库内单独放一份分析文档」的做法据此作废。
- **改动**：
  - `sandbox/policies/repo-memory-template.md` + `docs/repo-memory-template.md`（逐字一致）：从五节改成**四节** —— 一、**仓库基本信息**（来自按 skill 做的分析：技术栈 / 目录结构 / 启动方式 / 测试构建 / 已知约定，并写明「变了才改」，别每轮重写）／二、**变更记录**（PR + 状态，格式不动）／三、**当前缺口**（按模块列未完成的功能，下一轮该做什么的唯一出处；原「当前进度」改名而来）／四、**关键决策**。原「约定与注意事项」并进第一节的「已知约定」。
  - `src/ai_coding_harness/tools/register.py`：`ANALYSIS_FORM` 增加 `save_repo_memory` —— 分析轮是**唯一带记忆写回的只读形态**（仓库与 PR 仍全是只读；记忆在 StoreBackend 上，`policy_for("analysis")` 的沙箱写根仍是空的，两边不冲突）。
  - `src/ai_coding_harness/server/service.py`：`_MEMORY_WRITE_FORMS` 加 `ANALYSIS_FORM`，于是 analysis 收尾也会按 Gitee 实况打底刷新记忆的文件头。
  - `src/ai_coding_harness/agent/prompts.py`：新增 `_INTENT_DIRECTIVES["analysis"]`（本轮只读分析 + **收尾把「这个仓库是什么」沉淀进记忆** + 明确**不写变更记录**，因为这一轮没有改动、也没有 PR）；`_READONLY_BOUNDARY` 从「没有 `save_repo_memory`」改成只列 PR 写工具，并新增只给 analysis 的 `_ANALYSIS_MEMORY_WRITE` 段；基础提示词的记忆规则改成「写回只在 **coding / review / analysis** 三类轮的收尾」，并补上**首次建档**口径（没有注入记忆块＝这个仓库还没写过记忆，按模版从头写一份）；「当前进度」的提法全部改成「当前缺口」；skill 那四处点名改成**结论沉淀进记忆的两节**（不再指向独立文件）。
  - 口径随动：`tools/memory.py`、`agent/memory_reconcile.py`、`agent/middleware/memory.py` 的注释、`docs/workspace-directories.md`（补一条「这张表只管沙箱目录，记忆在 StoreBackend 上、能写它的形态有哪四个」+ skill 说明）、`README.md`（意图表 analysis 行、只读形态的写工具口径）、`scripts/check_prompts.py` 的 C5 说明。
- **关键决策**：
  - **「当前缺口」是记忆的一部分，不是独立文档**（用户口径）：它是分析结论的另一半（这个仓库现在缺什么），而「下一轮该做什么」正指着它 —— 放记忆里自动注入，比让每轮多读一个文件更稳。
  - **basic info 只在真变了时改**：技术栈 / 目录 / 启动测试方式每轮重写只会把上下文浪费在复述上，还会覆盖掉别人写的细节。提示词里因此写死「变了才改」。
  - **analysis 只多一件写工具**：`save_repo_memory` 走 StoreBackend，不进沙箱、不进目标仓库，所以「只读形态不碰仓库」这条边界不变；`create_pr` / `comment_pr` / 写文件仍然一律没有。
  - **analysis 不写变更记录**：这一轮没有代码改动、也没有 PR，而「一次改动一定带着一个 PR」这条纪律刚在上一条改动里立稳 —— 让它写条空条目等于把那个口子又开回去。
- **验证**：全量 `pytest` **924 条全过**（上一批 920，净增 4）；改动文件 `ruff check` 干净；`scripts/check_prompts.py` 8 份提示词、**0 条冲突**；两份记忆模版按字节比对一致。新增/改写的用例：analysis 的工具子集（有 `save_repo_memory`、没有 PR 与文件写工具）、analysis 提示词里「唯一的一处写入」段与 qa 的对照、analysis 意图指令（含「不写变更记录」）、写回轮次四处指令（coding / review / rework / analysis）都带「收尾写回 + 当前缺口 + 本轮只写一次」、轮末打底把 analysis 归入写回那组、模版四节的断言。
- **遗留**：
  - **真机未验证**：analysis 轮要重启后端 + 新建会话才生效；验证点是「问一句『这仓库是做什么的』→ 回复里有结论 → 记忆的第一节被写出来」。
  - **旧记忆不迁移**：用户会自己删掉那三份旧记忆、由模型重建 —— 正好一次性对齐四节格式；删掉之后**下一次 coding / review / analysis 收尾**才会落盘（平台的刷新只盖文件头，不凭空建档）。
  - 「关键决策」独立成节是用户选的（1b），于是它和变更记录里的「变更内容」有可能各说一遍同一件事；真觉得重复再合并。

#### 变更记录格式：每条都带 PR 与状态，取消「没有 PR」这一档

- **动机**：用户指出模板里那两个口径不对 —— ① 变更记录的 PR 那行写着「未提 PR 的改动写「未提 PR」」，可**写码与提 PR 是同一条流程**，留这一档等于给模型一个「这轮可以先不提 PR」的正当理由（真实记忆里就这么被用过：`wanqi-mall` 有一条「已推送、PR 待人工创建」，更早的 `java_test_demo` 里干脆整段 `- **PR**：无`）；② 每条变更记录缺 **PR 状态**，而 PR 后来合没合上只有人知道，需要留一格人工修订的位置。
- **改动**：
  - `sandbox/policies/repo-memory-template.md` + `docs/repo-memory-template.md`（两份逐字一致，测试钉着）：PR 那行改成 `- **PR**：{[#31](链接)}　｜　**状态**：{待合并 | 已合并 | 已关闭}`，并在条目下面补一段说明 —— 一定带着一个 PR、**没有「没有 PR」这种写法**；本轮 PR 没建成时**这条变更记录不写**（失败与原因写进「当前进度」和回复）；`状态` 写回时一律填 `待合并`，**之后由人工改**；平台只维护文件头那一行。
  - `src/ai_coding_harness/agent/prompts.py` 两处口径随动：基础提示词的「仓库记忆文件规则」（每条改动写清 PR 编号 / 链接 / **状态**）与收尾写回指令 `_MEMORY_WRITE_BACK`（**状态一律先填「待合并」**；PR 没建成时**不要**写这条变更记录，也不要写「没有 PR」这种条目）。
  - `tests/test_memory_reconcile.py` 新增「模板里有 PR 状态行 + 之后由人工改 + PR 没建成不写 + `未提 PR` 不许回来」；`tests/test_prompts.py` 新增「三条写回指令都带状态口径，基础提示词同口径」。
- **关键决策**：
  - **状态用词取 Gitee 那套**：`待合并 / 已合并 / 已关闭`（用户在需求里一直用「待合并」）。代码内部 `memory_reconcile.PENDING/MERGED/DISCARDED` 的「待合入 / 已合入 / 已废弃」只出现在实现与日志里、不落记忆，故本次**没动**它 —— 要统一再单独做。
  - **状态不做自动化**（用户明确要求人工改）：平台每轮确实查得到 PR 实况，但它只用来自动回填**文件头**的「最近 PR」；条目里的状态是人写给人看的，自动回填会把「当时算的状态」和「事后的事实」混在一起。
  - **取消「没有 PR」档，且给出失败时的出口**：光删掉这一档还不够 —— 模型遇到 PR 建不出来时得有地方写，所以明确「失败写进「当前进度」与回复」，而不是让它编一个 PR 编号或写一个占位条目。
- **验证**：`tests/test_memory_reconcile.py` + `tests/test_prompts.py` **134 条全过**；全量 `pytest` **920 条全过**；改动文件 `ruff check` 干净；`scripts/check_prompts.py` 8 份提示词、**0 条冲突**；两份模板按字节比对仍一致。
- **遗留**：
  - **已有记忆里的旧条目不改写**：三份真实记忆（`wanqi-mall` / `java-for-test` / `java_test_demo`）里那些没有状态、或写着「PR：无」的历史条目保持原样 —— 它们是发生过的事实，重写反而失真。新写的条目按新格式走。
  - 状态靠人工改，所以**它迟早会过期**（人工合并后忘了改回去）。要收口的话有两条路：平台每轮按 Gitee 实况自动回填条目状态（与「人工改」这条要求冲突，要先定谁优先），或给一个「状态复核」的提醒。现在都不做。

#### 右侧栏「代码」面板：宿主机自己 clone 一份仓库，切分支只是换个 ref

- **动机**：用户要「界面右侧栏能看代码，clone 下载、之后 fetch 更新、可以切分支 —— **不必经沙箱**」。这条要求本身就点出了问题：代码一直只存在于沙箱里（`/ai_coding/project/`），而沙箱会没（TTL 到期回收、会话结束销毁、首次创建失败），那时连「这个仓库长什么样」都看不到；而且从沙箱里往宿主机搬文件，等于给一个随时会消失的东西再加一条传输链。
- **改动**：
  - `src/ai_coding_harness/repo_cache.py`（**新增**，约 380 行）：宿主机侧的只读仓库缓存。**裸仓**（`git clone --bare`）+ 只读查询 —— 克隆/更新 `sync()`、分支 `list_branches()`（默认分支取裸仓 HEAD）、目录 `tree()`（`ls-tree -l -z`，实测记录之间用 NUL、记录内用 TAB，名字带空格与中文都稳）、文件 `read_file()`（先 `cat-file -t/-s` 判类型与大小，再 `cat-file blob` 取正文）。
  - `src/ai_coding_harness/api/routes/repo.py`（**新增**）+ `api/schemas.py` + `api/app.py`：四个接口 `GET /sessions/{id}/repo`（状态 + 分支，不联网）、`POST …/repo/sync`（没有就 clone、有就 fetch）、`GET …/repo/tree`、`GET …/repo/file`。
  - `src/ai_coding_harness/config.py`：`REPO_VIEW_ENABLED` / `REPO_VIEW_DIR`（默认 `repo_cache`，与 `log_dir` 同口径按 CWD 解析）/ `REPO_VIEW_GIT` / `REPO_VIEW_TIMEOUT_SECONDS`（默认 300，首次 clone 可能很慢）/ `REPO_VIEW_FILE_MAX_BYTES`（默认 1 MiB）。
  - 前端：新增 `frontend/src/RepoPanel.vue`（分支下拉 + 同步按钮 + 面包屑 + 惰性目录树 + 文件视图，高亮复用 `highlight.js`）；`App.vue` 的右侧栏从「只在有方案时才出现的计划面板」改成**计划 / 代码两个标签**的常驻面板（折叠态是竖排标签，点哪个展开到哪个）；`api.ts` 加四个接口；`style.css` 加代码面板样式；`markdown.ts` 导出 `renderCode()`（高亮 + DOMPurify，与正文同一条底线）。
  - 顺手做的两件（方案里说过）：`start.bat` / `start.sh` 各加一段**只警告**的 git 自检；计划步骤里的「涉及文件」变成可点按钮，点一下切到代码标签并打开那个文件（`/ai_coding/project/<repo>/…` 这类沙箱路径会被剥成仓库内相对路径）。
  - `README.md`：补上四个新接口、「代码面板」这条能力、`rework` 意图，并把意图表里几行过期的工具清单（plan 的增删改查、review 的写回）改回与 `tools/register.py` 一致。
- **关键决策**：
  - **裸仓 + `git show`，不是「克隆到工作区再 checkout」**：切分支退化成「换个 ref 参数」，服务端**零状态** —— 两个标签页能各看各的分支，也没有脏工作区、并发 checkout 打架。读操作不需要锁（`git fetch` 只在对象全部落盘后才动 ref）；只有 clone / fetch 之间互斥（`asyncio.Lock`，按 `owner/repo`），并发两次同步只会克隆一次（用例钉了）。
  - **显式 refspec 是必须的**：本机实测 `git clone --bare` **不写** `remote.origin.fetch`（为空），不带 refspec 的 `git fetch` 只更新 `FETCH_HEAD` —— 分支列表会永远停在克隆那一刻。故 `fetch --prune origin '+refs/heads/*:refs/heads/*'`，远程删分支也跟着消失。
  - **令牌不进 URL**（硬要求）：clone 的 URL 会被 git 记进 `remote.origin.url`，拼令牌等于写在磁盘上。改成经**环境里的 git 配置**注入（`GIT_CONFIG_*` + `http.https://<host>/.extraheader`，git ≥ 2.31），于是不进 URL、不进 argv、也不落 `.git/config`；用 `GIT_CONFIG_*` 而不是 `-c` 是因为 `-c` 的值会出现在命令行参数里（同机器其他进程读得到）。另加一层：git 的 stderr 回给调用方前按令牌及其 base64 替换（用例覆盖），`GIT_TERMINAL_PROMPT=0` 保证缺凭据时直接失败而不是挂住。
  - **路径穿越在结构上就不可能**：用户给的路径从不落到文件系统，只喂给 git 去树里查（实测 `git rev-parse --verify 'master:../readme.md'` 被 git 拒掉）；外面再加一层 `..` / 绝对路径 / 反斜杠 / NUL 的字面拒绝，`ref` 必须在分支白名单里（顺带挡住 `-` 开头的参数注入），`owner` / `repo` 过字符集才拿去拼磁盘路径。
  - **一次只发一层目录**：`ls-tree` 逐层展开，不把整棵文件树一次拉下来（大仓库会几万条）；文件超上限（1 MiB）与二进制（含 NUL）**只回标记与大小**，不把正文塞进 JSON —— 前端照实说「太大 / 二进制」，不假装能显示。
  - **缓存按 `owner/repo` 一份**：平台本来就「一仓库一个活跃会话」，多余的隔离只会重复下载；旁边一份 `<repo>.json` 记上次成功同步的时间（写失败只记日志，不影响浏览）。
  - **只在打开「代码」标签时才下载**（用户选的口径）：建会话不额外等几十秒；打开时先给状态、没缓存就下（带「同步中…」），之后每次打开都是秒开，点「同步」才 fetch。
- **验证**：
  - 新增 `tests/test_repo_cache.py`（30 条）与 `tests/test_repo_routes.py`（13 条），都是**真 git**：夹具在 `tests/git_repo.py`（临时仓库 + 带空格/中文的目录 + 二进制 + 大文件），没有 git 的机器整文件 `skipif`。覆盖：首次克隆 / 二次 fetch 认出新分支与新提交、并发同步只克隆一次、克隆失败不留半成品、**挪走远程后读操作照样可用**（证明读不联网）、fetch 真的会去远程（证明不是假装的）、目录与文件（含分支不同内容不同）、太大 / 二进制 / 目录 / 不存在的路径、路径与 ref 的各类注入、`_scrub` 与「令牌不进 `.git/config`」、没装 git 的文案；路由层覆盖状态 / 同步 / 目录 / 文件 / 409 文案 / 503（没 git）/ 404（开关关掉、会话不存在）。
  - 全量 `pytest`：**917 条通过、1 条既有失败**（见「遗留」第一条，与本次改动无关）；`ruff check` 改动文件干净；`scripts/check_prompts.py` 0 条冲突；前端 `npm.cmd --prefix frontend run build` 通过（含 `vue-tsc`，37 个模块）。
- **遗留**：
  - **仓库里有一处与本改动无关的红灯**：`docs/repo-memory-template.md`（今天 12:20 被改过）与 `sandbox/policies/repo-memory-template.md` 的第 28 行不再逐字一致 —— docs 那行是 `- **PR**：{[#31](链接)；}{状态(open,closed,merged) }`，sandbox 那行是 `- **PR**：{[#31](链接)；未提 PR 的改动写「未提 PR」}`。`tests/test_memory_reconcile.py::test_the_two_template_copies_are_identical` 因此失败。**本次没动这两个文件**（那是别人的改动，且沙箱里那份才是真正生效的），等确认保留哪一版再对齐。
  - **真机未验证**：私有仓库的认证走的是 `http.extraHeader`（本机拿不到能验证的 Gitee 网络），要重启后端 + 打开「代码」标签看首次 clone 是否成功；公开仓库可先拿它验证浏览 / 切分支 / 同步。
  - 面板**只看不搜**：没有按路径搜索、没有 diff、没有历史提交列表（都不在本次口径里）。文件大小上限 1 MiB，超了只提示。
  - 缓存**不会自动清理**：仓库越看越多、磁盘只增不减；要清就是删 `repo_cache/` 下对应目录（下次打开会重新下）。也没设「多久没看就踢掉」的过期策略。
  - 浅克隆（`--filter=blob:none` / `--depth`）没做：大仓库首次 clone 慢就是这个原因，真觉得慢再加开关。

#### 新增意图 `rework`：按评审意见改代码，闸门两头都管

- **动机**：用户提出「要在审查结论之后**改代码**」这条路上被闸门夹住了。实测现有判定（拿真词表跑）：`按评审意见修改一下` → `coding` → 走 orchestrate 形态 → **有未合入的 PR 就拦下本轮**，而它要改的正是那份还开着的 PR —— 等于把自己永久挡在门外；`根据审查结论把代码改了` / `评审提的几个问题都修一下` → `review`（**跑去审，不改**，因为「评审 / 审查」是 review 的强口令，而 review 的排除词只挡得住「修改 / 修复 / 修正」这几种写法）。用户进一步要求：**没有待合并的 PR 时直接拒绝这次修复** —— 确实，那时没有可修的对象，模型拿不到审查原文，只会空转或凭记忆编一份「评审意见」出来改。
- **改动**：
  - `src/ai_coding_harness/server/intent.py`：`TASK_TYPES` 增加 `rework`（并导出 `REWORK_INTENT` 常量）；`_RULES` 增加一条**排在 `review` 之前**的规则；`_INTENT_SPECS` 与 `_BOUNDARY` 各补一条（rework vs coding / rework vs review 的分界）。
  - `src/ai_coding_harness/agent/profiles.py`：新增 `FORM_ALIASES = {"rework": "coding"}`，别名在 `_PROFILES` 建好之后挂上**同一个对象** —— 装配与 coding 一字不差（同一批工具、同一段角色段、同样三个子智能体），差别只在闸门；真要给 rework 别的工具，就得摘出别名、单开形态。
  - `src/ai_coding_harness/agent/middleware/memory.py`：闸门从「形态级一刀切」改成**形态决定挂不挂、本轮意图决定拦不拦**。`plan` / `coding` 照旧（有未合入的 PR 就拦）；`rework` 反过来（**没有**才拦，新增 `NOTHING_TO_FIX_MARKER` 标记与 `no_pending_pull` 中断原因）；`review` 照旧不挂闸门；读不到意图时按正向判（保守）。记忆的**刷新**不受影响 —— 拦不拦是判 PR 状态的结论，不是不刷新。
  - `src/ai_coding_harness/agent/prompts.py`：新增 `_INTENT_DIRECTIVES["rework"]`（先取回审查原文且不得凭记忆复述 → 派 `coder` 在**既有分支**上只改用户点名的问题 → `comment_pr` 逐条回评 → 收尾写记忆；本轮不派 `planner`；闸门归平台判，别自己再查一遍）。
  - 口径随动：`agent/middleware/__init__.py`（导出与模块说明）、`server/service.py`（轮末打底的形态注释）、`docs/workspace-directories.md`（rework 复用 orchestrate 那一行）。
- **关键决策**：
  - **强口令带动词，弱线索才含糊**。光秃秃的「评审意见」**不**进强口令，否则「看看有哪些评审意见」（查现状）会被判成「去改代码」；而「评审提的 / 评审提了 / 评审提出」是**引用别人的审查输出**、不是在下达审查命令，关键字层分不出「按它改」还是「只是看看」，所以把它们放进 review 的排除词、只留给模型判（模型侧有 `rework(评审提)` 这条线索与两条分界规则）。于是两侧都保住：查现状仍是 review，真正的修改请求落 rework —— 兜底口径也跟着钉了测试。
  - **共用装配而不是新开形态**：形态一多，工具子集、角色段、模型路由、`scripts/check_prompts.py` 的渲染表都要各加一份，而它们本可以一字不差。代价写下来：同一会话里 coding 与 rework 是**同一个缓存 agent**（缓存键是形态），所以「这一轮不派 planner」只能靠意图指令，不能靠裁掉工具。
  - **闸门按轮读 `configurable.intent`**：意图是逐轮的，中间件（连同智能体）是按形态建好并缓存的，形态级知识分不开「coding 要拦 / rework 要放行」。原先模块里那句「`abefore_agent` 不读 runtime / state」的自我约束跟着改成「不读 state；唯一的例外是读本轮意图」，并在类 docstring 里写明理由（意图不是依赖，它每轮都变）。副作用：`rework` 的拦下发生在**刷新之后** —— 与其他闸门一致（记忆先按事实刷新，再决定放不放行）。
- **验证**：
  - 全量 `pytest`（conda 环境 `aicoding` + `PYTHONPATH=src`）**875 条全过**（上一批 851，净增 24）；`ruff check` 改动文件干净；`scripts/check_prompts.py` 仍是 **0 条冲突**。
  - 新增/改写的用例：`rework` 口令直通（5 句真话：按评审意见 / 根据审查结论 / 按审查意见 / 修复评审意见 / 返工）且**不调分类模型**；review 的命令与「看看有哪些评审意见」**没被抢走**；「评审提的…」关键字层让位给模型、线索进提示词、模型可选 rework、兜底口径两侧都成立；`parse_reply` 认 `rework`；映射表与「只有登记过的别名才共用形态」；别名拿到**同一个 profile 对象**、同形三个子智能体、同形 tool 清单（真建图跑一轮取 `task` 描述）；coding 与 rework **共用同一个缓存 agent**；rework 指令的四条要点与记忆回写口径；**穿图**验证闸门四态（rework + 有 open PR → 放行且记忆仍刷新；rework + 无 open PR → 拦下且 `model.calls == 0`；coding + 有 open PR → 仍拦；缺 intent → 仍拦）。
- **遗留**：
  - **真机未验证**（沙箱连不上）：重启后端 + 新建会话后，说一句「按评审意见把第 2 条改一下」看是否判成 `rework`、是否落在既有 PR 的分支上、以及仓库没有 open PR 时是否看到那条停下标记。
  - `rework` 只知道「仓库里有待合并的 PR」，**不知道是哪一份**：多个 PR 同时开着时，靠模型从用户原话 / `list_pr_comments` 里自己认（这条与 `review` 形态一直以来的处境相同，没做「指认 PR」的参数）。
  - 「评审提的…」这类句子现在**必定**走一次分类模型（关键字层让位）。分类模型不可用时会退到兜底口径：带修正意味的引用句落 `rework`、纯查看那句落 `review` —— 前者在降级模式下偏激进，记在这里备查。

#### 前端 Markdown 卡顿：渲染调用从模板里挪走，两种缓存挡住重算

- **动机**：用户反馈长会话（实测那个会话 125 条消息 / 59 个 Markdown 块）里界面发黏，打字、滚动都跟不上。
- **真因（读代码得出，不是猜的）**：`App.vue` 的块循环里写的是 `v-html="renderMarkdown(p.content)"` —— **渲染函数直接写在模板里**，于是**组件每次重渲染都会把会话里每一条消息整体重来一遍**：`marked` 重新 parse、`DOMPurify` 重新消毒、`highlight.js` 把每个代码块重新高亮。而重渲染的触发点极密：正文 `flush` 每 100ms 一次（`RENDER_INTERVAL_MS`），外加每个工具帧、待办帧、方案帧、活动帧。也就是说**一条消息不但在自己流式时反复渲染，还被别人的每一帧连累重渲染**。
- **改动**（都在前端，两个文件）：
  - `frontend/src/App.vue`：给 Markdown 那个 `<div>` 加 `v-memo="[p.content]"`。依赖只有 `p.content`，所以只有正文真变了的那条（正在流式的那条）才重跑，**其余消息原样跳过**。同页那个「还没出任何块」的占位气泡（正文恒为 `…`）加 `v-memo="[]"`（等价 `v-once`），省掉它跟着每一帧重跑一次 parse + 消毒。
  - `frontend/src/highlight.ts`：给 `highlightCode` 加一层记忆化（`Map`，键 `${语言}\u0000${代码}`，`CACHE_LIMIT=200`，到顶按插入顺序丢最早一条）。已闭合的代码块内容一个字都没变，不该每帧重新高亮；`null`（语言不认识 / 高亮失败）也缓存，免得反复重试。容量上限是防止长会话把内存吃光。
- **关键决策**：
  - **没有动 `marked` / `DOMPurify` 这一层的调用方式**，只改「什么时候调用」—— 语义、消毒强度、输出 HTML 都保持不变，纯性能改动。
  - **不缓存整篇渲染结果**：流式那条消息的正文每次都在变，缓存整篇就得逐帧失效、还得管消毒结果的复用，收益小风险大；`v-memo` 已经把它收敛成「每条消息各自看自己」。
  - `highlight.ts` 的缓存**按语言 + 正文**做键，不按消息 id —— 同一段代码在不同消息里出现也能命中复用。
- **验证**：
  - 前端 `npm.cmd --prefix frontend run build` 通过（含 `vue-tsc`）。
  - **前后耗时的对比数据不可用**：拿自动化量出来的「打开会话 1108ms → 878ms」「连打 31 键 3438ms → 3426ms」两组数字**并不能说明问题** —— 那个时间量的是 CDP 往返，不是应用自己的开销；页面内也拿不到 `performance` / `requestAnimationFrame` 做干净采样（page-evaluate 沙箱是只读的）。所以这次的结论只立在**代码路径**上：改动前每次重渲染 O(全部消息)，改动后 O(正在流式的那条)；真实体感请你在自己的 5173 dev server 上看（已热更新）。
- **遗留**：
  - 真机体感未由我确认。若仍觉卡，可选 **Tier 2**：流式期间**跳过高亮**（先出纯文本、`done` 后补高亮），或把流式那条消息按「已定型前缀 + 正在长出来的尾巴」切开 —— 前者最省、后者最平滑，都还没做。
  - `v-memo` 的依赖是**引用比较**：`p.content` 若是新字符串（流式分片拼接）就会重渲染，这是期望行为；但若日后有人改成原地改写同一个字符串对象，`v-memo` 会失灵，届时需要换成内容哈希或显式的版本号。

#### 任务树的子任务名与挂靠：两套 id 从来对不上，改用子智能体自报的角色

- **动机**：界面任务树里，子任务容器一直叫「子智能体任务」，用户要求按角色显示（「编码子任务」「评审子任务」「规划子任务」）。查下去发现不只是名字问题 —— **子清单从来没有挂到委派它的那一项下面**，两者同一个根因。
- **排查（都是查真会话得出的，不是推测）**：
  - 名字有两个来源，键却是两套：`task` 工具事件的入参（`subagent_type`）记在 `_names`，键是**模型的 `tool_call_id`**；而容器起名时用的键来自 `_sub_items` = `delegation_id(namespace)`。
  - 会话 `78780d7a…` 的库里：子智能体 namespace 是 `tools:<UUID>`（25 个，如 `tools:723ad355-ec07-…`），而主控 state 里 `task` 的 id 是 `call_ded5238e…` / `call_00_ET_…`。把那几个 UUID 拿到主控的 `checkpoint_blobs` 与 `checkpoint_writes` 里搜 —— **0 命中**，两套 id 毫无关系。
  - 那串 UUID 是 langgraph 自己的 task id：`langgraph/pregel/_algo.py` 里就是 `task_checkpoint_ns = f"{checkpoint_ns}:{task_id}"`。
  - 顺带反证了两件事：日志里 `委派子智能体 → planner / coder / reviewer` 名字都在（不是 `?`），说明**入参没问题**；日志里 `[coder]` 5340 行、`[planner]` 1971 行，说明**子智能体的事件自带 `lc_agent_name`**（它和 namespace 是同一把键，天然对得上）。
  - 于是两个后果同时成立：容器名取不到角色 → 落「子智能体任务」；`_parents` 的键也对不上 → 子清单永远进不了主清单项。CHANGELOG 早先那条「子智能体清单挂到了兜底容器项、而非某个主委派项」的真因就在这里。
- **改动**（全在 `src/ai_coding_harness/stream.py`）：
  - `feed()`：namespace 非空时先调 `_note_child_agent(namespace, event)`。
  - 新增 `_note_child_agent()`：从 `metadata["lc_agent_name"]` 取角色名，`setdefault` 进 `_names[delegation_id(ns)]`（**与 `_sub_items` 同一把键**）；再认领一次委派。
  - `note_delegation()`：原有登记保留作兜底，另把 `(角色, 委派时主清单里 in_progress 的那一项)` 压进新增的 `_pending` 表。
  - 新增 `_pending_index()` + 认领逻辑：子智能体自报角色时，按角色取第一个还没被认领的委派（**同角色 FIFO**），把它的 `_parents` 指过去；同一个子智能体只认领一次（`_claimed`），角色取不到就不认领。
  - 新增 `_NO_AGENT_NAMES = {"", "None", "none"}`（与 `run_log._NO_AGENT` 同一口径；`run_log` 反过来 import 本模块，所以不能 import 那个常量）。
- **关键决策**：
  - **名字与挂靠都以「子智能体这一侧」为准**：它的事件与 `_sub_items` 共用 namespace 键，不存在 id 对齐问题；`task` 入参那份退成兜底（它仍然有用：子智能体还没开口时，至少日志与容器有名字）。
  - **认领靠角色 + FIFO**：两套 id 无法关联，只能靠角色牵线；同一角色并发委派时最坏挂到同角色的另一项下（可接受），认领不上就照旧走容器兜底 —— **不硬猜**。
  - **测试里的「假通过」一并修掉**：夹具原本让 `sub_ns()` 用与 `tool_start` 相同的 id（`v3_events.py` 里那句注释还写着「必须与 call_id 对上」），等于假设两套 id 相等 —— 真机不成立。新用例故意用不同的 id，并覆盖 FIFO 认领、认领不上、以及「两处都取不到名字」的兜底。
- **验证**：
  - 全量 `pytest`（conda 环境 `aicoding` + `PYTHONPATH=src`）**851 条全过**（上一批 847，净增 4）；`ruff check` 改动文件干净；`scripts/check_prompts.py` 仍 0 条冲突（与提示词无关，按约定跑一遍）。
  - 新用例：子 ns id ≠ 委派 call id 时，容器名仍是「编码子任务」；同样形状下子清单挂到 in_progress 那一项下且树尾没有多余容器；同角色两次委派按先后各挂一项；角色对不上时不硬认领、容器名仍用自报角色。
- **遗留**：
  - **真机未验证**（沙箱连不上）：重启后端 + 新建会话跑一轮 coding，看任务树里是否出现「编码子任务」并且**挂在主项下面**。旧会话已经落库的树不会被改写（树是每轮全量下发的快照）。
  - 同一角色**并发**委派时靠 FIFO，顺序与实际派发顺序不一致时会挂到同角色的另一项下；要更准就得能在两侧共用同一个 id（langgraph 侧的事，暂不做）。

#### 新增 `AGENTS.md`：把协作约定写成仓库里的规则

- **动机**：用户明确要求「后续所有改代码的操作先出方案、等确认再改」。这类约定只放在对话里会随会话丢掉，而 Codex 会自动读取仓库根的 `AGENTS.md`，所以落成文件最稳；顺带把这段时间实测出来的命令与踩过的坑一并记下，省得下次重新摸一遍。
- **改动**：
  - **新增 `AGENTS.md`**（仓库根，中文），五节：① 协作方式（先方案后动手 / 只读排查不必问 / 明确放行后一次做完 / 多口径列 a-b-c）；② 改完必须做的三件事（全量 `pytest`、改动文件 `ruff` 干净、`CHANGELOG` 追加一条）；③ 环境与命令（conda 环境 `aicoding` + `PYTHONPATH=src`、`scripts/check_prompts.py`、前端 `npm.cmd --prefix frontend run build`、生效时机）；④ 硬约束与踩过的坑（提示词即行为契约、沙箱资产建会话时推入、`start.bat` 是 GBK、记忆写入口径、拦截是代码拦、两份模板逐字一致、Maven 已配好）；⑤ 去哪儿看（README / CHANGELOG / docs / scripts）。
  - **没有改任何代码**：这是一份纯文档改动。
- **关键决策**：
  - **只写稳定规则与实测事实，不写进度与待办**：进度归 `CHANGELOG.md`，AGENTS.md 一旦开始记进度，两处必然各说各话。
  - **命令都写成实测过的形状**：Python 走 conda 环境 `aicoding`（`pytest` 与 `ruff` 都在里面，已核）；前端注明 PowerShell 的 `npm.ps1` 可能被执行策略挡住、要退到 `npm.cmd`。
  - **只放 `AGENTS.md`，不建 `CLAUDE.md`**：同一份内容两份必然漂移。
  - **多写了一节「去哪儿看」**（原方案只有四节）：几条指路比在 AGENTS.md 里复述项目结构便宜，也不容易过期。
- **验证**：本次无代码改动，仍按新约定跑了一遍全量 `pytest`（**847 条全过**）与 `ruff check`（改动文件只有新增的 Markdown，不涉及 Python 文件）；`scripts/check_prompts.py` 仍是 **0 条冲突**。
- **遗留**：无。后续若发现某条约定过时或缺失，仍按「先方案、后改」走。

### 2026-09-25

#### 沙箱里的 Maven 加速与缓存：阿里云镜像 + 卷上的本地仓库

- **动机**：沙箱自带 Maven，但**每次构建都要重新下载依赖**，一轮编码里大半时间花在等网络上。两条原因：① 镜像走 Maven Central（国内慢）；② 本地仓库 `~/.m2/repository` 在容器里，而容器是按会话新建 / 可能被重建的 —— 依赖每次都得重下一遍。
- **改动**：
  - `sandbox/maven/settings.xml`（**新资产**）：`<mirror>` 指阿里云公共仓库（`https://maven.aliyun.com/repository/public`，`mirrorOf=*`）+ `<localRepository>/ai_coding/.m2/repository</localRepository>`。provider 把它推进 `/ai_coding/maven/settings.xml`，`setup.sh` 再复制到 `$HOME/.m2/settings.xml` —— 那是 Maven 默认就会读的位置，**任何 `mvn` 命令都不必加 `-s`**（skill 里写死的命令、coder 临时敲的命令一起受益）。
  - `sandbox/deploy/sandbox/setup.sh`：`mkdir -p /ai_coding/.m2/repository` 并把 settings.xml 摆到 `${HOME:-/root}/.m2/settings.xml`（幂等；建会话与重连都会跑，所以重连回来的容器也一定有）。
  - `src/ai_coding_harness/sandbox/volumes.py`：把「按后端造卷」抽成 `_build_volume`，新增 `build_maven_cache_volume` / `maven_cache_volume_name` —— **Maven 缓存默认跨会话共享一个卷**（`ai-coding-m2`，挂 `/ai_coding/.m2`），`SANDBOX_MAVEN_CACHE_SHARED=false` 退化成按会话各挂一个（`ai-coding-m2-<public_id>`）。
  - `src/ai_coding_harness/session/manager.py`：`_runtime_inputs` 现在挂**两个**卷 —— 工作区（`/ai_coding/project`）+ Maven 缓存（`/ai_coding/.m2`）；回填给库的 `workspace_volume` 仍是工作区那个（删会话的回收线索不变）。
  - `src/ai_coding_harness/config.py` + `.env.example`：`SANDBOX_MAVEN_DIR`、`SANDBOX_MAVEN_CACHE_ENABLED`、`SANDBOX_MAVEN_CACHE_SHARED`、`SANDBOX_MAVEN_CACHE_PREFIX`、`SANDBOX_MAVEN_CACHE_MOUNT_PATH`。
  - `src/ai_coding_harness/agent/prompts.py`：沙箱规则里补一条 —— 「Maven 已配好，直接用 `mvn`；不要自己再写 settings.xml / 换 `localRepository` / 清空本地仓库」。
  - 测试：`tests/test_sandbox_volumes.py`（**新增** 5 条：工作区卷不动、缓存卷默认共享、可退化按会话、可关、后端 none 时都不挂）、`tests/test_sandbox_provider.py`（3 条：资产里确实推了 settings.xml、那份 xml 同时含镜像与本地仓库、setup.sh 会把配置摆到 `$HOME/.m2`）、`tests/test_workspace_runtime.py`（1 条：建沙箱时两个卷都在）。
- **关键决策**：
  - **本地仓库路径写死在 `/ai_coding/.m2/repository`，与卷的挂载点是同一个口径**：卷挂上时它落在卷里（跨重建存活），没挂（`ENABLED=false` / 后端 `none`）时它就是个容器内目录，配置不用改，只是不持久。
  - **默认共享缓存卷**：依赖包与会话无关，共享之后第二、第三个会话一上来就是热的 —— 这正是「每次构建都要重下」的解药。代价是要求存储能多机同时挂（Docker 单机没问题；K8s 要么 RWX，要么把共享关掉）。多会话并发写同一个本地仓库有理论风险（Maven 自己有文件锁，最坏是某个包重下一次），所以给了开关。
  - **不把缓存放进 `project/`**：那会污染目标仓库（`git status` 里多一堆文件、`git add -A` 还可能把它带上），所以才另挂一个卷。
  - **不用 `-Dmaven.repo.local` / `MAVEN_ARGS`**：写 `~/.m2/settings.xml` 是 Maven 的默认口径，覆盖面最大（谁敲 `mvn` 都生效），也少一个「命令里忘了带参数」的失败模式。
- **验证**：
  - 全量 `pytest`（conda 环境 `aicoding` + `PYTHONPATH=src`）**847 条全过**（上一批 838，净增 9）；本次改动文件 `ruff check` 干净（顺手清掉两条既有告警：`volumes.py` 的 import 顺序、`provider.py` 的 BLE001）。
  - 静态自检：settings.xml 里镜像 id / `mirrorOf` / URL / `localRepository` 四样都在；setup.sh 里有建目录与复制两步。
- **遗留**：
  - **真机没验证**（本机连不上 OpenSandbox）：新建会话后在容器里 `cat ~/.m2/settings.xml`、`mvn help:effective-settings | head`（应能看到 aliyunmaven 与 `/ai_coding/.m2/repository`），再跑两次构建对比第二次是否明显更快。
  - **共享缓存卷在多会话并发构建同一仓库**时有理论风险（Maven 文件锁 + 半成品文件）；先按默认共享跑，真撞上再 `SANDBOX_MAVEN_CACHE_SHARED=false`。
  - 删会话不删卷（与工作区卷同口径，`SANDBOX_VOLUME_DELETE_ON_TERMINATION=false`）：共享缓存卷本来就该留着；若切成按会话，会攒孤儿卷。
  - Python / Node / Go 没有对应的缓存卷配置（`pip` / `npm` / `go mod` 同理受益），需要的话按同一套模式再加。

#### 沙箱续期：活跃会话不再因为 TTL 到期被回收（`renew_if_due`）

- **动机**：沙箱有 TTL（建时传 `SANDBOX_TIMEOUT_MINUTES`，默认 60 分钟），到期就被 OpenSandbox 侧回收 —— 注意 `expires_at` 是**绝对时间**，不是「空闲多久才回收」。到期后再调用虽然能靠 `OpenSandboxProxy` 的「探活失败 → 重连 → 重建」恢复，但**重建会丢容器里非卷的内容**（`/ai_coding/tmp`、`/ai_coding/review`、临时装的依赖；只有 `project/` 在卷上）。三种场景本来都会白白重建/被杀：
  1. **用户隔一阵回来接着聊** → 轮首就该把寿命接上；
  2. **一轮里工具调用很密**（长会话） → 每次调用前顺手续；
  3. **一次工具调用自己跑很久**（一次 `mvn test` / 一次大 diff / 一次全量构建） → 这期间**没有别的沙箱调用**，前两条都轮不到，而到期是掐着绝对时间来的 —— 命令会被中途杀掉。这是用户补的一句「还有一个场景：一个任务长时间运行」。
- **改动**：
  - `src/ai_coding_harness/sandbox/client.py`：`SandboxClient` 新增 `renew(timeout) -> datetime`，包住 SDK 的 `Sandbox.renew` —— 语义是**重设**到期时间为「现在 + timeout」（不是叠加），返回新的绝对到期时间。
  - `src/ai_coding_harness/sandbox/proxy.py`：`OpenSandboxProxy` 新增公开方法 `renew_if_due()` 与内部 `_maybe_renew()` —— 距上次续期超过 `SANDBOX_RENEW_INTERVAL_SECONDS` 就续一次（续期目标取 `SANDBOX_TIMEOUT_MINUTES`）。**失败只记日志**（客户端类错误，如限流 / 参数错）不影响本次调用；若是「句柄已失效」那类（传输层异常，或 404/410/502/503），照既有纪律触发恢复（重连 → 重建）并重置续期时钟。续期用独立的 `_renew_lock` 单飞（与恢复那把锁分开，避免互相等），且**先记「尝试过」再发请求** —— 失败也不会让后续每个调用都重试一遍。
  - 同上文件：`_ensure_alive()` 每次沙箱调用前先过一遍 `_maybe_renew()`（节流窗口内只是一次浮点比较），所以**一轮里跑多久都会被续上**；`_rebuild()` 成功后在两个分支都重置 `_renewed_at`（换了句柄就重新计时，不让它刚接手就续）。
  - `src/ai_coding_harness/session/manager.py`：`ensure_runtime()` 取「已存在的运行时」时先 `await runtime.sandbox.renew_if_due()` —— **轮首续期**，覆盖「用户刚回来、还没跑任何工具」那一刻。
  - 同上文件（**调用进行中的心跳**）：`_call()` 用 `async with self._renewing()` 包住真正的那次调用 —— 进入时起一个心跳任务、退出时取消它，心跳每隔 `RENEW_TICK_SECONDS`（30 秒，或比间隔更小）检查一次「该续期了吗」（真正的节流仍在 `_maybe_renew` 里）。心跳里的任何异常都只记日志：**它绝不能让正在跑的那次调用失败**。调用本身仍是**直接 await**（不另起任务），所以「用户终止 → 取消本轮」的语义没变。
  - `src/ai_coding_harness/config.py` + `.env.example`：新增 `SANDBOX_RENEW_INTERVAL_SECONDS`（默认 1800 秒 = 默认 TTL 的一半；**0 = 关掉续期**）。
- **关键决策**：
  - **懒续期为主、轮首续期为辅**，不起后台心跳任务：所有沙箱调用都经代理，活跃会话自然被续；空闲会话本来就不需要保活，为它养一个 asyncio 任务不值。
  - **续期失败不算故障**：它是保命动作，不是本轮的业务结果；真过期了还有既有恢复路径。只有「句柄失效」那类才升级为恢复。
  - **失败也推进节流时钟**：否则一次限流会让后续每个工具调用都打一次续期请求。
  - **续期目标复用 `SANDBOX_TIMEOUT_MINUTES`**，不另立「续期时长」配置 —— 口径只有一个。
- **验证**：
  - `tests/test_sandbox_proxy.py` 新增 `TestTheRenewal`（假沙箱 / 假 provider + 可控时钟）：刚接手的沙箱第一次调用不续；到点续一次、间隔内不重复；`0` 关掉续期；`renew_if_due`（轮首入口）本身可用；客户端类错误（429 / 400 / 403）只记日志、不触发恢复、本次调用照跑；失败后同一间隔内不反复重试；句柄失效（传输层异常）走重连；重建后续期时钟重置；并发调用只续一次；**一次长调用（`run_delay`）进行中被续多次**、**调用一结束心跳就停**、**短调用一次都不续**、**心跳里续期失败不影响这次调用**、**关掉续期时没有心跳**。
  - `tests/test_workspace_runtime.py` 新增一条：`ensure_runtime` 取已存在运行时时会调一次 `renew_if_due`。
  - 全量 `pytest`（conda 环境 `aicoding` + `PYTHONPATH=src`）**838 条全过**（上一批 821，净增 17）；本次改动文件 `ruff check` 干净。
  - 踩到一个测试环境的坑，值得记下来：那几条「长调用」用例**不能用**文件里那个冻结时钟的 `clock` fixture —— Windows 的 asyncio 定时器也走 `time.monotonic`，时钟一冻结 `await asyncio.sleep(...)` 就永远不醒（实测试跑挂住，得杀进程）。所以它们改用真实时钟 + 很小的续期间隔（0.05s）。
- **遗留**：
  - **没做真机验证**：本机到 OpenSandbox（`192.168.72.128:8080`）的 socket 被环境挡了（`WinError 10013`），续期请求没实打实发出去过。要确认两件事：① 你们那台 OpenSandbox 是否支持 `POST /sandboxes/{id}/renew-expiration`；② 续期后 `get_info().expires_at` 确实推后。跑一轮长会话、在 `logs/agent.log` 里找 `沙箱已续期：public_id=… 到期=…` 即可。
  - **续期只延长寿命，不解决「已经丢过的容器内状态」**：想让 `/ai_coding/tmp`、`/ai_coding/review` 跨重建存活，得把它们也放进卷（现在卷只挂 `project/`）—— 另一件事。
  - 间隔默认 30 分钟是拍的量（TTL 的一半）；若某类会话一轮常跑超过 60 分钟，可以把 `SANDBOX_TIMEOUT_MINUTES` 一起调大。
  - 心跳的 tick 目前是模块常量（30 秒），没做成配置项：它只是「多久检查一次」，真正决定续不续的是 `SANDBOX_RENEW_INTERVAL_SECONDS`，暂时不值得多一个旋钮。

#### 记忆写入改在「收尾」：出方案轮不写、coding 与 review 收尾各写一次、刷新归中间件

- **动机**：用户看过写入点之后定了口径 ——
  1. **出方案轮不该写记忆**：那时还没动代码，写进去的是「打算做的事」而不是事实；
  2. **内容只在 coding 与 review 之后写**（这两轮才有落地的事实：PR、审查结论）；
  3. **其他智能体开工时的记忆刷新归中间件**（`before_agent`），不该由模型管。
  另外：**plan 形态仍然要拦**（方案以 master 为基准，仓库还有未合入的 PR 时出的方案会建在错前提上）。
- **改动**：
  - `src/ai_coding_harness/tools/register.py`：`PLAN_FORM` **去掉** `save_repo_memory`；`REVIEW_FORM` **加上**它（审查结论是稳定事实，收尾要写回）。
  - `src/ai_coding_harness/agent/prompts.py`：`_MEMORY_GATE`（「第 0 步：先沉淀记忆」，开工前写）**换成 `_MEMORY_WRITE_BACK`**（「收尾时写回仓库记忆（本轮的最后一件事）」，写明在代码改完 / 审查出结论之后写、只写一次、不自己查 PR）；意图指令改为 —— **plan 不带**（并明写「不写仓库记忆」）、**coding 与 review 都带**；`_PLAN_WORKDIR` 的记忆行回到「（StoreBackend，**只读**）」；`_PLAN_RULES`、`_ROLE_PLAN_MAIN` 的可写范围收成「只有方案那一处」；`_ROLE_REVIEW_MAIN` 新增「## 收尾时写回仓库记忆」，措辞从「全程只读」改成「不改代码」（这一轮确实要写一次记忆）；`_REVIEWER_ROLE` 明写记忆只读；`_BASE_ENV` 的写回归属改成「只在 coding 与 review 两轮的收尾时由主控完成」。
  - `src/ai_coding_harness/agent/middleware/memory.py`：把原来揉在一起的「刷新」与「拦本轮」拆开 —— `REFRESH_FORMS`（**所有主控形态**）开工前都按 Gitee 实况刷新记忆（文件头 + 摘两张老表），`BLOCKING_FORMS`（`plan` / `orchestrate`）才在刷新后拦本轮；`MemoryMiddleware` 新增 `blocking` 开关。**`review` 刻意不拦**：它要审的那个 PR 本身就是 open 的，按「有 open PR 就拦」判会把自己永久挡在门外。子智能体（`form=None`）仍是纯只读注入（不查 PR）。
  - `src/ai_coding_harness/agent/middleware/__init__.py`、`agent/graph.py`：导出与文档同步（`GATED_FORMS` → `REFRESH_FORMS` / `BLOCKING_FORMS`）。
  - `src/ai_coding_harness/server/service.py`：轮末打底的判据从「形态是 orchestrate」改成「形态在 `_MEMORY_WRITE_FORMS` = {orchestrate, review}」。
  - `scripts/check_prompts.py`：C5 规则跟着反向（原来查「plan 记忆写成只读 = 与第 0 步相悖」，现在查「出方案轮**不得**声称要写记忆、且必须写清记忆只读」）。
  - 测试：`test_prompts.py` 的 `TestMemoryWritebackGate` 重写（只有 coding / review 带收尾写回、plan 不带、收尾在派发说明**之后**、平台已查过 PR、按模块列未完成功能、一轮只写一次）；`test_memory_gate.py` 拆出 `_refresh_only` 那条路并新增「只刷新不拦」用例、装配点改成「每个形态都刷新、只有两个形态拦」、轮末打底的参数化改成 coding/review 触发；`test_profiles.py` / `test_tool_registry.py` 的工具集与提示词断言同步；`test_intent_middleware.py` 的 plan 指令断言改成「你自己出方案、派不了 `task`」。
- **关键决策**：
  - **拦与写彻底解耦**。它们本来就只是「都需要查 PR」才被放在一起：现在 plan 只拦不写、review 只写不拦、coding 两样都有。三者的判据各自写在 `BLOCKING_FORMS` / `_MEMORY_WRITE_FORMS` 两个集合里，注释写明理由。
  - **review 不拦**是硬约束不是偏好：审查轮的输入就是 open PR，拦了这轮永远跑不起来。
  - **写入时机从「开工前」改成「收尾」**：原来要求开工第一件事就写记忆（理由是让 planner / coder 读到本轮刚沉淀的版本）；现在改成收尾 —— 这一轮产出的事实（PR 编号、审查结论）在收尾时才存在，开工前根本没得写。代价是同一轮里子智能体读到的仍是上一轮的记忆，而这正是用户要的（“之后才可以写入”）。
  - **刷新给所有主控形态**（含 qa / analysis / plan / review）：它们自己不写正文，但读到的记忆必须是新的（文件头 `最后更新 / 最近 PR` 与两张老表的清理都由它保证）。代价是每轮多一次 Gitee 查询 —— 用户明确要的就是这个（“其他智能体开始时由 middleware 负责更新”）。子智能体不加：同一轮主控已经刷过，让三个子智能体各再查一趟只是白花钱，而且 coder 刚建完 PR、reviewer 再查一遍会把自己挡在门外。
- **验证**：
  - 全量 `pytest`（conda 环境 `aicoding` + `PYTHONPATH=src`）**821 条全过**（上一批 817，净增 4）；本次改动文件的 `ruff check` 干净。
  - `scripts/check_prompts.py` 冲突扫描 **0 条**（八份成品提示词）。
  - 行为面（假后端 / 假 Gitee，不碰网络）：`_gate`（plan / orchestrate 那条路）有 open PR 照样抛 `TurnStop`；`_refresh_only`（qa / analysis / review）同样把记忆刷新了文件头，但 `abefore_agent` 返回 `None`、不拦；轮末打底只在 coding / review 两轮触发。
- **遗留**：
  - **`qa` / `analysis` 每轮都多一次 Gitee 查询**（刷新用），只读问答轮的延迟会多一跳网络往返；要收窄就把 `REFRESH_FORMS` 改回子集。
  - **同一轮内子智能体读到的记忆仍是上一轮的**：写入挪到收尾之后的必然结果。如果哪一轮需要「本轮先沉淀、子智能体再读」，得另设计（例如收尾后再跑一次注入）。
  - 提示词里的「收尾时写」是**软约束**（模型可能忘）；工具层面没有强制（`save_repo_memory` 仍是随时可调）。要不要在轮末由平台检查「这一轮该写没写」，**待定**。
  - 尚未在活的后端生效（提示词构建期读、工具装配建会话时定）：重启 + 新建会话才算数。

#### 前端对话列宽改成自适应（原来写死 760 / 800px）

- **动机**：窗口越宽，对话区反而显得越窄 —— `.msg` / `.thinking` / `.composer` / `.notice-bar` / `.workspace-bar` 五处的 `max-width` 都是写死的 `760px` / `800px`，再配上 `margin: 0 auto`，屏幕变大只是两侧留白变多。
- **改动**（全在 `frontend/src/style.css`）：`:root` 新增两个变量 —— `--chat-max: clamp(760px, 60vw, 1400px)` 与 `--chat-pad: clamp(24px, 2.2vw, 48px)`；上面五处改用它们（左右留白也跟着 vw 缩放，宽屏上文字不贴边）。用户气泡另加绝对上界：`max-width: min(85%, 860px)` —— 贴一大段日志时不会跟着列宽横到 1400。
- **关键决策**：
  - **跟窗口走，但留上界**。`60vw` 保证「窗口变大就变宽」，`1400px` 封顶避免一行长到没法读（这个应用里既有散文也有代码块，1600+ 就开始难扫）。窄屏时 clamp 落到 760 下限，行为与改动前一致。
  - **留白也一起自适应**：列宽变大而 padding 不变会显得字贴边，所以 padding 用同一套 `clamp` 缩放。
  - **只动宽度，不碰别处**：侧栏 260px、计划面板 300px、窄屏那条 `max-width: 1100px` 的媒体查询都没动 —— 这次的问题只是「列本身不跟着长」。
- **验证**：
  - `npm --prefix frontend run build`（含 `vue-tsc` 类型检查）通过。
  - **真实浏览器 + 真实样式**量了四档视口（临时探针页 `frontend/public/width-probe.html` + 内嵌浏览器的 viewport 覆盖，量完即删、服务已停）：

    | 视口 | 消息列 | 输入区 | 用户气泡（短文本） |
    | --- | --- | --- | --- |
    | 1280 | 768 | 768 | 88 |
    | 1920 | 1152 | 1152 | 88 |
    | 2560 | 1400 | 1400 | 88 |
    | 3440 | 1400 | 1400 | 88 |

    改动前这五处一律 760 / 800（1920 屏上少用了近 400px，2560 屏上少用了 600px）。
  - 用户气泡那一列说明上界策略生效：列变宽不影响短消息的贴合宽度。
- **遗留**：
  - 只实测了 1280 及以上的视口；更窄的窗口靠块级元素「不超过容器」这条保证不会溢出（clamp 的下限 760 比容器宽时按容器走），没有逐档回归。
  - 1400 这个上界是拍的量：想更宽（比如 1600）或更窄，改 `--chat-max` 一个变量即可。

#### 方案对象归 plan 智能体：补齐增删改查（软删除走 `closed`）

- **动机**：方案一直是「只能新建」的产物 —— ``plan`` 形态的主控手里只有 ``storePlan``（加 ``get_pr_info`` / ``save_repo_memory``），用户说「把刚才那份方案第 2 步改一下」「那份方案别做了，删掉」时它**连方案全文都读不到**（``get_unexecuted_plan`` 只认 pending，而且只给了 coder）。于是把方案这个对象的**增删改查**收到 ``plan`` 形态手里。
- **改动**：
  - `src/ai_coding_harness/db/dao.py`：新增 `get_plan`（按 id + 限定 session，**含已删除的**）、`update_plan_content`（改正文 / 标题）、`set_plan_steps`（整份替换步骤）；`set_plan_status` 增加可选 `session_id` 限定并返回是否命中；`get_latest_plan` **排除 `closed`** —— 软删之后面板要回落到上一条或空，而不是继续显示一份「已删除」的方案。
  - `src/ai_coding_harness/tools/plan.py`：新增四个工具 —— `getPlan`（给 id 取那一条 / 不给取最新一份）、`updatePlan`（正文 / 标题）、`updatePlanSteps`（整份替换步骤，**会把状态重置为 pending**）、`deletePlan`（**软删**：状态改 `closed`，记录留在库里）。模块 docstring 写明三家的分工。原有的 `storePlan` / `get_unexecuted_plan` / `updatePlanStatus` / `updatePlanStep` 一个没动。
  - `src/ai_coding_harness/tools/register.py`：`PLAN_FORM` 的工具集加上后四个（`planner` 子智能体仍只有 `storePlan` —— 它跑的是 plan → coder → reviewer 那一整套的第一段，写方案就够）。
  - `src/ai_coding_harness/server/service.py`：`_PLAN_TOOLS` 补上 `updatePlan` / `updatePlanSteps` / `deletePlan`（改完推一帧快照给前端）；**快照为 `None` 也要推**（`plan: null`）—— 否则删掉最后一份方案后，前端会一直停在那份已经不存在的方案上。`getPlan` 不进这个集合：它只读，不该动面板。
  - `src/ai_coding_harness/agent/prompts.py`：`_ROLE_PLAN_MAIN` 新增「## 方案由你管到底（增删改查）」段 —— 四个工具各自的用途、**删除前必须先向用户说明是哪一份方案并拿到明确同意**、正在执行的方案不许改步骤、只改用户点名的那一处；「可写范围」同步改成三处写（记忆 / 方案 / 结论）。
  - 测试：`tests/test_plan_tools.py` 新增 `TestPlanCrud`（软删成 closed、重复删有话说、未找到、改正文改标题、两个字段都不给直接回错、删后不许改、执行中拒绝改步骤、按 id 读得到已删的那份、默认取最新）+ 工具清单改成八条；`tests/test_profiles.py` 的 `_WRITE_TOOLS` 与 plan 形态工具集同步，并新增「别的形态一个 CRUD 工具都没有」；`tests/test_tool_registry.py` 新增 plan 形态拿到五个方案工具 + 其余四形态一个都没有；`tests/test_plan_events.py` 新增「每个写方案的工具都推快照」「`getPlan` 不推」「删除后推一帧 `plan: null`」。
- **关键决策**：
  - **实施进度仍由 coder 回写**（你选的 a）。coding 轮里在场的是 orchestrate 主控 + planner / coder，planner 只在出方案时跑一次 —— 进度若也归 plan 侧就没人写了，右侧面板会停在 pending。所以这次只把**方案对象本身**的增删改查收过去，`updatePlanStatus` / `updatePlanStep` 留在 coder 手里。
  - **删除是软删**（你定的 `closed`）：记录留在库里、可追溯，`get_latest_plan` 与 `get_unexecuted_plan` 都不再认它（前者排除 closed，后者本来就只认 pending）。
  - **执行中不许改步骤**：`updatePlanSteps` 在 `executing` 状态直接拒绝 —— 改了会让 coder 手上的计划与库里的对不上，而这正是「进度由 coder 回写」那条分工的代价，只能靠拒绝来守。
  - **每个写工具都带 session 限定**：模型给的 `plan_id` 可能来自别的会话或纯属猜的，跨会话改 / 删方案不该发生；`WHERE` 里加了 `session_id`，命中不了就回「未找到方案 N（或它不属于本会话）」。
  - **删除要用户点头**（提示词里的硬要求，与 `close_pr` 同款）：软删虽然可追溯，但用户看到的是「方案没了」，不能由模型替他决定。
  - **顺手清掉了 8 条既有的 `RUF100`（unused noqa）**：都在本次改动的文件里（`tools/plan.py` 与 `tests/test_plan_events.py`），属于「改了就得让它干净」；全仓既有告警从 16 条降到 8 条。
- **验证**：
  - 全量 `pytest`（conda 环境 `aicoding` + `PYTHONPATH=src`）**817 条全过**（上一批 792，净增 25）；本次改动文件的 `ruff check` 干净。
  - 交互面（假 dao，不碰真库）：软删确实写成 `closed` 且只改那一行；重复删 / 未找到 / 已删后再改都有明确回执；执行中改步骤被拒且**没有**触发写库；`getPlan` 默认取最新、按 id 能读到已删的那份。
  - 事件面：六个写方案的工具各推一帧快照、`getPlan` 一帧都不推、删除后推 `plan: null`。前端那侧 `plan.value = ev.plan ?? null` 本来就吃得下 null（未做前端实测）。
- **遗留**：
  - **没有「列本会话所有方案」的工具**：`getPlan` 只能按 id 或取最新一份。用户说「把第三份方案删了」时，模型手上没有编号，只能先问或先取最新 —— 要不要加 `listPlans`，等你定。
  - **软删只进不出**：`closed` 的方案会一直留在 `plans` 表里，没有清理策略（和会话、记录一样，先不删）。
  - **`updatePlanSteps` 是整份替换**：没有「只改某一步的 goal」这种细粒度工具；改完进度重置为 pending，执行中的方案还会被拒。
  - **尚未在活的后端生效**（提示词是构建期读盘，工具装配是建会话时定），要重启后端 + 新建会话才算数。

#### 仓库记忆改格式：两张表都撤掉，「未完成的功能」由模型写进「当前进度」

- **动机**：仓库记忆的模版一直在两张表上打转（``需求列表`` / ``功能列表``：前者平台按 PR 状态回填四列，后者是它的派生视图），而这两张表都不是用户要的东西：
  - ``功能列表`` 与「变更记录」重复 —— 变更记录本来就是以 PR 为角度的（每条改动挂着 PR 编号 / 链接），再列一张功能表只会两处各说各话；
  - ``需求列表`` 本质是「Gitee 上开着哪些 PR」的一份**缓存**，而闸门真要的那件事（仓库有没有还没落地的改动）可以直接查 PR 列表，不必先落到表里再读回来。
  真正缺的是第三样：**「当前进度」里要能看到每个模块还没做完的功能** —— 那是下一轮「该做什么」的出处，只有模型知道（模块怎么划、哪些事没做完），平台从 PR 状态推不出来。
- **改动**：
  - `sandbox/policies/repo-memory-template.md` 与 `docs/repo-memory-template.md`（**两份同时重写，且内容逐字一致**）：节次收成五节 —— 一、项目概览／二、变更记录（**以 PR 为角度**，每条写清 PR 编号 / 链接）／三、当前进度（**未完成的功能按模块全部列出**，另留一行可选的「最近一轮」）／四、约定与注意事项／五、关键决策。头行仍由平台维护，模版里明说这一点、也明说两张老表会被摘掉。此前两份内容跑偏了三周（docs 那份还留着功能列表 + 需求列表，sandbox 那份只有需求列表），现在加了一条比对测试钉住。
  - `src/ai_coding_harness/agent/memory_reconcile.py`（**大幅收窄**）：平台维护的部分只剩**文件头那一行**（`最后更新` / `最近 PR`，时间戳仍只在别的内容真变了时才前进）。删掉整张表机制 —— `Need` / `_NEED_HEADER` / 表解析与渲染（`_cells` / `_span` / `_rows` / `_put_table` / `_need_row`）/ 状态回填（`_reconcile_need`）/ 补行（`_seed`）/ 裁剪（`_trim`）/ 建表（`_build_needs`）。新增 `OpenPull`（编号 / 标题 / 分支 / 链接）与 `_open_pulls()`：**闸门判据直接取本轮查到的 PR 列表**（`state=open` 才算），与记忆里写了什么无关。`_drop_legacy()` 现在摘**两节**（`需求列表` 与 `功能列表`，仍是「去掉节号后正好等于」的严格匹配，模型自己写的「历史功能列表」不牵连）。
  - `src/ai_coding_harness/agent/middleware/memory.py`：闸门改用 `OpenPull`；marker 文案从「下面这些需求还挂在 PR 上」改成「下面这些 PR 还开着，改动尚未落地」（分支取 PR 的 `head.ref`，不再是表里那列）；「拦之前先刷记忆」的语义不变。
  - `src/ai_coding_harness/tools/memory.py`（含 `tools/register.py` 的一处注释）：落盘前的对账仍跑，但现在只做「重算文件头 + 摘两张老表」；工具描述改成「文件头归平台盖章；其余内容（含按模块列出的未完成功能）由你写；两张老表会被摘掉，别再写」。拒写条件不变（查不到 PR 就整篇不写）。
  - `src/ai_coding_harness/agent/prompts.py`（四处）：记忆规则里那句「四列由平台回填、你只管 `需求` 那一列」删掉，换成「只有文件头那一行由平台维护；其余全部由你写 —— 变更记录以 PR 为角度、**当前进度里按模块列出所有未完成的功能**」；`_MEMORY_GATE` 同口径；另一处「记忆块里的 `## 一、项目概览`……、需求列表」改成「……、「当前进度」里按模块列出的未完成功能就是这一轮该做什么的出处」。
  - 测试：`tests/test_memory_reconcile.py` **重写**（24 条）—— 散文逐字保留、**不再生成任何表**、「两张老表整节摘掉且算作 changed」、「正好叫表名」才摘、文件头三种形态、头行时间戳只在真变了时前进，以及闸门判据（只认 `state=open`、认不出的状态不算、没编号不算、merged/closed 不拦）；新增「两份模版逐字一致」与「模版要求按模块列未完成功能」两条。`tests/test_memory_gate.py` / `tests/test_memory_tool.py` 的 fixture 换成**不带表的记忆**（头行过期 → 对账该把它刷新），断言改成看文件头；「平台补一行」「模型伪造已合入被改回」两类用例换成「平台不再往记忆里写任何表」「模型写回两张老表会被摘掉」。`tests/test_prompts.py` 的「四列由平台回填」改成「头行归平台 + 要求按模块写未完成功能」。
- **关键决策**：
  - **闸门换锚点，行为等价**。旧机制里 `_seed` 会把每个 open PR 补成「待合入」行，`pending` 就是这些行；现在直接判 PR 列表里的 `state=open` —— 拦不拦与以前一致，但少了一层会腐烂的缓存（表里漏一行就不拦，是旧机制真实存在过的口子）。
  - **两张表都摘，不是只摘一张**。功能列表与变更记录重复这一点由用户点明；需求列表则是「平台不再维护的缓存」，留着只会被当成散文保留下来、里面的状态永远是死的（这正是 2026-09-18 摘功能列表时的理由，现在同样适用于需求列表）。
  - **平台只留文件头，是把「事实」与「判断」分开**：`最后更新` / `最近 PR` 是平台能核的事实；「哪些模块还没做完」是判断，归模型。两条互不纠缠，也就不需要平台再按 PR 状态去推导表里的状态。
  - **模版两份必须逐字一致**（沙箱那份生效、`docs/` 那份给人看）。此前它们各自演化过一次，跑偏三周没人发现；现在由一条测试钉住，改一份不改另一份当场红。
  - **不做迁移脚本**：老记忆里的两张表会在下一次写记忆（plan / coding 轮的闸门刷新或 `save_repo_memory`）时被自愈摘掉；「当前进度」里只剩「待办 / TODO」一行的老记忆，等模型下一轮写记忆时补成按模块的未完成功能。
- **验证**：
  - 全量 `pytest`（conda 环境 `aicoding` + `PYTHONPATH=src`）**792 条全过**（上一批 796：删掉了表机制的 20 余条、新增 8 条，净减 4）；`ruff check` 本次改动文件干净（全仓仍是那 16 条既有告警，全在未改动文件里）。
  - 模版条文自检：两份逐字一致、含「未完成的功能（按模块，全部列出）」与「以 PR 为角度」。
  - 行为自检（真代码、不碰网络）：老记忆（功能列表 + 需求列表）过一遍对账 → 两节连标题一起消失、散文一行不少、`changed=True`；再跑一遍 → `changed=False`（稳态）；PR 列表里只有 open 的才进 `pending`。
- **遗留**：
  - **只读形态不会触发对账**（qa / analysis / review 主控与三个子智能体只读注入）：老记忆里的两张表要等下一次 plan / coding 轮才会被摘掉；在那之前它们读到的是带表的老文本。
  - **老记忆的「当前进度」不会自动补齐**：没有迁移脚本，按模块的未完成功能要等模型下一轮写记忆时补。
  - **两份模版仍是两个文件**（内容由测试保证一致）；要合成一份得改上传路径口径（`config.sandbox_local_policies_dir` 指向 sandbox/policies）。
  - **尚未在活的后端生效**：提示词与模版是构建期 / 建会话时读的，要重启后端 + **新建会话**才算数。

#### 压缩流水改落 StoreBackend：沙箱会没，store 不会（`/conversation_history/` + `/large_tool_results/`）

- **动机**：deepagents 的摘要中间件把两类「溢出产物」写在沙箱里 —— `/conversation_history/<thread_id>.md`（被压缩掉的原始消息，每次压缩追加一节）与 `/large_tool_results/…`（`ContextOverflowError` 时过大的工具结果）。而沙箱**会话结束就销毁、卷只挂 `/ai_coding/project`**：于是摘要消息里那句「完整历史已保存到 <路径>，需要细节时可回看」在沙箱重建后是一句空话；2026-09-21 那次实测里，`/conversation_history/…md` 的 404 甚至被沙箱代理当成「沙箱没了」触发了一次重建（该缺口记在 2026-09-18 那条的遗留里）。会话一重建、进程一重启，这份流水就没有第二份副本。
- **改动**：
  - `src/ai_coding_harness/backends/composite.py`：`build_composite_backend` 新增两条路由 —— `"/conversation_history/"` → `StoreBackend(namespace=("conversation_history",))`、`"/large_tool_results/"` → `StoreBackend(namespace=("large_tool_results",))`，并**显式传 `artifacts_root="/"`**（前缀是按它拼的，把契约钉在我们这一侧）。前缀提成模块常量 `HISTORY_PREFIX` / `LARGE_TOOL_RESULTS_PREFIX`，模块注释写清它们与 deepagents 那两处写盘的对应关系。**没动 deepagents、没改提示词、摘要消息里的路径一字不变**（路径经 `CompositeBackend` 路由到 store，写/读/编辑/`ls`/`grep` 全走通）。
  - `tests/test_composite_backend.py`（**新增 8 条**）：两条前缀确实落进各自 namespace、默认后端一个字节没收到；其它路径（`/ai_coding/project/…`）照旧走默认后端；**换一个 composite 实例仍读得到**（沙箱重建 / 进程重启的等价断言）；摘要中间件的真实调用序列在 store 上成立（首次 `adownload` 得到 `file_not_found` 而**不是异常**、第二次 `aedit` 追加且 `occurrences=1`、两节都在）；模型侧 `aread` / `als` 读得回；`artifacts_root` 被钉在 `"/"`；最后一条直接拿 `create_summarization_middleware` 算出来的 `_history_path_prefix` / `_large_tool_results_prefix` 与我们的路由常量比对 —— 上游改默认值或改前缀名时当场红。
- **关键决策**：
  - **走路由，不动 `artifacts_root` 去蹭 `/memories/`**。另一条更省事的路是把 `artifacts_root` 设成 `"/memories/"`，让流水顺着现有记忆路由落库 —— 但那样「会话流水」与「仓库长期记忆」共用一个 namespace，摘要消息里还会出现 `/memories/conversation_history/…` 这种看着就像仓库记忆的路径（我们刚把记忆口径收紧过，不值得为省一条路由把语义搅浑）。
  - **不自建摘要中间件**。也可以排除上游那份、自己挂一个 backend 固定成 StoreBackend 的实例，但那要复刻 deepagents 的整层包装（offload、非破坏性 state、内部调用事件标记），而我们刚查过的「摘要调用绕过兜底」这类问题只会更难定位 —— 收益与代价不成比例。
  - **`StoreBackend` 缺异步 `adownload_files` 这个坑是实测踩出来的**：offload 用 `await backend.adownload_files([path])` 读旧内容来追加，而 `StoreBackend` 只实现了同步 `download_files`。它能走通是因为协议基类给了默认实现 `asyncio.to_thread(download_files)`——**这件事不写进测试就没人记得**，所以单测里专门钉了「首次读返回 `file_not_found`、不抛异常」与「二次追加不覆盖」两条。要是哪天上游去掉那个默认实现，offload 会静默退化成「每次覆盖」，这两条会红。
  - **前缀不做成配置项**。路径由 deepagents 决定（`artifacts_root` + 硬编码名），配置化只会让两边口径可能不一致；要跟上游对齐就对齐在代码常量 + 一条比对测试上。
  - **namespace 按「数据归属」分**：仓库记忆是跨会话的长期事实（`("memories",)`），另外两个是会话级流水（key 里带 `thread_id`）——同库不同 namespace，互不干扰，也不影响记忆的读写路径。
  - **脱敏口径补一句**：流水里是被压缩的**历史消息**（工具输出在进 state 之前已经过 `RedactionMiddleware`），所以内容本身已脱敏；但改动之后它多了一个落点（Postgres 的 store），此前 CHANGELOG 说的「四条出口」在这里应理解为「落库这条出口又多了一处写入方」。
- **验证**：
  - 单测：`tests/test_composite_backend.py` **8 条全过**；全量 `pytest`（conda 环境 `aicoding` + `PYTHONPATH=src`）**796 条全过**（上一批 788，净增 8）；`ruff check` 改动文件干净。
  - 另跑了一段不碰沙箱的真代码探针（`InMemoryStore` + `StoreBackend` + `CompositeBackend`）：`awrite` → `adownload`（首次 `content=None, error=file_not_found`）→ `aedit`（`occurrences=1`）→ 追加后两节都在 → **换一个 composite 实例仍读得到** → 模型侧 `aread` / `als` / `agrep` 全部正常。这就是「沙箱释放后还读得到」的等价验证（真机上换成 Postgres store，与仓库记忆同一套）。
- **遗留**：
  - **会话销毁时不清理这两份流水**（本次按决定先不做）：`StoreBackend` 没有 delete，要清就在 `session/manager.py::destroy_session` 里用底层 store 删 `("conversation_history",)/<thread_id>.md` 与 `("large_tool_results",)/<thread_id>/…`。现状是删了会话、checkpoint 与流水都留在库里 —— 好处是可复盘，代价是孤儿数据随会话数增长。
  - **流水体量没有上限**（本次按决定先不做）：每条 thread 的 `/conversation_history/<thread_id>.md` 随压缩次数线性增长（每次追加一整批被压缩消息）。要设上限得在写入后裁剪（保留最近 N 节），那要么包一层中间件、要么等 deepagents 出配置。
  - **主控与子智能体共用同一个 backend 前缀**：`deepagents/graph.py` 给子智能体也挂了同一套摘要中间件，而 thread_id 取自运行上下文 —— 同一会话里主控与子智能体的流水**可能落在同一个 key 上**（也可能按各自的上下文分开，未实测）。要不要按角色/run 分开 key，等真机看到实际写入形状再定。
  - **迁移**：本次改动只影响**新的写**。改动之前已经落在某个活沙箱里的那份流水不在 store 里，摘要消息指向的路径会读到 `file_not_found`，直到下一次压缩追加时才补上；没做「rehydrate 时把沙箱里那份搬进 store」的一次性搬迁（收益小、路径在容器根、还得经沙箱代理）。
  - **尚未在真机验证**：需要重启后端 + 跑一轮长会话触发一次压缩，看 store 里出现 `("conversation_history",)` 的记录、且沙箱 `/conversation_history/` 是空的。
  - 与本次改动相邻的老缺口没动：**摘要模型的调用绕过兜底中间件**（`MODEL_FALLBACK` 管不到摘要那一跳）。

#### 提示词自检：清掉六组「同一份提示词里互相打架」的指令（新增 `scripts/check_prompts.py`）

- **动机**：把五个主控形态与三个子智能体的**成品提示词**（经 `build_main_prompt` 拼装后的）逐个核了一遍，找出六组「同一份提示词内部」互相打架的指令。这类矛盾比「少写一句话」贵得多：模型每次只挑一句信，挑中哪句随上下文，行为就成了随机的 —— CHANGELOG 里记着的几次事故（清单塌成「执行计划中」、记忆写回被掐断、只读形态收到写命令）都是同一形状。分六组：
  1. **清单口径自相矛盾**：通用规则第 2 条末尾「清单不要照抄方案的步骤 …… 这时清单只写你自己的动作」与第 3 条「一项对应方案的一步：把方案的每一步各列一条」是对同一份文本的**相反要求**；而第 3 条给的示例（「执行方案步骤 2：改密钥存储」）本身就是一次复述。这条口径此前改过两版，两版都在真机上失败过（只留措辞 → 塌成一条占位；只留粒度 → 整段照抄）。
  2. **技能规则按 orchestrate 主控的口吻写，却挂在八个智能体上**：`_BASE_TAIL` 第 3 条要求「必须优先委派 `reviewer` 子 Agent」，第 4/5 条要求「主 Agent 用 `list_pr_comments` 取回原文、用 `comment_pr` 在 PR 上回复」。可 qa / analysis / plan / review 四个形态都没有 `task`，其中 qa / analysis / plan 也没有 `comment_pr`（`tools/register.py` 的 `_FORM_TOOLS`），review 形态与 reviewer 自己的角色段写的又都是「你自己审」—— `REVIEWER_PROMPT` 因此叫 reviewer **去委派 reviewer**。
  3. **plan 形态「零写操作」对撞「第一件事是写记忆」**：`_PLAN_WORKDIR` 把记忆写成「**只读**」，`_PLAN_RULES` 写「不产生任何写操作」、`_ROLE_PLAN_MAIN` 写「本轮一个文件都不落」，而 intent=plan 时由 `IntentMiddleware` 注入的 `_MEMORY_GATE` 要求「本轮的**第一件事**是用 `save_repo_memory` 更新一次记忆」—— plan 形态确实持有这件工具。模型照「零写操作」跳过第 0 步是**合理读法**（与「记忆写回路径被掐断」那次同形）。
  4. **`GIT_PROMPT` 把「怎么写代码」挂在四个不写代码的形态上**：共用段里列着 `git clone` / `checkout -b` / `add -A` / `commit -m` / `push origin`，而 orchestrate 主控（不亲自写代码）、plan 主控与 planner（出方案零动手）、review 主控与 reviewer（全程只读）都收到了这份说明书；`_PLAN_METHOD` 第 3 步还让 plan 形态自己去 `git push` + `create_pr`（它没有 `create_pr`）。
  5. **共享「工作区规则」逐目录声称可写，与策略层不符**：`/ai_coding/review/`：「可写」、`/ai_coding/tmp/`：「可写」两行是写给所有形态的，而 `safety/policy.py` 里 qa 写根为空、plan 写根为空、analysis 只有 tmp。同一段的「成果要及时落地：代码 commit + push」也是无条件句，与只读形态自己的「不 `git add` / `git commit` / `git push`」正相反。
  6. **三处「第一件事」竞争 + 记忆归属两说**：`_MEMORY_GATE` 说第一件事是写记忆，`TODO_PROMPT` 与主控角色段说先把这一轮看懂再落清单（都是绝对句，先后只能靠模型自己排）；`_BASE_ENV` 说「写回只由主智能体用 `save_repo_memory` 完成」，只读形态的能力边界又说「没有 `save_repo_memory`」；联网搜索规则把记忆**文件路径**列为「优先读」，而仓库记忆规则刚说「不要再 `read_file` 读它」。
- **改动**：
  - `src/ai_coding_harness/agent/prompts.py`（清单口径）：`TODO_PROMPT` 第 2 条末尾与第 3 条**合成一条**——粒度（一项对应方案的一步、不许塌成占位）与措辞（用你自己的话、一条一行、不搬 goal / done_criteria 原文）写在同一句里，示例改成「步骤 2：改密钥存储」（对两条都成立）；`_CODER_ROLE` 第 3 步同口径，第 5 步那条「你的清单只管自己的动作，不复述方案」改写成「清单与方案的分工」（方案原文与逐步状态在方案面板，清单写一步在动什么），不再出现「只写你自己的动作 / 不复述方案」这类二值禁令。
  - 同上文件（技能规则归属）：`_BASE_TAIL` 的「技能规则」收敛成 4 条通用话（哪件事用哪套 skill + skill 只是方法），**删掉委派与回评两条**；`_ROLE_ORCHESTRATE` 的「职责一」补上「**审查一律派 `reviewer`**（用 `code-review` skill 的方法），主控自己不做审查」与「先取回审查原文（`list_pr_comments` 或 `/ai_coding/review/` 下的报告），**不得凭记忆复述**，据此再派 `coder`；修复范围等于用户点名的问题，随后 `comment_pr` 回评」；`_REVIEW_METHOD` 第 2 步点名 `code-review` skill 的检查清单（review 主控与 reviewer 共用，两处同时落地）。
  - 同上文件（plan 记忆）：`_PLAN_WORKDIR` 那行不再写「只读」，改成「（StoreBackend）——本角色的读写权限见「你的角色」段」；`_PLANNER_ROLE` 新增「## 仓库记忆只读」段（StoreBackend，**只读**；写回由主智能体负责）；`_PLAN_RULES` 与 `_ROLE_PLAN_MAIN` 的写白名单改成「本轮的写**只有两处**：`save_repo_memory`（第 0 步）与 `storePlan`」，并把「一个文件都不落」限定成「**不落沙箱文件**」。
  - 同上文件（git 段拆分）：`GIT_PROMPT` 只留规则与**只读**命令（`git status` / `diff` / `diff --stat` / `log` / `show`）+ 凭据禁令 + 分支命名 + 「PR 用工具创建、合并人工」；新增 `_GIT_WRITE_STEPS`（克隆 / 更新 / 建分支 / 提交 / 推送五条命令），**只拼进 `CODER_PROMPT`**。`_PLAN_METHOD` 第 3 步写明这一步**由 coder 执行**。
  - 同上文件（工作区与记忆口径）：`_BASE_ENV` 的「工作区规则」不再逐目录声称可写，改成「**你能写进去哪些目录，以你这份提示词的「工作目录」段为准**」，只保留硬不变量（policies / skills 只读、output 谁也不写、.secrets 不可读写）；生命周期那句改成「**这一轮改了代码就及时 commit + push**（只有改码的形态适用）」；`_BASE_ENV` 的记忆写回归属改成「写回只由**持有 `save_repo_memory` 的主控形态**完成……只读形态与三个子智能体都不写记忆」；联网搜索规则的「仓库上下文」改成指向注入的「仓库记忆」块（别再读盘）。
  - 同上文件（工作目录补全）：`_ROLE_ORCHESTRATE` 的「工作目录」补上 review（可写，审查原文）与 tmp（可写），project 那行从「**只读**」改成「**本轮不写**（写码由 coder 负责；核实现状只用只读命令）」—— 边界允许写、口径不许写，说「只读」是句假话；`_REVIEW_WORKDIR` 补上仓库记忆（**只读**）；`_ROLE_QA` / `_ROLE_ANALYSIS` 的工作目录各补一行仓库记忆（**只读**，写回不归这一轮）。
  - 同上文件（顺序）：`_MEMORY_GATE` 第一句改成「在 `write_todos` 之前、`storePlan` 之前，也在派 `planner` / `coder` / `reviewer` 之前（它是记忆写回，不算「落文件」）」—— 把三处「第一件事」的先后钉死，并点明它不违反「不落文件」。
  - `start.bat` / `start.sh`：`PYTHON=` 默认值从已经失效的 `D:\python-venv\ai_coding\Scripts\python.exe` 改成 conda 环境 `aicoding` 的解释器（`D:\developmentBinary\miniconda3\envs\aicoding\python.exe`，`conda env list` 可见），并各加一段**启动前自检**：真跑一次 `python -c "import sys"`，失败就打印「Python 无法启动 + 项目运行环境是 conda 环境 aicoding」并退出。用「试跑」而不是「判文件在不在」是有理由的：那个坏掉的 venv 里 `Scripts\python.exe` **文件是在的**（255KB），`if not exist` 判不出来，只有真跑一次才知道它起不来。`start.bat` 是 **GBK 编码**，改动是用 `[System.Text.Encoding]::GetEncoding(936)` 读—改—写完成的，校验过：无 BOM（首三字节 `40 65 63`）、无 U+FFFD、按 UTF-8 严格解码仍失败（说明编码没被改坏）、`启动后端` / `未找到 .env` 等既有中文全部完好。
  - `scripts/check_prompts.py`（**新增**）：渲染八个成品提示词并扫七类跨形态冲突（C1 清单口径 / C2 越界委派 / C3 越界回评 / C4 越界写命令 / C5 plan 记忆 / C6 目录可写 vs `policy_for` 的实际写根 / C7 只读形态的落地要求）。它只加载 `profiles` / `prompts` / `policy` 三个纯模块（`config` 打桩），因此**不依赖第三方包**，在装不起依赖的机器上也能跑；`--grep 仓库记忆` 可按关键字打印各形态的原文。改前 44 条、改后 0 条。
  - 测试：`tests/test_prompts.py` 的 `TestTheListCarriesThePlanSteps` 按合并后的口径重写（新增「相反要求不许回来」一条：既钉「一步一条」在，也钉「清单只写你自己的动作 / 不复述方案」**不在**）；新增 `TestSkillRulesBelongToTheFormThatDoesTheWork`（只有 orchestrate 提委派与回评、`code-review` skill 落在审查那三份）与 `TestGitWriteCommandsOnlyReachTheCoder`（写命令只在 `CODER_PROMPT`、只读命令留在共用段、plan 末步写明由 coder 执行）；`TestNoDanglingReferences` 的「子 Agent 名字要对」改钉 `MAIN_AGENT_PROMPT`（名字归唯一挂子智能体的形态）；`TestReviewReply` 的「不得凭记忆复述」改钉 `MAIN_AGENT_PROMPT` + `REVIEW_AGENT_PROMPT`（原断言钉在共用段，而那句已经挪走）；记忆写回归属那条断言随文案更新。`tests/test_profiles.py` 的 `test_plan_form_lands_no_files_at_all` 改写成 `test_plan_form_writes_only_the_memory_and_the_plan`，并新增三条（plan 记忆不再写成只读、review 形态的写目录与记忆只读、共用目录表指向按形态的「工作目录」段）与一条「没有形态收到无条件 commit + push」。
- **关键决策**：
  - **清单口径走「一步一条 + 用自己的话」，不改回任何一版**。粒度不能丢（丢了两版实测都塌成占位），措辞也不能丢（丢了就整段照抄）；两者能同时成立的写法只有「条目 = 步骤号 + 你自己的一句话」，所以示例也必须换成对两条都成立的形状 —— 原来那个「执行方案步骤 2：改密钥存储」正是矛盾的另一半。
  - **委派与回评搬进 `_ROLE_ORCHESTRATE`，而不是给共用段加限定词**。共用段加「你这条不适用」的补丁，下一个人加形态时还会漏；搬进唯一具备该能力的形态段是结构性的（`_FORM_TOOLS` 与 `profiles.py` 已经保证「只有 orchestrate 挂子智能体、只有它拿 `comment_pr`」）。
  - **plan 的记忆写成「见你的角色段」而不是一律改成读写**。`_PLAN_WORKDIR` 是 plan 主控与 planner **共用**的，而两者权限相反（主控持 `save_repo_memory`、planner 连写工具都没有）—— 共用段不能替谁下结论，只报事实（路径 + StoreBackend），权限归各自角色段。
  - **git 写命令只给 coder，而不是给各形态各写一句「你别照着做」**。与仓库一贯的取舍一致（先撤选项）：plan / review 需要的只是「方案末步是提交 PR」和「怎么读 diff」，`git add` / `commit` / `push` 的写法对它们是纯噪声 —— 这次是从共享文本里**移走**，不是再加一句劝阻。
  - **共用段不再逐目录声称可写**，改成指向按形态的「工作目录」段。目录权限本来就随形态变（同一张表不可能对五个形态都成立），沿用「说不清就别在这里说」的老口径（与上一批撤 output 同因）。
  - **orchestrate 的 project 写根这次没动 policy，只把提示词里的「只读」改成「本轮不写」**。policy 仍留着 project 写根（它可能在派发前后自己跑 git 写操作，撤掉有真实风险），但提示词不能再说「只读」—— 那是句与边界相反的假话。这一处的最终形态（撤写根 vs 保留写根 + 明说口径）仍待你定，本批只是消掉「假话」那一半。
- **验证**：
  - `scripts/check_prompts.py`：改前 **44 条**冲突（C1×8、C2×7、C3×3、C4×13、C5×1、C6×9、C7×2，去重后逐条打印），改后 **0 条**。
  - 另一组按新测试口径写的 13 条不变量自检全部通过（orchestrate 独占委派与回评、`code-review` skill 落在审查三份、plan 记忆不再只读且写回进白名单、planner 记忆仍只读、写命令只在 coder 那份、清单两条不再互相否定、四个单智能体形态都无无条件 commit + push）。
  - `ruff check` 本次改动文件（`prompts.py` / `scripts/check_prompts.py` / 两个测试文件）**All checks passed**；`py_compile` 四个文件通过。
  - **全量 `pytest`：788 条全过**（`PYTHONPATH=src`，解释器 `D:\developmentBinary\miniconda3\envs\aicoding\python.exe`，9.48s；两条 warning 是既有的 v3 流式协议实验性提示，与本次改动无关）。跑第一遍时抓到一条**真失败**：`TestReviewReply::test_reply_must_quote_source_not_memory` 原来钉「不得凭记忆复述」在 `BASE_PROMPT` 里 —— 那句正是本批从共用段挪进 orchestrate 角色段的，断言跟着改成钉两个能回评的形态（已一并修正后才全绿）。
  - 运行环境（用户确认 + 本机实测）：项目的运行环境是 **conda 环境 `aicoding`**（`conda env list` 可见，实质路径 `D:\developmentBinary\miniconda3\envs\aicoding`，Python 3.14.0，依赖齐全）。标准跑法：`conda activate aicoding` 之后 `PYTHONPATH=src python -m pytest -q` —— 这个环境里**没有**把本项目 `pip install -e .` 进去，`src` 必须进 `PYTHONPATH`（`start.bat` / `start.sh` 里本来就是这么设的）。实测 `conda run -n aicoding --no-capture-output python -m pytest -q` → **788 passed**。
  - 另一套环境（顺手把话说准，免得下次误判）：`D:\python-venv\ai_coding` 这个 venv **现在能用** —— 它把本项目 editable 装了（`ai-coding-harness 0.1.0 → D:\python-project\ai-coding-harness`），pytest / deepagents 齐全，`-m pytest` 可直接跑。（**2026-09-24 我第一次检查时它是坏的**：当时 `pyvenv.cfg` 的 `home` 指向已被删除的 `D:\development_software\python3.4.17`，`Scripts\python.exe` 报「did not find executable」；现在该文件指向 `D:\developmentBinary\python3.14`。本批的启动脚本按用户指定的 conda 环境改，两套都能跑，要换回 venv 只需改那一行。）
- **遗留**：
  - **改动尚未在活的后端生效**（活进程比这些文件旧），要重启才算数；提示词是构建期读盘，新建会话即拿新的，重启只是让活进程也换过来。
  - **`start.sh` 没能做语法检查**：本机 PATH 上没有 `bash`（`Get-Command bash` 为空），`bash -n start.sh` 跑不了，改动靠与既有行同形 + 人工核对。`start.bat` 那半的 `if errorlevel 1` 构造用等价的 `cmd /v:on` 一次性命令验过（conda 解释器 → `OK`）。
  - **两个启动脚本本批都改了，但它们各自指向的解释器需要你确认**：现在指 conda 环境 `aicoding`（按你说的口径）；如果你日常其实用 `D:\python-venv\ai_coding`（那套是 editable 装的，能直接 `import ai_coding_harness`），把 `PYTHON=` 换回它即可 —— 两套我都实测过能跑。
  - **`orchestrate` 的 project 写根仍与它的口径并存**（本批只把「只读」改成「本轮不写」）：要彻底一致就得撤 policy 的写根，风险是主控在派发前后自己跑 git 写操作时会被拒 —— **等你定**。
  - **共用段与角色段的划分仍是手工维护的**：`scripts/check_prompts.py` 只覆盖这七类已知冲突，新模式（例如新形态、新技能）还得靠人看；如果以后这类矛盾再出现，优先往这个脚本里加一条规则，而不是只加测试。
  - 上一批记的两条仍未动：`save_repo_memory` 的「不像记忆就拒写」守卫、需求表已有行的「分支 / 链接」纠正。
  - README 的中间件表仍缺 `middleware/memory.py`、进度总览的测试数仍停在 692（与现状不符），本批没碰。

### 2026-09-18

#### `/ai_coding/output/` 全形态收口：一个文件都不落（qa / analysis / plan / orchestrate）

- **动机**：两件事，同一根。
  1. **2026-09-22 22:34 那轮**：用户说「给我一份仓库记忆文件」，qa 形态把整份记忆复制进 `/ai_coding/output/wanqi-mall-memory.md`，回了一句「仓库记忆文件已整理并写入：`/ai_coding/output/wanqi-mall-memory.md`」—— 这正是 `_ROLE_QA` 里明令禁止的那句话形状。它不是绕过了守卫，而是**从例外里钻出去的**：那行「`/ai_coding/output/`：本轮不用 —— 结论写在回复里；**只有用户明确要文件时才写入**」的后半句，把「这一轮没有交付物，只有回答」整条规则吃掉了。策略层同一处也留着口子（qa 的写根里有 output），于是模型照例外走时边界层一路放行。（记忆库本身没被写脏：qa 从来没调过 `save_repo_memory`，它只是把记忆全文复制到了别处。）
  2. **顺着这条线查下去，`/ai_coding/output/` 根本没有回传通路**：全仓只有三处碰它 —— `config.py` 的默认值、`policy.py` 的目录映射、`provider.py` 建目录时的 `mkdir -p`；API / service / 前端**一处都不读它**，工作区卷也只挂 `project`（`volumes.py:30`），写进去的文件随沙箱销毁。而 analysis 的口径一直是「成篇的产物可以落成文件」，技能 `repo-bootstrap-analysis` 第 6 步还明文要求把报告写成 `bootstrap-<repo>.md` —— 那是个**用户取不到**的落点，与 qa 那次「把答案藏进文件」是同一件事，只是还没人踩。
  3. **同一根理由也适用于 `plan` 与 `orchestrate`**。这两处当时仍留着 output 的写根，`plan` 尤其自相矛盾：它自己的 `_PLAN_WORKDIR`（「产出：方案写入 DB，不落文件」）与 `_PLAN_RULES`（「不落文件…不在 `/ai_coding/tmp` 里搭工程」）两处都说不落文件，只有「可写范围」那段写着 output 可写；而做同一件事的子智能体 `planner` 早就是空写根 + 撤写工具（注释：「方案写库，不再落文件（写文件等于同一件事记两遍）」）。于是两处一并收掉。
- **改动**：
  - `src/ai_coding_harness/agent/prompts.py`（`_ROLE_QA`，2 处）：工作目录那行改成「`/ai_coding/output/`：**本轮不用、也不写** —— 问答的交付物是回复本身」；回答口径那行补上落点 ——「用户点名要某份文件（仓库记忆、某段代码、一张表）时，**要的是里面的内容**：把全文贴进回复（需要时说明它出自哪个路径），不要落成文件再把路径给出去」。
  - `src/ai_coding_harness/safety/policy.py`：「qa」行改成 `((), frozenset({"write_todos", "write_file", "edit_file"}))` —— 写根置空 + 撤掉写工具（与 planner 同形），并带一段注释说明为什么这里是「撤选项」而不是「再加一句劝阻」。`write_todos` 一并撤掉：一轮问答没有多步进度可报，清单只会多出一份「查一下、答一下」的空转。
  - `src/ai_coding_harness/agent/prompts.py`（`_ROLE_ANALYSIS`，3 处）：角色段「成篇的产物可以落成文件」→「交付物就是这份结论本身，写在回复里 / 成篇的报告也一样进回复，不落文件」；工作目录段的「`/ai_coding/output/`：分析产物（**可写**）」→「**本轮不写**」，并补一行「`/ai_coding/tmp/`：中间过程文件（**可写**）—— 取证的中间产物放这里，不必进回复」；分析工作法第 5 条从「成篇产物写进 output 后，回复里仍要讲清」改成「成篇的结论直接写进回复（长报告也一样）：不要先落成文件、再回一句「已写入文件」把答案藏起来」。
  - `src/ai_coding_harness/agent/prompts.py`（`_BASE_ENV`，全形态共用）：output 那行先改成「**问答与分析这两轮不写**（各自的「工作目录」段写明）；其余形态按需可写」；本批再收一步成「**任何形态都不写** —— 那个目录没有回传通路（API 与前端一处都不读它），写进去的东西用户取不到，结论一律进回复」—— 此时「其余形态」已经没有可指的对象了。同一段里「结论写入对应输出目录」也改成「结论写进回复 —— 落成文件的话随沙箱一起销毁，用户取不到」。
  - `src/ai_coding_harness/agent/prompts.py`（`_READONLY_BOUNDARY`，qa / analysis 共用）：「本轮你**只有只读工具**」→「本轮你**不与仓库发生写入**」。因为 analysis 保留 tmp 的写根之后，手里确实有 `write_file` / `edit_file`，原话成了假话；`没有 `task`（派不了子智能体）` 那半句原样保留（测试钉着它）。**没有**把 `write_file` / `edit_file` 加进这句枚举 —— 加了对 analysis 又会是假话。
  - `src/ai_coding_harness/agent/prompts.py`（`_ROLE_ORCHESTRATE` 工作目录）：那行先去掉了「问答」，本批**整行删掉** —— 多智能体主控不写任何结论文件。
  - `src/ai_coding_harness/safety/policy.py`（「analysis」行）：`(("output", "tmp"), frozenset())` → `(("tmp",), frozenset())`，注释写明为什么连 output 一起撤（用户取不到）。写工具**留着** —— 它还要写 tmp。
  - `sandbox/skills/repo-bootstrap-analysis/SKILL.md`（5 处）：目录声明的「输出目录」、红线段的「可写目录」、执行步骤第 6 步（标题也从「输出结论到 `/ai_coding/output`」改成「输出结论」）、「输出格式」段的引导句、末尾红线 —— 一律从「写 `/ai_coding/output/bootstrap-<repo>.md`」改成「按这个结构直接回复」。
  - `docs/workspace-directories.md`：先把 `qa` 与 `analysis` 的 output 两格改空（analysis 的 tmp 保留），并在规则 5 里写上「那个目录没有回传通路」这条理由；本批再把总览表的 output 行从「条件可写」改成「**谁也不写**」，矩阵里 `orchestrate` 与 `plan` 两行的 output 格清空，规则 4 改写成「出方案不落文件（主控 `plan` 与 `planner` 同形）」，规则 5 改写成「`/ai_coding/output/` 谁也不写」。
  - 测试：`tests/test_profiles.py::test_qa_closes_down_the_output_directory` 加钉「例外不见了」与「要文件时有落点」；把它旁边那条 `test_analysis_keeps_the_output_directory` **反过来钉**成 `test_analysis_closes_down_the_output_directory_too`（output 不写 + tmp 可写 + 结论进回复 + 旧口径都清了）；`tests/test_prompts.py::test_it_says_which_tools_are_missing` 从钉「只有只读工具」改成钉「不与仓库发生写入」并断言「只有只读工具」**不在**；`tests/test_boundary.py`（`test_each_name_resolves_to_its_own_scope` 的参数表）qa / analysis 两行期望值同步改成 `(QA_FORM, (), ("write_todos", "write_file", "edit_file"))` 与 `(ANALYSIS_FORM, ("/ai_coding/tmp",), ())`。本批又把 `orchestrate` 那行的 output 去掉、`plan` 那行换成 `(PLAN_FORM, (), ("edit_file", "write_file"))`，并在 `tests/test_profiles.py` 新增两条：`test_plan_form_lands_no_files_at_all`（plan 提示词里「**本轮一个文件都不落**」在、旧的「（分析产物）可写」不在）与 `test_no_form_is_told_the_output_directory_is_writable`（五个形态一律：共用段写着「任何形态都不写」，且不再出现「结论写入对应输出目录」）。
  - `src/ai_coding_harness/safety/policy.py`（本批）：`"orchestrate"` 由 `(("project", "tmp", "output", "review"), frozenset())` 收成 `(("project", "tmp", "review"), frozenset())`；`"plan"` 由 `(("output",), frozenset())` 收成 `((), frozenset({"edit_file", "write_file"}))` —— 与子智能体 `planner` 完全同形，注释里写明了「三处两说」这件事。
  - `src/ai_coding_harness/agent/prompts.py`（本批）：`_ROLE_PLAN_MAIN` 的「可写范围」段从「只有 `/ai_coding/output/`（分析产物）可写…除了这一个目录，出方案阶段不落任何文件」改成「**本轮一个文件都不落** —— 写文件的工具已撤掉，调了会被直接拒绝…方案用 `storePlan` 落库，分析结论写进回复」；`_PLAN_METHOD` 里「分析产物与仓库记忆由主智能体落盘」改成「分析结论与仓库记忆由主智能体负责（前者进回复，后者用 `save_repo_memory` 写回）」。
  - `src/ai_coding_harness/agent/profiles.py`（本批）：`analysis` 与 `plan` 两行注释同步 —— 「产出可落 `/ai_coding/output/`」→「产出只进回复（output 没有回传通路）」，`plan` 补上「不落文件 —— 与子智能体 planner 同形」。
  - `adhd/AGENT.md`（本批）：输出形态规则的**范围**那句删掉「写进 `/ai_coding/output/` 的文件产物」，只留 PR 评论与仓库记忆 —— 那种产物不再存在，留着等于给模型一个落点暗示。
- **关键决策**：
  - **两层一起收，不只改提示词。** 只劝不撤选项，下一个「用户明确要文件」还会照旧走一遍 —— 本批的起因正是一行例外吃掉了一整条规则。
  - **但「要文件」这个正当需求不能变成死路。** 撤掉 output 之后，「给我一份仓库记忆」仍然得答得出来，所以口径补的是**内容进回复**（需要时带出处），不是「给不了」。
  - **撤 output 的硬依据是「用户取不到」，不是「文件不好」。** output 既没有回传通路、也不随卷保留，写进去不如写进回复 —— 两个只读形态先撤，`plan` 与 `orchestrate` 本批跟着撤。后者真正的交付物本来就有别的通路（方案落库 + 右侧面板、PR + 审查面板），output 只是顺带的落点；收完之后这条通路**既没有写方、也没有读方**。
  - **`plan` 与 `planner` 必须同形。** 两者做的是同一件事（`storePlan` 落库），分叉只会让「出方案到底落不落文件」随意图变。
  - **analysis 保留 `tmp` 与写工具。** 取证的中间产物（大段 grep、命令输出）需要一个**不进上下文**的落脚点；`tmp` 从来没被当成交付物，撤了只会让长分析无处堆放。代价是那句「只有只读工具」不能再留 —— 于是改成「不与仓库发生写入」，而不是往枚举里加 `write_file` / `edit_file`（加了对 analysis 又是假话）。
  - **技能按「三种执行者都成立」改，不按形态分叉。** `repo-bootstrap-analysis` 由编码形态、分析形态、`planner` 三处执行，改成「结论进回复」后三者口径一致 —— 比给每个形态各写一份说明划算得多。
- **验证**：`tests/test_profiles.py` / `tests/test_boundary.py` / `tests/test_prompts.py` 三份 **229 条全过**；全量 `pytest` **775 条全过**；本次改动文件 `ruff check` 干净。另做了一次渲染检查：五个形态的成品提示词里都出现共用段的「任何形态都不写」，`成篇的产物可以落成文件` / `分析产物（**可写**）` / `只有只读工具` / 那条例外句 / `结论写入对应输出目录` 一律**不在**，plan 的「可写范围」段也不再提 output；没有引入 4 空格缩进（那会让紧跟的 `##` 标题失效）。
- **遗留**：
  - **改动尚未在活的后端生效**（活进程是 2026-09-22 18:10 起的，比这几个文件旧），要重启才算数（重启由你定）。
  - 上一批记的「`_BASE_ENV` 那句自相矛盾的话」**本批已解**（就是上面那条 `_BASE_ENV` 的改动）。
  - `plan` / `orchestrate` 的 output **本批已收**（见上），收完之后**没有任何形态**能写 `/ai_coding/output/`。
  - **`/ai_coding/output/` 这条通路本身留着没删**：`config.sandbox_output_dir`、`policy._DIR_ATTRS["output"]` / `_DEFAULT_ROOTS["output"]`、`provider.py` 建目录那行现在都成了死代码（无写方、也无读方）。没删的理由是删配置字段会牵连 `.env` / `.env.example` 的键，而收益只是清爽；要彻底摘掉另开一批。
  - **`orchestrate` 的 `/ai_coding/project` 写根与它自己的提示词不一致**（既有问题，本批没动）：policy 给了写根、文档矩阵也跟着写「读写」，但提示词 :165 写着 project **只读**（「写码由 coder 负责」），同段「约束」还要求「核实现状只用只读命令」。撤 policy 的写根有实际风险（它可能在派发前后自己跑 git 写操作），改提示词又会与同段的只读要求打架 —— 要么撤写根、要么改提示词并写明理由，等你定。
  - 同一类的轻口子：`orchestrate` 的 `tmp` 与 `review` 写根没写进它那份「工作目录」列表（边界允许，提示词没提）。
  - 「`save_repo_memory` 加『不像记忆就拒写』守卫」（起因是 2026-09-21 那次 `content='OK'` 的写坏）**仍没做** —— 之前提过、当时建议先不动。

#### 提示词还原 + 记忆时间戳改由平台盖章

- **动机**：两件事，都是「谁在写这份长期记忆」的问题。
  1. 你手工改过一版提示词，其中几处改坏了：几段文字被顶到 4 空格缩进（Markdown 里那是代码块，会让紧跟的 `##` 标题失效），`_MEMORY_GATE` 整段被删。后者不只是文案问题 —— **plan / coding 两个形态从此再没有任何指令要求写记忆**，而 `refresh_memory` 只刷新已存在的文件、从不建档，于是 `save_repo_memory` 这条写回路径等于被掐断。
  2. 记忆文件头的 `最后更新` 仍由模型填。它没有可靠的钟（只能读注入的「当前时间」或跑 `date`），填出来的是一个**猜的时刻**；下一轮再看那个文件，谁也说不清那一行是本轮盖的还是上一轮编的。「更新于」列同理。
- **改动**：
  - `src/ai_coding_harness/agent/prompts.py`（7 处）：`TODO_PROMPT` 第 3 条（执行方案时一步一条）与 `_PLAN_METHOD` 第 1 步（先看系统提示里的「仓库记忆」块、不要再 `read_file`）按原措辞还原；`_PLANNER_ROLE` / `_ROLE_PLAN_MAIN` / `_REVIEWER_ROLE` / `_ROLE_REVIEW_MAIN` 里 4 处泄漏的 4 空格续行缩进去掉（常量本身的代码级缩进没动）；**重建 `_MEMORY_GATE`**（「出方案 / 派发前必须完成」「禁止颠倒顺序或与派发并行」「平台已经在每轮开始前替你查过」「不要再自己查一遍」「不要断言『仓库没有未合并的 PR』」「由平台按 Gitee 的实际状态回填」「本轮只写一次」「不要为措辞、空格、排版微调重复写回」），拼进 `plan` / `coding` 两条指令（`review` 不带，不变）。「本轮的第一件事是用 `save_repo_memory`」刻意写在**任何「派 `coder`」之前** —— 这个顺序本身是被测试钉住的不变量。
  - `src/ai_coding_harness/agent/prompts.py`（`_BASE_ENV` 的「仓库记忆文件规则」）：时间戳口径改写 —— 「`更新于` 与 `最后更新` 都是平台在写盘那一刻盖的，**时间戳一律不用你填**，也不必为它们跑 `date`」；散文里的日期（「变更记录」那种）另说，一律取自注入的「当前时间」或 `date`，**不许凭记忆编写**。
  - `src/ai_coding_harness/agent/memory_reconcile.py`：新增 `_replace_stamp(line, now)`（换掉一行里的时间戳，**其余字段逐字保留**）/ `_stamp_lines(text, now)`（没有头行时**原样返回**）/ `restamp(text, *, now)` / `same_except_stamp(old, new)`（两边都挖空时间戳再比，任一侧没有头行时退回逐字比较）；`_seed` 补出来的行盖上 `updated=now` —— 那一行是平台本轮刚发现的，那一刻就是它进表的时刻，留空渲染成 `—` 等于说「不知道什么时候进来的」，而这件事我们恰好知道。
  - `src/ai_coding_harness/tools/memory.py`：落盘前统一 `restamp`；「内容没变就不写盘」的判据换成 `same_except_stamp`；`noted`（「已按 Gitee 实际状态回填」）**收进 `pulls is not None` 分支** —— 原来放在外面，`pulls is None` 时 `restamp` 也会让 `filled != content`，于是没查过 Gitee 的回执里也会冒出「已回填」这句假话；docstring 与工具描述同步改成「时间由平台盖，你写的不生效」。
  - `sandbox/policies/repo-memory-template.md`、`docs/repo-memory-template.md`：头行示例改成 `{由平台盖写盘时间}　｜　{由平台回填编号/链接}`，`更新于` 单元格改成 `{由平台盖}`，表下说明点明「`更新于` 与文件头的时间戳由平台在写盘那一刻盖章」。
- **关键决策**：
  - **还原措辞，不放宽测试。** 那 15 条失败是提示词被改坏导致的；改测试等于把一处功能缺口（没人再写记忆）固化下来。
  - **`restamp` 只换时间，不重建整行。** 重建是 `reconcile` 的职责 —— 只有它知道「最近 PR」该写什么。这里凭空补一行会把「没有 PR」说成事实；老格式里那个多余的 `当前分支` 也照原样带着，等 `reconcile` 下次重建时清掉。
  - **「没变」要在挖掉时间戳之后比。** 否则那一次盖章让每轮内容都「变了」，「没变就不写盘」当场失效，还连带毁掉 `changed` 这个事实（轮末打底正是靠它决定写不写）。
  - **不给「更新于」列做整体盖章。** 需求行这格记的是**状态变化那一刻**，状态没变的行本来就该原样带过；每轮重盖会让每次写入都成为一次内容变更，正是上一条要避免的。所以提示词说的是「不用你填」，而不是「你填了也会被改」—— **状态没变的那一行，模型写进去的值暂时不动它**。
  - **`_seed` 盖 `now`** 与「已存在的行不重盖」不冲突：那是「这一轮刚发现的新事实」，有确定时刻。
- **验证**：`tests/test_prompts.py` **94 条全过**（还原后无需改测试）；`tests/test_memory_tool.py` 新增 `TestTheWriteStampsItsOwnTime`（模型编的时刻不落盘、头行的「最近 PR」由平台按查到的列表重建、只差一个过时时间戳不算内容变更）+「没接 PR 查询那条路也盖章、但回执不能说『回填』」，并加了一个钉住时钟的 fixture（`STAMP`），写盘的断言从「时间大概对了」变成逐字相等；`tests/test_memory_reconcile.py` 新增 7 条（`restamp` 四种形态、`same_except_stamp` 的严格与宽松两侧、模型写的时间在 `reconcile` 这一层**原样留着**、盖章由调用方在之后做），并给「补进来的行」补上 `更新于 == now` 的断言。全量 `pytest` **773 条全过**（上一批 763，净增 10）；本次改动文件 `ruff check` 干净。
- **遗留**：
  - **改动尚未在活的后端生效**，要重启才算数（重启由你定）。
  - 「已有需求行的分支 / 链接不纠正」这条**仍然存在**（前几批就记着），本批没碰。
  - 「`更新于` 在状态没变的行上仍可被模型写脏」是上面那条决策的代价，要收紧就得接受每轮都算一次内容变更。

#### 记忆全文注入系统提示：所有智能体都挂，模型不再自己读那份文件

- **动机**：仓库记忆一直靠模型自己 `read_file` 去读，于是每轮开头都白花一次工具往返（每多一次工具调用，就是整份上下文再发一遍）。而记忆全文其实在 `abefore_agent` 就已经被读到手里了（plan / coding 两个主控为了对账本来就要读它）—— 读到手的东西再让模型跑一次工具取一遍，没有任何收益。
- **改动**：
  - `src/ai_coding_harness/agent/middleware/memory_gate.py` → 重命名为 **`memory.py`**（内容不再只是闸门）：`MemoryReconcileMiddleware` 改名 `MemoryMiddleware` 并新增 `awrap_model_call`，把记忆块追加到系统消息末尾（与 `IntentMiddleware` 同一套做法）；`all_prs` 变成可空 —— `None` 表示「这次没有查 PR 的能力」，只读注入、不查不写不拦。新增 `MEMORY_BLOCK_TITLE`、`_memory_block(path, text)`、`_read_text(backend, path)`、`Refreshed`（`result` + 盘上原来那份 `current`）；`refresh_memory` 改为返回 `Refreshed`。`build_memory_gate(...)` 换成 `build_memory_middleware(settings, repo, backend, form=None)`，**每个形态都返回一个实例**（不再有「其余形态返回 None」）。
  - `src/ai_coding_harness/agent/middleware/__init__.py`：导出改名 + `MEMORY_BLOCK_TITLE` / `Refreshed` / `build_memory_middleware`；模块清单里 `memory_gate` 一条改写为 `memory`（注入 + 闸门两件事），「每个角色各 new 一个」的例外类补上 `MemoryMiddleware`。
  - `src/ai_coding_harness/agent/graph.py`：`build_agent` 的 `reconcile=` 改名 `memory=`（语义从「启动闸门」变成「本轮看不看得到记忆」）；列表位置不变，仍在 `BoundaryMiddleware` 之后。
  - `src/ai_coding_harness/agent/subagents.py`：`build_subagents` 新增 `memory_factory`，每个角色在边界之后各挂**一个新实例**（`form=None` ⇒ 不带 `all_prs`）。
  - `src/ai_coding_harness/session/manager.py`：主控传 `memory=build_memory_middleware(..., profile.form)`；子智能体传 `memory_factory=lambda: build_memory_middleware(...)`（同一个工厂给每个角色现造一份）。
  - `src/ai_coding_harness/agent/prompts.py`：「仓库记忆文件规则」整段改写为「系统提示里有一段「仓库记忆」块 —— 每个智能体、每个形态每轮开始都有，就是该文件的当前全文，**不要再 `read_file` 读它**」；写回口径同步改成「不必先读盘，拿块当底稿合并」；`_MEMORY_GATE` 去掉「本形态每轮注入」这句已经过时的限定；`PLANNER_PROMPT` / `CODER_PROMPT` 里「用 `read_file` 读取仓库记忆」的两步改成「看系统提示里的「仓库记忆」块」。
  - `src/ai_coding_harness/server/service.py`：只改 docstring 里的模块路径引用。
- **关键决策**：
  - **注入每个智能体都挂**（主控三个只读形态 + 三个子智能体，共 8 处），**闸门仍只挂 plan / orchestrate 两个主控**。闸门只挂主控的理由没变（coder 刚建完 PR、reviewer 再查一遍就会把自己挡在门外）；而注入没有这个副作用 —— 只读形态与子智能体多花的只是**一次 store 读**，换来的是省掉一次工具调用往返。它们本来就不写记忆，为一次注入多查一趟 Gitee 更不划算。
  - **注入的是「刷新后」的文本，不是盘上原来那份。** 闸门那条路 `reconcile` 已经算出新文本；若写回失败，盘上还是旧的，而照着旧事实让模型去合并写回比不注入更糟。只读那条路没有对账，注入的就是盘上那份。
  - **「没有记忆文件」与「记忆是空的」分开处理。** `_read_text` 返回 `None`（没写过）时不注入任何东西 —— 对账器此时也会产出一张只有表头的需求表，可那是「我们打算建的」，不是盘上的事实，讲给模型听等于骗它。空文件（`""`）照注入。
  - **注入块的标题不写「已按 Gitee 实际状态刷新」。** 只有两个主控那条路真的刷了，其余六处只是读盘；挂一句刷新过的话，会让它们以为自己看到的是刚核过的事实。PR 那件事由闸门的提示词去说。
  - **子智能体各拿一份新实例**（因此是 `memory_factory` 而不是传实例列表）：同一回合主控可能一次派几个 `task` 出去，那几个子智能体并发跑，而注入块按轮存在 `self._memory` 上，共用一个实例会互相覆盖。
  - **代价（已知，接受）**：记忆全文随每一次模型调用一起发出去，一轮里有多少次工具往返就发多少遍，且**没有长度上限**。先按实际记忆体量观察，不急着设上限或做摘要 —— 上限设小了会截掉需求表，反而是错的事实。
- **验证**：
  - `tests/test_memory_gate.py`：新增 `TestTheInjection` 5 条（全文贴进去且带「别再读一遍」、追加而不是替换（`SYS` 仍在前）、没写过记忆就不注入、只读那条路不写盘、注入的是刷新后的文本（用写盘必败的后端钉住「盘上旧、注入真」））；`TestTheAssemblyPoint` 重写为「每个形态都挂、闸门只给两个形态」+「每个子智能体各一个不同实例」（用真 `build_subagents` 跑）；`TestThroughTheGraph` 新增一条拿真图跑、从假模型收到的消息里找注入块的用例。
  - `tests/test_prompts.py`：`test_memory_is_not_claimed_to_be_injected`（钉住旧措辞「由运行时注入」不许回来）改写为 `test_the_block_is_claimed_to_exist_for_every_agent`，并新增子智能体两步不能再要求 `read_file` 读记忆的断言。
  - 全量 `pytest` **763 条全过**（上一批 755，净增 8）；本次改动文件 `ruff check` 干净。
- **遗留**：
  - **代码改动尚未在活的后端生效**，要重启才算数（重启由你定）；重启前后端跑的还是「模型自己 `read_file` 读记忆」那一套。
  - 「少一次工具调用」是设计意图，**不是实测结论**；真实会话里 `read_file` 调用次数少了多少，要重启后看日志才算数。
  - 记忆体量上限、以及注入块与 `IntentMiddleware` 的先后（现在是记忆在前、时间 / 意图在后），都留待真实记忆长起来之后再看。

#### 删掉「功能列表」：与需求列表重复的派生视图

- **动机**：功能表与需求表的状态由**同一批 PR** 推出来，功能表的「相关 PR」与需求表的「PR」指的也是同一件事。两张表共存，只多出一条「不许各说各话」的不变量和一整套状态推导代码；功能表独有的「已具备 / 待合入」是需求行「已合入 / 待合入」的同义反复，它的 `说明` 列（「怎么用、依赖什么」）本来就该待在散文节里。
- **改动**：
  - `src/ai_coding_harness/agent/memory_reconcile.py`：删 `READY`、`_FUNC_HEADER`、`_derive_func_state()`、`_reconcile_func()`、`_build_funcs()`；`reconcile()` 只剩「重写 / 补出需求表」一条路径。新增 `_drop_legacy()`：标题**去掉节号后正好等于**「功能列表」的那一节整节摘掉（连标题），并给前一节补一行空行，免得下一个标题贴在上一条内容后面。这里比 `_find()` 严一档（认整名不认尾巴）—— 摘节是删东西，模型自己写的「历史功能列表」不该被牵连。
  - `sandbox/policies/repo-memory-template.md`：删「二、功能列表」整节与表下那条「派生视图」说明；需求表升为「二」、变更记录→三、当前进度→四、约定→五、关键决策→六。表下的平台回填说明改为单表口径（回填 `PR` / `分支` / `状态` 三列，模型只管 `需求` 那一列的名字）。`docs/repo-memory-template.md` 同步，按先前定的顺序（变更记录留「二」，需求表→三、当前进度→四、约定→五、关键决策→六）。
  - `src/ai_coding_harness/agent/prompts.py`、`tools/memory.py`、`tools/register.py`、`agent/middleware/memory_gate.py`：文案从「两张表…你只管功能名 / 需求名 / `说明`」收敛为「需求表…你只管 `需求` 那一列的名字」。**顺带修掉一处不实承诺**：需求表没有 `说明` 列，提示词却让模型「只管需求名 / 说明」。
- **关键决策**：
  - **老记忆里已经写下的「功能列表」整节删掉（自愈），而不是留着不管。** 它已经没有消费者（模板不再有、提示词不再提），留着只会被当成普通散文原样保留，里面的「已具备 / 待合入」永远停在写下的那一刻 —— 一份没人维护、状态必定过期的表比没有更糟。摘节**必然**让 `changed` 为真，所以下一次进 plan / coding 形态时会被真的写掉。
  - **不把 `说明` 搬进需求表。** 需求表是「PR 视角的事实表」：一行一件事，状态可核对。掺进「怎么用」这类描述会让它重新变成半散文，也就没法可靠地按 PR 对账了。
- **验证**：`tests/test_memory_reconcile.py` 删掉 4 条功能表推导用例、新增 3 条（整节摘掉且下一个标题前留空行、别的什么都没变时摘节也算 `changed`、「历史功能列表」这类节名不被牵连）；`with_tables` 改名 `with_table`，并留一份 `LEGACY` 老格式片段专门喂摘节用例。`tests/test_memory_gate.py` 的 `STALE`、`tests/test_memory_tool.py` 的 `MEMORY` 去掉功能表那几节。全量 `pytest` **755 条全过**（上一批 756，净减 1 = 删 4 增 3）；`ruff check` 本次改动文件干净。
- **遗留**：
  - **代码改动尚未在活的后端生效**，要重启才算数（重启由你定）。模板走的是「建会话时整棵上传」，新开的会话拿到的就是新形态，不必重建镜像。
  - 上一批遗留的「已有需求行的分支 / 链接不纠正」**仍然存在**，本批没碰它。

#### 仓库记忆的文件头也交给平台：删掉「当前分支」，时间戳只在真变了时才前进

- **动机**：上一批把两张表从模型手里收回来了，但文件头那一行 `> 最后更新：… ｜ 当前分支：… ｜ 最近 PR：…` 还是模型填。这一行是三样东西里最像「事实」的：时间戳现在由 `agent/middleware/context_inject.py` 逐次注入，模型只要哪一轮忘了读、或按训练记忆写一个日期，盘上就留下一句假话；`当前分支` 更糟 —— 它是**沙箱工作树**的状态，与 Gitee 的 PR 状态、与计划库各有各的口径，写进长期记忆只会互相打脸。留着它就是留一个每轮都要模型手工维护、且没人能核对的字段。
- **改动**：
  - `src/ai_coding_harness/agent/memory_reconcile.py`：`reconcile()` 增加头行重算。按 `> 最后更新` **前缀**定位（不认整行逐字）：有就整行替换、没有就补在标题后 —— 老记忆里带 `当前分支` 的三段式头行因此被整行清掉（自愈，不是丢字）。新头行 = `> 最后更新：{now}　｜　最近 PR：{[#N](url)}`，`最近 PR` 取 `number` 最大的那个（Gitee 编号单调递增）；一个 PR 都没有时写 `—`，认不出编号的条目跳过。
  - `sandbox/policies/repo-memory-template.md`：头行删掉 `当前分支` 一段；表下的平台回填说明补一条 —— 头行同样由平台回填、每次刷新整行重写、别手改。
  - `src/ai_coding_harness/agent/prompts.py`：`_BASE_ENV` 的「仓库记忆文件规则」把「四列由平台回填」扩成「四列 + 文件头的 `最后更新` / `最近 PR`」，并**删掉**「模板头的 `> 最后更新：{YYYY-MM-DD HH:mm}` 用它填」——模型不再被要求碰那一行，不然两方会抢同一行、每轮互相覆盖。`_MEMORY_GATE` 里同口径的一句一并改掉。
  - `src/ai_coding_harness/tools/memory.py`：只改文案（docstring / 工具描述 / 回填写回话术）。**逻辑一行没动** —— 它本来就调同一个 `reconcile()`，闸门与轮末打底也各调一次，三处口径天然一致。
- **关键决策**：
  - **时间戳只在「别的内容真的变了」时才前进。** 反直觉，但必须这样：每轮都刷新 `最后更新`，`changed` 就**恒为真**，「没变就不写盘」这条优化当场失效 —— 每轮白付两次存储往返（闸门一次、轮末打底一次），而且「这一轮到底改没改」这个事实也一起失真（打底正是靠 `changed` 决定写不写）。实现上先拿**原文自己的时间戳**把正文试算一遍，逐字相同才算没变；真的变了才改用本轮的 `now`。副作用是「老格式头行被清」这类自愈会让时间戳前进一次，合理。
  - **删字段而不是留着不管。** 与「补一个 `当前进度` 栏目」这类半接管不同：留着 `当前分支` 就要在每一轮维护一个跨系统口径的字段，收益是零、出错面是全量。删掉之后头行只剩两个都能从平台侧算出真值的栏位。
  - **头行也算「散文之外」的一处例外**，但要写明白：`memory_reconcile.py` 的模块文档与 `prose_lines` 测试助手都点出「`> 最后更新` 行不属逐字保留范围」，免得后来人把它当成 bug。
- **验证**：`tests/test_memory_reconcile.py` 新增 3 条（老格式头行整行换掉且 `当前分支` 消失、缺头行补在标题下、没有 PR 时写 `—`），并把两条「散文逐字保留」用例的比对改成排除头行、给「没变就没变」那条补上「时间戳不许前进」的断言；`tests/test_memory_gate.py` 的 `STALE` 补上头行（否则它每轮都算「变了」，`test_an_unchanged_memory_is_not_written` 会假失败），并在刷新用例里钉住「头行随本轮前进」；`tests/test_memory_tool.py` 的 `MEMORY` 同上；`tests/test_prompts.py` 把「提示词让模型填 `{YYYY-MM-DD HH:mm}`」改为「提示词声明头行归平台，且不再出现那个占位符」。全量 `pytest` **756 条全过**（上一批 753）；`ruff check` 本次改动文件干净。另跑了模板实录：拿真模板当记忆喂 `reconcile`，头行正确落位，第二轮 `changed=False`。
- **遗留**：
  - **改动尚未在活的后端生效**，要重启才算数（重启由你定）。
  - 上一批的「历史记忆不刷」现在更轻了：老记忆下一次进 plan / coding 形态时，两张表与头行会被同一次对账一起补上 —— 但**只在真的写了盘之后**才算数，只读形态（qa / analysis / review）不挂对账，那类会话里的记忆保持原样。
  - 上一批遗留的「已有需求行的分支 / 链接不纠正」**仍然存在**，本批没碰它。

#### 仓库记忆加两张结构化表：需求列表是权威，功能列表是它的派生视图

- **动机**：仓库记忆此前是一份**纯散文** Markdown。「哪个功能已经具备、哪个需求还挂在 PR 上、挂在哪个分支」全靠模型在散文里写 —— 换个措辞、少写一句，事实就丢了。同时「派发前先查 PR、有未合入的就先合并」只是提示词里的一句**请求**，模型可以跳过、可以顺序颠倒、可以把「查不到」讲成「没有」。
- **改动**：
  - `src/ai_coding_harness/agent/memory_reconcile.py`（**新增**）：纯函数对账器（无 IO、不过模型）。`reconcile(text, pulls, *, now) -> Reconciled(text, changed, pending)` —— 认 `## …功能列表` / `## …需求列表` 两个尾巴定位区块，重写表体，**其余散文逐字不动**；两张表缺哪张补哪张（补在「项目概览」之后）。状态由 PR 推导：open → `待合入`、merged → `已合入`、closed 未合入 → `已废弃`（保留不删）；`pulls` 里查不到的编号**一个字符都不改**（查不到 ≠ 已合入，与 `open_pulls` 拒绝把失败返回成空列表是同一条纪律）。功能行状态由它引用的那些 PR 推导（全合入 → `已具备`、有未合入 → `待合入`、全废弃 → `已废弃`），所以两张表不可能各说各话。表里缺的**未合入** PR 会补成待合入行（别人在 Gitee 上手开的 PR 也算未结清的需求）；已合入的不补（那是「变更记录」的事）。体量有界：待合入全留，已结清留最近 `_KEEP_CLOSED = 10` 条。
  - `src/ai_coding_harness/agent/middleware/memory_gate.py`（**新增**）：`MemoryReconcileMiddleware`（只实现 `abefore_agent`）+ 模块级 `refresh_memory(backend, path, all_prs)` + `build_memory_gate(settings, repo, backend, form)`。流水：读记忆 → 查 PR → 对账 → 变了就 `aedit` 整篇写回 → `pending` 非空就抛 `TurnStop`。`PrLookupError` 原样抛出（两个调用方处置相反，写死在里面必然有一处是错的）。marker 自己说清编号 / 标题 / 分支 / 链接与「合并由人工在 Gitee 上完成」—— 它原样进 SSE 的 `ai` 帧与记录正文，不靠前后文（`_MAX_LISTED = 5`，多的折成「另有 N 个未列出」）。
  - `src/ai_coding_harness/agent/graph.py`：`build_agent` 新增 `reconcile=` 参数，实例插在 `BoundaryMiddleware` 之后（越界的先被拒，再谈本轮要不要开始）。
  - `src/ai_coding_harness/session/manager.py`：`get_agent` 里按形态构造 —— **只有 plan / orchestrate 挂闸门**，其余形态传 `None`。
  - `src/ai_coding_harness/tools/git.py`：把 `open_pulls` 里那段 `try/except httpx.* → PrLookupError` 提成 `_pulls_or_raise(*, state, limit)`，新增 `all_pulls()` 走 `state="all"` —— `PrLookupError` 的纪律只留一处，两个入口共享。
  - `src/ai_coding_harness/tools/memory.py`：拒写规则收敛成**只保留「查不到就拒写」**（「有未关闭 PR 就拒写」删掉——coding 收尾那一刻必然存在刚建好的未合入 PR，照原规则模型的补充写入会被一律拒绝）；落盘前用同一个 `reconcile()` 规范化两张表，模型因此**伪造不了「已合入」**。
  - `src/ai_coding_harness/server/service.py`：新增 `_refresh_repo_memory`（轮末打底），在 `chat()` 的智能体分支**事件流抽干之后**按 `profile_for(intent).form == ORCHESTRATE_FORM` 触发。
  - `src/ai_coding_harness/agent/prompts.py`：`_MEMORY_GATE` 第 0 步改写 —— 删掉「自己查 PR、有未合入就停下等合并」，改为「平台已经在每轮开始前替你查过，**不要再自己查一遍**，也不要对用户断言『仓库没有未合并的 PR』」；补「两张表的 PR 编号 / 链接 / 分支 / 状态四列由平台回填，你只管功能名 / 需求名 / 说明」。
  - `sandbox/policies/repo-memory-template.md`：插入「二、功能列表」「三、需求列表」两节（原四~七顺延），表下注明三个状态取值、两张表的派生关系、哪几列由平台回填。
- **关键决策**：
  - **闸门只挂主控，子智能体不挂对账也不挂闸门**。主控闸门抛异常时本轮已经结束，子智能体根本进不去；而同一轮内 coder 刚建完 PR、主控接着派 reviewer，若 reviewer 也查一遍，就会把「刚建好的 PR」当成未合入 PR 把自己挡在门外 —— 评审这步直接做不了。
  - **闸门从提示词挪进 `before_agent`，而不是继续加强措辞**。「软约束」这件事连写十遍也还是软的；抛 `TurnStop` 走的是既有出口（`service.py` 的 `except TurnStop` 包住整个 `astream_events` 循环，收尾按 `state.aborted` 发 `ai` 帧并写进记录正文），**不需要改 stream 消费端，也不需要 `jump_to: "end"`**。已用真图实测：`before_agent` 抛的 `TurnStop` **原样穿出** `astream_events`（此前只从 `wrap_tool_call` 抛过），且闸门一响模型一次都没被调用。
  - **「查不到」不放行**。`PrLookupError` 既不能当成「有」也不能当成「没有」，故拦下本轮且**一个字节都不写**。代价是 Gitee 抖动会让整轮不可用 —— 这是有意的。
  - **只刷新已存在的记忆，不凭空建档**。建档是 `save_repo_memory` + 模板的事；在 `before_agent` 造一份没有「项目概览」的残篇会占住路径，让模型下一轮以为自己已经写过了。注意：**有没有记忆**与**拦不拦**无关 —— 仓库有未合入的 PR 就该拦。
  - **轮末打底的判据是形态，不是「本轮建出过 PR」**（计划里的原判据）。计划里也写明了这条可放宽；实测确认必须放宽：识别 `create_pr` 是否成功要对工具返回文案做字符串匹配（它失败时返回 `创建 PR 失败：…` 而不抛异常），**改一次文案就会静默失效**。改成 `profile_for(intent).form == ORCHESTRATE_FORM`（coding 即「这轮可能在动代码」）与用户那句「只要是动代码了就更新」一致。
  - **打底失败不改本轮结论**。记忆是副产物：查不到 PR、写盘报错都只记日志，交给下一轮或下一次 `save_repo_memory` 再刷。
  - **打底不复用闸门的异常处置**，只复用 `refresh_memory` 本身 —— 两个调用方对 `PrLookupError` 的处置正好相反（闸门拦下，打底放行），把处置写进 `refresh_memory` 必然错一处。
  - **对账器不建「功能表从方案步骤播种」**（计划里的一条）。方案步骤的标题是**任务形状**（「补测试」「改配置」）而非功能形状，且对账器没有 `functions` 参数、功能名本来就归模型。凭空补出一批「补测试」当功能名，比空着更糟。
  - **需求表的列是 `需求 | PR | 分支 | 状态 | 更新于`**：计划里的 `#` 行号列去掉了 —— 它是渲染产物、不是数据，重排一次就全变，还会诱导模型去引用行号。
  - **没有 PR 编号的待合入行不触发闸门**（`pending` 只看 `number and state == PENDING`）。那种行无从合并，拦下来会把这一轮**永久锁死**（闸门在 `before_agent` 结束本轮，模型根本没机会改那一行）。
  - **刷新成功也留一条日志**，且记在 `_write` 里而不是调用方。`refresh_memory` 在**没有记忆**时也会算出 `changed=True`（那是「算出来变了」，不是「写进去了」），拿 `changed` 当「刷过没刷」的判据会撒谎。
- **验证**：`tests/test_memory_reconcile.py`（对账器纯函数，最厚）；`tests/test_memory_gate.py`（**新增 19 条**：拦得住 / 拦之前先刷记忆 / 没记忆也拦但不建档 / 写盘失败不改判定 / `PrLookupError` 抛且零写入 / 只在两个形态装配 / **真图确认 `before_agent` 的 `TurnStop` 穿得出来且模型零调用** / 干净轮次照跑 / 轮末打底三条（刷新、查不到零写入、后端抛异常不外溢）/ 判据是形态（coding 触发，qa·analysis·review 不触发））；`tests/test_memory_tool.py` 重写（含「模型伪造的『已合入』被改回『待合入』」「查不到就拒写」）；`tests/test_prompts.py` 的 `TestMemoryWritebackGate` 跟着改写。全量 `pytest` **753 条全过**（基线 733）。`ruff check` 本次改动文件干净（`src/ai_coding_harness/db/`、`observability/tracing.py`、`sandbox/provider.py`、`tools/plan.py` 里那 9 条是既有的，不属本次改动）。
- **遗留**：
  - **改动尚未在活的后端生效**，要重启才算数（重启由你定）。
  - **待实调确认一处**：Gitee 的 `state="all"` 是否真返回已合并项。若不返回，`all_pulls()` 要退回 `open` + `closed` 两次查询（`_pulls_or_raise` 已支持按 state 取，改的是那一个入口）。重启后跑一轮就能看出来。
  - **已知的一处口径差**：需求表里**已有**的行，对账时只更新 `状态`（与 `更新于`），**分支 / 链接保持表内原值**；只有新补进来的行才从 PR 取 `head.ref` 与 `html_url`。于是模型把已有行的分支写错时不会被纠正 —— 与模板里「四列由平台回填」的说法有出入。要收紧就是在 `_reconcile_need` 里比对 `url` / `branch`，**待你定**。
  - 历史记忆不刷：本轮之前写下的记忆里没有这两张表，下一次进入 plan / coding 形态时由对账器补上（补在「项目概览」之后）。

#### 工具提示按角色说话：委派时显示「正在编码 / 正在评审 / 正在规划」

- **动机**：输入框上方那行活动提示（「正在执行命令」「正在读取文件」…）在委派子智能体时一律显示「正在处理子任务」—— 派的是谁、在做什么，一个字都没说。上一批已经让**任务列表**里的容器项说中文角色名（「编码子任务」），但**提示那行是另一条通道**，没跟着改。上一条遗留里写明的就是这个缺口。
- **改动**：
  - `src/ai_coding_harness/stream.py`：把 `ToolNames._call_id` 提成模块级函数 `call_id_of(event)`（`ToolNames` 两个方法改用它）—— 服务层也要拿这个键，不该去蹭一个类的私有 staticmethod。
  - `src/ai_coding_harness/server/service.py`：新增循环内的 `delegated: dict[str, str]`（`task` 的 `tool_call_id` → 角色）；`tool-started` 时若工具是 `TASK_TOOL`，用现成的 `subagent_name()` 从入参取 `subagent_type` 记下，并把 `"agent": "coder"` 加进工具帧；收尾帧 `pop` 出来同样带上。**其它工具的帧形状不变**（不加空字段）。
  - `frontend/src/api.ts`：`ChatEvent` 加可选 `agent?: string`（注明只有委派帧带）。
  - `frontend/src/App.vue`：新增 `SUBAGENT_ACTIVITY = {planner: '正在规划', coder: '正在编码', reviewer: '正在评审'}`；工具分支改为 `SUBAGENT_ACTIVITY[ev.agent ?? ''] ?? TOOL_ACTIVITY[ev.name ?? ''] ?? '正在处理'`；`TOOL_ACTIVITY` 上那句「不提是谁在做」的注释改掉（`task` 那一格从此就提了），并注明它的兜底作用。
- **关键决策**：
  - **角色由后端带上，前端不猜**。`TOOL_ACTIVITY` 是按**工具名**查表的，`task` 只有一格；前端手里没有「这次派的是谁」的任何信息。原先估「改一行文案就够」是估错了 —— 必须先在 SSE 帧上补这个字段（`tool` 帧原本只发 `name` / `content`）。
  - **收尾帧也带角色**。只带起手帧的话，子智能体一返回提示就翻回「正在处理子任务」，而主智能体接着要想几秒才开口 —— 那几秒显示的是「还在处理」，活其实干完了。带上则停在「正在编码」直到主智能体自己说话。两档都不完美（没有「已完成」这一档），取**稳定不闪**的。
  - **字段名用 `agent`**，沿用仓库既有口径（run_log 的 `record.agent`、metadata 的 `lc_agent_name`），不新造词。
  - **取不到角色就不带这个键**，而不是推一个空串过去 —— 前端那层自然会落回 `TOOL_ACTIVITY['task']`（「正在处理子任务」）。那张表里的三行与后端 `_ROLE_LABELS` 是**平行的两份**，不复用：两边文本本来就不同（「编码子任务」是清单里的**物件名**，「正在编码」是**进行时的动作**），而且前端不依赖后端的中文。
- **验证**：`tests/test_stream_v3.py` 新增 `test_call_id_of_pairs_the_two_frames`；`tests/test_plan_events.py` 新增三条 —— 委派起止两帧都带角色、`task` 入参取不到角色时**没有** `agent` 键（三个畸形入参参数化）、普通工具帧没有 `agent` 键。全量 `pytest` **707 条全过**（基线 701）；`ruff check` 改动文件干净（`tests/test_plan_events.py` 仍是那 6 条既有的 `RUF100`，不属本次改动）；前端 `vue-tsc -b && vite build` 通过。前端没有测试框架，这块没补。
- **遗留**：**后端的改动尚未在活的后端生效**，要重启才算数。前端不用构建 —— 跑的是 Vite dev server（`frontend/vite.config.ts` 代理到 `127.0.0.1:8000`），`App.vue` 改完即热更新；后端重启前它拿不到 `agent` 字段，落回「正在处理子任务」，不会出错。历史记录不受影响：回放吃的是 `parts`（只有正文与待办树），`tool` 帧本来就不落库，无需回填。

#### 任务列表里的子任务容器改用中文角色名

- **动机**：挂不上主委派项的子清单会补一个容器项（`stream.py` 的 `_container`），那行文字此前把**内部角色名**摊在了用户面前 —— `「coder」子任务`，取不到角色名时则是一句「子智能体任务」。给用户看的一行应该说清**在做什么**（编码 / 评审 / 规划），而不是复述我们内部的英文角色名。
- **改动**（`src/ai_coding_harness/stream.py`）：新增 `_ROLE_LABELS = {"planner": "规划", "coder": "编码", "reviewer": "评审"}`；`_container` 取名分三档 —— 认识的三个角色 → `编码子任务`；表里没有的角色 → 照旧 `「changelog-writer」子任务`；连名字都取不到（`task` 入参畸形）→ 仍是 `子智能体任务`。
- **关键决策**：
  - **改在后端，前端不动**。那行文字由 `_container` 生成、随 SSE 下发、也落进 `parts`（历史回放吃的是同一份），改这一处同时管住两头；前端是 `{{ t.content }}` 原样渲染，本来也没有可插手的余地。
  - **标签表放在 `stream.py`，不 import `agent/subagents.py` 的 `_ROLES`**。角色名的权威清单在 `agent` 层，而 `stream` 是协议层，不反向依赖它；代价是三个名字重了一份，注释里点明了权威清单在哪。
  - **认识的翻中文，不认识的保留原名**。宁可显示 `「changelog-writer」子任务`，也别塌成辨识不出的兜底话 —— 兜底话只留给「连名字都没有」这一种情况（将来加角色时不至于看不出是什么）。
  - **已经落库的历史记录不刷**。历史里那行仍是旧文字；为改显示去写业务库不划算，且那是当时真实显示过的东西。
- **验证**：`tests/test_stream_v3.py` 新增 `test_container_name_is_the_chinese_role_label`（三个角色各自的标签 + 未知角色保留原名）；四处既有断言跟着改（`test_stream_v3.py` 三处、`test_plan_events.py` 一处），「没名字」那两条仍断言 `子智能体任务`。全量 `pytest` **701 条全过**；`ruff check` 改动文件干净。
- **遗留**：**尚未在活的后端生效**，要重启才算数（前端无需重启）。输入框上方那个「正在处理子任务」的工具提示（`frontend/src/App.vue` 的 `task`）不在任务列表里，本次未动。

#### 摘要调用不再混进助手正文

- **动机**：2026-09-21 那轮真实会话（轮次 `0030de3a3183`）里，助手答复**开头 5293 字是摘要中间体** —— `## SESSION INTENT` / `## NEXT STEPS` 那一整段，真实答案从 `## 方案要点（8 步，user-service）` 才开始。根因是一条**上游缺声明**：`langchain` 的 `SummarizationMiddleware` 在类上写了 `transformers = (InternalCallTransformer,)`（`langchain/agents/middleware/summarization.py:245`），靠它把中间件自己的模型调用事件从流里丢掉；`deepagents` 的 `_DeepAgentsSummarizationMiddleware` 只把活转包给内层那个实例（`deepagents/middleware/summarization.py` 的 `_lc_helper`），**自己那条 `transformers` 没有** —— 于是 `create_agent` 收集 transformer 时（`langchain/agents/factory.py:1849-1856`）漏掉它，没人丢：摘要正文顺着根层的 `text-delta` 流下来，被我们的消费端当成了助手在说话。
- **时间线佐证**：22:45:33 那次调用的用量（输入 106752 / 输出 3506 tokens）与 `/conversation_history/...md` 的 404 同秒；22:48:19 下一次调用输入掉到 83034，即历史已被替换。落库记录 id=267 共 6414 字 = 前述摘要 5293 字 + 真答复约 1100 字。
- **改动**：
  - `src/ai_coding_harness/stream.py`：新增 `INTERNAL_CALL_KEY = "lc_internal_call"` 与判定函数 `is_internal_call(event)` —— 只认 metadata 里那个**键在不在**（键值是一次性随机 token，只有 langchain 内部拿得到，我们不必比对值；`tools` 通道的 `data` 是单值、没有 metadata，判定天然为假）；`V3Translator.feed()` 开头把这类事件**整条丢掉**。丢在这里而不是 `service` 里，是因为「哪些事件算正文」是事件形状的知识，归这个模块；也顺带免了它那条 `message-start` 把主智能体的待办块登记清掉。
  - `src/ai_coding_harness/observability/run_log.py`：`message-finish` 分两支 —— 内部调用记「摘要调用完成：…」，其余照旧「LLM 调用完成：…」。**日志这边故意不筛**：一次十万输入的调用正是要能查账的，只是得看得出是谁花的。
- **关键决策**：
  - **在消费端兜，不动上游**。也可以给 `_DeepAgentsSummarizationMiddleware` 补一条 `transformers`，但那要改第三方库代码（或 monkey-patch）；判定键是 langchain 公开的约定，自己兜更稳，也更贴近「我们只认哪些事件算正文」这条职责。
  - **不清理已经落库的那条记录（id=267）**。它是当时真实发生过的事，改了反而看不出根因；新的一轮起不再出现即可。
  - **摘要调用的 token 照旧计入 `llm_calls`**，只是在日志里另署一个名。
- **验证**：`tests/v3_events.py` 的造事件器加 `internal` 开关（造「中间件自己发起」的那类事件）；`tests/test_stream_v3.py` 新增 `TestInternalModelCallsAreNotTheAnswer` 5 条（内部调用三帧都不产出、夹在两段正文之间不会把正文切成两块、普通调用照常流、工具事件判定恒假、判定只看那个键）；`tests/test_run_logging.py` 新增 `TestInternalCallLines` 3 条（摘要署名 / 普通署名 / 归属仍按 `lc_agent_name`）。全量 `pytest` **700 条全过**（基线 692）；ruff 改动文件干净（另顺手删掉 `tests/v3_events.py` 里一条对未启用规则 `A002` 的无效 `noqa`）。
- **遗留**：**改动尚未在活的后端生效**，要重启才算数。本轮监控另看到两处，**未纳入本次改动**：主清单最后两项（3「制定方案并 `storePlan` 落库」、4「向用户呈现方案要点」）实际都做完了却留在 `in_progress` / `pending`；以及子智能体清单挂到了兜底容器项、而非某个主委派项。

#### 清单口径回改：coder 一项对应方案的一步；qa 不再挂清单

- **动机**：上一版（见下下条「实施完成后面板自动收起；coder 的清单不再复述方案步骤」）要求 coder 执行方案时「不要照抄方案的步骤」，理由是方案进度已由 `updatePlanStep` 回写方案面板，两处各刷一遍。**实测那样会塌成一条「执行计划中」的占位** —— 用户在清单上看不出走到哪儿了。另一头同时补上：qa 形态挂 `write_todos` 是纯空转（「查一下、答一下」两步，清单只会多出一份）。
- **改动**：
  - `src/ai_coding_harness/agent/prompts.py`：基础提示词**拆成五段再拼**（`_BASE_HEAD` + `TODO_PROMPT` + `_BASE_ENV` + `GIT_PROMPT` + `_BASE_TAIL`），清单规则与 git 小节各自独立成段，让某个形态能**整段摘掉**它不需要的小节，而不是在通用规则里写反向禁令；写侧拼出的 `BASE_PROMPT` 与拆分前**逐字一致**。新增 `READONLY_TODO_BASE_PROMPT`（analysis 用：只读、留清单）；`READONLY_BASE_PROMPT`（qa 用）照旧两段都不含。`_BASE_HEAD` 里摘掉清单两条后重新编号为 1-5。
  - 同上文件新增 `TODO_PROMPT`，**两条并存、各管一头**：第 2 条末尾保留上一版那句「**执行已有方案时，清单不要照抄方案的步骤** —— 方案进度由 `updatePlanStep` 逐条回写、显示在方案面板上，再抄一遍等于同一件事说两处；这时清单只写你自己的动作，说明「正在执行方案的哪一步」与收尾即可」（管**措辞**）；新增第 3 条「**执行已有方案时，一项对应方案的一步**：把方案的每一步各列一条，条目写这一步在动什么（如「执行方案步骤 2：改密钥存储」）—— 不要只留一条「执行计划中」这样的占位，那样用户在清单上看不出走到哪儿了」（管**粒度**）。
  - 同上文件 `_CODER_ROLE` 工作流程**插入第 3 步**「**落清单**：取到方案后立刻调 `write_todos` 写出本轮清单」（位置是用户指定的：第 2 步取计划之后、动手之前），原 3-7 顺延为 4-8；「按步骤实施」下保留原来的子条目「**你的清单只管自己的动作，不复述方案**：…清单不是它的副本」并补一条「**清单同步**：同一步把清单里对应的那一条也改成 `running` / `completed`，收尾时再整份调一次 `write_todos`」。
  - `safety/policy.py`：qa 行的 `hidden_tools` 由空改为 `frozenset({"write_todos"})` —— 提示词摘段是「劝它别选」，这条是「选了也不执行」，与 planner 撤掉写文件工具同一手法。
- **关键决策**：
  - **粒度与措辞两条都要，谁单独放着都走偏**。只有措辞那条 →塌成「执行计划中」一行；只有粒度那条 →整段照抄方案。两条条件相同（都是「执行已有方案时」）但约束不同的东西，故没合成一条。
  - **撤工具，不只删提示词**。上游 `TodoListMiddleware` 给每个智能体（主控与全部子智能体）都装了 `write_todos`，而 `HarnessProfile.excluded_tools` 的键是**模型**（主控与 planner 共用一个模型），表达不了「按形态」。仓库既有的按角色撤工具杠杆就是 `BoundaryMiddleware`（既隐藏又拦截），qa 走它。
  - **残留（自觉取舍）**：上游那段 TodoList 的系统提示词片段写死在中间件的 `wrap_model_call` 里、摘不掉；工具藏起来后它调不到，代价与 planner 的 `write_file` / `edit_file` 相同，接受。
  - **analysis 保留清单**：一轮分析常是四五步取证（实测清单 4-6 条且有用），摘的是问答，不是全部只读形态。
- **验证**：`tests/test_boundary.py` 的 qa 行补 `("write_todos",)`；`tests/test_prompts.py` 把 `TestTodosDoNotDuplicateThePlan` 换成 `TestTheListCarriesThePlanSteps`（粒度在、措辞也在、落清单的位置钉在第 3 步）与 `TestQaHasNoTodoList`（qa 提示词里不出现 `write_todos` 与「任务清单规则」；analysis 与其余六个形态都还在）；全量 `pytest` **692 条全过**；ruff 改动文件干净（全仓仍是那 16 条既有告警）。
- **遗留**：
  - **「不复述方案」那半句 2026-09-21 期间被误删过一次**（手滑），本次一并补回 —— 补回前后它的措辞未变，只是位置从「通用规则第 5 条末尾」挪进 `TODO_PROMPT` 第 2 条末尾。
  - 清单规则那段从「通用规则」挪进 `TODO_PROMPT` 后内容一字未动（含收尾那半句与「条目就用他话里的对象」）。
  - 提示词与边界两半**尚未在活的后端生效**，要重启才算数。

#### 子任务折叠：开关认「哪一项」，不再认文本

- **动机**：展开一个子清单时，界面上同时展开好几个 —— 不是点错了，是折叠态**按条目文本**记的（`todoOpen[t.content]`）。文本相同的项共用一个开关，而文本相同这件事在真实数据里很常见。
- **现场证据**（那条真实会话 68 条记录）：一轮里会落下**多个同名的兜底容器** —— 子智能体名取不到时容器都叫「子智能体任务」（`stream.py` 的 `_container`），该会话里这个名字出现 **3 次**；跨消息重名的顶层条目另有 **5 种**（如「核实仓库现状…」出现 2 次）。修前实测：点其中一个「子智能体任务」的开关，**三个**同时展开（子项 6 / 7 / 4 全部渲染出来）。
- **改动**（全在 `frontend/src/App.vue`）：
  - 折叠态的 key 换成 `${消息下标}:${文本}#${同文本第几次出现}`（新增 `todoKey`）；`isTodoOpen` / `toggleTodo` 改为收「消息 + 下标」，模板三处调用跟着传参。
  - `resetPlan()` 补一行清空 `todoOpen`（跨会话留着会让新会话的同位项莫名是展开的），与既有的 `expandedSteps` 做法对齐。
- **关键决策**：
  - **不给条目加稳定 id，也不动后端的容器命名**。主清单项在多次 `write_todos` 之间会重排，后端也给不出稳定 id；容器叫什么与「点一个只开一个」无关。键在前端算得出来，够用且改动最小。
  - **默认展开规则（父项或任一子项在跑就展开）不变** —— 子智能体干活时正要看那份进度。
- **验证**：修后在同一界面上复验 —— 点消息 59 的容器只开它自己（子项 6），另外两个保持原状；点消息 69 里那个已展开的同名容器只关它自己。浏览器控制台无报错；`npm --prefix frontend run build`（含 `vue-tsc` 类型检查）通过。
- **遗留**：`.claude/launch.json` 新增两条 —— `frontend-attach`（指向 5173，本机未启用 URL 附加，留着备用）与 `frontend-preview`（在 5174 起第二个 vite 实例，实测用的就是它；你的 5173 没被碰过）。

#### 实施完成后面板自动收起；coder 的清单不再复述方案步骤

- **动机**：两件收尾的事。一是方案落终态之后，右侧那块面板一直占着宽度，把正文挤窄，而这时你多半已经不需要它了；二是 coder 执行方案时把方案步骤原样搬进 `write_todos`，方案面板上的进度（`updatePlanStep` 逐条回写）与清单说的是同一件事，两处各刷一遍。
- **改动**：
  - `frontend/src/App.vue`：`runStream` 的收尾块里，当前会话本轮结束且 `plan.status` 为 `done` / `failed` 时置 `planCollapsed = true`（点侧栏 rail 随时展开回来）。
  - `src/ai_coding_harness/agent/prompts.py` 两处：通用规则第 5 条补一句带条件的口径 —— 「**执行已有方案时，清单不要照抄方案的步骤** —— 方案进度由 `updatePlanStep` 逐条回写、显示在方案面板上，再抄一遍等于同一件事说两处；这时清单只写你自己的动作，说明「正在执行方案的哪一步」与收尾即可」；`_CODER_ROLE` 第 4 条后补一条同口径的子条目（离 coder 更近；通用规则里的话单放着被无视过，故刻意重复）。
- **关键决策**：
  - **只在方案落终态时收，不是「一轮结束就收」**。`pending`（刚出方案，正要读）与 `executing`（半途）都不收 —— 收了正好把要看的东西藏起来。
  - **收起的时机放在轮末，不放在 `plan` 帧到达时**。帧到达时回答可能还在写，那一刻收会显得突兀。
  - **清单口径写成条件句，不写成通用禁令**。qa / analysis 那几轮手上根本没有方案，无条件禁止等于没说清单该怎么写。
- **验证**：`tests/test_prompts.py` 新增 `TestTodosDoNotDuplicateThePlan` 4 条（条件句在、两处口径一致、方案面板被点名为进度视图、方案是步骤的出处而非清单的副本）；全量 `pytest` **688 条全过**；ruff 改动文件干净；`npm --prefix frontend run build`（含 `vue-tsc` 类型检查）通过。
- **遗留**：
  - **提示词那半改动尚未在活的后端生效** —— 要重启后端才算数（前端那半 vite 热更即生效）。
  - 没加「只自动收一次」的标记：方案 done 之后手动展开回看，再跑一轮结束时仍会被收起（点一下即回）。若嫌烦再加。
- **已生效**：前端（App.vue）随 `npm run dev` / 热更立即生效；提示词待重启后端。

#### 待办清单改成「先分析、再落清单」，并写明它是给用户看的

- **动机**：`write_todos` 此前被写成**开场动作**（「每个任务都必须先使用 `write_todos` 工具生成清单」），模型据此一上来先摆一份清单 —— 那时需求还没读清，摆出来的是猜的。而这份清单在界面上是**唯一一份进度**，用户看的就是它，条目却常写成「处理任务」「执行构建」这类内部口径，甚至直接拿工具名当条目，看了等于没看。
- **改动**（全在 `src/ai_coding_harness/agent/prompts.py`，三处）：
  - 通用规则第 4 条：改为「先看清这一轮要做什么，再把清单落下来：判断请求类型、读清需求之后才调 `write_todos` —— 一上来就写，写出来的是一份猜的清单」，并点明「这份清单是给用户看的（界面上就这一份进度）」。收尾那半句（以最后一次调用为准 / 做完标 `completed` / 不许把 `in_progress` 原样留着）**一字未动**。
  - 通用规则第 5 条：保留「按意图分类列步骤」，补上「也贴合他的说法 …… 既然是他看的，条目就用他话里的对象（哪个接口、哪个文件、哪件事），不写「处理任务」「执行流程」这类内部口径，也不把工具名当条目」。
  - 主控角色段的「动手前先规划」：改为「先把这一轮要做什么分析清楚（判断请求类型、读清需求），再用 `write_todos` 写出本轮清单，然后动手」；后半句「不允许一次 `write_todos` 都没调就直接派子智能体」原样保留。
- **关键决策**：
  - **只改「什么时候写」，不改「必须写」**。派子智能体之前自己那份清单要先有，这条是防「子清单没有落点、整份显示不出来」的老毛病，动它会把已经修好的显示问题重新打开。
  - **补的是顺序与口径，不是机制**。清单是猜的还是读懂了的，代码判不出来，只能靠提示词写死；两个旧写法都留了反向断言（`每个任务都必须先使用 write_todos 工具生成` / `先自己用 write_todos 写出本轮清单再动手` 都不许再出现）。
- **验证**：`tests/test_prompts.py` 新增 `TestTodosComeAfterTheTaskIsUnderstood` 4 条；全量 `pytest` **684 条全过**；ruff 改动文件干净（全仓仍是那 16 条既有告警）。
- **遗留**：另有一条「只在状态真有变化时才重发清单」**已定：不写** —— 怕被当成省事的口子（该更新的时候也不更新）。
- **已生效**：2026-09-21 重启后端后加载（`logs/backend_20260921_r3.out.log`）。

#### 空转刹车：同一工具同一参数反复调用时，先劝、再停

- **动机**：那一轮 686 秒（见下一条「终止」）真正的病灶不是「停不下来」，而是**它自己在原地打转**：日志里 `execute git status` 与 `git log` 交错着来回转，每一圈同样的输入、同样的输出，模型却当成进展。用户点的「终止」只是事后收尾，没人拦得住它继续转。要的是**判得出原地打转、并且当场收尾**。
- **改动**：
  - 新增 `src/ai_coding_harness/agent/middleware/guard.py`：`TurnState`（每轮一份：`message` / `history` / `repeats` / `max_handoffs` / `rejudge` 回调 / `aborted` 标记，`note_call` 数重复、`note_handoff` 扣交接额度、`abort` 造中止）、`TurnStop(Exception)`（`marker` + `extra`）、常量 `ABORT_REASON = "repeated_tool"` 与 `LOOP_ABORT_MARKER`、`LoopGuardMiddleware`（`awrap_tool_call`）。
  - `src/ai_coding_harness/config.py`：新增 `loop_guard_repeats`（`LOOP_GUARD_REPEATS`，默认 3，**0 = 关掉**）；`.env.example` 补键与说明。
  - `agent/graph.py`、`agent/subagents.py`：主智能体与三个子智能体各挂一份 `LoopGuardMiddleware`。
  - `src/ai_coding_harness/server/service.py`：`chat()` 每轮新建 `TurnState`（带上 `message` / `history` / 阈值 / 重判回调），经 `configurable["turn_state"]` 注入（智能体是长期存活对象，逐轮变化的东西只能走 config）；`_run_agent` 新增 `state` 必填关键字参数与 `except TurnStop` 出口；收尾那步**再看一眼** `state.aborted`。
  - `_record_interrupted` 增第三条分支：`aborted` 路径的 `extra` 带 `aborted` / `tool` / `repeats`，措辞与「取消」「故障」两条都不同（`state.aborted` 有值即中止，异常被某层吃成 ToolMessage 时靠它兜住）。
- **关键决策**：
  - **数的是本轮出现次数，不要求相邻**。只数「连续重复」会把那次事故整个漏掉 —— `git status` 与 `git log` 中间隔着别的调用，按相邻判就永远数不到第二次。
  - **第 N 次只劝、第 N+1 次才停**。第 N 次返回一条 `status="error"` 的 `ToolMessage`（**不执行**，并说清是哪个工具、第几次、为什么不再执行），模型据此换做法；一上来就抛 `TurnStop`，模型连改的机会都没有。
  - **键是 `(工具名, 参数)`，参数用 `json.dumps(sort_keys=True)` 归一**。参数顺序由模型生成、每次都不同，按顺序比较等于每次都算新调用，这层就永远数不到 3。不可序列化的参数退化为 `repr`，不在计数上炸。
  - **阈值与开关挂在 `TurnState` 上，没有轮次上下文就整层直通**。单测、脚本直接跑图时 `configurable` 里没有 `turn_state`，那时必须完全不受影响。
  - **中止既不是取消也不是故障**：另有一套标记（`LOOP_ABORT_MARKER`），`extra` 里既不带 `cancelled` 也不带 `error`。`state.aborted` 上留痕是纵深 —— 异常万一被某层吃成 `ToolMessage`，本轮会以「智能体完成」记进库、用户看到一句没有下文的正文。
- **验证**：新增 `tests/test_loop_guard.py` 22 条（计数与键归一、阈值 0 关闭、无上下文直通、第 N 次拒且工具**真没执行**、第 N+1 次抛且 `state.aborted` 同步、交错重复仍被拦、换个参数放行、警告措辞）；`tests/test_interrupted_record.py` 新增 `TestLoopAbortRecord` 4 条（抛出路径、无产出路径、异常被吞后靠标记收尾、健康轮不受影响）。
- **遗留**：
  - **改动尚未在活的后端生效** —— 要重启后端才算数（本次未重启）。阈值 3 合不合适、真机上打转是不是这个形状，都得重启后拿真实会话看。
  - 守卫只认**参数全等**的重复：换个说法（`git status` → `git status --short`）或换个等价命令就绕过了。它拦的是那次事故那种「一模一样的输入」，不是通用的「没有进展」。

#### 能力交接：`handoffTask` —— 形态做不了这一轮时，交给合适的形态接着做

- **动机**：形态是按意图裁出来的（`agent/profiles.py`），一旦裁得不合适，模型手里就没有那件工具。光在提示词里写「做不到就说做不到」不够 —— 它要么两手一摊，要么硬着头皮承诺一个做不出的事（686 秒那轮就是：plan 形态手上没有写权限，于是把方案复述一遍再问用户，绕不出来）。得给它一条**真能走**的路。
- **改动**：
  - 新增 `src/ai_coding_harness/tools/handoff.py`：`handoffTask(reason, notes="")` 与 `HandoffContext`（会话 / 运行时 / 管理器 / 设置 / 当前形态）。工具自己**不做路由**：调 `detect_intent(..., force_llm=True)` 重新判一次意图，只有重判结果可用时才放行。
  - `src/ai_coding_harness/server/intent.py`：`detect_intent` 新增 `force_llm` —— **跳过口令直通那一层**，直接问分类模型。
  - `src/ai_coding_harness/server/service.py`：`_rejudge_intent`（`force_llm=True` 那条路，超时与模型都在这一层）与 `chat()` 里的接线。
  - `tools/register.py`：`build_main_tools(..., handoff=None)` 末尾按需追加 `handoffTask` —— 五个形态**都**拿得到（`_FORM_TOOLS` 那张表裁的是「这个形态有什么能力」，交接恰恰是能力不够时的出路，所以它不在表里）。`handoff=None` 时不挂，脚本与单测直接建工具不必凑一个会话上下文。
  - `config.py`：新增 `handoff_result_max`（`HANDOFF_RESULT_MAX`，默认 4000）；`.env.example` 补键。
  - `agent/prompts.py`：新增 `_CAPABILITY_EXIT` 段（**五个形态共用**，接在 `_MAIN_CORE` 之后）；`_READONLY_BOUNDARY` 补第三条 —— 把「用户明确要改代码」（该交接）、「用户在问能不能改」（该请他明说「开始实施」）、「需要的是方案」（该请他明说「出方案」）三种情况分开写。
- **关键决策**：
  - **只有模型重判（`SOURCE_LLM`）才放行**。要纠正的常常正是口令层（句子里一个「实施」把意图钉成 coding，真实诉求是「先出方案」），拿它的复读去换形态等于换个姿势再猜一次。分类器不可用、退回关键字口径（`SOURCE_FALLBACK`）时一律拒。
  - **工具上不带「目标形态」参数**。目标由重判结果决定，不由模型的自我诊断决定 —— 让它自己说是该交给 plan 还是 review，就又回到「猜」上了。
  - **内层跑在派生线程上**（`…::handoff-1`）。直接蹭外层线程的话 `checkpoint_ns` 会被重置成 `""`，两边落到同一个根槽上。
  - **跑内层前把 `var_child_runnable_config` 摘干净，跑完还原**。实测：内层 `ainvoke` 的模型事件会带着**空 namespace** 混进外层 v3 流，被 `V3Translator` 当成外层自己的回答下发并落库（正是要避免的「一轮里冒出别人的话」）；`callbacks=[]` **挡不住**，摘掉那个 ContextVar 才挡得住，且内层图照常跑。
  - **额度扣在「尝试」上**，同一轮第二次交接直接拒（被拒之后还再试的多半是原地甩锅）；所有拒绝都返回**普通字符串**，措辞里不出现「失败 / 错误」—— 说成故障模型会当成环境问题去重试。
  - **结果截断在 4000**：内层是完整的一轮运行，正文可能上万字，而它在外层只是一个工具返回值，外层要的是结论。
  - **原计划里两件事拆掉了**：`HandoffMiddleware` 不必要（`LoopGuardMiddleware` 一个类就够，交接的判定与装配都在工具与 `register.py` 里）；`server/stream.py` 把 `handoffTask` 认成委派父节点也不必做 —— 内层事件既然被摘干净了，外层流里根本没有它的 todo / 工具事件可挂。
- **验证**：新增 `tests/test_handoff_tool.py` 22 条（六种拒绝情形与「拒绝不是错误」的措辞、按重判结果取形态、派生线程 id、内层 configurable 带 `intent` / `redact_terms` / 同一个 `TurnState`、自己的递归上限、**内层那一刻的环境 config 是 `{}` 且跑完已还原**、原话与历史与 `notes` 全部转达、空结果与截断、正文提取的六种形状）；`tests/test_tool_registry.py` 新增 3 条（五个形态都拿得到、不给 `handoff` 就不挂、子智能体拿不到）；`tests/test_intent_detection.py` 新增 `TestForceLlmRejudges` 7 条与 `_rejudge_intent` / `_intent_history` 的接线 4 条；`tests/test_prompts.py` 新增四组提示词守卫 14 条。
- **遗留**：
  - **改动尚未在活的后端生效**（本次未重启）。真机上跑一次 `handoffTask` 还没做 —— 「内层不进外层事件流」是拿假内层智能体在单测里钉的，真机的 v3 流没验过。
  - 交接**每轮一次**：额度固定为 1（`TurnState.max_handoffs` 上可传，暂未开配置项）。接手的那一轮看不到外层上下文，全靠 `notes`。

#### 补回主控提示词里丢掉的前置规划约束

- **动机**：`tests/test_prompts.py::TestMainPlansBeforeItDelegates` 已连续三批记着同一条红：`_ROLE_ORCHESTRATE` 那句「动手前先规划」在某次重写「工作流程」时丢了，全仓已无该串。它对应的是一次真实事故（主控一份清单都没写就直接派 `coder`，子清单因此没有落点、整份显示不出来），所以**补回约束**、而不是把断言改掉。
- **改动**：`agent/prompts.py` 的 `_ROLE_ORCHESTRATE`「## 工作流程」加回一句前置规划（步骤与编号不动）。
- **验证**：全量 `pytest` **680 条全过**，这条既有红消失。

#### 终止：输入框那一格在跑动时变成「终止」，点了就停住这一轮

- **动机**：跑飞的一轮**没有刹车**。实测栽过一次（2026-09-21）：某会话一轮 coding 跑了 686 秒还在重复 `execute git status` + 同一份 `write_todos`，用户既停不掉也刷不掉（刷新断的只是订阅，运行照跑、token 照烧），最后只能重启后端 —— 重启是唯一有效的动作，这显然不对。
- **改动**：
  - `src/ai_coding_harness/server/runs.py`：`RunHub.cancel` 改为返回 `bool`（**是否真有一轮在跑**）；签名不变，仍从注册表里 pop 掉那一轮再取消它的 task。
  - `src/ai_coding_harness/api/routes/sessions.py`：新增 `POST /sessions/{id}/cancel`，回 `{"cancelled": bool}`。**不查库、不改会话状态** —— 会话与历史都留着（要拆会话走 `DELETE`）。
  - `src/ai_coding_harness/server/service.py`：`_run_agent` 新增 `except asyncio.CancelledError` 分支 —— 调 `_record_interrupted` 补一条记录后**原样抛出**；`_record_interrupted` 新增 `exc: BaseException | None = None`，`exc is None` 即终止路径：正文尾巴/单独一条写 `CANCELLED_BY_USER = "〔已由用户终止〕"`，`extra` 里 `cancelled: True`（**不带** `error`，措辞里也不出现异常名）。故障路径的措辞与形状一字未动。
  - `frontend/src/api.ts`：`postChat` / `resumeChat` / `consumeStream` 加可选 `signal`；新增 `cancelChat(id)`（读回 `cancelled`）。`consumeStream` 的 catch 先判 `signal?.aborted` —— 用户主动断的不重连，否则白跑一个请求。
  - `frontend/src/App.vue`：`stopping` / `streamAbort` 两个状态；`stopRun()` 先 `await cancelChat(id)` 再 `abort()`；`runStream` 收尾按 `stopping` 决定「补 `〔已由用户终止〕`」与「不发那行 `[错误]`」；`send()` 的 catch 里 `stopping` 单独一条路（响应头还没回来就点了终止：**不**摘掉那两条消息、**不**弹窗）；模板发送位在 `loading` 时换成「终止」（`:disabled="stopping"`）。
  - `frontend/src/style.css`：`.send.stop` 走危险色（`var(--danger, #d64545)`）。
- **关键决策**：
  - **必须有后端路由，前端断流不算数**。运行宿主（`RunHub`）刻意把这一轮从请求生命周期里剥出来了，`EventSourceResponse` 那套约定是「请求断了断的只是订阅」。所以「终止」＝ `cancelChat` **且** 本地 `abort()`：前者真停，后者立刻松手。取消指令没送到也照停本地，但要提示「那一轮可能仍在运行」。
  - **中止要落一条记录**。不然库里就剩一条用户消息空着，刷新后看不出这轮出过事。复用 `_record_interrupted` 而不是新写一条路 —— 它已经管住了脱敏、`parts` 回放与 `summary` 口径。
  - **取消不是故障**：不产 `error` 帧（`CancelledError` 是 `BaseException`，pump 的 `except Exception` 本来就接不住它，`finally` 照跑 → 已挂着的订阅者看到 `done`、流干净结束），措辞里不出现 `CancelledError`，也不分「有产出 / 未产出」两句。
  - **重按幂等**：没在跑就回 `{"cancelled": false}`，不是错误 —— 用户点慢了、这一轮刚好自己跑完，都走这条。
  - **只清自己那份句柄**：`runStream` 收尾与 `send()` 的 catch 都按 `streamAbort === controller` / `signal` 判一下再清 —— 切走会话后旧流的收尾不能去动新会话的 `stopping`，否则新会话里点终止会被当成传输故障、正文后面多一行「错误」。
- **验证**：`ruff check` 改动的 5 个文件干净（`src` + `tests` 口径另有 16 条**既有**告警，全在本次未改动的文件里）。全量 `pytest` **600 条：599 passed / 1 failed** —— 那 1 条仍是既有的 `tests/test_prompts.py::TestMainPlansBeforeItDelegates`（`动手前先规划` 全仓已无该串），与本次改动无关。`npm run build`（含 `vue-tsc`）通过。
  - 新增 7 条用例。`tests/test_run_hub.py`：挂着订阅者时取消 → 流干净收尾且**无 error 帧**；`cancel` 的布尔值（从没跑过 / 真在跑 / 已跑完 / 重按）；`POST /sessions/{id}/cancel` 的 `{"cancelled": …}` 形状（前端读的就是这个键）。
  - `tests/test_interrupted_record.py`：终止且有正文 → `content == "前半段正文\n\n〔已由用户终止〕"`、`extra.cancelled is True`、**无** `error` 键、无 `CancelledError`/「错误：」字样；一帧都没有就终止 → 只留标记（不写「未产出内容」）；有待办 → 标记补进 `parts` 尾部；`CancelledError` 必须**原样抛出**（吞掉的话 `RunHub` 就分不出用户终止，前端按钮不复位）。另给故障路径加了一条守卫：`extra` 里不得出现 `cancelled`。
- **遗留**：
  - **改动尚未在活的后端生效** —— 要重启后端并重新构建前端才算数（本次未重启）。浏览器上的实际点击验证因此也还没做。
  - 取消只作用于本地这一轮的 task：沙箱里那一次 `execute` 是否随之终止**未做验证**（后面真撞上再查）。「停编排」与「停容器里的进程」不是一件事。
  - 运行宿主是**进程内**一份：多 worker 或重启后 `cancel` 找不到那一轮，会回 `false`（与既有缓冲的边界相同）。
  - 取消与 `_record_interrupted` 那次写库之间有极窄的窗口：期间**再**被取消一次，那条标记会丢（丢了也只是个标记）。

#### 熔断：主导连续失败就开闸，冷却期内不再撞它

- **动机**：降级兜底解决了「某家挂了这一轮还能答」，但没解决**每次都要先撞一次死掉的主导**。智能体一轮对话要跑多次模型节点（`model → tools → model …`），于是死着的主导会被反复撞：每一轮都白等一次 SDK 重试耗尽，这段等待全额计进用户感知延迟；主导**半路断流**那种情形还会把半截句子重复吐进流里。持续故障时该做的是**暂时不打它**。
- **改动**：
  - `src/ai_coding_harness/agent/middleware/fallback.py`：模块级 `_CIRCUITS`（键是**主导模型名**）与 `_Circuit(failures / opened_at / probing)`；`_may_try`（这会儿能不能撞主导）、`_note_primary`（记这一次的结局）、`_log_skip`（开闸期的留痕）；新留痕关键字 `CIRCUIT_LOG_TAG = "MODEL_CIRCUIT"`；占位异常 `_PrimarySkipped`；模块级时钟 `_now`（单测替换它，不用真等冷却）；`reset_circuits()` 给单测清状态。构造参数新增 `circuit_threshold`（默认 0＝关）与 `circuit_cooldown`。
  - `src/ai_coding_harness/config.py` + `.env.example`：`FALLBACK_CIRCUIT_THRESHOLD`（默认 3，`0` 关掉）与 `FALLBACK_CIRCUIT_COOLDOWN_SECONDS`（默认 60）。
  - `src/ai_coding_harness/session/manager.py`：`_fallback_middleware` 构造时把两个值传进去。
  - `tests/test_model_fallback.py`：新增 8 条，带可控时钟 `_Clock`。
- **关键决策**：
  - **不加依赖，自己写**（40 行）。先查过三家（`aiobreaker` / `pybreaker` / `circuitbreaker`）：`pybreaker` 纯同步；`circuitbreaker 2.1.3` 的 `call_async` 是 `with self:`，而 `__enter__` 返回 `None`、**不判开闸状态**，`__exit__` 无事还 `self.reset()` —— 实测 `failure_threshold=2` 跑三次一次都没熔断，只有装饰器那条路才拦；`aiobreaker 1.2.0` 在 3.14 上确实能用（阈值后不再调用下游、`exclude=[GraphBubbleUp]` 生效、半开探针成功即闭合），但它的元数据只声明到 3.6，且**熔断那一次会把原异常换成 `CircuitBreakerError`**（`raise … from`）—— 我们就少了 `MODEL_FALLBACK error=` 里那个真实原因。
  - **熔断那一刻仍抛原始异常**：不换成「已开路」异常，上游 `super()` 照常走它自己的兜底循环 —— 我们只在最前面加一道闸，判据仍旧归上游。这正是自己写相对 aiobreaker 的一处实际好处。
  - **按主导模型名共享、进程内一份**：同一家挂了，用这家的所有角色一起受益（`reviewer` 撞死 kimi 开到闸，别的 kimi 角色也免撞）。代价是多个 worker 各记一份，要跨进程得进 Redis —— 与 memory 里「无状态 worker 方向」一致，现在不做。
  - **只记主导的账**：兜底那次失败不算到主导头上，否则一家坏会连带把另一家的闸也拉下来。**`GraphBubbleUp`（暂停/恢复/嵌套图跳转）不算失败**：它跟供应商健康无关，算进去会被无关的中断顶开闸；但它也不留痕、且要**放掉探针标志**，否则那一轮之后不再有探针，熔断窗就永不开闸。
  - **冷却到点放一次探针**（`probing` 标志保证同时只有一个）：探针成功即 `state=closed` 并归零，失败即 `state=reopen` **重新计时**（不是把冷却加长）。
  - **开闸期的轮次不再打 `MODEL_FALLBACK`**：那条痕的 `error=` 要填真实原因，而这里压根没发生过模型调用。改打 `MODEL_CIRCUIT role=… primary=… state=open failures=3 age=12s fallback=…` —— 一轮的路由仍然查得到，一个关键字即可 `grep MODEL_CIRCUIT logs/agent.log`。
  - **阈值默认 3**：给「一次偶发 429」留余量，同一家连续三次失败才认定它在坏。
- **验证**：`ruff check` 改动的 4 个文件干净（`src` + `tests` 口径另有 16 条**既有**告警、`alembic/` 里 15 条，全在本次未改动的文件里）。`tests/test_model_fallback.py` 由 16 条增至 24 条。全量 `pytest` **593 条：592 passed / 1 failed** —— 那 1 条仍是既有的 `tests/test_prompts.py::TestMainPlansBeforeItDelegates`（`动手前先规划` 全仓已无该串），与本次改动无关。
  - 8 条新用例（注入假时钟，不真等 60 秒）：阈值 2 时前两轮各撞一次主导、**第 3 轮起一下都不撞**（`tried` 序列钉住）；中间成功一次即归零（否则零散故障会攒出一场熔断）；阈值 0 ＝ 每轮都撞且零 `MODEL_CIRCUIT`；冷却到点探针成功 → `state=closed`；探针失败 → `state=reopen` 且重新计时；兜底失败不计入主导的账；`GraphBubbleUp` 不计数不成瘾；探针被控制流异常打断后仍探得到。
  - 单测抓到一处真 bug：开闸那行的 `fallback=` 起初打成了**主导名**（那一刻 `handler` 还没拿到兜底请求），改成构造时存下兜底模型名。
- **遗留**：
  - 改动已生效（2026-09-21 重启后端后加载）。
  - **活体验证试过一次，没能撞开熔断（2026-09-21）**。做法：把 qa / analysis 的主导（gpt）临时指到一个打不通的地址（只动 `.env` 的 `GPT_BASE_URL` 一行，验完已还原、备份已删、后端已用还原后的配置重启），往已有会话连发 4 轮 qa 问题。实测：**轮 1 正常走兜底**（22.5s，首字 22.3s，`MODEL_FALLBACK role=qa primary=gpt-5.6-luna fallback=deepseek-flash error=APIConnectionError`，正文 76 字）；**轮 2~4 各 27~28s、一个字都没吐**（SSE 只有 2 帧、无 `ai` 分片），日志里**既无 `MODEL_FALLBACK` 也无 `MODEL_CIRCUIT`**，只有 3 条 `ai_coding_harness.server.service: 处理失败`。全程只记到 **1 次**主导失败，阈值 3 未到 —— 所以**熔断仍未活体验证**。
  - 为什么只记到 1 次：失败点不在主模型那条链上。轮 2~4 死在 `deepagents/middleware/summarization.py:1209` 的摘要中间件（`_summary_model.ainvoke` → `httpx.ConnectError`，重试约 20s 后冒出 → 「处理失败」），**根本没走到主模型节点**，熔断自然无账可记。根因单列一条，见下「摘要模型的调用绕过兜底」。**离撞开只差两轮能活到模型节点的对话**。

#### 降级兜底：某家模型彻底失败时换一家再试，并在日志里留一条可查的痕

- **动机**：模型的可用性不是我们能保证的（429 限流、5xx、连接断）。SDK 自带重试是**同一条链路**重试，供应商整家挂掉时重试多少次都一样，结果就是这一轮对用户直接失败。需要的是**换一家**再试一次，而且要能事后查出来「这一轮到底跑在哪家模型上」—— 否则用户说「今天答得怪」，我们无从判断是不是降级了。
- **改动**：
  - 新增 `src/ai_coding_harness/agent/middleware/fallback.py`：`FallbackMiddleware`（子类化上游 `langchain.agents.middleware.ModelFallbackMiddleware`）。留痕关键字 `FALLBACK_LOG_TAG = "MODEL_FALLBACK"`，`grep MODEL_FALLBACK logs/agent.log` 即查。
  - `src/ai_coding_harness/agent/llm.py`：把建模型那段抽成 `_config_for` / `_model_from`（原来全在 `build_model` 里，兜底要复用同一套构造细节：温度逐家、窗口 profile）；新增 `build_fallback(settings, *, role)` 与 `_FALLBACK_PROVIDERS`。
  - `src/ai_coding_harness/config.py` + `.env.example`：新增 `fallback_provider`（默认 `deepseek`，留空＝关掉）。
  - 装配：`agent/graph.py` 的 `build_agent` 新增 `fallback` 关键字参数（排在我们这批中间件的**最内侧**）；`agent/subagents.py` 的 `build_subagents` 新增 `fallbacks` 映射（缺键＝该角色没有兜底）；`session/manager.py` 新增必填 `fallback_for` 工厂与 `_fallback_middleware(role, agent)`；`api/app.py` 接线 `build_fallback`。
  - `tests/test_model_fallback.py`：新增 16 条。既有三个测试文件（`test_profiles` / `test_session_reuse` / `test_workspace_runtime`）的 `SessionManager(...)` 调用点补上新的必填参数。
- **关键决策**：
  - **不用 `with_fallbacks` 包装，改子类化上游中间件**：`deepagents._models.resolve_model` 要求 `isinstance(model, BaseChatModel)`，包一层的 `RunnableWithFallbacks` 在 `bind_tools` 那步就炸（实测 `AttributeError: 'ChatOpenAI' object has no attribute 'count'`）。子类化则是把留痕**加在已有机制旁边**：上游的 `awrap_model_call` 本来就是 `try 主导 → 逐个兜底 → 全败抛最后一个异常`，重试循环一个字不动，我们只覆写它以插一条痕。
  - **留痕靠包一层计数闭包，不重写重试循环**：把 `handler` 包成 `counted`，第 2 次被调用即「主导已失败、兜底正接手」，此刻记一条 WARNING；`cause` 由闭包捕获上一次的异常。这样判据归上游、痕迹归我们，上游以后改重试策略我们不用跟着改。带 `extra={"agent": …}` 是为了让这条落在 `%(agent)s` 列里，与其他运行日志同口径。
  - **只记异常类型与 HTTP 状态码，不记异常原文**：供应商的报错体常把 api key 原样回显（`invalid api key: sk-…`），而这是条不落盘的红线。`RateLimitError` / `InternalServerError` / `APITimeoutError` / `APIConnectionError` 四者已经指认了原因，够用。这条也钉在单测里（合成密钥必须不出现在摘要里）。
  - **挂最内侧**：降级重试跑的是它内层的 handler，不该把我们的边界检查与脱敏前处理再跑一遍；反过来，外层的 `ModelRedactionMiddleware` 因此永远看到「最终赢的那个回答」，一次脱敏覆盖所有模型来源。
  - **全局一条保底，不链式**：`FALLBACK_PROVIDER` 只有一个值。链式降级要处理「哪几家、什么顺序、失败怎么记」，收益是极少数「两家同时挂」的场景，先不做。
  - **`coder` 没有兜底**：它的主导模型就是 deepseek，兜底也是 deepseek ＝ 拿同一个模型再试一次，等于没兜。**代价是 deepseek 整家挂掉时编码这一环仍然失败**（主控与 planner 会降级到 deepseek，所以那时编码反而成了唯一的硬点）。这是全局一条保底规则的直接后果，不是漏挂。
  - **配坏了 fail-open，名字打错 fail-loud**：目标供应商名不在白名单（`_FALLBACK_PROVIDERS`）里直接 `ValueError` —— `.env` 写错字属于构建期的错，静默的后果是「以为有兜底，其实一直没挂」，比当场失败难查。而「白名单内但没配齐」（缺模型名或 key/base_url）只记一条 WARNING 就返回 `None`：兜底本身是降级功能，不该因为它把整场会话的构建拖死。
  - **只实现异步版**：服务端统一走 `astream_events`（与 `boundary.py` 同一约定），`wrap_model_call` 不覆写。
- **验证**：`ruff check` 改动的 9 个文件干净（全仓另有 16 条**既有**告警，全在本次未改动的文件里）。全量 `pytest` **585 条：584 passed / 1 failed** —— 那 1 条是既有的 `tests/test_prompts.py::TestMainPlansBeforeItDelegates`（断言 `动手前先规划` 出现在主控提示词里，而全仓已无该串），与本次改动无关。
  - 行为：主导抛 → 返回兜底的回答且 `tried == [主导, 兜底]`；主导成功 → 零日志、兜底模型一次都没被碰；两个都抛 → 上抛的是**兜底那次**的异常（上游 `raise last_exception` 的语义，容易误读成主导的）。
  - 留痕：一条 WARNING 且 `role=` / `primary=` / `fallback=` / `error=RateLimitError/429` 都在，`record.agent == "main"`（主控）与角色名（子智能体）各一例。
  - 挑模型：`reviewer`（主导 kimi）→ deepseek 的模型名、温度取 **deepseek 自己的 0.3**（不是 kimi 的 1，kimi 收 0.3 直接 400）、窗口 profile 取 deepseek 的；`coder` / 留空 / `plan` 对 `glm` → 全是 `None`；白名单外的名字与未登记的角色 → `ValueError`。
  - 装配：猴补 `create_deep_agent` 抓 `middleware=`，确认有兜底时它排最后、没有兜底时最后一项是 `HistoryRepairMiddleware`；子智能体侧确认给定角色的末项是兜底、缺键角色一个 `FallbackMiddleware` 都没有。
- **遗留**：
  - 改动已生效（2026-09-21 重启后端后加载）。
  - **摘要模型的调用绕过兜底（2026-09-21 实测，已知缺口，未做）**：`deepagents` 的会话摘要挂在 `create_summarization_middleware(model, backend)` 上（`deepagents/graph.py:702` 传的**就是主模型本身**），而摘要中间件是**直接** `self._summary_model.ainvoke()`（`deepagents/middleware/summarization.py:1209` → `:432` → `langchain/agents/middleware/summarization.py:886`），**不经过 `FallbackMiddleware` 所在的那条中间件链**。后果：主导整家挂、且该轮跨过摘要阈值时，整轮就是硬失败 —— 重试耗尽后 `httpx.ConnectError` 直接冒到 `service`，记一条「处理失败」，前端一个字都收不到，兜底一点用不上。这恰恰是兜底该挡的场景（比「半路断流漏半句」严重得多）。实测触发条件：`/conversation_history/<thread_id>.md` 是 deepagents 自己的历史文件，**旧沙箱销毁后新建的沙箱里没有它**，轮 2 读它 404 →（我们的沙箱代理把 404 当「沙箱没了」触发恢复）→ 重连后仍 404 → 摘要路径启动（那次上下文 11.5 万 token）。怎么修没定：给摘要单独配一个也带兜底的模型，或把摘要调用也纳入同一套中间件 —— 先出方案再定。
  - **「半路断流」会造成重复文本（已实测；消不消仍待定）**：用探针驱动真实 agent 实测（`astream_events(v3)` 与 `stream_mode="updates"` 两条路径）。主导**先吐两段再抛**（405，报错体里塞了假 key）时，节点序列是 `model → after_model → tools → model → after_model → tools → model → after_model`，**每一轮各漏一次半句** —— 漏几次＝模型节点跑了几次，不是固定 2 次或 3 次；中间轮次兜底答的全是工具调用（所以那 47 片流一片都没落到前端，消费端看不到正文）。漏出的半句进了 SSE 流与落库正文（`service.py:358-364` 是 `ai` 分片的唯一出口），但**不进**模型可见的 state（工具结果与 checkpoint 里都没有它）。反过来说：常见的 405 / 429 配额、连接断都是在**连接期**就失败，**一片都不漏** —— 只有「已经吐了字才报错」才漏。想彻底消掉得在降级前发一个「丢弃已输出」的信号，代价是前端渲染逻辑要动。判据清楚了，**先不做**。
  - **熔断**：本次已做，见上面「熔断：主导连续失败就开闸」那条。

#### 一轮跑到一半报错：把已产出的部分补进记录，别让它在历史里消失

- **动机**：会话 `78780d7a` 的 `#223` 是活样本 —— 用户消息在库里（正文 70 字），**后面没有任何 assistant 记录**，直接跳到下一条用户消息。根因是落库的位置：用户消息在开跑前就写（`chat()`），助手那轮的正文字符串只在 `async for` 循环**之外**写一次（`_run_agent` 末尾），异常一跳过那个位置就永远不执行。后果不只是「这一轮没记录」：浏览器上已经逐字渲染出来的内容，刷新后从库里回放就没了，用户看到的是自己问了一句、底下空白。同一形态的 `#221`/`#222`（两条「重试」）、`#224`/`#225`（重复提问）都是这么留下的。
- **改动**（`src/ai_coding_harness/server/service.py` 一个文件）：
  - `_run_agent`：把「取流 + 消费流」整段包进 `try`，`except Exception` 里先落一条中断记录，再 **`raise` 原样抛出** —— 错误帧的产生仍然只留在 `chat()` 那一处，SSE 实时行为一个字没变。取流那一跳（`astream_events`）也包进来：它炸的时候正文是空的，但「这一轮失败了」这件事同样该留痕。
  - 新增 `_record_interrupted()`：落一条 `type="assistant"`（**不新增 type**，前端与接口 schema 都不用动）。`content` = 已产出的正文 + 中断标记 + 脱敏后的异常文本；`extra` = `{"intent", "partial": True, "error": {"type", "message"}}`。
  - 新增模块级 `_interrupted_tail()` 与两个常量 `INTERRUPTED_PARTIAL` / `INTERRUPTED_EMPTY`（「〔本轮中断，以上为部分输出〕」/「〔本轮中断，未产出内容〕」）。
- **关键决策**：
  - **标记与错误都写进 `content`，不藏在 `extra` 里**：前端 `partsOf()`（`App.vue:141`）只在**没有 parts** 时才退回 `content`，而 `extra` 目前只被用来读 `parts` —— 写进 `content` 前端零改动就看得见。顺带与实时视图对齐：前端收到 error 帧做的正是 `append("\n[错误] …")`（`App.vue:523`），所以刷新前后看到的东西一致。`extra` 里同时留结构化字段，以后做角标或排查有据可依。
  - **有 parts 的回合要补一块**：待办树存在时前端按 parts 回放、`content` 整段被忽略，标记得追加到块序列尾部，否则「这轮出过事」整个看不见。
  - **空正文也记一条**：不然时间线上又是「用户消息后面空着」，看不出这轮出过事 —— 那正是原来最难查的形态。
  - **「未产出内容」的判据是「用户看到过东西没有」**，正文或待办树都算：只有待办、没有正文时标记得说部分输出，说「未产出内容」会和上面那个待办框自相矛盾。
  - **异常文本原样给前端，与实时一视同仁**：`redact(str(exc), terms)` 之后再落库与下发，异常原文只进日志（`logger.exception`）—— 异常文本常把工具返回值整段带上，脱敏这道不能省。`type(exc).__name__` 是类名，安全。
  - **取消路径不记**：`asyncio.CancelledError` 是 `BaseException`，`except Exception` 天然不碰它。这是想要的 —— 删会话时那条记录会变成孤儿；代价是进程重启时在跑的那轮仍然丢部分输出，与现状一致。
- **验证**：`ruff check` 改动的两个文件干净（`service.py`、新测试文件；全仓另有 16 条**既有**告警，全在本次未改动的文件里）。新增 `tests/test_interrupted_record.py` 5 条：有正文、无正文、异常里夹合成令牌（落库必须是脱敏版）、有 parts 补块、正常收尾不被误标 `partial`。`tests/v3_events.py` 加一个 `ExplodingAgent` 假件（`after=N` 吐 N 帧后炸；`after=None` 在取流那步就炸）。全量 `pytest` **569 条：563 passed / 6 failed** —— 那 6 条是 `src/ai_coding_harness/stream.py` 今天 22:12 的在途改动（里面还留着 `print("sub text:", ...)` 调试行）造成的既有失败，与本次改动无关。
- **遗留**：
  - **改动尚未生效**（需重启后端）。
  - **`sync` 直接执行路径是同一个病，本次没修**：`_execute_direct` 抛错时同样只有 user 记录、没有 assistant 记录（保护它的 `_record` 在 `chat()` 里，异常时被跳过）。同一把刀能修，但这是你没点名的部分，等你点头再动。

#### 按智能体划边界：每个角色能用的工具与能写的目录（外加一层全局底线）

- **动机**：原来只有一条**与角色无关**的危险命令黑名单，拦的是「谁都别干」的那几件事。可代码工作区里真正的越界不是「危险」，而是**错位**：出方案的智能体去改源码、审查的智能体去动工程目录、问答的智能体往仓库里落盘。这类越界黑名单一个都拦不住 —— 命令本身完全正当。定下的口径是「拦截范围根据智能体而定，不只是拦截危险命令，而是拦截每个智能体不能执行的工具，以及目录范围」。
- **改动**：
  - `src/ai_coding_harness/safety/permissions.py`：`DANGEROUS_COMMANDS` 由 7 条扩到 18 条，按四组归类 —— **毁机器**（原 7 条）、**凭据**（`.secrets` / `gitee-askpass` / `.git-credentials`，提到就拒）、**提权**（`sudo`）、**破坏性 git**（`push --force` / `+refspec` / `reset --hard` / `clean -f` / `checkout|restore .` / `branch -D`）。docstring 写清与 `policy.py` 的分工。`CommandSafety` 本身一行未动。
  - 新增 `src/ai_coding_harness/safety/policy.py`（纯函数，无副作用）：`AgentPolicy`（`write_roots` + `hidden_tools`，`frozen=True`）、策略表 `_ROOTS`、`policy_for(name, settings)`、目录判定 `_under_any`、命令落点 `command_write_targets`、以及 `write_targets(tool_name, args)`。装配名一张表管到底：主控用**形态名**（`orchestrate` / `plan` / `qa` / `analysis` / `review`，见 `profiles.py`），子智能体用**角色名**（`planner` / `coder` / `reviewer`）。
  - 新增 `src/ai_coding_harness/agent/middleware/boundary.py`：`BoundaryMiddleware` 一个类两件事 —— `awrap_model_call` 把该角色不该用的工具从 `request.tools` 里摘掉（**看不见** 就不会选），`awrap_tool_call` 在真要执行时再判一次（历史里带回来的调用、模型凭记忆硬写的调用名、以及 `execute` 里那条命令**往哪儿写**，这三种都只有执行前拦得住）。拦下返回 `ToolMessage(status="error")` 而不是抛异常：模型看得到理由、能换做法，图也不因为一个被拒的调用塌掉。
  - `agent/middleware/__init__.py`：加入重新导出与 `__all__`。`agent/graph.py`：`build_agent` 新增必填关键字参数 `policy`（与可选 `safety`），`BoundaryMiddleware` 排在我们这批中间件的**最外侧**。`agent/subagents.py`：`build_subagents` 新增必填 `policies`，三个角色各挂自己的那一份（主智能体的自定义中间件不下传）。`session/manager.py`：装配点按 `profile.form` / 角色名取策略传下去。
  - `agent/prompts.py`：planner 的禁写从「劝阻」改成事实 —— 方案经 `storePlan` 落库，不再另存一份文件；`_ROLE_PLAN_MAIN` 补「## 可写范围」段（只有 `/ai_coding/output/`）。
  - `docs/workspace-directories.md`：从「约定」改写成**执行口径**，补按智能体的读写矩阵、关键规则、已知缺口（相对路径判不出落点时放过、管道与间接执行、后端第二道只覆盖底线）与 skill 对应。
  - `tests/test_boundary.py`：新增 73 条（策略表逐名断言、未知名字 fail-closed、目录前缀/`..` 归一、凭据与平台资产的读写双向、命令落点的 13 个形状、中间件两层、全局底线接在 `execute` 上、策略对象不可变）。既有四个测试文件的 `build_agent` / `build_subagents` 调用点补上新的必填参数。
- **关键决策**：
  - **不用上游机制**（都查过源码，不是疏漏）：`FilesystemMiddleware(_permissions=…)` 与 `SubAgent["permissions"]` 在后端 `supports_execution` 时直接 `NotImplementedError`（我们的 `OpenSandboxBackend` 继承 `BaseSandbox`，正是这种），而且它只覆盖 fs 工具的读写、管不到 `execute`；`HarnessProfile.excluded_tools` 的**键是模型**，主控与 planner 共用一个模型，表达不了「按角色」。
  - **职责分工，一处规则只写一遍**：`permissions.py` 管与会话相关的底线，`policy.py` 管按智能体的范围。按角色的范围**只在中间件上落地** —— 后端是会话级共享的（一个会话一个后端、换形态不重建），挂不上按角色的判断；底线与会话绑定也成立，所以 `aexecute` 上另有一道，那是纵深不是替代。
  - **未知装配名 fail-closed**：没登记的名字按「不写文件」处理，而不是放开。新加角色忘了登记时，代价是它写不了文件、在工具返回里看到理由 —— 比默默拿到全目录写权限小得多。同理 `build_subagents` 取不到 `policies[role]` 要 `KeyError`，不做默认。
  - **豁免 `/tmp` 与 `/dev/*`**：随沙箱销毁、没有读方，逐角色计较没有意义；`/ai_coding/tmp` 是另一回事（有角色归属，见表）。
  - **只拦确定的凭据落点，不做通用读范围**：读方向只列 `/ai_coding/.secrets` 与 `/usr/local/sbin/gitee-askpass`。收窄读取会误伤 `cat /etc/os-release`、`git --version`、`pip list` 这类正当命令，收益却近乎为零。走 `execute` 的那条路由 `permissions.py` 的凭据规则兜 —— 顺带补上计划里没点名的缺口：`read_file` / `ls` / `glob` / `grep` 走的是文件接口、根本不经过命令检查。
  - **明说「反滑不反敌」**：拦的是模型手滑与越界意图，不是铁了心绕的对手。相对路径判不出落点时放过（沙箱当前工作目录不在我们手里，硬判会误伤大量正当命令）、`~` 目标放过、管道与间接执行看不出。真正不可挽回的那批由 `permissions.py` 的底线兜。
- **实测修掉的两个判定 bug**（都是探针跑出来的，不是想出来的）：
  - `sed -i 's|/a|/b|' file` 判出的写目标是**空**：按 `|` 硬切段把这条命令切成了三段、写目标丢了。改成**引号感知**的切段（`_segments`），另加 `_unquoted` 只给重定向扫描用（`echo "x > /a"` 里的 `>` 不是重定向）。
  - `cmd 2> err.log` 与 `cmd &> all.log` 的重定向**漏判**：原正则把数字与 `&` 排除在「前缀字符」之外，而 `2>&1` 不会误判的真正原因是它的目标是 `&1`、本来就不匹配。收紧成只排除 `>` 之后，`2>` / `&>` 能判了，`2>&1` / `>&2` 仍然不判。
- **验证**：`ruff check` 全仓只剩 18 条**既有**告警，全在本次未改动的文件里（`agent/profiles.py` 两条未用导入、`db/`、`sandbox/`、`tools/plan.py` 与两个测试文件）；全量 `pytest` **564 passed**（原 491 + 新增 73）。
  - **纯函数层**：13 个命令形状逐条核对，另拿 26 个真实命令样本跑探针；`sed -i 's|/a|/b|'`、`2>` / `&>`、`2>&1`、引号里的重定向与管道、heredoc 正文这几条都是探针逼出来的。
  - **装配层**：五个形态各自建图 —— 编码形态的三个子智能体中间件里都有 `BoundaryMiddleware`，`policies` 少给一个角色当场 `KeyError`。主控那份不生成独立图节点（只有 `wrap_*`，没有 `before_agent`），所以主控这一侧改**实跑**验证：`qa` 写 `/ai_coding/output/a.md` 成功落盘、写 `/ai_coding/project/a.py` 被拒（理由「qa 只能写入 /ai_coding/output，…不在其中」）；`coding` 写 `/ai_coding/skills/x/SKILL.md` 被拒、读 `/ai_coding/.secrets/gitee_token` 被拒、`git push --force` 被底线拒；`mkdir -p /ai_coding/tmp/x` 过了边界（只在 `backend=None` 的探针里因不支持执行而失败，与边界无关）。中间件链的实际顺序（由外到内）也借这次探针印了出来：deepagents 默认栈在**外**，我们这批在**内**，`BoundaryMiddleware` 排在我们这批的最外侧。
- **遗留**：
  - **改动尚未生效**（需重启后端）。
  - **分析形态的 `tmp` 是审阅时补上的**：`repo-bootstrap-analysis` 技能里写「中间文件放 `/ai_coding/tmp`」，而分析形态起初只给了 `output` —— 照 skill 写会被拒（理由会告诉它改道 `output`，不算坏，但两边口径不一致）。已确认并在 `_ROOTS["analysis"]` 里加上 `"tmp"`，代码、文档、测试三处同步。判据是「有没有读方」：`/ai_coding/tmp` 没有读方、随沙箱销毁，放开的代价近乎为零。
  - **按角色的范围只有中间件这一道**：`OpenSandboxBackend` 是会话级共享的，挂不上按角色的判断（见「关键决策」）。唯一不经中间件的沙箱调用是 `service.py::_direct_sync`（sync 意图），但它跑的是**我们写死的** `test -d` / `git pull` / `git clone` 三条、模型选不了命令，所以这里不是可被利用的绕过，只是"以后别往那条路上加模型可控的参数"这条约束没人强制。
  - **`agent/profiles.py` 有两条未用导入**（`BaseChatModel` / `BaseChatPromptTemplate`，第 22-23 行），是早先几次改动留下的，本次没动它们。

#### 工具调用配对自洽：三个钩子堵住「孤儿调用」造成的永久 400

- **动机**：会话 `78780d7a` 的**每一次**请求都 400 —— `An assistant message with 'tool_calls' must be followed by tool messages ...`，缺 id 清单**是空的**、`param: 'messages.[200].role'`，重试也不自愈。按 `checkpoint_blobs` 快照 + `checkpoint_writes` 增量重放出真历史（**383 条**，不是读快照得到的旧值）后定位：`[370]` 一条 `task` 调用带的 id 是**空串**、它后面**没有任何 ToolMessage**（下一条就是人类消息），是一个落在历史**中间**的孤儿。三个方向同时漏：
  1. **模型出口**吐出空 id 的调用（qwen 时代 v3 装配器浅覆盖的产物，见本日「多智能体主控从 qwen 换到 glm」那条，整批五条都是空串、**一个 None 都没有**）；
  2. **工具异常**：`ToolNode` 默认只把参数校验失败转成 tool 响应，其它异常（沙箱掉线等）一律上抛，整个 superstep 连同同批结果一起丢；
  3. **上游 `PatchToolCallsMiddleware` 的判据被空串骗过**：它算的是「**全历史**所有 tool 响应的 id 集合」，而历史里已经有过空 id 的响应 —— 于是这条空 id 调用被判成**已答**，每轮都跑、每轮都判「无事可做」，一个字节不写。实测把它的 gate 在真历史上复算：**能触发的 0 条**，与「17:06 那几个 checkpoint 只有新人类消息一次写入」吻合。
  - 顺带纠正两处早先的判断：读快照里的「337 条」是旧值（真值 383）；「补丁与模型调用同 superstep、写入被丢弃」是错的，`before_agent` 的写入会提交，它不生效纯粹是判据错。
- **改动**：
  - 新增 `src/ai_coding_harness/agent/middleware/tool_integrity.py`（模块 docstring 写全三个漏向与各自的取舍）：
    - `ToolCallIdMiddleware`（`awrap_model_call`）：模型出口把空/`None` 的 id 换成 `call_` + 24 位 hex（与端点同形的 29 字符）。目的是**同一轮内**就配得上 —— `ToolNode` 按 state 里那条 AI 消息的 id 写回响应；
    - `ToolErrorGuardMiddleware`（`awrap_tool_call`）：工具抛异常时补一条 `ToolMessage(status="error")` 而不是上抛；`GraphBubbleUp`（中断）照原样上抛，吞了就没法打断。排在 `RedactionMiddleware` **内侧**，合成的异常文本照样过脱敏；
    - `HistoryRepairMiddleware`（`abefore_agent`）：开工前把历史修成自洽的，`RemoveMessage(REMOVE_ALL_MESSAGES)` + 全量列表整表重写（孤儿在中间，而图输入只能追加）；`repair_messages` 返回 `None` 时**一个字节都不写**；
    - 纯函数 `repair_messages`：先给空 id 补真 id（**两侧同时换** —— 只换一边等于制造新的不匹配），再让空 id 的响应按顺序认领**空 id 的调用**，再判「答没答」，最后给仍没人答的补一条说明性响应（参数坏掉的与「跑到一半被打断」措辞分开）。
  - `agent/middleware/__init__.py`：三个类加入重新导出与 `__all__`。
  - `agent/graph.py`：主智能体挂三件；`register_harness_profile("openai", ...)` 加 `excluded_middleware=frozenset({PatchToolCallsMiddleware})` **排除上游那份**（同一件事的错判据，留着只会白写一遍且写的是错的）。
  - `agent/subagents.py`：planner / coder / reviewer 三个角色各挂一份（主智能体的自定义中间件**不下传**，而它们自己也跑工具）。
  - `tests/test_tool_integrity.py`：新增 29 条（空 id 配对、`None`、上游判据漏掉的那条回归、invalid 调用措辞与归档、邻近/非邻近「已答」、幂等、三个钩子的分支、装配点与子智能体逐角色）。
- **关键决策**：
  - **不照抄「id 在集合里就跳过」这个判据**，否则复刻同一个坑。「已答」只看**这条 AI 消息之后**的响应；紧跟 AI 的那一串 ToolMessage 只是**补配对时的落点**（provider 要求它紧随），不是「答没答」的依据。
  - **空 id 与真 id 不混认**：空 id 的响应只认领空 id 的调用，不能让它去认领真 id 的调用（那会把配对错位）。
  - **只在需要时整表重写**：每轮都重写会让每个 superstep 多出一次全量 checkpoint 写入；修好的历史下一轮就是干净的，所以只对「已经坏掉的那一轮」付费。
  - 三个钩子都**只实现异步版**（服务端统一走 `astream_events`，与 `redaction` 同因）；上游排除走**类形式**（`excluded_middleware` 的类形式按精确类型匹配，deepagents 的覆盖校验会在条目没匹配到任何栈时直接抛错）。
- **验证**：`ruff check` 改动文件全过；全量 `pytest` **491 passed**（原 462 + 新增 29）。拿**真实 checkpoint** 复算：真历史 383 条、修前**仅 1 条**位置性孤儿（`[370] task`，空 id，紧随响应 0 条）；`repair_messages` 输出 384 条（补 1 条、丢 0 条），修后孤儿 **0** 条，再跑一遍返回 `None`（幂等）。装配探针：主图节点有 `HistoryRepairMiddleware.before_agent`、**没有** `PatchToolCallsMiddleware.before_agent`，三个子智能体各自带全三件。
- **遗留**：
  - **改动尚未生效**（需重启后端）。重启后该会话**下一轮对话即自愈** —— 修复节点跑在模型调用之前。
  - **`ToolCallIdMiddleware` 能否拦住 v3 装配器那条路未经验证**：空 id 有可能在装配器里、也就是在模型调用的装配**之后**才出现。拦不住也不影响这条会话（真历史必然经过整表修复），但「模型出口就补好」这层可能落空。
  - **上游装配器的浅覆盖没修**：谁再把某个角色指回 qwen、或换成同样「续片带空 id」的供应商，都会再造出一批空 id（可由整表修复兜住，但那是善后不是预防）。

#### 温度改成逐家可配（kimi 取 1，其余仍 0.3）

- **动机**：`build_model` 原先无条件写 `temperature=0.3`，而 kimi-k2.6 只收 `temperature=1` —— **review / reviewer 两个角色每调一次模型就 400**（`invalid temperature: only 1 is allowed for this model`），不是偶发而是必坏。`logs/` 里搜不到这条报错，说明 review 形态至今没被真实跑过。上一条换主控模型时实测发现。
- **改动**：
  - `config.py`：`ModelConfig` 新增 `temperature: float = 0.3`（注释写明为什么是逐家的：kimi 只收 1，其余几家 0.3 是调优结果）；`kimi_model` 属性显式传 `temperature=1`；`qwen_model` 的 docstring 改成「当前没有角色取用」（上一条换主控的连带，配置与 `.env` 键仍保留）。
  - `agent/llm.py`：`build_model` 由写死的 `0.3` 改读 `cfg.temperature`。
  - `tests/test_llm_config.py`：新增 `test_kimi_only_accepts_temperature_one`（逐 review / reviewer 钉住 `1`），原有 `test_temperature_is_low`（coder 的 0.3）不动。
- **关键决策**：**不新增 `KIMI_TEMPERATURE` 之类的环境键**。温度是「这家模型收什么」的接口事实，不是运维旋钮；写进代码、由属性给出，改起来只碰一处。分类模型走的是另一个入口（`build_classifier_model` 自己传 `temperature=0`），不受本次改动影响。
- **验证**：`ruff check` 改动文件全过；全量 `pytest` **462 passed**（原 460，新增 2 条）；拿**真实 `.env`** 跑 v3 探针逐角色实测：`review` / `reviewer` 由 400 变为正常出 id（kimi-k2.6，22 字符），`orchestrate` / `plan` / `planner`（glm-5.3）29、`coder`（deepseek-flash）32 均不变。
- **遗留**：**改动尚未生效**（需重启后端）。另：该探针复跑时 `qa` 的 gpt 端点两次 `APIConnectionError`（`api.ztoken.pro` 抖动，前一次同角色 id 正常 29 字符），与本次改动无关。

#### 多智能体主控从 qwen 换到 glm（qwen 在 v3 事件流下丢 tool_call id）

- **动机**：页面报错 `[错误] Tool call ID is required for subagent invocation`。根因**不在我们这层**，而在 `astream_events(version="v3")` 的装配路径上：
  1. 后端用 `agent.astream_events(..., version="v3")`（`server/service.py`）消费事件流；
  2. v3 的装配器 `langchain_core/language_models/chat_model_stream.py::ChatModelStream._assemble_message` 从「按 index 累加的分片表」里取工具 id；
  3. DashScope（qwen）只在每个 index 的**第一个**分片带真 id，后续分片带 `id: ""`；而 `_merge_block_delta_into_store` 是**浅覆盖**（非 `None` 就写入），空串把真 id 冲掉了；
  4. 于是那一轮 AIMessage 的 `tool_calls[*].id` 全为 `""`，对应 ToolMessage 的 `tool_call_id` 也是 `""`（从 checkpoint 里解出来的原始值，不是渲染问题）；
  5. 同一轮里 `get_pr_info` / `execute` / `write_todos` 都照常跑通 —— **只有 deepagents 的 `atask`（`deepagents/middleware/subagents.py`）硬校验 `runtime.tool_call_id`**，所以坏的只有「委派子智能体」这一条线。
- **改动**：`agent/llm.py` 的 `_ROLE_MODEL_ATTR["orchestrate"]` 由 `qwen_model` 改 `glm_model`（多智能体主控与 plan / planner 同款 glm-5.3）；`agent/subagents.py` 与 `tests/test_profiles.py` 里「主智能体在 coding 形态下是 qwen」的陈旧注释同步改；`tests/test_llm_config.py` 的 `_EXPECTED["orchestrate"]` 与窗口参数化行（`qwen_context_window` → `glm_context_window`）跟着改。`qwen_model` 配置属性与 `.env` 的 `QWEN_MODEL_NAME` **保留不动**——成了没人取用的休眠配置，日后要换回来只改路由表一行。
- **关键决策 / 实测**：v3 下逐角色实测（同一张图、只换模型）——`orchestrate`/qwen3.8-flash **id 长度 0**（丢）、`plan`+`planner`/glm-5.3 29、`qa`/gpt-5.5 29、`coder`/deepseek-flash 32；换成 glm 后 `orchestrate` 复测 **29**。选 glm 而非 deepseek 的理由：glm 已在真机 plan 形态跑过、v3 下 id 保住、且接受 `temperature=0.3`（kimi 不收，见遗留）。旁证：失败那条 AIMessage 带 `output_version="v1"` 元数据，正是 v3 装配器盖的指纹；同一模型走 v2 事件流 id 完好。
- **验证**：`ruff check` 改动文件全过；全量 `pytest` **460 passed**；拿**真实 `.env`** 逐角色建模型，8 个角色路由符合预期（`orchestrate`/`plan`/`planner`→glm-5.3@open.bigmodel.cn，其余不变）；v3 探针复测 orchestrate 的 id 已回长。
- **遗留**：
  - **这是回避不是根治**：上游装配器的合并覆盖没修，谁再把某个角色指回 qwen、或换成同样「续片带空 id」的供应商，都会重犯。
  - **`review` / `reviewer` 当时是必坏的**：`build_model` 无条件写 `temperature=0.3`，而 kimi-k2.6 只收 `temperature=1`（实测 400 `invalid temperature: only 1 is allowed for this model`；换成 1 后正常出 id）。`logs/` 里搜不到这条报错，说明 review 形态至今没被真实跑过。**已在上一条修掉**（`ModelConfig` 加 `temperature` 字段）。
  - **改动尚未生效**（需重启后端）；重启后跑真机验证会落到 coding 形态，可能真去 commit / 开 PR，需单独授权。

#### 意图分类**临时**降级到 deepseek（本机分类器不启动时）

- **动机**：本机 vLLM 分类器（`localhost:6006`）这阵子不启动；没起时每一轮都白等 5s 超时再退回关键字口径（见 09-17 那条的遗留）。临时把分类这一跳交给已配好的 deepseek，`INTENT_*` 原样留着当回切路径。
- **改动**：`config.py` 的 `classifier_model` 从「`INTENT_*` 优先、缺失才回落主模型」改成**直接取 deepseek 三项**（docstring 注明是临时的、以及回切怎么改）；`.env` 一字未动，三个 `INTENT_*` 键仍在、此刻被忽略。`llm.build_classifier_model` 的参数（`temperature=0`、`max_tokens=512`、关思考的 `extra_body`）保持原样。
- **关键决策 / 实测**：
  - **关思考那行 `extra_body` 不能删**：裸调对比，带它 `finish=stop, content='OK'`（0.7~0.9s）；不带则 `finish=length, content=''` —— `deepseek-flash` 同样是思考型模型，`max_tokens` 全被思考吃掉、正文一个字没出。顺带修正 09-05~09-17 那条的措辞：这个键不只 vLLM 认，deepseek 也认（不报 400、确实生效）。
  - 判定质量与耗时：三条样板消息（qa / coding / plan 各一）全 `source=llm`，0.89~1.29s（本机 vLLM 是 0.3s，超时线仍是 5.0s）。强关键字依旧在 LLM 之前短接（`match_strong`），口令类消息一次云调用都不发。
  - 代价：分类与 coder 角色共用同一家 key 与模型 —— 「先降级」可接受的耦合，不为此另立配置项。
- **验证**：重启后端后一轮真实对话（会话 `78780d7a`，qa 形态）：日志 `意图分析：intent=qa source=llm 理由=询问现有登录逻辑，属查看/答疑类` —— 模型判定真生效（老口径此刻会是 `source=fallback` 加 5s 超时）。该轮 11 次 LLM 调用 / 23 次工具 / 3215 字，checkpoint 里 11 条模型名全是 `gpt-5.5`，qa 角色的路由未受影响。`tests/test_intent_detection.py` 36 条通过、ruff 干净。
- **遗留**：`intent_timeout_seconds` 仍 5.0s（云上留 4 倍余量）；「干脆关掉 LLM 判定走纯关键字」的开关 `INTENT_LLM_ENABLED` 未动。

#### 模型按角色路由：一家一个岗位

- **动机**：原先只有 DeepSeek（主）与 Kimi（备）两家，且全部智能体共用一个模型实例 —— 出方案、写码、审查、问答跑在同一家上。用户补了 qwen / gpt / glm 三家配置并指定用途：qwen 做多智能体主控、glm 做 planner、kimi 做 review（主与子）、gpt 做 qa / analysis、coder 仍是 deepseek。
- **改动**：
  - `agent/llm.py`：新增 `_ROLE_MODEL_ATTR` 路由表（角色 → `Settings` 上哪个 `*_model` 属性）+ `_config_for`；`build_model(settings, *, role)` 取代原 `backup: bool`。
  - `config.py`：`ModelConfig` **删掉 `extra_body` 字段**（`build_model` 本来也没转发过它 —— 给 glm 配的 `thinking` / `reasoning_effort` 一直是死配置）；`primary_model` → `deepseek_model`、`backup_model` → `kimi_model`（Kimi 已不是「备用」而是 review 岗，改名后与新增的 `glm_model` / `gpt_model` 同口径）；补 `qwen_model`；新增 `glm_context_window` / `gpt_context_window` / `qwen_context_window` 并接进各自属性。
  - `agent/subagents.py`：`build_subagents(*, tools_by_role, model_for)` 增加 `model_for`，每个角色带**自己的** `model`（原先不指定即继承主智能体）。
  - `session/manager.py`：`SessionManager` 的 `model: BaseChatModel` 换成 `model_for: Callable[[str], BaseChatModel]`，主智能体按 `profile.form` 取模型。
  - `api/app.py`：lifespan 不再预建一个共用模型，改传 `lambda role: build_model(settings, role=role)`。
  - `.env` / `.env.example`：补三个窗口键；顺手修掉 `.env.example` 里 `DEEPSEEK_MODEL` / `KIMI_MODEL` 两个**旧键名**（真键是 `*_MODEL_NAME`，照抄示例会静默拿不到模型名）。
  - `tools/web_search.py`：两处错误提示里的 `ZHIPU_API_KEY` / `ZHIPU_BASE_URL` 改 `GLM_*`。
- **关键决策**：
  - **路由挂在「形态」那根轴上，不另立词汇**：主智能体的角色名就是 `profile.form`，子智能体的角色名就是 `_ROLES` 的键，两者共用一个 `_ROLE_MODEL_ATTR`；缓存键本就是形态名，于是换形态必然连模型一起换，不会出现「用着 qa 的工具、跑着 plan 的模型」。
  - **未登记的角色不回落，直接报错**：静默落回某个模型会让某一轮悄悄跑在错的模型上，比当场失败难查得多；守卫测试逐个形态、逐个角色钉住登记齐全。
  - **窗口取保守值**（glm / gpt 128000、qwen 1048576）：三家接口都不暴露上下文窗口 —— `/models` 只回模型名，能问到的 131072（glm / qwen）与 128000（gpt）是**输出**上限不是窗口，文档也取不到（`docs.bigmodel.cn`、`alibabacloud.com` 均被网络策略挡）。偏小只是早压一点，偏大是真的把请求撑溢出。
  - **子智能体给模型实例而不是字符串**：`SubAgent.model` 声明是 `str | BaseChatModel`，但传字符串会走 deepagents 自己的 `resolve_model` 重建，丢掉 api_key / base_url。
  - **qwen / glm 不留思考开关**：qwen 在云端默认开启，glm 是服务端默认，`build_model` 本来就不转发任何额外参数。
- **验证**：`ruff check` 改动文件全过；全量 `pytest` **460 passed**（原 426）；拿**真实 `.env`** 逐角色建模型，8 个角色全部落到预期家（`orchestrate`→qwen3.8-flash@dashscope、`plan`/`planner`→glm-5.3@open.bigmodel.cn、`qa`/`analysis`→gpt-5.5@api.ztoken.pro、`review`/`reviewer`→kimi-k2.6@api.moonshot.cn、`coder`→deepseek-flash@api.deepseek.com），窗口逐家生效；gpt 端点 `api.ztoken.pro` 实测连通（早先那次连接失败是探针自身拼错了路径，不是端点不通）。
- **遗留**：**改动尚未生效**（需重启后端）；三个窗口值是保守估值，日后拿到真实窗口应当校准。

#### policies 补齐四种语言的风格指南（Python / TS / JS / Go 三份）

- **动机**：`/ai_coding/policies/` 此前只有一份 `java-style.md`，而平台声明的支持范围是 Java / Python / JavaScript·TypeScript / Go —— 写码与审查的共同依据缺了另外几门。
- **改动**：`sandbox/policies/` 新增六份译制文件（均纯 LF、无 CR）：

| 文件 | 行数 | 字节 | 代码块 | 标题 | 锚点行 | 站内引用 |
| --- | --- | --- | --- | --- | --- | --- |
| `python-style.md` | 3085 | 105130 | 140 | 152 | 490 | 39 |
| `ts-style.md` | 3028 | 99645 | 207 | 167 | 15 | 23 |
| `js-style.md` | 3054 | 113756 | 153 | 214 | 31 | 40 |
| `go-style.md` | 429 | 24933 | 6 | 21 | 24 | 3 |
| `go-decisions.md` | 3088 | 117023 | 146 | 87 | 122 | 37 |
| `go-best-practices.md` | 3181 | 129032 | 140 | 74 | 75 | 33 |

- **做法**：译文一律不手抄代码 —— 中文单元里只留升序的 `@@BLOCKnn@@` 占位符，`splice.py` 从英文原文把代码块逐字节回填。Python 那份还核了 `en/` 与上游原始 markdown 的关系：3689 行正是 3778 行的 `[89:3778]` **连续切片**，切掉的头部 89 行是上游的 AUTHORS 注释块。
- **关键决策**：
  - **上游是三个页面的（Go）就出三份文件**，不硬合成一份 —— 概览、风格决策、最佳实践各自独立引用更顺。
  - **锚点行贴在标题之上**：上游 `#anchor` 引用指向的是旧页面 id，不补就是满地断链（`go-decisions.md` 一度少 62 个锚点、93 处断链）。
  - **体例随上游**：Google 自己的 JS/TS/Python 指南没有「强制/推荐」分级，就不套 java 手册那套三分级；只在开头加出处与 CC BY 3.0 署名，TS 那份保留上游「本指南已不再更新，建议迁移 TypeScript」的提示。
  - **复核另起一套判据**：`verify_anchors.py` 不复用 `assemble.py` 自己的断言，直接拿上游原文重新数代码块、标题层级、节号序列、锚点多重集与站内引用落地情况。
- **验证**：六份的独立复核全部通过 —— 代码块与英文原文逐字节一致且顺序相同；标题层级与节号逐条相符（Python 的 152 条节号与上游逐条相同）；锚点计数一致；除上游自带的悬空引用（TS 6 个、Python 1 个 `from-future-imports`）外，站内引用全部落地；纯 LF、无 CR、无占位符残留。
- **遗留**：都还没在真实会话里让智能体读过。`policies/` 是**建会话时整棵上传**的，要到**新建会话**才进沙箱，与后端重启无关。

#### 仓库记忆写入加 PR 闸门

- **动机**：仓库记忆记的是「已合并的现实」。仓库还有未关闭的 PR 时，仓库正处在待审状态，此时写记忆等于把尚未落定的东西写成既成事实 —— 下一轮再读回来，它就成了依据。
- **改动**：
  - `tools/git.py`：新增 `GiteeClient.open_pulls()`（与 `_MEMORY_GATE` 第 1 步 `get_pr_info(<repo>, state="open")` 同口径）；新增 `PrLookupError(RuntimeError)`，把接口返回的 `{"error": ...}` 与非 2xx 都归一成该异常。
  - `tools/memory.py`：`MemoryContext` 加 `open_prs` 回调；`save_repo_memory` 在 `aread` **之前**先探 PR，有未关闭 PR 就直接拒绝 —— 不读也不写。拒绝文案列最多 5 条 `#n [open] 标题 (作者, 头 → 基)`，超出用「等 N 个」收口，并写明理由。
  - `tools/register.py`：注入 `GiteeClient(...).open_pulls`；工具描述补一句闸门说明。
- **关键决策**：探测放在**读之前**（否则白读一遍）；查询失败按「拒绝写入」处理，而不是按「没有未关闭 PR」放行 —— 宁可挡下，也不在状态未确认时写入。
- **验证**：`tests/test_memory_tool.py` 加 autouse fixture stub 掉 `register.GiteeClient`，新增用例覆盖：有开放 PR 时不写也不读、拒绝文案带编号与理由、无 PR 照旧放行、探测抛错时的策略、超过 5 条截断、工具描述含闸门。提示词未动（`TestMemoryWritebackGate` 仍成立）；令牌仍只存在 `GiteeRepo` 里，不进上下文与日志。
- **遗留**：尚未在真实会话里验证（要重启后端才生效）。

#### 委派的子任务挂进待办树

- **动机**：主控派子智能体时，子智能体自己的清单此前没有落点，`_apply` 会把整份丢掉 —— 前端只看得见主清单，看不见子任务里的推进。
- **改动**：
  - `server/stream.py`：`_apply` 去掉「没落点就整份丢掉」的提前返回，子清单一律存下（嵌套 ≥2 层仍丢弃）；`note_delegation` 记下 `input.subagent_type`；`tree()` 把无落点的子清单收进容器项；新增 `_container()` / `_rollup()` / `subagent_name()`。
  - 补掉两个边界：主项被改名 → 建容器；子清单为空 → 不建空壳。
  - `prompts.py`：`_ROLE_ORCHESTRATE` 的「## 工作流程」加一句前置规划约束（步骤 a–g 不动、不重编号）。
  - 前端：由「正文块 + 待办块」改成「正文块按先后 + 一棵待办树挂在最后」，整帧整体渲染。
- **关键决策**：挂靠用**两个锚点** —— `task` 的 `tool_call_id` 认出是哪一次委派，主清单里 `in_progress` 的那一项决定父节点；脱敏要递归进 `children`；整棵树按帧整体下发，不做增量。
- **验证**：2 个旧用例改写，`test_note_delegation_ignores_other_tools_and_nested_agents` 重新钉成「不挂在主项下、进容器」；新增 7 条；**417 passed**；改动文件的 ruff 干净（`test_plan_events.py:101–175` 的 6 条 RUF100 是既有告警，未动）。前端无改动 —— 前半程那版在 5174 端口用 stub 数据在浏览器里看过：DOM 顺序 `[div.ai-part.text, ul.ai-part.todos]`，`in_progress` 的父项展开、`pending` 折叠，有子项才给折叠按钮，标签 `1/3`、`1/1`；当时 pytest 409 passed、`vue-tsc` + `vite build` 通过。
- **遗留**：真实委派的活体验证待重启后做。

#### 输出形态规则集中到 adhd/AGENT.md

- **动机**：输出形态（怎么分行、怎么收尾、指代怎么写）此前散在各个角色段里，改一处漏一处。
- **改动**：规则集中到 `adhd/AGENT.md`，由 `build_main_prompt` 读盘（`settings.agent_output_style_file`）拼进主 prompt。五种主智能体形态都经过该函数，三个子智能体的提示词各是独立常量、不经过 —— 「只约束主智能体」是结构保证的，不靠在别处记得别加。刻意不用 deepagents 的 `memory=`（那是它自己的记忆注入通道，把输出规范混进「记忆」语义里会串味）。
- **关键决策**：加一句范围限定 —— 该段只管**面向读者的正文**，PR 评论、仓库记忆、写进 `/ai_coding/output/` 的文件各按自己的格式。这样就不必再按 review / orchestrate 两种形态各写一遍（`comment_pr` 的调用点都不产出用户可见正文：review 形态与 orchestrate 回评各一处，编码流的审查评论归 reviewer 子智能体）。同时新增第 10 条「指代对方写『你』，不要写『用户』」。
- **验证**：`tests/test_profiles.py` 54 passed。

#### 发送即时上屏；409 改成「接回本轮」而不是死胡同提示

- **动机**：用户反馈发消息后自己的话迟迟不进聊天记录，隔一会儿重发却提示「本轮仍在进行中」。
- **根因**（排除法定位，不是后端慢）：中间件是纯 ASGI（`ApiLogMiddleware` 自带注释说明不用 `BaseHTTPMiddleware` 就是怕影响 SSE 流式）、`RunHub.start` 是同步非阻塞的 `create_task`；实测探针显示 **响应头 0.116s 到达**。真正的问题是前端顺序：`send()` 把「push 用户消息 + 置 `loading`」放在 `await postChat` **之后**，而 `loading` 正是输入框与发送按钮 `:disabled` 的闸门 —— 等 POST 回来的那段窗口里 UI 还是活的，用户以为没发出去于是再点一次，第二次点击真能发出去，后端那一轮已经在跑，于是 409。
- **改动**：
  - `frontend/src/App.vue` 的 `send()`：清空输入框、push 用户那条与 AI 占位、置 `loading`、`scrollToBottom()` 全部提到 POST **之前**；POST 失败时按下标摘掉刚放上去的两条、把文字还原回输入框（`loading` 期间无人能动末尾两条，按下标摘是安全的）。
  - 同文件新增 `isRunInProgress(id)`：409 有两种成因共用同一个状态码 —— 「本轮已在跑」（接回来就好）与「会话已失效 / 缺仓库信息」（接不回，只能报错）。唯一干净的判据是详情里的 `running`，与 `openSession` 判断「刷新后要不要接回」同口径，不另立一套。
  - 409 且 `running` 为真 → `attachRunning(id)` 接回直播流（用户那条后端本就没收到，内容留在输入框里，等这轮完了他自己再发）。取详情失败则当作接不回，走报错。
  - `frontend/src/api.ts`：`postChat` 原来把**所有** 409 都换成固定文案「本轮仍在进行中…」，导致会话已失效也报这句话，还会让上面那个自动接回逻辑误判。新增 `errorDetail()` 原样带出服务端的 `detail`。
- **关键决策**：用户那条的时间戳取**发送时刻**、AI 那条留空等流结束再补 —— 与后端「回合收尾才写记录」对齐，刷新回来不会看出两套时间。
- **验证**（stub 掉 `window.fetch` 驱动真实 UI，不建沙箱、不耗模型 token）：POST 延迟 3003ms，气泡在 60ms 采样点就已在 DOM 里（比 POST 返回早 2676ms），输入框与发送按钮点击后立即禁用，第二次点 `.send` 的请求数仍是 1（没发出去，自然不会 409）。409 两条分支各跑一遍：
  - `running: true` → 消息数 1→2（接回那一轮的占位），发出 1 次 `/chat/{id}/stream?after=0`，**无 alert**，输入框还原成原文。
  - `running: false` → 消息数 2→2（乐观渲染的两条摘干净、未接回），**无** `/stream` 请求，alert 里是服务端原句「会话已失效（destroyed），请新建会话。」。
  - `npm --prefix frontend run build` 通过。

#### 配色定稿：冷调三层底 + 待办块做全页唯一的暖色点

- **动机**：整页此前只有 `#fff` 与 `--bg` 两档底，正文块、待办块、代码块全糊在一起，看不出层次；待办块本身还是蓝底，和右侧计划面板抢同一个品牌色。
- **改动**（只动 `frontend/src/style.css`）：
  - 三层冷调底：`--page-bg: #eef4fe`（消息区）/ `--panel-bg: #e9f0fd`（侧栏 + 计划面板，压深一档）/ `--bubble: #eaecf1`（正文块与用户气泡，再深一档）。
  - 待办块 `--todos: #fbdae5`；块内状态圈改用新变量 `--todo-mark: #c2456b`，品牌蓝 `--accent` 不动（发送按钮、计划面板状态标签仍是 `#4d6bfe`）；圈口由冷灰 `#b9c4d6` 换暖灰 `#c9b6be` —— 冷灰压在粉底上会发青。
  - 块间距 `--part-gap: 16px`（比消息间距 20px 小一档，保住「块内成组、消息间分开」的层次）；待办块内边距 `5px 14px`。
  - 内联 `code` 底改半透明 `rgba(0, 0, 0, .055)`（写死冷灰在深底上显脏）；代码块头 / `pre` / 表头统一走 `--code-bg: rgba(255, 255, 255, .62)`。
  - 退役 `--bg` 与 `--sidebar-bg`：`--bg` 原有三个消费点 —— `body` 改 `--page-bg`，`.plan-status` 与 `.step-files code` 内联补 `#fff`。
- **关键决策**：
  - 气泡走**冷灰**不走中性灰 —— 中性灰压在蓝底上会发闷发绿。
  - 状态圈**新开变量**而不是复用 `--accent`：待办块变粉后若沿用品牌蓝，发送按钮与计划状态标签会跟着变，影响面超出这次改动。
- **修掉一个真 bug**：`.ai-part.todos { margin: 0 }`（清 `ul` 默认外边距）与 `.ai-part + .ai-part` 同优先级、源序更靠后，把块间距整个盖成 **0px** —— 前一轮「10px / 20px 没啥效果」的真正原因，等于白调了一轮。补一条更高优先级的 `.ai-part + .ai-part.todos { margin-top: var(--part-gap) }` 顶回来。
- **验证**：浏览器探针读 computed style（不靠截图肉眼判断）—— 页面 `rgb(238, 244, 254)`、侧栏 `rgb(233, 240, 253)`、正文块 `rgb(234, 236, 241)`、待办块 `rgb(251, 218, 229)` + 边框 `rgba(0, 0, 0, .07)`、状态圈 `rgb(194, 69, 107)`、内联 code `rgba(0, 0, 0, .055)`、代码块头/底 `rgba(255, 255, 255, .62)`；块间距修后待办块上/下都是 16px、首个块仍是 0px。`npm run build`（vue-tsc + vite）通过。

#### 一个仓库一个活跃会话：再建会话即复用

- **动机**：同一仓库重复「新建对话」会各建一条会话，各自拉一个沙箱、各占一份工作区卷，又都去动同一个仓库；用户真正要的是「回到原来那条上接着聊」。
- **改动**：
  - `db/dao.py`：新增 `lock_repo`（按 `owner/name` 归一化后加事务级咨询锁）与 `find_active_session_by_repo`。
  - `session/manager.py`：`create_session` 改成**先拿锁、再查** —— 命中活跃会话就直接复用（`CreatedSession(session, reused=True, workspace_status=<库里那份>)`），不再落库、不再建沙箱；查不到才走原路。凭据与 `repo_url` 都取库里那份，**不回落 `.env`**，否则旧会话会突然用上后来换过的令牌。
  - `api/routes/sessions.py`：`POST /sessions` 响应加 `reused`；复用时**不踢工作区**（它可能早就是 `ready`），并把既有会话登记进 `app.state.sessions`。
  - 前端：复用命中时顶部出提示条「该仓库已有进行中的会话，已为你打开它」。
- **关键决策**：**拿锁必须在查之前** —— 顺序写反，并发两边都会查空、各建一条会话，唯一性形同虚设。锁到事务结束才释放，跨度正是「查 + 写」这一整个事务。
- **验证**：
  - 新增 `tests/test_session_reuse.py`：新建落一行；同仓库第二个会话复用（id / 凭据 / 工作区状态都取库里那份，且不再落行）；`.git`、末尾斜杠、大小写换个写法绕不开复用；并发建同一仓库只落一行、且恰好一个 `reused=False`；路由层复用时不调 `start_provisioning`。假库里断言存的是占位符而不是真令牌 —— pytest 失败会打 locals，令牌不许进任何输出。
  - 重启后端跑真实链路：日志 3 行「会话已落库」+ 3 行「直接复用」，与预期的新建/复用路径对得上。
  - 全量 `pytest` 398 条通过，前端 `npm run build` 通过。

#### 意图：否定语境下的口令不算口令

- **动机**：实测样本「请给这个仓库出一个增加 GET /hello 接口的方案，先不要动手改代码。」被判成 `coding`。根因**不在模型** —— `match_strong` 命中了强口令 `动手改` 与 `改代码`，两个都在**否定句**里，于是 `source=keyword` 当场定案，qwen3-8b 压根没被调用过。把同一句直接喂给模型，判的是 `plan`。
- **改动**：
  - `server/intent.py` 新增 `_hit(text, word)`：逐个 `find` 出现位置，只有**前面 4 个字里不含否定词**的那一次才算命中。`match_strong` 与回落的 `classify()` 都改走它 —— 两份口径共用同一份词表，守卫必须同时管住。否定词表 8 个：不 / 无 / 别 / 勿 / 甭 / 毋 / 莫 / 免。
  - 同文件补 `_BOUNDARY` 判定要点并接进 `_system_prompt()`：plan 与 coding 的分界**看这一轮的交付物，不看句子里提到了什么功能**（「出一个…方案」是 plan，「按这个方案做」是 coding），各列常见说法。
- **关键决策**：
  - 窗口**只开 4 个字**：开大容易把「不要动 A，动手改 B」这类真指令一起吞掉。被吞掉也不可怕 —— 退回 LLM 判定本来就是自然措辞该走的那条路（词表只管口令）。
  - 提示词的 `_BOUNDARY` 与否定守卫是**两件事**：前者治「模型看见功能名词就判 coding」，后者治「词表不认否定」。只改前者治不了这个样本 —— 命中发生在关键字档，模型没被问过。
  - `weak_hints` **不动**（仍是 `word in text`）：它只当线索与兜底依据，不在这次的口径里。
- **验证**：
  - `tests/test_intent_detection.py` 新增 8 条：否定短语不命中；「不要动 A，动手改 B」仍判 coding；普通口令不受影响；`classify` 回落口径一致；被否定的口令**会交给模型**（`model.calls == 1`）。提示词侧另 3 条钉住分界规则与两组变体。
  - 活体直调本机 qwen3-8b：同一句 `来源=llm`、`实得=plan`。
  - 真实链路一轮（会话 `6cae02b2`，java_test_demo）：否定守卫**确实拦住了直通** —— 若没拦，这一轮会是 `source=keyword`、`intent=coding`；本轮本机分类模型未启动，5s 超时后回落成 `qa`。
- **遗留**：本机分类模型（`localhost:6006`）没起时，一切自然措辞都退到关键字口径，判定质量取决于那个进程 —— 要不要给它加熔断/降级提示另议。

#### 待办项带状态 + 块号：进度块看得出推进

- **动机**：`write_todos` 的每一项此前只有文字，看不出哪条在做、哪条做完了；一个回合里模型反复调它汇报进度，早先的做法还会把上一块顶掉。
- **改动**：
  - `stream.py`：`todo_items(args)` 用正则从**流式累加的 JSON 片段**里抠 `content` 与 `status` —— 这份 args 随时可能半截，不能 `json.loads`；只认已闭合的 `content`（半截的先不显示，免得最后一项逐字抖动）。status 排在 content 之后，还没流到时按 `pending` 占位，收尾的 `content-block-finish` 帧（args 已是 dict）订正成权威值。状态只认 `pending / in_progress / completed`。
  - 新增 `StreamParts`：正文块与待办块按出现先后排；待办块**每次调用新开一块**（`open_todos` 给出块下标），块内全量替换；`has_todos` 决定纯正文的回合不写 `parts`。
  - `V3Translator` 给每次 `write_todos` 编一个**整轮递增的块号**，下发 `{"type":"todos","id":N,"items":[…]}` 全量帧（与 plan 事件同一个惯用法）。
  - 前端 `App.vue` + `style.css`：按**块号定位**、整块替换（找不到才开新块）；状态位纯 CSS 画 —— 空圈 pending / 半亮圈 in_progress / 实心勾 completed。历史记录里只有字符串的老待办归一化成 `pending`。
- **关键决策**：待办块**追加不原地改写** —— 追加才看得出推进过程。`message-start` 清空「块下标」表但**不清块号**：下标按消息编号，块号是整轮编号。
- **验证**：
  - 真实一轮（会话 `6cae02b2`，1155 帧、id 1..1155 连续）：`todos` 229 帧落在**两块**上 —— 块 1 首帧 0 项（空帧即分隔符）、末帧 4 项且状态为 `in_progress + 3×pending`；块 2 末帧 4 项全 `completed`。SSE 帧与落库 `parts` 形状一致。
  - `tests/test_stream_v3.py`（24 条）覆盖半截 args、状态缺省与订正、块号递增、块追加。

#### 一轮对话脱离 HTTP 请求的生命周期（RunHub）

- **动机**：`EventSourceResponse` 收到 `http.disconnect` 会取消整个 task group，而 `service.chat()` 原来正挂在那个 group 上 —— **浏览器一刷新，这一轮就被杀掉**，收尾的落库走不到，成果与 token 一起丢。
- **改动**：
  - 新增 `server/runs.py` 的 `RunHub`：一轮跑在自己的 `asyncio.Task` 里，产出的帧按序号攒在缓冲里；HTTP 侧只是**订阅**这份缓冲，请求断了只少一个订阅者。同一会话并发两轮抛 `RunInProgress`（同一个 thread_id、同一个工作区，会互相踩）→ 路由回 409。
  - `api/routes/chat.py`：`POST /chat/{id}` 只负责启动 + 订阅；新增 `GET /chat/{id}/stream?after=N` 供刷新 / 断线后带游标接回（本轮已结束也能用，拿到的是完整重放）。SSE 的 `id:` 改由服务端编号（帧号即游标）—— 某帧解析失败被跳过时本地计数会错位。
  - `SessionDetail` 加 `running`；`destroy_session` 顺手 `runs.cancel()`；进程退出前 `shutdown()` 收掉在跑的轮次，别在后端拆池子时还去写库。
- **关键决策**：订阅者**不各持一个队列**（那要复制全部帧），共用一份缓冲 + 一个 `asyncio.Event` 当唤醒信号；清事件与查缓冲之间没有 `await`，生产者插不进来，故不会漏唤醒。
- **出界**：缓冲在**进程内存**里，多 worker 或重启即失效（那时退化成读库，已落库的还在）。上多 worker 时这份缓冲要挪到 Redis。
- **验证**：同一轮（1155 帧）实测三种游标 —— `after=0` 从 id 1 起 1155 帧（与原始流一致）；`after=80` 从 **id 81** 起 1075 帧；`after=1154` 只 1 帧（id 1155）。`tests/test_run_hub.py` 10 条覆盖启动 / 并发拒绝 / 断订不影响运行 / 取消 / 收尾。

#### 方案步骤目标收短成「一行标签」

- **动机**：`goal` 直接显示在右侧进度面板上，可它此前被当成了给 coder 的说明书。实测一份 8 步的方案 goal 135–332 字、另一份 7 步的 168–274 字，塞着类名、注解、依赖坐标与 git 命令，面板一行读不完。细节的归宿本是方案全文（`content`）与 `done_criteria` —— coder 用 `get_unexecuted_plan` 取方案时两者都拿得到，不靠 goal。
- **改动**：`prompts.py` 的 planner 段与 `storePlan` 的工具描述同时钉住「**一行短句、约 15 字以内、只写「做什么」**」，并说明 goal 的读者是用户面板、实现细节写进 content 与 `done_criteria`；`_format_plan` 回显步骤时带上 `[status]`。长度只在提示词与工具描述里约束，`_normalize_steps` 不做硬拒。
- **关键决策**：提示词与工具描述**两处口径必须一致** —— 工具描述离模型更近，只改一处模型就只听得到一半。为此专门加了断言把两边钉在一起。
- **验证**：`tests/test_prompts.py` 的 `TestPlanGoalIsALabel` 4 条钉住措辞与两处一致。活体（会话 `e3c20479`，plan 26）：5 步，goal 字数 **19 / 12 / 8 / 14 / 5** —— 「新增 ByeController 端点」「补充 /bye 集成测试」「全量构建验证通过」「同步 README 接口文档」「提交 PR」。

#### 消息加时间：今天只给时刻，跨年才补年份

- **动机**：消息流长了分不清先后、也看不出某轮是刚发的还是昨天的。
- **改动**：
  - 前端 `App.vue`：`Message` 加 `at: string`（ISO 串）。`formatTime()` 按**本地日历日**判「今天」——今天给 `HH:mm`，今年给 `MM-dd HH:mm`，跨年才 `yyyy-MM-dd HH:mm`；一律 24 小时制。历史记录取 `created_at`，实时发的那条取本地钟。
  - 前端 `App.vue`（发送路径）：用户那条在 **push 时**就盖上发送时刻；AI 那条先留空串，在 `finally` 里补上流结束时刻 —— 与后端「回合收尾才写记录」对齐，失败/中断也补得上。
  - 前端 `style.css`：`.msg` 由「一行」改成纵向堆叠（`flex-direction: column`），左右对齐交给 `align-items`；新增 `.msg-time` —— 气泡下方的灰色小字，`tabular-nums` 免得数字宽度跳动。用户气泡贴右、AI 靠左的既有观感不变。
- **关键决策**：**解析不出就不渲染**（`v-if="formatTime(m.at)"`），不占位。正在流式的 AI 那条正好落在「`at` 是空串」这个分支上，于是它天然不会有时间，直到流结束。
- **验证**：
  - 历史：会话 `fb3c8215` 的 17 条消息**每条**都带 `.msg-time`，今天的记录显示 `21:26` / `21:44` / `21:47`（`HH:mm`）。用户那条的时间与气泡**右边缘齐平**、AI 的左边缘齐平（时间是同一 flex 列的末项，跟着 `align-items` 走）。
  - 三种格式分支（桩 `/records` 响应，时区 `Asia/Shanghai` 偏移 +8）：今天 → `21:59`；同年 `2026-03-05T18:20` → `03-05 18:20`；跨年 `2025-06-01T10:00` → `2025-06-01 10:00`。
  - 实时路径（桩 SSE，流故意不关）：发送瞬间用户那条已是 `22:01`，AI 那条**整块不渲染**（无时间元素、也不占位）；关掉流后 AI 那条补上 `22:01`，`.thinking` 与 `loading` 一起清掉。
  - 构建：`vue-tsc -b && vite build` 通过。

#### 流式协议升 v3，AI 消息按「正文 / 待办」分块渲染

- **动机**：两件事绑在一起做。① `astream_events` 此前跑的是 v2 老协议，LangChain 官方对**前端消费**场景推荐 `version="v3"`：新协议把各类事件归一成 `{type, event_id, seq, method, params}`，一条事件一个方法名，消费端不用再靠一堆 `on_*` 名字去猜形状。② 前端此前只能拿到**一段拼好的正文**，模型中途用 `write_todos` 排出的待办要么揉进正文、要么根本不显示。
- **改动**：
  - `server/service.py`：消费端整体改 v3。`method == "messages"` 取正文与工具调用 —— `content-block-delta` 的 `text-delta` 是正文增量；`block-delta.fields.args` 是**累计值而非增量**，收尾的 `content-block-finish` 里 args 已是 dict，以它为准。`method == "tools"` 的 `tool-started` / `tool-finished` 里**只有前者带工具名**，所以按 `tool_call_id → name` 配一张表。`message-finish` 取 `usage`。子智能体归属看 `lc_agent_name`（主控是字符串 `"None"`；`langgraph_node` 两边都是 `model`，区分不了）。
  - `server/stream.py`：新增 `todo_contents` + `PartBuilder`，把「正文 / 待办」抽成**有顺序的块序列**。
  - `observability/run_log.py`：`AgentRunLog` 按 v3 形状解析（原先是 v2 的 `on_tool_start/end`、`on_chat_model_stream`）。
  - 落库：出现过待办的回合把块序列写进 `extra.parts`（形如 `[{"kind":"todos","items":[…]}, {"kind":"text","content":…}]`）；没写 `parts` 的老回合仍只读 `content`，前端两条路都认。
  - 前端 `App.vue`：AI 消息由「一个气泡」改成**有序块列表** —— 正文块灰底（Markdown 渲染），待办块蓝底圆点列表，按出现顺序自上而下追加。不做原地改写、不做勾选/状态 UI。工具帧照旧下发（`{"type":"tool"}`）但前端不渲染；右侧计划面板与它无关，未动。
- **关键决策**：
  - 待办块是**追加**而非原地刷新：一次 `write_todos` 对应一块，块内逐帧全量替换。参数没流完时后端会推**空数组**的帧（一轮真实回话里连出过 21 条），所以判据是「末尾已是待办块**且**这一帧不是新调用的开头那条空帧」—— 少了后半句，每条空帧都会多顶出一个空框。
  - **已知边界**：两次 `write_todos` 之间没有正文、且第二次的首帧非空时，两块会并成一块。这是拿「首帧必空」当分隔符换掉的方案（另一条路是给 SSE 帧加 block `index`），代价就是这个边界。
- **验证**（会话 `fb3c8215` 的 `/hello2` 轮，后端已跑 v3 代码）：
  - 流：3054 帧 = `ai` 2921 / `todos` 112 / `tool` 20 / `done` 1；待办轨迹 `[] → 1 条 → 2 条 → 3 条 → 4 条`，**累计语义**在真实流里成立；子智能体的正文照旧被压掉（无英文旁白）；本轮没有 `task` 帧，即没派子智能体。
  - 库：新的 assistant 记录 `extra.parts = [todos(4 条), text(6193 字)]`，`content` 6193 字；更早的记录只有 `intent`，回退路径正常。
  - 前端（浏览器实看该会话）：最后一条 AI 消息渲染成 `[待办蓝框 4 条] + [正文灰框]`，顺序与落库一致，整页只有 1 个待办框。
  - 前端（桩 SSE）：21 条空帧 + `[] → [A] → [A,B] → [A,B,C]` 只产生 **1** 个框；两次连续 `write_todos`（中间无正文）产生 **2** 个框。
  - 构建：`vue-tsc -b && vite build` 通过。
- **测试**：新增 v3 事件夹具 `tests/v3_events.py`（`FakeAgent.astream_events` 必须是**协程**、返回异步迭代器 —— 写成异步生成器会直接 `TypeError`）；`tests/test_logging_pipeline.py`、`tests/test_redaction.py` 跟着改写。全量 **362** 条通过（此前 352 通过 / 10 失败，那 10 条正是按 v2 形状写的）。

#### 取消 `inspect` 直接执行路径：这类问题一律交给智能体

- **动机**：`inspect` 是**关键字独占的直接执行**路径 —— 命中强关键字（工作区 / 目录结构 / 仓库记忆 / 记忆文件 / 列出文件 / 检查工作区 / inspect）就绕开模型，直接 `ls -la` + 读仓库记忆。两个问题：① 它只会固定那两下，「当前工作目录是什么」「有哪些项目」这类稍作展开的问法答不了；② 已经出现误命中 —— 「…是还在 PR 里待合并还是已合并到 master 了？」期望 `qa`、实得 `inspect`。而 `qa` 形态的主控本来就有 `ls` / `grep` / `read_file` 与读仓库记忆的能力，答得比固定模板好。
- **改动**：
  - `server/intent.py`：`TASK_TYPES` 删 `"inspect"`；`DIRECT_INTENTS` 由 `{sync, inspect}` 收成 `frozenset({"sync"})`；删掉 `IntentRule("inspect", strong=(…))` 整条规则（那些词随即不再口令直通，改由 LLM 判定）；`match_strong` 的注释改成只说 `sync`。
  - `server/service.py`：删 `_direct_inspect`，`_execute_direct` 去掉 inspect 分支（见 `service.py:261`，现在只剩 `sync`）；顺带清掉只被它用到的 `memory_path` 导入。
  - `agent/profiles.py` / `agent/prompts.py`：注释里的「sync / inspect」改成不含 inspect。
- **关键决策**：**不是**把 inspect 放开给 LLM 判（那是另一条路），而是整个撤掉 —— 留着这个意图名本身就是风险：`profile_for` 对未知意图兜底成**全量装配**（`create_pr` + 三个子智能体），一个残留的「inspect」会静默挂上一整套编码权限。
- **验证**（会话 `ae35cc1f`，后端已跑新代码，分类器 `localhost:6006` 返回 200）：
  - 「列出项目根目录下的文件」→ `intent=qa source=llm 理由=查看仓库文件结构或回答基础问题`，`form=qa`；智能体自己 `execute` 一次 `ls -1a /ai_coding/project/java_test_demo/`，LLM 2 次 / 工具 1 次，整轮 **12.4s**。
  - 「看看工作区有哪些文件」（改动前**确定**会口令直通 inspect 的措辞）→ `intent=qa source=llm 理由=查看仓库文件列表，回答基础问题`；智能体自己跑 `ls -la /ai_coding/` 与 `find /ai_coding/skills -type f | sort`，LLM 3 次 / 工具 2 次，整轮 **7.2s**；答出整棵工作区（project / policies / skills + 三个空目录），比原固定模板细。
  - 两轮都没有走直接执行路径；也都没有 `todos` 帧（模型这轮没调 `write_todos`）。
- **测试**：`tests/test_intent_detection.py` 新增 `TestInspectIsGoneEntirely`（4 条）：`DIRECT_INTENTS == {"sync"}`、`"inspect" not in TASK_TYPES`、旧强关键字不再短接（`看下工作区` / `列出文件` / `检查工作区` / `仓库记忆里是怎么说的` 一律 `match_strong() is None` 且 `classify() == "qa"`）、撤掉口令后要**真交模型判**（`detect_intent` 对 `看下工作区` 会调一次分类模型且 `source=llm`）。全量 **362** 条通过。

#### 意图说明改口径：qa 覆盖「查仓库现状」，review 收紧成「只做审查」

- **动机**：分类器的 LLM 那一档恢复后（此前本机 vLLM 没起，路由一直退化成纯关键字）实测发现，「展示一下最近没有关闭的 PR」被判成 `review`，理由是「查看未关闭的PR列表进行评审」。**这不只是形态名不同**：`review` 形态的主控手里有 `comment_pr`（写工具），而 `qa` 形态没有 —— 一句纯查看的请求被配上了回评 PR 的权限。根因在 `_INTENT_SPECS`：`qa` 只写着「基础问题（概念、用法、原理、环境、工具的提问）」，通篇是**知识问答**，于是「查仓库和 PR 的当前状态」既够不上 qa，落进 review 的「PR 评审」里也不奇怪。
- **改动**：`server/intent.py` 的 `_INTENT_SPECS` 改两行。
  - `qa` → 「查看仓库相关信息（文件、PR、提交、仓库记忆），或回答基础问题（概念、用法、原理、环境、工具）」。PR **不单列**，作为「仓库相关信息」里的一项，与文件、提交、仓库记忆并列。
  - `review` → 「对已有的 PR 或代码改动做审查、给结论、回评（只做审查：不改代码、不出方案）」，把「不做别的」写死在说明里。
- **关键决策**：修描述而**不是**调选项顺序 —— 靠调整列出顺序去赌分类器的偏好是投机，改口径才是治本。
- **验证**：
  - 离线 9 例（直调分类器，不启后端）：8 例命中，含本次要修的「展示一下最近没有关闭的 PR」→ `qa`（0.8s、`source=llm`）、「…是还在 PR 里待合并还是已合并」→ `qa`、「就按这个改吧」→ `coding`、「分析一下这个仓库的代码结构」→ `analysis`。
  - 唯一「差异」是「仓库记忆里对这个项目是怎么说的？」→ `inspect`：那是强关键字「仓库记忆」的**口令直通**（既有设计，走直接执行路径读记忆），不经 LLM，读到的内容一样，不是本次改动引起的。
  - **live 轮**（会话 `3da409ae`）：`意图分析：intent=qa source=llm 理由=查看仓库相关信息（PR状态）` → `form=qa`，工具序列 `get_pr_info ×2`（无 `task`、无写工具），21.0s，列出唯一一条 open PR 即 PR #1，并主动补了「master 停在初始提交、合并需人工在 Gitee 操作」。
- **注意**：分类器端点（本机 `localhost:6006`）当天**反复掉线又恢复**，量之前先 `curl http://127.0.0.1:6006/v1/models` 确认有 200 —— 端口处于 LISTENING 不代表能用。
- **测试**：`tests/test_intent_detection.py` 新增 `TestIntentSpecsDrawTheQaBoundary`（4 条）：qa 的说明必须含「查看仓库相关信息」与文件 / PR / 提交 / 仓库记忆，且仍覆盖基础问题；review 的说明必须点明「只做审查」「不改代码」；两条旧措辞不得再出现。全量 **331** 条通过（原 327）。

#### qa / analysis 补上 PR 的**读**工具（写侧仍然一个不给）

- **动机**：上一段把主控工具改按形态给之后，`qa` 与 `analysis` 只剩两个联网工具 —— 「给我看看最近没关闭的 PR」「当前代码是在 PR 里还是已经合并了」这两类很常见的问法，只能靠提交历史猜，答不准也答不出筛选口径。
- **改动**：
  - `tools/register.py`：`_FORM_TOOLS` 的 `QA_FORM` 与 `ANALYSIS_FORM` 由「只有联网工具」改为 `("get_pr_info", "list_pr_comments")`。**读**工具给、**写**工具（`create_pr` / `close_pr` / `comment_pr` / `save_repo_memory` / `storePlan`）一概不给。顺带删掉 `build_main_tools` 里已成死代码的提前返回。
  - `agent/prompts.py`：新增 `_READONLY_PR` 段并接到 `QA_AGENT_PROMPT` / `ANALYSIS_AGENT_PROMPT`（与 `_READONLY_GIT` 同一路子：不讲清「PR 能读」，模型会把「只读」理解成「PR 一律别碰」，于是连着两个工具都不敢用）。段里写明 `get_pr_info` 传仓库地址时按 `state`（open / closed / merged / all，默认 all）列 PR、传 PR 链接时读详情，`list_pr_comments` 传编号读评论，以及「不评论、不关闭、不创建、不合并」。`_ROLE_QA` 的「怎么查」补第 3 条、`_ROLE_ANALYSIS` 补一段收尾：PR 状态以 `get_pr_info` 的实际返回为准，**不要凭提交历史推测**。
  - 同一次编辑修掉一段过期指路：`_READONLY_BOUNDARY` 里的「由 planner 产出分步方案」—— plan 已是单智能体，照这句找 `planner` 会扑空。
- **关键决策**：少给工具比在提示词里劝阻有效 —— 写侧工具**看不见**，模型就没有「嘴上答应不动手、实际调了」的余地。
- **验证**（真实会话 `3da409ae`，仓库 `zzpfordevelopment/java_test_demo`，后端重启后跑）：
  - **qa 轮**（「展示一下最近没有关闭的 PR」）：`form=qa`，工具序列 `get_pr_info ×4`（**无 `task`**、无写工具），21.2s，答出唯一一条 open PR 即 PR #1，并指明「已可合并、未合并、最近更新是两条审查评论之后」。
  - **analysis 轮**（「分析一下这个仓库当前处在什么阶段：代码是还在 PR 里待合并，还是已经合并到 master 了？」）：`form=analysis`，工具序列含 `get_pr_info` 与 `list_pr_comments`（**无 `task`**、无写工具），33.9s，四条独立证据（`ls-remote` 分支指针、master 文件清单、README 行数差异、PR 实时状态）得出「仍在 PR 里待合并，master 至今是空仓库」。
- **附带发现（环境，非代码问题）**：这两轮的意图判定都是 `source=fallback 理由=TimeoutError`。单独量过分类器（绕过 5s 超时给 120s）得到 `Connection error.`，每次约 8.2s —— `INTENT_BASE_URL` 指向的本机 vLLM（`localhost:6006`，qwen3-8b）**没在运行**，所以 5s 超时是假象，调大超时也救不回来。服务没起时路由**完全退化成关键字口径**：无强关键字的自然措辞一律落 `qa`，`analysis` 只有弱关键字（分析 / 排查 / 为什么 / 原因 / 根因…），弱词不直通、只在回落档生效。**（同日晚些时候该服务已启动，分类恢复 `source=llm`：三次实测 0.2~0.8s。）**
- **测试**：`tests/test_profiles.py` 的只读形态断言改为期望两个 PR 读工具，并加一条「与写侧工具集合不相交」；`tests/test_prompts.py` 新增 `TestReadonlyFormsCanReadPrs`（5 条：两个读工具被点名、`state` 取值被写明、写侧仍写明禁止、「不要凭提交历史推测」在位、过期指路已消失）。全量 **327** 条通过（原 322）。

### 2026-09-17

#### 装配形态合一轴：`form` 一个字段定工具 / 子智能体 / 提示词 / 缓存

- **动机**：此前「只读与否」与「任务类型」是两个各管一摊的字段，能各自漂移（例如只读表改了、提示词没跟上）。现在只剩一个轴 —— `form`，它同时决定：① 挂哪些子智能体；② 主控拿哪些工具；③ 用哪份角色提示词；④ 每会话的智能体缓存键。
- **改动**：
  - `agent/profiles.py`：`AgentProfile` 只留 `form` 与 `subagents` 两个字段（`key` / `readonly` 删除）；`_PROFILES` 以**意图名**为键（与 `server/intent.py` 的 `TASK_TYPES` 同名），`coding` → 形态 `orchestrate`；新增 `PROFILED_INTENTS` 与 `profile_for(intent)`（未知意图回落最全的 `coding`）。
  - `tools/register.py`：新增 `_FORM_TOOLS` 表与 `build_main_tools(..., form=...)`。主控工具按形态给：`qa` / `analysis` 只有两个联网工具（写侧一个不给；**次日改成另给两个 PR 读工具**，见 2026-09-18 那条）；`plan` 给 `get_pr_info` + `save_repo_memory` + `storePlan`；`review` 给 `get_pr_info` + `list_pr_comments` + `comment_pr`；`orchestrate` 给全部 7 个。
  - `session/manager.py`：`SessionRuntime.agents` 的缓存键由「提示词」改成 `profile.form`；装配时把 `form` 传下去；构建日志带上 `form=`。
- **关键决策**：`storePlan` 原本只挂在 `planner` 子智能体上。`plan` 改成单智能体后，若不把它注入主控，**方案面板与 `get_unexecuted_plan` 会同时断**（一个没人写、一个取不到）。故 `plan` 形态的主控工具里补上 `storePlan`，以及与它同属一个流程的 `save_repo_memory`。

#### plan / review 改单智能体；coding 增「直通」分支

- **改动**：
  - `plan` 与 `review` **不挂子智能体**：主控自己出方案、自己审。deepagents 在无子智能体时 `task` 工具**真的会消失**（不是留着空转），已有测试钉住这一点。
  - `agent/prompts.py`：主控提示词按形态各写一份（`QA_AGENT_PROMPT` / `ANALYSIS_AGENT_PROMPT` / `PLAN_AGENT_PROMPT` / `REVIEW_AGENT_PROMPT` / `MAIN_AGENT_PROMPT`）。plan / review 的主控与对应子智能体**共用同一批方法段**（`_PLAN_WORKDIR` / `_PLAN_METHOD` / `_PLAN_RULES`、`_REVIEW_WORKDIR` / `_REVIEW_METHOD`），保证「谁来做都是同一套做法」。plan / review 的主控仍用带 git 小节的 `BASE_PROMPT`（方案的末步是「提交 PR」、审查要读 diff 与分支状态，少了这一段讲不了）。
  - `coding` 增加分支判断，写在 `_INTENT_DIRECTIVES["coding"]` 里 —— 智能体按 `(会话, 形态)` 长期缓存，意图指令是唯一能逐轮注入的地方：
    - 用户这轮的话里**带直接执行的意思**（「实施」「动手改」「开始改」「按这个方案做」「就按这个改吧」「直接做」「确认实施」）→ **跳过 `planner`**，直接派 `coder`，它用 `get_unexecuted_plan` 取上一轮那份未执行方案来实施，一路做到提 PR，随后派 `reviewer`。
    - 话里**没有**这层意思 → 先派 `planner` 出方案并呈给用户，**停下等「确认实施」**，再派 `coder` 实施，最后派 `reviewer`。
    - coder 取不到可执行的未执行方案（本来没有方案、或方案已执行过）→ **停下回报主控**，主控改派 `planner`，回到「等确认」那条路；不让 coder 硬做。
  - 记忆回写门槛（`_MEMORY_GATE`）表述收紧为「**第 0 步**」：先 `get_pr_info` 查未合并 PR → 再 `save_repo_memory` → 才出方案 / 派发子智能体；未完成不得出方案，也不得派 `planner` / `coder`。
- **关键决策**：`save_repo_memory` 归**主控**持有（`coding` 与 `plan` 两种形态都有），`review` 去掉，三个子智能体从来不带。理由是链路上的先后关系：只要进编码就一定要读方案，而读方案之前必须先把记忆回写掉 —— 于是不管这轮是出方案还是直通实施，回写都发生在主控派发之前，后续谁读到的都是本轮刚刷新的版本。
- **验证**（真实会话 `3da409ae`，仓库 `zzpfordevelopment/java_test_demo`，跑完整三轮）：
  - **plan 轮**：`intent=plan source=keyword`、`form=plan`，工具序列里**没有 `task`**，顺序为 `get_pr_info → … → save_repo_memory → storePlan`（门槛顺序正确），73s，落库 `plan_id=25`（6 步），前端方案面板可见。
  - **coding 直通轮**（用户发「确认实施」）：`intent=coding`、`form=orchestrate`，**整轮只出现两次 `task`：`coder` 与 `reviewer`，没有 `planner`**；coder 第 4 个动作即 `get_unexecuted_plan` 取到 plan 25，随后逐条 `updatePlanStep` 推进，`mvn test` 全绿（1/1）、`mvn package` 出 jar、容器内真机 `curl` 得 200 + `{"status":"ok"}`，`create_pr` 建成 PR #1，coder 耗时 174.5s / 整轮 349s；`reviewer` 只读审查后 `comment_pr` 评论到 PR（P0 0 / P1 0 / P2 2 / P3 7）；主控把结论原样呈给用户并以「哪些需要修改（未提及的我不动）」停下等待。
  - **review 轮**（同会话同 thread，用户发「审查一下 PR #1」）：`intent=review`、`form=review`（缓存未命中、新建了一套装配，thread 不变所以历史连续），**无 `task`**；主控自己读 8 个改动文件、逐条 grep `policies/java-style.md` 核对，并在副本里独立复跑 `mvn -o test` / 打包 / jar 启动 curl / 边界探针（405 / 404 / 406），自己 `write_file` 报告到 `/ai_coding/review/`，自己 `comment_pr`（P2 2 / P3 9）—— 全程未改 `/ai_coding/project/` 下任何文件，162s，28 次工具调用。
- **测试**：`tests/test_profiles.py` 补主控工具子集（五种形态各一份断言）、形态互异性、「单智能体轮次里没有 `task`」等；`tests/test_prompts.py` 补 plan / review 单智能体轮次与 coding 双分支（含取不到方案的回退）断言。全量 **322** 条通过（原 262）。

### 2026-09-05 ~ 2026-09-17（补记）

- **说明**：这一段是事后补记。项目不是 git 仓库，无法把改动还原到具体某一天，故合并成一段（条目仍按时间倒序：最新在最上）。下方原有条目**保持原样**，不改写历史；其中「agent 层：模型参数（禁用思考 + 温度）」里 `llm.py` 的参数说法已被本段第一条取代。

#### 移除思考结果通道（SSE / 落库 / 前端）+ `llm.py` 迁 `init_chat_model`

- **改动**：
  - `server/service.py`：`_split_chunk()` 改为 `_chunk_text()` —— `content` 是字符串就直接用，是内容块列表就只拼文本块；**推理类型块**（`reasoning` / `thinking` / `reasoning_content`）与 `additional_kwargs.reasoning_content` 一律丢弃。不再单独推推理事件，`summary` 与 `content` 只留正文。
  - 前端：`Message` 去掉 `reasoning` 字段，SSE 事件联合类型去掉 `'reasoning'`（现为 `'ai' | 'tool' | 'plan' | 'done' | 'error'`），`.think*` 一组样式删除，历史记录只还原 `role` / `content`。
  - `agent/llm.py`：`build_model` 改用 `init_chat_model(model_provider="openai")`（主 / 备），`temperature` 1.1 → **0.3**，**不传** `thinking`。
  - 删除 `ReasoningChatOpenAI` 子类与 `TestReasoningContent` 类（相关测试同步删除）。
- **关键决策**：
  - 不再传 `extra_body={"thinking": {"type": "disabled"}}` —— 实测不带它也返回 `reasoning_content`，而该键不在 OpenAI 规范内，传了只是多依赖一条未验证的接口契约。
  - 思考结果当初打算在聊天里展示，后确认**不需要**，因此整条通道**去掉**（而不是隐藏）：少一条链路、少一份落库体积，也不会再有「推理块夹带正文」这类坑。
- **验证**：全量 pytest **262** 条通过；前端 `vue-tsc` + `vite build` 干净；真实会话流内除 `ai` 外无文本帧、页面 0 个 `.think` 元素；日志完成行由「…输出 181 字 / 推理 595 字」变为不再带推理字数。

#### 意图分类：强/弱关键字 + LLM 判定，分类模型关思考

- **改动**：`server/intent.py` 关键词分「强 / 弱」两档并支持排除词，未命中则调 LLM 判定；`llm.py` 新增 `build_classifier_model()`（独立必配 `INTENT_MODEL*`，温度 0、`max_tokens=512`、不挂工具）；`classify` 回传 `source=llm|keyword|fallback` 与判定理由，进日志与 `session_records.extra.intent`。
- **关键坑（vLLM 方言）**：关思考**必须**写 `extra_body={"chat_template_kwargs": {"enable_thinking": False}}`。顶层未知字段（含 DeepSeek 那套 `thinking`）会被 vLLM **静默忽略**，qwen3-8b 照旧把 `<think>…</think>` 内联写进正文 —— 首行不是意图词，`parse_reply` 必然失败，还白占 token（服务端 `max_model_len=1024`，抬 `max_tokens` 会直接 400），且拿满 5s 超时。
- **验证**：换键后实测 2.8~5.0s → 0.3s，解析直接命中；三次真实判定 `plan`/560ms、`coding`/312ms、`analysis`/396ms 均正确；关键字快路径（如「开始实施」）不调模型。
- **收尾**：删掉死配置 `intent_thinking`（连同 `.env.example` 的 `INTENT_THINKING`）；`build_classifier_model` 的 docstring、`config.classifier_model` 的 docstring 与 `INTENT_*` 段注释一并改写 —— 去掉「1.18s → 0.83s / 靠 `reasoning_content` 分通道 / 前端要展示思考过程」等旧说法，以及「三项留空逐项回落主模型」的错说法（实际不回落，未配置直接报错）。

#### 代码工作区状态（后台建 + 前端状态条）

- **改动**：`sessions` 加 `workspace_status` / `workspace_error` 两列（迁移 `b41d7e2c9f30`）+ dao `set_workspace_status`；`session/manager.py` 把「建会话」与「建代码工作区」拆开 —— 工作区放后台任务（`_on_build_done` 回调收尾）并新增 `ensure_runtime()`；`sandbox/provider.py` 支持**按 id 终止**沙箱；`service` 改为**先查状态再取运行时**；API 回执与 `SessionDetail` 带 `workspace_status`；前端加动态状态条。
- **关键决策**：建工作区约 10s，接口立即返回、前端按状态显示进度，不让创建会话的请求阻塞在那儿。
- **验证**：`tests/test_workspace_runtime.py` 覆盖「建成 / 重连失败回落新建 / 失败落 `failed` 并记 `workspace_error`」；前端构建通过。

#### 凭据脱敏层（令牌不落输出）

- **动机**：红线是「令牌不落输出」，而此前**只有提示词在防**（「别读 `.secrets`」）。模型一旦 `cat` 了 `/ai_coding/.secrets/gitee_token`、跑 `git remote -v` 或打印环境变量，令牌会流向四条出口：① 工具返回值 → ToolMessage → **Postgres checkpoint**（下次会话读回还会再喂给模型）；② 同一份文本 → `logs/agent.log`（工具**入参**也记）；③ SSE → 前端；④ 不经智能体的**直接执行路径**（`sync` / `inspect`）stdout 直接下发 + 落库。全部无人拦截。
- **改动**：
  - 新增 `redact.py`（纯函数，无状态可单测）：**值匹配**（会话级 Gitee 令牌 + settings 兜底令牌）+ **锚定上下文模式** —— 已知凭据前缀（`ghp_` / `github_pat_` / `glpat-` / `sk-` / `xox[bp]-` / `AKIA` / `LTAI` / `AIza`）、`Bearer <token>`、URL 用户信息（只打码口令段，保留用户名与主机）、赋值上下文 `token|secret|password|api_key = "…"`。
  - `agent/middleware/redaction.py` 具名中间件：`ToolMessage` 分支改写 `content`（`str` 与内容块列表两种形态都处理，用 `model_copy` **保住 `tool_call_id`**，丢了无法与 AI 的 tool call 配对）；`Command` 分支用 `dataclasses.replace` **保住 `goto` / `graph` / `resume`**；无命中时原样返回同一对象。
  - **挂载点**：主智能体 + **每个子智能体**（`SubAgent` 有独立 `middleware` 字段，主智能体的**不下传**）—— 读写沙箱的正是 coder / reviewer，漏掉它们等于没做。
  - `observability/run_log.py` 新增 `_safe()`：**先脱敏再截断**（顺序反了会把跨截断点的令牌留半截），覆盖委派描述 / 工具入参 / 工具返回三处。
  - `server/service.py` 四处出口：SSE 正文、SSE 工具帧、业务库记录与 `summary`、直接执行返回值与异常文本。
- **关键决策**：
  - **不复用** `langchain` 自带的 `PIIMiddleware`（已查源码，非疏漏）：它挂在 `before_model` / `after_model` 上，改写的是「送给模型的消息」，**没有 `wrap_tool_call`** —— 原始工具返回值照样进 state 与 checkpoint，正是要堵的第一条；且它管不到日志落盘与直接执行路径，内置检测器（email / 信用卡 / IP / MAC / URL）套在编码场景还会把仓库 URL 全打码。
  - 脱敏中间件实际落在 `FilesystemMiddleware` **内侧**（默认栈排在前面、我方传入的追加在后，而列表第一个才最外层）。这反而正合需要：原始输出先被打码，外层转存 `/large_tool_results/` 的内容也是脱敏后的。
  - **明确不打码**裸高熵长串与 40 位 hex —— 不加白名单机制就天然免疫 git SHA / sha256，否则 diff 与测试输出会被打花。赋值上下文的右侧也只认字面量形态，`api_key = os.getenv("X")` 这类代码不被误伤。
  - **不做**：流式分片跨块的令牌（工具结果进 state 前已打码，模型根本没见过令牌，不会回吐）；用户输入不打码（那是用户自己的输入）；不加开关配置项；不加 `/ai_coding/.secrets/**` 路径级拒绝（打码已足够）；提示词不动（「别读 `.secrets`」保留为前置劝阻）。
- **验证**：新增 `tests/test_redaction.py` —— 值匹配命中、四类模式各命中、**不误伤**（40 位 hex / SHA、`os.getenv("X")`、普通代码与 diff 原样返回）、空词表不报错；中间件两分支脱敏且 `tool_call_id` 保留、无命中返回原对象；`AgentRunLog` 三处；service 的 SSE / 落库 / 直接执行 / error 分支。**活体验证只查误伤面**（跑一轮真实对话确认正常输出没被打码）—— 真阳性路径一律用**合成令牌**在单测里覆盖，不拿沙箱真令牌去试（那正是会泄露的那条路）。

#### 方案面板（plans 表 → 工具 → 事件 → 右侧面板）

- **改动**：`plan.py` 加 `_normalize_steps` 与 `storePlan` 硬约束；`dao.set_plan_step_status` + `updatePlanStep` 工具；`dao.get_latest_plan` + `GET /sessions/{id}/plan`；`service` 在 `updatePlanStep` 之后读库推 `plan` 事件（同一状态**去重**只发一帧，读库失败不打断事件流）。
- **验证**：`tests/test_plan_events.py` 覆盖字段映射、脏步骤跳过、非数组 `steps` 归一、事件去重、只对方案类工具触发、读库异常不影响流；前端右侧固定面板按 `steps` 渲染状态。

#### PR 流程调整（回评 + 两个方向的工具增减）

- **改动**：新增 `list_pr_comments` 工具；`create_pr` 改为**先查后建**（已有 open PR 则复用，不重复开）；删除 `merge_pr` 工具链（合并交回人工）；`get_pr_info` 归主智能体并支持传仓库 / PR 链接；`prompts.py` 调整 PR 相关约束（PR 为末步、用户点单、回评）。
- **验证**：各工具名与参数 schema 正确；未配置令牌时返回友好错误；真实 PAT 端到端走通。

#### 仓库记忆写入去工具化

- **改动**：新增 `save_repo_memory` 工具（写 `/memories/<repo>.md`）；主智能体工具接线 backend；`prompts.py` 把「记忆写入与合并」从工具编排改回提示词约束。
- **验证**：全量 pytest；会话中确认记忆文件写入、并在后续会话可被读回。

#### 沙箱：资产注入 + 可用性代理 + skill 同步

- **资产注入**：`policies` 补齐 + `deploy/sandbox` 脚本；`config.py` 增加资产注入配置；`sandbox/provider.py` 建会话时注入；`session/manager.py` 注入 askpass 环境变量；`.env` / `.env.example` 切原生镜像；文档与提示词同步纠正。
- **可用性代理**：新增 `sandbox/proxy.py` 的 `OpenSandboxProxy` —— 沙箱是不可靠资源（虚拟机重启、容器被清），代理负责探活与**按状态码白名单恢复**（仅 404 / 410 / 502 / 503 才重建，其余原样抛出）；`SandboxClient` / `SandboxProvider` 支持探活与重连；接到 backend 与会话管理；`tests` 用假沙箱覆盖。
- **skill 同步**：skill / policies / askpass 集中在仓库 `sandbox/` 下，**建会话时整棵上传** —— 改仓库即生效，无需重建镜像（此前靠镜像 COPY 到 `/ai_coding/skills/`，改一次就得重打镜像）。
- **关键决策**：代理只对**可安全重建**的状态码动手，其余故障不吞成重建，避免掩盖真实问题。
- **验证**：代理单测覆盖白名单内外两类状态码；一轮真实对话确认中间件从沙箱加载到新 skill。

#### 前端：Markdown 渲染 + 代码高亮 + 复制

- **改动**：接入 `marked` + `dompurify` + `highlight.js`；新增 `markdown.ts`（渲染 + 消毒）与 `highlight.ts`；流式更新改**节流缓冲**（每来一个分片就整篇重渲染太费）；代码块复制用事件委托；`style.css` 补代码块与按钮样式。
- **验证**：构建通过；XSS 探针（`<script>` / `onerror`）被消毒；流式 token 逐个显示无卡顿；复制按钮可用。

#### 前端：会话标题与侧栏

- **改动**：首条用户消息写入 `sessions.title`；对已有会话做一次性**回填**；侧栏显示首条消息摘要 + 仓库地址入框；用户消息贴右、AI 头像改 `aicoding`。
- **验证**：重启后端 + 浏览器实际验证列表显示与回填结果。

#### 中间件改写为具名类 + 运行时缓存修复

- **改动**：`agent/middleware/`（`context_inject.py` 意图注入 / `redaction.py` 脱敏）改为**继承 `AgentMiddleware` 的具名类**，不再用 `@wrap_model_call` / `@wrap_tool_call` 装饰器；装配点改为传**实例**；修 `session/manager.py` 的 `_on_build_done`，让后台建好的运行时真正进缓存。
- **关键决策**：具名类在装配点显式传实例更清楚，单测可直接实例化，也避开装饰器在子智能体链路里的作用域歧义。
- **验证**：全量 pytest；真实会话确认意图注入与脱敏均生效。

#### skill 与 references 整理

- **改动**：改写 `sandbox/skills/code-review/SKILL.md`，新增 `references/` 四份中文清单（`code-quality-checklist.md` / `removal-plan.md` / `security-checklist.md` / `solid-checklist.md`）；删除 `code-review-expert` 目录。
- **验证**：目录结构与 skill 加载正常；上传到沙箱后可被中间件读到。

### 2026-09-04

#### Server 层：意图分析 + 直接执行 + 记录

- **位置**：新增 `src/ai_coding_harness/server/`（api 与 agent 之间的业务层，API 路由只做 HTTP 转换）。
- **意图分析**（`server/intent.py`）：
  - 任务类型（新架构命名）：`coding / review / plan / qa / analysis / inspect / sync`。
  - `classify(message)` 关键字匹配（按规则顺序，先匹配先胜，默认 `qa`）；`DIRECT_INTENTS = {sync, inspect}`。
- **服务层**（`server/service.py`）：`HarnessService.chat(session, message)` 产出事件流：
  1. 分类意图 → 记录 `session_records`（type=user，extra.intent）；
  2. **直接执行**：`sync`（clone/pull）、`inspect`（列工作区 + 读仓库记忆）不经智能体，直接跑沙箱返回；
  3. 其余 → 调主智能体，**intent 经 `configurable.intent` 注入**；智能体调用用 **`astream_events(version="v2")`** 事件流（`on_chat_model_stream` 出 token、`on_tool_start/end` 出工具），config 用 `RunnableConfig` 对象；
  4. 回复记录到 `session_records`（type=assistant）。
- **接入**：`api/routes/chat.py` 改走 `app.state.service`；`api/app.py` 装配 `HarnessService`；`tools/context.py` 加 `get_intent()`。
- **验证**（端到端）：`同步一下仓库` → 直接 `git clone`；`看看工作区有哪些文件` → 直接列工作区+记忆；`你好…` → 走主智能体；`session_records` 落库 6 条（user/assistant × 3，带 intent，sync/inspect 标 direct）。

#### 可观测：接口日志 / 智能体日志双流 + trace_id

- **两条日志流**（按 logger 名前缀路由，各自滚动文件）：
  - `logs/api.log` —— 接口日志：`ai_coding_harness.api.*`、`uvicorn.access`；
  - `logs/agent.log` —— 智能体运行日志：`ai_coding_harness.{agent,server,session,sandbox,backends,tools,memory,safety}.*` + `opensandbox`/`httpx`/`langchain`/`langgraph`/`deepagents`；
  - 控制台：输出全部。
- **trace_id**：`observability/context.py`（ContextVar）+ 日志 `Filter` 注入；**接口中间件**（`api/middleware.py`，**纯 ASGI**，避免影响 SSE）为每个请求生成 trace_id，并记录 `→/← 方法 路径 状态 耗时 客户端`。
- **细粒度智能体日志**（`server/service.py`）：派发、**每次工具调用**（名称/参数摘要/耗时）、**每次 LLM 调用**（token 用量）、完成汇总（LLM/工具次数、输出字数）。
- **配置**：`LOG_API_FILE` / `LOG_AGENT_FILE`（替换原 `LOG_FILE`）。
- **验证**：起后端 + 调 `/health` 与 `/chat` → `api.log`（`[trace] → POST /chat …`、`← … 200 9461ms`）与 `agent.log`（`LLM 调用完成：输入 18111 / 输出 69 tokens`、`智能体完成：LLM 1 次 / 工具 0 次`）**同一 trace_id** 贯穿两文件。

#### 可观测：日志改为「控制台 + 滚动文件」

- **改动**：`observability/logging.py` 的 `setup_logging(settings)` 配置根日志为**双通道**：
  - 控制台：`StreamHandler(stdout)`；
  - 文件：`RotatingFileHandler`（按大小滚动，`encoding="utf-8"`）；
  - **uvicorn 自带 handler 移除、改为冒泡到根** → uvicorn / access 日志并入同一套输出（控制台与文件）。
- **配置**：新增 `LOG_LEVEL` / `LOG_DIR` / `LOG_FILE` / `LOG_MAX_BYTES` / `LOG_BACKUP_COUNT`（`.env.example` 同步；`logs/` 加入 `.gitignore`）。
- **验证**：起后端后 `logs/harness.log` 生成，含 `Application startup complete` / `Uvicorn running` / access 日志（`GET /health`、`GET /sessions`）；格式 `时间 [级别] logger: 消息`；控制台输出与文件一致。

#### 修复：重启后旧会话 `/chat` 报 404（会话重建）

- **现象**：调用 `/chat/{id}` 返回 404（如 `a72ebc3e-...`）。
- **根因**：`/chat` 从**内存注册表** `app.state.sessions` 取会话；该表只存**本进程创建**的会话，**后端一重启就空**。而会话列表来自 **DB**（持久化）→ 界面能列出旧会话、点进去发消息就 404（库里该会话其实是 `active`）。
- **修复（方案 C：先重建、失败标失效）**：
  - `SessionManager.rehydrate(...)`：用库里存的 `repo_url`/`gitee_token`/`gitee_username` **重建沙箱 + 混合后端**；卷名由 `public_id` 决定 → **复用同一工作区卷**（克隆的仓库/未提交改动仍在），回填 `sandbox_id`。
  - `/chat` 路由：未命中内存 → 查库；`active` 则重建并注册；**重建失败**则把该会话标记 `expired` 并返回 409（明确提示新建会话）；库里没有 → 404；非 active → 409。
  - `DELETE /sessions/{id}`：不在内存时也能清理（仅标记库状态 `destroyed`）。
- **验证**：重启后端（内存清空）后对 `a72ebc3e-...` 调 `/chat` → **正常流式返回**（非 404）；库里 `sandbox_id` 更新为新沙箱、**卷名不变**（工作区复用）。

#### 启动脚本：加端口占用检查

- **改动**：`start.sh` / `start.bat` 启动前检查后端(8000)/前端(5173)端口；**被占用则打印占用 PID 并询问是否结束**（y 结束并继续，其余取消启动）。
- **验证**：`start.sh` 实测 —— 端口被占用时提示「端口 8000（后端）已被占用：PID 11624」，输入 `n` 后「已取消启动」并退出；`bash -n` 语法通过；`start.bat` 仍为 GBK 编码（含新增内容）。

#### 前端：会话列表加「复制仓库地址」按钮

- **后端**：`SessionListItem` 加 `repo_url`（取 `repositories.url`）。
- **前端**：每个会话项右侧加「复制」按钮（悬停显示，与「删除」并列）；点击用 `navigator.clipboard.writeText` 复制仓库地址（无 clipboard API 时降级 `execCommand`），按钮短暂显示「已复制」。
- **验证**：`/sessions` 返回 `repo_url`；浏览器实测 13 个会话均有按钮，点击后标签变「已复制」。

#### 修复：start.bat 中文乱码

- **现象**：运行 `start.bat` 时 echo 的中文显示为乱码。
- **根因**：`.bat` 存成了 **UTF-8**，而 cmd 用**系统代码页（中文 Windows 为 GBK/936）**逐行读取批处理文件 → UTF-8 的中文字节被按 GBK 解码 → 乱码。（此前加的 `chcp 65001` 也救不了：cmd 中途切换代码页有已知解析问题。）
- **修复**：把 `start.bat` 改为 **GBK 编码（无 BOM、CRLF）**，并把 `chcp 65001 >nul` 换成 **`chcp 936 >nul`**（与文件编码一致，确定性输出）。
- **验证**：回读校验 —— 无 BOM、UTF-8 无法解码（确为 GBK）、含「一键启动/启动后端/启动前端/打开浏览器/完成…」等中文、行尾为 CRLF。

#### 修复：后端起不来（zai 包冲突）+ web_search 懒加载

- **现象**：`python -m ai_coding_harness.main` 启动即报 `ImportError: cannot import name 'ZhipuAiClient' from 'zai'`。
- **根因**：`pyproject.toml` 的依赖**把发行名写成了导入名** —— 写的是 `zai`（PyPI 上是空占位包 0.0.2），而真 SDK 的发行名是 **`zai-sdk`**（`import zai`）。两者共用 `zai/` 目录，占位包的**空 `__init__.py` 覆盖了真 SDK 的导出**。README 的 Setup 步骤 `pip install -e ".[dev]"` 会按 pyproject 装依赖 → 装上占位包 → 覆盖 → 崩。而 `tools/web_search.py` 在**模块顶层**导入它 → 整条导入链崩 → 后端起不来。
- **修复环境**：`pip uninstall -y zai`（卸占位包）+ `pip install --force-reinstall --no-deps zai-sdk`（还原真 SDK 文件）。
- **修复根因**：`pyproject.toml` 依赖 `zai` → **`zai-sdk`**（`--dry-run` 验证：现在只装 `zai-sdk`）。
- **修复代码**：`tools/web_search.py` 把 `zai` 导入改为**懒加载**（在工具函数体内导入，缺失时返回友好错误）—— 避免可选 SDK 缺失/损坏拖垮整个后端启动。
- **验证**：`from zai import ZhipuAiClient` 恢复；后端启动成功（`Application startup complete`）。

#### 前端流式渲染修复（token 逐个显示）

- **现象**：回复不是逐个 token 显示，而是**结束后一次性出现**。
- **定位**（实测各层 SSE 到达时序）：直连后端 **逐 token**（~50ms/块）；经 Vite 代理 **也逐 token** → 传输层无问题；**问题在前端**。
- **根因**：`App.vue` 的 `send()` 把 assistant 消息存成**局部普通对象**再 push 进数组，后续 `reply.content += ...` 改的是**原始对象**而非 Vue 的响应式代理 → **不触发重渲染**，直到 `loading=false` 才一次性刷新。
- **修复**：改为按**索引**改数组元素（`messages.value[idx].content += ...`），走响应式代理触发更新。
- **验证**：短信流式采样（每 0.8s）文本长度 `1 → 23 → 145 → 152`，确认增量渲染；`vite build` 通过。

#### 意图分类：支持词 + 排除词

- **改动**：`server/intent.py` 的 `classify` 规则改为 `IntentRule(intent, keywords, excludes)` —— 命中任一**排除词**则该规则不成立，否则命中任一**支持词**判为该意图，全不命中默认 `qa`。
- **加的排除词**（针对易混场景）：
  - sync：排除「同步机制 / 同步锁 / 同步原理 / 数据同步」（并发话题属问答）。
  - plan：排除「设计模式」（知识话题）。
  - coding：排除「怎么实现 / 如何实现 / 实现原理 / 功能介绍 / 功能是什么 / 功能有哪些」（是在问，不是让动手写）。
- **验证**：16/16 用例通过，含排除词场景（"讲讲 Python 的同步机制"→qa、"怎么实现一个登录功能？"→qa、"介绍下设计模式"→qa、"这个功能有哪些参数"→qa）。

#### 启动脚本（一键起前后端）

- **改动**：新增 `start.bat`（Windows，双击可运行：分别在新窗口起后端/前端并打开浏览器）与 `start.sh`（Git Bash / Linux：后台起后端、等就绪后前台起前端，退出时 trap 停掉后端）。
- **配置**：venv 路径 / 端口 / 前端地址在脚本顶部可改，也支持环境变量覆盖。
- **验证**：`start.sh` 实测 —— 后端就绪（health 200）→ 前端 Vite 起在 5173（HTTP 200）。

#### 前端：DeepSeek 风格聊天页（Vite + Vue3）

- **后端**：新增 `GET /sessions`（会话列表，带仓库）、`GET /sessions/{id}/records`（时间线，记录+详情）；DAO 加 `list_sessions_with_repo` / `list_records_with_detail`；schemas 加 `SessionListItem` / `RecordItem`。
- **前端**：`frontend/`（Vite + Vue3 + TS）。
  - 布局仿 DeepSeek：左侧栏（品牌 + 新对话 + 历史会话列表）、主区（欢迎页 / 消息 / 底部输入）。
  - 新建对话需输入 `repo_url`；选中会话加载历史；发送用 **POST + 手写 SSE 解析**（EventSource 不支持 POST）。
  - `vite.config.ts` 把 `/sessions` `/chat` `/health` `/auth` 代理到 FastAPI（免 CORS）。
- **踩到两个坑**：① uvicorn 0.36+ 在 Windows 默认用 ProactorEventLoop（忽略事件循环策略），psycopg 异步报错 → `main.py` 用 `loop="ai_coding_harness.main:selector_loop_factory"` 显式指定 SelectorEventLoop；② sse-starlette 用 `\r\n` 换行 → 前端按 `\n\n` 切块解析不到事件 → 改用 `/\r?\n\r?\n/` 兼容。
- **验证**：`vue-tsc && vite build` 通过；浏览器端到端跑通（会话列表、新建对话、选中加载历史、发消息流式渲染回复）。

#### 智能体架构：一主三从

- **结构**：主智能体（唯一入口，协调 + 直接处理）+ 三个子智能体 `planner` / `coder` / `reviewer`（经 `task` 工具派发）。
- **改动**：
  - 新增 `agent/subagents.py`（`build_subagents(planner_tools, coder_tools, reviewer_tools)`）。
  - `agent/graph.py`：`build_agent` 支持 `subagents`；注册 harness profile 关闭 deepagents 默认自动添加的 `general-purpose` 子智能体（固定一主三从）。
  - `tools/register.py`：改为 `build_main_tools` / `build_subagent_tools` 按智能体分配工具。
  - `session/manager.py`：每会话建**一个主**智能体（去掉 multiton 按类型缓存）；`thread_id` = `public_id`。
  - `api/routes/chat.py` + `api/schemas.py`：**去掉 `task_type`**，chat 直接交给主智能体。
  - `prompts.py`：移除旧的 `CODING_PROMPT` / `READ_ONLY_PROMPTS` / `TASK_TYPES` / `get_task_prompt`，新增 `build_main_prompt(repo_url)`。
- **工具分配**：主 = web_search/fetch_url/get_pr_info；planner = web_search/fetch_url/storePlan；coder = get_unexecuted_plan/updatePlanStatus/create_pr；reviewer = comment_pr/get_pr_info。
- **验证**：编译通过、无旧符号残留；子智能体与工具装配正确；端到端建会话 + chat 走主智能体（自报「主控智能体」）；主智能体列出的子智能体恰为 planner/coder/reviewer。

#### get_pr_info 增强（支持仓库 / PR 链接）并归主智能体

- **输入**：`get_pr_info(source, state="all")` —— `source` 可以是仓库（`https://gitee.com/<owner>/<repo>` 或裸 `<owner>/<repo>`）或 PR 链接（`.../pulls/<n>`）；前者列 PR（`state` 过滤），后者返回单个 PR 详情。新增 `_normalize_source`（裸路径补全 URL）、`GiteeClient.list_pulls`、`_format_pr_list`。
- **归属**：该工具**归主智能体**；因主/子智能体架构尚未接入代码，**暂不注册**（从 `review` / `qa` 移除），待主智能体接入时挂载。
- **验证**：仓库 URL / 裸 owner-repo / PR 链接 / 非法输入四种路径均正确；`register` 已不含 `get_pr_info`。

#### git 工具：get_pr_info（按 PR 地址拉取 PR 信息）

- **改动**：`tools/git.py` 新增 `parse_gitee_pr_url(url) -> (owner, repo, number)`（支持 `/pulls/<n>` 与 `/pull/<n>`）；`GiteeClient.get_pull(owner, repo, number)`（owner/repo 可来自 PR 地址，不限于本会话仓库）；工具 `get_pr_info(pr_url)` —— 拉取并格式化为可读文本（标题/状态/作者/分支/链接/时间/可合并/评论数/描述）。
- **装配**：`review` → comment_pr + get_pr_info；`qa` → web_search + fetch_url + get_pr_info。
- **验证**：PR 地址解析（含非法地址拒绝）；工具装配正确；非法地址返回友好错误；**真实 token 拉取真实 PR 成功**。

#### 方案工具：storePlan / get_unexecuted_plan

- **改动**：新增 `tools/plan.py` —— `PlanContext`（会话上下文：session_db_id/owner/repo）+ `build_plan_tools(database, ctx)`：
  - `storePlan(content, steps?, title?)`：把方案写入 `plans` 表（供 coder 取用）。
  - `get_unexecuted_plan()`：取本会话最新 `pending` 方案并格式化为可读文本（含分步骤，结果带 `plan_id`）。
  - `updatePlanStatus(plan_id, status)`：更新方案状态（`executing` / `done` / `failed`），补上「取计划 → 执行完标记」闭环。
  - **按会话闭包**：模型无需传 owner/repo/session_id（工具内部捕获）。
- **装配**：`build_task_tools` 按类型挂载 —— coding → `create_pr` + `get_unexecuted_plan` + `updatePlanStatus`；planning → web_search + fetch_url + `storePlan`；`SessionManager.get_agent` 传入 `Database` + `PlanContext`。
- **coder 提示词**：取计划改为「工具已绑定会话、无需传参」；新增第 5 步「回写方案状态」（完成后 done / 失败 failed）。
- **验证**：store → get(带 plan_id) → updatePlanStatus(done) → 再 get 为空；存新方案后 get 取到新的；非法状态被拒；各任务类型装配正确。

#### 会话流程接入业务库 DAO

- **改动**：
  - `SessionManager.create_session` 改为「生成 `public_id` → 落库（sessions + repositories，拿内部 id）→ 用 `public_id` 建卷/沙箱 → 回填 `sandbox_id`/`workspace_volume`」；建沙箱/后端失败时**回滚**（删库记录 + 销毁沙箱）。
  - `Session` 数据类：`session_id` → `public_id`（对外）+ `db_id`（内部主键）；`thread_id` 用 `public_id`。
  - `destroy_session`：销毁沙箱后在库中标记 `status='destroyed'`。
  - `volumes.py` 加 `workspace_volume_name()`（卷名统一，供回填）。
  - `api/app.py`：lifespan 建 `Database` 并注入 `SessionManager`，退出时 `dispose()`。
- **验证**：HTTP 建会话 → 库中 `sessions`（public_id / sandbox_id / `ai-coding-ws-<public_id>` / status=active）+ `repositories`（owner/name）均正确落库；销毁后 status=`destroyed`。

#### 业务库 DAO 层

- **改动**：
  - `db/engine.py`：`Database` —— 异步引擎 + `async_sessionmaker`，`session()` 事务性上下文（正常提交 / 异常回滚）、`dispose()`。
  - `db/dao.py`：各表 CRUD —— sessions（create / get / get_by_public_id / list / set_sandbox / set_status / delete）、repositories（create / get）、plans（store_plan / get_unexecuted_plan / set_plan_status）、session records（add_record / list_records / get_record_detail）。
  - `db/__init__.py` 导出 `Database`。
  - 约定：DAO 函数接收外部传入的 `AsyncSession`、不自行提交，事务由 `Database.session()` 统一处理。
- **验证**：端到端跑通 —— 建会话（public_id）→ 建仓库 → 回填 sandbox/卷 → store_plan → get_unexecuted_plan → set_plan_status(done) → 再取未执行方案为 None → add_record → list_records / get_record_detail → **delete_session 级联清空 repo/plan/records**。

#### 业务库：会话记录表（垂直分表，页面展示用）

- **目的**：按会话记录「主要聊天 / 生成的方案 / review 结果」，供页面时间线展示；方案与审查结果**冗余存一份**（引用业务对象 id），便于查询。
- **垂直分表**（未来数据量大）：
  - `session_records`（基本信息/轻）：id / session_id（FK → sessions.id CASCADE）/ type（user·assistant·plan·review·pr·system）/ summary（摘要）/ ref_id（关联 plans.id 等）/ created_at；索引 `(session_id, created_at)`。
  - `session_record_details`（详情/重，1:1）：record_id（PK，FK → session_records.id CASCADE）/ content（全文）/ extra（JSONB）。
- **迁移**：`8cc9adc2c4ba_add_session_records_tables.py`。
- **验证**：`upgrade head` 成功；列/主键/FK(CASCADE)/索引确认；两表注释已写入。

#### session 主键改用方案 D（内部 BIGINT + 对外 UUID）

- **决策**：`sessions.id` 用 `BIGINT` 自增内部主键（索引最小最快、FK 轻量）；新增 `public_id`（`UUID`，UNIQUE，应用生成）作为**对外标识**（API / 卷名 / 线程名），避免自增 id 可枚举。
- **改动**：`db/models.py` —— `AgentSession.id` → BigInteger PK，新增 `public_id`（Uuid, unique）；`repositories.session_id` / `plans.session_id` → `BIGINT` FK。
- **迁移**：表为空，故 `alembic downgrade base` → 删旧迁移 → 重新 `--autogenerate` → 单一初始迁移 `6b0fc93c3dcd_init_business_tables.py`（仅本项目表）。
- **验证**：`upgrade head` 成功；`sessions.id=bigint(PK)` + `public_id=uuid(UNIQUE)`；两处 FK 均为 bigint → sessions.id CASCADE；漂移检查空 diff；注释 sessions 12/12、repositories 6/6、plans 12/12。
- **流程影响**：会话创建流程调整为「应用生成 `public_id` → INSERT 拿 `id` → 用 `public_id` 建卷名/线程名 → 建沙箱 → UPDATE 回填 sandbox_id」。当前 app 尚未接业务库，待 DAO 落地时生效。

#### 业务库：plans 表

- **模型**：`db/models.py` 加 `Plan` → `plans` 表。
  - 列：id / session_id（FK → sessions.id，`ON DELETE CASCADE`）/ owner / repo（冗余）/ title / content（Markdown 全文）/ steps（JSONB 结构化步骤 `[{index, goal, files, done_criteria}]`）/ status（`pending`/`executing`/`done`/`failed`）/ created_at / updated_at / started_at / finished_at。
  - 索引：`plans_session_status_idx (session_id, status)` —— 支撑 `get_unexecuted_plan`（找该会话 `status='pending'` 的最新方案）。
- **迁移**：`c3d0376fddd9_add_plans_table.py`（autogenerate，仅 plans 表——LangGraph 表已被过滤，未误判）。
- **验证**：`upgrade head` 建表成功；查 `information_schema` 确认列/类型/FK/索引；12/12 列 + 表注释已写入。

#### 业务表加列/表注释

- **改动**：`db/models.py` 给所有列加 `comment`，并加表注释（`__table_args__={"comment": ...}`）；`alembic revision --autogenerate` 生成迁移 `17e442b44e60_add_column_comments.py`（含 `alter_column(comment=...)` + `create_table_comment`）。
- **验证**：`alembic upgrade head` 应用成功；查 `col_description` / `obj_description` 确认所有列与表的注释已写入。

#### 业务库：sessions / repositories 表（SQLAlchemy + Alembic）

- **依赖**：`sqlalchemy[asyncio]` + `alembic`（加入 pyproject）。
- **模型**（`src/ai_coding_harness/db/`）：`base.py`（`Base`）、`models.py`（`AgentSession` → `sessions`、`Repository` → `repositories`）。
  - sessions：id / status / gitee_token（落库）/ gitee_username / sandbox_id / workspace_volume / title / created_at / updated_at / last_active_at / expires_at。
  - repositories：id / session_id（FK → sessions.id，`ON DELETE CASCADE`，`UNIQUE` 保证 1:1）/ owner / name / url / created_at。
- **Alembic**：async env（`alembic/env.py`），URL 取自 `settings.postgres_async_url`；`alembic/versions/0001_init_sessions_and_repositories.py`。
- **关键坑**：① Windows 下 psycopg 异步需 SelectorEventLoop（env.py 已处理）；② autogenerate 会把 **LangGraph 的表**（checkpoints/store 等）误判为「应删除」——已用 `include_object` 过滤，只管理本项目的表，避免误删。
- **验证**：`alembic upgrade head` 建表成功；查 `information_schema` 确认两表结构与 FK/UNIQUE 正确；`--autogenerate` 复检为空 diff（模型与库一致）。

#### 工作区卷改为「可配置后端」（支持集群）

- **背景**：集群部署已定，本地命名卷（节点本地）在集群下失效，需按环境切换共享存储。
- **改动**：
  - 新增 `sandbox/volumes.py` 的 `build_workspace_volume(settings, session_id)`，支持四种后端：`pvc`（本地命名卷 / K8s PVC，可配 `storage_class`/`storage`/`access_modes` RWX）、`host`（NFS 等共享路径，每会话子目录）、`ossfs`（对象存储）、`none`（不挂）；未知后端抛 `ValueError`。
  - `config.py` 新增 `sandbox_volume_backend` 及各后端配置（`sandbox_volume_host_path` / `sandbox_volume_storage_class` / `sandbox_volume_storage` / `sandbox_volume_access_modes` / `sandbox_oss_*`）。
  - `session/manager.py` 的 `_build_workspace_volume` 改为调用该函数（去掉内联构造）。
  - `.env.example` 补配置（默认 `pvc`，行为不变）。
- **验证**：各后端构造正确（pvc/host/ossfs/none + 未知后端报错）；默认 `pvc` 端到端回归通过（会话创建、`/ai_coding/project` 可写）。
- **范围外**：卷 GC、集群实机联调。

#### 重写 ai-coding-implementation skill（对齐 coder 角色）

- **定位**：从「需求理解 + 方案设计 + 实现」收敛为「**按 planner 的方案实施**」；明确不做方案设计、不与用户对话。
- **流程**：改为 `取方案(get_unexecuted_plan) → 建分支 → 按步骤实施(强要求) → 测试 → 更新仓库记忆 → 自检 → commit/push → create_pr → 回报主智能体`。
- **新增**：「按方案步骤逐条执行（强要求，不跳步/不合并/不改序）」「更新仓库记忆（按 repo-memory-template）」。
- **移除**：`comment_pr`（PR 评论归 reviewer）、「收尾回复用户」（改为回报主智能体）、「编码分层规则」（移到 planner 提示词，由方案管控）。
- **对齐**：前置条件改为「仓库已就绪（由 coder 准备）+ 已取到方案」，不重复 clone/pull。
- **planner 提示词**：新增「编码分层规则（方案须遵循）」段。
- **验证**：py_compile 通过；skill 无 `comment_pr`、含取方案/更新记忆/回报主智能体；planner 含分层规则。

#### 强要求：plan 分步骤、coder 按步骤逐条执行

- **planner**：方案**必须拆分为有序步骤**（每步含目标 / 涉及文件 / 完成标准），另附接口变化、影响面、整体验收标准；约束里明确「方案必须是有序步骤，每步可独立执行与验证」。
- **coder**：**必须按方案步骤逐条执行**——不跳步、不合并、不擅自改序，每步完成确认「完成标准」再进下一步；工作流程与原则两处都标为强要求。
- **验证**：py_compile 通过，关键字命中。

#### 四个智能体补「工作目录」段

- **主智能体**：project 只读（写码归 coder）、policies 只读、`output/` 可写、仓库记忆只读。
- **planner**：project 只读、policies 只读、仓库记忆（首次可生成）、产出写入 DB（不落文件）。
- **coder**：project 读写、policies/skills 只读、仓库记忆读写、`tmp/` 可写。
- **reviewer**：project 只读、policies 只读、`review/` 可写。
- **planner 首次处理**：非空且无记忆文件时，先用 `repo-bootstrap-analysis` skill 分析并生成仓库记忆文件。
- **验证**：py_compile 通过，各提示词均含「## 工作目录」且边界（只读/可写）明确。

#### 提示词明确「工具 + 具体命令」（不让模型猜）

- **原则**：提示词中所有操作都写明用哪个工具、执行哪条命令，给出确定执行路径。
- **改动**：
  - `BASE_PROMPT`：沙箱规则注明「写明具体命令，不要凭猜测」；git 工作方式给出逐操作命令（clone/pull/status/diff/checkout/add/commit/push）。
  - 主智能体：仓库分析用 `execute` 跑 `ls`/`find`/`cat`；代码问答用 `read_file`/`grep`；通用问答用 `web_search_tool`/`fetch_url`。
  - planner：`read_file` 读记忆 + `execute` 跑 `ls`/`find`/`cat` 浏览代码。
  - coder：`execute` 跑 `ls`/`git clone`/`git pull`；`read_file` 读记忆；实施走 skill（git/测试命令用 `execute`）。
  - reviewer：`execute` 跑 `git diff --stat` / `git diff -- <path>`；`read_file` 看代码；`write_file` 写报告。
- **验证**：py_compile 通过；各提示词均含明确的工具/命令。

#### coder / planner 提示词细化（计划落库 + 取计划 + 仓库区分）

- **coder**：① 区分项目存在与否（不存在 `git clone` / 已存在 `git pull` 更新）；② 新增「调用 `get_unexecuted_plan`（入参 `owner`/`repo`/`session_id`）从 DB 取上一次未执行的方案」；③ 代码编写与 git 提交通过 `ai-coding-implementation` skill 完成；④ 保留「按审查结论修复至无严重问题」。
- **planner**：新增「调用 `storePlan` 把方案写入 DB（供 coder 取用）」。
- **待办**：`get_unexecuted_plan` / `storePlan` 两个工具待写；DB 表设计待定；`ai-coding-implementation` skill 待修正。
- **验证**：py_compile 通过，关键字均命中。

#### 新架构提示词：主 + planner + coder + reviewer

- **改动**：`prompts.py` 新增 4 个角色提示词（均 = `BASE_PROMPT` + 角色）：
  - `MAIN_AGENT_PROMPT`：主智能体 —— ① 协调子智能体；② 直接处理仓库分析 / 仓库代码问答 / 通用问答。
  - `PLANNER_PROMPT`：制定实施方案（不写代码）。
  - `CODER_PROMPT`：按计划写码 / 测试 / 更新记忆 / 提 PR。
  - `REVIEWER_PROMPT`：只读审查 PR / diff，报告写 `/ai_coding/review/` 并 `comment_pr`。
- **说明**：旧的任务类型提示词（`CODING_PROMPT` / `READ_ONLY_PROMPTS` / `get_task_prompt`）暂时保留，待架构代码接入后移除。
- **验证**：py_compile 通过；4 个提示词均以 `BASE_PROMPT` 开头。

#### 仓库记忆：模版独立成文件 + 规则进基本提示词

- **改动**：
  - 基本提示词新增「仓库记忆文件规则」模块（用途、StoreBackend 位置、何时读写、模版路径）。
  - `CODING_PROMPT` 去掉内嵌模版，执行流程改为引用「仓库记忆文件规则」。
  - 模版独立成文件 `docs/repo-memory-template.md`，随镜像放入沙箱 `/ai_coding/policies/repo-memory-template.md`。
- **记忆存储**：StoreBackend，namespace `("memories",)`，key `${owner}_${repo_name}.md`。
- **验证**：py_compile 通过；BASE 8 模块（含新规则）、CODING_PROMPT 无内嵌模版且引用规则。

#### CODING_PROMPT 重写（方案先行 + 用户确认）

#### 移除 config 中的 repo_url 设计（统一走会话级 GiteeRepo）

- **结论**：`repo_url` 的值来自前端请求，PR 工具通过**会话创建时捕获的 `GiteeRepo` 闭包**获取 owner/repo，已足够；不需要把 `repo_url` 放进 agent 运行时 config。
- **改动**：`api/routes/chat.py` 的 `configurable` 去掉 `repo_url`（仅留 `thread_id`）；`tools/context.py` 删除未使用的 `get_repo_url()` 并更新模块说明。
- **保留**：`session.repo_url` 仍用于向 agent system prompt 注入「目标仓库」（`get_task_prompt`）。
- **验证**：py_compile 通过、无 `get_repo_url`/configurable repo_url 残留；app health 200。

#### 真实 PAT 端到端验证通过

- **验证**（真实私人令牌 + `zzpfordevelopment`，仓库 `zzpfordevelopment/test-abc`）：`.secrets/gitee_token` == 真实 PAT；askpass Username = `zzpfordevelopment`；沙箱内 `git clone` 成功；**`git push --dry-run` exit 0**（完整认证 + push 协商，dry-run 不改远端）。
- **说明**：本测试走「无请求 token → .env PAT 兜底」路径。发现并修正 `.env` 的 `SANDBOX_IMAGE` 误为原版镜像（导致 `setup.sh` 找不到）。

#### PAT / OAuth 双模式 git 认证用户名

- **方案**：git 认证用户名按 token 来源自动判定 —— 请求 token（线上 OAuth）用 `oauth2`；`.env` 兜底 token（本地 PAT）用配置的 Gitee 用户名。
- **改动**：`config.py` 加 `gitee_username` + `sandbox_git_username_env`；`GiteeRepo` 加 `git_username`；`SessionManager` 判定并注入 `GITEE_GIT_USERNAME`；镜像 askpass 改为 `${GITEE_GIT_USERNAME:-oauth2}`；`.env.example` 加 `GITEE_USERNAME`。
- **验证**：无请求 token → `alice`、有请求 token → `oauth2`，且沙箱内 askpass 返回值与之一致。
- **本地配置**：`.env` 用 `GITEE_ACCESS_TOKEN`（PAT）+ `GITEE_USERNAME`（Gitee 用户名）。

#### git 身份：静态写入镜像

- **结论**：push/clone/fetch 只需 token；git 身份（`user.name`/`email`）与认证无关，仅 `commit` 需要 → 采用**静态写入镜像**（`/etc/gitconfig`）。
- **改动**：镜像指南恢复 `files/gitconfig` + `COPY` 到 `/etc/gitconfig`；回退先前尝试的「按会话动态注入身份」代码（`fetch_gitee_user` / `GIT_AUTHOR_*` 注入）。
- **验证**：镜像内 `/etc/gitconfig` 就位，`git var GIT_AUTHOR_IDENT` = `ai-coding-agent <agent@example.com>`，沙箱内 `git commit`（不传身份、不配 config）成功、author 正确。

#### 镜像 git 能力验证

- **结果**：git 2.43.0，clone / checkout -b / add / commit / log / status / fetch / push 全部可用；**push 会调用 askpass**（`GIT_TRACE` 捕获 `run_command: /usr/local/sbin/gitee-askpass`）。
- **缺口**：镜像**未配置 git 身份**（无 `/etc/gitconfig`，`git var GIT_AUTHOR_IDENT` 报 Author identity unknown）→ `git commit` 会失败。
- **修复指引**：镜像指南的 `setup.sh` 增加 `git config --system user.name/email`（原「可选 gitconfig」改为 setup.sh 内必需项）。

#### 定制镜像验证通过（ai-coding/code-interpreter:v1）

- **验证**：走真实流程（注入 `GITEE_TOKEN` → 创建后执行 `setup.sh`）—— `.secrets/gitee_token`（0400）写入正确内容；`askpass` 返回 `oauth2`/令牌；`GIT_ASKPASS` 已配置；三个 skill、policies、git 2.43.0 就位；公开仓库 `git ls-remote` 成功。
- **配置**：`.env` 的 `SANDBOX_IMAGE` 切到 `ai-coding/code-interpreter:v1`。
- **待验证**：真实令牌的认证 push（需有效 OAuth 令牌 + 有写权限的仓库）。

#### 按方案 A 简化：去掉 sudo / gitwrite / agent 用户

- **决策**：沙箱 `execute` 默认以 **root** 运行 → 「令牌 root-only + sudo gitwrite 白名单」这套防护在 root 下形同虚设。按方案 A（已接受令牌对模型可见）**简化掉** sudo / gitwrite / sudoers / agent 用户。
- **改动**：
  - 镜像指南精简：仅保留 `gitee-askpass` + `setup.sh`；Dockerfile 不装 sudo、删 ENTRYPOINT、加 `ENV GIT_ASKPASS`/`GIT_TERMINAL_PROMPT`。
  - skill（`ai-coding-implementation`）与 `BASE_PROMPT` 的 push 改回普通 `git push`（askpass 自动供令牌）；删去 gitwrite/sudo 相关规则与排障项。
  - `workspace-directories.md` 的凭据说明对齐。

#### 沙箱初始化改走「创建后 execute」（Docker provider 限制）

- **验证定制镜像** `ai-coding/code-interpreter:v1`：✅ skills（3 个）、policies（java-style.md）、askpass/gitwrite 脚本、sudoers 文件、运行环境（Java21/Go1.25.5/Node22）都在；❌ `.secrets` 未生成、`sudo` 未安装、`agent` 用户不存在。
- **根因（两条 Docker provider 限制）**：① OpenSandbox 用默认 entrypoint `tail -f /dev/null` **覆盖镜像 ENTRYPOINT** → 镜像的初始化脚本从不执行；② **Docker provider 不支持 lifecycle hooks**（`lifecycle hooks are not supported by the Docker provider`）。
- **改动**：`SandboxProvider.create` 改为**创建后经 `execute` 执行 `sandbox_setup_command`**（默认 `/usr/local/sbin/setup.sh`）完成初始化，不依赖 ENTRYPOINT / hooks。
- **验证**：机制跑通 —— 创建后执行 setup，`.secrets/gitee_token`（0400 root）写入正确内容，且令牌不出现在命令文本里。
- **镜像待补**：装 `sudo`、提供不阻塞的 `setup.sh`（勿含 `exec tail`）、建 `agent` 用户。

#### 秘密处理决策：方案 A（令牌落沙箱 + 最小权限）

- **决策**：接受「令牌进沙箱 env」这一事实，靠「不池化（每沙箱单租户）+ OAuth 短时效/`projects` scope + prompt·skill 明令禁止读取」来控制风险。
- **改动**：`BASE_PROMPT` 的 git 章节改为如实描述（凭据由平台注入、gitwrite/askpass 取用），并加「严禁读取环境变量/`.secrets`、严禁打印令牌」；`ai-coding-implementation` skill 红线与动因描述同步；镜像指南补「已知取舍」。
- **排除**：OpenSandbox 凭据保险库（需服务端 `dns+nft`）。

#### 沙箱令牌注入（env 方式）

- **改动**：`SandboxProvider.create(volumes, env)` 支持注入环境变量；`SessionManager` 把会话 token 以 `GITEE_TOKEN`（`sandbox_gitee_token_env`）注入沙箱。
- **验证**：沙箱内 `env | grep GITEE_TOKEN` 能取到注入值。
- **发现问题（重要）**：env 注入**无法隐藏令牌** —— 环境变量不受权限保护，沙箱内任意命令（含 agent 的 `execute`）都能读到，且沙箱默认 root，entrypoint 也无法 unset（容器级 env，execd 独立继承）。即令牌会进入模型上下文，违背「秘密不进模型」的目标。要真正隐藏，需改用「root-only 文件 + 非 root 运行 agent」或 OpenSandbox 凭据保险库（需服务端 dns+nft）。

#### 工作区 Volume（容器可换、坏能重挂）

- **改动**：`SandboxProvider.create(volumes=...)` 支持卷挂载；`SessionManager` 为每个会话构建会话级工作区卷（Docker 命名卷 `ai-coding-ws-<session_id>` 挂到 `/ai_coding/project`）；config 加 `sandbox_volume_enabled/prefix/delete_on_termination`。
- **关键设计**：`session_id` 在创建沙箱前生成，卷名确定；`delete_on_sandbox_termination=False`（默认）使容器终止后卷仍在，新容器可挂同一卷续接。
- **验证**：真实故障转移测试 —— 容器 A 写 `/ai_coding/project/probe.txt` → 销毁 → 容器 B 挂同卷 → 读回成功。
- **遗留**：SDK 无删除卷 API，会话结束的卷清理需 Docker 侧 GC（否则命名卷堆积）。

#### 持久化 SQLite → PostgreSQL

- **改动**：checkpointer/store 从 SQLite 切到 PostgreSQL（`AsyncPostgresSaver` / `AsyncPostgresStore`，psycopg 异步连接池）；config 加 `postgres_host/port/db/user/password` + `postgres_url` 属性；依赖加 `langgraph-checkpoint-postgres`、`psycopg[binary]`。
- **动机**：多租户/多 worker 下状态需外置共享，SQLite 本地文件无法跨 worker。这也是「worker 无状态、可伸缩」的先决条件。
- **关键坑**：① langgraph `setup()` 建表迁移含 `CREATE INDEX CONCURRENTLY`，首次建表需 `autocommit=True`；表建好后连接池改为 `autocommit=False`（常规读写走事务），仅将来新增含 CONCURRENTLY 的迁移时需临时切回；② Windows 下 psycopg 异步不支持默认 ProactorEventLoop，`main.py` 需切 SelectorEventLoop（Linux 无此问题）。
- **验证**：checkpointer/store 构建成功，store 读写往返成功；完整端到端跑通（health 200 → create session 200 → chat SSE 236 行 → destroy 200），持久化走 Postgres。经 SSH 隧道连 `127.0.0.1:5432`。

#### 沙箱运行环境版本验证

- **验证**：沙箱（`opensandbox/code-interpreter:v1.1.0`）实测运行环境 —— Java 21.0.11、Go go1.25.5、Node v22.2.0（npm 10.7.0）均符合；Python 3.14.5 位于 `/root/.local/bin/python3.14`（uv 多版本，`/opt/python/versions/` 下有 3.10.20 / 3.11.15 / 3.12.13 / 3.13.13 / 3.14.5）。
- **说明**：默认 `python3` 指向系统 3.12.3；镜像中已按 3.14.5 使用。沙箱规则增加「仅支持 Java/Python/JavaScript/Go」与「支持运行环境版本」。

#### Gitee OAuth 登录（方案 A）

- **改动**：新增 `api/routes/auth.py`（`GET /auth/gitee/authorize` 跳授权页、`GET /auth/gitee/callback` 用 code 换 token）；config 加 `gitee_oauth_client_id` / `gitee_oauth_client_secret` / `gitee_oauth_redirect_uri`。
- **关键设计**：OAuth 授权码流程，scope=projects；callback 换 token 后返回 access_token 给前端，前端随 `create_session` 的 `gitee_token` 字段带上。
- **验证**：路由注册正常，未配置时返回友好 500。

#### 运行时上下文辅助函数 + repo_url 注入

- **改动**：新增 `tools/context.py`（`get_config` / `get_configurable` / `get_thread_id` / `get_repo_url` / `get_state`）；chat 路由把 `repo_url` 注入 `configurable`。
- **关键设计**：`thread_id` / `repo_url` 存于 LangGraph `configurable`；辅助函数无 runnable 上下文时安全返回默认值（不抛错）。

#### fetch_url 工具

- **改动**：新增 `tools/fetch_url.py`（httpx 拉取 + BeautifulSoup 剥离 script/style/nav 等 + markdownify 转 markdown），并接入 `build_task_tools`（qa / planning 增加 fetch_url）。
- **关键设计**：处理超时 / 连接失败 / HTTP 错误 / 限流（429 + Retry-After）/ 非 HTML / 空内容；内容超 20000 字符截断。
- **验证**：抓 PEP 8 页面转 markdown 成功；qa / planning 工具集含 web_search_tool + fetch_url。

#### 工作区目录调整（去 run、tmp 归入 /ai_coding/tmp）

- **改动**：沙箱工作区去掉 `run/`，`tmp` 改为 `/ai_coding/tmp`；`BASE_PROMPT` 工作区规则覆盖为新目录结构（含读写权限说明）。
- **同步**：`config.py`（去 `sandbox_run_dir`、加 `sandbox_review_dir`/`sandbox_tmp_dir`）、`provider.py`（mkdir 目录）、两个只读 skill（`/tmp`→`/ai_coding/tmp`）、`verify_opensandbox.py`、README、`docs/workspace-directories.md`。

#### skill 接入 deepagents（SkillsMiddleware）

- **改动**：`build_agent` 加 `skills` 参数透传给 `create_deep_agent`；`SessionManager.get_agent` 传 `skills=[sandbox_skills_dir]`（`/ai_coding/skills`）。
- **说明**：三个 skill（`ai-coding-implementation` / `repo-bootstrap-analysis` / `code-review`）写在 `sandbox/skills/` 目录，需经镜像 COPY 到沙箱 `/ai_coding/skills/` 后才被加载。
- **验证**：py_compile 通过；端到端 create + chat + destroy 跑通，SkillsMiddleware 初始化正常（沙箱 `/ai_coding/skills/` 为空加载为空、不报错）。

#### observability 层（日志 + trace 预留）

- **改动**：新增 `observability/logging.py`（`setup_logging`）+ `observability/tracing.py`（`setup_tracing` 预留）；在 `manager.py` 关键点加日志，`app.py` lifespan 启用。
- **关键设计**：日志格式含时间戳/级别/模块名；记录沙箱创建、会话创建/销毁、智能体构建等关键事件；trace 预留（LangSmith/OTel 后续接入）。
- **验证**：端到端跑通，日志输出关键事件。

#### safety 层：命令安全

- **改动**：新增 `safety/permissions.py`（`CommandSafety` 危险命令 denylist），接入 `OpenSandboxBackend.aexecute` 执行前检查。
- **关键设计**：denylist 覆盖 `rm -rf /`、`mkfs`、`dd` 写盘、`shutdown/reboot`、fork bomb、覆盖磁盘、全盘 777 等；拒绝时返回友好错误（exit_code=1）。
- **验证**：危险命令拒绝、安全命令（echo/git clone/python/rm -rf 具体子目录）放行。
- **说明**：人审（approval）预留，单用户本地暂不实现。

#### 多例智能体架构（按任务类型）

- **改动**：从「主智能体 + code_writer/code_reviewer 子智能体」改为「多例智能体」——每种任务类型一个智能体实例，按需构建并缓存，prompt = 基本提示词 + 任务类型提示词。
- **涉及**：
  - `prompts.py`：`CODING_PROMPT` + `READ_ONLY_PROMPTS`（qa/planning/analysis/inspect/sync/review）+ `get_task_prompt()`；移除旧角色 prompt。
  - `graph.py`：`build_agent` 改为接收任务级 tools + prompt（无子智能体）。
  - `tools/register.py`：`build_task_tools()` 按类型返回工具（coding→create_pr、review→comment_pr、qa/planning→web_search，其余无）。
  - `session/manager.py`：Session 持有多例 `agents` 字典，`get_agent()` 按需构建缓存，checkpoint 按 `(session, task_type)` 隔离。
  - api 层：`ChatRequest` 加 `task_type`，chat 路由按类型路由；`session_id` 替换 `thread_id`。
  - 删除 `subagents.py`。
- **验证**：7 种类型 prompt/工具正确；端到端 create + chat(task_type=inspect) 按需构建缓存 + destroy 通过。

#### repo_url 注入 agent prompt

- **改动**：`prompts.py` 加 `build_main_prompt(repo_url)`，`graph.py` 的 `build_agent` 加 `system_prompt` 参数，`session/manager.py` 创建会话时把仓库地址注入主智能体 prompt（主智能体调度 code_writer 时把仓库地址传下去 clone）。
- **验证**：prompt 含 repo_url 与 clone 说明；端到端 create_session 成功。

#### Gitee 配置动态化（仓库地址 + token）

- **改动**：Gitee 的 owner/repo 改为由 API 请求的仓库地址动态解析，token 请求优先、平台配置兜底。
- **涉及**：`tools/git.py`（新增 `GiteeRepo` + `parse_gitee_repo_url`）、`tools/register.py`、`config.py`（移除 gitee_owner/repo）、`api/schemas.py`（`CreateSessionRequest`）、`api/routes/sessions.py`、`session/manager.py`（每会话构建 PR 工具）、`api/app.py`（web_search 共享）。
- **验证**：仓库地址解析（`.git`/无后缀/尾随斜杠/非法均正确）；会话创建绑定 owner/repo/token，端到端通过。

#### api 层（FastAPI）

- **改动**：新增 `api/`（`app.py` / `schemas.py` / `routes/{health,sessions,chat}.py`），`main.py` 改用 `create_app` + uvicorn 启动。
- **关键设计**：
  - lifespan 构建共享依赖（model/tools/store/checkpointer/SessionManager），关闭时销毁会话并关闭持久化连接。
  - `POST /sessions` 创建会话（真实沙箱）、`DELETE /sessions/{thread_id}` 销毁；`POST /chat/{thread_id}` 用 `astream(stream_mode="messages")` 做 SSE 流式。
- **验证**：端到端跑通 —— `/health` 200；创建会话（真实沙箱）；`/chat` SSE 流式返回 AI token + done；销毁会话 200。

#### session 层：manager.py（会话管理）

- **改动**：新增 `session/manager.py`，`SessionManager` 管理每会话生命周期：`create_session()` 建沙箱 → 构建混合后端 → 编译绑定沙箱的 agent；`destroy_session()` 销毁沙箱。
- **关键修正**：移除 code_reviewer 的 permissions —— deepagents 的 `FilesystemMiddleware` 不支持「沙箱 backend + permissions」组合（抛 `NotImplementedError`），只读改由 prompt 约束。
- **验证**：端到端创建会话（真实沙箱）+ 编译 agent（`CompiledStateGraph`）+ 销毁，均成功。

#### agent 层：graph.py（装配）

- **改动**：新增 `agent/graph.py`，`build_agent(model, backend, tools, store, checkpointer)` 用 `create_deep_agent` 装配主智能体 + 两个子智能体。
- **关键设计**：backend 传实例（factory 已弃用），即每会话编译一个 graph（backend 绑定会话沙箱）；model/tools/store/checkpointer 跨会话共享。
- **验证**：用 `StateBackend` 临时后端 + `:memory:` store/checkpointer 装配，返回 `CompiledStateGraph`。

#### session 层：state.py（checkpointer + store）

- **改动**：新增 `session/state.py`，`build_checkpointer()` / `build_store()` 构建 async SQLite 的 LangGraph checkpointer 与 store。
- **关键设计**：
  - checkpointer = `AsyncSqliteSaver`，store = `AsyncSqliteStore`。
  - 连接必须用 `aiosqlite.connect(path, isolation_level=None)`（autocommit），否则 store 写操作报「嵌套事务」错误。
  - 配置新增 `checkpoint_db_path` / `store_db_path`（默认 `data/checkpoints.db`、`data/store.db`）。
- **验证**：构建 + setup 成功；store `aput`/`aget` 读写往返成功。

#### tools 层：register.py（汇总）

- **改动**：新增 `tools/register.py`，`build_tools(settings)` 返回 `HarnessTools`（`main_agent_tools` / `create_pr` / `comment_pr`）。
- **验证**：工具归属正确（web_search 归主智能体，create_pr/comment_pr 归子智能体），可直接喂给 `build_subagents`。

#### tools 层：web_search.py 优化

- **改动**：优化 `tools/web_search.py`（智谱 web 搜索工具）。
- **关键设计**：
  - 配置化：从 Settings 读 `zhipu_api_key` / `zhipu_base_url`（不再硬编码 YOUR_API_KEY），移除无用 `runtime` 参数。
  - 失败场景：超时 / 连接 / API 状态 / 响应错误 / 通用异常逐级捕获，返回友好错误信息；未配置 key 时返回提示。
  - 修正返回结构：`search_result` 实际是 `list`，逐条格式化为可读文本；空结果返回「搜索无结果」。
- **验证**：未配置 key 返回友好错误；真实调用返回 9 条格式化结果。

#### tools 层：git.py（Gitee PR 工具）

- **改动**：新增 `tools/git.py`，实现 `GiteeClient` + `build_pr_tools()`，提供 `create_pr` / `comment_pr` 两个工具，用 httpx 直接调 Gitee API v5（宿主进程，不经沙箱）。
- **关键设计**：
  - 端点：创建 PR `POST /repos/{owner}/{repo}/pulls`，评论 `POST /repos/{owner}/{repo}/pulls/{number}/comments`，`access_token` 放请求 body。
  - 配置新增 `gitee_access_token` / `gitee_owner` / `gitee_repo` / `gitee_base_url` / `gitee_base_branch`。
  - 未配置返回友好错误；HTTP 错误返回状态码 + 详情。
- **验证**：两工具名/参数 schema 正确；未配置时调用返回友好错误。

#### agent 层：subagents.py

- **改动**：新增 `agent/subagents.py`，提供 `build_subagents(create_pr_tool, comment_pr_tool)`，返回 `code_writer` / `code_reviewer` 两个 `SubAgent`。
- **关键设计**：
  - `code_writer`：`tools=[create_pr]`；`code_reviewer`：`tools=[comment_pr]`。
  - `model` 不指定、继承主智能体（DeepSeek）；内置文件工具/execute 由 deepagents 中间件注入。
- **验证**：两子智能体构造正确。

#### agent 层：prompts.py（三个 system prompt）

- **改动**：新增 `agent/prompts.py`，定义 `MAIN_AGENT_PROMPT`（主控/orchestrator）、`CODE_WRITER_PROMPT`、`CODE_REVIEWER_PROMPT`。
- **关键设计**：
  - 主智能体只做调度，先判断问题类型（一般性问题走 `web_search_tool`，写代码类走编码流程）。
  - 编码流程：`code_writer` 写码 → `create_pr` 工具创建 PR → `code_reviewer` 针对 PR 只读审查 → 按需回环。
  - PR 的创建/评论由专用工具（`create_pr` / `comment_pr`）实现，不直接跑 gh 命令；git add/commit/push 仍走命令。
  - `code_writer` 负责写码 + 自测 + 提交；`code_reviewer` 只读、报告写到 `/ai_coding/output/` 并发到 PR。
- **说明**：`web_search_tool` 由用户自行实现；git 代码平台采用 **Gitee**，`create_pr` / `comment_pr` 对接 Gitee API。

#### agent 层：模型参数（禁用思考 + 温度）

- **改动**：
  - `llm.py`：`init_chat_model` 增加 `extra_body={"thinking": {"type": "disabled"}}`（禁用思考/推理）与 `temperature=1.1`。
  - `config.py`：改用 `load_dotenv(verbose=True)`，字段默认值用 `os.getenv(...)` 读取；移除 `write_model_env`/`model_post_init`（不再写环境变量），并修正 `kimi_model` 环境变量名为大写 `KIMI_MODEL`。
- **验证**：主/备模型 `temperature=1.1`、`extra_body` 正确传入，model_name 正确。

#### agent 层：llm.py（DeepSeek 主 + Kimi 备）

- **改动**：`agent/llm.py` 改用 `init_chat_model(model_provider="openai")` 初始化主/备模型；`config.py` 新增 `ModelConfig`、`deepseek_*` / `kimi_*` 字段、`primary_model` / `backup_model` 属性、`write_model_env()`（把主模型写入 `OPENAI_API_KEY` / `OPENAI_BASE_URL`，在 `model_post_init` 自动执行）。
- **关键决策**：DeepSeek 为主模型、Kimi 为备用，均走 OpenAI 兼容协议；显式 `model_provider="openai"`，避免 `deepseek-v4-pro` 被前缀推断成 `deepseek` provider（需额外包）。
- **验证**：`build_model` 主/备返回 `ChatOpenAI`、model_name 正确；`write_model_env` 生效；空 model 抛 `ValueError`。

#### 验证：OpenSandbox 端到端跑通

- **改动**：新增 `scripts/verify_opensandbox.py` 端到端验证脚本；`config.py` 增加 `opensandbox_request_timeout_seconds = 600`，`build_connection_config` 设置 `request_timeout`。
- **关键发现**：首次创建沙箱拉镜像耗时超过 SDK 默认 30s，触发 `ReadTimeout`；加大请求超时后解决。镜像内 Python 3.12.3。
- **验证结果**：创建沙箱 → 五个目录初始化 → 命令执行 → 文件写读 → 搜索 → 销毁，全部通过。

#### backends 层（智能体文件后端）

- **改动**：实现 deepagents 的混合文件后端。
  - `backends/opensandbox_backend.py`：`OpenSandboxBackend(BaseSandbox)`，子类化 deepagents 的 `BaseSandbox`，只实现异步原语 `aexecute` / `aupload_files` / `adownload_files` / `id`；`ls`/`read`/`write`/`edit`/`grep`/`glob` 由基类派生。
  - `backends/composite.py`：`build_composite_backend()` → `CompositeBackend(default=OpenSandboxBackend, routes={"/memories/": StoreBackend})`。
- **关键决策**：
  - OpenSandbox SDK 是异步的，故实现异步原语，同步版本抛 `NotImplementedError`（deepagents 工具层走异步路径）。
  - `StoreBackend` 用 `namespace=("memories",)`；`store`（LangGraph `BaseStore`）由外部传入，供 memory/session 层做持久化。
- **验证**：`/memories/` 路由的写入/读取/编辑端到端通过（`InMemoryStore`）；沙箱默认路由待真实沙箱验证。

#### 沙箱层（sandbox/）

- **改动**：
  - `sandbox/client.py`：`build_connection_config()` + `SandboxClient`（`run`/`write_file`/`read_file`/`list_dir`/`search`/`destroy`）+ `CommandResult`。
  - `sandbox/provider.py`：`SandboxProvider`（每会话新建沙箱 → `mkdir -p` 初始化五个目录 → 返回 `SandboxClient`；`destroy` 销毁）。
  - `config.py` 增加 `sandbox_timeout_minutes = 60`。
- **关键决策**：
  - 沙箱镜像 `opensandbox/code-interpreter:v1.1.0`，服务地址 `192.168.72.128:8080`，沙箱根目录 `/ai_coding`。
  - 五个工作目录：`/ai_coding/{project,policies,run,skills,output}`。
  - OpenSandbox API key 配置在 `.env`（不进代码、不入库）。

#### 脚手架 + 配置

- **改动**：建立 src 布局、`pyproject.toml`（hatchling 打包）、`.env.example`、`README.md`、`.gitignore`、`config.py`、`main.py` 占位。
- **关键决策**：
  - 发行包名 `ai-coding-harness`，导入包 `ai_coding_harness`。
  - venv 用 `D:\python-venv\ai_coding\Scripts\python.exe`（Python 3.14.7）。
  - 环境变量无前缀；`OPENAI_API_KEY` / `OPENAI_MODEL` 由用户自填。
