"""仓库记忆的对账：平台只维护文件头那一行，并摘掉两张老表。

记忆内容（``项目概览`` / ``变更记录`` / ``当前进度`` / ``约定`` / ``关键决策``）全部由模型写，
所以这个模块的职责收得很窄，测试也围着这四条转：

- **散文一个字都不许碰。** 输出是直接覆盖写回记忆文件的，改写时把模型写的任何一句话弄丢，
  都是拿一份缺了几段的记忆去换一份多了个头的记忆。两处**有意**的例外各自有用例钉着：
  文件头那一行由平台重算，老模板留下的「需求列表」「功能列表」整节摘掉。
- **不再生成任何表。** 需求表曾经由平台按 PR 状态回填，那个机制连同表一起退场 ——
  留一条「不许冒出新表」的用例，免得它从别处爬回来。
- **闸门判据只来自 PR 列表**，与记忆里写了什么无关：仓库有开着的 PR 就拦本轮，
  记忆里写「都做完了」也拦。
- **头行的时间戳是平台的钟。** ``reconcile`` 只在真变了时推进它；落盘前另有一次盖章
  （``restamp``），把模型自己写的时间换成写盘那一刻 —— 而判「没变」就得先挖掉这一格
  （``same_except_stamp``），否则那一次盖章会让每轮都写盘。

PR 数据一律是**合成**的（形状照 Gitee，值是我们编的），不碰任何真凭据。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_coding_harness.agent.middleware.memory_reconcile import (
    PENDING,
    reconcile,
    restamp,
    rows_to_verify,
    same_except_stamp,
    strip_legacy,
)

NOW = "2026-09-22 15:10"

#: 仓库根 —— 用来比对两份模版（一份给沙箱、一份给人看）。
REPO = Path(__file__).resolve().parents[1]


def pull(
    number: int,
    *,
    state: str = "open",
    title: str = "",
    branch: str = "",
    merged: bool = False,
) -> dict[str, Any]:
    """一条合成 PR，字段名与形状照 Gitee 列表接口。"""
    return {
        "number": number,
        "state": "merged" if merged else state,
        "merged_at": "2026-09-21T09:30:00+08:00" if merged else None,
        "title": title or f"第 {number} 号改动",
        "html_url": f"https://gitee.com/acme/shop/pulls/{number}",
        "head": {"ref": branch or f"feat/{number}"},
        "base": {"ref": "master"},
    }


MEMORY = """# 仓库记忆：acme/shop

> 最后更新：2026-09-20 08:00　｜　当前分支：master　｜　最近 PR：#12

## 一、项目概览
- **技术栈**：Vue 3 + FastAPI
- **目录结构**：`web/` 前端，`api/` 后端

## 二、变更记录（最新在上）
### 2026-09-20 · 新增 · 商品列表分页
- **涉及文件**：`api/goods.py`
- **PR**：https://gitee.com/acme/shop/pulls/12

## 三、当前进度
**未完成的功能（按模块）**
- **订单**：导出 CSV 还没做
"""

FUNC_TABLE = """## 二、功能列表

| 功能 | 状态 | 相关 PR | 说明 |
|---|---|---|---|
| 商品列表分页 | 已具备 | #12 | 每页 20 条 |
| 订单导出 CSV | 待合入 | #31 | 依赖分页先合入 |
"""

NEED_TABLE = """## 三、需求列表

| 需求 | PR | 分支 | 状态 | 更新于 |
|---|---|---|---|---|
| 商品列表分页 | [#12](https://gitee.com/acme/shop/pulls/12) | feat/12 | 已合入 | 2026-09-21 09:30 |
| 订单导出 CSV | [#31](https://gitee.com/acme/shop/pulls/31) | feat/order-export | 待合入 | 2026-09-20 08:00 |
"""


def legacy_memory() -> str:
    """一份老模板写的记忆：功能列表 + 需求表都在。"""
    head, rest = MEMORY.split("## 二、变更记录", 1)
    return f"{head}{FUNC_TABLE}{NEED_TABLE}\n## 二、变更记录{rest}"


def prose_lines(text: str) -> list[str]:
    """非空、且不是文件头那一行的行。

    头行（``> 最后更新…``）由平台按 PR 现状重算，**不在**「逐字保留」的范围里；
    老记忆里带 ``当前分支`` 的三段式头行会被整行换掉，那是自愈不是丢字。
    """
    return [
        line
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("> 最后更新")
    ]


def table_lines(text: str) -> list[str]:
    """Markdown 表格行 —— 新版记忆里应当**一条都没有**。"""
    return [line for line in text.splitlines() if line.lstrip().startswith("|")]


# --------------------------------------------------------------------- 散文逐字保留


def test_the_prose_is_untouched_when_the_header_moves():
    result = reconcile(MEMORY, [pull(12, merged=True), pull(31)], now=NOW)

    assert result.changed
    for line in prose_lines(MEMORY):
        assert line in result.text, f"散文行丢了：{line!r}"


def test_no_table_is_created_any_more():
    """老版本会给没有表的记忆补一张需求表 —— 现在一张都不补。"""
    result = reconcile(MEMORY, [pull(31, title="库存预警")], now=NOW)

    assert table_lines(result.text) == []
    assert "需求列表" not in result.text
    assert "功能列表" not in result.text
    assert [p.number for p in result.pending] == ["31"]


def test_an_empty_memory_stays_empty():
    result = reconcile("", [pull(31)], now=NOW)

    assert result.changed
    assert table_lines(result.text) == []


# --------------------------------------------------------------------- 摘掉两张老表


def test_both_legacy_tables_are_dropped():
    """功能列表（与变更记录重复）与需求列表（不再维护）都整节摘掉 —— 连标题一起。"""
    result = reconcile(legacy_memory(), [], now=NOW)

    assert "功能列表" not in result.text
    assert "需求列表" not in result.text
    assert "已具备" not in result.text
    assert table_lines(result.text) == []
    assert "\n\n## 二、变更记录" in result.text, "下一个标题前要留一行空行"
    for line in prose_lines(MEMORY):
        assert line in result.text, f"散文行丢了：{line!r}"


def test_dropping_the_legacy_tables_marks_the_memory_as_changed():
    """别的什么都没变时，摘那两节也得算「变了」—— 否则死表永远留在盘上。"""
    pulls = [pull(12, merged=True), pull(31)]
    settled = reconcile(legacy_memory(), pulls, now=NOW)
    assert settled.changed
    assert not reconcile(settled.text, pulls, now=NOW).changed, "这份文本本身该是稳态"

    # 往稳态里塞回一节老表：只这一处差异
    text = settled.text.replace("## 二、变更记录", f"{NEED_TABLE}\n## 二、变更记录", 1)
    result = reconcile(text, pulls, now=NOW)

    assert result.changed
    assert "需求列表" not in result.text


def test_a_prose_section_merely_ending_in_the_name_is_kept():
    """摘的是「去掉节号后正好等于表名」的那一节，模型自己写的历史节不该被牵连。"""
    text = MEMORY + "\n## 四、历史功能列表\n- 2026-08 做过一版\n"

    result = reconcile(text, [], now=NOW)

    assert "## 四、历史功能列表" in result.text
    assert "2026-08 做过一版" in result.text


# --------------------------------------------------------------------- 文件头


def test_the_header_is_rebuilt_and_drops_the_old_branch_field():
    """头行由平台重算：老格式里的 `当前分支` 整行清掉，`最近 PR` 取编号最大的那个。"""
    old = "> 最后更新：2026-09-20 08:00　｜　当前分支：master　｜　最近 PR：#12"

    result = reconcile(MEMORY, [pull(12, merged=True), pull(31)], now=NOW)

    assert old not in result.text
    assert "当前分支" not in result.text
    assert (
        f"> 最后更新：{NOW}　｜　最近 PR：[#31](https://gitee.com/acme/shop/pulls/31)"
        in result.text
    )


def test_a_missing_header_is_added_under_the_title():
    text = MEMORY.replace(
        "> 最后更新：2026-09-20 08:00　｜　当前分支：master　｜　最近 PR：#12\n\n", ""
    )

    result = reconcile(text, [pull(12, merged=True), pull(31)], now=NOW)

    assert result.changed
    assert result.text.splitlines()[:3] == [
        "# 仓库记忆：acme/shop",
        "",
        f"> 最后更新：{NOW}　｜　最近 PR：[#31](https://gitee.com/acme/shop/pulls/31)",
    ]


def test_the_header_says_dash_when_there_is_no_pull_request():
    result = reconcile(MEMORY, [], now=NOW)

    assert f"> 最后更新：{NOW}　｜　最近 PR：—" in result.text


def test_unchanged_memory_is_reported_as_unchanged():
    """第二轮没有任何变化：一个字都不改，**包括文件头的时间戳**。

    时间戳若每轮都推进，``changed`` 就恒为真 —— 「没变就不写盘」当场失效，
    而且「这一轮到底改没改」这个事实也跟着假了。
    """
    pulls = [pull(12, merged=True), pull(31, branch="feat/order-export")]
    first = reconcile(MEMORY, pulls, now=NOW)

    second = reconcile(first.text, pulls, now="2026-09-22 16:00")

    assert not second.changed
    assert second.text == first.text
    assert f"> 最后更新：{NOW}" in second.text
    assert "2026-09-22 16:00" not in second.text


# --------------------------------------------------------------------- 闸门判据


def test_pending_comes_from_the_pull_list_not_the_memory():
    """记忆里写什么都拦不拦：只要仓库还有开着的 PR 就拦。"""
    assert [p.number for p in reconcile(MEMORY, [pull(31)], now=NOW).pending] == ["31"]
    assert reconcile(MEMORY, [], now=NOW).pending == ()


def test_a_pending_pull_carries_what_the_marker_needs():
    (open_pull,) = reconcile(
        MEMORY, [pull(31, title="库存预警", branch="feat/stock")], now=NOW
    ).pending

    assert open_pull.number == "31"
    assert open_pull.title == "库存预警"
    assert open_pull.branch == "feat/stock"
    assert open_pull.url == "https://gitee.com/acme/shop/pulls/31"


def test_merged_and_closed_pulls_do_not_block():
    pulls = [pull(12, merged=True), pull(31, state="closed"), pull(32, state="rejected")]

    assert reconcile(MEMORY, pulls, now=NOW).pending == ()


def test_an_unrecognized_state_is_not_treated_as_open():
    """Gitee 回了我们认不出的状态：不猜 ——既不拦本轮，也不把记忆写成别的结论。"""
    result = reconcile(MEMORY, [pull(31, state="locked")], now=NOW)

    assert result.pending == ()
    assert PENDING not in result.text


def test_a_pull_without_a_number_does_not_block():
    """没编号的条目无从核对，拦下来只会把这一轮白白结束掉。"""
    pulls = [{**pull(31), "number": None}]

    assert reconcile(MEMORY, pulls, now=NOW).pending == ()


# --------------------------------------------------------------------- 落盘盖章


def test_restamp_replaces_the_time_and_keeps_the_rest():
    text = "> 最后更新：2026-09-20 08:00　｜　最近 PR：[#12](https://g/pulls/12)\n正文\n"

    assert restamp(text, now=NOW) == (
        f"> 最后更新：{NOW}　｜　最近 PR：[#12](https://g/pulls/12)\n正文\n"
    )


def test_restamp_keeps_the_legacy_branch_field():
    """只换时间：老格式里那个多余的 ``当前分支`` 归 ``reconcile`` 重建头行时清，不归这一层。"""
    text = "> 最后更新：2026-09-20 08:00　｜　当前分支：master　｜　最近 PR：#12\n"

    assert restamp(text, now=NOW) == (
        f"> 最后更新：{NOW}　｜　当前分支：master　｜　最近 PR：#12\n"
    )


def test_restamp_on_a_header_without_other_fields():
    assert restamp("> 最后更新：2026-09-20 08:00\n", now=NOW) == f"> 最后更新：{NOW}\n"


def test_restamp_without_a_header_changes_nothing():
    """补头行是 ``reconcile`` 的事 —— 只有它知道「最近 PR」该写什么，
    这里凭空补一行，等于替仓库断言「没有 PR」。"""
    text = "# 记忆\n\n正文\n"

    assert restamp(text, now=NOW) == text


def test_same_except_stamp_ignores_only_the_time():
    old = "> 最后更新：2026-09-20 08:00　｜　最近 PR：#12\n正文\n"
    new = "> 最后更新：2026-09-23 09:41　｜　最近 PR：#12\n正文\n"

    assert same_except_stamp(old, new)
    assert not same_except_stamp(old, new.replace("正文", "正文改了"))
    assert not same_except_stamp(old, new.replace("#12", "#13"))


def test_same_except_stamp_is_strict_when_a_header_is_missing():
    """缺头行是结构差异，不是时间差异 —— 那份该照写，不能因为「两边时间都是空的」判成没变。"""
    assert not same_except_stamp(
        "# 记忆\n\n正文\n", f"> 最后更新：{NOW}\n\n# 记忆\n\n正文\n"
    )


def test_the_forged_stamp_does_not_survive_a_write():
    """对账器对「内容没变」的记忆是恒等变换（模型写的时间也照留），盖章在它之后。"""
    link = "[#12](https://gitee.com/acme/shop/pulls/12)"
    text = f"> 最后更新：2026-02-12 03:07　｜　最近 PR：{link}\n\n## 三、当前进度\n- 导出没做\n"

    result = reconcile(text, [pull(12)], now=NOW)

    assert result.changed is False, "对账这一层不认时间变了"
    assert "2026-02-12 03:07" in result.text, "它原样返回，是调用方在后面盖章"
    assert f"> 最后更新：{NOW}" in restamp(result.text, now=NOW)


# ---------------------------------------------- 变更记录里的 PR 状态（2026-09-26）

#: 一份带编号与状态的变更记录：#31 写着「待合并」、#30 写着「已合并」；
#: 末节还有一条**长得像 PR 行但不在变更记录里**的散文（用来钉住「只扫变更记录」）。
RECORDS = """# 仓库记忆：acme/shop

> 最后更新：2026-09-20 08:00　｜　最近 PR：#31

## 一、项目概览
- **技术栈**：Vue 3 + FastAPI

## 二、变更记录（最新在上，以 PR 为角度）
### 2026-09-20 · 新增 · 商品列表分页
- **PR**：[#31](https://gitee.com/acme/shop/pulls/31)　｜　**状态**：待合并
- **涉及文件**：`api/goods.py`

### 2026-09-19 · 修改 · 订单导出
- **PR**：[#30](https://gitee.com/acme/shop/pulls/30)　｜　**状态**：已合并
- **涉及文件**：`api/orders.py`

## 四、关键决策
- 导出走异步任务 —— 见 [#31](https://gitee.com/acme/shop/pulls/31)
"""


def test_rows_to_verify_reads_only_the_change_log():
    """要核对的编号：只扫「变更记录」，且**已经说清结局的不算**。"""
    assert rows_to_verify(RECORDS) == ("31",), "已合并的 #30 不必再查，#31 还写着待合并"


def test_rows_to_verify_counts_a_row_without_a_status():
    """老格式只写了 PR、没写状态那一格 → 算「要核对」：不确定不能当成已经完事。"""
    text = RECORDS.replace("　｜　**状态**：待合并", "")

    assert rows_to_verify(text) == ("31",)


def test_rows_to_verify_skips_a_row_without_a_number():
    """没编号的条目无从核对（也就无从改写），跳过。"""
    text = RECORDS.replace(
        "- **PR**：[#31](https://gitee.com/acme/shop/pulls/31)　｜　**状态**：待合并",
        "- **PR**：https://gitee.com/acme/shop/pulls/31　｜　**状态**：待合并",
    )

    assert rows_to_verify(text) == ()


def test_a_merged_pull_rewrites_only_the_status_cell():
    result = reconcile(RECORDS, [pull(31, merged=True), pull(30, merged=True)], now=NOW)

    assert result.changed
    assert (
        "- **PR**：[#31](https://gitee.com/acme/shop/pulls/31)　｜　**状态**：已合并"
        in result.text
    )
    assert result.restated == ("31",)
    assert "- **涉及文件**：`api/goods.py`" in result.text, "同行其余字段一个字节都不碰"
    assert "见 [#31](https://gitee.com/acme/shop/pulls/31)" in result.text, "别的节不碰"


def test_a_closed_pull_becomes_closed():
    result = reconcile(RECORDS, [pull(31, state="closed")], now=NOW)

    assert "**状态**：已关闭" in result.text
    assert result.restated == ("31",)


def test_the_status_cell_may_skip_the_bold():
    """状态那一格不加粗也认（认的是这一格，不是排版）；同行后面的字段照旧留着。"""
    text = RECORDS.replace(
        "- **PR**：[#31](https://gitee.com/acme/shop/pulls/31)　｜　**状态**：待合并",
        "- **PR**：[#31](https://gitee.com/acme/shop/pulls/31)　｜　状态：待合并　｜　**分支**：feat/31",
    )

    result = reconcile(text, [pull(31, merged=True)], now=NOW)

    assert "状态：已合并　｜　**分支**：feat/31" in result.text
    assert result.restated == ("31",)


def test_a_still_open_pull_keeps_the_row():
    result = reconcile(RECORDS, [pull(31, title="库存预警")], now=NOW)

    assert "**状态**：待合并" in result.text
    assert result.restated == ()


def test_an_unrecognized_live_state_leaves_the_row_alone():
    """Gitee 回了认不出的状态：不猜，也不改这一格。"""
    result = reconcile(RECORDS, [pull(31, state="locked")], now=NOW)

    assert "**状态**：待合并" in result.text
    assert result.restated == ()


def test_a_row_is_rewritten_when_live_state_disagrees():
    """记忆状态与平台不一致时按平台实况改写。"""
    result = reconcile(RECORDS, [pull(30, state="closed")], now=NOW)

    assert "**状态**：已关闭" in result.text
    assert result.restated == ("30",)


def test_the_status_rewrite_alone_marks_the_memory_as_changed():
    """头行本来就是对的、只有状态那一格变了 —— 那也是变了，必须写盘。"""
    header = f"> 最后更新：{NOW}　｜　最近 PR：[#31](https://gitee.com/acme/shop/pulls/31)"
    text = RECORDS.replace("> 最后更新：2026-09-20 08:00　｜　最近 PR：#31", header)

    result = reconcile(text, [pull(31, merged=True), pull(30, merged=True)], now=NOW)

    assert result.changed, "只改状态也得算变了，否则盘上那份永远是旧的"
    assert "**状态**：已合并" in result.text


def test_restating_is_idempotent():
    """改过一遍之后再对一次账：没有新变化，一个字都不动。"""
    pulls = [pull(31, merged=True), pull(30, merged=True)]
    first = reconcile(RECORDS, pulls, now=NOW)

    second = reconcile(first.text, pulls, now=NOW)

    assert not second.changed
    assert second.restated == ()
    assert second.text == first.text


def test_strip_legacy_is_identity_without_the_old_tables():
    """没有老表时逐字返回 —— 空串、缺尾换行都原样。"""
    assert strip_legacy(RECORDS) == RECORDS
    assert strip_legacy("") == ""
    assert strip_legacy("# 记忆\n\n正文") == "# 记忆\n\n正文"


def test_strip_legacy_drops_the_old_tables():
    text = strip_legacy(legacy_memory())

    assert "需求列表" not in text and "功能列表" not in text
    assert "## 二、变更记录" in text
    assert "- **技术栈**：Vue 3 + FastAPI" in text


# --------------------------------------------------------------------- 模版


def test_the_two_template_copies_are_identical():
    """沙箱里那份（真正生效的）与 ``docs/`` 那份曾是两套内容，跑偏了三周。

    现在两份逐字一致：改模版必须两处一起改，否则这条当场红。
    """
    sandbox = (REPO / "sandbox" / "policies" / "repo-memory-template.md").read_text(
        encoding="utf-8"
    )
    docs = (REPO / "docs" / "repo-memory-template.md").read_text(encoding="utf-8")

    assert sandbox == docs


def test_the_template_asks_for_the_unfinished_features():
    """「当前缺口」里按模块列未完成的功能 —— 模版与提示词都得这么说，否则模型不会写。

    这一节 2026-09-26 从「当前进度」改名成「当前缺口」：它是**分析结论**的一半（这个仓库现在
    缺什么），与「仓库基本信息」一起来自按 skill 做的分析，所以留在记忆里而不是另存文件。
    """
    text = (REPO / "sandbox" / "policies" / "repo-memory-template.md").read_text(
        encoding="utf-8"
    )

    assert "## 三、当前缺口（按模块）" in text
    assert "未完成的功能" in text
    assert 'section="gaps"' in text, "按节增量写的口径要写在模版里"


def test_the_template_records_the_pr_status_but_never_a_pr_less_entry():
    """变更记录每条都写 PR 与状态，且**没有**「没有 PR」这一档（2026-09-26 用户口径）。

    理由：写码与提 PR 是同一条流程，一轮改动一定带着一个 PR；留一档「未提 PR」等于给模型
    一个「这轮可以先不提 PR」的正当理由。状态则先由模型填「待合并」，之后**由平台按代码平台
    （Gitee / GitHub）的实况改**（已合并 / 已关闭，2026-09-26 起 —— 此前靠人工）。
    """
    text = (REPO / "sandbox" / "policies" / "repo-memory-template.md").read_text(
        encoding="utf-8"
    )

    assert "- **PR**：{#31}　｜　**状态**：{待合并 | 已合并 | 已关闭}" in text
    assert "之后由平台按代码平台实况改" in text
    assert "这条**不写**" in text, "PR 没建成时的处置口径要写在模版里"
    assert "未提 PR" not in text, "「没有 PR」这一档已经取消，别再回来"
