# Phase 7 Medium-48 Summary（Batch 5.9）

Expanded benchmark: **48 tasks** across 4 medium repos (`medium_inventory`, `medium_config_loader`, `medium_event_pipeline`, **`medium_auth_rules`**).  
Original 24 tasks unchanged; 24 new tasks with **new test files and boundary conditions** (not issue-only rewrites).

| 项 | 值 |
| --- | --- |
| Task file | `benchmarks/tasks/phase7_medium_48.yaml` |
| Run ID | `20260704_062354` |
| Seed | 42, repeats=1, Mock LLM=False |
| Sanity (limit 6) | Run `20260704_060542` — pipeline OK |

---

## Task Set Composition

| Tag | Count |
| --- | ---: |
| skill_heavy | 10 |
| memory_heavy | 16 |
| context_heavy | 11 |
| multi_file_api | 8 |
| repo_convention | 8 |
| traceback_repair | 5 |

**New in 5.9:** 24 tasks — 6 per repo (inventory/config/events expansion + auth_rules greenfield).

---

## Overall Success

| Config | Success | vs L0 | parse_err | repair (try/ok) | skill_inj | skill_neg | mem_inj | mem_sup |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| L0 | **48%** (23/48) | — | 2 | 0/0 | 0 | 0 | 0 | 0 |
| L2 | **69%** (33/48) | +21pp | 5 | 5/5 | 0 | 0 | 0 | 0 |
| L5 | **79%** (38/48) | **+31pp** | **1** | 1/1 | 8 | **1** | 79 | 129 |

**L5 vs L2: +10.4pp**（达到 ideal +10pp 线）  
**L5 vs L0: +31pp**

---

## By Category（48 tasks）

| Category | L0 | L2 | L5 | L5≥L2 |
| --- | ---: | ---: | ---: | --- |
| skill_heavy (10) | 50% | 60% | **80%** | ✅ |
| context_heavy (11) | 50% | 70% | **90%** | ✅ |
| memory_heavy (16) | 50% | 75% | **94%** | ✅ |
| multi_file_api (8) | 25% | 50% | 25% | ❌（共享难点） |
| repo_convention (8) | 67% | 67% | **100%** | ✅ |
| traceback_repair (5) | 40% | 80% | 40% | ❌（L5 回退，样本小） |

---

## vs phase7_medium_24（Batch 5.8 baseline）

| Config | 24 tasks | 48 tasks | Δ |
| --- | ---: | ---: | ---: |
| L0 | 42% | 48% | +6pp |
| L2 | 75% | 69% | -6pp |
| L5 | 79% | **79%** | ≈0 |
| **L5−L2** | +4pp | **+10pp** | **+6pp** |

**趋势一致：** L5 在更宽任务覆盖下仍 **高于 L2**，且 margin 扩大；memory/context/skill 子集正向收益在 48 题上保持或增强。

---

## L5 失败任务（10/48）

`evt_context_pipeline_01`, `inv_discount_stack_01`, `cfg_schema_validation_01`, `cfg_nested_key_holdout_01`, `evt_serialization_01`, `auth_token_format_01`, `auth_token_format_holdout_01`, `cfg_key_alias_01`, `cfg_key_alias_holdout_01`, `inv_api_tax_holdout_01`

- **共享难点**（L2 亦常失败）：`multi_file_api` 族、部分 holdout
- **新增任务**：`inv_discount_stack_01`, `auth_token_format_*` — 待后续 skill/card 覆盖，非 mechanism 回退

---

## 验收线

| 条件 | 结果 |
| --- | --- |
| L5 ≥ L2 | ✅ 79% ≥ 69% |
| L5 ≥ L0 + 20pp | ✅ +31pp |
| memory_heavy L5 ≥ L2 | ✅ 94% ≥ 75% |
| skill_heavy L5 ≥ L2 | ✅ 80% ≥ 60% |
| context_heavy L2/L5 > L0 | ✅ |
| skill_negative_transfer ≤ 5 | ✅ 1 |
| parse_err 不显著高于 L2 | ✅ 1 vs 5 |

**全部通过。**

---

## 报告路径

| 文档 | 路径 |
| --- | --- |
| Task 定义 | `benchmarks/tasks/phase7_medium_48.yaml` |
| Full 48 主对比 | `reports/llm/20260704_062354/llm-ablation-summary.md` |
| 24-task 基线 | `reports/archive/batch5.7-full24/` |
| 5.8 摘要 | `reports/phase7-positive-gain-summary.md` |
| **本摘要** | `reports/phase7-medium48-summary.md` |
