# E 线动态 InterventionPolicy 设计

这次工程改动把 E 线从“固定系统提示”扩展为可恢复的动态策略接口，同时保留 FIX-13/FIX-15 的旧模式兼容。策略只能看到当前工具轮的调用、结果、步号和已有干预事件，不能读取 gold、标准补丁、grader 结果或隐藏任务文件。

## 接口

`src/runtime/tool_intervention.py` 提供：

- `InterventionContext`：一次工具轮的只读输入；
- `InterventionDecision`：是否注入可见提示、触发名称和结构化元数据；
- `InterventionPolicy`：`initial_prompt()`、`observe()`、`snapshot()`、`restore()` 协议；
- `DynamicInterventionPolicy`：把纯函数或显式状态回调接入主循环；
- `ConfiguredInterventionPolicy`：封装已有 `none`、`repeat_reminder`、`next_action` 和 `result_explanation` 行为。

每个策略还带有固定的 `policy_version` 和 `config_hash`。`InterventionContext`
只暴露当前工具轮的调用、结果、步号和既有干预事件，并提供成功、失败、截断和
工具数量等派生事实；`InterventionDecision` 固定记录触发名、是否注入提示和结构化
元数据。策略接口没有 gold、标准补丁、grader 或隐藏任务文件的参数。

`AgentLoop` 接受可选的 `intervention_policy`。没有传入时仍按原来的 `tool_intervention` 字符串构造兼容策略。每次策略决策会进入 `AgentRunResult.intervention_events`；状态通过 session loop 保存和恢复，旧的 `repeat_call` 字段仍可读取。

## 无模型验证

输入：`tests/test_fix13_intervention.py`、现有 `MockLLMClient` 和本地 fixture 工具。

允许修改范围：`src/runtime/tool_intervention.py`、`src/runtime/loop.py`、对应无模型测试和本说明；不修改 `runs/fix13`、FIX-13 结果报告或付费实验输入。

验证命令：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_fix13_intervention.py -q --tb=short --disable-warnings
.venv/Scripts/python.exe -m pytest tests/test_unified_adapters.py tests/test_unified_eval_contract.py tests/test_unified_eval_report.py -q --tb=short --disable-warnings
.venv/Scripts/python.exe -m compileall -q src/runtime/tool_intervention.py src/runtime/loop.py
```

这些检查只验证策略接线、结构化事件、状态恢复和旧行为兼容；它们不证明策略能提高解出率，也不替代新的正式评估。任何后续付费比较都必须重新确定题集、预算、停止规则和证据边界。

无模型展示 fixture 位于 `src/runtime/intervention_fixtures.py`。它在成功读取/搜索后
连续若干轮没有编辑时发出候选提醒；失败工具结果不会被写成“已找到目标”，多工具轮也
不会被误判成单一重复调用。这个 fixture 的用途是验证边界和审计证据，不是证明收益。
