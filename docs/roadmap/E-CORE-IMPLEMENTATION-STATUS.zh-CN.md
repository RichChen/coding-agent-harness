# E 线第一版工程化状态

日期：2026-11-15

## 本轮范围

本轮只做无付费的兼容层升级，不改变 FIX-12 至 FIX-15 的提示词、触发条件、执行后端或评分规则，也没有重跑历史实验。

## 已完成

- 在 `src/runtime/tool_intervention.py` 增加版本化的 `InterventionSpec` 和 `intervention_spec()`。
- 保留原有四种配置值：`none`、`repeat_reminder`、`next_action`、`result_explanation`。
- 保留原有 E/N 提示词原文和重复调用“三次相同单工具调用”规则。
- `AgentRunResult` 增加 `intervention_events`，与原有 `context_events` 分开，避免改变旧报告依赖的事件顺序。
- 记录干预模式、版本、触发条件、是否注入提示词和重复提醒的实际触发事件。
- 会话断点保存和恢复 `intervention_events`；旧会话缺少该字段时使用兼容默认值。
- EventBus 增加对应的配置和触发事件，但不改变原有事件名称。

## 验证

运行：

```text
pytest -q tests/test_fix13_intervention.py
```

结果：13 passed，16 skipped。已有 FIX-13/FIX-15 行为测试继续通过。

## 当前边界

三类任务已经接入同一个显式 adapter/receipt 入口，但没有被强行改成同一种执行器：

- `MiniAdapter` 继续调用现有 `EvalRunner`；
- `RepoPytestAdapter` 继续调用 `RepositoryGrader`，要求调用方显式提供 `base`、`candidate` 和 `RepositorySpec`；
- `SwebenchAdapter` 继续调用 `ImageGrader`，要求调用方显式提供 image、delta、overlay、F2P/P2P 等 grader 输入；
- `AdapterRegistry.execute_many_async()` 只按 manifest 顺序调度已注册 adapter，统一记录 `RunSpec`、`RunReceipt` 和 provenance，不扫描目录、不猜测任务、不读取隐藏 grader 结果。

干预回执现在还会记录策略名、策略版本、配置哈希、触发次数、提示注入次数、是否从 session 恢复和紧凑事件摘要。真实 adapter 的 Docker/pytest/grader 运行仍属于原有执行层，尚未启动新的付费实验。
