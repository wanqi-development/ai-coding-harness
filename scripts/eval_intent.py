"""意图判定评测：收集真实样本、跑现有判定与影子路由、出准确率与混淆矩阵。

用法（在仓库根目录）：

    $env:PYTHONPATH='src'
    conda run -n aicoding --no-capture-output python scripts/eval_intent.py collect \
        --out docs/intent-eval.jsonl --limit 200

    # 人工把每行的 expected 填成期望意图（可留空，留空的样本不参与统计）
    conda run -n aicoding --no-capture-output python scripts/eval_intent.py run \
        docs/intent-eval.jsonl

``collect`` / ``enrich`` 只读业务库；``run`` 只读一次库里的模型配置（不写库）。
影子路由器的建议**不改变线上形态**，线上要记录它需显式打开
``INTENT_ROUTER_SHADOW=true``。
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

from ai_coding_harness.agent.llm import build_classifier_model, build_model
from ai_coding_harness.config import Settings
from ai_coding_harness.db import Database
from ai_coding_harness.db.models import SessionRecord, SessionRecordDetail
from ai_coding_harness.server.intent import (
    EXECUTION_INTENTS,
    SOURCE_ACTIVE,
    active_task_override,
    build_jev_decider,
    classify,
    detect_intent,
    head_tail_excerpt,
)
from ai_coding_harness.server.intent_router import (
    RouterDecision,
    route,
    suggest_intent,
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
                active = next(
                    (
                        str(extra["intent"])
                        for kind, extra, _content in reversed(conv)
                        if kind == "assistant"
                        and isinstance(extra, dict)
                        and extra.get("intent") in EXECUTION_INTENTS
                    ),
                    None,
                )
                if active:
                    row["active_intent"] = active
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


def _router_or_none(settings: Settings, classifier: Any) -> Any:
    """路由模型：优先分类行；没有就借一条已配置的 agent 行（原型阶段不挂工具）。"""
    if classifier is not None:
        return classifier
    if not settings.intent_llm_enabled:
        return None
    for role in ("qa", "analysis", "coding", "plan", "review"):
        model = _build_role_model(settings, role)
        if model is not None:
            return model
    return None


def _build_role_model(settings: Settings, role: str) -> Any:
    try:
        return build_model(settings, role=role)
    except Exception:  # noqa: BLE001 —— 某一行没配就试下一行
        return None


def _accuracy(pairs: list[tuple[str, str]]) -> tuple[int, int, float]:
    correct = sum(1 for expected, got in pairs if expected == got)
    total = len(pairs)
    return correct, total, (correct / total if total else 0.0)


async def run(
    path: Path,
    limit: int = 0,
    ids: set[int] | None = None,
    router_only: bool = False,
    normal_only: bool = False,
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
    router_model = _router_or_none(settings, model)
    print(
        "模型："
        f"分类={'有' if model else '无'}；路由={'有' if router_model else '无'}；"
        f"JEV={'有' if jev else '无'}"
    )

    keyword_pairs: list[tuple[str, str]] = []
    normal_pairs: list[tuple[str, str]] = []
    router_pairs: list[tuple[str, str]] = []
    router_missing = 0
    router_sync_skipped = 0
    details: list[dict[str, Any]] = []

    for row in labelled:
        message = str(row.get("message") or "").strip()
        expected = str(row["expected"]).strip()
        history = [str(item) for item in (row.get("history") or [])]
        previous_reply = row.get("previous_reply") or None
        previous_intent = row.get("previous_intent") or None
        active_intent = row.get("active_intent") or previous_intent
        dialogue = row.get("dialogue") or []

        keyword = classify(message)
        normal_intent = ""
        normal_source = ""
        normal_confidence = None
        if not router_only:
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
            active_intent=active_intent,
                dialogue=dialogue,
                mode=settings.intent_mode,
            )
            normal_intent = normal.intent
            normal_source = normal.source
            normal_confidence = normal.confidence
            override = active_task_override(message, normal_intent, active_intent)
            if override:
                normal_intent = override
                normal_source = SOURCE_ACTIVE
        router_decision: RouterDecision | None = None
        suggested = ""
        if router_model is not None and not normal_only:
            router_decision = await route(
                router_model,
                message,
                history=history,
                previous_reply=previous_reply,
                previous_intent=previous_intent,
                active_intent=active_intent,
                dialogue=dialogue,
                timeout=settings.intent_router_timeout_seconds,
            )
            if router_decision is not None:
                suggested = suggest_intent(
                    router_decision,
                    previous_intent,
                    message,
                    active_intent=active_intent,
                )

        keyword_pairs.append((expected, keyword))
        if not router_only:
            normal_pairs.append((expected, normal_intent))
        if expected == "sync":
            # sync 是关键字独占的直接执行路径，路由器本来就不该判它
            router_sync_skipped += 1
        elif suggested:
            router_pairs.append((expected, suggested))
        else:
            router_missing += 1
        details.append(
            {
                "expected": expected,
                "message": message,
                "keyword": keyword,
                "normal": normal_intent,
                "normal_source": normal_source,
                "normal_confidence": normal_confidence,
                "router_axes": router_decision.as_dict() if router_decision else None,
                "router_suggested": suggested or None,
                "previous_intent": previous_intent,
                "active_intent": active_intent,
                "record_id": row.get("record_id"),
            }
        )

    methods = [("keyword", keyword_pairs)]
    if not router_only:
        methods.append(("normal", normal_pairs))
    if not normal_only:
        methods.append(("router", router_pairs))
    for name, pairs in methods:
        correct, total, accuracy = _accuracy(pairs)
        print(f"\n{name}: {correct}/{total} = {accuracy:.1%}")
        _print_matrix(name, pairs)
    if not router_only:
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
    if router_missing:
        print(f"\nrouter 未给出结论：{router_missing} 条（模型不可用或 JSON 解析失败）")
    if router_sync_skipped:
        print(f"router 跳过 sync：{router_sync_skipped} 条（sync 由关键字独占）")

    print("\n错例（最多 20 条，便于补评测集 / 调策略表）")
    shown = 0
    for item in details:
        normal_ok = router_only or item["normal"] == item["expected"]
        router_ok = (
            normal_only
            or not item["router_suggested"]
            or item["router_suggested"] == item["expected"]
        )
        if normal_ok and router_ok:
            continue
        if shown >= 20:
            break
        shown += 1
        print(
            f"- expected={item['expected']} normal={item['normal'] or '-'}"
            f" ({item['normal_source']}) router={item['router_suggested']}"
            f" axes={item['router_axes']}\n  {item['message']}"
        )
    if shown == 0:
        print("- 没有错例（在当前标注集上）")

    if out is not None:
        out.write_text(
            json.dumps(details, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\n已保存 {len(details)} 条逐样本结果 → {out}")


def policy(path: Path, *, show: int = 20) -> None:
    """离线重算策略表：读 `run --out` 的逐样本结果，不调用任何模型。

    路由轴是模型产出的，会抖动；策略表是确定性的。把轴固定下来之后再调策略，
    才能分清「模型判错」还是「策略判错」。
    """
    rows = _read_rows(path)
    labelled = [row for row in rows if str(row.get("expected") or "").strip()]
    if not labelled:
        print("没有 expected，先跑 run --out 生成逐样本结果。")
        return

    pairs: list[tuple[str, str]] = []
    errors: list[dict[str, Any]] = []
    for row in labelled:
        axes = row.get("router_axes")
        if not isinstance(axes, dict):
            continue
        decision = RouterDecision(
            str(axes.get("speech_act") or ""),
            str(axes.get("wants") or ""),
            str(axes.get("target") or ""),
            str(axes.get("continuation") or ""),
        )
        expected = str(row["expected"]).strip()
        if expected == "sync":
            # sync 是关键字独占路径，路由器本来就不该判它（与 run 的统计口径一致）。
            continue
        suggested = suggest_intent(
            decision,
            row.get("previous_intent") or None,
            str(row.get("message") or ""),
            active_intent=row.get("active_intent") or None,
        )
        pairs.append((expected, suggested))
        if suggested != expected:
            errors.append(
                {
                    "expected": expected,
                    "suggested": suggested,
                    "message": row.get("message"),
                    "axes": axes,
                    "previous_intent": row.get("previous_intent"),
                    "active_intent": row.get("active_intent"),
                }
            )

    correct, total, accuracy = _accuracy(pairs)
    print(f"policy: {correct}/{total} = {accuracy:.1%}（轴固定，不调用模型）")
    if errors:
        print("\n错例：")
        for item in errors[:show]:
            print(
                f"- expected={item['expected']} suggested={item['suggested']}"
                f" axes={item['axes']} previous={item['previous_intent']}"
                f" active={item['active_intent']}\n  {item['message']}"
            )
    else:
        print("- 没有错例")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="意图判定评测 / 影子路由对比")
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
        "--router-only",
        action="store_true",
        help="只跑影子路由（跳过现有判定，节省模型调用）",
    )
    p_run.add_argument(
        "--normal-only",
        action="store_true",
        help="只跑现有判定（JEV/LLM），跳过影子路由",
    )
    p_run.add_argument(
        "--out",
        type=Path,
        default=None,
        help="保存逐样本结果（JSON），供 policy 离线重算",
    )
    p_policy = sub.add_parser(
        "policy", help="用固定路由轴离线重算策略表（不调用模型）"
    )
    p_policy.add_argument("file", type=Path, help="run --out 生成的逐样本结果")
    p_policy.add_argument("--show", type=int, default=20, help="最多打印多少条错例")
    p_enrich = sub.add_parser("enrich", help="给已有标注文件补上一轮意图与回复末尾")
    p_enrich.add_argument("file", type=Path, help="已标注 JSON / JSONL（原地更新）")
    p_format = sub.add_parser("format", help="把 JSONL / JSON 统一格式化成缩进 JSON 数组")
    p_format.add_argument("file", type=Path, help="待格式化文件（原地更新）")
    args = parser.parse_args(argv)

    if args.command == "collect":
        _run(collect(args.out, args.limit))
    elif args.command == "enrich":
        _run(enrich(args.file))
    elif args.command == "policy":
        policy(args.file, show=args.show)
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
                args.router_only,
                args.normal_only,
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
