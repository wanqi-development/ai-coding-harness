"""意图澄清协议：qa 自由措辞，但可执行选项必须是固定 JSON。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from ai_coding_harness.clarification import parse_clarification
from ai_coding_harness.config import Settings
from ai_coding_harness.db import dao
from ai_coding_harness.server.service import HarnessService


def _payload(**overrides: object) -> str:
    data = {
        "question": "你想让我做哪一类？",
        "options": [
            {"intent": "qa", "label": "查已有信息", "description": "回答既有事实"},
            {"intent": "plan", "label": "出方案", "description": "只出步骤"},
        ],
    }
    data.update(overrides)
    return f"前置说明\n<CLARIFY>\n{json.dumps(data, ensure_ascii=False)}\n</CLARIFY>\n后置说明"


class TestParse:
    def test_extracts_the_block_and_keeps_order(self):
        got = parse_clarification(_payload())

        assert got is not None
        assert got.question == "你想让我做哪一类？"
        assert [item.intent for item in got.options] == ["qa", "plan"]
        assert got.options[0].label == "查已有信息"

    def test_accepts_a_json_fence_inside_the_block(self):
        raw = json.dumps(
            {
                "question": "q",
                "options": [
                    {"intent": "qa", "label": "a"},
                    {"intent": "plan", "label": "b"},
                ],
            },
            ensure_ascii=False,
        )

        got = parse_clarification(f"<CLARIFY>```json\n{raw}\n```</CLARIFY>")

        assert got is not None
        assert len(got.options) == 2

    def test_rejects_unknown_intent(self):
        assert parse_clarification(
            _payload(
                options=[
                    {"intent": "qa", "label": "a"},
                    {"intent": "inspect", "label": "b"},
                ]
            )
        ) is None

    def test_rejects_wrong_option_count(self):
        assert parse_clarification(
            _payload(options=[{"intent": "qa", "label": "只有一项"}])
        ) is None
        assert parse_clarification(
            _payload(
                options=[
                    {"intent": "qa", "label": "1"},
                    {"intent": "plan", "label": "2"},
                    {"intent": "coding", "label": "3"},
                    {"intent": "review", "label": "4"},
                ]
            )
        ) is None

    def test_rejects_missing_block_or_bad_json(self):
        assert parse_clarification("普通回答") is None
        assert parse_clarification("<CLARIFY>{not json}</CLARIFY>") is None

    def test_duplicate_intent_is_rejected(self):
        assert parse_clarification(
            _payload(
                options=[
                    {"intent": "qa", "label": "1"},
                    {"intent": "qa", "label": "2"},
                ]
            )
        ) is None


class _SessionCtx:
    async def __aenter__(self) -> Any:
        return object()

    async def __aexit__(self, *args: object) -> None:
        return None


class _DB:
    def session(self) -> _SessionCtx:
        return _SessionCtx()


def _service() -> HarnessService:
    return HarnessService(
        settings=Settings(_env_file=None),
        manager=SimpleNamespace(),
        database=_DB(),  # type: ignore[arg-type]
    )


def _session() -> Any:
    return SimpleNamespace(db_id=1)


async def test_consume_accepts_an_index_and_deletes_the_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = SimpleNamespace(
        id="clr-1",
        original_message="帮我补一下退款",
        options=[
            {"intent": "qa", "label": "查已有信息"},
            {"intent": "coding", "label": "改代码"},
        ],
    )
    deleted: list[int] = []

    async def fake_get(db: Any, *, session_id: int) -> Any:
        assert session_id == 1
        return row

    async def fake_delete(db: Any, *, session_id: int) -> bool:
        deleted.append(session_id)
        return True

    monkeypatch.setattr(dao, "get_clarification", fake_get)
    monkeypatch.setattr(dao, "delete_clarification", fake_delete)

    got = await _service()._consume_clarification(
        _session(), clarification_id="clr-1", choice=1
    )

    assert got == ("帮我补一下退款", "coding", "改代码")
    assert deleted == [1]


async def test_consume_rejects_an_unknown_id_and_clears_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = SimpleNamespace(
        id="clr-1",
        original_message="原话",
        options=[{"intent": "qa", "label": "查"}],
    )
    deleted: list[int] = []

    async def fake_get(db: Any, *, session_id: int) -> Any:
        return row

    async def fake_delete(db: Any, *, session_id: int) -> bool:
        deleted.append(session_id)
        return True

    monkeypatch.setattr(dao, "get_clarification", fake_get)
    monkeypatch.setattr(dao, "delete_clarification", fake_delete)

    assert (
        await _service()._consume_clarification(
            _session(), clarification_id="wrong", choice=0
        )
        is None
    )
    assert deleted == [1]


async def test_pending_clarification_returns_the_frame_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = SimpleNamespace(
        id="clr-1",
        question="你想做什么？",
        options=[{"intent": "qa", "label": "查"}],
    )

    async def fake_get(db: Any, *, session_id: int) -> Any:
        return row

    monkeypatch.setattr(dao, "get_clarification", fake_get)

    assert await _service().pending_clarification(_session()) == {
        "id": "clr-1",
        "question": "你想做什么？",
        "options": [{"intent": "qa", "label": "查"}],
        "allow_other": True,
    }
