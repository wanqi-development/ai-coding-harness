"""日志管道落盘检查：格式串、主角字段、文件路由一起验（走真实 ``setup_logging``）。

单元层面的事在 ``test_run_logging.py``；这里管的是「配好之后落盘长什么样」——
谁进了 agent.log、谁进了 api.log、``%(agent)s`` 有没有渲染出来。

日志目录必须重定向到 tmp_path：``logs/`` 是运行中的后端在写的滚动文件，
测试进程插进去写会把两边都搞乱。
"""

from __future__ import annotations

import logging
import logging.handlers
import pathlib
from collections.abc import Iterator

import pytest

from ai_coding_harness.config import Settings
from ai_coding_harness.observability.logging import setup_logging
from ai_coding_harness.observability.run_log import AgentRunLog, brief
from tests.v3_events import SUB_NS, RunLogFeed, message_finish, tool_finish, tool_start

# 远超默认的 log_tool_arg_max(2000)，用来验截断真的按配置走
_HUGE = "x" * 3000


@pytest.fixture
def logs(tmp_path: pathlib.Path) -> Iterator[pathlib.Path]:
    """配好日志后把根 logger 复原，免得污染同一次 pytest 会话里的其他测试。"""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    try:
        setup_logging(Settings(log_dir=str(tmp_path)))
        yield tmp_path
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)


def _run_one(logs: pathlib.Path) -> list[str]:
    """走一遍主智能体 → 委派 planner → planner 自己调工具 → 回收，返回 agent.log 各行。

    返回值行（``工具返回 ← …``）单独在 ``test_tool_result_lands_in_agent_log`` 里验，
    这里滤掉，免得下面的行号断言被它整体顶位。

    planner 的工具事件不带 metadata，所以先得有一条它的 ``messages`` 事件（模型调用）
    把 namespace 登记下来 —— 少那一行，write_file 就会被算到主智能体头上。
    """
    feed = RunLogFeed(AgentRunLog(arg_limit=2000, result_limit=300))
    feed.start(tool_start("read_file", call_id="r1", input={"file_path": "/p/a.py"}))
    feed.start(
        tool_start(
            "task",
            call_id="r2",
            input={"description": "去查一下仓库结构", "subagent_type": "planner"},
        )
    )
    feed.llm(message_finish(ns=SUB_NS, agent="planner"))
    feed.start(tool_start("write_file", ns=SUB_NS, call_id="r3", input={"content": _HUGE}))
    feed.end(tool_finish(ns=SUB_NS, call_id="r3", output="ok"))
    feed.end(tool_finish(call_id="r2", output="方案已定"))
    logging.getLogger("ai_coding_harness.server.service").info("普通运行日志")
    logging.getLogger("ai_coding_harness.api.routes").info("接口日志")
    text = (logs / "agent.log").read_text(encoding="utf-8")
    return [line for line in text.strip().splitlines() if "工具返回 ←" not in line]


class TestAgentLog:
    def test_actor_tags_in_order(self, logs):
        lines = _run_one(logs)
        assert len(lines) == 7, lines  # 6 条运行日志 + 1 条服务日志（接口日志不在此列）
        actors = [
            "main",  # 主智能体自己的工具调用
            "main",  # 委派（也记在主智能体名下）
            "planner",  # planner 的模型调用，顺带登记它的 namespace
            "planner",  # planner 的工具调用
            "planner",  # planner 的工具完成
            "main",  # 子智能体完成
            "-",  # 没标注主角的服务日志
        ]
        for line, actor in zip(lines, actors):
            assert f"[{actor}]" in line, line

    def test_delegation_lines_carry_subagent_name(self, logs):
        lines = _run_one(logs)
        assert "planner" in lines[1] and "委派子智能体" in lines[1]
        assert "planner" in lines[5] and "子智能体完成" in lines[5]

    def test_tool_args_truncated_at_configured_limit(self, logs):
        lines = _run_one(logs)
        assert lines[3].endswith("参数=" + brief({"content": _HUGE}, 2000))

    def test_service_log_routed_here(self, logs):
        lines = _run_one(logs)
        assert "server.service" in lines[6]

    def test_tool_result_lands_in_agent_log(self, logs):
        _run_one(logs)
        text = (logs / "agent.log").read_text(encoding="utf-8")
        line = next(ln for ln in text.splitlines() if "工具返回 ← write_file" in ln)
        assert "[planner]" in line and line.endswith("ok"), line


class TestApiLog:
    def test_runtime_logs_stay_out(self, logs):
        _run_one(logs)
        api_text = (logs / "api.log").read_text(encoding="utf-8")
        assert "接口日志" in api_text
        assert "planner" not in api_text and "read_file" not in api_text


class TestByPassStateLog:
    """Redis 旁路状态的告警必须**落盘**。

    回归：``ai_coding_harness.infra.redis_cache`` 曾经既不在 api 前缀、也不在 agent 前缀里，
    于是「Redis 上的运行缓冲不可用」那句只进控制台 —— 用户反复看到它，日志里却一个字
    都没有，无从判断是连不上还是运行期降级。
    """

    def test_redis_degradation_lands_in_agent_log(self, logs):
        logging.getLogger("ai_coding_harness.infra.redis_cache").warning(
            "Redis 上的 运行缓冲 不可用，本进程后续退回进程内实现"
        )
        text = (logs / "agent.log").read_text(encoding="utf-8")
        assert "运行缓冲" in text, "旁路状态的降级告警要能在日志里查到"

    def test_redis_logs_stay_out_of_api_log(self, logs):
        logging.getLogger("ai_coding_harness.infra.redis_cache").warning("Redis 不可用")
        assert "Redis 不可用" not in (logs / "api.log").read_text(encoding="utf-8")


def test_a_locked_api_log_falls_back_to_a_process_specific_file(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_handler = logging.handlers.RotatingFileHandler
    fallback_lines: list[str] = []

    class HandlesFallback(real_handler):
        def __init__(self, filename, *args, **kwargs):
            path = pathlib.Path(filename)
            fallback_lines.append(str(path))
            if path.name == "api.log":
                raise PermissionError(13, "locked", str(path))
            super().__init__(filename, *args, **kwargs)

    monkeypatch.setattr(logging.handlers, "RotatingFileHandler", HandlesFallback)
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    try:
        path = setup_logging(Settings(_env_file=None, log_dir=str(tmp_path)))
        logging.getLogger("ai_coding_harness.api.routes").info("fallback works")
        for handler in list(root.handlers):
            handler.flush()
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)

    assert path.name == f"api.{__import__('os').getpid()}.log"
    assert "fallback works" in path.read_text(encoding="utf-8")
