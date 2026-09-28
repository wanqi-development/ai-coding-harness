"""按智能体划分的边界：哪些工具不给它、它能往哪些目录写。

``safety/permissions.py`` 管**与智能体无关的底线**（凭据、提权、毁机器）；这里管
**每个智能体自己的活动范围**。范围这份只在 ``BoundaryMiddleware`` 上落地：只有它知道
「当前是哪个智能体」—— ``OpenSandboxBackend`` 是**会话级共享**的（一个会话一个后端，
同一会话里换形态不重建），把按角色的范围挂在它上面等于给两个角色判同一个范围；何况
``aexecute`` 只看得到 ``execute``，管不住 ``read_file`` / ``ls`` 这类文件接口的调用。
底线另说：它与会话绑定也成立，所以 ``aexecute`` 上还有一道，那是纵深，不是这里的替代。

**为什么不用上游机制**：``FilesystemMiddleware(_permissions=…)`` 与
``SubAgent["permissions"]`` 在后端 ``supports_execution`` 时直接 ``NotImplementedError``
（``OpenSandboxBackend`` 继承 ``BaseSandbox``，正是这种），而且它只覆盖 fs 工具的读写、
管不到 ``execute``；``HarnessProfile.excluded_tools`` 的键是**模型**，主控与 planner
共用一个模型，表达不了「按角色」。所以这里自己判。

**为什么还要有用提示词**：提示词是「劝它别选」，这里是「选了也不执行」。两条都留 ——
前者省一轮往返，后者是真边界。仓库一贯的取舍是先撤掉选项（见 ``profiles.py`` 开头），
这也是 ``BoundaryMiddleware`` 既隐藏工具又拦截调用的原因。

**已知缺口（都是「反滑不反敌」的自觉取舍，不是遗漏）**：

- ``cd`` 到了我们判不出的地方（``cd $DIR`` / ``cd ~``）或整条命令没有 ``cd`` 时，
  **相对路径放过**：沙箱当前工作目录我们不知道，硬判会误伤大量正当命令。落在
  ``/`` 或家目录的写没有读方、随沙箱一起销毁，代价小。
- ``~`` 开头的目标同上（没展开，判不出落点）。
- 管道与间接执行（``find … | xargs rm``、``base64 -d | sh``、脚本套一层）判不出来。
  真正不可挽回的那批由 ``permissions.py`` 的底线兜。

这里的函数都是纯函数（只读参数、无副作用），判定结果由中间件落地。
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ai_coding_harness.config import Settings

# 目录名 → ``Settings`` 上的属性名（沙箱内路径可配，不写死）
_DIR_ATTRS: dict[str, str] = {
    "project": "sandbox_project_dir",
    "tmp": "sandbox_tmp_dir",
    "output": "sandbox_output_dir",
    "review": "sandbox_review_dir",
}
# 取不到配置时的落点（与 ``config.py`` 的默认值一致）
_DEFAULT_ROOTS: dict[str, str] = {
    "project": "/ai_coding/project",
    "tmp": "/ai_coding/tmp",
    "output": "/ai_coding/output",
    "review": "/ai_coding/review",
}

# 任何智能体都不许写：凭据、注入的资产（skills / policies 是平台按仓库内容铺进去的，
# 让它改等于让模型改自己的规则）、askpass 所在的目录。
PROTECTED_WRITE = (
    "/ai_coding/.secrets",
    "/ai_coding/skills",
    "/ai_coding/policies",
    "/usr/local/sbin",
)
# 读方向的底线。只列这几个**确定的凭据落点**，不做通用读范围 —— 收窄读取会误伤
# `cat /etc/os-release`、`git --version`、`pip list` 这类正当命令，收益却近乎为零。
# 走 ``execute`` 的那条路由 ``permissions.py`` 的凭据规则兜。
PROTECTED_READ = (
    "/ai_coding/.secrets",
    "/usr/local/sbin/git-askpass",
)
# 写目标里的豁免：临时区与设备文件。``/tmp`` 随沙箱销毁、没有读方，逐角色计较没意义。
WRITE_EXEMPT = (
    "/dev/null",
    "/dev/stdout",
    "/dev/stderr",
    "/dev/tty",
    "/dev/fd",
    "/tmp",
)

# fs 工具的路径入参（与 ``deepagents`` 的 schema 同名；见各工具的 args_schema）
_READ_ARGS: dict[str, tuple[str, ...]] = {
    "ls": ("path",),
    "read_file": ("file_path",),
    "glob": ("path",),
    "grep": ("path",),
}
_WRITE_ARGS: dict[str, tuple[str, ...]] = {
    "write_file": ("file_path",),
    "edit_file": ("file_path",),
}

# 会落盘的写动词。``sed`` / ``perl`` 要带 ``-i`` 才算写，单独处理。
_WRITE_VERBS = frozenset(
    {
        "chmod",
        "chown",
        "cp",
        "dd",
        "install",
        "ln",
        "mkdir",
        "mv",
        "patch",
        "rm",
        "rmdir",
        "rsync",
        "tar",
        "tee",
        "touch",
        "truncate",
        "unzip",
    }
)
_INPLACE_VERBS = frozenset({"sed", "perl"})
# 真正干活的是它后面那个词（``nohup rm x`` / ``xargs rm x``）
_PREFIX_WORDS = frozenset({"command", "env", "nohup", "time", "xargs"})

_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# 路径样子的 token：不含引号、``$``、``=``、括号、逗号等「不是路径」的字符。
# 通配符留着 —— ``build/*`` 是实打实的写目标，取到第一个通配符为止的字面前缀再判。
_TOKEN = re.compile(r"^[A-Za-z0-9._/@+~?*\[\]{}^-]+$")
_GLOB_CHARS = "*?[]{}"
_REDIRECT = re.compile(r"(?:^|[^>])>>?\s*([^\s;&|><]+)")
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


@dataclass(frozen=True)
class AgentPolicy:
    """一个智能体的活动范围：能写哪些目录、哪些工具不给它。

    ``write_roots`` 为空 = 这个角色不写文件（写工具同时也进 ``hidden_tools``，
    提示词里也不再出现写文件的说法）。
    """

    name: str
    write_roots: tuple[str, ...] = ()
    hidden_tools: frozenset[str] = frozenset()

    def check_tool(self, tool_name: str, args: Mapping[str, Any]) -> str | None:
        """返回拒绝原因；``None`` 表示放行。"""
        if tool_name in self.hidden_tools:
            return f"{self.name} 不使用 {tool_name}"
        for key in _READ_ARGS.get(tool_name, ()):
            value = args.get(key)
            if isinstance(value, str) and _under_any(_norm(value), PROTECTED_READ):
                return f"{self.name} 不允许访问 {value}"
        targets = write_targets(tool_name, args)
        if targets is None:
            return None
        for target in targets:
            if _under_any(target, PROTECTED_WRITE):
                return f"{self.name} 不允许写入 {target}"
            if _under_any(target, WRITE_EXEMPT):
                continue
            if not self.write_roots:
                return f"{self.name} 不写文件（试图写 {target}）"
            if not _under_any(target, self.write_roots):
                return (
                    f"{self.name} 只能写入 {'、'.join(self.write_roots)}，"
                    f"{target} 不在其中"
                )
        return None


def policy_for(name: str, settings: Settings | None = None) -> AgentPolicy:
    """取某个智能体的策略。

    ``name`` 是**装配名**：主控用形态名（``orchestrate`` / ``plan`` / …，见
    ``profiles.py``），子智能体用角色名（``planner`` / ``coder`` / ``reviewer``）。

    不认识的名字按**最保守**处理（不写文件）：新加角色忘了登记时，代价是它写不了文件、
    在工具返回里看到理由 —— 比默默拿到全目录写权限小得多。
    """
    entry = _ROOTS.get(name)
    if entry is None:
        return AgentPolicy(name=name)
    return AgentPolicy(
        name=name,
        write_roots=tuple(_root(key, settings) for key in entry[0]),
        hidden_tools=entry[1],
    )


# 装配名 → (可写目录的键, 不给的工具)。主控那几个形态与子智能体三个角色共处一张表：
# 名字在整个仓库里唯一，分两张表只会让人猜「主控的 plan 和子智能体的 planner 是不是
# 一回事」。
_ROOTS: dict[str, tuple[tuple[str, ...], frozenset[str]]] = {
    # 主控（多智能体形态）：改代码、临时区、审查原文三处都要落
    "orchestrate": (("project", "tmp", "review"), frozenset()),
    # 主控（单智能体出方案）：与子智能体 planner 同形 —— 方案 `storePlan` 落库、结论进回复，
    # 一个文件都不落，写工具一并撤掉。它自己的提示词有两处就是这么写的（`_PLAN_WORKDIR` 的
    # 「产出：方案写入 DB，不落文件」、`_PLAN_RULES` 的「不落文件」），只有「可写范围」那段
    # 还留着 output —— 三处两说，这次统一到不落文件。
    "plan": ((), frozenset({"edit_file", "write_file"})),
    # 主控（单智能体审查）：审查原文要给 ``prompts.py`` 读回来当兜底来源
    "review": (("review",), frozenset()),
    # 问答：产出只进回复，**一个文件都不落**（与 planner 同形：写根置空 + 撤掉写工具）。
    # 曾经留着 output 的写根，附一句提示词例外「只有用户明确要文件时才写入」—— 2026-09-22
    # 那轮就是照例外走的：用户说「给我一份仓库记忆文件」，它把记忆整篇复制进 output，再回一句
    # 「已写入 + 路径」。「这一轮没有交付物，只有回答」只靠劝，会被一行例外吃掉，所以这里撤选项。
    # `write_todos` 也撤掉 —— 一轮问答没有多步进度可报，清单只会多出一份「查一下、答一下」的
    # 空转（提示词里对应那一段也摘了，见 `prompts.TODO_PROMPT`）。
    "qa": ((), frozenset({"write_todos", "write_file", "edit_file"})),
    # 分析：结论进回复，只有中间文件落 tmp。曾经连 output 一起可写（技能里也是那么写的），
    # 但那个目录**没有回传通路** —— API / 前端一处都不读它，卷也只挂 project，写进去的
    # 文件随沙箱销毁，用户根本取不到。留着它等于留一条「把答案藏进文件」的路，
    # 与只读形态「交付物就是回复」这条口径直接冲突，所以撤掉 output、只留 tmp。
    # 现在 output 是**所有形态都不写**（plan 与 orchestrate 也一并撤了），这条通路只剩
    # 目录本身（`sandbox_output_dir` 与 provider 的 mkdir），留给未来有回传通路时再用。
    "analysis": (("tmp",), frozenset()),
    # 子智能体 planner：方案写库，不再落文件（写文件等于同一件事记两遍）
    "planner": ((), frozenset({"write_file", "edit_file"})),
    # 子智能体 coder：只动工程目录与临时区
    "coder": (("project", "tmp"), frozenset()),
    # 子智能体 reviewer：审查原文与临时区
    "reviewer": (("review", "tmp"), frozenset()),
}


def _root(key: str, settings: Settings | None) -> str:
    """沙箱内目录：优先取配置（用户可能改过），取不到用默认值。"""
    if settings is not None:
        value = getattr(settings, _DIR_ATTRS[key], None)
        if isinstance(value, str) and value:
            return _norm(value)
    return _DEFAULT_ROOTS[key]


def _norm(path: str) -> str:
    """归一：去掉尾斜杠、消掉 ``..``（``/a/../b`` 得先归一才判得出落点）。"""
    if not path:
        return path
    return posixpath.normpath(path)


def _under_any(path: str, roots: tuple[str, ...]) -> bool:
    return any(path == root or path.startswith(root.rstrip("/") + "/") for root in roots)


def _path_like(token: str) -> bool:
    """这个 token 像不像一个路径。

    滤掉的都是「肯定不是路径」的：开关（``-rf``）、权限位（``755``）、含 ``$`` 或引号
    的（展开与拼接的结果我们判不出）。
    """
    if token.startswith("-") or token.isdigit():
        return False
    return bool(_TOKEN.match(token))


def _literal(token: str) -> str:
    """通配符之前的那段字面前缀：``build/*`` → ``build/``，``/a/*.md`` → ``/a/``。"""
    for index, char in enumerate(token):
        if char in _GLOB_CHARS:
            return token[:index] or "/"
    return token


def _strip_heredocs(command: str) -> str:
    """丢掉 heredoc 的正文。

    正文是**要写进文件的内容**，不是要执行的命令 —— 里面出现 ``> /var/log/x`` 这类
    字符串是常态（写 Dockerfile、写脚本），留着会把正当写入判成越界。
    """
    lines = command.split("\n")
    kept: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        kept.append(line)
        index += 1
        match = _HEREDOC.search(line)
        if match is None:
            continue
        marker = match.group(2)
        while index < len(lines):
            body = lines[index]
            index += 1
            if body.strip() == marker:
                break
    return "\n".join(kept)


def _segments(text: str) -> list[str]:
    """按 ``;`` / ``&`` / ``|`` / 换行切段，**引号里的不算**。

    ``sed -i 's|/a|/b|' file`` 里的 ``|`` 是 sed 的分隔符，不是管道 —— 按字符硬切会把
    这条命令切成三段，写目标 ``file`` 就丢了（实测）。
    """
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
    return out


def _unquoted(text: str) -> str:
    """把引号包起来的部分换成空格：``echo "x > /a"`` 里的 ``>`` 不是重定向。

    只在找重定向目标时用 —— 判写动词时引号里的内容是参数，得留着（见 ``_segments``）。
    """
    out: list[str] = []
    quote = ""
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in "'\"":
            quote = char
            index += 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _verb_tokens(tokens: list[str]) -> list[str]:
    """跳过前置赋值与 ``env`` / ``nohup`` 这类包装词，剩下从动词开始的参数。"""
    index = 0
    while index < len(tokens) and _ASSIGN.match(tokens[index]):
        index += 1
    if index < len(tokens) and tokens[index].lstrip("(){}!") in _PREFIX_WORDS:
        index += 1
    return tokens[index:]


def _segment_targets(tokens: list[str]) -> list[str]:
    """一段命令（``;`` / ``&&`` / ``|`` 之间）会写到哪些路径。"""
    args = _verb_tokens(tokens)
    if not args:
        return []
    verb = args[0].lstrip("(){}!")
    rest = args[1:]
    if verb in _INPLACE_VERBS:
        if not any(flag.startswith("-i") for flag in rest):
            return []  # 不加 -i 是只读
        # 只认最后一个 token：``sed -i 's|/a|/b|' file`` 里脚本本身含斜杠与引号
        last = next((t for t in reversed(rest) if _path_like(t)), None)
        return [last] if last else []
    if verb not in _WRITE_VERBS:
        return []
    if verb == "dd":
        return [target[3:] for target in rest if target.startswith("of=")]
    return [target for target in rest if _path_like(target)]


def _resolve(target: str, cwd: str | None) -> str | None:
    """相对路径按最近一次 ``cd`` 的落点展开；判不出落点时返回 ``None``（放过）。"""
    if target.startswith("/"):
        return _norm(target)
    if target.startswith("~") or cwd is None:
        return None
    return _norm(posixpath.join(cwd, target))


def command_write_targets(command: str) -> list[str]:
    """一条 shell 命令会写到的路径（绝对值）。

    只看得出**直接**写：写动词后面的参数、重定向目标。管道与间接执行看不出，见模块
    开头的已知缺口。
    """
    text = _strip_heredocs(command)
    cwd: str | None = None
    found: list[str] = []
    for segment in _segments(text):
        tokens = segment.split()
        if not tokens:
            continue
        head = _verb_tokens(tokens)
        if head and head[0] == "cd":
            target = next((t for t in head[1:] if not t.startswith("-")), None)
            if target is not None and target.startswith("/"):
                cwd = _norm(target)
            continue
        for raw in _segment_targets(tokens):
            resolved = _resolve(_literal(raw), cwd)
            if resolved is not None:
                found.append(resolved)
    for raw in _REDIRECT.findall(_unquoted(text)):
        if not _path_like(raw):
            continue
        resolved = _resolve(_literal(raw), cwd)
        if resolved is not None:
            found.append(resolved)
    return found


def write_targets(tool_name: str, args: Mapping[str, Any]) -> list[str] | None:
    """这个工具调用会写到的路径；``None`` 表示它不涉及写。"""
    for key in _WRITE_ARGS.get(tool_name, ()):
        value = args.get(key)
        if isinstance(value, str) and value:
            return [_norm(value)]
    if tool_name == "execute":
        command = args.get("command")
        return command_write_targets(command) if isinstance(command, str) else []
    return None
