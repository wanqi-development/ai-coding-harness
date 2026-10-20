"""供应商的模型详情：类型判定、按模型查窗口、刷新落表、手改。

2026-10-14 用户口径：窗口是**模型**的属性（同一家 glm-5.3 是 1M、glm-4.6 是 128k），
所以模型详情挂在供应商下（表 ``provider_models``），窗口按模型查；清单**点了刷新才拉**。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from ai_coding_harness.agent import llm as llm_mod
from ai_coding_harness.api.routes import model_config as route
from ai_coding_harness.config import (
    ModelConfig,
    ProviderModelMeta,
    Settings,
    model_metas_of,
)
from ai_coding_harness.core.model_types import MODEL_TYPES, is_text_model, model_type_of

GLM = "https://open.bigmodel.cn/api/paas/v4/"


def _settings(**over: Any) -> Settings:
    """不读 .env：断言不该被本机配置影响。"""
    return Settings(_env_file=None, **over)


class TestModelTypes:
    @pytest.mark.parametrize(
        ("name", "want"),
        [
            ("deepseek-v4-pro", "chat"),
            ("doubao-seed-2-1-pro-260915", "chat"),
            ("qwen-vl-max", "chat"),
            ("grok-4.20-reasoning", "chat"),
            ("gpt-image-2.5-sunburst", "image"),
            ("gemini-nano-banana-2.1", "image"),
            ("doubao-seedream-5-0-pro", "image"),
            ("doubao-seedance-2-5-260628", "video"),
            ("wanx2.1-t2i-plus", "video"),
            ("sora-2", "video"),
            ("tts-1-hd", "audio"),
            ("gpt-4o-realtime-preview", "audio"),
            ("whisper-1", "audio"),
            ("text-embedding-3-small", "embedding"),
            ("bge-m3", "embedding"),
            ("bge-reranker-v2-m3", "rerank"),
            ("olm-ocr", "other"),
        ],
    )
    def test_the_name_decides_the_type(self, name: str, want: str):
        assert model_type_of(name) == want

    def test_every_type_is_declared(self):
        assert set(MODEL_TYPES) >= {"chat", "embedding", "rerank", "image", "video", "audio", "other"}

    def test_only_chat_is_text(self):
        assert is_text_model("glm-5.3") is True
        assert is_text_model("glm-5.3-embedding") is False


class TestWindowLookup:
    """窗口的解析顺序：行内 → 按模型（先精确再同名）→ 保守兜底值。

    「家那格」（``.env`` 的 ``<供应商>_CONTEXT_WINDOW``）2026-10-15 起不再是解析的一环。
    """

    def _settings(self) -> Settings:
        settings = _settings(
            agent_models={
                "plan": ModelConfig(model="glm-5.3", api_key="k", base_url=GLM),
            }
        )
        settings.provider_models = (
            ProviderModelMeta("glm", "glm-5.3", context_window=1_048_576),
            ProviderModelMeta("glm", "glm-4.6", context_window=131_072),
        )
        return settings

    def test_an_unknown_model_gets_the_conservative_value(self):
        """模型表里查不到 → 保守值（偏小只是早一点触发压缩，撑溢出才是真的错）。"""
        settings = self._settings()
        settings.agent_models["plan"] = ModelConfig(model="glm-9.9", api_key="k", base_url=GLM)

        assert llm_mod.resolve_window(settings, "plan") == 128_000

    def test_the_model_wins_over_the_provider(self):
        """**这就是这次要修的**：同一家两个模型窗口不同，按家取值会把压缩线推错地方。"""
        settings = self._settings()
        settings.agent_models["plan"] = ModelConfig(model="glm-4.6", api_key="k", base_url=GLM)

        assert llm_mod.resolve_window(settings, "plan") == 131_072

    def test_a_custom_gateway_falls_back_to_the_bare_model_name(self):
        """自建网关（对不上任何一家）也能按模型名查到 —— 同名模型的窗口是一样的。"""
        settings = self._settings()
        settings.agent_models["plan"] = ModelConfig(
            model="glm-4.6", api_key="k", base_url="https://my-gateway/v1"
        )

        assert llm_mod.resolve_window(settings, "plan") == 131_072

    def test_the_row_overrides_everything(self):
        settings = self._settings()
        settings.agent_models["plan"] = ModelConfig(
            model="glm-4.6", api_key="k", base_url=GLM, context_window=8192
        )

        assert llm_mod.resolve_window(settings, "plan") == 8192

    def test_a_disabled_model_is_not_used(self):
        settings = self._settings()
        settings.provider_models = (ProviderModelMeta("glm", "glm-4.6", context_window=131_072, status=0),)
        settings.agent_models["plan"] = ModelConfig(model="glm-4.6", api_key="k", base_url=GLM)

        assert llm_mod.resolve_window(settings, "plan") == 128_000, "停用的模型不算数，退保守值"

    def test_the_metas_come_from_the_table_rows(self):
        row = SimpleNamespace(
            provider="glm", model="glm-4.6", model_type="chat", context_window=131_072, status=1
        )

        assert model_metas_of([row]) == (
            ProviderModelMeta("glm", "glm-4.6", "chat", 131_072, "manual", 1),
        )


# -- 接口：清单 / 刷新 / 手改 --------------------------------------------------


class _FakeDb:
    def __init__(self, store: list[Any]) -> None:
        self.store = store

    @asynccontextmanager
    async def session(self):
        yield None


def _patch_dao(monkeypatch, store: list[Any]) -> None:
    """把模型详情表换成一个内存 list（``upsert`` 按 (provider, model) 就地更新）。"""
    from ai_coding_harness.db import dao

    async def list_all(db, *, provider=None):
        return [row for row in store if provider is None or row.provider == provider]

    async def upsert(db, *, provider, model, model_type="chat", context_window=0,
                     source="manual", status=1):
        row = next(
            (r for r in store if r.provider == provider and r.model == model), None
        )
        if row is None:
            row = SimpleNamespace(
                provider=provider, model=model, model_type=model_type,
                context_window=context_window, source=source, status=status,
            )
            store.append(row)
            return
        row.model_type = model_type
        if context_window > 0 or not row.context_window:
            row.context_window = context_window
        row.source = source
        row.status = status

    monkeypatch.setattr(dao, "list_provider_models", list_all)
    monkeypatch.setattr(dao, "upsert_provider_model", upsert)


def _app(settings: Settings, store: list[Any]) -> Any:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(route.router)
    app.state.settings = settings
    app.state.database = _FakeDb(store)
    return app


async def _call(app: Any, method: str, path: str, **kw: Any) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.request(method, path, **kw)


class TestApi:
    async def test_the_list_comes_from_the_table(self, monkeypatch):
        store: list[Any] = [
            SimpleNamespace(provider="glm", model="glm-5.3", model_type="chat",
                            context_window=1_048_576, source="probe", status=1),
        ]
        _patch_dao(monkeypatch, store)
        settings = _settings(providers=(_glm_provider(),))
        settings.provider_models = model_metas_of(store)

        body = (await _call(_app(settings, store), "GET", "/model-config/providers/glm/models")).json()

        assert body["provider"] == "glm"
        assert body["items"] == [
            {"provider": "glm", "model": "glm-5.3", "model_type": "chat",
             "context_window": 1_048_576, "source": "probe", "status": 1}
        ]

    async def test_refresh_writes_everything_including_the_non_text_ones(self, monkeypatch):
        """刷新的口径：**全都要**（非文本也落表并标类型），界面上只显示 chat。"""
        store: list[Any] = []
        _patch_dao(monkeypatch, store)
        settings = _settings(
            providers=(_glm_provider(),),
            glm_api_key="k",
        )

        async def fake_entries(base_url, api_key, **kw):
            assert base_url == GLM and api_key == "k", "key 借用 agent 行 / .env 那家的"
            return [
                {"model": "glm-5.3", "model_type": "chat", "context_window": 1_048_576},
                {"model": "glm-5.3-embedding", "model_type": "embedding", "context_window": 8192},
            ]

        monkeypatch.setattr(route, "fetch_provider_model_entries", fake_entries)

        body = (
            await _call(
                _app(settings, store), "POST", "/model-config/providers/glm/models/refresh"
            )
        ).json()

        assert body["refreshed"] == 2
        assert {item["model"] for item in body["items"]} == {"glm-5.3", "glm-5.3-embedding"}
        assert {item["source"] for item in body["items"]} == {"probe"}
        assert settings.provider_models, "刷新之后内存那份也换了（窗口解析立刻用新的）"

    async def test_a_failed_refresh_is_a_readable_409(self, monkeypatch):
        store: list[Any] = []
        _patch_dao(monkeypatch, store)
        settings = _settings(providers=(_glm_provider(),), glm_api_key="k")

        async def boom(base_url, api_key, **kw):
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(route, "fetch_provider_model_entries", boom)

        resp = await _call(
            _app(settings, store), "POST", "/model-config/providers/glm/models/refresh"
        )

        assert resp.status_code == 409 and "ConnectError" in resp.json()["detail"]
        assert store == [], "拉不到就不该写半条"

    async def test_a_provider_without_an_address_says_so(self, monkeypatch):
        store: list[Any] = []
        _patch_dao(monkeypatch, store)
        settings = _settings(
            providers=(
                route_provider("qwen", "Qwen（阿里百炼）", ""),
            )
        )

        resp = await _call(
            _app(settings, store), "POST", "/model-config/providers/qwen/models/refresh"
        )

        assert resp.status_code == 400 and "地址" in resp.json()["detail"]

    async def test_an_unknown_provider_is_a_400(self, monkeypatch):
        store: list[Any] = []
        _patch_dao(monkeypatch, store)

        resp = await _call(
            _app(_settings(), store), "GET", "/model-config/providers/没有这家/models"
        )

        assert resp.status_code == 400

    async def test_hand_editing_a_window_keeps_the_other_fields(self, monkeypatch):
        store: list[Any] = [
            SimpleNamespace(provider="glm", model="glm-4.6", model_type="chat",
                            context_window=0, source="seed", status=1),
        ]
        _patch_dao(monkeypatch, store)
        settings = _settings(providers=(_glm_provider(),))
        settings.provider_models = model_metas_of(store)

        body = (
            await _call(
                _app(settings, store),
                "PUT",
                "/model-config/providers/glm/models",
                json={"model": "glm-4.6", "context_window": 131_072},
            )
        ).json()

        assert body["items"][0]["context_window"] == 131_072
        assert body["items"][0]["source"] == "manual"
        assert body["items"][0]["model_type"] == "chat", "没传的字段不动（类型还是按名字判出来的）"

    async def test_a_bad_type_is_a_400(self, monkeypatch):
        store: list[Any] = []
        _patch_dao(monkeypatch, store)
        settings = _settings(providers=(_glm_provider(),))

        resp = await _call(
            _app(settings, store),
            "PUT",
            "/model-config/providers/glm/models",
            json={"model": "x", "model_type": "不存在的类型"},
        )

        assert resp.status_code == 400

    async def test_an_empty_window_does_not_wipe_a_known_one(self, monkeypatch):
        """刷新时很多入口不回窗口：不能把已有的窗口抹成 0。"""
        store: list[Any] = [
            SimpleNamespace(provider="glm", model="glm-5.3", model_type="chat",
                            context_window=1_048_576, source="manual", status=1),
        ]
        _patch_dao(monkeypatch, store)
        settings = _settings(providers=(_glm_provider(),), glm_api_key="k")

        async def fake_entries(base_url, api_key, **kw):
            return [{"model": "glm-5.3", "model_type": "chat", "context_window": 0}]

        monkeypatch.setattr(route, "fetch_provider_model_entries", fake_entries)

        body = (
            await _call(
                _app(settings, store), "POST", "/model-config/providers/glm/models/refresh"
            )
        ).json()

        assert body["items"][0]["context_window"] == 1_048_576
        assert body["note"], "没回窗口要说一句"


def route_provider(key: str, label: str, url: str) -> Any:
    from ai_coding_harness.config import ProviderMeta

    return ProviderMeta(provider=key, display_name=label, default_base_url=url)


def _glm_provider() -> Any:
    return route_provider("glm", "GLM（智谱）", GLM)
