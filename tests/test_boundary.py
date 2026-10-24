"""按智能体的边界：策略表、目录范围判定、两层落地。

这一层要挡的不是「危险命令」，而是**每个智能体自己的活动范围** —— 出方案的智能体不该
写源码、审查的智能体不该动工程目录。所以用例分三组：

1. **策略表**（``safety/policy.py`` 的纯函数）：表格本身、目录前缀的边界、命令里的写目标。
   这些用例是改表时的护栏 —— 表格是执行口径，不是文档，改错一行就是放开一个角色。
2. **中间件两层**（``agent/middleware/boundary.py``）：``awrap_model_call`` 把工具从模型
   眼前撤掉、``awrap_tool_call`` 在真执行前拦下。
3. **全局底线**（``safety/permissions.py`` 接在 ``execute`` 上）：凭据、提权、破坏性 git。

真实语义是「反滑不反敌」：拦模型手滑与越界意图，不是铁了心绕的对手。所以这里只钉**已知
能拦住**的形状，不假装覆盖管道与间接执行（那两条记在 ``policy.py`` 开头的缺口清单里）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool as make_tool
from langgraph.prebuilt.tool_node import ToolCallRequest

from ai_coding_harness.agent.middleware import BoundaryMiddleware
from ai_coding_harness.agent.profiles import (
    ORCHESTRATE_FORM,
    PLAN_FORM,
    QA_FORM,
)
from ai_coding_harness.config import Settings
from ai_coding_harness.safety.permissions import CommandSafety
from ai_coding_harness.safety.policy import (
    AgentPolicy,
    command_write_targets,
    policy_for,
    write_targets,
)

# -- 策略表 -----------------------------------------------------------------


class TestPolicyTable:
    """表格就是执行口径：这一组断言改了就得有人解释为什么。"""

    @pytest.mark.parametrize(
        ("name", "roots", "hidden"),
        [
            # 主控多智能体形态：写码一律派 coder，主控自己不给写文件的工具
            (
                ORCHESTRATE_FORM,
                (
                    "/ai_coding/project",
                    "/ai_coding/tmp",
                    "/ai_coding/review",
                ),
                ("edit_file", "write_file"),
            ),
            # 出方案：与子智能体 planner 同形 —— 方案 storePlan 落库、结论进回复，一个文件都不落
            (PLAN_FORM, (), ("edit_file", "write_file")),
            # qa 合并原 analysis：结论进回复，只留 tmp 放取证台账。
            (QA_FORM, ("/ai_coding/tmp",), ("write_todos", "write_file", "edit_file")),
            # 子智能体 planner 不写文件，写工具一并撤掉
            ("planner", (), ("edit_file", "write_file")),
            ("coder", ("/ai_coding/project", "/ai_coding/tmp"), ()),
            ("reviewer", ("/ai_coding/review", "/ai_coding/tmp"), ()),
        ],
    )
    def test_each_name_resolves_to_its_own_scope(self, name, roots, hidden):
        policy = policy_for(name)
        assert policy.name == name
        assert policy.write_roots == roots
        assert policy.hidden_tools == frozenset(hidden)

    def test_unknown_name_fails_closed(self):
        """新加角色忘了登记时，代价是写不了文件、在工具返回里看到理由。

        反过来（默认放开）是默默给了一个新角色全目录写权限 —— 两种错的代价差得太远。
        """
        policy = policy_for("还没登记的角色")
        assert policy.write_roots == ()
        assert policy.hidden_tools == frozenset()

    def test_roots_follow_settings(self):
        """路径可配，不写死 —— 配置改了策略表也得跟着改，否则判定打在空气上。"""
        settings = Settings(sandbox_project_dir="/srv/repo")
        assert policy_for("coder", settings).write_roots == ("/srv/repo", "/ai_coding/tmp")

    def test_blank_setting_falls_back_to_default(self):
        """配置项被显式设成空串时不能把根目录判成 ``""`` —— 那会让所有路径都「在范围内」。"""
        settings = Settings(sandbox_project_dir="")
        assert policy_for("coder", settings).write_roots[0] == "/ai_coding/project"


class TestDirectoryRange:
    """目录判定：够用就行，但前缀这类经典错必须钉死。"""

    def test_sibling_prefix_is_not_inside(self):
        """``/ai_coding/projectx`` 不在 ``/ai_coding/project`` 里面 —— 少一个斜杠就是越权。"""
        policy = policy_for("coder")
        assert policy.check_tool("write_file", {"file_path": "/ai_coding/projectx/a.py"})
        assert policy.check_tool("write_file", {"file_path": "/ai_coding/project/a.py"}) is None

    def test_the_directory_itself_is_writable(self):
        policy = policy_for("coder")
        assert policy.check_tool("write_file", {"file_path": "/ai_coding/project"}) is None

    def test_dotdot_is_normalized_before_judging(self):
        """``/ai_coding/project/../policies/x`` 归一之后落在 policies 里，不能放过。"""
        reason = policy_for("coder").check_tool(
            "write_file", {"file_path": "/ai_coding/project/../policies/x.yaml"}
        )
        assert reason is not None and "/ai_coding/policies/x.yaml" in reason

    def test_out_of_range_write_explains_where_it_can_write(self):
        """理由要能照着改：说清楚谁能写哪儿、目标不在其中。"""
        reason = policy_for("coder").check_tool(
            "edit_file", {"file_path": "/ai_coding/output/report.md"}
        )
        assert reason is not None
        assert "/ai_coding/project" in reason and "/ai_coding/output/report.md" in reason

    def test_role_without_roots_is_refused_explicitly(self):
        """没有可写目录 ≠ 随便写：措辞得把「这个角色不落文件」说透。"""
        reason = policy_for("没登记的角色").check_tool(
            "write_file", {"file_path": "/ai_coding/output/a.md"}
        )
        assert reason is not None and "不写文件" in reason

    def test_hidden_tool_wins_over_directory_check(self):
        """planner 的 write_file 是被撤掉的，不该走到目录那一步再报「不在范围内」。"""
        reason = policy_for("planner").check_tool(
            "write_file", {"file_path": "/ai_coding/output/a.md"}
        )
        assert reason is not None and "write_file" in reason

    def test_readonly_tools_have_no_targets(self):
        assert write_targets("read_file", {"file_path": "/ai_coding/project/a.py"}) is None
        assert write_targets("ls", {"path": "/ai_coding"}) is None
        assert write_targets("task", {"description": "随便"}) is None

    def test_write_tools_expose_their_path_argument(self):
        assert write_targets("edit_file", {"file_path": "/a/b.py"}) == ["/a/b.py"]
        # 空串不是路径，也不构成一次写：漏给路径时工具自己就会报参数错，这里不必再判
        assert write_targets("write_file", {"file_path": ""}) is None


class TestVerificationCommands:
    """2026-10-20 用户口径：qa 允许跑测试与构建，但不改任务代码。

    装依赖允许，但只能落在 ``/tmp`` 的仓库副本里 —— 项目目录里不留 node_modules /
    lockfile 改动，「清理」是结构性的，不靠模型自觉。
    """

    @pytest.mark.parametrize("name", [QA_FORM])
    def test_tests_and_builds_are_allowed(self, name):
        policy = policy_for(name)
        for command in ("pytest -q", "npm test", "npm run build", "mvn test", "python -c 'print(1)'"):
            assert policy.check_tool("execute", {"command": command}) is None, command

    @pytest.mark.parametrize("name", [QA_FORM])
    def test_installing_is_only_allowed_inside_tmp(self, name):
        policy = policy_for(name)
        assert policy.check_tool("execute", {"command": "cd /tmp/verify && npm ci"}) is None
        assert policy.check_tool("execute", {"command": "cd /tmp/verify && pip install -r req.txt"}) is None
        # 没 cd：不知道落在哪，按项目目录算
        assert policy.check_tool("execute", {"command": "npm ci"}) is not None
        # 直接在仓库里装：留在项目里的依赖没人清理
        assert (
            policy.check_tool(
                "execute", {"command": "cd /ai_coding/project/repo && npm install"}
            )
            is not None
        )

    @pytest.mark.parametrize("name", [QA_FORM])
    def test_git_writes_and_in_place_fixes_are_blocked(self, name):
        policy = policy_for(name)
        for command in (
            "git add -A",
            "git commit -m x",
            "git push origin main",
            "git checkout -b x",
            "git merge main",
            "ruff check --fix .",
            "npm run lint -- --fix",
            "black .",
        ):
            assert policy.check_tool("execute", {"command": command}) is not None, command

    def test_read_only_commands_still_pass(self):
        policy = policy_for(QA_FORM)
        for command in ("git log --oneline -5", "git diff", "black --check .", "ls -la"):
            assert policy.check_tool("execute", {"command": command}) is None, command

    def test_copying_the_repo_to_tmp_is_not_a_write_to_the_source(self):
        """``cp`` 的源是只读的：只有目标是写。否则「先拷到 /tmp 再跑」会被自己挡住。"""
        assert (
            policy_for(QA_FORM).check_tool(
                "execute", {"command": "cp -a /ai_coding/project/repo /tmp/verify"}
            )
            is None
        )

    def test_the_coder_keeps_its_write_powers(self):
        policy = policy_for("coder")
        assert policy.check_tool("execute", {"command": "git commit -m x"}) is None
        assert policy.check_tool("execute", {"command": "npm install"}) is None


class TestProtectedPaths:
    """凭据与平台资产：跟角色无关，谁都不许碰。"""

    @pytest.mark.parametrize(
        "name",
        [
            ORCHESTRATE_FORM,
            PLAN_FORM,
            QA_FORM,
            "coder",
            "reviewer",
        ],
    )
    def test_nobody_writes_credentials(self, name):
        reason = policy_for(name).check_tool(
            "write_file", {"file_path": "/ai_coding/.secrets/token"}
        )
        assert reason is not None

    @pytest.mark.parametrize(
        "name",
        [
            ORCHESTRATE_FORM,
            PLAN_FORM,
            QA_FORM,
            "coder",
            "reviewer",
        ],
    )
    def test_nobody_writes_platform_assets(self, name):
        """skills / policies 是平台按仓库内容铺进去的 —— 让它改等于让它改自己的规则。"""
        for path in ("/ai_coding/skills/x/SKILL.md", "/ai_coding/policies/p.yaml"):
            assert policy_for(name).check_tool("write_file", {"file_path": path})

    def test_file_tools_cannot_read_credentials(self):
        """命令层拦的是 ``execute``；``read_file`` 这类走文件接口，得单独拦一道。"""
        policy = policy_for(ORCHESTRATE_FORM)
        for name, args in (
            ("read_file", {"file_path": "/ai_coding/.secrets/gitee_token"}),
            ("ls", {"path": "/ai_coding/.secrets"}),
            ("glob", {"path": "/ai_coding/.secrets"}),
            ("grep", {"path": "/ai_coding/.secrets"}),
            ("ls", {"path": "/usr/local/sbin/git-askpass"}),
        ):
            assert policy.check_tool(name, args) is not None, f"{name} {args} 没被拦"

    def test_read_scope_is_narrow_on_purpose(self):
        """只拦确定的凭据落点，不做通用读范围 —— 拦宽了会误伤一堆正当命令。"""
        policy = policy_for(ORCHESTRATE_FORM)
        for args in (
            {"file_path": "/ai_coding/project/README.md"},
            {"file_path": "/usr/local/sbin/别的脚本"},
            {"path": "/ai_coding"},
        ):
            assert policy.check_tool("read_file", args) is None
            assert policy.check_tool("ls", args) is None


class TestCommandTargets:
    """命令里能看出落点的那部分。看不出的一律放过（缺口清单在 ``policy.py`` 开头）。"""

    def test_write_verb_arguments(self):
        assert command_write_targets("mkdir -p /ai_coding/output/x") == ["/ai_coding/output/x"]

    def test_redirect_targets(self):
        assert command_write_targets("echo hi > /ai_coding/output/a.md") == [
            "/ai_coding/output/a.md"
        ]
        assert command_write_targets("cat a >> /ai_coding/output/b.md") == [
            "/ai_coding/output/b.md"
        ]

    def test_fd_redirect_is_a_write_and_stderr_merge_is_not(self):
        """``2> err.log`` 是实打实的写；``2>&1`` / ``>&2`` 的目标是描述符，不是文件。"""
        assert command_write_targets("python x.py 2> /ai_coding/output/err.log") == [
            "/ai_coding/output/err.log"
        ]
        assert command_write_targets("python x.py &> /ai_coding/output/all.log") == [
            "/ai_coding/output/all.log"
        ]
        assert command_write_targets("python x.py 2>&1") == []
        assert command_write_targets("python x.py >&2") == []

    def test_quoted_redirect_is_not_a_redirect(self):
        """``echo "x > /a"`` 写的是标准输出，不是那个文件。"""
        assert command_write_targets('echo "x > /ai_coding/output/a.md"') == []

    def test_quoted_pipe_is_not_a_segment_split(self):
        """``sed -i 's|/a|/b|' file`` 里的 ``|`` 是 sed 的分隔符，不是管道。

        按字符硬切会把这条切成三段、丢掉写目标 —— 这是实测过的坏形状。
        """
        targets = command_write_targets("sed -i 's|/a|/b|' /ai_coding/project/a.py")
        assert targets == ["/ai_coding/project/a.py"]

    def test_sed_without_dash_i_is_readonly(self):
        assert command_write_targets("sed 's/a/b/' /ai_coding/project/a.py") == []

    def test_heredoc_body_is_not_a_command(self):
        """正文是要写进文件的内容，里面出现 ``> /var/log/x`` 是常态，不能当成越界。"""
        command = "cat > /ai_coding/output/a.sh <<'EOF'\nlog > /var/log/app.log\nEOF\n"
        assert command_write_targets(command) == ["/ai_coding/output/a.sh"]

    def test_relative_target_after_an_absolute_cd(self):
        """判得出落点就判：``cd`` 到绝对路径后的相对目标展开成绝对值。"""
        assert command_write_targets("cd /ai_coding/project && rm -rf src/build") == [
            "/ai_coding/project/src/build"
        ]

    def test_unresolvable_relative_target_is_allowed(self):
        """沙箱当前目录不在我们手里，硬判会误伤大量正当命令 —— 这是自觉的取舍。"""
        assert command_write_targets("rm -rf src/build") == []
        assert command_write_targets("cd ~ && rm -rf x") == []
        assert command_write_targets("cd $DIR && rm -rf x") == []

    def test_glob_reduces_to_its_literal_prefix(self):
        assert command_write_targets("rm -rf /ai_coding/project/build/*") == [
            "/ai_coding/project/build"
        ]
        assert command_write_targets("rm -rf /ai_coding/project/*") == ["/ai_coding/project"]

    def test_prefix_words_are_skipped(self):
        assert command_write_targets("nohup mkdir /ai_coding/output/x") == [
            "/ai_coding/output/x"
        ]
        assert command_write_targets("F=1 mkdir /ai_coding/output/x") == [
            "/ai_coding/output/x"
        ]

    def test_dd_only_uses_of(self):
        assert command_write_targets("dd if=/dev/zero of=/ai_coding/output/img bs=1M") == [
            "/ai_coding/output/img"
        ]

    def test_flags_and_numbers_are_not_paths(self):
        assert command_write_targets("chmod -R 755 /ai_coding/output/x") == [
            "/ai_coding/output/x"
        ]


class TestCommandScope:
    """命令级的越界：工具名没错，是命令往哪儿写。"""

    def test_in_range_command_passes(self):
        assert (
            policy_for("coder").check_tool(
                "execute", {"command": "mkdir -p /ai_coding/project/src"}
            )
            is None
        )

    def test_out_of_range_command_is_refused(self):
        reason = policy_for("coder").check_tool(
            "execute", {"command": "touch /ai_coding/output/a.md"}
        )
        assert reason is not None and "/ai_coding/output/a.md" in reason

    def test_move_writes_to_the_second_argument_too(self):
        """``mv`` 会写到第二个参数指向的地方 —— 只看第一个就漏了。"""
        reason = policy_for("coder").check_tool(
            "execute", {"command": "mv /ai_coding/project/a.py /ai_coding/output/a.py"}
        )
        assert reason is not None

    def test_tmp_and_dev_are_exempt(self):
        """``/tmp`` 随沙箱销毁、没有读方，逐角色计较没意义；``/dev/null`` 同理。"""
        policy = policy_for(QA_FORM)
        assert policy.check_tool("execute", {"command": "touch /tmp/scratch"}) is None
        assert policy.check_tool("execute", {"command": "echo x > /dev/null"}) is None

    def test_exemption_is_not_a_prefix_match(self):
        """``/tmpfoo`` 不是 ``/tmp`` —— 前缀判定少一个斜杠就是越权。"""
        reason = policy_for(QA_FORM).check_tool("execute", {"command": "touch /tmpfoo/x"})
        assert reason is not None

    def test_role_without_roots_cannot_write_via_command(self):
        reason = policy_for("planner").check_tool(
            "execute", {"command": "touch /ai_coding/output/a.md"}
        )
        assert reason is not None and "不写文件" in reason

    def test_platform_assets_are_refused_in_commands(self):
        reason = policy_for(ORCHESTRATE_FORM).check_tool(
            "execute", {"command": "echo bad > /ai_coding/policies/p.yaml"}
        )
        assert reason is not None

    def test_readonly_commands_pass(self):
        for command in (
            "ls -la /ai_coding/project",
            "git log --oneline -5",
            "cat /ai_coding/output/a.md",
        ):
            assert policy_for(QA_FORM).check_tool("execute", {"command": command}) is None


# -- 中间件两层 -------------------------------------------------------------


def _model_request(tools: list[Any]) -> ModelRequest:
    return ModelRequest(
        model=None,  # type: ignore[arg-type]
        messages=[],
        system_message=None,
        tool_choice=None,
        tools=tools,
        response_format=None,
        state=None,
        runtime=None,
    )


async def _through_model_boundary(
    middleware: BoundaryMiddleware, tools: list[Any]
) -> ModelRequest:
    """跑一遍 ``awrap_model_call``，把 handler 收到的那份 request 还回来。"""
    seen: list[ModelRequest] = []

    async def handler(request: ModelRequest) -> Any:
        seen.append(request)
        return request

    await middleware.awrap_model_call(_model_request(tools), handler)
    return seen[0]


def _tool_call(name: str, args: Mapping[str, Any], call_id: Any = "call-1") -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": dict(args), "id": call_id},
        tool=None,
        state=None,
        runtime=None,
    )


async def _through_tool_boundary(
    middleware: BoundaryMiddleware, name: str, args: Mapping[str, Any] | None = None
) -> Any:
    sentinel = ToolMessage(content="真跑了", name=name, tool_call_id="call-1")

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return sentinel

    return await middleware.awrap_tool_call(_tool_call(name, args or {}), handler)


class TestHideTools:
    """看不见 > 提示词劝阻 —— 与 ``profiles.py`` 开头那段同一个取舍。"""

    async def test_hidden_tools_are_dropped_from_the_request(self):
        request = await _through_model_boundary(
            BoundaryMiddleware(policy_for("planner")),
            [{"name": "write_file"}, {"name": "read_file"}, {"name": "storePlan"}],
        )
        assert [t["name"] for t in request.tools] == ["read_file", "storePlan"]

    async def test_both_tool_objects_and_raw_dicts_are_recognized(self):
        """``request.tools`` 里两种形态都可能出现 —— 只认一种就有漏网的。"""
        def _write() -> str:
            return "x"

        real = make_tool("write_file", description="写文件")(_write)
        request = await _through_model_boundary(
            BoundaryMiddleware(policy_for("planner")),
            [real, {"name": "edit_file"}, {"name": "read_file"}],
        )
        kept = [getattr(t, "name", None) or t.get("name") for t in request.tools]
        assert kept == ["read_file"]

    async def test_request_is_untouched_when_nothing_is_hidden(self):
        """没命中就别造新对象：状态无谓更新会连带下游一堆重算。"""
        original = _model_request([{"name": "execute"}])
        seen: list[ModelRequest] = []

        async def handler(request: ModelRequest) -> Any:
            seen.append(request)
            return request

        await BoundaryMiddleware(policy_for(ORCHESTRATE_FORM)).awrap_model_call(
            original, handler
        )
        assert seen[0] is original


class TestBlockCalls:
    """第二层：历史里带回来的、模型凭记忆硬写的、参数越界的，都只有执行前拦得住。"""

    async def test_out_of_range_call_becomes_an_error_message(self):
        """返回错误 ``ToolMessage`` 而不是抛异常：模型看得到理由、能换做法，图也不塌。"""
        out = await _through_tool_boundary(
            BoundaryMiddleware(policy_for("coder")),
            "write_file",
            {"file_path": "/ai_coding/output/a.md"},
        )
        assert isinstance(out, ToolMessage)
        assert out.status == "error"
        assert out.tool_call_id == "call-1", "配不上 id 就还是个孤儿"
        assert out.name == "write_file"
        assert "未执行" in out.content

    async def test_hidden_tool_is_blocked_even_when_called(self):
        out = await _through_tool_boundary(
            BoundaryMiddleware(policy_for("planner")),
            "write_file",
            {"file_path": "/ai_coding/output/a.md"},
        )
        assert isinstance(out, ToolMessage) and out.status == "error"

    async def test_allowed_call_passes_through(self):
        """没命中就别碰返回值 —— 换一个对象会让下游认不出这是同一个结果。"""
        sentinel = ToolMessage(content="真跑了", name="read_file", tool_call_id="call-1")

        async def handler(_request: ToolCallRequest) -> ToolMessage:
            return sentinel

        out = await BoundaryMiddleware(policy_for("coder")).awrap_tool_call(
            _tool_call("read_file", {"file_path": "/ai_coding/project/a.py"}), handler
        )
        assert out is sentinel

    async def test_non_mapping_args_do_not_crash(self):
        request = ToolCallRequest(
            tool_call={"name": "read_file", "args": None, "id": "call-1"},
            tool=None,
            state=None,
            runtime=None,
        )

        async def handler(_request: ToolCallRequest) -> ToolMessage:
            return ToolMessage(content="ok", name="read_file", tool_call_id="call-1")

        out = await BoundaryMiddleware(policy_for("coder")).awrap_tool_call(request, handler)
        assert isinstance(out, ToolMessage) and out.status != "error"

    async def test_call_without_an_id_still_returns(self):
        """``ToolMessage.tool_call_id`` 是必填 ``str``，写成 ``None`` 会当场校验失败。"""
        request = ToolCallRequest(
            tool_call={"name": "write_file", "args": {"file_path": "/etc/passwd"}, "id": None},
            tool=None,
            state=None,
            runtime=None,
        )

        async def handler(_request: ToolCallRequest) -> ToolMessage:
            raise AssertionError("越界调用不该跑到工具里去")

        out = await BoundaryMiddleware(policy_for("coder")).awrap_tool_call(request, handler)
        assert isinstance(out, ToolMessage) and out.tool_call_id == ""


class TestGlobalFloor:
    """全局底线接在 ``execute`` 上：与角色无关的那几条。"""

    @pytest.mark.parametrize(
        "command",
        [
            "cat /ai_coding/.secrets/gitee_token",
            "sudo rm -rf /ai_coding/tmp/x",
            "git push --force origin main",
            "git reset --hard HEAD~1",
            "git clean -fd",
        ],
    )
    async def test_floor_rules_are_enforced_at_the_middleware(self, command):
        """最宽的那个形态（可写 project/tmp/review）也拦得住 —— 与角色无关。"""
        middleware = BoundaryMiddleware(policy_for(ORCHESTRATE_FORM), CommandSafety())
        out = await _through_tool_boundary(middleware, "execute", {"command": command})
        assert isinstance(out, ToolMessage) and out.status == "error"

    async def test_legitimate_command_passes(self):
        middleware = BoundaryMiddleware(policy_for(ORCHESTRATE_FORM), CommandSafety())
        out = await _through_tool_boundary(
            middleware, "execute", {"command": "git status --short"}
        )
        assert isinstance(out, ToolMessage) and out.status != "error"

    async def test_command_check_is_optional(self):
        """``safety`` 可以不传（装配点未必都给）—— 那时命令只看落点在不在范围内。"""
        out = await _through_tool_boundary(
            BoundaryMiddleware(policy_for(ORCHESTRATE_FORM)),
            "execute",
            {"command": "sudo ls"},
        )
        assert isinstance(out, ToolMessage) and out.status != "error"

    async def test_non_execute_tools_skip_the_command_check(self):
        """``CommandSafety`` 只对 ``execute`` 生效：别的工具没有命令文本。"""
        middleware = BoundaryMiddleware(policy_for(QA_FORM), CommandSafety())
        out = await _through_tool_boundary(
            middleware, "read_file", {"file_path": "/ai_coding/output/a.md"}
        )
        assert isinstance(out, ToolMessage) and out.status != "error"


class TestPolicyObject:
    """策略对象本身：每个角色各要一份自己的，所以它必须不可变。"""

    def test_policy_is_frozen(self):
        """``frozen=True`` 是为了让「每个角色一份」不变成「共享一份被改」。"""
        policy = policy_for("coder")
        assert isinstance(policy, AgentPolicy)
        with pytest.raises(FrozenInstanceError):
            policy.name = "别改"  # type: ignore[misc]
