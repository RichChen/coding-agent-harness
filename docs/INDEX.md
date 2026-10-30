# 文档索引

这个仓库的文档分五类。想读代码之前，先看这里，不用按目录顺序翻。

## 1. 入口

| 文档 | 内容 |
| --- | --- |
| [../README.md](../README.md) / [../README.zh-CN.md](../README.zh-CN.md) | 安装、跑一次、跑评测、项目结构 |
| [PROJECT-OVERVIEW.zh-CN.md](PROJECT-OVERVIEW.zh-CN.md) | 项目要解决什么问题，和同类工作的差别 |
| [PROJECT-HISTORY.zh-CN.md](PROJECT-HISTORY.zh-CN.md) | 按轮次记录做了什么、哪里做错了、怎么发现的 |
| [LEARNING.zh-CN.md](LEARNING.zh-CN.md) | 按调用顺序读代码的路线和自测题 |
| [CONTAMINATION-NOTES.zh-CN.md](CONTAMINATION-NOTES.zh-CN.md) | 评测污染和答案隔离的处理 |
| [下一阶段纲领](roadmap/HARNESS-NEXT-CHARTER.zh-CN.md) / [阶段拆解](roadmap/HARNESS-NEXT-PLAN.zh-CN.md) | 工作区检索与修复验证的目标、阶段验收与并行分工；实际进度见执行状态 |
| [下一阶段执行状态](roadmap/EXECUTION-STATUS.zh-CN.md) | 实际进度、可见工作会话、P0 证据和 P1 接口的统一入口；功能是否完成以阶段验收为准 |
| [P3 统一入口与检查点](roadmap/P3-INTEGRATION-RESULTS.zh-CN.md) / [运行命令](roadmap/EXECUTION-STATUS.zh-CN.md#从这里上手) | 当前工作区检索→公开验证→有界反馈→候选与证据交付；脚本回复结合真实 Docker 的机制演示，尚无自主修复收益结论 |

## 2. 实现说明（当前有效）

| 文档 | 内容 |
| --- | --- |
| [P2 捕获基础](roadmap/P2-FOUNDATION-RESULTS.zh-CN.md) / [工作区检索](roadmap/P2-A-RESULTS.zh-CN.md) / [公开验证](roadmap/P2-B-RESULTS.zh-CN.md) | 新增模块的实际 API、独立审查修正、真实机制证据与当前限制；旧实验入口保持原样 |
| [upgrade/H3-IMPLEMENTATION.zh-CN.md](upgrade/H3-IMPLEMENTATION.zh-CN.md) | Docker 执行、干净镜像、宿主机判分 |
| [upgrade/H4-IMPLEMENTATION.zh-CN.md](upgrade/H4-IMPLEMENTATION.zh-CN.md) | 上下文窗口、模型视图、插件接口 |
| [upgrade/H5-DEEPSEEK.zh-CN.md](upgrade/H5-DEEPSEEK.zh-CN.md) | 真实 DeepSeek 接入和预算 |
| [upgrade/H5-INTEROP.zh-CN.md](upgrade/H5-INTEROP.zh-CN.md) | 跨仓库插件的开/关对照 |
| [upgrade/SESSION-USAGE.zh-CN.md](upgrade/SESSION-USAGE.zh-CN.md) | 会话日志：意图先落盘、恢复时复用已提交结果、结果未知就停下 |
| [upgrade/R1-CLOSEOUT.zh-CN.md](upgrade/R1-CLOSEOUT.zh-CN.md) | R1 收口，含四个崩溃窗口的恢复验证 |
| [upgrade/R2-LITE-TRUE-SUMMARY.zh-CN.md](upgrade/R2-LITE-TRUE-SUMMARY.zh-CN.md) | 真摘要与共享预算 |
| [upgrade/R2-EXPERIMENT-CONTRACT.zh-CN.md](upgrade/R2-EXPERIMENT-CONTRACT.zh-CN.md) / [R2-WRAPPER-CONTRACT.zh-CN.md](upgrade/R2-WRAPPER-CONTRACT.zh-CN.md) | R2 实验和包装对照的设置 |

### 历史方案（已被替代，不是当前设计）

| 文档 | 状态 |
| --- | --- |
| [upgrade/A5-REAL240-HARNESS-CONTRACT.zh-CN.md](upgrade/A5-REAL240-HARNESS-CONTRACT.zh-CN.md) | 原 20 题 × 4 变体 × 3 次的 240 矩阵执行合同，2026-09-22 冻结。**已被 [A5-MAIN-DESIGN](sprint/A5-MAIN-DESIGN.zh-CN.md) 替代**：B0 对 B2 的核心对比只用到 120 次，最终改成 56 题 × 2 条件 × 3 次。保留它是为了解释当时的预算和接线，不代表当前运行方式。 |
| [upgrade/A5-HARNESS-GATE.zh-CN.md](upgrade/A5-HARNESS-GATE.zh-CN.md) | 同一时期的 20 目标 / 20 来源计划清单，`runnable_frozen=false`。后续的池、来源和门槛以 A5-MAIN-DESIGN 与 A5-MAIN-FREEZE 为准。 |

## 3. A5 / A6-R 实验证据（`docs/sprint/`）

按时间顺序读：

1. [A5-MAIN-DESIGN.zh-CN.md](sprint/A5-MAIN-DESIGN.zh-CN.md)：题池、功效、B0/B2 的定义。
2. [A5-MAIN-FREEZE.md](sprint/A5-MAIN-FREEZE.md)：冻结的输入和 hash。
3. [CHECKPOINT-1.md](sprint/CHECKPOINT-1.md) 到 [CHECKPOINT-7.md](sprint/CHECKPOINT-7.md)：逐轮记录。第 6 份最长，包含 B0/B2 的统计和排除规则。
4. [A5-MAIN-INVENTORY.md](sprint/A5-MAIN-INVENTORY.md)、[A5-CASE-DATA.md](sprint/A5-CASE-DATA.md)：运行清单和案例数据。
5. [A6-R-PLAN.zh-CN.md](sprint/A6-R-PLAN.zh-CN.md) → [A6-R-FREEZE.md](sprint/A6-R-FREEZE.md) → [CHECKPOINT-A6-1.md](sprint/CHECKPOINT-A6-1.md) 到 [CHECKPOINT-A6-3.md](sprint/CHECKPOINT-A6-3.md)：第二次尝试实验。
6. [AUDIT-A6-*.zh-CN.md](sprint/AUDIT-A6-1.zh-CN.md)：审计方对每轮的核查记录。

历史的阶段计划和启动指令（`HARNESS-SPRINT-*.zh-CN.md`、`HARNESS-CAPABILITY-SPRINT.zh-CN.md`、`R4-START-PROMPTS.zh-CN.md`、`A6-R-START-PROMPTS.zh-CN.md`）保留在这里，因为 `CHECKPOINT-A6-2/3` 把其中两份记为原样入库的预注册材料。它们是当时的执行指令，不是使用说明。

[PROGRESS.md](sprint/PROGRESS.md) 是按时间追加的执行流水（约 47 KB），用来追查某一步的细节，不是入口文档。

## 4. 测试：哪些能直接跑，哪些需要未公开材料

不需要 API key，LLM 全部是 mock。干净克隆里实测：**654 通过、25 跳过、4 失败**。

跳过的 25 项里，22 项是需要真实 Linux / Docker 的 opt-in 测试，3 项需要本机 `runs/a5-main` 的运行数据。4 个失败读取只在原开发机器上存在的文件，不是代码回归，也没有被跳过：

| 测试 | 缺的文件 | 原因 |
| --- | --- | --- |
| `test_a5_real240_h0_snapshot.py::test_h0_export_and_audit_via_cli` | A5 memory pool 的 `validated-memory-records.jsonl` | 私有记忆构建产物，`.jsonl` 不入库 |
| `test_repo_grader.py::test_full_archive_and_private_test_resources_are_complete` | `full-repo-grader/attempt-02/flask-base.tar.gz` | 完整上游归档，体积过大 |
| `test_summary_pilot.py::test_prepare_frozen_data_and_window_expectations` | `H5/deepseek-flash-attempt-01/seed/boltons/strutils.py` | R2 种子文件，仅本地保存 |
| `test_summary_pilot.py::test_trace_accounting_replay_and_private_grading` | 同上 | 同上 |

只想跑公开部分：

```bash
python -m pytest -q --deselect tests/test_a5_real240_h0_snapshot.py::test_h0_export_and_audit_via_cli \
  --deselect tests/test_repo_grader.py::test_full_archive_and_private_test_resources_are_complete \
  --deselect tests/test_summary_pilot.py::test_prepare_frozen_data_and_window_expectations \
  --deselect tests/test_summary_pilot.py::test_trace_accounting_replay_and_private_grading
# 654 passed, 25 skipped, 4 deselected
```

## 5. 运行证据（`upgrade/evidence/`）

每个子目录是一次执行留下的收据，但**不都是真实模型运行**，也不都是容器运行。**真实 Docker 和真实模型是两个独立维度**，下面按这两条维度分：

| 类型 | 目录 | 说明 |
| --- | --- | --- |
| 工程验证：mock 模型 + 真实 Docker | [H3](upgrade/evidence/H3/README.md)、[H4](upgrade/evidence/H4/README.md) | 验证容器生命周期、文件/命令工具、宿主 grader、窗口策略和恢复。H3 用模拟模型跑完 4 轮并在容器里改文件；H4 的摘要来自本地 fixture。两处 README 都写明**没有真实或付费模型调用**。 |
| 工程验证：离线 preflight，无容器 | [H5-interop](upgrade/evidence/H5-interop/README.md) 的 `offline-01`、`offline-02` | 外部 adapter 与 dispatcher 的离线 preflight，0 次付费调用。 |
| 失败预检 | `H5-interop/real-02` | 想启用 dispatcher，但沙箱读不到 Docker named pipe，baseline 记为 `invalid_env`，**不能作为联调通过证据**。 |
| 真实模型修复：DeepSeek Flash + Docker | [H5](upgrade/evidence/H5/README.md) 的 `deepseek-flash-attempt-01/03`；`H5-interop/real-01`、`real-03`、`real-04` | 真实 SSE、工具调用和容器内修复，判分在宿主侧。`H5/independent-regrade` 是对冻结快照的 18 个容器复验，**0 模型调用**，与上面的付费运行区分开。`real-01` 是 dispatcher 启用前的历史证据，当前以 `real-04` 为准。 |
| 真实模型摘要与比对 | [R2-real](upgrade/evidence/R2-real/README.md)（`smoke-01`、`pilot-01`） | 1 次真实摘要 smoke、36 个固定历史对照，另有 8 次 Docker 修复对照；失败分母全部保留。 |
| 真实模型包装实验 | [R2-real/wrapper-01](upgrade/evidence/R2-real/wrapper-01/README.md) | 9 份固定摘要 × 3 种包装 × 3 次重复，共 102 个真实主请求、0 次新摘要。它验证的是**包装措辞**，不是 Docker 修复成功率，报告里也明确不宣称修复提升。 |
| 离线 / fixture（0 模型调用） | [R2-lite](upgrade/evidence/R2-lite/README.md)、[HP-01](upgrade/evidence/HP-01/REPORT.md) 到 [HP-04](upgrade/evidence/HP-04/REPORT.md)、`A5-real240-79-fixture-wiring` | 只验证实现链路，报告里写明模型调用数为 0。 |
| 预检与 gate（只读检查） | `A5-harness-preflight-01` 到 `-72`、`A5-pilot-grader-readiness-*`、`A5-real240-77/78`、`A5-h3-dual-end-review-*` | 检查镜像、环境、来源、基线是否满足条件；多数不调用模型。 |
| grader 校准 | [full-repo-grader](upgrade/evidence/full-repo-grader/README.md) | 整仓 grader 的构建与校准，0 模型调用。 |
| 记忆建池 | `A5-memory-pool-73` 到 `-76` | 候选索引、smoke 和 S2 建池记录。 |

其中 `A5-real240-*` 属于已被替代的 240 方案，见上文"历史方案"。

### 哪些产物公开，哪些只在本地

`.gitignore` 对 `evidence/` 的规则是：**只跟踪 `.md` 和 `.json`**。所以仓库里有报告、manifest、收据和小的结果 JSON；以下内容不入库，只留在跑过实验的机器上：

- `session/` 下的会话日志（SQLite）和逐条轨迹；
- `workspace/`、`staging/`、`original/`、`known-good/`、`baseline/`、`build/`、`full-repo/` 等仓库副本；
- `private*` 目录、`.tar.gz` 整包归档，以及少数被逐个排除的超大 `source-inventory.json`。

因此文档里有些链接指向"本地生成，未入库"的路径，是正常的；对应的脚本可以重新生成。GitHub 上能读到的是这些运行留下的报告和收据，不是可重放的完整工作区。
