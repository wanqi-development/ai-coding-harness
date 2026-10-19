---
name: code-tracing-analysis
description: >
  流程 / 行为类只读代码调查的工作法：X 是怎么跑通的、谁调了这个函数、改它会影响什么、
  某逻辑在哪实现、数据从输入到存储怎么流。双向追踪闭合到可观察结果，每条结论带定位与
  证据状态；不做 bug 根因诊断、不出方案、不审代码、不改代码。
  Triggers on: 怎么跑通, 调用链, 追踪, 数据流, 影响分析, 谁调用, 在哪实现, 怎么工作,
  trace, call chain, data flow, impact analysis, how does it work.
---

# 代码追踪分析（code-tracing-analysis）

## 运行环境与红线
- 项目目录 `/ai_coding/project` **只读**；命令走 `execute`，只读白名单与 `repo-bootstrap-analysis` 相同。
- 中间产物（证据台账、检查点）放 `/ai_coding/tmp/`；结论进回复，不落文件。

## 用 / 不用
- 用：流程 / 行为（X 端到端怎么跑）、影响分析（改 Y 会坏什么）、实现定位、入口清单、数据流。
- 不用：bug 根因诊断、方案设计、代码审查、性能优化 —— 那些归别的形态与 skill。

## 工作法：SCOPE → ROUTE → EFFECT → BREAK → SHIP
1. **SCOPE 锁身份**：用户点名的对象先建身份卡 `repo@分支 | path | symbol / 配置键 | 已读范围`；
   不可读或不存在就如实报 PARTIAL / BLOCKED，**不许拿相邻同名实现顶替作答**。
2. **ROUTE 双向追踪**：正向从入口沿**实际调用表达式**走，反向从结果 / 状态写入 / 副作用找调用者，
   在两侧相遇处核对参数、条件与错误；关键跳（分派、状态变化、副作用、错误分流、跨边界）记链账本，
   两端着地：起点是 confirmed 的入口，终点是用户关心的结果 —— **不许停在中间 helper**。
3. **EFFECT 闭合卡**：写「自动 / 保证 / 重试 / 补偿 / 最终一致 / 不影响」前填责任闭合卡
   （references/closure-card.md），任一格缺证据整条主张降级。
4. **BREAK 反证**：每条强 / 全称主张主动找最小反例（提前返回、吞错、开关、分派、批次部分完成、
   相邻但未选中的实现）；相关分支没读完之前不写「所有 / 始终 / 只有 / 自动」。
5. **SHIP 交付**：首屏顺序＝范围与证据状态 → 直接答案（带状态标签）→ 最短主链 → 未知与验证动作；
   交付前过一遍 references/final-audit.md 的否定式收口门。

## 证据状态与激活轴（速记，全表见 references/claim-ledger.md）
- 状态只四档：`confirmed / inferred / unknown / conflicted`；`inferred` 写前提与证伪动作，
  `unknown` 写缺失与影响，`conflicted` 写冲突范围与判别动作。
- 激活另成一轴：defined → assembled → selected → runtime_activated；静态源码最多证明
  「当前源码选中」，没有可复核的运行观测不写「线上在跑 / 已生效」。

## 检查点
- 取证超过一屏或察觉剪裁时，把台账写进 `/ai_coding/tmp/evidence-ledger.md`：已闭合边、未闭合边与
  竞争解释、已搜 / 未搜范围、下一条查询；被裁之后**先读它**，不重读源码。
- 平台真断（步数上限、权限）就交付 PARTIAL / BLOCKED：列未闭合主张、影响与解阻动作；
  检查点不是完成，不许冒充闭合。

## references
| 文件 | 什么时候读 |
|---|---|
| references/claim-ledger.md | 下结论前：证据权限表、四状态、激活轴、负向搜索与全称 |
| references/closure-card.md | 准备写跨组件效果主张时 |
| references/final-audit.md | 交付前必过一遍 |
