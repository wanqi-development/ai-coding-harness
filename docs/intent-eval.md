# 意图判定评测

`scripts/eval_intent.py` 用于从真实记录收集样本、人工标注，并比较：

- `keyword`：纯关键字口径；
- `normal`：当前线上判定（口令 → JEV / 大模型 → 关键字兜底）。

评测不写数据库，也不改变线上行为。

## 上下文与语义口径

- 判定看最近三个用户轮次 + 本轮问题：user / assistant / plan 记录保留正文；
- `qa`：已有数据问答、解释功能 / 用户疑问、排查原因、需求澄清；
- `plan`：计划、方案、任务完成度一律归 plan；
- `coding`：没有可继续的 PR 时写代码、修代码、按方案实施；
- `rework`：跟进 open PR，审查、回评或按意见继续修改；
- 「继续 / 接着 / 修正 / 纠正 / 重来 / 再试一次」按未完成轮次处理；
- 短追问优先参考紧邻上一轮判定与最近三轮完整会话，不维护跨轮 active task。

## 快速开始

```powershell
$env:PYTHONPATH='src'

# 从业务库导出最近的真实用户消息（只读）
conda run -n aicoding --no-capture-output python scripts/eval_intent.py collect `
  --out docs/intent-eval.jsonl --limit 200

# 人工把 expected 填成 coding / rework / plan / qa 之一；
# 留空的样本不参与统计。

# 跑评测（只读一次库里的模型配置，不写库）
conda run -n aicoding --no-capture-output python scripts/eval_intent.py run `
  docs/intent-eval.jsonl
```

仓库里有一份可运行的小样例：`docs/intent-eval.example.jsonl`。
真实样本默认写到 `docs/intent-eval.jsonl`，该文件在 `.gitignore` 中；不要提交可能包含
代码片段或业务信息的用户消息。

补充最近三轮上下文：

```powershell
conda run -n aicoding --no-capture-output python scripts/eval_intent.py enrich `
  docs/intent-eval.jsonl
```

把 JSONL / JSON 统一格式化成缩进 JSON 数组：

```powershell
conda run -n aicoding --no-capture-output python scripts/eval_intent.py format `
  docs/intent-eval.jsonl
```

## 输出

- `keyword` 与 `normal` 各打印准确率和“行=期望、列=预测”的混淆矩阵；
- 低置信度区段会模拟当前 clarify 阈值，给出原判定正确率；
- 最后列出最多 20 条错例，便于补评测样本、调整提示词或关键字规则；
- `run --out <path>` 可保存逐样本结果，方便人工对比，不参与线上运行。

## 标注口径

`expected` 是希望这一轮进入哪个形态，不是“模型上次判了什么”：

- “现在有哪些 PR”标 `qa`；
- “审查 PR #13”“按评审意见修改”标 `rework`；
- “你为什么不派 coder”按用户真实意图标；只想解释是 `qa`，要求继续上一轮动作则沿用该动作；
- 讨论方案标 `plan`，明确让写代码 / 按方案实施标 `coding`。

`previous_intent` / `previous_reply` / `dialogue` 能补就补；追问类样本缺上下文会显著
降低评测价值。

## 边界

- `collect` / `enrich` 只读业务库；
- `run` 只读模型配置，不修改线上状态；
- 评测集是持续资产：每发现一个稳定错例就补样本，先量准确率再改提示词或规则。
