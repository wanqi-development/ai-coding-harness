# 多用户登录与认证

## 目标

当前版本提供单部署内的多用户工作台：

- 首次由运维在服务器上创建初始管理员；
- 管理员可以创建、启停、升级/降级用户并重置临时密码；
- 浏览器使用用户名和密码登录；
- 登录状态存 PostgreSQL，多 worker 共享，不依赖进程内 Session 或 sticky session；
- 业务会话按用户隔离；
- 不开放公开注册；账号由管理员创建；
- 本期仍不实现 OIDC、SAML、邮件找回密码或 2FA。

代码平台仍然使用设置页中的 Gitee / GitHub access token，与登录认证是两套凭据。

## 数据模型

| 表 | 用途 |
| --- | --- |
| `users` | 平台用户主体、显示名、角色与启停状态 |
| `local_credentials` | 本地密码哈希、失败次数和短时锁定时间 |
| `user_identities` | 预留给 OIDC / SAML 等外部身份的稳定映射 |
| `auth_sessions` | 登录会话；只保存 token 的 SHA-256，不保存明文 |
| `sessions.user_id` | 业务会话归属；认证上线前的历史会话由 CLI 选择是否认领 |

密码使用 Python 标准库 `hashlib.scrypt`。登录 token 和 CSRF token 都是随机不透明值，
数据库只保存哈希。

## 登录与会话

1. 用户提交用户名和密码；
2. 服务端校验 `local_credentials`，连续失败达到阈值后短时锁定；
3. 成功后创建 `auth_sessions` 行，并下发两个 Cookie：
   - `harness_session`：HttpOnly，浏览器脚本不可读；
   - `harness_csrf`：前端可读，用于双提交 CSRF 校验。
4. 每个受保护请求都由全局 `AuthMiddleware` 查库恢复身份；
5. `POST /auth/logout` 撤销当前数据库会话并清理 Cookie；
6. 修改密码会撤销该用户的其他登录会话，并为当前浏览器换发新会话。
7. 管理员创建账号或重置密码后，`must_change_password=true`；用户登录后除
   `/auth/me`、`/auth/logout`、`/auth/change-password` 外的 API 都会被阻止。

认证 Cookie 默认 `SameSite=Lax`。纯 HTTP 本地开发可保持 `AUTH_COOKIE_SECURE=false`；
任何 HTTPS 部署都必须设为 `true`。

公开接口只有：

```text
GET  /health
GET  /auth/config
POST /auth/login
```

其余 API 都要求有效登录。所有写请求还要求 Cookie 与 `X-CSRF-Token` 请求头一致。

## 角色与用户管理

- `admin`：可以使用用户管理、全局运行期配置和提供商目录管理；
- `member`：可以使用自己的会话、聊天、代码面板、模型配置、代码缓存/默认分支、个人改密和本机显示偏好；
- 角色限制由 `AuthMiddleware` 在 HTTP 边界执行，前端隐藏菜单只是展示层；
- 系统禁止停用或降级最后一个启用的管理员，也禁止管理员通过用户管理接口停用自己；
- 管理员重置密码和停用账号都会撤销目标用户的全部登录会话。

管理员接口：

```text
GET   /users
POST  /users
PATCH /users/{id}
POST  /users/{id}/reset-password
POST  /users/{id}/revoke-sessions
```

“同一仓库一个活跃会话”的复用边界是 `user_id + 仓库`：不同用户打开同一仓库时各自拥有
独立会话和工作区。

## 多 worker

认证事实全部在 PostgreSQL：

- worker 不缓存登录会话；
- 任意 worker 都能处理后续请求；
- 修改密码撤销其他会话后，各 worker 的下一次请求立即失效；
- Redis 不参与认证，也不保存 Cookie。

因此反向代理不需要 sticky session，只需把同一域名下的请求正常分发到各 worker。

## 首次初始化与运维

先执行数据库迁移：

```powershell
alembic upgrade head
```

再交互式创建管理员。密码只从终端输入，不进入命令历史或配置文件：

```powershell
python -m ai_coding_harness.manage create-admin --username admin --claim-existing
```

`--claim-existing` 会把认证上线前 `sessions.user_id IS NULL` 的历史会话归给该管理员。
如果不希望认领，可省略该参数。

忘记密码时在服务器执行：

```powershell
python -m ai_coding_harness.manage reset-password --username admin
```

重置会同时撤销该用户的全部现有登录会话。

## 后续接入外部用户系统

`user_identities` 已按 `(provider_key, issuer, subject)` 预留唯一映射。后续接入 OIDC / SAML 时：

1. 外部认证适配器只负责验签并返回统一的 `AuthIdentity`；
2. 平台按 `(provider_key, issuer, subject)` 找到或关联 `users`；
3. 平台仍然签发自己的 `auth_sessions`，不把外部 access token 当浏览器会话；
4. 业务 API 和会话归属校验保持不变。

这样替换的只是“如何证明用户是谁”，不替换平台会话、CSRF、多 worker 和会话隔离机制。

## 当前边界与后续

- 初始管理员仍通过 `create-admin` 创建；之后不再使用 CLI 创建普通用户；
- 不提供注册接口；
- 本阶段完成账号、角色、会话、模型、代码缓存路径、默认分支与 Gitee/GitHub 凭据隔离；
- 仓库记忆与 PR 数据属于**仓库级共享事实**，不按用户复制。用户必须通过自己的会话访问，
  创建会话前会用当前用户凭据验证仓库访问权；
- Session 快照使用版本化缓存：Redis 模式下 5 分钟 TTL，token 以 AES-256-GCM 加密；
- CLI 初始化要求数据库迁移已经执行；
- 外部 IdP 适配器只预留了数据表与设计边界，本期没有登录入口。
