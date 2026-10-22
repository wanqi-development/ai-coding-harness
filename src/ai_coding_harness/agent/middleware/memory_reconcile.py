"""仓库记忆里**由平台维护**的部分：文件头那一行、PR 行的状态，以及两张老表。

记忆文件的内容全部由模型写 —— ``仓库基本信息`` / ``变更记录`` / ``当前缺口`` / ``关键决策``
（四节，2026-09-26 起）。平台做三件事：

1. **文件头那一行**（``> 最后更新：…　｜　最近 PR：…``）按本轮时间与代码平台的实际状态重算；
2. **「变更记录」里 PR 行的状态**按平台实况改写：写着 ``待合并`` 的行，线上已经合入就改成
   ``已合并``、已关闭未合入就改成 ``已关闭``（2026-09-26 起，此前这一格靠人工改）；
3. **老模板留下的两张表整节摘掉** —— ``需求列表``（不再要了）与 ``功能列表``（与「变更记录」
   重复：变更记录本来就是以 PR 为角度的）。留着它们只会被当成散文原样保留，表里的状态永远
   停在写下的那一刻，而已经没有人维护它。

第 2 件事只动**状态那一格**，并且只动写着 ``待合并`` 的行：已经写成 ``已合并`` / ``已关闭``
的不再回改（人工改过的结论优先），认不出状态的（老格式里没写这一格）**只当作「要核对」**
而不去改写 —— 认不出字段位置就动那一行，是在拿一份好记忆换一份被啃过的记忆。
``rows_to_verify`` 与 ``_restate`` 共用同一套行扫描，两者的判据不会各说各话。

**为什么不再维护任何表**：``需求列表`` 曾经由平台按 PR 状态回填四列、把未合入的 PR 补成行、
裁剪已结清的行 —— 它本质上是「平台上开着哪些 PR」的一份**缓存**。而闸门要的从来不是缓存：
它要的是「仓库里有没有还没落地的改动」，那个判据直接取本轮查到的 PR 列表即可
（见 :func:`_open_pulls`），于是缓存、回填、补行、裁剪这一整套跟着一起退场。而「没做完的功能」
按**模块**由模型写进 ``当前缺口`` 更贴合它的用途（下一轮该做什么），也不必被 PR 状态牵着走。

其余取舍：

- **查不到就保持原状**：PR 状态认不出来时一律不当成 ``待合入``。与 ``tools/git`` 拒绝把查询
  失败返回成空列表是同一条纪律（真查不到时 ``PrLookupError`` 在调用方抛出去）。
- **时间戳只在别的内容真的变了时才前进**：每轮都刷会让 ``changed`` 恒为真，「没变就不写盘」
  当场失效，连「这一轮到底改没改」这个事实也失真（轮末打底正是靠 ``changed`` 决定写不写）。
  做法是先拿**原文自己的时间戳**把正文试算一遍，逐字相同才算没变。
- **落盘那一步另有一次盖章**（:func:`restamp`）：模型提交的正文里常带着它自己写的时间戳，
  那个不该进盘 —— 判「没变」时用 :func:`same_except_stamp` 把时间戳挖空再比，于是这次盖章
  不会毁掉「没变就不写盘」。

不含任何 IO，也不经过模型：调用方把记忆全文与 PR 列表喂进来，拿回新全文。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ai_coding_harness.tools.pr_client import (
    PULL_CLOSED,
    PULL_MERGED,
    PULL_OPEN,
    pull_state,
    pull_summary,
)

#: PR 状态的三态映射 —— 词面与模版/提示词同一口径（``变更记录`` 的状态那格就写这三个词）。
#: ``PENDING``（PR 开着）是唯一会触发闸门的。
PENDING = "待合并"
MERGED = "已合并"
DISCARDED = "已关闭"

#: 状态码 → 记忆里那三个词。判定本身在 ``tools/pr_client.pull_state``（右栏「PR」面板与
#: 这里共用同一套），这边只负责翻成中文 —— 两处各判一次，就会各自漂。
_LABEL_BY_CODE = {PULL_OPEN: PENDING, PULL_MERGED: MERGED, PULL_CLOSED: DISCARDED}

#: 老模板留下的、现在整节摘掉的标题。认的是**去掉节号后正好等于**这些词 ——
#: 模型自己写的「历史功能列表」「需求变更记录」不被牵连（摘节是删东西，宁严勿宽）。
LEGACY_TITLES = ("需求列表", "功能列表")

#: 「变更记录」节的标题认法：去掉节号后**以它开头**（模版里后面还带着「（最新在上，以 PR 为角度）」）。
CHANGE_TITLE = "变更记录"

#: 文件头那一行的起头。认这个前缀而不是整行 —— 老记忆的头行里还有 ``当前分支``。
HEADER_PREFIX = "> 最后更新"
#: 头行两个字段之间的分隔。模板用的是全角竖线，同时容忍半角。
_HEADER_SEP = "　｜　"
_HEADER_SPLIT = re.compile(r"\s*[｜|]\s*")

#: 空单元格 / 空值在记忆里的占位符。
_DASH = "—"

_HEADING = re.compile(r"^##\s+(?P<title>.*\S)\s*$")
#: 节号前缀（``二、`` / ``3.``）。只在摘老节时用来还原标题本身。
_LEADING_NUMBER = re.compile(r"^[一二三四五六七八九十百\d]+\s*[、.．]\s*")

#: 变更记录里的一条 PR 记录：列表项 + ``**PR**`` 打头。
_PR_ROW = re.compile(r"^\s*[-*+]\s*\*\*PR\*\*")
#: 行内的 PR 编号（``[#31](…)``）。没有编号的条目无从核对。
_PULL_NUMBER = re.compile(r"#(\d+)")
#: 行内的状态字段：``**状态**：待合并``（加粗可省 —— 认的是这一格，不是它的排版）。
#: 值取到下一个竖线或行尾 —— 后面的字段照原样留着。
_STATUS_FIELD = re.compile(
    r"(?:\*\*)?状态(?:\*\*)?[ \t]*[:：][ \t]*(?P<value>.+?)[ \t]*(?:[｜|]|$)"
)


@dataclass(frozen=True)
class OpenPull:
    """一个仍未合入的 PR —— 闸门用它决定拦不拦本轮。"""

    number: str
    title: str
    branch: str = ""
    url: str = ""


@dataclass(frozen=True)
class Reconciled:
    """对账结果。"""

    #: 新的记忆全文，可直接覆盖写回
    text: str
    #: 全文是否真的变了 —— 没变就别写回，省一次存储往返
    changed: bool
    #: 仍未合入的 PR（**与本轮记忆内容无关**，只取自查到的 PR 列表）：闸门用它决定拦不拦。
    #: 没编号的 PR 不算 —— 那种条目无从核对，拦下来只会把这一轮白白结束掉。
    pending: tuple[OpenPull, ...]
    #: 这一轮被平台改掉状态的 PR 编号（``待合并`` → ``已合并``/``已关闭``）。只用于日志与
    #: 测试断言 —— 内容的事实全在 ``text`` 里。
    restated: tuple[str, ...] = ()


# --------------------------------------------------------------------------- 分节


@dataclass
class _Section:
    """``## 标题`` 那一行 + 它下面的全部内容。``head is None`` 是文件开头的散文。"""

    head: str | None
    body: list[str] = field(default_factory=list)


def _split(lines: Sequence[str]) -> list[_Section]:
    sections: list[_Section] = []
    head: str | None = None
    body: list[str] = []
    for line in lines:
        if _HEADING.match(line):
            sections.append(_Section(head, body))
            head, body = line, []
        else:
            body.append(line)
    sections.append(_Section(head, body))
    return sections


def _title_of(section: _Section) -> str:
    match = _HEADING.match(section.head) if section.head else None
    return match.group("title") if match else ""


def _join(sections: Sequence[_Section]) -> str:
    lines: list[str] = []
    for section in sections:
        if section.head is not None:
            lines.append(section.head)
        lines.extend(section.body)
    return "\n".join(lines)


def _drop_legacy(sections: list[_Section]) -> None:
    """摘掉老模板留下的「需求列表」「功能列表」整节（连标题一起）。

    两张表都已经没有消费者：前者平台不再回填、后者与「变更记录」重复（变更记录本来就是以
    PR 为角度的）。留着只会被当成散文原样保留 —— 里面的状态没人维护，而模型照它往下判断时
    读到的是过期的结论。摘完给前一节补个空行，免得下一个标题直接贴在上一条内容后面。

    认的是「去掉节号之后**正好等于**表名」，不是模糊匹配：摘节是删东西，模型自己写的
    「历史功能列表」不该被牵连进来。
    """
    for i in range(len(sections) - 1, -1, -1):
        title = _LEADING_NUMBER.sub("", _title_of(sections[i])).strip()
        if title not in LEGACY_TITLES:
            continue
        sections.pop(i)
        before = sections[i - 1] if i else None
        if before is not None and before.body and before.body[-1].strip():
            before.body.append("")


# --------------------------------------------------------------------------- PR 状态


def _pull_state(pull: Mapping[str, Any]) -> str:
    """一个 PR 的实际状态（记忆里那三个词）；认不出来的返回空串（＝不算未合入）。

    判定**只有一处**（``tools/pr_client.pull_state``，右栏「PR」面板用的是同一个），
    这里只把状态码翻成中文 —— 两处各判一次，就会出现「面板说还开着、闸门说没有」。
    """
    return _LABEL_BY_CODE.get(pull_state(pull), "")


def memory_pull_state(pull: Mapping[str, Any]) -> str:
    """公开版：PR → 记忆里的状态词（``待合并`` / ``已合并`` / ``已关闭`` / 空串）。"""
    return _pull_state(pull)


def _pull_fields(pull: Mapping[str, Any]) -> tuple[str, str, str]:
    """``(编号, 链接, 分支)`` —— 与面板同一个取法（``tools/pr_client.pull_summary``）。"""
    summary = pull_summary(pull)
    return summary["number"], summary["url"], summary["branch"]


def _open_pulls(pulls: Sequence[Mapping[str, Any]]) -> tuple[OpenPull, ...]:
    """仍未合入的 PR（闸门判据）。认不出状态的、没编号的都跳过。"""
    out: list[OpenPull] = []
    for pull in pulls:
        number, url, branch = _pull_fields(pull)
        if not number or _pull_state(pull) != PENDING:
            continue
        out.append(
            OpenPull(
                number=number,
                title=str(pull.get("title") or "").strip() or f"PR #{number}",
                branch=branch,
                url=url,
            )
        )
    return tuple(out)


# ------------------------------------------------------------------- 变更记录的行


@dataclass(frozen=True)
class _PrRow:
    """变更记录里的一行 PR 记录。"""

    #: PR 编号；行里没写编号时是空串
    number: str
    #: 行里状态那一格的值（``待合并`` / ``已合并`` / ``已关闭``）；没写或认不出是空串
    state: str
    #: 状态**值**在行内的区间（``line[start:end]``）；没写这一格时是 ``None``
    span: tuple[int, int] | None


def _state_of(value: str) -> str:
    """状态那一格的取值 → 三个已知词之一；认不出返回空串（空串 = 「不确定」，不是「没有」）。"""
    text = value.strip().strip("{}").strip()
    return text if text in (PENDING, MERGED, DISCARDED) else ""


def _row_of(line: str) -> _PrRow | None:
    """把一行解析成 PR 记录；不是 PR 行时返回 ``None``。"""
    if not _PR_ROW.match(line):
        return None
    found = _PULL_NUMBER.search(line)
    field = _STATUS_FIELD.search(line)
    span: tuple[int, int] | None = None
    state = ""
    if field is not None:
        start, _end = field.span("value")
        # 值尾巴上的空白不属于这一格（全角空格也在内）—— 改状态时只替换这一个词，
        # 与下一个字段之间的分隔空白照原样留着
        value = field.group("value").rstrip()
        span = (start, start + len(value))
        state = _state_of(value)
    return _PrRow(
        number=found.group(1) if found else "",
        state=state,
        span=span,
    )


def _is_change_section(section: _Section) -> bool:
    title = _LEADING_NUMBER.sub("", _title_of(section)).strip()
    return title.startswith(CHANGE_TITLE)


def _pr_rows(sections: Sequence[_Section]) -> list[tuple[int, int, _PrRow]]:
    """``(节下标, 行下标, 行)`` —— 只扫「变更记录」节。"""
    out: list[tuple[int, int, _PrRow]] = []
    for si, section in enumerate(sections):
        if not _is_change_section(section):
            continue
        for li, line in enumerate(section.body):
            row = _row_of(line)
            if row is not None:
                out.append((si, li, row))
    return out


def rows_to_verify(text: str) -> tuple[str, ...]:
    """记忆里**需要按平台实况核对**的 PR 编号（按出现顺序、去重）。

    判据是「这一行是不是已经说清了结局」：写着 ``已合并`` / ``已关闭`` 的不必再查；写着
    ``待合并`` 的要查（查完可能就要改成别的）；**老格式里没写状态那一格的也算**（``state`` 是
    空串 = 不确定，而「不确定」不能当成「已经完事」）。没编号的行无从核对，跳过。

    调用方（``middleware/memory.py`` 与 ``tools/memory.py``）拿它决定**要不要查平台** ——
    一条都不用核对的仓库，这一轮连 PR 列表都不去取。
    """
    out: list[str] = []
    for _si, _li, row in _pr_rows(_split(text.split("\n"))):
        if not row.number or row.state in (MERGED, DISCARDED):
            continue
        if row.number not in out:
            out.append(row.number)
    return tuple(out)


def recorded_pull_numbers(text: str) -> tuple[str, ...]:
    """记忆「变更记录」里已经出现过的 PR 编号（去重、按出现顺序）。"""
    numbers: list[str] = []
    for _si, _li, row in _pr_rows(_split(text.split("\n"))):
        if row.number and row.number not in numbers:
            numbers.append(row.number)
    return tuple(numbers)


def newest_recorded_pull(text: str) -> int | None:
    """记忆里最大的 PR 编号；没有带编号的 PR 行时返回 ``None``。"""
    values = [int(number) for number in recorded_pull_numbers(text) if number.isdigit()]
    return max(values) if values else None


def _states_by_number(pulls: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """``编号 → 实际状态``（认不出状态的编号不入表）。"""
    out: dict[str, str] = {}
    for pull in pulls:
        number, _url, _branch = _pull_fields(pull)
        state = _pull_state(pull)
        if number and state:
            out[number] = state
    return out


def _restate(
    sections: list[_Section], states: Mapping[str, str]
) -> tuple[str, ...]:
    """把记忆状态与平台不一致的行改成实况；返回被改的编号。

    只动状态那一格；行里没写状态栏、平台状态认不出、或两边已经一致时不改。
    """
    changed: list[str] = []
    for si, li, row in _pr_rows(sections):
        live = states.get(row.number, "")
        if row.span is None or not live or row.state == live:
            continue
        line = sections[si].body[li]
        start, end = row.span
        sections[si].body[li] = f"{line[:start]}{live}{line[end:]}"
        changed.append(row.number)
    return tuple(changed)


# --------------------------------------------------------------------------- 文件头


def _header_at(lines: Sequence[str]) -> int | None:
    for i, line in enumerate(lines):
        if line.lstrip().startswith(HEADER_PREFIX):
            return i
    return None


def _header_stamp(text: str) -> str:
    """头行里的时间戳；没有头行就返回空串。"""
    lines = text.split("\n")
    at = _header_at(lines)
    if at is None:
        return ""
    rest = lines[at].lstrip()[len(HEADER_PREFIX) :].lstrip("：: ")
    return _HEADER_SPLIT.split(rest, 1)[0].strip()


def _latest_label(pulls: Sequence[Mapping[str, Any]]) -> str:
    """「最近 PR」栏：编号最大的那个（两个平台的编号都单调递增）。一个都没有就是 ``—``。"""
    best: tuple[int, str, str] | None = None
    for pull in pulls:
        number, url, _branch = _pull_fields(pull)
        try:
            key = int(number)
        except ValueError:
            continue
        if best is None or key > best[0]:
            best = (key, number, url)
    if best is None:
        return _DASH
    return f"[#{best[1]}]({best[2]})" if best[2] else f"#{best[1]}"


def _stamp_header(text: str, *, now: str, latest: str) -> str:
    """把文件头换成 ``> 最后更新：<now>　｜　最近 PR：<latest>``。

    有头行就整行替换（老格式里的字段因此被清掉），没有就补在标题下面。
    """
    line = f"{HEADER_PREFIX}：{now}{_HEADER_SEP}最近 PR：{latest}"
    lines = text.split("\n")
    at = _header_at(lines)
    if at is not None:
        lines[at] = line
        return "\n".join(lines)
    # 补在标题行（跳过后面的空行）之后；文件开头就是正文时不加标题也不要紧
    at = 1 if lines and lines[0].lstrip().startswith("# ") else 0
    while at < len(lines) and not lines[at].strip():
        at += 1
    lines[at:at] = [line, ""]
    return "\n".join(lines)


def _replace_stamp(line: str, now: str) -> str:
    """把一行里的时间戳换成 ``now``，**其余字段逐字留着**。

    只认 ``> 最后更新`` 打头的行（与 ``_header_at`` 同一口径）；时间戳取第一个分隔符
    之前那一段，后面的（``最近 PR``、老格式里的 ``当前分支``）原样带回去 —— 这一层
    只管时间，不做 ``_stamp_header`` 那种整行重建。
    """
    at = len(line) - len(line.lstrip())
    rest = line[at + len(HEADER_PREFIX) :].lstrip("：: ")
    fields = _HEADER_SPLIT.split(rest, 1)
    tail = f"{_HEADER_SEP}{fields[1].strip()}" if len(fields) > 1 and fields[1].strip() else ""
    return f"{line[:at]}{HEADER_PREFIX}：{now}{tail}"


def _stamp_lines(text: str, now: str) -> str:
    """头行的时间戳换成 ``now``；没有头行时**原样返回**（补头行是 ``reconcile`` 的事 ——
    只有它知道「最近 PR」该写什么，这里凭空补一行会把「没有 PR」说成事实）。
    """
    lines = text.split("\n")
    at = _header_at(lines)
    if at is None:
        return text
    lines[at] = _replace_stamp(lines[at], now)
    return "\n".join(lines)


def restamp(text: str, *, now: str) -> str:
    """把文件头 ``> 最后更新`` 的时间戳换成 ``now``。

    ``reconcile`` 只在自己算出的正文与原文不同时才动头行 —— 那是「这一轮真的变了没有」
    的判据，不能因为时钟走了一分钟就每轮写盘。但模型提交的正文里也带着它自己写的头行
    时间（模版里就是那么写的），**那个时间不该落盘**：猜的时刻与真实写盘时刻对不上，
    「最后更新」也就没意义了。故落盘那一步由平台盖一次章
    （``middleware.context_inject.now_stamp()``）。
    """
    return _stamp_lines(text, now)


def same_except_stamp(old: str, new: str) -> bool:
    """两份全文是否**只差**文件头的时间戳（其余逐字相同）。

    「内容没变就不写盘」这条守卫要留住，可落盘内容里的时间戳每次都是本轮新盖的，逐字比
    必然不相等、于是每次都写一遍。故先把两边的时间戳挖空再比 —— 两份都是本模块挖的，
    被抹掉的只有那一个字段（``_replace_stamp(line, "")``）。

    有一边没有头行时按原样逐字比：缺头行是结构差异，不是时间差异，那种情况该照写。
    """
    if not (_header_stamp(old) and _header_stamp(new)):
        return old == new
    return _stamp_lines(old, "") == _stamp_lines(new, "")


# --------------------------------------------------------------------------- 入口


def strip_legacy(text: str) -> str:
    """只做**不需要 PR 事实**的那部分维护：摘掉老模板留下的两张表。

    给「这一轮不必查 PR」的调用方用（见 ``rows_to_verify``）：文件头里的「最近 PR」只有查过
    才知道该写什么，所以那一条不在这一层，剩下的摘表与它无关。没有老表时是恒等变换 ——
    ``_split`` / ``_join`` 一个字节都不改（空串、缺尾换行都原样回来）。
    """
    sections = _split(text.split("\n"))
    _drop_legacy(sections)
    return _join(sections)


def reconcile(text: str, pulls: Sequence[Mapping[str, Any]], *, now: str) -> Reconciled:
    """按 ``pulls`` 重算文件头、回填 PR 行的状态，并摘掉老模板留下的两张表。

    ``now`` 是写进文件头的时间戳（调用方给，本模块不取时钟）；正文（含各节散文）逐字不动 ——
    例外只有两处，都是平台那份：文件头那一行，与「变更记录」里写着 ``待合并`` 的状态格。
    """
    sections = _split(text.split("\n"))
    _drop_legacy(sections)
    restated = _restate(sections, _states_by_number(pulls))
    body = _join(sections)
    pending = _open_pulls(pulls)
    latest = _latest_label(pulls)

    # 先用**原文自己的时间戳**把正文试算一遍：只有逐字相同才算「这一轮什么都没变」。
    # 直接拿 now 去比，每轮都不相等，changed 恒为真 —— 「没变就不写盘」就废了，
    # 而且「这一轮改没改」这个事实也跟着失真（服务端打底靠它决定写不写）。
    if _stamp_header(body, now=_header_stamp(text) or now, latest=latest) == text:
        return Reconciled(text=text, changed=False, pending=pending, restated=restated)
    # 真变了：正文照写，头行的时间戳推进到本轮
    return Reconciled(
        text=_stamp_header(body, now=now, latest=latest),
        changed=True,
        pending=pending,
        restated=restated,
    )
