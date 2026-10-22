# 简历项目条目（AI Agent 工程师方向）

> 本文件只放简历用的项目描述，方便后续取用与迭代；不属于运行时文档。

## 推荐版本（6 条）

**AI Coding Harness｜面向代码仓库的 Multi-Agent 编码平台（个人项目，独立完成）**  
Python / FastAPI / LangChain·deepagents / OpenSandbox / PostgreSQL / Redis / Vue3

- **Agent 架构**：独立设计“形态即装配轴”的 Agent 架构，由一处配置决定子 Agent、工具集与角色提示词；实现 1 主 3 从（planner / coder / reviewer）协作、跨形态 handoff、PR 状态候选与函数闸门，覆盖 4 类意图、3 种形态，避免多套分支漂移。
- **Context Engineering**：独立定位并修复单轮累计 690 万输入 token 的上下文膨胀，根因包括历史工具结果重复发送、占位符失忆导致重读、压缩阈值口径错配；落地中英混合 token 计数、带身份占位符、压缩线“窗口比例 OR 绝对上限”、摘要输入限长与压缩前应急裁剪。事故轮单次输入 15.2 万 token，整改后复测轮峰值 7.7 万 token。
- **Tool Use & Guardrails**：独立实现按角色的工具与目录边界中间件，把“角色能用哪些工具、能写哪些目录”从提示词约束下沉为代码拦截；配合命令 denylist、凭据脱敏与沙箱 askpass，形成工具调用、文件写入、凭据访问三层防线。
- **Agent Runtime**：独立实现与 HTTP 请求解耦的运行宿主，SSE 帧带游标支持断线续传与显式终止；同一会话运行权互斥；可选 Redis 承载跨 worker 帧缓冲、运行权与熔断状态，未配置时自动降级到进程内。
- **平台化与全栈落地**：独立完成模型配置、提供商目录与设置工作台：模型按 Agent 分行配置并支持统一兜底，上下文窗口按模型解析，保存后下一轮生效；同时完成 FastAPI 后端、Vue3 前端、PostgreSQL/Redis 与 OpenSandbox 沙箱集成。
- **质量与可观测**：独立建立 1400+ 自动化测试与提示词契约检查，覆盖工具调用配对、历史修复、上下文压缩/裁剪口径、模型解析顺序与记忆闸门；日志记录每次模型调用、上下文裁剪/压缩与 Token 消耗，支撑问题定位。

## 一页简历精简版（4 条）

**AI Coding Harness｜Multi-Agent 编码平台（个人项目，独立完成）**

- 从 0 到 1 独立设计并实现面向真实代码仓库的 Multi-Agent 编码平台：状态候选意图识别 → Agent 装配 → 沙箱工具调用 → PR 创建/评审 → SSE 流式回传，覆盖 4 类意图、3 种形态、1 主 3 从协作。
- 独立完成 Context Engineering：定位单轮 690 万 token 的膨胀根因，落地中英混合计数、带身份占位符、压缩阈值与应急裁剪，事故轮单次输入 15.2 万 → 复测轮峰值 7.7 万 token。
- 独立实现 Agent 权限边界与运行宿主：按角色裁剪工具/可写目录，执行前校验命令；SSE 断线续传与显式终止、同会话运行权互斥、可选 Redis 跨 worker 状态。
- 独立建立 1400+ 测试与提示词契约检查，覆盖工具调用配对、历史修复、压缩/裁剪口径、模型解析顺序与记忆闸门；日志记录模型调用、上下文裁剪/压缩与 Token 消耗。

## 关键词

Multi-Agent、Agent Runtime、Tool Calling、Context Engineering、Memory、Guardrails、Handoff、SSE、FastAPI、LangChain / deepagents、OpenSandbox、PostgreSQL、Redis、Vue3。

## 面试准备：三个主线故事

1. **690 万 token 上下文膨胀**：现象 → 日志/trace 定位 → 三个根因 → 分层整改 → 复测数据。
2. **Tool Use 权限边界**：为什么提示词靠不住、如何按角色裁工具/目录、有哪些已知缺口（相对路径、间接执行）。
3. **Agent Runtime 的失败模式**：断线、刷新、取消、同会话并发、Redis 不可用时如何降级。

## 写作口径

- “独立完成”只在标题或第一行写一次，不必每条重复。
- 用范围证明 ownership：后端、前端、沙箱、数据库、测试、部署都点到，但 AI Agent 岗优先写 Agent 架构、Context Engineering、Tool Use。
- 每个 PR 只做一件事，描述里写清“背景 / 改动 / 关键决策 / 验证 / 风险”，这些记录本身就是独立完成的证据。
