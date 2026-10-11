"""按会话看日志：格式里的会话格、按会话筛、脱敏、下载。

三层各钉一段：

1. **会话格真的写进日志了** —— 走真实 ``setup_logging``，看落盘的每一行是否带 ``[<会话id>]``。
   这是整个视图的地基：没有这一格，按会话筛只剩约 3% 的行（见 CHANGELOG 的实测数字）。
2. **筛选口径** —— 四种命中写法、级别阈值、尾部 limit 与「命中总数」两个数分开。
3. **脱敏** —— 读出来是第五条出口，令牌不能在响应里。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from ai_coding_harness.api.routes import sessions as sessions_route
from ai_coding_harness.codeplatform import Platform, RepoSpec
from ai_coding_harness.config import Settings
from ai_coding_harness.observability.context import reset_session_id, set_session_id
from ai_coding_harness.observability.log_tail import read_session_logs
from ai_coding_harness.observability.logging import setup_logging

SESSION = "78780d7a-91f4-498c-bdcf-5f55a8190f11"
OTHER = "11111111-2222-3333-4444-555555555555"
TOKEN = "glpat-abcdefghijklmnopqrst"


def _line(
    moment: str,
    level: str,
    message: str,
    *,
    session: str = "-",
    logger: str = "ai_coding_harness.server.service",
) -> str:
    """按当前格式串造一行（三格上下文：trace / session / agent）。"""
    return (
        f"{moment} [{level}] [abc123abc123] [{session}] [main] "
        f"{logger}: {message}"
    )


async def test_resolve_session_marks_the_log_context() -> None:
    """会话 id 是在 ``resolve_session`` 里设的（每个会话级请求的必经点）。

    设置点放这儿而不是 ``service.chat``：连 ``/workspace`` 触发的 provisioning 也算这个
    会话的日志；``RunHub`` 起的那一轮跑在 ``create_task`` 里，会把这个上下文复制过去。
    """
    from ai_coding_harness.api.session_lookup import resolve_session
    from ai_coding_harness.observability.context import get_session_id

    app = SimpleNamespace(state=SimpleNamespace(sessions={SESSION: _session()}))

    assert get_session_id() == "-", "没有上下文时是占位符，不是空串"
    await resolve_session(SimpleNamespace(app=app), SESSION)
    assert get_session_id() == SESSION


@pytest.fixture
def logs(tmp_path: Path) -> Any:
    """真实 ``setup_logging``（落到 tmp_path），结束后复原根 logger。"""
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    settings = Settings(_env_file=None, log_dir=str(tmp_path))
    try:
        setup_logging(settings)
        yield settings, tmp_path
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)


def test_every_line_carries_the_session_slot(logs) -> None:
    """地基：设置了会话 id 之后，落盘的每一行都带 ``[<会话id>]``。

    没有它，按会话筛日志只能捞到 ``public_id=`` 那 3% 的行（工具调用 / LLM 调用都不带）。
    """
    _settings, tmp_path = logs

    token = set_session_id(SESSION)
    try:
        logging.getLogger("ai_coding_harness.server.service").info("带会话 id 的一行")
    finally:
        reset_session_id(token)
    # 没有上下文时是 `-`（不是缺格）
    logging.getLogger("ai_coding_harness.server.service").info("没有会话上下文的一行")

    text = (tmp_path / "agent.log").read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if "的一行" in ln]
    assert len(lines) == 2
    # 三格上下文的顺序：[trace_id] [session_id] [agent]
    assert re.match(
        rf"^\d{{4}}-\d\d-\d\d \d\d:\d\d:\d\d \[INFO\] \[[^\]]+\] \[{SESSION}\] \[-\] "
        r"ai_coding_harness\.server\.service:",
        lines[0],
    ), lines[0]
    assert "[-]" in lines[1], lines[1]


def test_read_session_logs_matches_every_writeup_of_the_id(tmp_path: Path) -> None:
    """会话 id 出现的几种写法都要认：会话格 / 请求路径 / 老的 ``public_id=``。"""
    path = tmp_path / "agent.log"
    path.write_text(
        "\n".join(
            [
                _line("2026-10-11 09:00:01", "INFO", "会话格命中", session=SESSION),
                _line("2026-10-11 09:00:02", "INFO", "别人的会话", session=OTHER),
                _line("2026-10-11 09:00:03", "INFO", f"老格式 public_id={SESSION} intent=coding"),
                _line("2026-10-11 09:00:04", "INFO", "无关的一行"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    api = tmp_path / "api.log"
    api.write_text(
        _line("2026-10-11 09:00:05", "INFO", f"→ POST /chat/{SESSION} (client=127.0.0.1)") + "\n",
        encoding="utf-8",
    )

    got = read_session_logs([api, path], SESSION)

    assert got.total == 3, [item.text for item in got.lines]
    assert [item.ts for item in got.lines] == [
        "2026-10-11 09:00:01",
        "2026-10-11 09:00:03",
        "2026-10-11 09:00:05",
    ], "两个文件要按时间合并，不是先 api 后 agent"


def test_level_is_a_threshold_and_limit_keeps_the_tail(tmp_path: Path) -> None:
    """``level`` 是阈值（WARNING 含以上）；``limit`` 留尾部，``total`` 仍是命中总数。"""
    path = tmp_path / "agent.log"
    path.write_text(
        "\n".join(
            [
                _line("2026-10-11 09:00:01", "INFO", "一", session=SESSION),
                _line("2026-10-11 09:00:02", "WARNING", "二", session=SESSION),
                _line("2026-10-11 09:00:03", "ERROR", "三", session=SESSION),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert [item.level for item in read_session_logs([path], SESSION, level="WARNING").lines] == [
        "WARNING",
        "ERROR",
    ]
    tail = read_session_logs([path], SESSION, limit=2)
    assert tail.total == 3, "总数是命中数，不是尾部长度"
    assert [item.text.rsplit(": ", 1)[-1] for item in tail.lines] == ["二", "三"]


def test_a_continuation_line_keeps_its_parents_timestamp(tmp_path: Path) -> None:
    """续行没有自己的时间戳：沿用它上面那条，别被排到最前、更别被 tail 丢掉。"""
    path = tmp_path / "agent.log"
    path.write_text(
        _line("2026-10-11 09:00:09", "ERROR", "第一行", session=SESSION)
        + "\n"
        + "Traceback (most recent call last):\n"
        + f"  File \"/x.py\", line 1, in <module>  # {SESSION}\n",
        encoding="utf-8",
    )

    got = read_session_logs([path], SESSION, limit=1)

    assert got.total == 2
    assert got.lines[0].ts == "2026-10-11 09:00:09", "续行跟着父行，所以最后一行是它"


def test_a_missing_file_is_an_empty_slice(tmp_path: Path) -> None:
    assert read_session_logs([tmp_path / "nope.log"], SESSION).total == 0


def test_http_request_noise_is_hidden_by_default(tmp_path: Path) -> None:
    """HTTP 请求行默认不显示：一个请求三行、前端每 1.5 秒轮询一次，实测占某会话 76%。

    真正要看的运行日志（工具调用、LLM、沙箱）不能被它淹掉；要就拿 ``with_http`` 打开。
    """
    path = tmp_path / "agent.log"
    path.write_text(
        "\n".join(
            [
                _line(
                    "2026-10-11 09:00:01",
                    "INFO",
                    f"→ GET /sessions/{SESSION} (client=127.0.0.1)",
                    session=SESSION,
                    logger="ai_coding_harness.api.middleware",
                ),
                _line(
                    "2026-10-11 09:00:02",
                    "INFO",
                    f'"GET /sessions/{SESSION} HTTP/1.1" 200',
                    session=SESSION,
                    logger="uvicorn.access",
                ),
                _line("2026-10-11 09:00:03", "INFO", "工具调用 → read_file", session=SESSION),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    default = read_session_logs([path], SESSION)
    assert default.total == 1, "默认只剩真正有用的那行"
    assert default.lines[0].logger == "ai_coding_harness.server.service"

    noisy = read_session_logs([path], SESSION, with_http=True)
    assert noisy.total == 3, "开关打开时把请求行也带上"


def test_the_log_endpoints_own_requests_are_never_shown(tmp_path: Path) -> None:
    """日志接口自己的请求**永远**不显示（没有开关）—— 否则「看日志」这个动作在污染日志。

    实测教训：验证时调了 ~55 次 ``/logs``，给那个会话留下 166 行，正好把要看的东西淹了。
    """
    path = tmp_path / "agent.log"
    path.write_text(
        "\n".join(
            [
                _line(
                    "2026-10-11 09:00:01",
                    "INFO",
                    f"→ GET /sessions/{SESSION}/logs (client=127.0.0.1)",
                    session=SESSION,
                    logger="ai_coding_harness.api.middleware",
                ),
                _line(
                    "2026-10-11 09:00:02",
                    "INFO",
                    f'"GET /sessions/{SESSION}/logs/download HTTP/1.1" 200',
                    session=SESSION,
                    logger="uvicorn.access",
                ),
                _line("2026-10-11 09:00:03", "INFO", "工具调用 → read_file", session=SESSION),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert read_session_logs([path], SESSION).total == 1
    assert read_session_logs([path], SESSION, with_http=True).total == 1, (
        "就算打开「含 HTTP 请求行」，日志接口自己也还是不显示"
    )


def test_the_keyword_filter_runs_before_the_tail(tmp_path: Path) -> None:
    """关键字**先筛再截尾**：命中数是真的，不是「尾部那 N 行里碰巧没有」。"""
    path = tmp_path / "agent.log"
    path.write_text(
        "\n".join(
            [
                _line("2026-10-11 09:00:01", "INFO", "含 Tool 的关键行", session=SESSION),
                _line("2026-10-11 09:00:02", "INFO", "无关的一行", session=SESSION),
                _line("2026-10-11 09:00:03", "INFO", "另一条无关的", session=SESSION),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    got = read_session_logs([path], SESSION, query="tool", limit=1)

    assert got.total == 1, "大小写不敏感地命中最上面那条，而不是在尾部 1 行里找"
    assert "Tool" in got.lines[0].text
    assert read_session_logs([path], SESSION, query="  ").total == 3, "空关键字＝不筛"


# -- 接口 ----------------------------------------------------------------


def _session() -> Any:
    return SimpleNamespace(
        public_id=SESSION,
        db_id=1,
        repo=RepoSpec(owner="acme", repo="demo", token=TOKEN, platform=Platform.GITEE),
        repo_url="https://gitee.com/acme/demo.git",
    )


def _app(log_dir: Path) -> FastAPI:
    app = FastAPI()
    app.include_router(sessions_route.router)
    app.state.settings = Settings(_env_file=None, log_dir=str(log_dir))
    app.state.sessions = {SESSION: _session()}
    return app


async def _get(app: FastAPI, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


async def test_the_endpoint_returns_the_tail_and_masks_tokens(tmp_path: Path) -> None:
    (tmp_path / "agent.log").write_text(
        "\n".join(
            [
                _line("2026-10-11 09:00:01", "INFO", "第一条", session=SESSION),
                _line("2026-10-11 09:00:02", "INFO", f"令牌是 {TOKEN} 别外泄", session=SESSION),
                _line("2026-10-11 09:00:03", "INFO", "别人的", session=OTHER),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    resp = await _get(_app(tmp_path), f"/sessions/{SESSION}/logs")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert [line["level"] for line in body["lines"]] == ["INFO", "INFO"]
    assert TOKEN not in resp.text, "读日志是第五条出口，令牌不能回显"
    assert "已脱敏" in resp.text


async def test_the_endpoint_passes_the_search_and_the_http_switch(tmp_path: Path) -> None:
    """接口这一层要把 ``q`` / ``with_http`` 传下去，并把 logger 名回给前端。"""
    (tmp_path / "agent.log").write_text(
        "\n".join(
            [
                _line("2026-10-11 09:00:01", "INFO", "工具调用 → read_file", session=SESSION),
                _line("2026-10-11 09:00:02", "INFO", "无关的一行", session=SESSION),
                _line(
                    "2026-10-11 09:00:03",
                    "INFO",
                    f'"GET /sessions/{SESSION} HTTP/1.1" 200',
                    session=SESSION,
                    logger="uvicorn.access",
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    app = _app(tmp_path)

    default = await _get(app, f"/sessions/{SESSION}/logs")
    assert default.json()["total"] == 2, "默认不带 HTTP 请求行"
    assert default.json()["lines"][0]["logger"] == "ai_coding_harness.server.service"

    searched = await _get(app, f"/sessions/{SESSION}/logs?q=read_file")
    assert searched.json()["total"] == 1
    assert "read_file" in searched.json()["lines"][0]["text"]

    noisy = await _get(app, f"/sessions/{SESSION}/logs?with_http=true")
    assert noisy.json()["total"] == 3, "开关打开时请求行也回来"


async def test_the_download_is_an_attachment_with_the_whole_slice(tmp_path: Path) -> None:
    (tmp_path / "agent.log").write_text(
        "\n".join(
            _line(f"2026-10-11 09:00:0{i}", "INFO", f"第 {i} 条", session=SESSION)
            for i in range(1, 4)
        )
        + "\n",
        encoding="utf-8",
    )

    resp = await _get(_app(tmp_path), f"/sessions/{SESSION}/logs/download")

    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    assert "acme-demo-logs.txt" in resp.headers["content-disposition"]
    assert resp.text.count("条") == 3, "下载给全，不受面板的 limit 影响"


async def test_an_unknown_session_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不在内存注册表里就去库里找；库里也没有 ＝ 404（与其它会话接口同一口径）。"""
    from contextlib import asynccontextmanager

    from ai_coding_harness.db import dao

    async def no_such_session(db: Any, *, public_id: Any) -> Any:
        return None

    class _Db:
        @asynccontextmanager
        async def session(self, *_a: Any, **_kw: Any):
            yield None

    monkeypatch.setattr(dao, "get_session_with_repo", no_such_session)
    app = _app(tmp_path)
    app.state.database = _Db()

    resp = await _get(app, "/sessions/6f1a7c58-0000-4000-8000-000000000009/logs")

    assert resp.status_code == 404
