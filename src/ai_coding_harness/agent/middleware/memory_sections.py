"""仓库记忆的**结构化合并**：按节读写，不让模型整篇重写。

2026-10-20 用户口径：`save_repo_memory` 改成「只提交变化的那一节」，由平台合并、渲染、
去重、限长。理由有三条：

* 整篇覆盖写要求模型把上万字原样再吐一遍，写一次就是一次大输出；
* 模型会往错节写（实测把本次改动写进「仓库基本信息」）；
* 格式由模型自由发挥，同一件事每次写法都不一样，后面的解析与对账跟着受罪。

这个模块**不含 IO、不经过模型**：调用方给当前全文和一份结构化增量，拿回合并后的全文。

四节（老模板的标题也认，读到就归到对应节）：

===========  ==========================================
``basic``    仓库基本信息（稳定事实）
``changes``  变更记录（一行一条，最新在上）
``gaps``     当前缺口（按模块，整节替换）
``decisions``长期约束（原「关键决策」，整节替换）
===========  ==========================================

「当前缺口」与「长期约束」是**整节替换**语义：它们回答的是「现在」，过时的必须能删掉。
只有「变更记录」是追加（按 PR 编号去重，超出条数从最旧的滚出窗口）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: 四节的稳定键（工具入参用它们，不用中文标题）
SECTION_BASIC = "basic"
SECTION_CHANGES = "change"
SECTION_GAPS = "gaps"
SECTION_DECISIONS = "decisions"
SECTIONS = (SECTION_BASIC, SECTION_CHANGES, SECTION_GAPS, SECTION_DECISIONS)

#: 渲染时用的标题（第三节改名「长期约束」：只收会影响以后怎么做的决定）
HEADINGS: dict[str, str] = {
    SECTION_BASIC: "## 一、仓库基本信息",
    SECTION_CHANGES: "## 二、变更记录（最新在上）",
    SECTION_GAPS: "## 三、当前缺口（按模块）",
    SECTION_DECISIONS: "## 四、长期约束",
}

#: 老模板的标题也认（读老文件时归到对应节，写回时统一成新标题）。
_TITLE_KEYS: tuple[tuple[str, str], ...] = (
    ("仓库基本信息", SECTION_BASIC),
    ("项目概览", SECTION_BASIC),
    ("约定与注意事项", SECTION_BASIC),
    ("变更记录", SECTION_CHANGES),
    ("当前缺口", SECTION_GAPS),
    ("当前进度", SECTION_GAPS),
    ("长期约束", SECTION_DECISIONS),
    ("关键决策", SECTION_DECISIONS),
)

_HEADING = re.compile(r"^##\s+(?P<title>.*\S)\s*$")
_SUB_HEADING = re.compile(r"^###\s+")
_NUM = re.compile(r"#(\d+)")
_EMPTY = "（暂无）"

#: 变更记录最多留几条（旧的滚出窗口；历史在代码平台上，需要时按 PR 查）
MAX_CHANGES = 8

#: 精简注入（planner / coder / coding / plan 主控）要的三节：
#: 基本信息（怎么做才符合仓库）+ 当前缺口（还差什么）+ 长期约束（不能怎么做）。
#: **变更记录不在内** —— 那是「历史发生过什么」，这四个角色没有一处靠它干活。
DIGEST_KEYS: tuple[str, ...] = (SECTION_BASIC, SECTION_GAPS, SECTION_DECISIONS)


def empty_memory(owner: str, repo: str) -> str:
    """首次建档的最小骨架：四节标题 + 平台维护的头行。"""
    return (
        f"# 仓库记忆：{owner}/{repo}\n\n"
        "> 最后更新：—　｜　最近 PR：—\n\n"
        "## 一、仓库基本信息\n\n"
        "## 二、变更记录（最新在上）\n\n"
        "## 三、当前缺口（按模块）\n\n"
        "## 四、长期约束\n"
    )


def _key_of(title: str) -> str | None:
    """标题 → 节键。按关键词判，``二、`` 这种节号不影响。"""
    bare = re.sub(r"^[一二三四五六七八九十百\d]+\s*[、.．]\s*", "", title).strip()
    for word, key in _TITLE_KEYS:
        if bare.startswith(word):
            return key
    return None


@dataclass
class _Section:
    key: str | None
    heading: str
    body: list[str] = field(default_factory=list)


@dataclass
class MemoryDoc:
    """一份记忆的结构：文件头散文 + 若干节（认不出的节原样保留）。"""

    preamble: list[str] = field(default_factory=list)
    sections: list[_Section] = field(default_factory=list)

    def body(self, key: str) -> list[str]:
        return next((s.body for s in self.sections if s.key == key), [])

    def set_body(self, key: str, lines: list[str]) -> None:
        """整节替换；节不存在就按标准顺序补上（放在最后一个已存在节之后）。"""
        for section in self.sections:
            if section.key == key:
                section.body = list(lines)
                section.heading = HEADINGS[key]
                return
        order = list(SECTIONS)
        rank = order.index(key)
        insert_at = len(self.sections)
        for index, section in enumerate(self.sections):
            if section.key in order and order.index(section.key) > rank:
                insert_at = index
                break
        self.sections.insert(insert_at, _Section(key, HEADINGS[key], list(lines)))


def split_memory(text: str) -> MemoryDoc:
    """把全文拆成结构与节；老标题按关键词归到新节。"""
    doc = MemoryDoc()
    current: _Section | None = None
    for line in (text or "").splitlines():
        match = _HEADING.match(line)
        if match is None:
            (current.body if current is not None else doc.preamble).append(line)
            continue
        key = _key_of(match.group("title"))
        current = _Section(key, line)
        doc.sections.append(current)
    return doc


def render_memory(doc: MemoryDoc) -> str:
    """结构 → 全文。认不出的节保持原样，认得的节统一标题。"""
    lines = list(doc.preamble)
    for section in doc.sections:
        lines.append(HEADINGS[section.key] if section.key else section.heading)
        lines.extend(section.body)
    return "\n".join(lines).rstrip() + "\n"


def _normalize_blank(lines: list[str]) -> list[str]:
    """去掉首尾空行、压缩连续空行 —— 增量合并后最容易在这里长出空洞。"""
    out: list[str] = []
    for line in lines:
        if not line.strip():
            if out and not out[-1].strip():
                continue
            out.append("")
            continue
        out.append(line.rstrip())
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return out


def _merge_duplicate_sections(doc: MemoryDoc) -> None:
    """老模板里两节归到同一键时，把后一节的正文并到前一节。"""
    merged: list[_Section] = []
    for section in doc.sections:
        if section.key is None:
            merged.append(section)
            continue
        target = next((s for s in merged if s.key == section.key), None)
        if target is None:
            merged.append(section)
            continue
        body = [line for line in section.body if line.strip()]
        if body:
            target.body.extend([""] if target.body else [])
            target.body.extend(body)
    doc.sections = merged


def parse(text: str) -> MemoryDoc:
    """解析 + 合并重复节（对外入口）。"""
    doc = split_memory(text)
    _merge_duplicate_sections(doc)
    return doc


# --------------------------------------------------------------------------- 各节的合并


def add_change(
    doc: MemoryDoc,
    *,
    date: str,
    kind: str,
    summary: str,
    pr: int,
    status: str = "待合并",
    url: str = "",
) -> None:
    """在最上面追加/替换一条变更记录；只留最近 ``MAX_CHANGES`` 条。

    同一个 PR 再来一次时**替换**原条目（写回可能发生在同一轮的多个收尾点），而不是写两条。
    """
    target = f"[#{pr}]({url})" if url else f"#{pr}"
    entry = [
        f"### {date} · {kind} · {summary}",
        f"- **PR**：{target}　｜　**状态**：{status}",
    ]
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in doc.body(SECTION_CHANGES):
        if _SUB_HEADING.match(line):
            if current:
                blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(current)

    def pr_of(block: list[str]) -> int | None:
        for line in block:
            if "**PR**" in line:
                match = _NUM.search(line)
                return int(match.group(1)) if match else None
        return None

    kept: list[list[str]] = []
    for block in blocks:
        if pr_of(block) == pr:
            continue  # 同一 PR：用新条目替换旧的
        kept.append(block)
    kept.insert(0, entry)
    lines: list[str] = []
    for block in kept[:MAX_CHANGES]:
        lines.extend(_normalize_blank(block))
        lines.append("")
    doc.set_body(SECTION_CHANGES, _normalize_blank(lines))


def replace_gaps(doc: MemoryDoc, items: list[tuple[str, str]]) -> None:
    """整节替换「当前缺口」：``[(模块, 文本), ...]``。"""
    lines = [f"- **{module}**：{text}" for module, text in items]
    doc.set_body(SECTION_GAPS, lines or [_EMPTY])


def replace_decisions(doc: MemoryDoc, items: list[tuple[str, str, str]]) -> None:
    """整节替换「长期约束」：``[(约束, 原因, 影响), ...]``。"""
    lines = [f"- **约束**：{a}　｜　**原因**：{b}　｜　**影响**：{c}" for a, b, c in items]
    doc.set_body(SECTION_DECISIONS, lines or [_EMPTY])


def patch_basic(
    doc: MemoryDoc, fields: dict[str, str], *, allowed: tuple[str, ...] = ()
) -> int:
    """按字段替换「仓库基本信息」；给了 ``allowed`` 就**顺手归一化**整节。

    2026-10-20 用户口径：基本信息只收固定几个字段。只拦新写入不够 —— 存量里可能已经躺着
    「**认证体系**」「**服务间鉴权**」这类非白名单字段，下一次写 basic 时必须把它们清掉，
    否则白名单永远只是「以后别这么写」。散文行（模板里的说明文字）保留。

    返回被丢弃的非白名单字段条数（回执里要说出来，别静默删内容）。
    """
    body = list(doc.body(SECTION_BASIC))
    line_of = re.compile(r"^\s*[-*+]\s*\*\*(?P<label>[^*]+)\*\*\s*[:：]")
    dropped = 0
    if allowed:
        kept: list[str] = []
        for line in body:
            match = line_of.match(line)
            if match and match.group("label").strip() not in allowed:
                dropped += 1
                continue
            if line.strip().startswith("（其余内容已裁剪"):
                continue
            kept.append(line)
        body = kept
    for label, value in fields.items():
        line = f"- **{label}**：{value}"
        pattern = re.compile(rf"^\s*[-*+]\s*\*\*{re.escape(label)}\*\*\s*[:：]")
        for index, old in enumerate(body):
            if pattern.match(old):
                body[index] = line
                break
        else:
            body.append(line)
    doc.set_body(SECTION_BASIC, _normalize_blank(body))
    return dropped


def section_sizes(doc: MemoryDoc) -> dict[str, int]:
    """各节正文字符数（给「超了哪一节」的回执与迁移报告用）。"""
    return {key: len("\n".join(doc.body(key))) for key in SECTIONS}


def digest(text: str, keys: tuple[str, ...] = DIGEST_KEYS) -> str:
    """按角色**精简**注入：只留指定几节，其余丢掉。

    返回空串 ＝ 这份记忆里没有这些节（调用方据此不注入）。标题保留原样，方便模型认出
    「这是同一个文件」；末尾补一句说明「变更记录没有注入」，免得它以为历史丢了。
    """
    doc = parse(text)
    picked = [section for section in doc.sections if section.key in keys]
    if not picked:
        return ""
    lines = list(doc.preamble)
    for section in picked:
        lines.append(HEADINGS[section.key] if section.key else section.heading)
        lines.extend(section.body)
        lines.append("")
    names = " / ".join(
        {
            SECTION_BASIC: "仓库基本信息",
            SECTION_GAPS: "当前缺口",
            SECTION_DECISIONS: "长期约束",
        }[key]
        for key in keys
        if any(section.key == key for section in picked)
    )
    lines.append(f"> 本节选只含：{names}；变更记录没有注入（需要历史时回报给主控 / 由主控查 PR）。")
    return "\n".join(_normalize_blank(lines)) + "\n"
