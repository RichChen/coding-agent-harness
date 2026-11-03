# 文档索引

## 先读什么

| 你想知道 | 读这个 |
| --- | --- |
| 这个项目在做什么、结论是什么 | [A5 最终报告](A5-FINAL-REPORT.zh-CN.md)（跨 issue 记忆）、[A6-R 结果](sprint/A6-R-RESULTS.zh-CN.md)（同题重试） |
| 新路径的接口长什么样 | [PUBLIC-API](upgrade/PUBLIC-API.md) |
| 新路径实现了什么、没做什么 | [首批实现记录](upgrade/IMPLEMENTED-TP01-04.md) |
| 怎么在本地跑一遍（不需要 API key） | [README](../README.md) 的"新离线演示" |
| 已知局限 | [limitations.md](limitations.md)、[current-evidence.md](current-evidence.md) |
| 按什么顺序读代码 | [code-reading-guide.zh-CN.md](code-reading-guide.zh-CN.md) |

## 接口与实现（当前有效）

| 文档 | 内容 |
| --- | --- |
| [upgrade/PUBLIC-API.md](upgrade/PUBLIC-API.md) | PublicTask / EvaluationSpec、证据定位、MemoryRecord、有效性检查、注入收据 |
| [upgrade/IMPLEMENTED-TP01-04.md](upgrade/IMPLEMENTED-TP01-04.md) | 四个包的实现、限制、测试结果 |
| [upgrade/CONTRACT-CHANGE.md](upgrade/CONTRACT-CHANGE.md) | 两仓之间的字段和收据约定 |
| [upgrade/H4-ADAPTER.md](upgrade/H4-ADAPTER.md) | harness H4 插件适配 |
| [upgrade/A5-PLAN.md](upgrade/A5-PLAN.md) | A5 离线合同：B0–B3、公平候选、成本与审计 |
| [upgrade/A5-VERIFICATION.md](upgrade/A5-VERIFICATION.md) | A5 各批的验收命令和结果 |
| [upgrade/OFFLINE-CONTRACT-v1.md](upgrade/OFFLINE-CONTRACT-v1.md) | 离线演示的输入输出约定 |
| [upgrade/schemas/PublicTask.schema.json](upgrade/schemas/PublicTask.schema.json) | 从实际 Pydantic 类导出的 schema |

> `reports/` 和 `baseline/` 下的运行产物不入库。文档里提到这些路径时标为"本地生成，未入库"，用对应脚本可以重新生成。

## 测试：哪些能直接跑，哪些需要未公开材料

不需要 API key。干净克隆里实测：**357 通过、7 跳过、3 失败**。

跳过的 7 项依赖本机布局或数据：5 项要求 `../coding-harness` 同级检出，1 项要求 source pool，1 项要求符号链接权限。3 个失败读取只在原开发机器上存在的文件，不是代码回归，也没有被跳过：

| 测试 | 缺的文件 | 原因 |
| --- | --- | --- |
| `test_a5_funding.py::test_funded_build_bundle_is_prepared_only_and_private_material_stays_outside_public` | `reports/upgrade/a5-funded-build-preparation-20260921-v12/*` | `reports/` 是运行产物，不入库 |
| `test_a5_funding.py::test_funded_build_artifacts_are_hashed` | 同上 | 同上 |
| `test_memory_cards_v5.py::test_generic_builder_matches_v5_bytes` | django-10914 的 handoff 字段 | 依赖 harness 侧输出，需要两仓联调的数据 |

只想跑公开部分：

```bash
python -m pytest -q --deselect tests/test_a5_funding.py::test_funded_build_bundle_is_prepared_only_and_private_material_stays_outside_public \
  --deselect tests/test_a5_funding.py::test_funded_build_artifacts_are_hashed \
  --deselect tests/test_memory_cards_v5.py::test_generic_builder_matches_v5_bytes
# 357 passed, 7 skipped, 3 deselected
```

## 实验证据（`docs/sprint/`）

- A5-main：设计在 harness 仓的 `docs/sprint/A5-MAIN-DESIGN.zh-CN.md`（本仓不重复）→ [A5-MAIN-PREREG-ADDENDUM](sprint/A5-MAIN-PREREG-ADDENDUM.zh-CN.md) → [A5-MAIN-RESULTS](sprint/A5-MAIN-RESULTS.zh-CN.md) → [A5-MAIN-SYNTHETIC-REPORT](sprint/A5-MAIN-SYNTHETIC-REPORT.md)
- A6-R：[A6-R-PREREG](sprint/A6-R-PREREG.zh-CN.md) → [A6-R-RESULTS](sprint/A6-R-RESULTS.zh-CN.md) → [A6-R-ANALYSIS-OUTPUT](sprint/A6-R-ANALYSIS-OUTPUT.zh-CN.md) → [A6-R-STOP-DESCRIPTION](sprint/A6-R-STOP-DESCRIPTION.zh-CN.md)（非预注册）→ [A6-R-SYNTHETIC-REPORT](sprint/A6-R-SYNTHETIC-REPORT.md)
- 记忆卡协议：[MEMORY-PROTOCOL-V4](sprint/MEMORY-PROTOCOL-V4.md)、[V4-CARDS](sprint/V4-CARDS.md)、[MEMORY-INVENTORY](sprint/MEMORY-INVENTORY.md)、`AGENT-MEMORY-*.zh-CN.md`（v2 到 v7，逐版记录卡片规则）
- 逐轮记录：[CHECKPOINT-1.md](sprint/CHECKPOINT-1.md) 到 [CHECKPOINT-7.md](sprint/CHECKPOINT-7.md)、[CHECKPOINT-A6-1.md](sprint/CHECKPOINT-A6-1.md) 到 [CHECKPOINT-A6-4.md](sprint/CHECKPOINT-A6-4.md)

[sprint/PROGRESS.md](sprint/PROGRESS.md) 是按时间追加的执行流水，用来追查某一步的细节，不是入口文档。

## 归档（`docs/archive/`）

早期计划保留原文，用于追溯当时的范围，不代表当前设计：

- [PHASE7_1-POSITIVE-GAIN-HARDENING-PLAN.zh-CN.md](archive/PHASE7_1-POSITIVE-GAIN-HARDENING-PLAN.zh-CN.md)：Phase 7.1 计划。
- [IMPLEMENTATION-PLAN-TP01-04.md](archive/IMPLEMENTATION-PLAN-TP01-04.md) 和 [tasks/](archive/tasks/TP01-PUBLIC-BOUNDARY.md)：新路径首批实现的任务包。里面的"可直接转交的执行说明"是当时的派工文本，接口结论已收进 `PUBLIC-API` 和实现记录。

## 不在当前分支的文档

| 文件 | 为什么移出 |
| --- | --- |
| `docs/archive/SESSION-HANDOFF.zh-CN.md` | 2026-07-04 的 AI 会话交接，用于上下文过长时恢复对话，内容是当时的目标、进度和下一步；对应的实验已被 A5/A6-R 取代 |
| `docs/sprint/A5-FINAL-REPORT-OUTLINE.zh-CN.md` | 写给执行模型的报告写作大纲；成稿是 [A5-FINAL-REPORT](A5-FINAL-REPORT.zh-CN.md) |
| `docs/upgrade/A5-HARNESS-HANDOFF.md` | 两仓接线交接便条；边界和门槛已在 [A5-PLAN](upgrade/A5-PLAN.md)、[A5-PILOT20-STATUS](upgrade/A5-PILOT20-STATUS.md) 和 [CONTRACT-CHANGE](upgrade/CONTRACT-CHANGE.md) |

原文仍在 git 历史里，可用 `git log --diff-filter=D -- <path>` 找到删除它的提交，再 `git show <commit>^:<path>` 取出。
