# 上下文裁剪事件设计

> 日期：2026-10-22  
> 状态：已确认，按本文实施

## 一、要解决的问题

现有应急裁剪只修改本次 `ModelRequest`，不修改 checkpoint。裁剪后的上下文能完成当前
一次 LLM 调用，但下一次调用仍从完整 checkpoint 取历史。只要原历史已经达到压缩线，
下一次调用必然再次到线：

```text
checkpoint: R >= 触发线 T
本次请求: 裁剪到 P < T
下一次请求: checkpoint 仍为 R，并追加新消息 Δ
结果: R + Δ 再次到线，重复裁剪
```

这种临时变换只可能在“裁剪后的这次模型调用正好是最后一次 LLM 调用”时为整轮省下一次
摘要；只要模型还继续调用工具，收益就不稳定，而且会反复处理同一批旧消息。

直接把裁剪结果写回 `messages` 又会破坏原文：后续摘要与 `/conversation_history` 只能
看到占位符，无法恢复原始工具结果。

## 二、核心方案

裁剪改成和摘要一样的**持久化事件 + 动态上下文投影**：

```text
checkpoint.messages                 原始消息，不覆盖
checkpoint._summarization_event     现有摘要事件
checkpoint._context_trim_event      新增裁剪事件
```

每次模型调用按固定顺序投影：

```text
原始 messages
  → 应用摘要事件：summary_message + messages[cutoff_index:]
  → 应用裁剪事件：替换指定工具结果 / 压缩指定工具入参
  → 得到本次实际发送给模型的消息
```

事件只记录稳定的 `tool_call_id`，不重复保存占位符正文。占位符根据原始消息在每次请求时
确定性生成。

## 三、事件结构

```python
class ContextTrimEvent(TypedDict):
    version: int
    trimmed_result_ids: list[str]
    compacted_arg_ids: list[str]
```

示例：

```json
{
  "version": 1,
  "trimmed_result_ids": ["call_read_1", "call_execute_2"],
  "compacted_arg_ids": ["call_write_3", "call_edit_4"]
}
```

约束：

- 数组排序后再写入，保证事件确定、便于比较。
- 只接收有 `tool_call_id` 的工具消息；没有稳定 ID 的消息保持原样。
- `messages` 永远保留原文，事件不复制文件正文、命令输出或 diff。
- 新裁剪只做并集追加；同一 ID 不重复记录。

## 四、一次到线后的执行顺序

1. 先应用已有的摘要事件和裁剪事件，计算当前有效上下文。
2. 未到压缩线：直接调用模型，不新增事件。
3. 到达压缩线：
   - 从“未应用新裁剪”的有效消息中寻找新的候选；
   - 工具结果只有超过最小字符阈值才进入 `trimmed_result_ids`；
   - 工具入参只有超过最小字符阈值才进入 `compacted_arg_ids`；
   - 当前步骤、`KEEP` 点名项和 `KEEP_TOOLS` 永不进入事件；
   - 合并已有事件，重新投影并计数。
4. 合并后已落到安全区：
   - 用投影后的消息调用模型；
   - 返回 `ExtendedModelResponse`，通过 `Command.update` 持久化
     `_context_trim_event`。
5. 没有新候选，或合并后仍高于安全区：
   - 不发送裁剪后的投影；
   - 使用裁剪前的原始消息进入摘要；
   - 摘要与 offload 必须看到原始工具结果，不能看到裁剪占位符。

因此不需要再维护“三次应急裁剪后强制摘要”的计数器。只要已经存在的裁剪事件不再产生
新的有效压缩收益，系统就自然转入摘要。

## 五、阈值与参数处理

### 工具结果

- `CONTEXT_PRETRIM_MIN_RESULT_CHARS`，默认 `2000`。
- 原始结果不超过阈值时不裁剪。
- 超过阈值才替换为带工具名、目标、原文字符数和 `tool_call_id` 的占位符。

### 工具入参

- `CONTEXT_PRETRIM_MIN_ARG_CHARS`，默认 `2000`。
- `CONTEXT_TRIM_CLEAR_INPUTS` 保留为兼容开关，但语义改为“允许历史入参选择性压缩”，
  默认关闭。
- 开启后不再把整个 `args` 清成 `{}`。
- 只压缩超过阈值的字符串字段，例如：
  - `write_file.content`
  - `edit_file.old_string`
  - `edit_file.new_string`
  - 超长 `execute.command`
- `file_path`、`path`、`offset`、`limit`、`timeout`、工具名和调用 ID 原样保留。

上游 LangChain/DeepAgents 自带的历史入参截断关闭。它的行为是保留前 20 字符后截断，与
本次“保留关键字段”的事件语义重复且更容易误导模型。

## 六、与摘要事件的协作

- 摘要判断使用“原始消息 + 已有摘要事件”，不再叠加本次候选裁剪。
- 裁剪事件对应的原始结果仍在 checkpoint；摘要在 offload 时能写入完整原文。
- 摘要成功后，`cutoff_index` 之前的消息已经由摘要事件隐藏，其中的裁剪 ID 从事件中清理，
  避免事件无限增长。
- `cutoff_index` 之后仍保留的消息，其裁剪 ID 继续生效。
- 如果 offload 失败，已有摘要语义不变：仍然告警，但本次不新增额外数据删除。

## 七、可恢复性与失败回退

- 原始消息始终保留在 checkpoint；裁剪事件只影响模型看到的投影。
- 模型需要旧内容时，可以依据占位符中的工具名、路径和调用 ID 重新读取文件或重跑命令。
- 只有用户删除 checkpoint 或后续摘要成功逐出消息时，完整历史才离开当前 state；摘要
  路径仍会把逐出原文写入 `/conversation_history/{thread_id}.md`。
- 投影只替换模型请求，不修改消息数量、消息顺序、角色、`tool_call_id` 和工具调用配对。
- 真实模型调用发生上下文溢出时，不持久化本次候选裁剪事件，回退摘要路径。

## 八、验证要求

- 事件编码/解码、并集合并、幂等投影。
- 小工具结果和小入参完全不裁剪。
- 大结果进入 `trimmed_result_ids`，大入参进入 `compacted_arg_ids`。
- 参数压缩保留路径、offset/limit/timeout，不出现 `args={}`。
- 当前步骤、`KEEP`、`KEEP_TOOLS` 不进入事件。
- 第二次调用直接复用 checkpoint 中的裁剪事件，不再重复计算同一批 ID。
- 摘要使用未裁剪消息；摘要后清理 cutoff 之前的裁剪 ID。
- 全量 pytest、ruff、`scripts/check_prompts.py` 通过。

## 九、遗留

- 阈值先按 2000 字符观察，真机统计工具结果与参数长度分布后再调整。
- checkpoint 中的原始 `messages` 物理体积不会因裁剪事件变小；如需长期控制 state 体积，
  仍应依靠摘要、offload 或后续 state 归档。
