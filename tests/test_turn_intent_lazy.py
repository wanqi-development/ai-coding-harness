"""Turn intent must initialize only the configured decider."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from ai_coding_harness.server.intent import IntentDecision
from ai_coding_harness.server.turn import intent as intent_module
from ai_coding_harness.server.turn.intent import TurnIntentMixin


class Probe(TurnIntentMixin):
    def __init__(self) -> None:
        self._settings = SimpleNamespace(intent_timeout_seconds=1.0)
        self._intent_usage: dict[str, int] = {}
        self.jev_calls = 0
        self.classifier_calls = 0

    def _jev_for(self, user_id: int | None):
        self.jev_calls += 1
        return object()

    def _classifier_for(self, user_id: int | None):
        self.classifier_calls += 1
        return object()


@pytest.mark.parametrize(
    ("mode", "expected_jev", "expected_classifier"),
    [("llm", 0, 1), ("jev", 1, 0)],
)
async def test_only_the_selected_decider_is_initialized(
    monkeypatch, mode: str, expected_jev: int, expected_classifier: int
) -> None:
    monkeypatch.setattr(intent_module, "intent_mode_for", lambda settings, user_id: mode)

    async def fake_detect(*args: Any, **kwargs: Any) -> IntentDecision:
        return IntentDecision("qa", "fallback")

    monkeypatch.setattr(intent_module, "detect_intent", fake_detect)
    service = Probe()

    await service._detect_intent("你好", history=[], user_id=1)

    assert service.jev_calls == expected_jev
    assert service.classifier_calls == expected_classifier


async def test_handoff_rejudge_never_initializes_jev(monkeypatch) -> None:
    monkeypatch.setattr(intent_module, "intent_mode_for", lambda settings, user_id: "jev")

    async def fake_detect(*args: Any, **kwargs: Any) -> IntentDecision:
        assert kwargs["jev"] is None
        assert kwargs["force_llm"] is True
        return IntentDecision("qa", "fallback")

    monkeypatch.setattr(intent_module, "detect_intent", fake_detect)
    service = Probe()

    await service._rejudge_intent("换一种做法", history=[], session=None)

    assert service.jev_calls == 0
    assert service.classifier_calls == 1
