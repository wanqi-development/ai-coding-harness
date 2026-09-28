---
name: repo-bootstrap-analysis
description: >
  首次接触某个仓库（Gitee / GitHub）时的引导分析：摸清代码结构、技术栈、目录组织，
  识别启动/测试/构建命令，输出项目引导报告。当用户要求分析项目结构、
  生成技术方案、或不清楚如何启动/测试时使用。只读分析，不改代码。
  Triggers on: 分析项目结构, 分析代码结构, 项目引导, 怎么启动, 怎么测试,
  生成技术方案, bootstrap, analyze repo structure, how to run.
---

# 仓库引导分析（repo-bootstrap-analysis）

## 运行环境背景（务必先读）

本 skill 运行在 **OpenSandbox 沙箱**中：

- 工作区根目录：`/ai_coding`
- 项目目录：`/ai_coding/project`（目标仓库 clone 在此）
- 结论去向：**回复本身**（分析结论直接写进回复，不落文件）
- 中间目录：`/ai_coding/tmp`（分析过程产生的临时文件放这里）
- 命令一律通过 deepagents 的 `execute` 工具执行，无交互。

## 命令限制（红线）

**只读分析**：除中间目录外，一切操作只读，不修改代码、不改仓库状态、不装依赖。

- 可写目录：只有 `/ai_coding/tmp`（中间过程文件）。最终结论不落文件 —— 写进回复。
- 其余路径（尤其 `/ai_coding/project`）**只允许只读命令**。

### 只读命令白名单

```
ls / find / tree / stat / wc / file / cat / head / tail
grep / rg（ripgrep）/ sed -n（只打印）/ awk（只打印）
git status / log / show / diff / ls-remote / remote -v
python --version / node --version / go version / java -version / cargo --version
python -c "..."（仅用于解析依赖文件，不写任何文件）
```

### 禁止

- 写 `/ai_coding/project` 下任何文件（改代码、生成文件都不行）。
- git 写命令（add/commit/checkout -b/push 等）。
- 安装依赖（pip install / npm install / go get 等，会写环境）。
- 运行可能产生副作用的命令（启动长驻服务、写日志到项目目录等）。

## 适用场景

- 第一次处理某个仓库，需要快速摸清项目。
- 用户要求分析项目结构、生成技术方案。
- 不清楚项目的启动方式、测试方式、构建方式。

## 执行步骤

### 1. 确认仓库就绪

```bash
cd /ai_coding/project
ls -la
[ -d <repo>/.git ] && echo "repo cloned"   # 未 clone 则报告用户
cd <repo>
```

### 2. 摸清目录结构、识别语言

```bash
# 顶层结构
ls -la
# 目录树（限制深度，避免过长）
find . -maxdepth 2 -type d -not -path '*/.git*' | sort
# 依赖/构建配置文件（据此判断主要语言）
ls pyproject.toml requirements.txt setup.py package.json tsconfig.json \
   go.mod pom.xml build.gradle build.gradle.kts Cargo.toml composer.json \
   Dockerfile Makefile 2>/dev/null
```

根据出现的配置文件判断语言，再按对应语言走第 3-5 步。若项目同时含多种语言，分别分析并在报告中说明。

### 3. 按语言识别依赖

- **Python**：`cat pyproject.toml` / `requirements.txt` / `setup.py`
- **JavaScript / TypeScript**：`cat package.json`（dependencies / devDependencies / scripts）
- **Go**：`cat go.mod`（module 名 + require 列表）
- **Java**：`cat pom.xml`（Maven，dependencies）或 `build.gradle`（Gradle）

### 4. 按语言识别入口与启动方式

- **Python**：`main.py` / `app.py` / `manage.py` / `wsgi.py` / `asgi.py`
- **JavaScript / TypeScript**：`index.js` / `server.js` / `app.js` / `src/index.ts`，结合 `package.json` 的 `scripts.start`
- **Go**：`main.go` / `cmd/` 目录，结合 `go.mod` 的 module 名
- **Java**：`src/main/java`（找 `@SpringBootApplication` 或含 `public static void main` 的类）

通用：读 `../../../README.md` 的启动说明、`../../../scripts`、`Makefile`、`Dockerfile` 的 CMD/ENTRYPOINT。

### 5. 按语言识别测试与构建命令

- **Python**：`pytest` / `unittest`（找 `../../../tests`、`pytest.ini`、`conftest.py`）
- **JavaScript / TypeScript**：`npm test`（jest / vitest / mocha），构建 `npm run build`
- **Go**：`go test ./...`，构建 `go build ./...`
- **Java**：`mvn test` / `gradle test`，构建 `mvn package` / `gradle build`

通用：`ls tests/ test/`、`grep` 测试框架关键词、`Dockerfile` / `Makefile` 里的构建命令。

### 6. 输出结论

分析结论**直接写进回复**，按下方「输出格式」的结构组织 —— 不要写入 `/ai_coding/output/`，也不要只回一句「已写入文件」：那个目录用户读不到，落成文件等于把答案藏起来。中间过程的临时文件放 `/ai_coding/tmp/`，不必回报。

## 输出格式

回复按下面的结构组织（标题用仓库名）：

```markdown
# <repo> 项目引导分析

## 技术栈
- 语言 / 框架 / 关键依赖

## 目录结构
- 关键目录与职责

## 启动方式
- 启动命令 + 前置依赖（环境变量、配置文件、外部服务）

## 测试方式
- 测试命令 + 测试目录

## 构建方式
- 构建命令 + 产物位置（如适用）

## 注意事项
- 启动/测试的坑、依赖版本约束、未明确之处

## 下一步建议（必写）
- 无论结论如何都要写；即使结论是「项目已就绪、可直接开工」，也必须明确写出。
- 给出 1-3 条按优先级排序的动作，每条写清「做什么 + 为什么」。
- 依据本次分析的实际发现来写，不要抄通用套话。典型两类：
  - 前置未就绪（缺环境变量 / 依赖外部服务 / 启动命令没查实）→ 建议先补齐或先与用户确认，再谈开发。
  - 项目已能跑通 → 建议进入需求分析 / 方案设计，或明确需求后按 `ai-coding-implementation` 落地提 PR。
- 命令或结论有不确定的地方时，建议里要指名让用户确认哪一条。
```

报告写进回复后，再用一两句给出结论：技术栈、启动命令、测试命令、构建命令，以及下一步建议。

## 红线规则

- **只读**：不改 `/ai_coding/project` 下任何内容，不执行 git 写命令、不装依赖。
- 只写 `/ai_coding/tmp`（中间过程）；结论进回复，不落文件，不越界。
- 命令失败/不确定时，如实报告，不要臆造启动命令。
- 分析结果要基于实际读到的文件，不要凭空推断。
