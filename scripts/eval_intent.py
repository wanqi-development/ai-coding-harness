"""意图判定评测：收集真实样本、跑现有判定、出准确率与混淆矩阵。

用法（在仓库根目录）：

    $env:PYTHONPATH='src'
    conda run -n aicoding --no-capture-output python scripts/eval_intent.py collect \
        --out docs/intent-eval.jsonl --limit 200

    # 人工把每行的 expected 填成期望意图（可留空，留空的样本不参与统计）
    conda run -n aicoding --no-capture-output python scripts/eval_intent.py run \
        docs/intent-eval.jsonl

``collect`` / ``enrich`` 只读业务库；``run`` 只读一次库里的模型配置（不写库）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ai_coding_harness.agent.llm import build_classifier_model
from ai_coding_harness.config import Settings
from ai_coding_harness.db import Database
from ai_coding_harness.db.models import SessionRecord, SessionRecordDetail
from ai_coding_harness.server.intent import (
    build_jev_decider,
    classify,
    detect_intent,
    head_tail_excerpt,
)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    """兼容两种写法：pretty JSON 数组（便于人工复核）与一行一个对象的 JSONL。"""
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("["):
        data = json.loads(text)
        return [row for row in data if isinstance(row, dict)]
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    """统一写成缩进 JSON 数组；读取端两种都认。"""
    path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def format_file(path: Path) -> None:
    rows = _read_rows(path)
    _write_rows(path, rows)
    print(f"已格式化 {len(rows)} 条 → {path}（缩进 JSON 数组）")


async def collect(out: Path, limit: int) -> None:
    """把最近的用户消息导成待标注 JSONL（只读业务库）。"""
    settings = Settings()
    database = Database(settings)
    try:
        async with database.session() as db:
            rows = (
                await db.execute(
                    select(
                        SessionRecord.id,
                        SessionRecord.session_id,
                        SessionRecord.created_at,
                        SessionRecordDetail.content,
                        SessionRecordDetail.extra,
                    )
                    .join(
                        SessionRecordDetail,
                        SessionRecordDetail.record_id == SessionRecord.id,
                    )
                    .where(SessionRecord.type == "user")
                    .order_by(SessionRecord.id.desc())
                    .limit(limit)
                )
            ).all()
    finally:
        await database.dispose()

    out.parent.mkdir(parents=True, exist_ok=True)
    out_rows: list[dict[str, Any]] = []
    for record_id, session_id, created_at, message, extra in rows:
        if not isinstance(message, str) or not message.strip():
            continue
        extra = extra if isinstance(extra, dict) else {}
        out_rows.append(
            {
                "message": message.strip(),
                "expected": "",
                "current_intent": extra.get("intent"),
                "current_source": extra.get("intent_source"),
                "previous_intent": extra.get("previous_intent"),
                "previous_reply": "",
                "history": [],
                "record_id": record_id,
                "session_id": session_id,
                "created_at": created_at.isoformat() if created_at else None,
            }
        )
    _write_rows(out, out_rows)
    print(f"已写出 {len(out_rows)} 条待标注样本 → {out}")
    print("把 expected 填成 coding / review / rework / plan / qa / analysis / sync 之一；留空不统计。")


async def enrich(path: Path) -> None:
    """给已有标注文件补上最近两轮对话（只读业务库、保留 expected）。"""
    rows = _read_rows(path)
    settings = Settings()
    database = Database(settings)
    touched = 0
    try:
        async with database.session() as db:
            for row in rows:
                record_id = int(row.get("record_id") or 0)
                session_id = int(row.get("session_id") or 0)
                if not record_id or not session_id:
                    continue
                conv = (
                    await db.execute(
                        select(
                            SessionRecord.type,
                            SessionRecordDetail.extra,
                            SessionRecordDetail.content,
                        )
                        .join(
                            SessionRecord,
                            SessionRecord.id == SessionRecordDetail.record_id,
                        )
                        .where(
                            SessionRecord.session_id == session_id,
                            SessionRecord.type.in_(("user", "assistant")),
                            SessionRecord.id < record_id,
                        )
                        .order_by(SessionRecord.id.desc())
                        .limit(4)
                    )
                ).all()
                conv = list(reversed(conv))
                if not conv:
                    continue
                row["dialogue"] = [
                    {
                        "role": str(kind),
                        "content": str(content)
                        if kind == "user"
                        else head_tail_excerpt(str(content)),
                    }
                    for kind, _extra, content in conv
                    if isinstance(content, str) and content.strip()
                ]
                row["history"] = [
                    str(content)
                    for kind, _extra, content in conv
                    if kind == "user" and isinstance(content, str)
                ]
                last_assistant = next(
                    (
                        (extra, content)
                        for kind, extra, content in reversed(conv)
                        if kind == "assistant"
                    ),
                    None,
                )
                if last_assistant is not None:
                    extra, content = last_assistant
                    if isinstance(extra, dict) and extra.get("intent"):
                        row["previous_intent"] = extra["intent"]
                    if isinstance(content, str) and content.strip():
                        row["previous_reply"] = head_tail_excerpt(content)
                touched += 1
    finally:
        await database.dispose()

    _write_rows(path, rows)
    print(f"已补上最近两轮对话：{touched}/{len(rows)} 条 → {path}")


def _print_matrix(name: str, pairs: list[tuple[str, str]]) -> None:
    """按 expected（行）× predicted（列）打印一张紧凑表。"""
    labels = sorted({label for pair in pairs for label in pair})
    if not labels:
        print(f"\n{name}：没有可统计的样本")
        return
    table: dict[tuple[str, str], int] = Counter(pairs)
    width = max(7, *(len(label) for label in labels))
    print(f"\n{name}（行=期望，列=预测）")
    print(" " * (width + 2) + " ".join(f"{label:>{width}}" for label in labels))
    for expected in labels:
        cells = " ".join(f"{table.get((expected, label), 0):>{width}}" for label in labels)
        print(f"{expected:>{width}}  {cells}")


async def _load_model_endpoints(settings: Settings) -> None:
    """独立脚本不会走 app 启动钩子；从库里只读载入「模型配置」那几行。"""
    database = Database(settings)
    try:
        from ai_coding_harness.api.app import apply_model_endpoints

        await apply_model_endpoints(settings, database)
    except Exception as exc:  # noqa: BLE001
        print(f"读取库里的模型配置失败（继续用 .env / 空配置）：{type(exc).__name__}: {exc}")
    finally:
        await database.dispose()


def _classifier_or_none(settings: Settings) -> Any:
    if not settings.intent_llm_enabled:
        return None
    try:
        return build_classifier_model(settings)
    except Exception as exc:  # noqa: BLE001
        print(f"分类模型不可用：{type(exc).__name__}: {exc}")
        return None


def _accuracy(pairs: list[tuple[str, str]]) -> tuple[int, int, float]:
    correct = sum(1 for expected, got in pairs if expected == got)
    total = len(pairs)
    return correct, total, (correct / total if total else 0.0)


async def run(
    path: Path,
    limit: int = 0,
    ids: set[int] | None = None,
    out: Path | None = None,
) -> None:
    rows = _read_rows(path)
    labelled = [row for row in rows if str(row.get("expected") or "").strip()]
    if ids:
        labelled = [row for row in labelled if int(row.get("record_id") or 0) in ids]
    if limit > 0:
        labelled = labelled[:limit]
    print(f"读取 {len(rows)} 条，其中已标注 {len(labelled)} 条；未标注的不参与统计。")
    if not labelled:
        print("没有 expected，先填标注再跑。")
        return

    settings = Settings()
    # 模型连接在库里（界面「模型配置」），独立脚本不会经过 app 启动钩子，先只读载入一次
    await _load_model_endpoints(settings)
    try:
        jev = build_jev_decider(settings)
    except Exception as exc:  # noqa: BLE001
        print(f"JEV 不可用：{type(exc).__name__}: {exc}")
        jev = None
    model = _classifier_or_none(settings)
    print(f"模型：分类={'有' if model else '无'}；JEV={'有' if jev else '无'}")

    keyword_pairs: list[tuple[str, str]] = []
    normal_pairs: list[tuple[str, str]] = []
    details: list[dict[str, Any]] = []

    for row in labelled:
        message = str(row.get("message") or "").strip()
        expected = str(row["expected"]).strip()
        history = [str(item) for item in (row.get("history") or [])]
        previous_reply = row.get("previous_reply") or None
        previous_intent = row.get("previous_intent") or None
        dialogue = row.get("dialogue") or []

        keyword = classify(message)
        normal_intent = ""
        normal_source = ""
        normal_confidence = None
        use_usage: dict[str, int] = {}
        normal = await detect_intent(
            message,
            history=history,
            jev=jev,
            model=model,
            timeout=settings.intent_timeout_seconds,
            usage=use_usage,
            previous_reply=previous_reply,
            previous_intent=previous_intent,
            dialogue=dialogue,
            mode=settings.intent_mode,
        )
        normal_intent = normal.intent
        normal_source = normal.source
        normal_confidence = normal.confidence

        keyword_pairs.append((expected, keyword))
        normal_pairs.append((expected, normal_intent))
        details.append(
            {
                "expected": expected,
                "message": message,
                "keyword": keyword,
                "normal": normal_intent,
                "normal_source": normal_source,
                "normal_confidence": normal_confidence,
                "previous_intent": previous_intent,
                "record_id": row.get("record_id"),
            }
        )

    methods = [("keyword", keyword_pairs), ("normal", normal_pairs)]
    for name, pairs in methods:
        correct, total, accuracy = _accuracy(pairs)
        print(f"\n{name}: {correct}/{total} = {accuracy:.1%}")
        _print_matrix(name, pairs)
    threshold = float(settings.intent_clarify_confidence or 0)
    low = [
        item
        for item in details
        if item["normal_confidence"] is not None
        and item["normal_source"] in {"jev", "llm"}
        and item["normal_confidence"] < threshold
    ]
    if low:
        correct = sum(1 for item in low if item["normal"] == item["expected"])
        print(
            f"\nclarify 模拟：{len(low)} 条 confidence < {threshold:.2f}，"
            f"其中原判定 {correct}/{len(low)} 正确"
        )

    print("\n错例（最多 20 条，便于补评测集 / 调提示词与词典）")
    shown = 0
    for item in details:
        if item["normal"] == item["expected"]:
            continue
        if shown >= 20:
            break
        shown += 1
        print(
            f"- expected={item['expected']} normal={item['normal'] or '-'}"
            f" ({item['normal_source']})\n  {item['message']}"
        )
    if shown == 0:
        print("- 没有错例（在当前标注集上）")

    if out is not None:
        out.write_text(
            json.dumps(details, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\n已保存 {len(details)} 条逐样本结果 → {out}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="意图判定评测")
    sub = parser.add_subparsers(dest="command", required=True)
    p_collect = sub.add_parser("collect", help="从业务库导出待标注用户消息")
    p_collect.add_argument("--out", required=True, type=Path, help="输出文件路径")
    p_collect.add_argument("--limit", type=int, default=200, help="最多导出多少条")
    p_run = sub.add_parser("run", help="跑已标注文件，输出准确率与混淆矩阵")
    p_run.add_argument("file", type=Path, help="已标注 JSON / JSONL")
    p_run.add_argument("--limit", type=int, default=0, help="只跑前 N 条已标注样本（0=全部）")
    p_run.add_argument(
        "--ids",
        default="",
        help="只跑指定 record_id（逗号分隔，便于迭代错例）",
    )
    p_run.add_argument(
        "--out",
        type=Path,
        default=None,
        help="保存逐样本结果（JSON）",
    )
    p_enrich = sub.add_parser("enrich", help="给已有标注文件补上一轮意图与回复末尾")
    p_enrich.add_argument("file", type=Path, help="已标注 JSON / JSONL（原地更新）")
    p_format = sub.add_parser("format", help="把 JSONL / JSON 统一格式化成缩进 JSON 数组")
    p_format.add_argument("file", type=Path, help="待格式化文件（原地更新）")
    args = parser.parse_args(argv)

    if args.command == "collect":
        _run(collect(args.out, args.limit))
    elif args.command == "enrich":
        _run(enrich(args.file))
    elif args.command == "format":
        format_file(args.file)
    else:
        ids = {
            int(part)
            for part in args.ids.split(",")
            if part.strip().isdigit()
        }
        _run(
            run(
                args.file,
                args.limit,
                ids or None,
                args.out,
            )
        )
    return 0


def _run(coro: Any) -> None:
    """Windows 上 psycopg 异步要求 SelectorEventLoop（与 ``main.py`` 同一口径）。"""
    if sys.platform == "win32":
        asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(coro)


if __name__ == "__main__":
    sys.exit(main())
