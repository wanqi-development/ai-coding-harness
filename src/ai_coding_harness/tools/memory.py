"""仓库记忆工具：主智能体把稳定结论**按节增量**写回 `/memories/<owner>/<repo>.md`。

2026-10-20 用户口径：**不再整篇覆盖写**。工具入参是 ``section + payload``，平台负责
合并、渲染、去重与限长（见 ``agent/middleware/memory_sections.py``）。这么改有三条理由：
整篇重写要求模型把上万字再吐一遍；模型会往错节写（实测把本次改动写进「仓库基本信息」）；
格式由模型自由发挥，后面的解析与对账跟着受罪。

四节各有一种写法：

* ``change``：追加一条变更记录（``kind / summary / pr``），按 PR 去重，只留最近 8 条；
* ``gaps``：整节替换「当前缺口」（``items: [{module, text}]``）；
* ``decisions``：整节替换「长期约束」（``items: [{decision, reason, impact}]``）；
* ``basic``：按字段替换「仓库基本信息」（``fields: {字段: 值}``，字段有白名单）。

**服务端打底**：合并后拿服务端刚查到的 PR 列表跑一遍 ``reconcile``，重算文件头那行、
把「变更记录」里写着「待合并」的状态改成实际结论，并摘掉老模板的两张表。

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

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from deepagents.backends.protocol import BackendProtocol
from langchain_core.tools import BaseTool, tool

from ai_coding_harness.agent.middleware.context_inject import now_stamp
from ai_coding_harness.agent.middleware.memory_reconcile import (
    reconcile,
    restamp,
    rows_to_verify,
    same_except_stamp,
    strip_legacy,
)
from ai_coding_harness.agent.middleware.memory_sections import (
    SECTION_BASIC,
    SECTION_CHANGES,
    SECTION_DECISIONS,
    SECTION_GAPS,
    SECTIONS,
    add_change,
    parse,
    patch_basic,
    render_memory,
    replace_decisions,
    replace_gaps,
    section_sizes,
)
from ai_coding_harness.backends.composite import memory_path
from ai_coding_harness.tools.git import PrLookupError

logger = logging.getLogger(__name__)

#: 全文与各节的硬上限（2026-10-20 用户口径：记忆每轮全文注入，不能无限长）
MAX_TOTAL = 6_000
MAX_BASIC = 2_000
MAX_GAPS = 1_500
MAX_DECISIONS = 1_500

#: 「仓库基本信息」只收这几个字段（模型写别的字段一律拒 —— 改动详情不该进这一节）
BASIC_FIELDS = ("项目地址", "技术栈", "目录结构", "启动方式", "测试 / 构建", "已知约定")

#: 变更条目的类型
CHANGE_KINDS = frozenset({"新增", "修改", "删除", "修复"})

#: 一条变更记录的摘要上限（细节在 PR 里，不进记忆）
MAX_CHANGE_SUMMARY = 120
MAX_FIELD = 300
MAX_ITEM = 200


def _skeleton(owner: str, repo: str) -> str:
    """首次建档的最小骨架：四节标题 + 平台维护的头行。"""
    return (
        f"# 仓库记忆：{owner}/{repo}\n\n"
        "> 最后更新：—　｜　最近 PR：—\n\n"
        "## 一、仓库基本信息\n\n"
        "## 二、变更记录（最新在上）\n\n"
        "## 三、当前缺口（按模块）\n\n"
        "## 四、长期约束\n"
    )


def _as_dict(payload: object) -> dict:
    """``payload`` 允许是 dict 或 JSON 字符串（模型偶尔把对象序列化着传）。"""
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            value = json.loads(payload)
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}
    return {}


def _checked(value: object, *, limit: int, what: str) -> tuple[str, str | None]:
    """取一段文本并**显式拒绝超长**（不做静默截断 —— 模型该知道哪一格超了）。"""
    text = str(value or "").strip()
    if len(text) > limit:
        return "", f"{what}超过 {limit} 字（{len(text)} 字），请精简后再提交。"
    return text, None


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
    async def save_repo_memory(section: str, payload: dict) -> str:
        """按节**增量**写仓库记忆 —— 只提交变化的那一节，平台负责合并与限长。

        不要再整篇提交记忆（记忆全文已由平台注入到系统提示里，以它为底稿也不许整篇重写）。

        - `section="change"`：追加一条变更记录。payload：
          `{"kind": "新增|修改|删除|修复", "summary": "一句话", "pr": 5}`。
          按 PR 编号去重，只留最近 8 条；`files` / `verification` 不会写入（细节在 PR 里）。
        - `section="gaps"`：**整节替换**「当前缺口」。payload：
          `{"items": [{"module": "订单", "text": "退款接口未实现"}]}`。
        - `section="decisions"`：**整节替换**「长期约束」。payload：
          `{"items": [{"decision": "不引入新依赖", "reason": "沙箱离线",
          "impact": "新增依赖必须先问"}]}`。只收会影响以后怎么做的约束，最多 8 条。
        - `section="basic"`：按字段替换「仓库基本信息」。payload：
          `{"fields": {"技术栈": "FastAPI + Vue3"}}`。字段只收
          项目地址 / 技术栈 / 目录结构 / 启动方式 / 测试 / 构建 / 已知约定 ——
          **本次改动不要写进这一节**，用 `change`。

        文件头的「最后更新 / 最近 PR」由平台盖章；「变更记录」的状态平台按代码平台实况回填。
        超长会被拒绝并告诉你是哪一节超了 —— 缩短后再提交，平台不会静默截断。
        """
        if section not in SECTIONS:
            return (
                "section 只能是 change / gaps / decisions / basic。"
                "本工具是增量写，不支持整篇提交。"
            )
        data = _as_dict(payload)
        if not data:
            return 'payload 必须是对象（dict），例如 {"kind":"修改","summary":"…","pr":5}。'

        current = await ctx.backend.aread(path)
        old = (getattr(current, "file_data", None) or {}).get("content")
        base = old if old is not None else _skeleton(ctx.owner, ctx.repo)
        doc = parse(strip_legacy(base))
        now = now_stamp()
        noted = ""

        if section == SECTION_BASIC:
            fields = data.get("fields")
            if not isinstance(fields, dict) or not fields:
                return 'basic 的 payload 形如 {"fields": {"技术栈": "…"}}。'
            cleaned: dict[str, str] = {}
            for raw_label, raw_value in fields.items():
                label = str(raw_label).strip().replace("测试构建", "测试 / 构建")
                if label not in BASIC_FIELDS:
                    return (
                        f"「{raw_label}」不是仓库基本信息的字段。只收："
                        f"{'、'.join(BASIC_FIELDS)}。本次改动请用 section=change。"
                    )
                value, problem = _checked(raw_value, limit=MAX_FIELD, what=f"字段「{label}」")
                if problem:
                    return problem
                if not value:
                    return f"字段「{label}」的值不能为空。"
                cleaned[label] = value
            dropped = patch_basic(doc, cleaned, allowed=BASIC_FIELDS)
            if dropped:
                noted += f"；清掉 {dropped} 条非白名单字段（基本信息只收固定六项）"

        elif section == SECTION_CHANGES:
            kind, problem = _checked(data.get("kind"), limit=10, what="kind")
            if problem:
                return problem
            summary, problem = _checked(
                data.get("summary"), limit=MAX_CHANGE_SUMMARY, what="summary"
            )
            if problem:
                return problem
            if kind not in CHANGE_KINDS:
                return f"change 的 kind 只能是 {' / '.join(sorted(CHANGE_KINDS))}。"
            if not summary:
                return "change 需要 summary（一句话，≤120 字）。"
            try:
                pr = int(data.get("pr"))
            except (TypeError, ValueError):
                return "change 需要 pr（PR 编号，整数）。"
            if pr <= 0:
                return "pr 必须是正整数。"
            if data.get("files") or data.get("verification"):
                noted = "；files / verification 不写入记忆（细节看 PR）"
            add_change(doc, date=now[:10], kind=kind, summary=summary, pr=pr)

        elif section == SECTION_GAPS:
            items = data.get("items")
            if not isinstance(items, list):
                return 'gaps 的 payload 形如 {"items": [{"module": "…", "text": "…"}]}。'
            cleaned_items: list[tuple[str, str]] = []
            for item in items:
                if not isinstance(item, dict):
                    return "gaps 的 items 里每一项都要是对象。"
                module, problem = _checked(item.get("module"), limit=60, what="module")
                if problem:
                    return problem
                text, problem = _checked(item.get("text"), limit=MAX_ITEM, what="text")
                if problem:
                    return problem
                if not module or not text:
                    return "gaps 的每一项都要有 module 与 text。"
                cleaned_items.append((module, text))
            if len(cleaned_items) > 20:
                return "当前缺口最多 20 条，请合并同模块的条目。"
            replace_gaps(doc, cleaned_items)

        else:  # SECTION_DECISIONS
            items = data.get("items")
            if not isinstance(items, list):
                return (
                    'decisions 的 payload 形如 '
                    '{"items": [{"decision": "…", "reason": "…", "impact": "…"}]}。'
                )
            if len(items) > 8:
                return "长期约束最多 8 条：先删掉已经过时的，再提交完整清单。"
            cleaned_decisions: list[tuple[str, str, str]] = []
            for item in items:
                if not isinstance(item, dict):
                    return "decisions 的 items 里每一项都要是对象。"
                decision, problem = _checked(item.get("decision"), limit=120, what="decision")
                if problem:
                    return problem
                reason, problem = _checked(item.get("reason"), limit=120, what="reason")
                if problem:
                    return problem
                impact, problem = _checked(item.get("impact"), limit=120, what="impact")
                if problem:
                    return problem
                if not (decision and reason and impact):
                    return "每条长期约束都要有 decision / reason / impact（影响不能空）。"
                cleaned_decisions.append((decision, reason, impact))
            replace_decisions(doc, cleaned_decisions)

        rendered = render_memory(doc)
        sizes = section_sizes(doc)
        problems: list[str] = []
        if sizes[SECTION_BASIC] > MAX_BASIC:
            problems.append(f"仓库基本信息 {sizes[SECTION_BASIC]}/{MAX_BASIC}")
        if sizes[SECTION_GAPS] > MAX_GAPS:
            problems.append(f"当前缺口 {sizes[SECTION_GAPS]}/{MAX_GAPS}")
        if sizes[SECTION_DECISIONS] > MAX_DECISIONS:
            problems.append(f"长期约束 {sizes[SECTION_DECISIONS]}/{MAX_DECISIONS}")
        if len(rendered) > MAX_TOTAL:
            problems.append(f"全文 {len(rendered)}/{MAX_TOTAL}")
        if problems:
            return (
                "仓库记忆未写入：超出上限（" + "、".join(problems) + "）。"
                "先精简对应的那一节再提交；平台不会静默截断。当前各节："
                + "、".join(f"{key}={size}" for key, size in sizes.items())
                + "。"
            )

        # 只有「有要核对的 PR」时才查平台（与开工前刷新同一条纪律）：提交的内容里没有
        # 待核对的 PR 行时，这一次往返省掉 —— 代价是文件头里的「最近 PR」保持原样（没查
        # 就不知道新值），所以那一格不动，而不是写成「—」。
        pulls: list[dict] | None = None
        if ctx.all_prs is not None and rows_to_verify(rendered):
            try:
                pulls = await ctx.all_prs()
            except PrLookupError as exc:
                logger.warning("查询 PR 失败，跳过记忆回写：%s", exc)
                return (
                    f"未能确认本仓库的 PR 状态，仓库记忆本次未更新：{exc}\n"
                    "先解决这个问题（凭据 / 网络 / 代码平台可用性）再重试。在确认之前，"
                    "不要把它当成「仓库没有未合入的 PR」，也不要凭这条回报用户。"
                )

        if pulls is not None:
            reconciled = reconcile(rendered, pulls, now=now)
            filled = reconciled.text
            if filled != rendered:
                noted += "；文件头已按代码平台实际状态盖章"
            if reconciled.restated:
                noted += (
                    f"；{len(reconciled.restated)} 条变更记录的状态已按"
                    f"实际结果更新（{'、'.join('#' + n for n in reconciled.restated)}）"
                )
        else:
            # 没查 PR：老表照样摘（摘表不需要 PR 事实），头行的时间戳照盖
            filled = strip_legacy(rendered)
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
        label = {
            SECTION_BASIC: "仓库基本信息",
            SECTION_CHANGES: "变更记录",
            SECTION_GAPS: "当前缺口",
            SECTION_DECISIONS: "长期约束",
        }[section]
        return f"仓库记忆{done}「{label}」（{len(filled)} 字）{noted}。"

    return [save_repo_memory]
