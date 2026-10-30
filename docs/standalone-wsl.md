# Standalone WSL 部署

本项目支持两套明确的部署档位。两套档位使用同一套业务代码，但外部依赖和运行边界不同。

## 档位对照

| 能力 | `standalone` | `multi` |
| --- | --- | --- |
| 业务数据库 | 单个 SQLite 文件 | PostgreSQL |
| LangGraph checkpoint / Store | 同一个 SQLite 文件 | PostgreSQL |
| 运行帧、运行权、熔断、Session 快照 | 进程内 | Redis |
| 命令沙箱 | WSL2 + bubblewrap | OpenSandbox |
| worker 数 | 1 | 多个 |
| Docker | 不需要 | OpenSandbox 侧需要 |
| Windows | 仅用于启动 WSL 和打开界面 | 可作为客户端 |

`DEPLOYMENT_MODE` 决定档位。留空或设为 `multi` 时保持多 worker 语义；独立版必须显式设为
`standalone`。

## 数据与目录

独立版默认只在 WSL 的 ext4 文件系统中写数据：

```text
/home/<WSL_USER>/.ai-coding-harness/
├── harness.sqlite3          # 业务表 + checkpoint + Store
├── sessions/
│   └── <session-id>/
│       ├── home/
│       ├── sbin/
│       └── ai_coding/
│           ├── project/
│           ├── policies/
│           ├── skills/
│           ├── maven/
│           ├── output/
│           ├── review/
│           ├── tmp/
│           ├── .secrets/
│           └── .m2/
├── logs/
└── venv/
```

每个会话的 `project`、`home`、`output`、`review`、`tmp`、`.secrets` 与 `.m2` 可写；
`policies`、`skills`、`maven` 只读。`bubblewrap` 不暴露真实的 WSL home、`/mnt/c` 或
`/mnt/d`，会话销毁时删除自己的 session 根目录。

业务库、LangGraph checkpoint 与 Store 必须共用一个 SQLite 文件。推荐只设置
`SQLITE_PATH`，不要同时手工填写另一个 `DATABASE_URL`；若两者同时出现，启动校验会要求它们
指向同一文件。

## 环境要求

- Windows 10/11 上安装 WSL2 和一套 Ubuntu 22.04/24.04。
- 建议使用名为 `ai-coding-harness` 的专用发行版；使用现有发行版时同步修改 `WSL_DISTRO`。
- 不需要在 Windows 或 WSL 中安装 Docker / OpenSandbox。
- WSL 内核必须允许 unprivileged user namespaces，`bubblewrap` 才能创建命令沙箱。

先确认 WSL 本身可用：

```powershell
wsl --update
wsl --list --verbose
```

如果还没有发行版：

```powershell
wsl --install -d Ubuntu-24.04
```

安装后把 `WSL_DISTRO` 改成实际的发行版名称即可。

## 一键启动

在 Windows 中双击仓库根目录的 `start-standalone.bat`，或从终端执行：

```powershell
.\start-standalone.bat
```

入口会按以下顺序自动选择 WSL 发行版：

1. 环境变量 `WSL_DISTRO`
2. `Ubuntu-24.04`
3. `Ubuntu-22.04`
4. `Ubuntu`
5. 当前安装的第一个发行版

也可以显式指定：

```powershell
.\start-standalone.bat -Distro Ubuntu-24.04
```

首次运行若发现工具链、虚拟环境或管理员缺失，会自动执行 WSL bootstrap：

1. 安装 Python 3.11+、Node.js 20+、Java 21、Maven、Go、Git、ripgrep、sqlite3 与
   bubblewrap。
2. 创建 `harness` 用户及 `~/.ai-coding-harness` 数据目录。
3. 创建独立 Python 虚拟环境并安装项目依赖。
4. 缺少 `.env` 时从 `.env.example` 创建；已有 `.env` 不覆盖。
5. 数据库没有用户时，自动生成随机密码创建首个 `admin` 并认领历史会话。

初始管理员结果会同时出现在控制台和：

```text
/home/harness/.ai-coding-harness/logs/initial-admin.txt
```

示例：

```text
Standalone 初始管理员已创建
用户名：admin
密码：<随机密码>
凭据文件：/home/harness/.ai-coding-harness/logs/initial-admin.txt
首次登录后必须修改密码。
```

凭据文件权限为 `0600`。管理员带有 `must_change_password=true`，首次登录后必须先修改密码。
如果数据库已有用户，后续启动只提示管理员已存在，不再生成新密码，也不覆盖凭据文件。

Windows 侧的 `frontend/node_modules` 缺失时，入口也会自动执行 `npm install`。
启动成功后自动打开 `http://localhost:5173`。

运行期间保持启动窗口开启，按 `Ctrl+C` 同时停止前后端。直接关闭窗口可能留下脱离的后台
子进程，应使用下面的 stop 入口。

如果窗口已经关闭或子进程仍在运行，执行：

```text
双击根目录 stop-standalone.bat
```

或：

```powershell
.\stop-standalone.bat
```

stop 会按端口定位进程，并核对 WSL 命令行确实是 `ai_coding_harness.main`、Windows 进程确实
属于本仓库 Vite，再停止前后端；不会仅因为端口相同就误杀无关服务。

也可以完全在 WSL 中手动操作：

```bash
sudo bash scripts/wsl/bootstrap.sh
bash scripts/wsl/verify-toolchain.sh
bash scripts/wsl/start-standalone.sh
```

## 配置

推荐的最小独立版配置：

```env
DEPLOYMENT_MODE=standalone
SQLITE_PATH=/home/harness/.ai-coding-harness/harness.sqlite3
SANDBOX_PROVIDER=wsl
WSL_DISTRO=ai-coding-harness
WSL_USER=harness
WSL_ROOT=/home/harness/.ai-coding-harness/sessions
WSL_BWRAP=/usr/bin/bwrap
WSL_ALLOW_NETWORK=true
REDIS_REQUIRED=false
```

Windows 启动脚本会用环境变量覆盖 `.env` 中与档位相关的键，因此同一个仓库可以在多 worker
部署配置之外启动独立版。不要反过来在 Windows 上直接运行 standalone 后端：bubblewrap 和
WSL 路径只存在于 Linux 侧。

多 worker 部署保持：

```env
DEPLOYMENT_MODE=multi
REDIS_URL=redis://127.0.0.1:6379/0
REDIS_REQUIRED=true
SANDBOX_PROVIDER=opensandbox
```

PostgreSQL 地址继续由 `POSTGRES_*` 或 `DATABASE_URL` 提供。multi 模式要求
PostgreSQL、Redis URL 与 OpenSandbox；`REDIS_REQUIRED=true` 用于要求 Redis 故障时
直接阻止启动，存量部署只配置 `REDIS_URL` 也能运行。multi 的管理员创建由具体运维人员
执行 `create-admin`，不会自动生成密码，也不会把凭据写入文件。

## 数据库

standalone 第一次启动会用 ORM metadata 创建当前 schema；多 worker 模式仍以 Alembic
迁移为事实源。现有 PostgreSQL 数据不会自动迁入新建的 SQLite 文件，从 multi 切换到
standalone 应视为新建独立实例。

常用检查：

```bash
sqlite3 ~/.ai-coding-harness/harness.sqlite3 ".tables"
sqlite3 ~/.ai-coding-harness/harness.sqlite3 "SELECT id, username, role FROM users;"
```

## 安全边界

- 命令在 bubblewrap 中运行，使用独立 PID / IPC / UTS namespace。
- `/usr`、`/bin`、`/sbin`、`/lib*`、`/etc` 与会话资产中的只读目录按只读方式挂载。
- 会话目录位于 WSL ext4，不使用 Windows 盘上的路径。
- 自动生成的初始凭据只写入 `logs/initial-admin.txt`，权限 `0600`；首次改密后临时密码失效。
- 默认保留网络访问，因为代码智能体需要拉依赖和访问代码平台；网络 allowlist 代理尚未接入。
- SQLite 使用 WAL、`busy_timeout=5000`、外键约束；该档位仍明确只支持单 worker。
