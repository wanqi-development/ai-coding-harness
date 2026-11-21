"""Normalize Git commands and classify them as allow / approval / deny.

This is intentionally a policy engine, not a sandbox. It prevents accidental
destructive commands issued by the agent. The shell sandbox remains responsible
for adversarial isolation.
"""

from __future__ import annotations

import os
import posixpath
import shlex
from dataclasses import dataclass
from typing import Literal

DecisionLevel = Literal["allow", "approval", "deny"]

_LEVEL_ORDER: dict[DecisionLevel, int] = {"allow": 0, "approval": 1, "deny": 2}


@dataclass(frozen=True)
class GitPolicyDecision:
    level: DecisionLevel
    action: str = ""
    reason: str = ""
    risk: str = ""
    targets: tuple[str, ...] = ()
    preview_commands: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ParsedGit:
    raw: str
    argv: tuple[str, ...]
    subcommand: str
    args: tuple[str, ...]


def _segments(text: str) -> list[str]:
    """Split shell operators while preserving quoted separators."""
    out: list[str] = []
    current: list[str] = []
    quote = ""
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
            current.append(char)
        elif char == "\\" and index + 1 < len(text):
            current.extend((char, text[index + 1]))
            index += 1
        elif char in ";&|\n":
            out.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    out.append("".join(current))
    return [part.strip() for part in out if part.strip()]


def _tokens(segment: str) -> tuple[str, ...]:
    try:
        return tuple(shlex.split(segment, posix=True))
    except ValueError:
        # Incomplete quoting cannot be normalized safely. Treat it as opaque so
        # the policy can fail closed if the segment mentions Git.
        return ()


def _skip_prefix(tokens: tuple[str, ...]) -> tuple[str, ...]:
    index = 0
    while index < len(tokens) and (
        "=" in tokens[index]
        and tokens[index].split("=", 1)[0].replace("_", "").isalnum()
    ):
        index += 1
    while index < len(tokens) and tokens[index] in {"command", "nohup", "time"}:
        index += 1
    if index < len(tokens) and tokens[index] == "env":
        index += 1
        while index < len(tokens) and (
            tokens[index].startswith("-")
            or (
                "=" in tokens[index]
                and tokens[index].split("=", 1)[0].replace("_", "").isalnum()
            )
        ):
            index += 1
    return tokens[index:]


def _nested_commands(segment: str) -> list[str]:
    tokens = _skip_prefix(_tokens(segment))
    if not tokens:
        return []
    if tokens[0] in {"sh", "bash", "zsh", "dash"} and "-c" in tokens[1:]:
        index = tokens.index("-c", 1)
        if index + 1 < len(tokens):
            return [tokens[index + 1]]
    return [segment]


def _parse_git(segment: str) -> _ParsedGit | None:
    tokens = _skip_prefix(_tokens(segment))
    if not tokens or tokens[0] != "git":
        return None
    index = 1
    prefix: list[str] = ["git"]
    while index < len(tokens) and tokens[index].startswith("-"):
        token = tokens[index]
        prefix.append(token)
        if token in {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}:
            if index + 1 >= len(tokens):
                return None
            prefix.append(tokens[index + 1])
            index += 2
        elif token.startswith(("--git-dir=", "--work-tree=", "--namespace=")):
            index += 1
        else:
            index += 1
    if index >= len(tokens):
        return None
    prefix.extend(tokens[index:])
    return _ParsedGit(
        raw=segment,
        argv=tuple(prefix),
        subcommand=tokens[index],
        args=tokens[index + 1 :],
    )


def _normalise_ref(value: str) -> str:
    ref = value.strip()
    ref = ref.removeprefix("+")
    ref = ref.removeprefix(":")
    ref = ref.removeprefix("refs/heads/")
    if "/" in ref and ref.split("/", 1)[0] in {"origin", "upstream"}:
        ref = ref.split("/", 1)[1]
    return ref


def _is_protected(value: str, protected: set[str]) -> bool:
    return _normalise_ref(value) in protected


def _path_inside(value: str, repo_root: str) -> bool:
    if value.startswith("~"):
        return False
    root = posixpath.normpath(repo_root)
    target = posixpath.normpath(
        value if value.startswith("/") else posixpath.join(root, value)
    )
    return target == root or target.startswith(root.rstrip("/") + "/")


def _path_operands(args: tuple[str, ...]) -> tuple[str, ...]:
    if "--" in args:
        return tuple(args[args.index("--") + 1 :])
    return tuple(
        token
        for token in args
        if not token.startswith("-")
        and ("/" in token or token.startswith(".") or "." in token)
    )


def _has_force_flag(args: tuple[str, ...]) -> bool:
    return any(
        token in {"-f", "--force", "--force-with-lease"}
        or token.startswith("--force-with-lease=")
        or (token.startswith("-") and not token.startswith("--") and "f" in token[1:])
        for token in args
    )


def _deny(
    reason: str,
    *,
    action: str,
    risk: str = "",
    targets: tuple[str, ...] = (),
) -> GitPolicyDecision:
    return GitPolicyDecision("deny", action, reason, risk, targets)


def _approval(
    reason: str,
    *,
    action: str,
    risk: str,
    targets: tuple[str, ...] = (),
    preview_commands: tuple[str, ...] = (),
) -> GitPolicyDecision:
    return GitPolicyDecision(
        "approval",
        action,
        reason,
        risk,
        targets,
        preview_commands,
    )


def _evaluate_parsed(
    parsed: _ParsedGit,
    *,
    repo_root: str,
    protected_branches: set[str],
) -> GitPolicyDecision:
    argv = parsed.argv
    action = parsed.subcommand
    args = parsed.args

    for index, token in enumerate(argv):
        if token in {"--git-dir", "--work-tree"}:
            return _deny(
                f"{token} 指向另一个仓库或工作区，Agent 不允许切换仓库根",
                action=action,
                risk="external_repository",
            )
        if token.startswith(("--git-dir=", "--work-tree=")):
            return _deny(
                f"{token} 指向另一个仓库或工作区，Agent 不允许切换仓库根",
                action=action,
                risk="external_repository",
            )
        if token == "-C" and index + 1 < len(argv):
            target = argv[index + 1]
            if target.startswith("/") and not _path_inside(target, repo_root):
                return _deny(
                    f"git -C 指向仓库外路径：{target}",
                    action=action,
                    risk="external_repository",
                )

    if "--no-verify" in args:
        return _deny("不允许绕过仓库 hooks", action=action, risk="hook_bypass")

    paths = _path_operands(args)
    for path in paths:
        if not _path_inside(path, repo_root):
            return _deny(
                f"Git 目标路径逃出仓库：{path}",
                action=action,
                risk="external_path",
                targets=(path,),
            )

    if action in {"filter-branch", "filter-repo", "replace"}:
        return _deny(
            "整体 Git 历史改写命令只能由用户手工执行",
            action=action,
            risk="history_rewrite",
        )
    if action == "update-ref":
        return _deny(
            "update-ref 可绕过正常 Git 引用更新流程",
            action=action,
            risk="reference_rewrite",
        )
    if action == "reflog" and any(token in {"delete", "expire"} for token in args):
        return _deny(
            "reflog delete/expire 会破坏 Git 恢复路径",
            action=action,
            risk="recovery_destruction",
        )
    if action == "gc" and any(
        token == "--prune=now" or token.startswith("--prune=")
        for token in args
    ):
        return _deny(
            "gc --prune 会主动销毁不可达对象",
            action=action,
            risk="recovery_destruction",
        )
    if action == "prune":
        return _deny(
            "git prune 会销毁不可达对象",
            action=action,
            risk="recovery_destruction",
        )

    if action == "config":
        if any(token in {"--global", "--system"} for token in args):
            return _deny(
                "不允许修改全局或系统 Git 配置",
                action=action,
                risk="global_config",
            )
        return _approval(
            "修改仓库 Git 配置会影响后续所有 Git 操作",
            action=action,
            risk="repository_config",
        )

    if action == "push":
        refs = tuple(token for token in args if not token.startswith("-"))
        if "--mirror" in args or "--all" in args:
            return _deny(
                "镜像或全量远端推送可能批量覆盖/删除 ref",
                action=action,
                risk="remote_mirror",
                targets=refs,
            )
        if any("*" in token for token in refs):
            return _deny(
                "远端 ref 使用通配符，影响范围不可枚举",
                action=action,
                risk="remote_wildcard",
                targets=refs,
            )
        deleting = "--delete" in args or any(
            token.startswith(":") or token.endswith(":")
            for token in refs
        )
        if deleting:
            if any(_is_protected(token, protected_branches) for token in refs):
                return _deny(
                    "不允许删除默认/受保护分支",
                    action=action,
                    risk="protected_branch_delete",
                    targets=refs,
                )
            return _approval(
                "将删除远端分支或标签",
                action=action,
                risk="remote_delete",
                targets=refs,
            )
        if any(token == "--force" or token == "-f" for token in args):
            return _deny(
                "裸 force push 被禁止；改写后的任务分支请使用 --force-with-lease",
                action=action,
                risk="force_push",
                targets=refs,
            )
        if any(
            token.startswith("--force-with-lease")
            or (
                token.startswith("-")
                and not token.startswith("--")
                and "f" in token[1:]
            )
            for token in args
        ):
            if any(_is_protected(token, protected_branches) for token in refs):
                return _deny(
                    "不允许强推默认/受保护分支",
                    action=action,
                    risk="protected_branch_force",
                    targets=refs,
                )
            return _approval(
                "将强制更新远端任务分支",
                action=action,
                risk="force_push",
                targets=refs,
            )
        return GitPolicyDecision("allow", action)

    if action == "clean":
        if _has_force_flag(args):
            preview = (f"git clean -ndx {_quote_paths(paths)}".strip(),)
            return _approval(
                "将删除未跟踪文件"
                + ("（包含 ignored 文件）" if any("x" in token for token in args if token.startswith("-")) else ""),
                action=action,
                risk="delete_untracked",
                targets=paths,
                preview_commands=preview,
            )
        return GitPolicyDecision("allow", action)

    if action == "reset":
        destructive = any(
            token in {"--hard", "--keep", "--merge"} for token in args
        )
        if destructive:
            return _approval(
                "将移动分支引用并可能覆盖本地改动",
                action=action,
                risk="discard_worktree",
                targets=paths,
            )
        return GitPolicyDecision("allow", action)

    if action in {"checkout", "switch"}:
        force = any(token in {"-f", "--force", "--discard-changes"} for token in args)
        if force:
            return _approval(
                "将强制切换分支并覆盖本地改动",
                action=action,
                risk="discard_worktree",
                targets=paths,
            )
        discard_paths = tuple(
            path
            for path in paths
            if (
                "--" in args
                or path == "."
                or path.startswith("./")
                or "." in posixpath.basename(path)
            )
        )
        if action == "checkout" and discard_paths:
            return _approval(
                "将用索引/HEAD 内容覆盖指定文件",
                action=action,
                risk="overwrite_files",
                targets=discard_paths,
            )
        return GitPolicyDecision("allow", action)

    if action == "restore":
        return _approval(
            "将用索引/HEAD/指定版本覆盖工作区文件",
            action=action,
            risk="overwrite_files",
            targets=paths,
        )

    if action == "branch" and any(token in {"-D", "--delete", "--force"} for token in args):
        names = tuple(token for token in args if not token.startswith("-"))
        if any(_is_protected(token, protected_branches) for token in names):
            return _deny(
                "不允许删除默认/受保护分支",
                action=action,
                risk="protected_branch_delete",
                targets=names,
            )
        return _approval(
            "将强制删除本地分支",
            action=action,
            risk="branch_delete",
            targets=names,
        )

    if action == "tag" and "-d" in args:
        return _approval(
            "将删除本地标签",
            action=action,
            risk="tag_delete",
            targets=tuple(token for token in args if not token.startswith("-")),
        )

    if action == "stash" and any(token in {"drop", "clear"} for token in args[:1]):
        return _approval(
            "将丢弃 stash 保存的改动",
            action=action,
            risk="stash_delete",
        )

    if action in {"rebase"} or (
        action == "commit" and "--amend" in args
    ):
        return _approval(
            "将改写当前分支历史",
            action=action,
            risk="history_rewrite",
        )

    if action == "worktree" and args[:1] == ("remove",) and "--force" in args:
        return _approval(
            "将强制删除 worktree",
            action=action,
            risk="worktree_delete",
        )

    if action == "submodule" and args[:1] == ("deinit",) and any(
        token in {"-f", "--force"} for token in args
    ):
        return _approval(
            "将强制移除子模块工作区",
            action=action,
            risk="submodule_delete",
        )

    return GitPolicyDecision("allow", action)


def _quote_paths(paths: tuple[str, ...]) -> str:
    return " ".join(shlex.quote(path) for path in paths)


def evaluate_git_command(
    command: str,
    *,
    repo_root: str,
    protected_branches: tuple[str, ...] = ("main", "master"),
) -> GitPolicyDecision:
    """Classify all Git segments in a shell command by highest risk."""
    protected = {item.strip() for item in protected_branches if item.strip()}
    root = posixpath.normpath(repo_root)
    current_root = root
    best = GitPolicyDecision("allow")
    seen_git = False

    pending = list(_segments(command))
    while pending:
        segment = pending.pop(0)
        for nested in _nested_commands(segment):
            if nested != segment:
                pending.extend(_segments(nested))
                continue
            segment_tokens = _skip_prefix(_tokens(segment))
            if segment_tokens and segment_tokens[0] == "cd":
                target = next(
                    (
                        token
                        for token in segment_tokens[1:]
                        if not token.startswith("-")
                    ),
                    "",
                )
                if target and any(ch in target for ch in "$`") and "git" in command:
                    return _deny(
                        "无法可靠解析 cd 目标，不能在未知目录执行 Git",
                        action="opaque_cwd",
                        risk="external_repository",
                    )
                if target and not any(ch in target for ch in "$`"):
                    current_root = posixpath.normpath(
                        target
                        if target.startswith("/")
                        else posixpath.join(current_root, target)
                    )
                    if not _path_inside(current_root, root):
                        return _deny(
                            f"命令切换到仓库外目录后执行 Git：{current_root}",
                            action="external_repository",
                            risk="external_repository",
                        )
                continue
            parsed = _parse_git(segment)
            if parsed is None:
                if "git " in segment or segment.startswith("git"):
                    return _deny(
                        "包含无法可靠解析的 Git 命令",
                        action="unknown",
                        risk="opaque_command",
                    )
                continue
            seen_git = True
            decision = _evaluate_parsed(
                parsed,
                repo_root=current_root,
                protected_branches=protected,
            )
            if _LEVEL_ORDER[decision.level] > _LEVEL_ORDER[best.level]:
                best = decision
            if best.level == "deny":
                return best
    return best if seen_git else GitPolicyDecision("allow")


def configured_protected_branches() -> tuple[str, ...]:
    raw = os.getenv("GIT_PROTECTED_BRANCHES", "main,master")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


__all__ = [
    "DecisionLevel",
    "GitPolicyDecision",
    "configured_protected_branches",
    "evaluate_git_command",
]
