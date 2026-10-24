"""提示词自检：渲染六个装配好的提示词，并扫一遍跨形态的口径冲突。

为什么不用 ``pytest`` 就够了：这里要的是**渲染后的成品**（主控三形态经
``build_main_prompt`` 拼上输出形态规则，三个子智能体各是一份独立常量）——
单测钉的是「某句话在不在某份提示词里」，这个脚本回答的是「同一句话在**别**的形态里
是否成了一句做不到的指令」。

另外它不导入第三方依赖（``config`` 被桩掉，只加载 ``profiles`` / ``prompts`` /
``policy`` 三个纯模块），所以在装不起依赖的机器上也能跑：

    <python> scripts/check_prompts.py
    <python> scripts/check_prompts.py --grep 仓库记忆     # 看某个说法在各个形态里的原文

退出码 1 = 有冲突（每条都打印出来）。
"""

from __future__ import annotations

import importlib.util
import re
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
PKG = SRC / "ai_coding_harness"


def _load(name: str, path: Path):
    """按文件路径加载模块（不触发 ``ai_coding_harness`` 各层 ``__init__``）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _bootstrap():
    """桩掉 config 与包链，只把三个纯模块装进来。"""
    for name in ("ai_coding_harness", "ai_coding_harness.agent"):
        package = types.ModuleType(name)
        package.__path__ = []  # type: ignore[attr-defined]
        sys.modules[name] = package

    class _Settings:
        agent_output_style_file = "adhd/AGENT.md"

    config = types.ModuleType("ai_coding_harness.config")
    config.Settings = _Settings  # type: ignore[attr-defined]
    config.get_settings = _Settings  # type: ignore[attr-defined]
    sys.modules["ai_coding_harness.config"] = config

    profiles = _load("ai_coding_harness.agent.profiles", PKG / "agent" / "profiles.py")
    prompts = _load("ai_coding_harness.agent.prompts", PKG / "agent" / "prompts.py")
    policy = _load("ai_coding_harness.safety.policy", PKG / "safety" / "policy.py")
    return profiles, prompts, policy


def _forms(profiles, prompts) -> dict[str, str]:
    repo = "https://gitee.com/owner/repo.git"
    main = {
        f"主控/{form}": prompts.build_main_prompt(repo, form=form)
        for form in (
            profiles.ORCHESTRATE_FORM,
            profiles.QA_FORM,
            profiles.PLAN_FORM,
        )
    }
    return main | {
        "子/planner": prompts.PLANNER_PROMPT,
        "子/coder": prompts.CODER_PROMPT,
        "子/reviewer": prompts.REVIEWER_PROMPT,
    }


# ---------------------------------------------------------------------------
# 冲突扫描：每条都对应一次真发生过的口径打架（改完必须全绿）
# ---------------------------------------------------------------------------

_WRITE_STEP = ("git checkout -b", "git add -A", "git push origin")
_WRITABLE = re.compile(r"`/ai_coding/(project|tmp|output|review)/`")


def _claims_writable(text: str, directory: str) -> bool:
    """这一行有没有把这个目录说成可写（否定的说法不算）。"""
    for line in text.splitlines():
        if not _WRITABLE.search(line) or directory not in line:
            continue
        if "可写" not in line:
            continue
        if any(negation in line for negation in ("不可写", "不写", "只读")):
            continue
        return True
    return False


def _scan(forms: dict[str, str], profiles, policy) -> list[str]:
    bad: list[str] = []

    def flag(check: str, where: str, detail: str) -> None:
        bad.append(f"[{check}] {where}：{detail}")

    for name, text in forms.items():
        # 1. 清单口径：既要求「一步一条」又要求「不复述方案」，模型只能二选一
        if "把方案的每一步各列一条" in text and "清单只写你自己的动作" in text:
            flag("C1 清单口径", name, "同时要求「一步一条」与「不复述方案」")

        # 2. 委派子智能体只属于 orchestrate：别的形态没有 `task`
        is_orchestrate = name == "主控/orchestrate"
        if "必须优先委派" in text and not is_orchestrate:
            flag("C2 越界委派", name, "被要求委派 reviewer，但本形态没有 task")

        # 3. PR 回评只给持有 comment_pr 的形态（qa / plan 都没有）
        if name in ("主控/qa", "主控/plan") and "在 PR 上回复" in text:
            flag("C3 越界回评", name, "被要求回评 PR，但本形态没有 comment_pr")

        # 4. git 写命令只给 coder：其余形态的身份是「不写码 / 零动手 / 全程只读」
        if name != "子/coder":
            for step in _WRITE_STEP:
                if step in text:
                    flag("C4 越界写命令", name, f"拿到写仓库的命令「{step}」")

        # 5. 记忆正文只在 coding / rework 收尾时写：出方案轮不能声称自己会写记忆
        if name == "主控/plan":
            if "不写仓库记忆" not in text:
                flag("C5 plan 记忆", name, "没写清出方案轮不写仓库记忆")
            if "（StoreBackend，**只读**）" not in text:
                flag("C5 plan 记忆", name, "没写清仓库记忆是只读的")

        # 6. 提示词里的「可写」必须与 policy 的实际写根一致
        identity = name.split("/", 1)[1]
        entry = policy.policy_for(identity)
        allowed = {Path(root).name for root in entry.write_roots}
        for directory in ("project", "tmp", "output", "review"):
            if _claims_writable(text, directory) and directory not in allowed:
                flag("C6 目录可写", name, f"说 `/ai_coding/{directory}/` 可写，边界却不允许")

        # 7. 只读形态不该收到「改了代码就 commit + push」这类无条件落地要求
        if name == "主控/qa" and "代码 commit + push" in text:
            flag("C7 落地口径", name, "无条件要求 commit + push")

    return bad


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    profiles, prompts, policy = _bootstrap()
    forms = _forms(profiles, prompts)

    if args and args[0] == "--grep":
        needle = args[1]
        for name, text in forms.items():
            hits = [line.strip() for line in text.splitlines() if needle in line]
            print(f"--- {name}（{len(hits)} 行）")
            for line in hits:
                print("    " + line)
        return 0

    bad = _scan(forms, profiles, policy)

    print(f"渲染了 {len(forms)} 份提示词：" + "、".join(forms))
    if not bad:
        print("冲突扫描：0 条")
        return 0
    print(f"冲突扫描：{len(bad)} 条")
    for item in bad:
        print("  " + item)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
