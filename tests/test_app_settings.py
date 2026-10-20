"""运行期配置（设置页「基本设置」那两组）：键表 / 类型转换 / 接口行为 / 热生效。

三条口径（用户拍过）：

1. **只有登记过的键能写** —— 定义只在 ``config.RUNTIME_SETTINGS`` 一处，认不出的键回 400；
2. **改完立刻生效**（不用重启）—— 值赋回 ``Settings`` 实例，读那些字段的地方一行都不用改；
3. **库里没写过的键用 `.env` / 代码默认值** —— 界面上就是 ``source=default``。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from ai_coding_harness.api.routes import app_settings as route
from ai_coding_harness.config import (
    RUNTIME_SETTING_BY_KEY,
    RUNTIME_SETTINGS,
    Settings,
    apply_runtime_settings,
    coerce_runtime_setting,
)


def _settings(**over: Any) -> Settings:
    """不读 .env：断言不该被本机配置影响。"""
    return Settings(_env_file=None, **over)


# -- 一、键表 ------------------------------------------------------------------


class TestTheKeyTable:
    def test_every_key_lands_on_a_real_field(self):
        settings = _settings()

        for item in RUNTIME_SETTINGS:
            assert hasattr(settings, item.field), f"{item.key} 指向的字段不存在：{item.field}"

    def test_the_keys_are_unique_and_grouped_the_way_the_page_shows_them(self):
        keys = [item.key for item in RUNTIME_SETTINGS]

        assert len(set(keys)) == len(keys), "键是表主键"
        assert {item.group for item in RUNTIME_SETTINGS} == {
            "代码缓存配置",
            "代码平台",
            "上下文窗口配置",
            "上下文压缩配置",
            "意图识别",
        }

    def test_intent_clarify_threshold_defaults_to_055(self):
        assert _settings().intent_clarify_confidence == 0.55

    def test_only_the_four_kinds_are_used(self):
        assert {item.kind for item in RUNTIME_SETTINGS} <= {"str", "int", "float", "secret"}

    def test_the_platform_tokens_are_secrets(self):
        for key in ("gitee_access_token", "github_access_token"):
            assert RUNTIME_SETTING_BY_KEY[key].kind == "secret", key


# -- 二、类型转换与落值 --------------------------------------------------------


class TestCoercion:
    def test_strings_are_stripped_and_empty_is_legal(self):
        """空串对字符串是合法值：默认分支留空＝按远程探测。"""
        item = RUNTIME_SETTING_BY_KEY["gitee_base_branch"]

        assert coerce_runtime_setting(item, "  dev  ") == "dev"
        assert coerce_runtime_setting(item, "") == ""

    def test_numbers_are_cast_and_bad_values_raise(self):
        assert coerce_runtime_setting(RUNTIME_SETTING_BY_KEY["context_warn_ratio"], "0.75") == 0.75
        assert coerce_runtime_setting(RUNTIME_SETTING_BY_KEY["context_warn_ratio"], 0.7) == 0.7
        with pytest.raises(ValueError):
            coerce_runtime_setting(RUNTIME_SETTING_BY_KEY["context_warn_ratio"], "不是数字")


class TestApplying:
    def test_values_land_on_the_settings_instance(self):
        settings = _settings()

        apply_runtime_settings(
            settings,
            {"context_alert_ratio": 0.9, "repo_view_dir": "/x", "context_warn_ratio": 0.5},
        )

        assert settings.context_alert_ratio == 0.9
        assert settings.repo_view_dir == "/x"
        assert settings.context_warn_ratio == 0.5

    def test_a_broken_row_does_not_take_the_others_down(self):
        settings = _settings()

        apply_runtime_settings(
            settings,
            {"nope": 1, "context_alert_ratio": "坏值", "repo_view_dir": "/keep"},
        )

        assert settings.repo_view_dir == "/keep"
        assert settings.context_alert_ratio == _settings().context_alert_ratio, "坏值跳过，保留原值"


# -- 三、接口 ------------------------------------------------------------------


class _FakeDb:
    def __init__(self, store: dict[str, Any]) -> None:
        self.store = store

    @asynccontextmanager
    async def session(self):
        yield None


def _patch_dao(monkeypatch, store: dict[str, Any]) -> None:
    """把 app_settings 表换成一个内存 dict。"""
    from ai_coding_harness.db import dao

    async def list_all(db):
        return dict(store)

    async def upsert(db, *, key, value):
        store[key] = value

    monkeypatch.setattr(dao, "list_app_settings", list_all)
    monkeypatch.setattr(dao, "upsert_app_setting", upsert)


def _app(settings: Settings, store: dict[str, Any]) -> Any:
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
    async def test_get_lists_every_key_and_says_where_the_value_comes_from(self, monkeypatch):
        store: dict[str, Any] = {}
        _patch_dao(monkeypatch, store)

        body = (await _call(_app(_settings(), store), "GET", "/app-settings")).json()

        by_key = {item["key"]: item for item in body["items"]}
        assert set(by_key) == {item.key for item in RUNTIME_SETTINGS}
        assert by_key["context_warn_ratio"]["source"] == "default"
        assert by_key["context_warn_ratio"]["group"] == "上下文窗口配置"
        assert by_key["repo_view_dir"]["kind"] == "str"
        assert [item["group"] for item in body["items"]].count("代码缓存配置") == 3
        assert [item["group"] for item in body["items"]].count("上下文窗口配置") == 2, (
            "上下文那一组**只留两条阈值**（窗口本身按模型查，2026-10-14 用户口径）"
        )

    async def test_put_only_writes_what_was_given_and_takes_effect_at_once(self, monkeypatch):
        """「改完立刻生效」：同一个进程里 settings 当场就变了，不用重启。"""
        store: dict[str, Any] = {}
        settings = _settings()
        _patch_dao(monkeypatch, store)

        body = (
            await _call(
                _app(settings, store),
                "PUT",
                "/app-settings",
                json={"items": {"context_warn_ratio": 0.75}},
            )
        ).json()

        assert store == {"context_warn_ratio": 0.75}
        assert settings.context_warn_ratio == 0.75, "当场生效，不是等重启"
        by_key = {item["key"]: item for item in body["items"]}
        assert by_key["context_warn_ratio"]["value"] == 0.75
        assert by_key["context_warn_ratio"]["source"] == "db"
        assert by_key["context_alert_ratio"]["source"] == "default", "没传的键不动"

    async def test_a_stored_value_is_applied_on_read(self, monkeypatch):
        """直接改库之后打开这一页也是新的：GET 会把库里的值重新落一遍。"""
        store: dict[str, Any] = {"repo_view_dir": "/tmp/别处"}
        settings = _settings()
        _patch_dao(monkeypatch, store)

        await _call(_app(settings, store), "GET", "/app-settings")

        assert settings.repo_view_dir == "/tmp/别处"

    async def test_an_unknown_key_or_a_bad_value_is_a_400_and_writes_nothing(self, monkeypatch):
        store: dict[str, Any] = {}
        _patch_dao(monkeypatch, store)
        app = _app(_settings(), store)

        unknown = await _call(app, "PUT", "/app-settings", json={"items": {"nope": 1}})
        bad = await _call(
            app, "PUT", "/app-settings", json={"items": {"context_warn_ratio": "坏值"}}
        )

        assert unknown.status_code == 400 and bad.status_code == 400
        assert store == {}, "坏请求不落半格"

    async def test_an_empty_string_clears_the_override(self, monkeypatch):
        """默认分支那类：填空串＝回到「按远程探测」（这也是合法值，不能当没填）。"""
        store: dict[str, Any] = {"gitee_base_branch": "dev"}
        settings = _settings(gitee_base_branch="dev")
        _patch_dao(monkeypatch, store)

        body = (
            await _call(
                _app(settings, store),
                "PUT",
                "/app-settings",
                json={"items": {"gitee_base_branch": ""}},
            )
        ).json()

        assert store["gitee_base_branch"] == ""
        assert settings.gitee_base_branch == ""
        assert {i["key"]: i for i in body["items"]}["gitee_base_branch"]["source"] == "db"


class TestSecrets:
    """令牌那两格（2026-10-20 用户口径）：打开设置页回掩码，保存之后不再回显。"""

    def _app_with_token(self, monkeypatch, token: str = "ghp_0123456789abcdef"):
        store: dict[str, Any] = {}
        settings = _settings(github_access_token=token)
        _patch_dao(monkeypatch, store)
        return _app(settings, store), settings, store

    async def test_get_echoes_a_mask_not_the_value(self, monkeypatch):
        app = self._app_with_token(monkeypatch)[0]

        body = (await _call(app, "GET", "/app-settings")).json()

        item = {i["key"]: i for i in body["items"]}["github_access_token"]
        assert item["kind"] == "secret"
        assert item["value"] == "ghp…cdef"
        assert item["has_value"] is True
        assert "0123456789abcdef" not in str(body), "明文一个字节都不该出现在响应里"

    async def test_put_does_not_echo_the_token(self, monkeypatch):
        app, settings_, store = self._app_with_token(monkeypatch, token="")

        body = (
            await _call(
                app,
                "PUT",
                "/app-settings",
                json={"items": {"github_access_token": "ghp_0123456789abcdef"}},
            )
        ).json()

        item = {i["key"]: i for i in body["items"]}["github_access_token"]
        assert settings_.github_access_token == "ghp_0123456789abcdef", "当场生效"
        assert store["github_access_token"] == "ghp_0123456789abcdef"
        assert item["value"] == "", "保存之后不再回显"
        assert item["has_value"] is True, "但要能说「配过了」"

    async def test_a_mask_sent_back_unchanged_is_not_a_write(self, monkeypatch):
        """界面上那串掩码只是提示：原样提交回来不等于把令牌改成了掩码本身。"""
        token = "ghp_0123456789abcdef"
        app, settings_, store = self._app_with_token(monkeypatch, token=token)

        await _call(app, "PUT", "/app-settings", json={"items": {"github_access_token": "ghp…cdef"}})

        assert settings_.github_access_token == token
        assert store == {}

    async def test_an_empty_value_clears_it(self, monkeypatch):
        """把掩码删空再保存＝清除（这是界面上唯一能触发清空的动作）。"""
        app, settings_, store = self._app_with_token(monkeypatch)

        await _call(app, "PUT", "/app-settings", json={"items": {"github_access_token": ""}})

        assert settings_.github_access_token == ""
        assert store["github_access_token"] == ""


# -- 四、这几格真的被读到（不然「能配」是假的）--------------------------------


class TestTheSettingsAreActuallyConsumed:
    def test_the_window_fallback_follows_the_setting(self):
        from ai_coding_harness.agent import llm as llm_mod

        # plan 那一行没配（也没有统一模型）＝ 说不出窗口，这才轮到兜底那一格
        settings = _settings()
        settings.fallback_context_window = 4096

        assert llm_mod.resolve_window(settings, "plan") == 4096

    def test_the_zero_fallback_goes_back_to_the_conservative_constant(self):
        """兜底被填成 0：窗口 0 会让压缩永不触发，所以退回常量而不是照用。"""
        from ai_coding_harness.agent import llm as llm_mod

        settings = _settings()
        settings.fallback_context_window = 0

        assert llm_mod.resolve_window(settings, "plan") == 128_000

    async def test_the_code_panel_opens_the_configured_branch(self):
        from ai_coding_harness.core.codeplatform import Platform
        from ai_coding_harness.infra.repo_cache import RepoSpec, default_branch

        spec = RepoSpec(owner="o", repo="r", url="https://gitee.com/o/r.git", platform=Platform.GITEE)

        # 配了就以它为准（不跑 git：配错会打开一个不存在的分支，界面上写明了）
        assert await default_branch(_settings(gitee_base_branch="dev"), spec) == "dev"


class TestTheAgentIsNotCached:
    """用户口径：**每次都创建一个新的 agent，不缓存** —— 这是「配置热生效」的另一半。"""

    def test_the_runtime_no_longer_holds_agents(self):
        from ai_coding_harness.session.manager import SessionRuntime

        assert "agents" not in SessionRuntime.__dataclass_fields__

    def test_two_calls_build_two_agents(self, monkeypatch):
        from ai_coding_harness.session import manager as manager_mod

        for name, value in (
            ("build_main_tools", lambda *a, **kw: []),
            ("build_subagent_tools", lambda *a, **kw: []),
            ("build_subagents", lambda *a, **kw: []),
            ("policy_for", lambda *a, **kw: None),
            ("build_memory_middleware", lambda *a, **kw: None),
            # 压缩中间件要拿**真模型**才建得起来（上游 __init__ 第一件事是 model.with_retry()）；
            # 这一条测的是「不缓存」，构造细节归 tests/test_summarization.py
            ("build_summarization", lambda *a, **kw: None),
            ("build_main_prompt", lambda *a, **kw: "p"),
            ("build_agent", lambda **kw: object()),
        ):
            monkeypatch.setattr(manager_mod, name, value)

        manager = manager_mod.SessionManager(
            settings=_settings(),
            model_for=lambda role: None,
            fallback_for=lambda role: None,
            web_search_tool=None,
            store=None,
            checkpointer=None,
            database=None,
        )
        runtime = SimpleNamespace(sandbox=None, backend=None)
        session = SimpleNamespace(
            public_id="s-1",
            db_id=1,
            repo=SimpleNamespace(owner="o", repo="r"),
            repo_url="https://gitee.com/o/r.git",
        )
        profile = SimpleNamespace(form="qa", subagents=())

        first = manager.get_agent(runtime, session, profile)
        second = manager.get_agent(runtime, session, profile)

        assert first is not second, "每轮现建：缓存住就换不动模型与窗口"
