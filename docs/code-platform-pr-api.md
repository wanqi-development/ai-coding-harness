# 代码平台 PR 接口速查（Gitee / GitHub）

> **本文件仅供人查阅，不会注入沙箱。**
> 智能体**一律通过 `create_pr` / `comment_pr` / `get_pr_info` / `close_pr` 工具**
> 操作 PR，禁止自行拼接凭据调平台 API。**PR 合并由人工在平台上处理，平台不提供合并工具**
> （下方「合并 PR」一节仅供人查阅接口细节）。原先这份文档放在
> `../sandbox/skills/code-review/references/` 下会被推进沙箱，模型读到「token 放 body 直接调
> API」后试图绕开工具白名单，故移出。

工具层两个平台是**同一套**（工具名与入参相同），平台由仓库地址判定；差异封装在
`tools/gitee_client.py` / `tools/github_client.py`。下面先列 Gitee，再列 GitHub 的差异点。

## Gitee（OpenAPI v5）

Base URL: `https://gitee.com/api/v5`

## 认证：令牌只走请求头

```http
Authorization: token <GITEE_TOKEN>
```

**不要**把 `access_token` 放进 query 或 JSON body。Gitee 这两种形式都接受，但令牌会因此进 URL ——
httpx 会把完整 URL 按 INFO 写进日志（`logs/agent.log`），等于把令牌落盘明文。

实测（2026-09-14）确认 header 形式对读写方法均有效：无凭据时 `GET /user`、`POST/PUT/PATCH /pulls*`
一律 401；改带 `Authorization: token <T>` 后分别变为 200 / 400 / 404（认证已过，只是参数无效）。
`Bearer <T>` 形式同样可用（实测 `/user` 200），平台客户端统一用 `token` 形式。

创建/合并前请确认令牌拥有 `projects` 权限。

## 创建 PR

```http
POST /v5/repos/{owner}/{repo}/pulls
Authorization: token <GITEE_TOKEN>
Content-Type: application/json

{
  "title": "feat: xxx",
  "head": "feature/xxx",          // 同仓库分支名；fork 场景为 "fork_owner:branch"
  "base": "master",
  "body": "改动说明...",
  "prune_branch": true            // 可选：合并后删除源分支
}
```

响应 201，返回的 `html_url` 即 PR 页面链接。

## 查询 PR 状态

```http
GET /v5/repos/{owner}/{repo}/pulls/{number}
Authorization: token <GITEE_TOKEN>
```

关注字段：`state`（open/merged/closed）、`mergeable`、`merged_at`。

**注意**：合并后 `merged` 字段可能仍为 `false`，只有 `state` 变成 `merged` —— 判断是否合并不只看
`merged`。

## 列出 PR

```http
GET /v5/repos/{owner}/{repo}/pulls?state=open&per_page=100
Authorization: token <GITEE_TOKEN>
```

按分支筛 PR 时不依赖 `head` 查询参数（各版本对 `owner:branch` 形式的接受度不一致），
而是拉回列表在本地按 `head.ref` / `base.ref` 过滤，并跳过来自 fork 的同名分支。

## 评论 / 合并 / 关闭

```http
POST  /v5/repos/{owner}/{repo}/pulls/{number}/comments      body: {"body": "..."}
PUT   /v5/repos/{owner}/{repo}/pulls/{number}/merge         query: merge_method=merge|squash|rebase
PATCH /v5/repos/{owner}/{repo}/pulls/{number}               query: state=closed
```

## 注意事项

- 频率限制：未认证 10 次/分钟；认证后按账号配额。
- `head` 分支必须**已 push** 到远端，否则报 404/422。
- 目标分支受保护时，PR 需满足仓库的分支保护规则（review、状态检查）才能合并；
  未通过审查时合并接口返回 405，响应体含「审查」字样。

## GitHub（REST v3）—— 与 Gitee 的差异

Base URL: `https://api.github.com`（`GITHUB_BASE_URL` 可覆盖）。

```http
Authorization: Bearer <GITHUB_TOKEN>
Accept: application/vnd.github+json
X-GitHub-Api-Version: 2022-11-28
```

| 动作 | Gitee | GitHub |
| --- | --- | --- |
| 创建 PR | `POST /repos/{o}/{r}/pulls` | 同（`head` 可用 `owner:branch`） |
| 评论 PR | `POST/GET /repos/{o}/{r}/pulls/{n}/comments` | **走 issues 端点** `POST/GET /repos/{o}/{r}/issues/{n}/comments`（`/pulls/{n}/comments` 是 diff 行级 review 评论，要 `path`/`position`） |
| PR 详情 | `GET /repos/{o}/{r}/pulls/{n}` | 同 |
| 关闭 PR | `PATCH /pulls/{n}?state=closed`（query） | `PATCH /pulls/{n}` + body `{"state":"closed"}` |
| 列表筛选 | `state=open\|closed\|merged\|all` | 只有 `open\|closed\|all`；要 merged 就查 `closed` 再按 `merged_at` 本地过滤 |
| 合并态 | `state=merged`（或 `merged` 字段） | `state=closed` + `merged_at` 非空 |
| PR 链接 | `/{o}/{r}/pulls/{n}` | `/{o}/{r}/pull/{n}`（单数） |
| 评论顺序 | 新版在前 | 默认按时间**正序** |

其余注意：

- 令牌权限：classic PAT 需要 `repo`；fine-grained 需要 `Contents` 与 `Pull requests` 的读写；
  GitHub App 安装令牌同理。令牌只走请求头（进 URL 会被 httpx 按 INFO 记进日志）。
- `mergeable` 在刚创建时常为 `null`（后台还在算）—— 客户端与工具如实回「未知（平台还在计算）」，
  不当成「不能合并」。
- 频率限制：未认证 60 次/小时；认证后 5000 次/小时，头部 `x-ratelimit-remaining` 为 0 时返回 403。
- **PR 合并仍然由人工在平台上点**（与 Gitee 同一口径），平台不提供合并工具。
