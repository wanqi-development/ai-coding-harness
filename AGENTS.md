# AGENTS.md —— 本仓库的协作约定

给在本仓库里干活的编码智能体看的最小规则集。**只写稳定规则与实测事实**；
「这次改了什么、还有什么没做」一律看 `CHANGELOG.md`（最新在最上），不要写进本文件。

## 一、协作方式（最重要）

1. **凡是会写文件的操作，先给方案、等确认、再动手。** 方案要写清：改哪些文件、为什么、
   关键决策与取舍、要用户拍的点、风险与回退。
2. **只读排查不必先问**：读代码、看日志、量数据、跑测试看现状 —— 直接做。
3. 用户明确放行（「改吧」「开始做吧」「按这个来」）后，可以一次做到完并给验证结果；
   用户只是**提需求**时，默认先给方案。
4. 有多个口径可选时列成 a/b/c 并给出推荐，等用户点。

## 二、改完之后必须做的三件事

1. **跑测试**：见第三节。全量 `pytest` 必须全过；只改了某个模块时也至少跑相关文件。
2. **lint**：`ruff check` 本次改动的文件必须干净（全仓有若干既有告警，别新增）。
3. **写 CHANGELOG**：在 `CHANGELOG.md` 顶部（当日 `### 日期` 下、其他条目之上）追加一条，
   含 **动机 / 改动 / 关键决策 / 验证 / 遗留**。

## 三、环境与命令

- **Python 运行环境是 conda 环境 `aicoding`**（`D:\developmentBinary\miniconda3\envs\aicoding`）。
  本项目没有 `pip install -e .` 进这个环境，所以 `src` 要进 `PYTHONPATH`：

  ```powershell
  $env:PYTHONPATH='src'
  conda run -n aicoding --no-capture-output python -m pytest -q
  ```

- **lint**：`D:\python-venv\ai_coding\Scripts\ruff.exe check <改动过的文件>` —— 实测最稳。
  （`conda run -n aicoding --no-capture-output python -m ruff check …` 也能用，但命令非零退出、
  输出较多时会只回一句 conda 自己的 ERROR / 插件告警，把检查结果吞掉，别指望它稳定。）
  那套 venv（`D:\python-venv\ai_coding`）依赖也齐、且是把本项目 editable 装进去的，但它依赖的
  基座解释器曾经被删过一次；跑测试与跑代码默认仍走 conda。
- **提示词自检**：`python scripts/check_prompts.py` —— 渲染八份成品提示词并扫跨形态冲突，
  **必须是 0 条**。它零第三方依赖，任何 Python ≥3.11 都能跑。
- **前端**：`npm --prefix frontend run build`（含 `vue-tsc`）。PowerShell 里 `npm.ps1` 可能被
  执行策略挡住，那就用 `npm.cmd --prefix frontend run build`。dev 在 5173、后端在 8000，
  一键起用 `start.bat` / `start.sh`。
- **配置**：模型 / Gitee / GitHub / OpenSandbox / PostgreSQL 的凭据都在 `.env`（不进版本库），
  键的说明看 `.env.example`。
- **生效时机**：提示词与沙箱资产是**建会话时**读 / 推的，后端配置与装配改动要**重启后端**；
  前端是 vite 热更新，改完刷新即可。

## 四、硬约束与踩过的坑

- **提示词是行为契约**：改 `src/ai_coding_harness/agent/prompts.py` 的措辞等于改行为 ——
  必须跑 `tests/test_prompts.py` 与 `scripts/check_prompts.py`。
- **沙箱资产建会话时整棵推入**：`sandbox/policies`、`sandbox/skills`、`sandbox/maven`、
  `sandbox/deploy/sandbox/*` 改完要**新建会话**才保证生效。脚本类的资产有编码要求：
  `start.bat` 是 **GBK**，改动必须保住原编码，不能拿 UTF-8 直接覆盖（否则中文乱码）。
- **仓库记忆的写入口径**：平台只维护文件头（`最后更新` / `最近 PR`）并摘掉老模板的
  「需求列表」「功能列表」两张表；正文只在 **coding 与 review 的收尾**由主控写；
  `plan` 形态与三个子智能体都不写记忆。
- **拦截一律是代码拦，不是提示词**：`BoundaryMiddleware` 管「这个角色能用哪些工具、能写哪些
  目录」，`MemoryMiddleware` 管「仓库还有 open PR 就拦下 plan / orchestrate」（review **不**拦：
  它要审的那个 PR 本身就是 open 的），`LoopGuardMiddleware` 管空转。提示词只做前置劝阻。
- **两份记忆模板必须逐字一致**：`docs/repo-memory-template.md` 与
  `sandbox/policies/repo-memory-template.md`（有测试钉着）。
- **目录权限口径**看 `docs/workspace-directories.md`。**沙箱里的 Maven 已配好**（阿里云镜像 +
  卷上的本地仓库 `/ai_coding/.m2/repository`），不要让模型自己另配或清空。

## 五、去哪儿看

- `README.md`：项目是什么、API、目录结构、启动方式。
- `CHANGELOG.md`：每一次改动（最新在最上）—— 动手前先读顶部几条，能避开大部分重复劳动。
- `docs/`：目录权限、沙箱镜像、代码平台（Gitee / GitHub）PR API、记忆模板。
- `scripts/`：`check_prompts.py`（提示词跨形态冲突自检）、`verify_opensandbox.py`（沙箱连通性验证）。
