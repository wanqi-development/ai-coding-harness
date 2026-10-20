"""宿主机侧的仓库只读缓存 —— 右侧栏「代码」面板的数据源。

**为什么不在沙箱里读代码**：沙箱会没（TTL 到期被回收、会话结束被销毁、首次创建失败），
而「看看这个仓库的代码」是随时都可能想做的事。本机自己 clone 一份只读缓存，面板就与沙箱
彻底解耦 —— 工作区还在准备、已经被回收、甚至建失败，代码照样看得到。

**为什么是裸仓（``git clone --bare``）而不是「克隆到工作区再 checkout」**：

- 切分支退化成「换个 ref 参数」（``git ls-tree <ref>:<dir>`` / ``git cat-file blob <ref>:<path>``），
  服务端**零状态** —— 两个标签页可以各看各的分支，也不存在脏工作区、并发 checkout 打架；
- 用户给的路径**从不落到文件系统**（只喂给 git 去树里查），目录穿越在结构上就不可能
  （实测 ``git rev-parse --verify 'master:../readme.md'`` 被 git 直接拒掉），这里再加一层
  ``..`` / 绝对路径 / 反斜杠的字面拒绝；
- 读操作**不需要锁**：``git fetch`` 只在对象全部落盘之后才动 ref，读到的要么是旧提交、
  要么是新提交，不会是半个。只有 clone / fetch 之间要互斥（同仓库并发同步没有意义）。

**缓存按 ``owner/repo`` 共享一份**（平台本来就「一个仓库一个活跃会话」）：目录
``<repo_view_dir>/<owner>/<repo>``，旁边一份 ``<repo>.json`` 记上次成功同步的时间。
``repo_view_dir`` 默认是**用户个人目录**下的 ``~/.ai-coding-harness/repo_cache``（不是项目目录，
见 ``config._default_repo_view_dir``）。

**凭据**：令牌经**环境里的 git 配置**注入（``GIT_CONFIG_*`` + ``http.extraHeader``，
与 ``https://oauth2:<token>@…`` 等价），于是它**不进 URL、不进命令行参数、也不落进
``.git/config``** —— 最后一条是硬要求：clone 用的 URL 会被 git 记进 ``remote.origin.url``，
把令牌拼进 URL 就等于把它写在了磁盘上。git 的 stderr 回给调用方前先按令牌（及其 base64）
替换一遍，报错信息里也就带不出令牌。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import stat
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from ai_coding_harness.config import Settings
from ai_coding_harness.core.codeplatform import Platform

if TYPE_CHECKING:  # 只为类型标注：不把会话模块的导入拖进本模块
    from ai_coding_harness.session.manager import Session

logger = logging.getLogger(__name__)

#: ``shutil.which`` 只看 PATH，而 Windows 上装了 git 却没把 ``cmd`` 加进 PATH 是常态
_WINDOWS_GIT_HINTS: tuple[str, ...] = (
    r"C:\Program Files\Git\cmd\git.exe",
    r"C:\Program Files (x86)\Git\cmd\git.exe",
    r"C:\Program Files\Git\bin\git.exe",
)

#: owner / repo 会用来拼**磁盘路径**，所以只认保守的字符集（Gitee 的名字都在这个范围里），
#: 顺带堵掉 ``..`` 这类拼路径的花样。
_NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: 同步状态文件的后缀（与裸仓目录同级，不往裸仓里塞杂物）
_STATE_SUFFIX = ".json"


class RepoViewError(RuntimeError):
    """这次读取/同步没做成 —— ``str(exc)`` 是**可以直接给用户看**的一句话。"""


class GitMissingError(RepoViewError):
    """本机没找到 git。"""


@dataclass(frozen=True)
class RepoSpec:
    """要看的是哪个仓库、用哪份凭据 —— 与沙箱那套共用同一个来自会话的描述。"""

    owner: str
    repo: str
    url: str
    token: str = ""
    git_username: str = "oauth2"
    #: 仓库所在平台。只用来给「URL 解析不出 host」兜底（见 ``_auth_env``）——
    #: 写死 gitee.com 会让 GitHub 仓库的认证头挂到别的域名上，等于**静默失效**。
    platform: Platform = Platform.GITEE

    @classmethod
    def from_session(cls, session: Session) -> RepoSpec:
        """从会话描述组装（没有仓库的会话到不了这里）。"""
        return cls(
            owner=session.repo.owner,
            repo=session.repo.repo,
            url=session.repo_url,
            token=session.repo.token or "",
            git_username=session.repo.git_username or "oauth2",
            platform=session.repo.platform,
        )


@dataclass(frozen=True)
class BranchInfo:
    """一个分支：名字、短 sha、最后一次提交的时间与标题。"""

    name: str
    sha: str
    updated_at: str
    subject: str
    is_default: bool = False


@dataclass(frozen=True)
class TreeEntry:
    """目录里的一个条目。``size`` 只有文件才有（目录是 ``None``）。"""

    name: str
    path: str
    type: str  # "file" | "dir" | "link" | "submodule"
    size: int | None = None


@dataclass(frozen=True)
class FileContent:
    """一个文件的正文，或「为什么不给正文」。"""

    path: str
    ref: str
    text: str | None = None
    size: int = 0
    binary: bool = False
    too_big: bool = False


@dataclass(frozen=True)
class SyncResult:
    """一次同步的结果：这次是克隆还是更新，加上同步后的分支清单。"""

    cloned: bool
    default_branch: str
    branches: list[BranchInfo] = field(default_factory=list)


@dataclass(frozen=True)
class FileChange:
    """一处改动：路径 + 增删行数 + 状态码（``A`` / ``M`` / ``D`` / ``R``…）。"""

    path: str
    added: int
    deleted: int
    status: str


# --------------------------------------------------------------------------- 比对（PR 的 diff）


def _merge_changes(counts: str, codes: str) -> list[FileChange]:
    """把 ``--numstat``（增删行数）与 ``--name-status``（状态码）按路径并起来。

    二进制文件的增删是 ``-``（git 不数行）：记 0（界面上按「二进制」显示，不假装是 0 行改动）。
    改名的行是 ``R100\told\tnew``：状态码取新路径那份。
    """
    status: dict[str, str] = {}
    for line in codes.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            status[parts[-1]] = parts[0]
    out: list[FileChange] = []
    for line in counts.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        out.append(
            FileChange(
                path=path,
                added=int(added) if added.isdigit() else 0,
                deleted=int(deleted) if deleted.isdigit() else 0,
                status=status.get(path, "M"),
            )
        )
    return out


async def compare(settings: Settings, spec: RepoSpec, *, base: str, head: str) -> list[FileChange]:
    """``base...head`` 之间改了哪些文件 —— **PR 的 diff 就是这个**。

    三点（``...``）而不是两点：PR 讲的是「相对共同祖先改了什么」，两点式会把 base 分支上
    别人新提交的内容也算进来。

    **只在本地缓存里算，不联网、也不看沙箱**（2026-10-14 用户口径）：裸仓里有对象就够，
    调用方负责先 ``sync()`` 把远端的新提交 fetch 下来。缓存里没有这个仓库时抛
    ``RepoViewError``（调用方决定是提示还是先 clone）。
    """
    directory = await _require_cloned(settings, spec)
    base_ref = await _resolve_ref(settings, spec, base)
    head_ref = await _resolve_ref(settings, spec, head)
    git = require_git(settings)
    timeout = settings.repo_view_timeout_seconds
    counts = await _text(
        [git, "diff", "--numstat", f"{base_ref}...{head_ref}"],
        cwd=directory,
        timeout=timeout,
        spec=spec,
    )
    codes = await _text(
        [git, "diff", "--name-status", f"{base_ref}...{head_ref}"],
        cwd=directory,
        timeout=timeout,
        spec=spec,
    )
    return _merge_changes(counts, codes)


async def file_diff(
    settings: Settings, spec: RepoSpec, *, base: str, head: str, path: str
) -> str:
    """一个文件的 diff 正文（``base...head``，只看这一个路径）。

    路径先过 ``clean_path``（与读文件同一个守卫，``../`` 之类在这里就被挡掉）；正文大小由
    调用方截断 —— 这里只负责取。
    """
    safe = clean_path(path)
    directory = await _require_cloned(settings, spec)
    base_ref = await _resolve_ref(settings, spec, base)
    head_ref = await _resolve_ref(settings, spec, head)
    git = require_git(settings)
    return await _text(
        [git, "diff", f"{base_ref}...{head_ref}", "--", safe],
        cwd=directory,
        timeout=settings.repo_view_timeout_seconds,
        spec=spec,
    )


# --------------------------------------------------------------------------- git


def find_git(settings: Settings) -> str | None:
    """找 git：先看配置，再看 PATH，最后看 Windows 的常见安装位置。找不到返回 ``None``。"""
    configured = settings.repo_view_git.strip()
    if configured:
        return configured
    found = shutil.which("git")
    if found:
        return found
    for hint in _WINDOWS_GIT_HINTS:
        if Path(hint).is_file():
            return hint
    return None


def require_git(settings: Settings) -> str:
    """要 git，没有就给一句用户能照着做的错误。"""
    git = find_git(settings)
    if git is None:
        raise GitMissingError(
            "本机没找到 git，看不了仓库代码：装一个 git 并确保它在 PATH 上，"
            "或用 REPO_VIEW_GIT 指到 git 可执行文件。"
        )
    return git


# --------------------------------------------------------------------------- 路径


def cache_root(settings: Settings) -> Path:
    """缓存根目录：配置里的相对路径按**进程的工作目录**解析成**绝对路径**。

    必须绝对 —— 下面每条 git 命令都是「换 ``cwd`` 再跑」，把相对路径当目标传进去，git 会
    相对那个 cwd **再解析一次**。实测过一次：`REPO_VIEW_DIR` 给了相对路径 `repo_cache` 时，
    克隆落到了 `repo_cache/<owner>/repo_cache/<owner>/<repo>`，而随后的 `HEAD` 检查按
    cwd 找不到它、判定「还没克隆」，下一次克隆就报
    「destination path … already exists and is not an empty directory」（两处都对，合起来错）。
    """
    return Path(settings.repo_view_dir).expanduser().resolve()


def _remove_tree(path: Path) -> None:
    """删掉这棵目录树，**必须真删掉**；删不掉就抛可读错误，而不是继续往下跑。

    Windows 上 git 的打包对象是只读的（实测属性是 ``-ar--``），
    ``shutil.rmtree(..., ignore_errors=True)`` 会**静默失败** —— 留下半个仓库，下一次
    `git clone` 就报那句「already exists and is not an empty directory」。所以先整棵清掉
    只读位，再删；仍然删不掉时把路径写进错误里，让人手动处理。
    """
    if not path.exists():
        return
    try:
        for target in (path, *path.rglob("*")):
            try:
                os.chmod(target, stat.S_IWRITE)
            except OSError:
                pass
    except OSError:
        pass
    try:
        shutil.rmtree(path)
    except OSError as exc:
        raise RepoViewError(
            f"清不掉旧的仓库缓存（{path}：{exc}）—— 手动删掉这个目录，再点一次「同步」"
        ) from exc


def repo_dir(settings: Settings, owner: str, repo: str) -> Path:
    """这个仓库的裸仓目录。名字先过一遍字符集，不拿未校验的字符串去拼路径。"""
    for value in (owner, repo):
        if not _NAME_OK.match(value) or value in (".", ".."):
            raise RepoViewError(f"仓库名不合法：{value!r}")
    return cache_root(settings) / owner / repo


def _state_path(settings: Settings, owner: str, repo: str) -> Path:
    return repo_dir(settings, owner, repo).with_name(f"{repo}{_STATE_SUFFIX}")


def is_cloned(settings: Settings, spec: RepoSpec) -> bool:
    return (repo_dir(settings, spec.owner, spec.repo) / "HEAD").is_file()


def fetched_at(settings: Settings, spec: RepoSpec) -> str | None:
    """上次**成功**同步的时间（没记录过就是 ``None``）。"""
    try:
        raw = json.loads(_state_path(settings, spec.owner, spec.repo).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    stamp = raw.get("fetched_at") if isinstance(raw, dict) else None
    return stamp if isinstance(stamp, str) else None


def _write_state(settings: Settings, spec: RepoSpec, *, cloned: bool) -> None:
    """记一次成功同步。写失败只记日志 —— 它只是「上次什么时候同步的」这条展示信息。"""
    path = _state_path(settings, spec.owner, spec.repo)
    previous: dict[str, Any] = {}
    try:
        loaded = json.loads(path.read_text("utf-8"))
        if isinstance(loaded, dict):
            previous = loaded
    except (OSError, ValueError):
        pass
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    payload = {
        **previous,
        "url": spec.url,
        "fetched_at": now,
        "cloned_at": now if cloned else previous.get("cloned_at"),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    except OSError as exc:
        logger.warning("同步状态没写进去（不影响浏览）：%s", exc)


# --------------------------------------------------------------------------- 环境


def _scrub(text: str, spec: RepoSpec | None) -> str:
    """把令牌（含 base64 形式）从要外发 / 落日志的文本里抹掉。"""
    if spec is None or not spec.token:
        return text
    out = text.replace(spec.token, "***")
    basic = base64.b64encode(f"{spec.git_username}:{spec.token}".encode()).decode()
    return out.replace(basic, "***")


def _auth_env(spec: RepoSpec) -> dict[str, str]:
    """把令牌经**环境里的 git 配置**注入（不进 URL / argv / ``.git/config``）。

    用 ``GIT_CONFIG_*``（git ≥ 2.31）而不是 ``-c``：``-c`` 的值会出现在命令行参数里，
    同机器上的其他进程读得到；环境变量只属于这个子进程，范围更小。
    """
    if not spec.token:
        return {}
    host = urlparse(spec.url).netloc or spec.platform.host
    basic = base64.b64encode(f"{spec.git_username}:{spec.token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"http.https://{host}/.extraheader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
    }


def _env(spec: RepoSpec | None) -> dict[str, str]:
    env = dict(os.environ)
    # 私有仓库缺凭据时**直接失败**：git 默认会去开终端提示，那在服务进程里就是挂住
    env["GIT_TERMINAL_PROMPT"] = "0"
    if spec is not None:
        env.update(_auth_env(spec))
    return env


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def _tail(text: str, limit: int = 400) -> str:
    text = text.strip()
    return text if len(text) <= limit else "…" + text[-limit:]


async def _run(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout: float,
    spec: RepoSpec | None = None,
) -> bytes:
    """跑一条 git 命令，返回 stdout（bytes）。失败抛 ``RepoViewError``（已脱敏）。

    **走线程里的 ``subprocess.run``，不用 ``asyncio.create_subprocess_exec``** —— 后者要求
    事件循环支持子进程，而 Windows 上它只有 ProactorEventLoop 支持；本项目的后端在 Windows
    上**故意**用 ``SelectorEventLoop``（psycopg 异步的要求，见 ``main.selector_loop_factory``），
    于是 ``create_subprocess_exec`` 会直接抛 ``NotImplementedError`` —— 实测过一次：面板点
    「同步」就是 500，日志里一行 ``NotImplementedError``。线程方案与事件循环无关，两条平台
    同一条代码路径。
    """
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            list(argv),
            cwd=str(cwd),
            env=_env(spec),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        step = argv[1] if len(argv) > 1 else ""
        raise RepoViewError(f"git 命令超时（{timeout:.0f}s）：{step}") from None
    except OSError as exc:  # git 不在、不可执行、名字其实是个目录……
        raise RepoViewError(f"git 启动失败：{_scrub(str(exc), spec)}") from exc
    if completed.returncode != 0:
        detail = _tail(_decode(completed.stderr)) or f"git 退出码 {completed.returncode}"
        raise RepoViewError(_scrub(detail, spec))
    return completed.stdout


async def _text(
    argv: Sequence[str], *, cwd: Path, timeout: float, spec: RepoSpec | None = None
) -> str:
    return _decode(await _run(argv, cwd=cwd, timeout=timeout, spec=spec)).strip()


# --------------------------------------------------------------------------- 同步

#: 同一仓库的 clone / fetch 互斥（读操作不参与：见模块说明里的「ref 原子更新」）
_sync_locks: dict[str, asyncio.Lock] = {}


def _lock_for(key: str) -> asyncio.Lock:
    lock = _sync_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _sync_locks[key] = lock
    return lock


#: 显式 refspec：``git clone --bare`` **不写** ``remote.origin.fetch``（本机实测为空），
#: 不带 refspec 的 ``git fetch`` 只会更新 ``FETCH_HEAD`` —— 分支列表会永远停在克隆那一刻。
_FETCH_REFSPEC = "+refs/heads/*:refs/heads/*"


async def sync(settings: Settings, spec: RepoSpec) -> SyncResult:
    """克隆或更新这个仓库的缓存：没有就 clone，有就 fetch。失败不留半成品。"""
    git = require_git(settings)
    directory = repo_dir(settings, spec.owner, spec.repo)
    timeout = settings.repo_view_timeout_seconds
    async with _lock_for(f"{spec.owner}/{spec.repo}"):
        cloned = not is_cloned(settings, spec)
        directory.parent.mkdir(parents=True, exist_ok=True)
        if cloned:
            # 上一次克隆崩在半路留下的目录会让这次 clone 直接报 already exists，
            # 所以先清掉 —— 删的是我们自己这一份缓存，不碰别处
            _remove_tree(directory)
            logger.info("开始下载仓库缓存：%s/%s", spec.owner, spec.repo)
            try:
                await _run(
                    [git, "clone", "--bare", spec.url, str(directory)],
                    cwd=directory.parent,
                    timeout=timeout,
                    spec=spec,
                )
            except RepoViewError:
                # 清掉这次留下的半成品（同样得能真删掉只读的 git 对象）；清不掉只记日志 ——
                # 要报给用户的是原始错误（认证失败 / 网络不通），不是清理失败
                try:
                    _remove_tree(directory)
                except RepoViewError as cleanup_exc:
                    logger.warning("克隆失败后的清理也没成：%s", cleanup_exc)
                raise
        else:
            # 显式 refspec + --prune：远程删掉的分支，缓存里也跟着消失
            await _run(
                [git, "fetch", "--prune", "origin", _FETCH_REFSPEC],
                cwd=directory,
                timeout=timeout,
                spec=spec,
            )
        default = await default_branch(settings, spec)
        branches = await list_branches(settings, spec, default=default)
        _write_state(settings, spec, cloned=cloned)
    logger.info(
        "仓库缓存已同步：%s/%s（%s，%d 个分支）",
        spec.owner,
        spec.repo,
        "克隆" if cloned else "更新",
        len(branches),
    )
    return SyncResult(cloned=cloned, default_branch=default, branches=branches)


async def default_branch(settings: Settings, spec: RepoSpec) -> str:
    """代码面板默认打开哪个分支。

    优先级：**设置页配的那个**（按平台分：Gitee / GitHub）→ 裸仓 ``HEAD`` 探测到的远程默认分支
    → ``master``。2026-10-14 起可配（用户口径：代码缓存配置里的「默认分支」）—— 配置留空就回到
    探测。**配错会打开一个不存在的分支**，所以界面上那一格写明了「留空＝按远程默认」。
    """
    configured = settings.default_branch_for(spec.platform).strip()
    if configured:
        return configured
    directory = repo_dir(settings, spec.owner, spec.repo)
    name = await _text(
        [require_git(settings), "symbolic-ref", "--short", "HEAD"],
        cwd=directory,
        timeout=30,
        spec=spec,
    )
    return name or "master"


async def list_branches(
    settings: Settings, spec: RepoSpec, *, default: str | None = None
) -> list[BranchInfo]:
    """缓存里的所有分支（**不联网**）：默认分支排最前，其余按名字升序 —— 顺序稳定好对照。"""
    if not is_cloned(settings, spec):
        return []
    directory = repo_dir(settings, spec.owner, spec.repo)
    if default is None:
        try:
            default = await default_branch(settings, spec)
        except RepoViewError:
            default = ""
    out = await _text(
        [
            require_git(settings),
            "for-each-ref",
            "--format=%(refname:short)%09%(objectname:short)%09%(committerdate:iso-strict)%09%(subject)",
            "refs/heads/",
        ],
        cwd=directory,
        timeout=30,
        spec=spec,
    )
    branches: list[BranchInfo] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or not parts[0]:
            continue
        branches.append(
            BranchInfo(
                name=parts[0],
                sha=parts[1],
                updated_at=parts[2] if len(parts) > 2 else "",
                subject=parts[3] if len(parts) > 3 else "",
                is_default=parts[0] == default,
            )
        )
    branches.sort(key=lambda item: (not item.is_default, item.name))
    return branches


# --------------------------------------------------------------------------- 读


def clean_path(path: str) -> str:
    """把前端给的路径规整成 git 认的相对路径；``..`` / 绝对路径 / 反斜杠一律拒绝。"""
    if "\\" in path:
        raise RepoViewError("路径里不能有反斜杠")
    raw = path.strip()
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise RepoViewError("请用仓库内的相对路径")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise RepoViewError("路径里不能有 `..`")
    if any("\x00" in part for part in parts):
        raise RepoViewError("路径里有非法字符")
    return "/".join(parts)


async def _require_cloned(settings: Settings, spec: RepoSpec) -> Path:
    if not is_cloned(settings, spec):
        raise RepoViewError("这个仓库还没下载到本机，先点「同步」下载一次")
    return repo_dir(settings, spec.owner, spec.repo)


async def _resolve_ref(settings: Settings, spec: RepoSpec, ref: str) -> str:
    """``ref`` 必须是缓存里真有的一支分支：既挡住不存在的分支，也挡住 ``-`` 开头的参数注入。"""
    wanted = ref.strip()
    if not wanted:
        return await default_branch(settings, spec)
    if wanted.startswith("-"):
        raise RepoViewError("分支名不合法")
    names = {branch.name for branch in await list_branches(settings, spec)}
    if wanted not in names:
        raise RepoViewError(f"缓存里没有分支 {wanted}（先同步一次再试）")
    return wanted


async def tree(
    settings: Settings, spec: RepoSpec, *, ref: str = "", path: str = ""
) -> list[TreeEntry]:
    """列一层目录。``-z`` 分隔记录、记录内以 TAB 分隔元信息与名字（名字带空格/中文也稳）。"""
    directory = await _require_cloned(settings, spec)
    target_ref = await _resolve_ref(settings, spec, ref)
    clean = clean_path(path)
    target = f"{target_ref}:{clean}" if clean else target_ref
    raw = _decode(
        await _run(
            [
                require_git(settings),
                "-c",
                "core.quotepath=false",
                "ls-tree",
                "-l",
                "-z",
                target,
            ],
            cwd=directory,
            timeout=60,
            spec=spec,
        )
    )
    entries: list[TreeEntry] = []
    for record in raw.split("\0"):
        if not record:
            continue
        meta, _, name = record.partition("\t")
        fields = meta.split()
        if len(fields) < 4 or not name:
            continue
        kind = fields[1]
        size = fields[3]
        entries.append(
            TreeEntry(
                name=name,
                path=f"{clean}/{name}" if clean else name,
                # ls-tree 的 type 是 blob / tree / commit；换成前端那套 file / dir / submodule
                type={"blob": "file", "tree": "dir", "commit": "submodule"}.get(kind, kind),
                size=None if size == "-" else int(size),
            )
        )
    # 目录在前、同类按名字（文件浏览器该有的样子）
    entries.sort(key=lambda item: (item.type != "dir", item.name.lower()))
    return entries


async def read_file(
    settings: Settings, spec: RepoSpec, *, ref: str = "", path: str = ""
) -> FileContent:
    """读一个文件的正文；太大 / 二进制只回标记，不把内容塞进 JSON。"""
    directory = await _require_cloned(settings, spec)
    target_ref = await _resolve_ref(settings, spec, ref)
    clean = clean_path(path)
    if not clean:
        raise RepoViewError("没给文件路径")
    git = require_git(settings)
    target = f"{target_ref}:{clean}"
    try:
        kind = await _text([git, "cat-file", "-t", target], cwd=directory, timeout=60, spec=spec)
    except RepoViewError as exc:
        raise RepoViewError(f"`{clean}` 不在 {target_ref} 里") from exc
    if kind == "tree":
        raise RepoViewError(f"`{clean}` 是个目录，展开它看里面的文件")
    if kind != "blob":
        raise RepoViewError(f"`{clean}` 不是普通文件（{kind}）")
    size = int(
        await _text([git, "cat-file", "-s", target], cwd=directory, timeout=60, spec=spec) or "0"
    )
    if size > settings.repo_view_file_max_bytes:
        return FileContent(path=clean, ref=target_ref, size=size, too_big=True)
    raw = await _run([git, "cat-file", "blob", target], cwd=directory, timeout=60, spec=spec)
    # 二进制最简单可靠的判据就是 NUL：文本文件里基本不会出现（UTF-16 除外，那本来也不该看）
    if b"\x00" in raw[:8000]:
        return FileContent(path=clean, ref=target_ref, size=size, binary=True)
    return FileContent(
        path=clean, ref=target_ref, text=raw.decode("utf-8", errors="replace"), size=size
    )
