"""把 coding 模式那张时序图导成 **draw.io 可直接打开**的 ``.drawio`` 文件。

数据与 ``docs/business-flow.md`` §五 那张 Mermaid 图同源（参与者 + 逐条消息），几何在这里
算：每条消息一行、每个阶段一条灰带、``alt`` / ``loop`` 画成方框（``else`` 用虚线分隔）。
**改图时两处一起改** —— Mermaid 是给人读的源码，这里是给 draw.io 打开的产物。

用法::

    python scripts/make_drawio.py                    # 写 docs/coding-sequence.drawio
    python scripts/make_drawio.py --out <路径>        # 换个落点

产物用法（三选一）：

1. draw.io 桌面版 / 网页版直接打开 ``.drawio``（原生格式，可继续编辑）；
2. 或把 ``docs/business-flow.md`` §五 的 Mermaid 源码贴进 draw.io 的
   「Extras → Edit Diagram」/「Insert → Advanced → Mermaid」；
3. 要嵌进网页/文档：draw.io 里另存为 ``.drawio.svg``（SVG 里带回编辑用的 XML）。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from xml.sax.saxutils import escape

#: 参与者：``(名字, 副标题)`` —— 与图上那一排表头一一对应。
LANES: tuple[tuple[str, str], ...] = (
    ("用户 · 前端", "点发送 / 收 SSE"),
    ("后端 API", "路由 + RunHub"),
    ("service", "意图 · 分发 · 收尾"),
    ("中间件", "记忆 · 边界"),
    ("主控模型", ""),
    ("子智能体", "planner / coder / reviewer"),
    ("工具 · 沙箱", ""),
    ("Gitee · Postgres", ""),
)

#: 逐行内容，与 Mermaid 那张图同序：
#: ``("p", 阶段名)`` / ``("m", 发出方, 接收方, 说明)`` / ``("r", ...)`` 返回 /
#: ``("s", 泳道, 说明)`` 自调用 / ``("alt"|"loop", 名)`` / ``("else", 名)`` / ``("end",)``。
ROWS: tuple[tuple, ...] = (
    ("p", "① 建会话（每个仓库一份活跃会话）"),
    ("m", 0, 1, "POST /sessions（仓库地址 + 授权）"),
    ("m", 1, 7, "写 sessions + repo（token 落库）"),
    ("m", 1, 6, "后台建沙箱（约 10s）"),
    ("r", 1, 0, "session_id + workspace_status=pending"),
    ("m", 0, 1, "轮询到 ready（重连 / 按需续期）"),
    ("p", "② 发消息（SSE：断线不影响这一轮）"),
    ("m", 0, 1, "发送「按这个方案做 / 实施」→ POST /chat/{id}"),
    ("s", 1, "RunHub.start 起独立 task（同会话在跑 → 409）"),
    ("m", 1, 2, "service.chat：凭据拒收 → 意图分类 → 记 user 记录"),
    ("r", 1, 0, "SSE 开始（帧按序号；刷新后带 after 接回）"),
    ("p", "③ 开工前（记忆闸门、工具边界）"),
    ("s", 2, "ensure_runtime（沙箱重连 / 续期）"),
    ("m", 2, 3, "读记忆；只有「有待核对的 PR」才查 Gitee"),
    ("m", 3, 7, "all_pulls ＋ 刷新文件头 / PR 状态"),
    ("alt", "alt 记忆闸门"),
    ("r", 3, 2, "有未合入 PR → TurnStop（提示等人工合并）"),
    ("else", "放行"),
    ("r", 3, 2, "按形态裁工具（越界调用当场拒）"),
    ("end",),
    ("p", "④ 跑这一轮（模型 ↔ 工具，直到收尾）"),
    ("loop", "loop 模型 ↔ 工具"),
    ("m", 2, 4, "astream_events（系统提示 + 记忆 + 时间与意图）"),
    ("r", 4, 2, "正文 / 工具调用"),
    ("m", 2, 6, "执行工具（脱敏 + 错误兜底）"),
    ("end",),
    ("s", 2, "get_unexecuted_plan（主控先查有没有可执行的方案）"),
    ("alt", "alt 有没有可执行的方案"),
    ("m", 2, 5, "有 → task：coder"),
    ("else", "没有"),
    ("m", 2, 5, "task：planner"),
    ("m", 5, 7, "storePlan 落库（status=pending）"),
    ("m", 2, 5, "task：coder（用户已说直接做 → 不等确认）"),
    ("end",),
    ("m", 5, 6, "改文件 / 跑测试 / commit + push"),
    ("m", 5, 7, "create_pr（同分支已有未关闭 PR 则复用）"),
    ("r", 7, 5, "PR 链接 + 编号"),
    ("r", 5, 2, "回报：PR 编号 / 链接 + 验证结果"),
    ("m", 2, 5, "task：reviewer"),
    ("m", 5, 7, "get_pr_info / list_pr_comments"),
    ("m", 5, 7, "comment_pr（审查结论与问题清单）"),
    ("r", 5, 2, "回报问题清单"),
    ("p", "⑤ 收尾（先呈结论，再写记忆，最后平台打底）"),
    ("r", 2, 0, "ai 帧：审查结论原样呈给用户"),
    ("m", 2, 6, "save_repo_memory（本轮的最后一件事）"),
    ("m", 6, 7, "all_pulls（写盘前核对状态）"),
    ("m", 6, 7, "覆盖写记忆（文件头由平台盖章）"),
    ("m", 2, 3, "轮末打底（按 Gitee 实况再刷一遍）"),
    ("m", 2, 7, "写 assistant 记录（正文 + 待办）"),
    ("r", 2, 1, "done"),
    ("r", 1, 0, "done 帧 → 渲染这一轮（正文 + 待办树 + 方案面板）"),
    ("p", "⑥ 之后（本轮已结束，这些是另外的请求）"),
    ("m", 0, 1, "终止 / 刷新接回 / 删会话"),
    ("m", 0, 7, "人工在 Gitee 合并 PR"),
    ("r", 7, 0, "下一轮刷新时状态改成「已合并」"),
)

# 版面常量（draw.io 里的绝对坐标，单位 px）
_LANE_GAP = 150
_LANE_LEFT = 90
_BOX_W = 130
_BOX_H = 42
_BOX_TOP = 16
#: 每条消息占的高度：**箭头上方要留出标签那一行**（draw.io 的边标签默认压在线上，
#: 41 条消息挤在 34px 行距里会互相压 —— 所以标签另画成一行文本，行距跟着放大）。
_ROW_H = 44
_LABEL_H = 18
_PHASE_H = 26
_GROUP_PAD = 18
_MARGIN_BOTTOM = 40

_CALL = "endArrow=block;endFill=1;html=1;strokeColor=#4D6BFE;"
_REPLY = "endArrow=open;html=1;dashed=1;strokeColor=#7A7A7A;"
_LIFELINE = "endArrow=none;dashed=1;strokeColor=#B3B3B3;html=1;"
#: 消息文字另画一格（白底、无边框），落在箭头上方 —— 与 Mermaid 的排法一致。
_LABEL = (
    "rounded=0;html=1;fillColor=#FFFFFF;strokeColor=none;align=center;"
    "verticalAlign=middle;fontSize=12;"
)


def _lane_x(index: int) -> float:
    """第 ``index`` 条泳道的中轴 x。"""
    return _LANE_LEFT + index * _LANE_GAP


def _label(rows: tuple) -> str:
    kind = rows[0]
    return str(rows[2] if kind == "s" else rows[3])


def _text_width(text: str) -> int:
    """粗略估宽（中文按 12px、其余按 6.6px）—— 只用来给白底标签定格子大小。"""
    total = 0.0
    for ch in text:
        total += 12.0 if ord(ch) > 0x2E80 else 6.6
    return int(total) + 12


def build() -> str:
    """算出每个元素的位置，拼出 ``.drawio``（mxGraphModel）XML。"""
    cells: list[str] = []
    lane_count = len(LANES)
    width = _lane_x(lane_count - 1) + _BOX_W + 60

    y = _BOX_TOP + _BOX_H + 40
    # 先把每一行的 y 算出来，才能知道阶段带与分组框该覆盖到哪
    placed: list[tuple[int, int]] = []  # (行下标, y)
    cursor = y
    for i, row in enumerate(ROWS):
        kind = row[0]
        if kind == "p":
            placed.append((i, cursor))
            cursor += _PHASE_H + 14
        elif kind in ("alt", "loop", "else"):
            placed.append((i, cursor))
            cursor += _GROUP_PAD
        elif kind == "end":
            placed.append((i, cursor))
            cursor += 12
        else:
            placed.append((i, cursor))
            cursor += _ROW_H
    bottom = cursor + _MARGIN_BOTTOM

    def y_of(row_index: int) -> int:
        for index, value in placed:
            if index == row_index:
                return value
        raise KeyError(row_index)

    # 表头 + 生命线（生命线是一条从表头到页面底部的虚线边）
    for i, (name, sub) in enumerate(LANES):
        x = _lane_x(i)
        text = escape(name if not sub else f"{name}<br><font style='font-size:10px' color='#7A7A7A'>{sub}</font>")
        cells.append(
            f'<mxCell id="lane{i}" value="{text}" '
            'style="rounded=1;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#B3B3B3;fontSize=12;fontStyle=1;" '
            f'vertex="1" parent="1"><mxGeometry x="{x - _BOX_W / 2:.0f}" y="{_BOX_TOP}" '
            f'width="{_BOX_W}" height="{_BOX_H}" as="geometry"/></mxCell>'
        )
        cells.append(
            f'<mxCell id="lifeline-end{i}" style="fillColor=none;strokeColor=none;html=1;" '
            f'vertex="1" parent="1"><mxGeometry x="{x:.0f}" y="{bottom}" width="1" height="1" as="geometry"/></mxCell>'
        )
        cells.append(
            f'<mxCell id="lifeline{i}" style="{_LIFELINE}" edge="1" parent="1" source="lane{i}" '
            f'target="lifeline-end{i}"><mxGeometry relative="1" as="geometry"/></mxCell>'
        )

    # 阶段带：从本阶段第一条到下一个阶段之前
    phase_rows = [i for i, row in enumerate(ROWS) if row[0] == "p"]
    for order, row_index in enumerate(phase_rows):
        start = y_of(row_index) - 2
        end = y_of(phase_rows[order + 1]) - 10 if order + 1 < len(phase_rows) else bottom - 20
        cells.append(
            f'<mxCell id="phase{order}" value="{escape(str(ROWS[row_index][1]))}" '
            'style="fillColor=#F5F5F5;strokeColor=none;html=1;align=left;verticalAlign=middle;'
            'spacingLeft=10;fontSize=12;fontStyle=1;" vertex="1" parent="1">'
            f'<mxGeometry x="10" y="{start}" width="{width - 20}" height="{end - start}" as="geometry"/></mxCell>'
        )

    # 分组框（alt / loop）+ else 分隔虚线
    open_groups: list[tuple[int, int, str]] = []  # (起始 y, 行下标, 名字)
    groups: list[str] = []
    dividers: list[str] = []
    for i, row in enumerate(ROWS):
        kind = row[0]
        if kind in ("alt", "loop"):
            open_groups.append((y_of(i), i, str(row[1])))
        elif kind == "else" and open_groups:
            start, _, _ = open_groups[-1]
            dividers.append(
                f'<mxCell id="divider{i}" value="{escape(str(row[1]))}" '
                'style="endArrow=none;html=1;dashed=1;strokeColor=#7A7A7A;fontSize=11;'
                'labelBackgroundColor=#FFFFFF;" '
                f'edge="1" parent="1"><mxGeometry relative="1" as="geometry">'
                f'<mxPoint x="20" y="{y_of(i)}" as="sourcePoint"/>'
                f'<mxPoint x="{width - 20}" y="{y_of(i)}" as="targetPoint"/></mxGeometry></mxCell>'
            )
        elif kind == "end" and open_groups:
            start, start_row, name = open_groups.pop()
            end = y_of(i)
            groups.append(
                f'<mxCell id="group{start_row}" value="{escape(name)}" '
                'style="rounded=1;html=1;fillColor=none;strokeColor=#B3B3B3;align=left;verticalAlign=top;'
                'spacingLeft=8;spacingTop=4;fontSize=11;fontColor=#7A7A7A;" vertex="1" parent="1">'
                f'<mxGeometry x="14" y="{start}" width="{width - 28}" height="{end - start}" as="geometry"/></mxCell>'
            )

    # 消息
    for i, row in enumerate(ROWS):
        kind = row[0]
        if kind in ("p", "alt", "loop", "else", "end"):
            continue
        y_row = y_of(i)
        text = _label(row)
        label_w = _text_width(text)
        if kind == "s":
            x = _lane_x(int(row[1]))
            cells.append(
                f'<mxCell id="msg{i}" style="{_CALL}" edge="1" parent="1">'
                f'<mxGeometry relative="1" as="geometry">'
                f'<mxPoint x="{x:.0f}" y="{y_row}" as="sourcePoint"/>'
                f'<mxPoint x="{x + 26:.0f}" y="{y_row + 20}" as="targetPoint"/>'
                f'<Array as="points"><mxPoint x="{x + 26:.0f}" y="{y_row}"/>'
                f'<mxPoint x="{x + 26:.0f}" y="{y_row + 20}"/></Array>'
                f'</mxGeometry></mxCell>'
            )
            label_x = x + 34
            if label_x + label_w > width - 12:
                label_x = width - 12 - label_w
            label_y = y_row - 8
        else:
            from_x = _lane_x(int(row[1]))
            to_x = _lane_x(int(row[2]))
            style = _REPLY if kind == "r" else _CALL
            cells.append(
                f'<mxCell id="msg{i}" style="{style}" edge="1" parent="1">'
                f'<mxGeometry relative="1" as="geometry">'
                f'<mxPoint x="{from_x:.0f}" y="{y_row}" as="sourcePoint"/>'
                f'<mxPoint x="{to_x:.0f}" y="{y_row}" as="targetPoint"/>'
                f'</mxGeometry></mxCell>'
            )
            label_x = (from_x + to_x) / 2 - label_w / 2
            label_x = max(12, min(label_x, width - 12 - label_w))
            label_y = y_row - _LABEL_H - 2
        cells.append(
            f'<mxCell id="msgtext{i}" value="{escape(text)}" style="{_LABEL}" vertex="1" parent="1">'
            f'<mxGeometry x="{label_x:.0f}" y="{label_y:.0f}" width="{label_w}" '
            f'height="{_LABEL_H}" as="geometry"/></mxCell>'
        )

    body = "\n        ".join(cells + dividers + groups)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<mxfile host="app.diagrams.net" agent="ai-coding-harness" version="24.7.17" '
        'type="device">\n'
        '  <diagram id="coding-sequence" name="coding 时序图">\n'
        f'    <mxGraphModel dx="1200" dy="800" grid="0" gridSize="10" guides="1" tooltips="1" '
        f'connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="{width}" '
        f'pageHeight="{bottom + 40}" math="0" shadow="0">\n'
        "      <root>\n"
        '        <mxCell id="0" />\n'
        '        <mxCell id="1" parent="0" />\n'
        f"        {body}\n"
        "      </root>\n"
        "    </mxGraphModel>\n"
        "  </diagram>\n"
        "</mxfile>\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 draw.io 可打开的时序图")
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parents[1] / "docs" / "coding-sequence.drawio"),
        help="输出路径（默认 docs/coding-sequence.drawio）",
    )
    args = parser.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(), encoding="utf-8")
    tags = build().count("<mxCell")
    print(f"已写入 {out}（{tags} 个 mxCell）")


if __name__ == "__main__":
    main()
