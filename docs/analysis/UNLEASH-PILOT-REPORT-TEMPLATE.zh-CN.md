# UNLEASH pilot 结果报告模板

这是一份空报告骨架。它先固定数据口径和解释边界，避免 pilot 跑完后根据结果临时改指标。所有带 `待填` 的位置都必须从运行账本、请求 telemetry 和独立 grader 生成；不要手工凭印象补数字。

## 0. 报告状态

- 数据冻结时间：`待填`
- 代码提交 / 工作树状态：`待填`
- 配置哈希：A `待填`；U `待填`；U+T `待填`
- 模型与返回身份：`待填`
- 价格快照与标准化价格：`待填`
- 报告状态：`draft / valid-for-pilot-description / invalid-for-expansion-inference`

## 1. 运行账本

按 arm、task、run 分层报告：

| Arm | 计划槽位 | 启动 | 最终记录 | 有效 | 无效 | 待处理 | 退役/基础设施重跑 |
|---|---:|---:|---:|---:|---:|---:|---:|
| A | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
| U | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
| U+T | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |

额外记录：HTTP 400/402、超时、模型身份不一致、admission 拒绝、compaction、调用上限、单次预算上限、provider usage 缺失、grader 失败、运行中断和重跑原因。基础设施失败不应被并入模型任务失败。

## 2. 有效性门槛（G1–G5）

| 门槛 | 检查 | 结果 | 影响 |
|---|---|---|---|
| G1 | 每个 task × arm 有足够的有效重复，目标为至少 27/30 题达到六次有效运行 | 待填 | 任务级配对分析是否可信 |
| G2 | U/U+T 的 compaction/admission 触发不超过设计容忍度 | 待填 | 是否真的测试到放宽窗口 |
| G3 | A/U 无 reasoning；U+T 有请求级 thinking 元数据且每次有效 run 至少出现一次非零 reasoning | 待填 | U+T null 结果是否可解释 |
| G4 | 每 arm 的单次成本上限触发不超过设计容忍度 | 待填 | 成本比较是否被 hard cap 主导 |
| G5 | 每个最终请求都有 settled usage、请求元数据、模型身份和完整 telemetry；失败请求也要检查 | 待填 | 账单与行为指标是否完整 |

若任一 G1–G5 失败，报告必须标记为 **pilot invalid for expansion inference**。仍可报告工程诊断和描述性观察，但不能据此批准更大规模扩展，也不能把 U+T 的低暴露结果解释为 thinking 无效。

## 3. 任务接纳与数据来源

- 数据集版本、任务清单哈希：`待填`
- 任务来源与发布日期分布：`待填`
- 被排除任务及理由：`待填`
- 替补任务及是否破坏 block/randomization：`待填`
- SWE-rebench 状态：更新鲜，但没有被证明无污染；公开 RL 训练来源和模型发布日期关系必须单独记录。

## 4. 主要结果

主结果使用 task-level paired comparison，不把重复运行直接当成独立任务。至少报告：

| 对比 | solve contrast | 置信区间 | 有效 task 数 | 解释 |
|---|---:|---|---:|---|
| U − A | 待填 | 待填 | 待填 | bundled relaxation 的整体差异 |
| U+T − U | 待填 | 待填 | 待填 | 在暴露足够时才可解释为 thinking 条件差异 |
| U+T − A | 待填 | 待填 | 待填 | 描述性总对比 |

应按总体、difficulty、接口类型、任务来源月份做描述性分层。pilot 不做多重假设检验，也不把小样本区间跨 0 写成“证明没有效果”。

## 5. 成本与暴露

成本至少拆成 prompt、cache hit、cache miss、output、reasoning；区分 settled、unknown、retired、in-flight。报告 run/task 的 median、p90、max，并同时给出：

- 标准化 peak-price cost；
- 实际 provider usage cost；
- 运行 overhead（同时记录 model calls 和 synthetic steps）；
- 当前“cost per solved task”如果分母是 solved runs，必须明确写成 **cost per solved run**；需要时另算去重后的 task-level cost。

暴露审计至少包括：U+T 非零 reasoning 请求比例、reasoning token 分布、零暴露 run、首次编辑前调用数、reread share、compaction/admission 触发、prompt/cache 命中情况。

## 6. 失败分类

建议至少分为：

1. provider / HTTP / 认证 / 限流；
2. harness / 预算 / 超时 / admission / compaction / 记录故障；
3. grader / image / answer-isolation / telemetry 无效；
4. agent 轨迹（重复读取、未编辑、工具未使用、编辑后未验证）；
5. 任务结果（测试失败、部分修复、错误文件、回归）；
6. 未知，需人工复核。

`found_idle`、最终文本或某个工具调用本身只能作为描述性字段，不能单独当作成功或失败。

## 7. 成本敏感性与账本复核

独立重算：已花费、已结算、在途预留、退役尝试、计划剩余槽位、重跑比例。不要直接把全部 rows 乘以 `6 × 1.14` 当作最终 forecast；检查是否重复计入退役或已完成的额外尝试。另做：

- 不同 rerun 率；
- U/U+T 长运行偏斜；
- 4 元 hard cap 是否绑定；
- 250 元 batch cap 与约 263 元设计上界之间的差异；
- off-peak 实际账单与 standardized peak-price 账本。

## 8. 解释与决策

先写数据事实，再写统计描述，再写机制假设。决策模板：


> **有效性**：G1–G5 为 `待填`。因此本报告 `可/不可` 用于扩展实验推断。
>
> **A→U**：在 bundled intervention 口径下，solve/cost 的方向为 `待填`；不能归因于单一组件。
>
> **U→U+T**：reasoning exposure 为 `待填`。若暴露不足，结论只能是“本 pilot 没有充分测试到 thinking 条件”。
>
> **下一步**：`停止 / 修正 telemetry 或配置后重复 pilot / 进入更大实验 / 只做工程诊断`。

## 9. 必须避免的表述

- “证明 harness 普遍提升了 agent”；
- “SWE-rebench 无污染”；
- “U 的效果来自 masking、admission、调用上限或预算中的单一因素”；
- “thinking、retrieval 或 memory 没有价值”；
- “没有显著差异，所以没有效果”；
- “成本是 X 元”而没有说明是 provider 账单、标准化峰值还是含重跑的 forecast。

## 10. 图表清单

- task-level paired delta 点图或 forest plot；
- run/task 成本分布（hit/miss/output/reasoning 分解）；
- 基础设施、轨迹、任务结果的堆叠失败条形图；
- U+T reasoning exposure 与成本/结果的散点图；
- 已花费、在途预留、剩余槽位和 forecast 的累计图；
- 首次编辑前调用数与 reread share 的分布图。
