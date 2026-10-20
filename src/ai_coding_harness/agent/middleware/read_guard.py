"""读文件红线：`policies` 与项目代码都不许整文件读。

2026-10-20 的账：coder 一轮 67 次 `read_file`（52 个文件）、131 次调用、1519 万输入。
提示词只能劝；这一层在**执行前**拦：

- `read_file`：必须显式给 `limit`；`/ai_coding/policies` ≤ 80 行，`/ai_coding/project`
  ≤ 200 行。不给 `limit`（工具默认 100 行，可能正好是整个文件）一律拦。
- `execute`：`cat` / `less` / `more` / `nl` / `tac` 读受保护目录一律拦；`head` / `tail`
  的 `-n` 超过上限也拦；`sed -n '1,$p'` 这种整段输出也拦。允许 `sed -n '1,60p'`、
  `head -n 60`、`grep`。

路径解析按命令里的 `cd` 逐段跟踪；相对路径按当时的 cwd 解析。含 `$` 的路径参数
（`cat $f`、`cat $(find ...)`）在受保护目录下无法核实，按“可能读全文”拦掉。

**这不是沙箱**：`python -c "print(open(...).read())"`、`awk` 整文件打印这类任意脚本
仍能绕过。这一层封的是模型最常用的整文件读入口，不是防恶意。
"""

from __future__ import annotations

import posixpath
import shlex
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

#: 受保护目录与单次读取上限（行）。policies 是规则原文，只允许按小节看；
#: project 是业务代码，按函数 / 片段看。
POLICY_ROOT = "/ai_coding/policies"
PROJECT_ROOT = "/ai_coding/project"
POLICY_MAX_LINES = 80
CODE_MAX_LINES = 200

#: 整文件 dump 类命令：在受保护目录下一律不许用。
WHOLE_FILE_COMMANDS = frozenset({"cat", "less", "more", "nl", "tac"})
#: 可以带前缀、不影响命令判定的包装词。
_PREFIXES = frozenset({"sudo", "env", "time", "command", "builtin"})
_SEPARATORS = frozenset({";", "&&", "||", "|"})


class ReadGuardMiddleware(AgentMiddleware):
    """在读文件 / 执行整文件命令之前拦下，并给出可执行的替代写法。"""

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        call = request.tool_call
        name = str(call.get("name") or "")
        args = call.get("args")
        args = args if isinstance(args, Mapping) else {}

        reason = None
        if name == "read_file":
            reason = _read_file_violation(args)
        elif name == "execute":
            reason = _execute_violation(args)
        if reason is None:
            return await handler(request)

        return ToolMessage(
            content=reason,
            name=name,
            tool_call_id=call.get("id") or "",
            status="error",
        )


def _rule_for(path: str) -> tuple[str, int] | None:
    """路径落在哪个受保护目录、单次上限多少行；不受保护的返回 ``None``。"""
    normalized = posixpath.normpath(path)
    if normalized == POLICY_ROOT or normalized.startswith(POLICY_ROOT + "/"):
        return POLICY_ROOT, POLICY_MAX_LINES
    if normalized == PROJECT_ROOT or normalized.startswith(PROJECT_ROOT + "/"):
        return PROJECT_ROOT, CODE_MAX_LINES
    return None


def _read_file_violation(args: Mapping[str, Any]) -> str | None:
    path = args.get("file_path")
    if not isinstance(path, str):
        return None
    rule = _rule_for(path)
    if rule is None:
        return None
    root, cap = rule
    limit = args.get("limit")
    if isinstance(limit, str) and limit.isdigit():
        limit = int(limit)
    if not isinstance(limit, int) or isinstance(limit, bool):
        return (
            f"读 `{root}` 下的文件必须显式给 `limit`（≤{cap} 行），不能整文件读。"
            f"例如 `read_file(file_path=..., offset=0, limit=60)`；本次未执行。"
        )
    if limit <= 0 or limit > cap:
        return (
            f"`{root}` 下单次最多读 {cap} 行（这次 limit={limit}）。"
            "缩小 limit，或者用 offset 分段读；本次未执行。"
        )
    return None


def _execute_violation(args: Mapping[str, Any]) -> str | None:
    command = args.get("command")
    if not isinstance(command, str):
        return None
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="&|;()<>")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None

    cwd = "/ai_coding"
    for segment in _segments(tokens):
        words = [word for word in segment if word not in _SEPARATORS]
        if not words:
            continue
        while words and words[0] in _PREFIXES:
            words = words[1:]
        if not words:
            continue
        if words[0] == "cd" and len(words) > 1:
            cwd = _resolve(cwd, words[1])
            continue

        protected = [
            (token, rule)
            for token in words[1:]
            if not token.startswith("-")
            and (rule := _rule_for(_resolve(cwd, token))) is not None
        ]
        if not protected:
            continue

        first = posixpath.basename(words[0])
        if first in WHOLE_FILE_COMMANDS:
            root, cap = protected[0][1]
            return (
                f"不允许用 `{first}` 读 `{root}` 下的文件（整文件读）。改用 "
                f"`sed -n '1,{cap}p' <文件>` 或 `head -n {cap} <文件>`；"
                "要看别处就用 offset / 行号范围。本次未执行。"
            )
        if first in {"head", "tail"}:
            count = _line_count(words)
            cap = protected[0][1][1]
            if count is not None and count > cap:
                return (
                    f"`{first} -n {count}` 超过 `{protected[0][1][0]}` 的单次上限 {cap} 行；"
                    "缩小行数或用行号范围读。本次未执行。"
                )
        if first == "sed" and _sed_reads_to_end(words):
            cap = protected[0][1][1]
            return (
                f"`sed -n '1,$p'` 等于整文件读；`{protected[0][1][0]}` 下请给明确的"
                f"起止行（≤{cap} 行）。本次未执行。"
            )
    return None


def _segments(tokens: list[str]) -> list[list[str]]:
    """按 ``;`` / ``&&`` / ``||`` / ``|`` 切段（管道两侧各自判定）。"""
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _SEPARATORS:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def _resolve(cwd: str, path: str) -> str:
    if path.startswith("/"):
        return posixpath.normpath(path)
    return posixpath.normpath(posixpath.join(cwd, path))


def _line_count(words: list[str]) -> int | None:
    """``head -n 50`` / ``head -50`` 里那个 50；没写行数返回 ``None``（默认 10）。"""
    for index, word in enumerate(words[1:], start=1):
        if word == "-n" and index + 1 < len(words):
            value = words[index + 1]
            return int(value) if value.isdigit() else None
        if word.startswith("-n") and word[2:].isdigit():
            return int(word[2:])
        if word.startswith("-") and word[1:].isdigit():
            return int(word[1:])
    return None


def _sed_reads_to_end(words: list[str]) -> bool:
    """``sed -n '1,$p'`` / ``sed -n p`` / ``sed '1,$p'`` 都算整段输出。"""
    scripts = [word for word in words[1:] if not word.startswith("-")]
    for script in scripts:
        compact = script.replace(" ", "")
        if compact.endswith("$p") or compact in {"p", "1,$p", "$p"}:
            return True
    return False
