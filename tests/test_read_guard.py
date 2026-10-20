"""读文件红线：policies ≤80 行、project ≤200 行；整文件命令直接拦。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from ai_coding_harness.agent.middleware import ReadGuardMiddleware


def _request(name: str, args: dict[str, Any]) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": args, "id": "call-1"},
        tool=None,
        state=None,
        runtime=None,
    )


class _Handler:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, request: ToolCallRequest) -> ToolMessage:
        self.calls += 1
        return ToolMessage(
            content="真跑了",
            name=str(request.tool_call.get("name") or ""),
            tool_call_id="call-1",
        )


async def _run(name: str, args: dict[str, Any]) -> tuple[Any, _Handler]:
    handler = _Handler()
    out = await ReadGuardMiddleware().awrap_tool_call(_request(name, args), handler)
    return out, handler


class TestReadFile:
    async def test_policies_require_an_explicit_limit(self):
        out, handler = await _run(
            "read_file", {"file_path": "/ai_coding/policies/java-style.md"}
        )

        assert handler.calls == 0
        assert isinstance(out, ToolMessage) and out.status == "error"
        assert "limit" in out.content and "80" in out.content

    async def test_policies_allow_a_slice(self):
        _out, handler = await _run(
            "read_file",
            {"file_path": "/ai_coding/policies/java-style.md", "offset": 10, "limit": 60},
        )
        assert handler.calls == 1

    async def test_policies_reject_a_limit_over_the_cap(self):
        out, handler = await _run(
            "read_file",
            {"file_path": "/ai_coding/policies/java-style.md", "limit": 120},
        )
        assert handler.calls == 0
        assert isinstance(out, ToolMessage) and out.status == "error"

    async def test_project_requires_a_limit_and_caps_it(self):
        out, handler = await _run(
            "read_file", {"file_path": "/ai_coding/project/x/src/A.java"}
        )
        assert handler.calls == 0 and isinstance(out, ToolMessage)

        _out, handler = await _run(
            "read_file",
            {"file_path": "/ai_coding/project/x/src/A.java", "limit": 150},
        )
        assert handler.calls == 1

        out, handler = await _run(
            "read_file",
            {"file_path": "/ai_coding/project/x/src/A.java", "limit": 300},
        )
        assert handler.calls == 0
        assert isinstance(out, ToolMessage) and out.status == "error"

    async def test_tmp_and_memory_are_not_restricted(self):
        for path in ("/ai_coding/tmp/evidence.md", "/memories/o/r.md"):
            _out, handler = await _run("read_file", {"file_path": path})
            assert handler.calls == 1


class TestExecute:
    async def test_cat_on_policies_is_blocked(self):
        out, handler = await _run(
            "execute", {"command": "cat /ai_coding/policies/java-style.md"}
        )

        assert handler.calls == 0
        assert isinstance(out, ToolMessage) and out.status == "error"
        assert "sed -n" in out.content

    async def test_cat_after_cd_into_the_project_is_blocked(self):
        out, handler = await _run(
            "execute",
            {"command": "cd /ai_coding/project/wanqi-mall && cat src/A.java"},
        )
        assert handler.calls == 0
        assert isinstance(out, ToolMessage) and out.status == "error"

    async def test_nl_is_also_a_whole_file_dump(self):
        out, handler = await _run(
            "execute",
            {"command": "cd /ai_coding/project/x && nl -ba src/A.java"},
        )
        assert handler.calls == 0 and isinstance(out, ToolMessage)

    async def test_bounded_head_and_sed_are_allowed(self):
        for command in (
            "head -n 50 /ai_coding/policies/java-style.md",
            "sed -n '1,60p' /ai_coding/policies/java-style.md",
            "grep -n 'class' /ai_coding/project/x/src/A.java",
        ):
            _out, handler = await _run("execute", {"command": command})
            assert handler.calls == 1, command

    async def test_unbounded_head_and_sed_to_end_are_blocked(self):
        for command in (
            "head -n 500 /ai_coding/policies/java-style.md",
            "sed -n '1,$p' /ai_coding/policies/java-style.md",
        ):
            out, handler = await _run("execute", {"command": command})
            assert handler.calls == 0, command
            assert isinstance(out, ToolMessage) and out.status == "error"

    async def test_tmp_reads_are_not_restricted(self):
        _out, handler = await _run("execute", {"command": "cat /ai_coding/tmp/x.log"})
        assert handler.calls == 1

    async def test_other_tools_are_untouched(self):
        _out, handler = await _run("execute", {"command": "git status"})
        assert handler.calls == 1
