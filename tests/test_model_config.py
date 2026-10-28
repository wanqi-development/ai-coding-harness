"""模型配置（按智能体）：解析优先级、生效值、接口行为、启动加载、脱敏词表。

四条口径钉在这里：**键是智能体**（2026-10-13）、**没有出厂默认**（2026-10-15：一行没配
就报错，``source=unset``，不再回落 ``.env``）、**生效值有出处**（``effective`` 说清现在
真的用谁、是这一行还是被「统一模型」顶掉）、**保存后下一轮生效**（热切换，不用重启）。
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from ai_coding_harness import config as config_mod
from ai_coding_harness.agent import llm as llm_mod
from ai_coding_harness.api import app as app_mod
from ai_coding_harness.api.app import apply_model_endpoints, apply_model_providers
from ai_coding_harness.api.routes import model_config as route
from ai_coding_harness.config import (
    FALLBACK_CONTEXT_WINDOW,
    ModelConfig,
    ProviderModelMeta,
    Settings,
)


def _cfg(model: str, **over: Any) -> ModelConfig:
    base: dict[str, Any] = {"model": model, "api_key": "k-" + model, "base_url": "https://x/v1"}
    base.update(over)
    return ModelConfig(**base)


def _settings(**over: Any) -> Settings:
    """不读 .env：断言不该被本机配置影响（`_env_file=None`）。"""
    return Settings(_env_file=None, **over)


# -- 一、解析优先级（unified → 该智能体那一行；没有第三档）---------------------


class TestResolution:
    def test_the_role_to_row_map_covers_every_role(self):
        """六个角色都要登记：三个主控形态 + 三个子智能体。

        「编码」一主三从的归属（2026-10-14 用户口径）：主控与 coder 各占一行，
        **planner / reviewer 复用已有的「方案」/「评审」两行** —— 四个角色都有出处，
        但只有主控与编码两行要配。
        """
        assert {role: llm_mod.agent_key(role) for role in llm_mod._AGENT_KEY} == {
            "orchestrate": "coding",
            "qa": "qa",
            "plan": "plan",
            "planner": "plan",
            "coder": "coder",
            "reviewer": "review",
        }
        with pytest.raises(ValueError):
            llm_mod.agent_key("不存在的角色")

    def test_without_any_row_it_raises_and_says_which_row(self):
        """**一行都没配 = 没有模型可用**（2026-10-15 干掉出厂默认）：报错要指认哪一行。

        这条钉的是「不许偷偷回落到 ``.env``」—— 回落会让界面上什么都没配却还能跑，
        用户根本看不出自己在用哪个模型。
        """
        settings = _settings()

        for role, label in (
            ("qa", "问答"),
            ("plan", "方案"),
            ("reviewer", "评审"),
            ("coder", "编码"),
        ):
            with pytest.raises(ValueError, match=f"「{label}」这一行还没配置模型"):
                llm_mod._config_for(settings, role)

    def test_an_agent_row_is_used_for_that_role_only(self):
        settings = _settings(agent_models={"qa": _cfg("my-qa-model", context_window=2000)})

        got = llm_mod._config_for(settings, "qa")

        assert got.model == "my-qa-model" and got.context_window == 2000
        with pytest.raises(ValueError, match="「方案」"):
            llm_mod._config_for(settings, "plan")

    def test_the_own_row_wins_over_unified(self):
        """2026-10-15 用户口径：**统一模型不是总闸**，它只兜底没填的部分 ——
        配了的行按自己的走（改回来之前，配了「统一模型」就会把所有行的配置压掉）。"""
        settings = _settings(
            agent_models={
                "unified": _cfg("one-for-all"),
                "qa": _cfg("my-qa-model"),
                "coding": _cfg("my-coding-model"),
            }
        )

        assert llm_mod._config_for(settings, "qa").model == "my-qa-model"
        assert llm_mod._config_for(settings, "orchestrate").model == "my-coding-model"

    def test_unified_fills_the_rows_that_are_not_configured(self):
        """没配的角色才落统一模型（含「编码」那组的 coder）。"""
        settings = _settings(
            agent_models={"unified": _cfg("one-for-all"), "qa": _cfg("my-qa-model")}
        )

        assert llm_mod._config_for(settings, "qa").model == "my-qa-model"
        assert llm_mod._config_for(settings, "coder").model == "one-for-all"
        assert llm_mod._config_for(settings, "plan").model == "one-for-all"

    def test_a_row_without_a_model_is_not_configured(self):
        """只有 key/base_url、没模型名的行＝没配（半填的行不算一条可用的连接）。"""
        settings = _settings(
            agent_models={"qa": _cfg("", api_key="k", base_url="https://x/v1")}
        )

        with pytest.raises(ValueError, match="「问答」这一行还没配置模型"):
            llm_mod._config_for(settings, "qa")


class TestWindow:
    def test_the_row_wins(self):
        settings = _settings(agent_models={"plan": _cfg("m", context_window=65_536)})

        assert llm_mod.resolve_window(settings, "plan") == 65_536

    def test_an_unconfigured_row_gets_the_conservative_window(self):
        """没配的行说不出用哪个模型 —— 给保守值（窗口只是提示与压缩线，不该拦住这一轮）。"""
        settings = _settings()

        assert llm_mod.resolve_window(settings, "plan") == FALLBACK_CONTEXT_WINDOW

    def test_the_setting_page_can_move_the_conservative_value(self):
        """兜底值本身可以在设置页改（改成 0 时退回常量，窗口 0 会让压缩永不触发）。"""
        settings = _settings(fallback_context_window=4096)

        assert llm_mod.resolve_window(settings, "plan") == 4096

    def test_an_unregistered_role_is_a_build_time_error(self):
        """未登记的角色直接报错 ——「漏登记」是构建期的错，
        不许静默落到某个数上。**上层**（``service._main_window``）会接住它、这轮只是不给
        压力提示，不会把整轮拖死。"""
        with pytest.raises(ValueError):
            llm_mod.resolve_window(_settings(), "没这角色")


class TestFallbackAndClassifier:
    def test_the_fallback_row_is_used_when_present(self):
        settings = _settings(
            agent_models={"fallback": _cfg("fb-model", base_url="https://fb/v1")}
        )

        model = llm_mod.build_fallback(settings, role="qa")

        assert model is not None

    def test_a_fallback_equal_to_the_primary_is_skipped(self):
        """与主模型同一套：再试一次等于没兜。"""
        same = _cfg("dup")
        settings = _settings(agent_models={"qa": same, "fallback": same})

        assert llm_mod.build_fallback(settings, role="qa") is None

    def test_without_the_row_there_is_no_fallback(self):
        """没填兜底行 = 关掉兜底（``FALLBACK_PROVIDER`` 那个 .env 口径 2026-10-15 已作废）。"""
        settings = _settings(
            agent_models={"qa": _cfg("m-qa")},
            kimi_api_key="k",
            kimi_base_url="https://kimi/v1",
        )

        assert llm_mod.build_fallback(settings, role="qa") is None

    def test_the_classifier_prefers_its_own_row_then_unified(self):
        own = _settings(
            agent_models={"classifier": _cfg("cls"), "unified": _cfg("one-for-all")}
        )
        assert llm_mod.build_classifier_model(own).model_name == "cls"

        only_unified = _settings(agent_models={"unified": _cfg("one-for-all")})
        assert llm_mod.build_classifier_model(only_unified).model_name == "one-for-all"

    def test_without_any_classifier_row_it_raises(self):
        """意图识别那一关没配 → 抛错，由调用方落关键字口径（判定是增强，不是故障点）。"""
        with pytest.raises(ValueError, match="「意图识别（大模型）」这一行还没配置模型"):
            llm_mod.build_classifier_model(_settings(agent_models={"qa": _cfg("m-qa")}))


class TestSecretTerms:
    def test_model_keys_enter_the_redaction_terms(self):
        """库里/界面配的 key 也要进脱敏词表 —— 少一格就漏一格（2026-10-13）。"""
        settings = _settings(
            deepseek_api_key="env-key-123456",
            agent_models={"qa": _cfg("m", api_key="db-key-abcdef")},
        )

        values = settings.model_secret_values()

        assert "env-key-123456" in values and "db-key-abcdef" in values
        assert "" not in values, "空值不进词表（replace(\"\") 会把整段文本切碎）"


class TestProviderCatalog:
    """界面「先选提供商」用的目录（2026-10-13 用户口径①）。

    目录现在是**表**（``model_providers``）：新库按 ``config/model_providers.toml`` 灌一次，
    之后以库为准。下面两个测试钉的是「表为空时」的兜底形状（``PROVIDER_OPTIONS`` + `.env`），
    行为与加表之前完全一致。
    """

    def test_the_catalog_comes_from_env_with_a_custom_slot(self):
        settings = _settings(
            deepseek_base_url="https://api.deepseek.com",
            gpt_base_url="https://api.ztoken.pro/v1/",
        )

        catalog = settings.provider_metas()
        by_key = {meta.provider: (meta.display_name, meta.default_base_url) for meta in catalog}

        assert by_key["deepseek"] == ("DeepSeek", "https://api.deepseek.com")
        assert by_key["gpt"][1] == "https://api.ztoken.pro/v1/", "用它自己 .env 里那套网关"
        assert by_key["custom"][1] == "", "自定义没有默认地址"
        assert "kimi" in by_key and "glm" in by_key and "qwen" in by_key

    def test_the_provider_is_derived_from_the_base_url(self):
        catalog = _settings(deepseek_base_url="https://api.deepseek.com").provider_metas()

        assert config_mod.provider_key_for("https://api.deepseek.com/", catalog) == "deepseek"
        assert config_mod.provider_key_for("HTTPS://Api.DeepSeek.com", catalog) == "deepseek"
        assert config_mod.provider_key_for("https://my-own-gateway/v1", catalog) == "custom"
        assert config_mod.provider_key_for("", catalog) == "custom"


class TestProviderSeed:
    """``config/model_providers.toml`` 的解析与优先级（2026-10-14 用户口径：目录放表）。"""

    def test_the_seed_file_parses_every_section(self):
        metas = config_mod.load_provider_seed()
        by_key = {meta.provider: meta for meta in metas}

        assert {
            "deepseek", "qwen", "glm", "kimi", "doubao", "hunyuan",
            "openai", "gpt", "gemini", "grok", "custom",
        } <= set(by_key), "界面上的「提供商」下拉就照这份文件建"
        deepseek = by_key["deepseek"]
        assert deepseek.display_name == "DeepSeek"
        assert deepseek.default_base_url == "https://api.deepseek.com"
        assert deepseek.is_dynamic_model_list is True

    def test_the_seed_is_internally_coherent(self):
        """种子文件的形状约束：键唯一且合法、地址是干净的 https、分组顺序不打架。

        这些不是洁癖 —— 键是表主键，地址会带进探测请求，分组顺序决定界面的下拉长什么样。
        """
        metas = config_mod.load_provider_seed()
        keys = [meta.provider for meta in metas]

        assert len(set(keys)) == len(keys), "provider 键不许重复（它是主键）"
        assert all(re.fullmatch(r"[a-z0-9_]+", key) for key in keys), "键只能是小写字母/数字/下划线"
        for meta in metas:
            assert meta.default_base_url == meta.default_base_url.strip()
            assert not meta.default_base_url or meta.default_base_url.startswith("https://")

        domestic = [m.sort_order for m in metas if m.ui_group == "国内厂商"]
        overseas = [m.sort_order for m in metas if m.ui_group == "海外厂商"]
        assert max(domestic) < min(overseas), "两个分组不许交错（界面按 ui_group → sort_order 画）"

    def test_unknown_fields_and_broken_sections_are_skipped(self, tmp_path):
        path = tmp_path / "seed.toml"
        path.write_text(
            """
[[provider]]
provider = "ok"
display_name = "好的一节"
            "什么鬼字段" = 1

[[provider]]
display_name = "缺 provider"

[[provider]]
provider = "也缺 display_name"
""",
            encoding="utf-8",
        )

        metas = config_mod.load_provider_seed(path)

        assert [meta.provider for meta in metas] == ["ok"], "坏节跳过、白名单外的字段丢掉"

    def test_a_missing_file_is_just_empty(self, tmp_path):
        assert config_mod.load_provider_seed(tmp_path / "没有这个.toml") == ()

    def test_the_table_wins_over_the_seed(self):
        """库里有行就以库为准（改种子文件不该悄悄把用户的改动顶掉）。"""
        settings = _settings(
            deepseek_base_url="https://api.deepseek.com",
            providers=(
                config_mod.ProviderMeta(
                    provider="deepseek",
                    display_name="我自己改的名字",
                    default_base_url="https://my-gateway/v1",
                    ui_group="我的组",
                    sort_order=5,
                ),
            ),
        )

        metas = settings.provider_metas()

        assert [meta.provider for meta in metas] == ["deepseek"]
        assert metas[0].display_name == "我自己改的名字"
        assert metas[0].default_base_url == "https://my-gateway/v1"
        assert config_mod.provider_key_for("https://my-gateway/v1/", metas) == "deepseek"
        assert config_mod.provider_key_for("https://api.deepseek.com", metas) == "custom", (
            "表里没有的地址就是「自定义」——反推只认当前目录"
        )

    def test_a_disabled_provider_is_not_offered(self):
        settings = _settings(
            providers=(
                config_mod.ProviderMeta(provider="on", display_name="启用", status=1),
                config_mod.ProviderMeta(provider="off", display_name="停用", status=0),
            )
        )

        assert [meta.provider for meta in settings.provider_metas()] == ["on"]

    def test_the_order_is_group_then_sort_then_key(self):
        settings = _settings(
            providers=(
                config_mod.ProviderMeta(
                    provider="b", display_name="B", ui_group="group-2", sort_order=10
                ),
                config_mod.ProviderMeta(
                    provider="a", display_name="A", ui_group="group-1", sort_order=20
                ),
                config_mod.ProviderMeta(
                    provider="c", display_name="C", ui_group="group-1", sort_order=10
                ),
            )
        )

        assert [meta.provider for meta in settings.provider_metas()] == ["c", "a", "b"], (
            "排序在后端做，界面照画（口径只有一个出处）"
        )

    def test_a_row_becomes_a_meta(self):
        row = SimpleNamespace(
            provider="x",
            display_name="X",
            default_base_url=None,
            ui_group=None,
            sort_order=None,
            status=None,
            is_dynamic_model_list=False,
            models_path=None,
            auth_header=None,
            auth_prefix=None,
        )

        meta = config_mod.provider_meta_of(row)

        assert meta.display_name == "X"
        assert meta.default_base_url == "" and meta.auth_prefix == ""
        assert meta.status == 1, "没写 status 的行＝启用（不是被停用）"
        assert meta.models_path == "/models"
        assert meta.auth_header == "Authorization"


# -- 二、启动加载 --------------------------------------------------------------


class _FakeDb:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    @asynccontextmanager
    async def session(self):
        yield None


async def test_startup_loading_reads_rows_and_intent_mode(monkeypatch):
    from ai_coding_harness.db import dao

    row = SimpleNamespace(
        agent="qa", model_name="row-model", base_url="https://row/v1",
        api_key="row-key", context_window=4096,
    )
    mode = SimpleNamespace(agent="intent_mode", model_name="llm", base_url=None, api_key=None,
                           context_window=0)
    blank = SimpleNamespace(agent="plan", model_name="", base_url="https://x/v1", api_key="k",
                            context_window=0)
    jev = SimpleNamespace(agent="jev", model_name="", base_url="https://openrouter.ai/api",
                          api_key="sk-jev", context_window=0)

    async def fake_list(db):
        return [row, mode, blank, jev]

    monkeypatch.setattr(dao, "list_model_endpoints", fake_list)
    settings = _settings()

    await apply_model_endpoints(settings, _FakeDb([]))

    assert settings.agent_models["qa"].model == "row-model"
    assert settings.agent_models["qa"].context_window == 4096
    assert "plan" not in settings.agent_models, "没模型名的行＝没配"
    assert settings.intent_mode == "llm"
    # JEV 那一行不建 BaseChatModel（走 SDK）：地址 / key 装到 typesafe_* 上，模型名留空 = SDK 默认
    assert (settings.typesafe_api_key, settings.typesafe_base_url) == (
        "sk-jev",
        "https://openrouter.ai/api",
    )
    assert settings.typesafe_model_name == "jev-latest"
    assert "jev" not in settings.agent_models, "JEV 不是聊天模型，不进 agent_models"


async def test_a_broken_table_only_warns(monkeypatch):
    """配置表读不了 → 一份连接都没有也要能起（不能因为配置坏了起不来；用到哪一行哪一行报错）。"""
    from ai_coding_harness.db import dao

    async def boom(db):
        raise RuntimeError("表不存在")

    monkeypatch.setattr(dao, "list_model_endpoints", boom)
    settings = _settings()

    await apply_model_endpoints(settings, _FakeDb([]))

    assert settings.agent_models == {} and settings.intent_mode == "jev"
    assert settings.typesafe_api_key == "", "JEV 也要清干净（先清空再落的另一半）"


class TestProviderStartup:
    """提供商目录的启动装配：**表为空才灌种子**，之后以库为准（2026-10-14）。"""

    def _patch_dao(self, monkeypatch, rows, *, count=None):
        from ai_coding_harness.db import dao

        seeded: list[Any] = []

        async def fake_count(db):
            return len(rows) if count is None else count

        async def fake_list(db):
            return list(rows)

        async def fake_seed(db, metas):
            seeded.extend(metas)
            rows.extend(metas)  # 灌进库＝之后 list 就能读到（真库里就是这么发生的）
            return len(metas)

        monkeypatch.setattr(dao, "count_model_providers", fake_count)
        monkeypatch.setattr(dao, "list_model_providers", fake_list)
        monkeypatch.setattr(dao, "seed_model_providers", fake_seed)
        return seeded

    async def test_an_empty_table_is_seeded_from_the_file(self, monkeypatch):
        seeded = self._patch_dao(monkeypatch, [])
        settings = _settings()

        await apply_model_providers(settings, _FakeDb([]))

        assert seeded, "空表要灌一次种子"
        assert {meta.provider for meta in settings.providers} >= {"deepseek", "custom"}

    async def test_a_non_empty_table_is_used_as_is(self, monkeypatch):
        row = SimpleNamespace(
            provider="mine", display_name="我的网关", default_base_url="https://mine/v1",
            ui_group="", sort_order=1, status=1, is_dynamic_model_list=False,
            models_path="/v1/models", auth_header="x-api-key", auth_prefix="",
        )
        seeded = self._patch_dao(monkeypatch, [row])
        settings = _settings()

        await apply_model_providers(settings, _FakeDb([]))

        assert not seeded, "库里有行就不该再灌种子（用户的改动优先）"
        assert [meta.provider for meta in settings.providers] == ["mine"]
        assert settings.providers[0].auth_header == "x-api-key"

    async def test_a_broken_table_only_warns(self, monkeypatch):
        from ai_coding_harness.db import dao

        async def boom(db):
            raise RuntimeError("表不存在")

        monkeypatch.setattr(dao, "count_model_providers", boom)
        settings = _settings()

        await apply_model_providers(settings, _FakeDb([]))

        assert settings.providers == (), "坏表 → 退回内置那份，界面照样能用"


# -- 三、接口 ------------------------------------------------------------------


def _app(settings: Settings | None = None) -> Any:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(route.router)
    app.state.settings = settings or _settings()
    app.state.database = _FakeDb([])
    return app


def _row_of(meta: config_mod.ProviderMeta) -> Any:
    """``ProviderMeta`` → 表的行形状（``model_providers`` 一行的样子）。"""
    return SimpleNamespace(
        provider=meta.provider,
        display_name=meta.display_name,
        default_base_url=meta.default_base_url,
        ui_group=meta.ui_group,
        sort_order=meta.sort_order,
        status=meta.status,
        is_dynamic_model_list=meta.is_dynamic_model_list,
        models_path=meta.models_path,
        auth_header=meta.auth_header,
        auth_prefix=meta.auth_prefix,
    )


def _provider_row(**over: Any) -> Any:
    """一行**目录**（DB 形状）：测试里想改哪格就传哪格。"""
    return _row_of(_provider(**over))


def _seed_rows() -> list[Any]:
    """拿真种子文件当目录数据（形状与真库一致）。"""
    return [_row_of(meta) for meta in config_mod.load_provider_seed()]


def _patch_dao(
    monkeypatch,
    store: dict[str, Any],
    *,
    providers: list[Any] | None = None,
    provider_models: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把连接表换成一个内存 dict，验的是接口行为而不是库。

    目录（``model_providers``）**默认给种子文件那几行** —— 路由现在是**每条请求现读库**的，
    不喂给它就等于空目录。要验「库改了立即生效」就传 ``providers=``。

    ``provider_models``（模型详情表）给一个内存 dict 用；不传就新建一个 —— 返回它，
    调用方可以断言「刷新清单往表里写了什么」。
    """
    from ai_coding_harness.db import dao

    rows_provider = _seed_rows() if providers is None else providers
    models_table: dict[str, Any] = {} if provider_models is None else provider_models

    async def list_all(db, *, user_id=None):
        return list(store.values())

    async def list_providers(db):
        return list(rows_provider)

    async def count_providers(db):
        return len(rows_provider)

    async def upsert_provider_row(
        db,
        *,
        provider,
        display_name,
        default_base_url="",
        ui_group="",
        sort_order=0,
        status=1,
        is_dynamic_model_list=True,
        models_path="/models",
        auth_header="Authorization",
        auth_prefix="Bearer ",
    ):
        """整行覆盖（与真 DAO 同语义）：没有这家就插一行，有就全字段写过去。"""
        row = next((item for item in rows_provider if item.provider == provider), None)
        if row is None:
            row = SimpleNamespace(provider=provider)
            rows_provider.append(row)
        row.display_name = display_name
        row.default_base_url = default_base_url
        row.ui_group = ui_group
        row.sort_order = sort_order
        row.status = status
        row.is_dynamic_model_list = is_dynamic_model_list
        row.models_path = models_path
        row.auth_header = auth_header
        row.auth_prefix = auth_prefix

    async def upsert_model_row(db, *, provider, model, model_type="chat", context_window=0,
                               source="manual", status=1):
        models_table[model] = SimpleNamespace(
            provider=provider, model=model, model_type=model_type,
            context_window=context_window, source=source, status=status,
        )

    async def list_model_rows(db, *, provider=None):
        rows = list(models_table.values())
        return [row for row in rows if provider is None or row.provider == provider]

    async def upsert(db, *, user_id=None, agent, base_url=None, model_name=None, api_key=None,
                     context_window=None):
        row = store.get(agent) or SimpleNamespace(
            agent=agent, base_url=None, model_name=None, api_key=None, context_window=0
        )
        for field, value in (
            ("base_url", base_url), ("model_name", model_name),
            ("api_key", api_key), ("context_window", context_window),
        ):
            if value is not None:
                setattr(row, field, value)
        store[agent] = row

    async def remove(db, *, user_id=None, agent):
        return store.pop(agent, None) is not None

    monkeypatch.setattr(dao, "list_model_endpoints", list_all)
    monkeypatch.setattr(dao, "upsert_model_endpoint", upsert)
    monkeypatch.setattr(dao, "delete_model_endpoint", remove)
    monkeypatch.setattr(dao, "list_model_providers", list_providers)
    monkeypatch.setattr(dao, "count_model_providers", count_providers)
    monkeypatch.setattr(dao, "upsert_model_provider", upsert_provider_row)
    monkeypatch.setattr(dao, "upsert_provider_model", upsert_model_row)
    monkeypatch.setattr(dao, "list_provider_models", list_model_rows)
    return models_table


async def _call(app: Any, method: str, path: str, **kw: Any) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.request(method, path, **kw)


class TestApi:
    async def test_provider_editing_lists_every_provider_including_disabled(self, monkeypatch):
        """设置页「提供商」那一栏（2026-10-15 用户口径 b）：**全量、含停用** ——
        停用的家要能看见才能改回来（快照里那份仍是"只含启用"，给弹窗下拉用）。"""
        rows = [
            _provider_row(
                provider="glm",
                display_name="GLM（智谱）",
                default_base_url="https://open.bigmodel.cn/api/paas/v4/",
                models_path="/v1/models",
                ui_group="国内厂商",
                sort_order=30,
            ),
            _provider_row(provider="dead", display_name="停用的家", status=0),
        ]
        _patch_dao(monkeypatch, {}, providers=rows)
        app = _app(_settings())

        body = (await _call(app, "GET", "/model-config/providers")).json()
        snapshot = (await _call(app, "GET", "/model-config")).json()

        got = {item["key"]: item for item in body}
        assert set(got) == {"glm", "dead"}, "编辑那一栏含停用的家"
        assert got["glm"]["models_path"] == "/v1/models"
        assert got["glm"]["auth_header"] == "Authorization"
        assert got["dead"]["status"] == 0
        assert {item["key"] for item in snapshot["providers"]} == {"glm"}, "下拉只给启用的"

    async def test_put_rewrites_the_whole_row_and_takes_effect_at_once(self, monkeypatch):
        """整行覆盖写 + **当场刷内存那份** —— 「改完即生效」靠的就是它（不用重启）。"""
        rows = [_provider_row(provider="glm", display_name="GLM（智谱）", models_path="/models")]
        _patch_dao(monkeypatch, {}, providers=rows)
        settings = _settings()
        await apply_model_providers(settings, _FakeDb([]))  # 内存那份先是旧值
        assert next(m for m in settings.providers if m.provider == "glm").models_path == "/models"

        body = (
            await _call(
                _app(settings),
                "PUT",
                "/model-config/providers/glm",
                json={
                    "label": "GLM（智谱）",
                    "base_url": "https://open.bigmodel.cn/api/paas/v4/",
                    "ui_group": "国内厂商",
                    "sort_order": 30,
                    "models_path": "/v1/models",
                    "auth_header": "Authorization",
                    "auth_prefix": "Bearer ",
                    "status": 1,
                    "is_dynamic_model_list": True,
                },
            )
        ).json()

        row = rows[0]
        assert row.models_path == "/v1/models" and row.sort_order == 30
        assert {item["key"]: item for item in body}["glm"]["models_path"] == "/v1/models"
        assert (
            next(m for m in settings.providers if m.provider == "glm").models_path == "/v1/models"
        ), "内存那份立刻跟上"

    async def test_a_new_provider_can_be_added_and_bad_input_is_a_400(self, monkeypatch):
        rows: list[Any] = []
        _patch_dao(monkeypatch, {}, providers=rows)
        app = _app(_settings())

        created = await _call(
            app,
            "PUT",
            "/model-config/providers/typesafe",
            json={"label": "JEV", "base_url": "https://api.ztoken.pro/v1/"},
        )
        assert created.status_code == 200
        assert {item["key"] for item in created.json()} == {"typesafe"}, "新增一家 = 直接写进去"

        bad_key = await _call(app, "PUT", "/model-config/providers/Bad Key", json={"label": "x"})
        no_label = await _call(app, "PUT", "/model-config/providers/ok", json={"label": "   "})
        bad_status = await _call(
            app, "PUT", "/model-config/providers/ok", json={"label": "x", "status": 7}
        )
        empty_path = await _call(
            app, "PUT", "/model-config/providers/ok", json={"label": "x", "models_path": " "}
        )

        assert [item.status_code for item in (bad_key, no_label, bad_status, empty_path)] == [
            400,
            400,
            400,
            400,
        ]
        assert {item.provider for item in rows} == {"typesafe"}, "坏请求不落半行"

    async def test_refresh_uses_the_edited_models_path(self, monkeypatch):
        """改完目录行之后，「刷新清单」**立刻**用新路径 —— 以前它读的是启动快照
        （改了 `models_path` 得重启才生效，那「改完即生效」就是假的）。"""
        rows = [
            _provider_row(
                provider="glm",
                display_name="GLM（智谱）",
                default_base_url="https://open.bigmodel.cn/api/paas/v4/",
                models_path="/models",
            )
        ]
        table = _patch_dao(monkeypatch, {}, providers=rows)
        seen: dict[str, str] = {}

        async def fake_entries(base_url, api_key, *, path, auth_header, auth_prefix):
            seen["url"] = route._models_url(base_url, path)
            return [{"model": "glm-5.3", "model_type": "chat", "context_window": 1_048_576}]

        monkeypatch.setattr(route.providers, "fetch_provider_model_entries", fake_entries)
        settings = _settings()
        app = _app(settings)
        await apply_model_providers(settings, _FakeDb([]))  # 内存那份还拿着旧路径

        await _call(
            app,
            "PUT",
            "/model-config/providers/glm",
            json={
                "label": "GLM（智谱）",
                "base_url": "https://open.bigmodel.cn/api/paas/v4/",
                "models_path": "/v1/models",
            },
        )
        body = (await _call(app, "POST", "/model-config/providers/glm/models/refresh")).json()

        assert seen["url"] == "https://open.bigmodel.cn/api/paas/v4/v1/models"
        assert body["refreshed"] == 1
        assert table["glm-5.3"].context_window == 1_048_576, "拉到的窗口落进模型详情表"

    async def test_get_lists_every_row_with_masked_keys(self, monkeypatch):
        settings = _settings(deepseek_base_url="https://api.deepseek.com")
        store: dict[str, Any] = {
            "qa": SimpleNamespace(
                agent="qa", base_url="https://api.deepseek.com", model_name="row-model",
                api_key="sk-abcdefghijk", context_window=4096,
            )
        }
        _patch_dao(monkeypatch, store)

        body = (await _call(_app(settings), "GET", "/model-config")).json()

        rows = {row["key"]: row for row in body["rows"]}
        assert {item["key"] for item in body["providers"]} >= {
            "deepseek", "kimi", "glm", "gpt", "qwen", "custom",
        }, "快照要带上厂商目录（界面「先选提供商」用它带出地址）"
        # 行名用中文（用户口径）；「编码」一主三从拆成主控 + 编码两行（2026-10-14）
        assert [row["label"] for row in body["rows"]][:5] == [
            "问答", "方案", "评审", "编码主控", "编码",
        ]
        assert rows["qa"]["source"] == "db" and rows["qa"]["model"] == "row-model"
        assert rows["qa"]["provider"] == "deepseek", "按 base_url 反推提供商（纯界面提示）"
        assert rows["qa"]["api_key_masked"] == "sk-…hijk"
        assert "abcdefghijk" not in str(body), "明文 key 绝不出口"
        assert rows["plan"]["source"] == "unset", "没配就是 unset（没有出厂默认可回显）"
        assert rows["plan"]["base_url"] == "" and rows["plan"]["model"] == ""
        assert body["intent_mode"] == "jev"
        # 界面分组与「编码一主三从」的角色归属都由后端下发（前端不再硬编码行名）
        assert [item["key"] for item in body["sections"]] == ["agents", "global", "intent"]
        assert {row["key"]: row["section"] for row in body["rows"]}["jev"] == "intent"
        assert {role["role"]: role["row"] for role in body["coding_group"]} == {
            "orchestrate": "coding",
            "planner": "plan",
            "coder": "coder",
            "reviewer": "review",
        }
        assert rows["plan"]["roles"] == ["plan", "planner"], "一行服务哪些角色由后端下发"
        assert rows["jev"]["provider"] == "typesafe" and rows["jev"]["provider_locked"] is True

    async def test_effective_says_what_is_really_in_use(self, monkeypatch):
        """「生效值」= 现在**真的**用谁：自己配的、靠统一模型兜底的、还是没配。"""
        store: dict[str, Any] = {
            "qa": SimpleNamespace(
                agent="qa", base_url="https://row/v1", model_name="qa-model",
                api_key="k", context_window=0,
            ),
            "unified": SimpleNamespace(
                agent="unified", base_url="https://one/v1", model_name="one-for-all",
                api_key="k", context_window=0,
            ),
        }
        _patch_dao(monkeypatch, store)
        # 生效值读的是**内存那份**（启动 / 保存时从库里落进去的），所以这里两半都要有
        settings = _settings(
            agent_models={
                "qa": _cfg("qa-model", base_url="https://row/v1"),
                "unified": _cfg("one-for-all", base_url="https://one/v1"),
            }
        )

        rows = {
            row["key"]: row
            for row in (await _call(_app(settings), "GET", "/model-config")).json()["rows"]
        }

        # 配了的行按自己的走（不再被统一模型压掉）
        assert rows["qa"]["effective"]["model"] == "qa-model"
        assert rows["qa"]["effective"]["source"] == "row"
        # 没配的行才落统一模型
        assert rows["plan"]["effective"]["model"] == "one-for-all"
        assert rows["plan"]["effective"]["source"] == "unified"

    async def test_effective_is_unset_when_nothing_covers_the_role(self, monkeypatch):
        """既没配这一行、也没有统一模型 → ``unset``（界面据此说「用不了」）。"""
        store: dict[str, Any] = {
            "qa": SimpleNamespace(
                agent="qa", base_url="https://row/v1", model_name="qa-model",
                api_key="k", context_window=0,
            )
        }
        _patch_dao(monkeypatch, store)
        settings = _settings(agent_models={"qa": _cfg("qa-model", base_url="https://row/v1")})

        rows = {
            row["key"]: row
            for row in (await _call(_app(settings), "GET", "/model-config")).json()["rows"]
        }

        assert rows["plan"]["effective"] == {
            "role": "plan",
            "row": "",
            "model": "",
            "window": 0,
            "window_source": "none",
            "base_url": "",
            "has_key": False,
            "source": "unset",
        }

    async def test_the_unified_row_shows_itself_not_another_row(self, monkeypatch):
        """「统一模型」那一行看的是**它自己**（2026-10-15 用户报的：「里面明明是 deepseek，
        外面显示的却是 glm」—— 那是拿 orchestrate 去算的，行优先之后取到了「编码主控」）。"""
        store: dict[str, Any] = {
            "unified": SimpleNamespace(
                agent="unified", base_url="https://api.deepseek.com", model_name="deepseek-flash",
                api_key="k", context_window=0,
            ),
            "coding": SimpleNamespace(
                agent="coding", base_url="https://open.bigmodel.cn/api/paas/v4/",
                model_name="glm-5.3", api_key="k", context_window=0,
            ),
        }
        _patch_dao(monkeypatch, store)
        settings = _settings(
            agent_models={
                "unified": _cfg("deepseek-flash", base_url="https://api.deepseek.com"),
                "coding": _cfg("glm-5.3", base_url="https://open.bigmodel.cn/api/paas/v4/"),
            }
        )

        rows = {
            row["key"]: row
            for row in (await _call(_app(settings), "GET", "/model-config")).json()["rows"]
        }

        assert rows["unified"]["effective"]["model"] == "deepseek-flash"
        assert rows["unified"]["effective"]["source"] == "row"
        assert rows["coding"]["effective"]["model"] == "glm-5.3", "编码主控按自己的走"

    async def test_the_window_says_where_it_came_from(self, monkeypatch):
        """窗口那一格要能分辨「查到的」和「我们自己兜的 128k」—— 界面只回显前者。"""
        _patch_dao(monkeypatch, {}, provider_models={})
        settings = _settings(
            agent_models={"plan": _cfg("glm-5.3", base_url="https://open.bigmodel.cn/api/paas/v4/")}
        )
        assert llm_mod.effective_for(settings, "plan")["window_source"] == "fallback"

        # 表里登记了这个模型的窗口 → 来源变成 model
        settings.provider_models = (ProviderModelMeta("glm", "glm-5.3", context_window=1_048_576),)
        eff = llm_mod.effective_for(settings, "plan")
        assert (eff["window"], eff["window_source"]) == (1_048_576, "model")

        # 行内手填 → 来源是 row（优先级最高）
        settings.agent_models["plan"] = ModelConfig(
            model="glm-5.3", api_key="k", base_url="https://open.bigmodel.cn/api/paas/v4/",
            context_window=65_536,
        )
        eff = llm_mod.effective_for(settings, "plan")
        assert (eff["window"], eff["window_source"]) == (65_536, "row")

    async def test_put_leaves_untouched_fields_alone(self, monkeypatch):
        store: dict[str, Any] = {
            "qa": SimpleNamespace(
                agent="qa", base_url="https://keep/v1", model_name="old",
                api_key="sk-keep", context_window=0,
            )
        }
        _patch_dao(monkeypatch, store)

        body = (
            await _call(
                _app(), "PUT", "/model-config", json={"rows": {"qa": {"model": "new"}}}
            )
        ).json()

        rows = {row["key"]: row for row in body["rows"]}
        assert rows["qa"]["model"] == "new"
        assert rows["qa"]["base_url"] == "https://keep/v1", "没传的字段不动"
        assert rows["qa"]["has_key"] is True

    async def test_put_rejects_clearing_a_key_while_the_model_remains_configured(
        self, monkeypatch
    ):
        store: dict[str, Any] = {
            "qa": SimpleNamespace(
                agent="qa", base_url="https://x/v1", model_name="m",
                api_key="sk-x", context_window=0,
            )
        }
        _patch_dao(monkeypatch, store)

        resp = await _call(
            _app(), "PUT", "/model-config", json={"rows": {"qa": {"api_key": ""}}}
        )

        assert resp.status_code == 400
        assert "缺少 api_key" in resp.json()["detail"]
        assert store["qa"].api_key == "sk-x", "失败时不能真的清掉旧 key"

    async def test_put_rejects_unknown_keys_and_bad_mode(self, monkeypatch):
        _patch_dao(monkeypatch, {})

        bad_key = await _call(_app(), "PUT", "/model-config", json={"rows": {"nope": {}}})
        bad_mode = await _call(_app(), "PUT", "/model-config", json={"intent_mode": "x"})

        assert bad_key.status_code == 400 and bad_mode.status_code == 400

    async def test_delete_clears_the_row(self, monkeypatch):
        store: dict[str, Any] = {
            "qa": SimpleNamespace(
                agent="qa", base_url="https://x/v1", model_name="m",
                api_key="sk-x", context_window=0,
            )
        }
        _patch_dao(monkeypatch, store)

        body = (await _call(_app(), "DELETE", "/model-config/qa")).json()

        rows = {row["key"]: row for row in body["rows"]}
        assert rows["qa"]["source"] == "unset" and "qa" not in store
        assert (await _call(_app(), "DELETE", "/model-config/nope")).status_code == 400

    async def test_saving_refreshes_the_in_memory_config_at_once(self, monkeypatch):
        """配置热生效：保存之后**同一个进程里** `settings` 就变了 —— 主智能体每轮现建，
        于是下一轮就用新连接，不用重启后端（2026-10-14 用户口径）。"""
        store: dict[str, Any] = {}
        _patch_dao(monkeypatch, store)
        settings = _settings()
        app = _app(settings)

        await _call(
            app,
            "PUT",
            "/model-config",
            json={
                "rows": {
                    "plan": {
                        "model": "new-model",
                        "base_url": "https://new/v1",
                        "api_key": "new-key",
                    }
                }
            },
        )

        assert settings.agent_models["plan"].model == "new-model"

    async def test_restoring_a_row_also_clears_it_from_memory(self, monkeypatch):
        """恢复默认之后内存里不能留幽灵：`apply_model_endpoints` 是**先清空再落**的。"""
        store: dict[str, Any] = {"plan": SimpleNamespace(
            agent="plan", base_url="https://x/v1", model_name="m", api_key="k", context_window=0,
        )}
        _patch_dao(monkeypatch, store)
        settings = _settings(agent_models={"plan": _cfg("m", base_url="https://x/v1")})
        app = _app(settings)

        await _call(app, "DELETE", "/model-config/plan")

        assert "plan" not in settings.agent_models

    async def test_saving_the_jev_row_feeds_typesafe_settings(self, monkeypatch):
        """JEV 那一行：走 SDK、不建 ``BaseChatModel``，所以它的连接灌进 ``typesafe_*``
        （2026-10-15 用户口径：供应商锁 ``typesafe``，只填 base_url + key 就能用）。"""
        store: dict[str, Any] = {}
        _patch_dao(monkeypatch, store)
        settings = _settings()

        body = (
            await _call(
                _app(settings),
                "PUT",
                "/model-config",
                json={
                    "rows": {
                        "jev": {
                            "base_url": "https://openrouter.ai/api",
                            "api_key": "sk-jev",
                        }
                    }
                },
            )
        ).json()

        assert settings.typesafe_api_key == "sk-jev"
        assert settings.typesafe_base_url == "https://openrouter.ai/api"
        assert settings.typesafe_model_name == "jev-latest", "模型名留空 = SDK 默认"
        assert "jev" not in settings.agent_models, "JEV 不是聊天模型"
        rows = {row["key"]: row for row in body["rows"]}
        assert rows["jev"]["provider"] == "typesafe" and rows["jev"]["provider_locked"] is True
        assert rows["jev"]["model"] == "", "库里的模型名留空（走 SDK 默认）"
        assert body["jev_configured"] is True, "配齐了：地址 + key"
        assert "sk-jev" not in str(body), "明文 key 绝不出口"

    async def test_probe_returns_models_and_window(self, monkeypatch):
        _patch_dao(monkeypatch, {})

        async def fake(base_url: str, api_key: str, **kw: Any):
            assert (base_url, api_key) == ("https://row/v1", "sk-fresh")
            return ["m1", "m2"], 65_536

        monkeypatch.setattr(route.probe, "fetch_provider_models", fake)

        body = (
            await _call(
                _app(),
                "POST",
                "/model-config/probe",
                json={"base_url": "https://row/v1", "api_key": "sk-fresh"},
            )
        ).json()

        assert body == {
            "ok": True, "models": ["m1", "m2"], "source": "live", "context_window": 65_536,
            "note": "", "error": "",
        }

    async def test_probe_without_a_window_asks_the_user_to_fill_it(self, monkeypatch):
        """读不到窗口＝预期结果之一：界面据此提示用户自己填（留空按 128k 兜）。"""
        _patch_dao(monkeypatch, {})

        async def fake(base_url: str, api_key: str, **kw: Any):
            return ["m1"], 0

        monkeypatch.setattr(route.probe, "fetch_provider_models", fake)

        body = (
            await _call(
                _app(),
                "POST",
                "/model-config/probe",
                json={"base_url": "https://row/v1", "api_key": "k"},
            )
        ).json()

        assert body["ok"] is True and body["context_window"] == 0 and body["note"]

    async def test_a_failed_probe_is_a_result_not_a_500(self, monkeypatch):
        _patch_dao(monkeypatch, {})

        async def boom(base_url: str, api_key: str, **kw: Any):
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(route.probe, "fetch_provider_models", boom)

        resp = await _call(
            _app(),
            "POST",
            "/model-config/probe",
            json={"base_url": "https://row/v1", "api_key": "k"},
        )

        assert resp.status_code == 200
        assert resp.json()["ok"] is False and "ConnectError" in resp.json()["error"]

    async def test_probe_can_borrow_the_key_from_a_row(self, monkeypatch):
        """界面上没重填 key：用库里那一份（后端内部用，明文不回前端）。

        读的是**库里的行**而不是内存那份：只填了地址与 key、还没填模型名的行也在库里、
        也能借（那样的行不在 ``settings.agent_models`` 里）。
        """
        store: dict[str, Any] = {
            "qa": SimpleNamespace(
                agent="qa", base_url="https://row/v1", model_name="",
                api_key="sk-from-row", context_window=0,
            )
        }
        _patch_dao(monkeypatch, store)
        seen: dict[str, str] = {}

        async def fake(base_url: str, api_key: str, **kw: Any):
            seen.update(base_url=base_url, api_key=api_key)
            return [], 0

        monkeypatch.setattr(route.probe, "fetch_provider_models", fake)

        await _call(
            _app(_settings()),
            "POST",
            "/model-config/probe",
            json={"base_url": "https://row/v1", "key_from": "qa"},
        )

        assert seen["api_key"] == "sk-from-row"

    async def test_probe_can_borrow_the_jev_key(self, monkeypatch):
        """JEV 那一行的 key 存在 ``typesafe_api_key`` 上（它不建 BaseChatModel）。"""
        _patch_dao(monkeypatch, {})
        seen: dict[str, str] = {}

        async def fake(base_url: str, api_key: str, **kw: Any):
            seen.update(base_url=base_url, api_key=api_key)
            return [], 0

        monkeypatch.setattr(route.probe, "fetch_provider_models", fake)

        await _call(
            _app(_settings(typesafe_api_key="sk-jev")),
            "POST",
            "/model-config/probe",
            json={"base_url": "https://openrouter.ai/api", "key_from": "jev"},
        )

        assert seen["api_key"] == "sk-jev"


def _provider(**over: Any) -> config_mod.ProviderMeta:
    base: dict[str, Any] = {"provider": "p", "display_name": "P"}
    base.update(over)
    return config_mod.ProviderMeta(**base)


class TestProbeWithDirectory:
    """「测试连接」按目录行走：去哪拉、怎么鉴权、拉不到给什么（2026-10-14 用户口径③）。"""

    async def test_the_provider_decides_path_and_auth_header(self, monkeypatch):
        _patch_dao(
            monkeypatch,
            {},
            providers=[
                _provider_row(
                    provider="gw",
                    display_name="自建网关",
                    models_path="/v1/models",
                    auth_header="x-api-key",
                    auth_prefix="",
                )
            ],
        )
        seen: dict[str, Any] = {}

        async def fake(base_url: str, api_key: str, **kw: Any):
            seen.update(base_url=base_url, api_key=api_key, **kw)
            return ["m1"], 0

        monkeypatch.setattr(route.probe, "fetch_provider_models", fake)

        await _call(
            _app(),
            "POST",
            "/model-config/probe",
            json={"base_url": "https://gw/v1", "api_key": "k", "provider": "gw"},
        )

        assert seen["path"] == "/v1/models"
        assert seen["auth_header"] == "x-api-key" and seen["auth_prefix"] == ""

    async def test_a_failed_probe_reports_the_error_and_no_candidates(self, monkeypatch):
        """连不上就老实说连不上：**不再回内置候选**（那份种子清单 2026-10-15 已删）——
        模型名只认「刷新清单」拉到的那份。"""
        _patch_dao(
            monkeypatch,
            {},
            providers=[_provider_row(provider="gw", display_name="自建")],
        )

        async def boom(base_url: str, api_key: str, **kw: Any):
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(route.probe, "fetch_provider_models", boom)

        body = (
            await _call(
                _app(),
                "POST",
                "/model-config/probe",
                json={"base_url": "https://gw/v1", "api_key": "k", "provider": "gw"},
            )
        ).json()

        assert body["ok"] is False and body["error"]
        assert body["models"] == [] and body["source"] == "none"
        assert body["note"]

    async def test_the_probe_ignores_the_directory_switch(self, monkeypatch):
        """目录行写着「这家没有实时清单接口」也照样发请求 —— 用户口径：能不能测只看地址与 key。"""
        _patch_dao(
            monkeypatch,
            {},
            providers=[
                _provider_row(
                    provider="gw",
                    display_name="自建",
                    is_dynamic_model_list=False,
                )
            ],
        )
        seen: dict[str, Any] = {}

        async def fake(base_url: str, api_key: str, **kw: Any):
            seen["called"] = base_url
            return ["live1"], 0

        monkeypatch.setattr(route.probe, "fetch_provider_models", fake)

        body = (
            await _call(
                _app(),
                "POST",
                "/model-config/probe",
                json={"base_url": "https://gw/v1", "api_key": "k", "provider": "gw"},
            )
        ).json()

        assert seen.get("called") == "https://gw/v1", "开关不该拦住一次真的连通性测试"
        assert body["ok"] is True and body["models"] == ["live1"] and body["source"] == "live"

    async def test_an_empty_live_list_is_an_error_not_candidates(self, monkeypatch):
        """入口回了个空清单也算拉不到：**不拿内置候选顶上**，老实回 ok=false。"""
        _patch_dao(
            monkeypatch,
            {},
            providers=[_provider_row(provider="gw", display_name="自建")],
        )

        async def fake(base_url: str, api_key: str, **kw: Any):
            return [], 0

        monkeypatch.setattr(route.probe, "fetch_provider_models", fake)

        body = (
            await _call(
                _app(),
                "POST",
                "/model-config/probe",
                json={"base_url": "https://gw/v1", "api_key": "k", "provider": "gw"},
            )
        ).json()

        assert body["ok"] is False and body["models"] == [] and body["source"] == "none"
        assert body["note"], "说清是「入口没回清单」而不是「连不上」"

    async def test_the_directory_row_reaches_the_ui(self, monkeypatch):
        _patch_dao(
            monkeypatch,
            {},
            providers=[
                _provider_row(
                    provider="gw",
                    display_name="自建网关",
                    ui_group="海外厂商",
                    sort_order=7,
                ),
                _provider_row(provider="plain", display_name="匿名入口", auth_header=""),
            ],
        )

        body = (await _call(_app(), "GET", "/model-config")).json()

        by_key = {item["key"]: item for item in body["providers"]}
        assert by_key["gw"]["ui_group"] == "海外厂商" and by_key["gw"]["sort_order"] == 7

    async def test_the_catalog_is_read_from_the_table_on_every_request(self, monkeypatch):
        """改库 + 刷新界面就生效，**不用重启后端**（用户口径：这个目录直接读库）。"""
        rows = [_provider_row(provider="gw", display_name="改之前", default_base_url="https://old/v1")]
        _patch_dao(monkeypatch, {}, providers=rows)
        app = _app()

        before = (await _call(app, "GET", "/model-config")).json()["providers"]
        rows[0] = _provider_row(
            provider="gw", display_name="改之后", default_base_url="https://new/v1"
        )
        after = (await _call(app, "GET", "/model-config")).json()["providers"]

        assert [(p["label"], p["base_url"]) for p in before] == [("改之前", "https://old/v1")]
        assert [(p["label"], p["base_url"]) for p in after] == [("改之后", "https://new/v1")]

    async def test_a_broken_catalog_falls_back_to_the_startup_snapshot(self, monkeypatch):
        """目录读不出来（表坏了）也不该让整页打不开：退回启动时那份。"""
        from ai_coding_harness.db import dao

        async def boom(db):
            raise RuntimeError("表不存在")

        _patch_dao(monkeypatch, {})
        monkeypatch.setattr(dao, "list_model_providers", boom)
        settings = _settings(
            providers=(config_mod.ProviderMeta(provider="fallback", display_name="兜底那份"),)
        )

        body = (await _call(_app(settings), "GET", "/model-config")).json()

        assert [p["key"] for p in body["providers"]] == ["fallback"]


class TestProviderReload:
    """种子文件改了、库又不为空时的对齐入口（``POST /model-config/providers/reload``）。"""

    async def test_reload_aligns_the_directory_and_returns_the_snapshot(self, monkeypatch):
        # 「对齐」的结果就是库变成种子文件那份 —— 这里直接把它喂成库里的行
        _patch_dao(
            monkeypatch, {}, providers=[_provider_row(provider="fresh", display_name="新灌的")]
        )
        from ai_coding_harness.db import dao

        async def fake_models(db, *, provider=None):
            return []

        monkeypatch.setattr(dao, "list_provider_models", fake_models)

        async def fake_reload(settings: Settings, database: Any) -> int:
            settings.providers = (_provider(provider="fresh", display_name="新灌的"),)
            return 1

        monkeypatch.setattr(app_mod, "reload_model_providers", fake_reload)

        body = (await _call(_app(), "POST", "/model-config/providers/reload")).json()

        assert [item["key"] for item in body["providers"]] == ["fresh"]

    async def test_a_missing_seed_file_is_a_400(self, monkeypatch):
        _patch_dao(monkeypatch, {})

        async def boom(settings: Settings, database: Any) -> int:
            raise FileNotFoundError("种子文件读不到")

        monkeypatch.setattr(app_mod, "reload_model_providers", boom)

        resp = await _call(_app(), "POST", "/model-config/providers/reload")

        assert resp.status_code == 400 and "种子文件" in resp.json()["detail"]


def test_mask_key_never_leaks_short_values():
    assert route.mask_key("sk-abcdefghijk") == "sk-…hijk"


class TestTextOnlyFilter:
    """拉回来的清单只留文本模型（2026-10-14 用户口径）。

    OpenAI 兼容的 ``/models`` 只回名字、没有类型字段，所以只能按名字判 —— 这里把「该留 / 该滤」
    两边都钉住：**误杀文本模型**（``doubao-seed-*`` / ``qwen-vl-*`` / ``glm-4v``）比漏掉一个
    图像模型更难发现，也更烦人。
    """

    @pytest.mark.parametrize(
        "name",
        [
            "dall-e-3",
            "gpt-image-1",
            "gpt-image-2",
            "imagen-3",
            "gemini-3.1-flash-image",
            "gemini-nano-banana-2.1",
            "seedream-4",
            "seededit-3",
            "stable-diffusion-3.5",
            "flux-pro",
            "kolors-v2",
            "sd3.5-large",
            "doubao-seedance-2-5-260628",
            "sora-2",
            "veo-3",
            "kling-2.0",
            "hunyuan-video",
            "wanx2.1-t2v",
            "wan2.5-i2v",
            "tts-1",
            "gpt-4o-mini-tts",
            "gpt-4o-audio-preview",
            "gpt-4o-realtime-preview",
            "whisper-1",
            "paraformer-v2",
            "sensevoice-small",
            "cosyvoice-v2",
            "text-embedding-3-small",
            "bge-m3",
            "bge-reranker-v2-m3",
            "gte-large",
            "qwen3-embedding-8b",
            "rerank-2",
            "olm-ocr",
            "llama-guard-3-8b",
        ],
    )
    def test_non_text_models_are_dropped(self, name: str):
        assert route.is_text_model(name) is False

    @pytest.mark.parametrize(
        "name",
        [
            "deepseek-v4-pro",
            "deepseek-v4.1-flash",
            "gpt-5.6-luna",
            "gpt-5.4-mini",
            "gpt-4o",
            "doubao-seed-2-1-pro-260915",
            "doubao-seed-2-0-lite-260215",
            "qwen3.8-flash",
            "qwen-vl-max",
            "glm-5.3-flash",
            "glm-4v",
            "kimi-k2.7-code",
            "claude-sonnet-5",
            "gemini-3.6-flash",
            "grok-4.20-reasoning",
            "MiniMax-M3",
            "hy4-preview",
            "mimo-v2.5-pro",
            "step-5-preview",
            "gpt-6.1-sol",
            "自定义模型",
        ],
    )
    def test_text_models_survive(self, name: str):
        assert route.is_text_model(name) is True, "误杀文本模型比漏掉一个图像模型更难查"

    def test_parse_models_keeps_both_payload_shapes_and_drops_the_rest(self):
        payload = {
            "data": [
                {"id": "deepseek-v4-pro", "context_length": 1_048_576},
                {"id": "bge-m3"},
                "tts-1",
                "deepseek-v4.1-flash",
            ]
        }

        names, window = route.parse_models(payload)

        assert names == ["deepseek-v4-pro", "deepseek-v4.1-flash"], "两种形状都认，非文本滤掉"
        assert window == 1_048_576, "窗口从被留下的那条上取"

    def test_an_unknown_payload_is_just_empty(self):
        assert route.parse_models({"error": "nope"}) == ([], 0)
        assert route.parse_models("不是对象") == ([], 0)

    def test_the_qwen_native_shape_is_understood(self):
        """qwen 的 ``/api/v1/models`` 把自己那套埋在 ``output.models[].model_info`` 里
        （2026-10-15 用户实测）：名字与窗口都要**往下钻**才能拿到。"""
        payload = {
            "request_id": "x",
            "output": {
                "models": [
                    {
                        "model_info": {
                            "model_name": "qwen3.8-max",
                            "context_window": 1_048_576,
                        }
                    },
                    {"model_info": {"model_name": "text-embedding-v4"}},
                ]
            },
        }

        names, window = route.parse_models(payload)

        assert names == ["qwen3.8-max"], "非文本模型仍然滤掉"
        assert window == 1_048_576, "窗口埋在 model_info 里也要读到"
        assert route.parse_model_entries(payload) == [
            {"model": "qwen3.8-max", "model_type": "chat", "context_window": 1_048_576},
            {"model": "text-embedding-v4", "model_type": "embedding", "context_window": 0},
        ], "刷新清单要**全都要**（非文本的也落表，标好类型）"

    def test_a_string_window_is_accepted(self):
        """有的网关把窗口回成字符串 —— 认得出来就存，认不出来才是 0。"""
        names, window = route.parse_models(
            {"data": [{"id": "glm-5.3", "context_length": "1048576"}]}
        )

        assert names == ["glm-5.3"] and window == 1_048_576

    def test_the_models_url_joins_base_and_path(self):
        """清单地址两处都支持：拼在 base_url 后面、或直接写绝对地址（qwen）。"""
        assert (
            route._models_url("https://open.bigmodel.cn/api/paas/v4/", "/v1/models")
            == "https://open.bigmodel.cn/api/paas/v4/v1/models"
        ), "glm 的清单比默认多一层 /v1（2026-10-15 用户实测）"
        assert route._models_url("https://api.moonshot.cn/v1", "/models") == (
            "https://api.moonshot.cn/v1/models"
        )
        assert route._models_url(
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "https://dashscope.aliyuncs.com/api/v1/models",
        ) == "https://dashscope.aliyuncs.com/api/v1/models"

    def test_the_seed_carries_the_real_models_paths(self):
        """种子文件里那两条**实测出来的**路径别被后人改回去：
        glm 要 `/v1/models`、qwen 要绝对地址（清单不在兼容入口那个根下面）。"""
        metas = {meta.provider: meta for meta in config_mod.load_provider_seed()}

        assert metas["glm"].models_path == "/v1/models"
        assert metas["qwen"].models_path.startswith("https://")
    assert route.mask_key("short") == "*****"
    assert route.mask_key("") == "" and route.mask_key(None) == ""
