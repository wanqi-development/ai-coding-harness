"""一轮改了哪些代码：只在**建成 PR** 的轮次里出现，在**本地代码缓存**里算（不看沙箱）。

三条要钉住的：

1. ``base`` / ``head`` 取 ``create_pr`` 的**入参**（那才是这一轮 PR 自己的声明）；
2. 本地没缓存过这个仓库 → 整块不出现（一次 clone 可能很大，不该塞在一轮对话的收尾里）；
3. 算不出来（缺分支、git 出错）**不影响这一轮收尾** —— 只是没有那一块。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from ai_coding_harness.config import Settings
from ai_coding_harness.server import service as service_mod
from ai_coding_harness.server.service import HarnessService, _pr_refs
from ai_coding_harness.tools.git import GiteeRepo


def _service() -> HarnessService:
    return HarnessService(
        settings=Settings(_env_file=None),
        manager=SimpleNamespace(),
        database=SimpleNamespace(),
    )


def _session() -> Any:
    return SimpleNamespace(
        public_id="s-1",
        db_id=1,
        repo=GiteeRepo(owner="acme", repo="demo", token=""),
        repo_url="https://gitee.com/acme/demo.git",
    )


def _change(path: str, *, added: int = 1, deleted: int = 0, status: str = "M") -> Any:
    from ai_coding_harness.repo_cache import FileChange

    return FileChange(path=path, added=added, deleted=deleted, status=status)


# -- 一、base / head 从哪来 ----------------------------------------------------


class TestPullRequestRefs:
    def test_a_dict_input_is_the_normal_case(self):
        assert _pr_refs({"title": "t", "head": "feat/x", "base": "master"}) == {
            "base": "master",
            "head": "feat/x",
        }

    def test_a_json_string_input_is_also_accepted(self):
        """v3 的工具入参有时是 dict、有时是一段 JSON 字符串（与 subagent_name 同一个坑）。"""
        assert _pr_refs('{"head": "feat/x", "base": "master"}') == {
            "base": "master",
            "head": "feat/x",
        }

    @pytest.mark.parametrize("raw", [None, "", "不是 JSON", {"head": "feat/x"}, {"base": "m"}, 42])
    def test_missing_or_broken_input_is_none(self, raw):
        assert _pr_refs(raw) is None


# -- 二、采集 ------------------------------------------------------------------


class TestCollectingTheChanges:
    async def test_without_a_local_cache_there_is_no_block(self, monkeypatch):
        """从没打开过代码面板：整块不出现（也不顺手 clone 一个仓库）。"""
        monkeypatch.setattr(service_mod.repo_cache, "is_cloned", lambda *a: False)
        service = _service()

        assert await service._changes_for_round(_session(), {"base": "master", "head": "x"}) is None

    async def test_it_lists_what_the_pull_request_touched(self, monkeypatch):
        monkeypatch.setattr(service_mod.repo_cache, "is_cloned", lambda *a: True)

        async def fake_compare(settings, spec, *, base, head):
            assert (base, head) == ("master", "feat/x")
            return [_change("a.py", added=3), _change("b.md", status="A")]

        monkeypatch.setattr(service_mod.repo_cache, "compare", fake_compare)

        payload = await _service()._changes_for_round(
            _session(), {"base": "master", "head": "feat/x"}
        )

        assert payload is not None
        assert payload["base"] == "master" and payload["head"] == "feat/x"
        assert [f["path"] for f in payload["files"]] == ["a.py", "b.md"]
        assert payload["files"][1]["status"] == "A"
        assert "total" not in payload, "没超上限就不带这个字段"

    async def test_a_missing_branch_triggers_one_fetch_then_retries(self, monkeypatch):
        """coder 刚推上去的分支本地多半还没有：先 fetch 一次再算。"""
        from ai_coding_harness.repo_cache import RepoViewError

        monkeypatch.setattr(service_mod.repo_cache, "is_cloned", lambda *a: True)
        calls = {"compare": 0, "sync": 0}

        async def fake_compare(settings, spec, *, base, head):
            calls["compare"] += 1
            if calls["compare"] == 1:
                raise RepoViewError("缓存里没有分支 feat/x（先同步一次再试）")
            return [_change("a.py")]

        async def fake_sync(settings, spec):
            calls["sync"] += 1

        monkeypatch.setattr(service_mod.repo_cache, "compare", fake_compare)
        monkeypatch.setattr(service_mod.repo_cache, "sync", fake_sync)

        payload = await _service()._changes_for_round(
            _session(), {"base": "master", "head": "feat/x"}
        )

        assert calls == {"compare": 2, "sync": 1}
        assert payload is not None and payload["files"][0]["path"] == "a.py"

    async def test_a_failure_never_breaks_the_round(self, monkeypatch):
        monkeypatch.setattr(service_mod.repo_cache, "is_cloned", lambda *a: True)

        async def boom(settings, spec, *, base, head):
            raise RuntimeError("git 挂了")

        monkeypatch.setattr(service_mod.repo_cache, "compare", boom)

        assert await _service()._changes_for_round(
            _session(), {"base": "master", "head": "x"}
        ) is None

    async def test_the_file_list_is_capped(self, monkeypatch):
        """一轮改上百个文件更像事故：只列前 N 个，另带总数。"""
        monkeypatch.setattr(service_mod.repo_cache, "is_cloned", lambda *a: True)
        monkeypatch.setattr(service_mod, "_CHANGES_MAX_FILES", 2)

        async def fake_compare(settings, spec, *, base, head):
            return [_change(f"f{i}.py") for i in range(5)]

        monkeypatch.setattr(service_mod.repo_cache, "compare", fake_compare)

        payload = await _service()._changes_for_round(
            _session(), {"base": "master", "head": "x"}
        )

        assert payload is not None
        assert len(payload["files"]) == 2 and payload["total"] == 5


# -- 三、接到帧上 --------------------------------------------------------------


async def test_the_round_streams_the_changes_frame(monkeypatch):
    """建了 PR 的轮次：收尾推一帧 `changes`（在 usage 之前），并落进这一轮的记录。"""
    from tests.test_plan_events import _run
    from tests.v3_events import tool_finish, tool_start

    async def fake_changes(self, session, pr_refs):
        # 这一帧的入参就是这一轮 PR 的声明（base / head 都在这儿）
        assert pr_refs == {"base": "master", "head": "feat/x"}
        return {"base": "master", "head": "feat/x", "files": [{"path": "a.py", "added": 1, "deleted": 0, "status": "M"}]}

    monkeypatch.setattr(HarnessService, "_changes_for_round", fake_changes)

    out = await _run(
        [
            tool_start("create_pr", call_id="t1", input={"head": "feat/x", "base": "master"}),
            tool_finish(call_id="t1", output="PR 已创建"),
        ]
    )

    kinds = [e["type"] for e in out]
    assert "changes" in kinds, "建了 PR 就该有这一帧"
    # 跑动中会推好几帧 usage（工具收尾各一帧），所以这里钉的是「改动排在**收尾那一帧**之前」
    assert kinds[-2:] == ["changes", "usage"], f"改动那块紧挨在收尾的账之前：{kinds}"
    assert next(e for e in out if e["type"] == "changes")["changes"]["head"] == "feat/x"


async def test_a_round_without_a_pull_request_has_no_changes_frame(monkeypatch):
    """没建 PR 的轮次（问答、只读排查）：一帧都没有 —— 用户口径：展示的前提是写完并提了 PR。"""
    from tests.test_plan_events import _run, _tool_call

    called = {"n": 0}

    async def fake_changes(self, session, pr_refs):
        called["n"] += 1

    monkeypatch.setattr(HarnessService, "_changes_for_round", fake_changes)

    out = await _run([*_tool_call("execute")])

    assert [e for e in out if e["type"] == "changes"] == []
    assert called["n"] == 0, "没建 PR 就不该去算"
