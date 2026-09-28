"""安全规则：命令安全检查（denylist）。

沙箱已提供与宿主机的隔离；这里再补一层沙箱内的命令安全，拒绝破坏性命令。

只放**与智能体无关的底线** —— 凭据、提权、毁机器、毁本轮工作。按智能体不同的部分
（谁能用哪些工具、能往哪些目录写）在 ``safety/policy.py``；两层都在
``OpenSandboxBackend.aexecute`` 与边界中间件上生效。

全部按 ``re.IGNORECASE`` 匹配（既有行为），所以 git 那几条大小写通吃（``-d`` 与 ``-D``
同拒）。实现 skill 本来就禁止 ``reset --hard`` / ``clean`` / ``rm`` / ``mv``，与这些规则
不冲突。
"""

from __future__ import annotations

import re

# (正则模式, 拒绝原因)
DANGEROUS_COMMANDS: list[tuple[str, str]] = [
    # --- 毁机器（沙箱容器本身） -------------------------------------------
    (r"rm\s+-[a-z]*r[a-z]*f[a-z]*\s+/(\s|\*|$)", "删除根目录（rm -rf /）"),
    (r"\bmkfs", "格式化文件系统"),
    (r"\bdd\b[^|]*of=/dev/", "写磁盘设备"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "系统关机/重启"),
    (r":\(\)\s*\{[^}]*:\|:&\s*\};:", "fork bomb"),
    (r">\s*/dev/sd[a-z]", "覆盖磁盘"),
    (r"\bchmod\b[^|]*-R[^|]*777[^|]*\s+/", "全盘 777 权限"),
    # --- 凭据：提到就拒（读写皆然） ---------------------------------------
    # 凭据由平台注入沙箱，令牌一旦被读出来就会顺着四条出口流出去。脱敏层是「漏了也不
    # 外泄」，这里是不让它去读。单条 `\.secrets\b` 覆盖绝对与相对两种写法
    # （`cd /ai_coding && cat .secrets/git_token`）；仓库里恰好叫 `.secrets` 的目录也
    # 一并拒 —— 那种目录里本来就不该有命令要读的东西。
    (r"\.secrets\b", "访问凭据目录 /ai_coding/.secrets"),
    (r"\bgit-askpass\b", "访问凭据助手 git-askpass"),
    (r"\.git-credentials\b", "访问 git 凭据文件"),
    # --- 提权 -------------------------------------------------------------
    # 只认命令位置的 sudo：`git commit -m "去掉 sudo 依赖"` 这类正当命令不该被拦。
    (r"(?:^|[;&|\n(]\s*)sudo\s", "提权执行（sudo）"),
    # --- 破坏性 git：不毁沙箱，但毁掉本轮改了一半的工作 -------------------
    # 沙箱是会话级的、用户看不到里面，被 reset 掉的改动没有别处可找回。
    (
        r"git\s+push\b[^;&|\n]*(?<!\w)(-f|--force|--force-with-lease)(?![\w-])",
        "强推（git push --force）",
    ),
    (r"git\s+push\b[^;&|\n]*\s\+\S", "强推（+refspec）"),
    (r"git\s+reset\b[^;&|\n]*--hard", "丢弃改动（git reset --hard）"),
    (r"git\s+clean\b[^;&|\n]*(?:--force\b|-[a-z]*f)", "强制清理未跟踪文件（git clean -f）"),
    (
        r"git\s+(?:checkout|restore)\b[^;&|\n]*(?:--\s+)?\.(?:\s|$)",
        "丢弃改动（git checkout / restore .）",
    ),
    (r"git\s+branch\b[^;&|\n]*\s-D\b", "强删分支（git branch -D）"),
]


class CommandSafety:
    """命令安全策略：拒绝危险命令。"""

    def __init__(self, deny_patterns: list[tuple[str, str]] | None = None) -> None:
        self._patterns = deny_patterns if deny_patterns is not None else DANGEROUS_COMMANDS

    def check(self, command: str) -> str | None:
        """返回拒绝原因；``None`` 表示允许执行。"""
        for pattern, reason in self._patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return f"命令被安全策略拒绝：{reason}"
        return None
