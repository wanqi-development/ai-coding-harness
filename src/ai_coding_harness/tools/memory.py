"""仓库记忆工具：主智能体把稳定结论写回 `/memories/<owner>/<repo>.md`。

工具按会话构建（闭包捕获 owner/repo/backend），路径由工具算 —— 模型只提供内容，
不拼路径、不经过沙箱文件系统。内容由模型决定，结构由
`/ai_coding/policies/repo-memory-template.md` 约定。

**服务端打底**：落盘前拿服务端刚查到的 PR 列表跑一遍 ``agent.memory_reconcile.reconcile``，
按本轮重算文件头那行的「最后更新 / 最近 PR」、把「变更记录」里写着「待合并」的状态改成实际
结论（已合并 / 已关闭），并摘掉老模板留下的「需求列表」「功能列表」两张表（那两张表平台不再
维护，留着只会被当成散文保留下来）。**其余内容逐字不动** —— 记忆的内容（含「当前缺口」里
按模块列的未完成功能）全部由模型写。

**没事就不查**（2026-09-26 起，与开工前的刷新同一条纪律）：提交的内容里没有待核对的 PR
（``rows_to_verify`` —— 都写着已合并 / 已关闭，或压根没有 PR 行）时，这一次平台往返省掉，
落盘前只做摘老表与盖时间戳这两件不需要 PR 事实的事。

后端是 deepagents 的 ``StoreBackend``，它的 ``awrite`` **只在文件不存在时新建**，
已存在一律报 ``already exists`` 拒绝。记忆要能逐轮覆盖，就得对已存在的文件走
``aedit``（old_string 取当前全文做整篇替换）。

**要查的时候查不到就拒写。** 早先那条「本仓库还有未关闭的 PR 就不写」已去掉：编码结束
那一刻必然存在刚建好的未合入 PR（这一轮的成果正挂在它上面），照原规则模型这轮的补充写入
会被一律拒绝。查询本身失败仍然拒写：既不把「查不到」说成「没有未合入的 PR」，也不说成「有」。
不需要查的那一轮（见上）没有这个拒写条件 —— 那时要写的是与 PR 状态无关的内容。

路径格式不在这里定义 —— 统一走 ``backends.composite.memory_path``（读写两侧同一口径）。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from deepagents.backends.protocol import BackendProtocol
from langchain_core.tools import BaseTool, tool

from ai_coding_harness.agent.memory_reconcile import (
    reconcile,
    restamp,
    rows_to_verify,
    same_except_stamp,
    strip_legacy,
)
from ai_coding_harness.agent.middleware.context_inject import now_stamp
from ai_coding_harness.backends.composite import memory_path
from ai_coding_harness.tools.git import PrLookupError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryContext:
    """记忆工具绑定的会话上下文。"""

    owner: str
    repo: str
    backend: BackendProtocol
    prefix: str = "/memories/"
    # 查本仓库**全部** PR（含已合入与已废弃）—— 拒写检查与文件头的「最近 PR」共用这一次查询。
    # None 表示这项能力没接上：既不查也不盖章，内容原样落盘（脚本与单测会这么用）。
    all_prs: Callable[[], Awaitable[list[dict]]] | None = None


def build_memory_tools(ctx: MemoryContext) -> list[BaseTool]:
    """构建主智能体的记忆工具（只写，不含读——读走 ``read_file``）。"""
    path = memory_path(ctx.owner, ctx.repo, ctx.prefix)

    @tool
    async def save_repo_memory(content: str) -> str:
        """把仓库记忆整篇写回 `/memories/<owner>/<repo>.md`（StoreBackend 持久化，跨会话可读）。

        结构按 `/ai_coding/policies/repo-memory-template.md`。记忆全文已经由平台注入到
        系统提示里（「仓库记忆」块），**以它为底稿**把仍然成立的内容合并进来再整篇提交
        即可 —— 本工具是覆盖写，只提交增量会丢掉旧内容；不必先 `read_file` 读一遍。

        文件头那行的 `最后更新` / `最近 PR` 由平台按代码平台（Gitee / GitHub）的实际状态重算与盖章 ——
        **你自己写的时间不会生效**，也不必为了它去取当前时间。其余内容（含「当前缺口」里
        **按模块列出的未完成功能**）由你写；老模板遗留的「需求列表」「功能列表」两张表会被
        平台摘掉，别再写它们。「变更记录」里 PR 行的**状态你一律写「待合并」**，之后平台会按
        实况把它改成「已合并」/「已关闭」—— 别自己查、也别手改这一格。有要核对的 PR
        而查不到本仓库的 PR 时整篇都不会落盘 —— 宁可不动，也不把还没落地的改动记成既成事实。

        Args:
            content: 记忆全文（Markdown），不是增量片段。
        """
        # 只有「有要核对的 PR」时才查平台（与开工前刷新同一条纪律）：提交的内容里没有
        # 待核对的 PR 行时，这一次往返省掉 —— 代价是文件头里的「最近 PR」保持原样（没查
        # 就不知道新值），所以那一格不动，而不是写成「—」。
        pulls: list[dict] | None = None
        if ctx.all_prs is not None and rows_to_verify(content):
            try:
                pulls = await ctx.all_prs()
            except PrLookupError as exc:
                logger.warning("查询 PR 失败，跳过记忆回写：%s", exc)
                return (
                    f"未能确认本仓库的 PR 状态，仓库记忆本次未更新：{exc}\n"
                    "先解决这个问题（凭据 / 网络 / 代码平台可用性）再重试。在确认之前，"
                    "不要把它当成「仓库没有未合入的 PR」，也不要凭这条回报用户。"
                )

        now = now_stamp()
        noted = ""
        if pulls is not None:
            reconciled = reconcile(content, pulls, now=now)
            filled = reconciled.text
            if filled != content:
                noted = "；文件头已按代码平台实际状态盖章"
            if reconciled.restated:
                noted += (
                    f"；{len(reconciled.restated)} 条变更记录的状态已按"
                    f"实际结果更新（{'、'.join('#' + n for n in reconciled.restated)}）"
                )
        else:
            # 没查 PR：老表照样摘（摘表不需要 PR 事实），头行的时间戳照盖
            filled = strip_legacy(content)
        # 头行的时间戳由平台盖：模型提交的正文里也带着它自己写的时间（模版里就有那一行），
        # 那个时刻与真实写盘时刻对不上，「最后更新」就失去意义了。
        filled = restamp(filled, now=now)

        current = await ctx.backend.aread(path)
        old = (getattr(current, "file_data", None) or {}).get("content")
        if old is None:
            result = await ctx.backend.awrite(path, filled)
            done = "已创建"
        elif same_except_stamp(old, filled):
            return f"仓库记忆与现有内容一致，未重复写入（{len(filled)} 字）。"
        else:
            result = await ctx.backend.aedit(path, old, filled)
            done = "已更新"
        error = getattr(result, "error", None)
        if error:
            return f"写入仓库记忆失败：{error}"
        return f"仓库记忆{done} {path}（{len(filled)} 字）{noted}。"

    return [save_repo_memory]
