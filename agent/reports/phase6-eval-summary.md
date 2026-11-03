# Phase 6 Evaluation Strengthening — Summary Report

Generated: 2026-07-02  
Plan: `PHASE6-EVAL-STRENGTHENING-PLAN.zh-CN.md`

## 1. 补强目标

将 Phase 6 从「评测管线跑通」升级为可支撑 **Agent 机制收益判断** 的 ablation 框架：

- 区分 **plumbing validation** 与 **mechanism evidence**
- 关闭 hard 任务上的 prefix scripted fix，避免 baseline 饱和
- 让 skill/memory 影响 deterministic patch strategy（非仅 telemetry）
- 按 task category 分组报告，避免整体 success rate 掩盖局部收益

## 2. 实现变更（已完成）

| 项 | 变更 |
| --- | --- |
| 报告编码 | `report.py` 使用 ASCII `+/-` 替代乱码 `卤` |
| 默认 repeats | `scripts/run_phase6_ablation.py` 默认 `--repeats 3` |
| Hard 任务集 | `benchmarks/tasks/phase6_hard_12.yaml` + 4 类 tiny repos |
| Adapter 开关 | `FakeAdapter.allow_prefix_scripted_fixes`；hard eval 自动关闭 |
| A1 vs A2 | `execution_mode`: A1=`single_graph`（planner_agent），A2=`role_agents` |
| Skill/Memory | `DeveloperAgent` 按 active_skills / memory_hints 选择 hard patch strategy |
| 分组报告 | Markdown 新增 `## By Task Category` |
| 文档 | `docs/evaluation-contract.md` 区分三层评测用途 |

## 3. 评测矩阵与 Run ID

| 评测层 | 命令 | Run ID | 用途 |
| --- | --- | --- | --- |
| Plumbing | `phase6_tiny_24.yaml --repeats 3 --seed 42` | `20260702_101348` | 管线稳定性 |
| Hard mechanism | `phase6_hard_12.yaml --repeats 3 --seed 42` | `20260702_102422` | 机制差异证据 |
| Seeded memory | `phase6_hard_12.yaml --repeats 3 --seed-memory` | `20260702_102744` | memory 跨 repeat 收益 |

详细报告路径：`reports/phase6/<run_id>/ablation-summary.md`

## 4. 核心结果

### 4.1 Plumbing（tiny_24）— 不用于机制结论

| Config | Success | Avg Steps | Role Handoff | Roles |
| --- | ---: | ---: | ---: | --- |
| A0_baseline | 1.00 | 2.00 | 0.00 | single_agent |
| A1_planner | 1.00 | 5.00 | 0.00 | planner_agent |
| A2_multi_agent | 1.00 | 5.00 | 4.00 | developer, locator, manager, qa |
| A3–A5 | 1.00 | 5.00 | 4.00 | role agents |

**解读**：tiny_24 仍由 scripted fix 驱动，全配置 100% 通过。价值在于验证 runner / trace / report / memory 隔离；**不能**据此声称 skill/memory 带来成功率提升。

### 4.2 Hard mechanism（hard_12，无 seed-memory）

| Config | Success | Avg Steps | Avg Replan | Main Failure |
| --- | ---: | ---: | ---: | --- |
| A0_baseline | 0.00 | 1.00 | 0.00 | test_failure |
| A1_planner | 0.00 | 6.00 | 1.00 | test_failure |
| A2_multi_agent | 0.00 | 6.00 | 1.00 | test_failure |
| A3_skill | **0.92** | 5.08 | 0.08 | success / test_failure |
| A4_memory | **0.67** | 5.33 | 0.33 | mixed |
| A5_full | **0.97** | 5.03 | 0.03 | success |

**按类别（A3_skill）**：

| Category | Success | 说明 |
| --- | ---: | --- |
| regression | 1.00 | skill 覆盖完整 |
| api | 1.00 | skill 覆盖完整 |
| traceback | 1.00 | skill 覆盖完整 |
| rename | 0.67 | 部分 case 仍失败，未伪造收益 |

**解读**：

- Baseline 在 hard 模式 **0%**，满足「不再 100% 饱和」验收。
- **A3_skill** 相对 A0/A1/A2 有显著成功率提升（0% → 92%），证明 skill 驱动的 deterministic patch 有效。
- **A1/A2 在 hard 上仍为 0%**：planner / multi-agent 本身不提供 patch strategy，需 skill 或 memory 触发 hard fix——这是诚实负结果。
- A2 vs A1：成功率相同，但 A2 `role_handoff=4`、trace 中 agent_role 为 developer/locator/manager/qa，与 A1 的 planner_agent 可区分。

### 4.3 Seeded memory（hard_12 + `--seed-memory`）

| Config | Success | Memory Useful | Memory Stale |
| --- | ---: | ---: | ---: |
| A0–A2 | 0.00 | 0 | 0 |
| A3_skill | 0.92 | 0 | 0 |
| A4_memory | **0.89** | 32 | 1 |
| A5_full | **1.00** | 35 | 0 |

**解读**：

- 共享 memory 后 **A4_memory** 从 0.67 → 0.89（相对 non-seeded hard run）。
- **A5_full** 达到 **100%**，说明 seeded procedural/episodic hints 可补齐 rename 等边缘 case。
- memory_stale 计数低，未观察到大规模 negative transfer。

## 5. 机制收益矩阵（诚实表述）

| 机制 | Hard 任务上可观测收益 | 局限 |
| --- | --- | --- |
| Planner (A1) | trace/步骤结构变化；hard 上无 patch 收益 | 无 skill 时无法修复 hard repo |
| Multi-agent (A2) | role handoff、多角色 trace | 成功率与 A1 相同 |
| Skill (A3) | **+92% success** vs A0 | rename 类未全覆盖 |
| Memory (A4) | **+67% success**；seeded 后 **+89%** | 依赖 seeded hints 质量 |
| Full (A5) | seeded 下 **100%** | 仍 deterministic，非 LLM |

## 6. 验收状态

```text
pytest:  27 passed
ruff:    All checks passed
plumbing: success_rate=1.00（A0–A5），tiny_24 未破坏
hard:     A0=0.00，A3/A4/A5 有可解释增量
报告:     无 `卤`，使用 `+/-`；含 By Task Category
```

## 7. 后续建议（Phase 7）

- README / design.md 引用本报告与 `docs/evaluation-contract.md`
- 可选：让 A2 multi-agent 在 locate 阶段利用 memory 减少步骤（当前 mainly success 维度有收益）
- 扩展 hard 任务数量前，先固定当前 12 任务 + repeats=3 作为 regression suite
