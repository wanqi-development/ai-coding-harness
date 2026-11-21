# ASGI Gateway / Agent Worker 分离

## 执行模式

```text
RUN_EXECUTION_MODE=inline
```

保持旧的单进程执行：API 和 Agent run 都在同一个进程。`standalone` 固定使用该模式，
单机 `multi` 不配置时也使用该模式。

```text
RUN_EXECUTION_MODE=distributed
```

启用网关/worker 分离。仅支持 PostgreSQL + Redis 的 `multi` 部署。

## 进程角色

API 网关：

```bash
APP_ROLE=gateway
RUN_EXECUTION_MODE=distributed
REDIS_REQUIRED=true
python -m ai_coding_harness.main
```

Gateway 负责 HTTP、SSE、认证、CSRF、普通业务 API、工作区准备接口和队列提交；
它不执行 Agent、不调用模型、不运行业务工具。工作区准备与连接仍由 Gateway-owned
`/workspace` API 完成，Agent 工具实际在 worker 中使用同一个沙箱。

Agent worker：

```bash
APP_ROLE=worker
RUN_EXECUTION_MODE=distributed
REDIS_REQUIRED=true
bash scripts/linux/start-worker.sh
```

Worker 不暴露 HTTP 端口，只消费 `RUN_JOB_STREAM`，执行 LangGraph / 模型 / 工具 / 沙箱，
并把事件写入共享 Redis RunBuffer。

## Redis 数据

- `harness:agent-jobs`：Redis Stream，consumer group 为 `harness:agent-workers`；
- `harness:queued:<session>`：同一会话只允许一个待执行 job；
- `harness:active-job:<session>`：worker 心跳，防止长任务被误判为失效后重复领取；
- `harness:run:<session>:frames/seq/done`：SSE 帧与续传游标；
- `harness:run-lock:*`：执行权租约。

Worker 每 30 秒续一次 job heartbeat。进程崩溃后，pending message 在
`RUN_JOB_CLAIM_IDLE_MS` 与 active TTL 都过期后由其他 worker 领取。

## 一致性

- PostgreSQL 是会话、记录、work task、review、approval 和 checkpoint 的事实源；
- Redis 只承担队列、唤醒、帧流和租约；
- worker 每次执行 job 前重新加载模型配置和用户运行期设置；
- Gateway 读取 SSE 时只依赖 RunBuffer，不关心 job 最终由哪个 worker 执行；
- 前端 API 和 SSE 协议不变，仍使用 `after` 游标重连。

## 扩容

```text
API gateway replicas: 按 SSE 连接数、文件描述符和网络扩容
Agent workers:        按 Redis Stream lag、模型并发和沙箱容量扩容
```

网关无状态，不需要 sticky session。一个会话仍由 Redis run lock 保证最多一个执行者。

## 单机

```text
DEPLOYMENT_MODE=standalone
RUN_EXECUTION_MODE=inline
APP_ROLE=all
```

继续使用 SQLite、WSL 沙箱和进程内 RunHub，不需要 Redis，也不需要 worker 进程。
