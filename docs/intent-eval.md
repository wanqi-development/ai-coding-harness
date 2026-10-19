# 意图判定评测与影子路由器

这份文档对应两件**不改变线上行为**的事：

- `scripts/eval_intent.py`：从真实记录收集样本、人工标注、跑关键词 / 现有判定 / 影子路由三路，出准确率与混淆矩阵；
- `server/intent_router.py`：把一句话拆成 `speech_act / wants / target / continuation` 四个轴，
  再由第一版策略表给一个“建议意图”。**线上默认不采用建议**，只在
  `INTENT_ROUTER_SHADOW=true` 时把两者并排记进 `INTENT_ROUTER` 日志。

## 为什么拆轴

七选一把“这句话在干什么”“想要什么”“指向什么”“是不是继续上一轮”压成一格，规则只能
靠词表补丁。拆轴后：

- “为什么这里会超时” = 新任务 + analyze + code；
- “订单模块有哪些功能” = 新任务 + lookup + code（→ qa）；
- “你为什么不派 coder” = 追问上一轮 + analyze/change + execution；
- “按评审意见改 PR” = 新任务 + 改 + pr_change。

同样的轴可以覆盖新说法，不必再把每一句话加进关键词。

## 上下文与语义口径

- 判定看**最近两轮对话 + 本轮问题**：用户消息保留全文；助手消息取首 200 / 尾 300 字
  （中间省略）。只给上一轮回复的尾部不够解释“余额不足/任务中断”这类上下文。
- `analysis`：解释功能、解释用户疑问、刷新仓库记忆、**PR 已合并或已关闭之后的收尾动作**
  （更新记忆、同步、清理）。刚提交/待合并的 PR 不算。
- `qa`：只对**已有数据**问答（已具备哪些功能、有哪些 PR、文件/提交/记忆里写了什么）。
- `plan`：计划、方案、任务完成度**一律**归 plan。
- 「继续 / 接着 / 修正 / 纠正 / 重来 / 再试一次」= 未完成轮次，沿用上一轮可执行意图。

## 快速开始

```powershell
$env:PYTHONPATH='src'

# 1. 从业务库导出最近的真实用户消息（只读）
conda run -n aicoding --no-capture-output python scripts/eval_intent.py collect `
  --out docs/intent-eval.jsonl --limit 200

# 2. 人工把每行的 expected 填成
#    coding / review / rework / plan / qa / analysis / sync 之一
#    留空的样本不参与统计。

# 3. 跑评测（只读一次库里的模型配置，不写库）
conda run -n aicoding --no-capture-output python scripts/eval_intent.py run `
  docs/intent-eval.jsonl
```

仓库里有一份可直接跑的小样例：`docs/intent-eval.example.jsonl`。
真实样本默认写到 `docs/intent-eval.jsonl`，该文件已在 `.gitignore` 里 —— 用户消息可能
包含代码片段或业务信息，别把它提交到公开仓库。

如果导出后想补上真实的上一轮意图 / 回复末尾（追问类样本很需要）：

```powershell
conda run -n aicoding --no-capture-output python scripts/eval_intent.py enrich `
  docs/intent-eval.jsonl
```

如果想把这个文件从“一行一个对象”改成便于阅读的缩进 JSON 数组：

```powershell
conda run -n aicoding --no-capture-output python scripts/eval_intent.py format `
  docs/intent-eval.jsonl
```

文件名保留 `.jsonl`，但 `format` 后内容是缩进 JSON 数组；评测脚本的读取端两种格式都认。

## 输出怎么看

- `keyword`：纯关键字口径，作为最保守基线；
- `normal`：当前线上判定（口令 → JEV/大模型 → 关键字）；
- `router`：影子路由器的建议意图（**线上不采用**）。

每路都会打印准确率和一张“行=期望、列=预测”的混淆矩阵；最后列出最多 20 条错例，
包括路由器的四个轴，方便判断是“轴判错了”还是“策略表映射错了”。

## 标注口径

`expected` 是**你希望这一轮进入哪个形态**，不是“模型上次判了什么”。例如：

- “你为什么不派 coder”标 `rework`，表示你希望它继续/纠正上一轮动作；
  如果你只想让它解释，就标 `analysis`。这是一个产品口径，先在小样例里明确。
- “现在有哪些 PR”标 `qa`；“审查 PR #13”标 `review`；“按评审意见改 PR”标 `rework`。

`previous_intent` / `previous_reply` 能填就填：追问类样本没有上一轮上下文，测不准。
`collect` 目前只导出消息与当时的 intent；上一轮上下文可以手工补，后续可以再扩展收集脚本。

## 线上影子日志

默认关闭。要观察真实轮次：

```dotenv
INTENT_ROUTER_SHADOW=true
INTENT_ROUTER_TIMEOUT_SECONDS=5
```

重启后端后，`logs/agent.log` 会出现：

```text
INTENT_ROUTER normal=analysis source=jev confidence=... axes={'speech_act': 'meta_question', ...} suggested=rework public_id=...
```

它跑在独立 task 里，不占本轮首字节；失败只记日志，不影响任何返回。**不要**在评测
证明路由器更准之前把 `suggest_intent()` 接进形态选择。

## 当前边界

- 策略表是第一版，只为把“轴 → 意图”的映射集中到一处，不是最终结论；
- 路由器用“意图识别（大模型）”那一行的模型；没配就只跑关键词/现有判定；
- `collect` / `enrich` 只读库、不改记录；`run` 只读一次库里的模型配置，不写库；
- 评测集的标注是持续工作：每次发现错例，补一条样本并跑一遍，比继续堆关键词可靠。
