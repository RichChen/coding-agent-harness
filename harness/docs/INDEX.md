# 文档索引

这个仓库的文档分五类。想读代码之前，先看这里，不用按目录顺序翻。

## 1. 入口

| 文档 | 内容 |
| --- | --- |
| [../README.md](../README.md) / [../README.zh-CN.md](../README.zh-CN.md) | 安装、跑一次、跑评测、项目结构 |
| [PROJECT-HISTORY.zh-CN.md](PROJECT-HISTORY.zh-CN.md) | 按轮次记录做了什么、哪里做错了、怎么发现的 |
| [CONTAMINATION-NOTES.zh-CN.md](CONTAMINATION-NOTES.zh-CN.md) | 评测污染和答案隔离的处理 |
| [retrieval/](retrieval/) | 代码检索（CR）线：冻结记录、结果、数据与演示；从 [CR-1 结果](retrieval/CR-1-RESULTS.zh-CN.md) 和 [CR-2 结果](retrieval/CR-2-RESULTS.zh-CN.md) 读起 |
| [roadmap/](roadmap/) | 工作区快照、公开验证与完成控制：P2 到 P4 的结果和证据 |
| [P3 统一入口与检查点](roadmap/P3-INTEGRATION-RESULTS.zh-CN.md) | 当前工作区检索→公开验证→有界反馈→候选与证据交付；脚本回复结合真实 Docker 的机制演示，尚无自主修复收益结论 |
| [P4 公开目标检查与预检](roadmap/P4-01-02-RESULTS.zh-CN.md) | 真实基点复现、四个公开场景、终态候选检查入口及读取配额审查修正；不是自主修复结果 |
| [P4 首轮真实任务结果](roadmap/P4-FIRST-RUN-RESULTS.zh-CN.md) | 最终候选通过四项公开目标检查，运行触及 24 步上限；实际轨迹、费用估算、交付证据与后续讨论点 |
| [P4 延后遮蔽诊断](roadmap/P4-DELAYED-MASK-RESULTS.zh-CN.md) | 单次新增对照未观察到修改或自然收尾效率改善；保留结果与限制，不扩建上下文功能 |
| [P4 同一历史下的动作诊断](roadmap/P4-ACTION-DIAGNOSTICS.zh-CN.md) | 12 次固定历史动作采样与 6 次工具顺序校准：恢复正文改变读取位置，倒计时促使修改尚无稳定支持 |
| [P4 短续跑与行动指令敏感性](roadmap/P4-CONTINUATION-AND-ACTIONABILITY.zh-CN.md) | 24 次短续跑响应仍读取；另四次同历史采样中，行动指令触发两份通过公开检查的补丁，含额外改动风险与宿主回放边界 |

代码检索线和 P2 到 P4 的过程文档（实验合同、检查点、审计、阶段计划）不在公开仓库里；结果文档提到它们的地方只说明出处。

冻结标签指向公开快照提交。快照里的代码与原冻结提交逐字节相同，只去掉了这些过程文档。文档里写的是原开发仓库的提交号，对应关系如下；其他原始提交号在公开仓库里没有对应提交。

| 标签 | 文档中的提交号 | 公开快照 |
| --- | --- | --- |
| `harness/cr1-freeze` | `4faaa53` | `93f25de` |
| `harness/cr2-freeze` | `f911103` | `2daa3dc` |
| `harness/cr2-freeze-r1` | `cd1ba35` | `a776315` |
| `harness/cr2-freeze-r2` | `b902b93` | `302d82e` |
| `harness/cr2-freeze-r3` | `0d0ac45` | `e0c4e54` |

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

不需要 API key，LLM 全部是 mock。全新虚拟环境（`pip install -e ".[dev]"`）里实测：**1249 通过、44 跳过、4 失败**。

跳过的 44 项里，39 项需要真实 Linux 或 Docker（opt-in 的 Docker 执行测试、本机缓存的固定镜像、真实 Linux 进程组）；5 项读取不入库的本机数据，文件不存在时自动跳过：`runs/a5-main` 的运行数据 3 项、A5 私有记忆池 1 项、`runs/sprint5-main-public` 1 项（见下表最后一行）。4 个失败读取只在原开发机器上存在的文件，不是代码回归，也没有被跳过。下表列出这 4 项和自动跳过的 `test_public_target`：

| 测试 | 缺的文件 | 原因 |
| --- | --- | --- |
| `test_a5_real240_h0_snapshot.py::test_h0_export_and_audit_via_cli` | A5 memory pool 的 `validated-memory-records.jsonl` | 私有记忆构建产物，`.jsonl` 不入库 |
| `test_repo_grader.py::test_full_archive_and_private_test_resources_are_complete` | `full-repo-grader/attempt-02/flask-base.tar.gz` | 完整上游归档，体积过大 |
| `test_summary_pilot.py::test_prepare_frozen_data_and_window_expectations` | `H5/deepseek-flash-attempt-01/seed/boltons/strutils.py` | R2 种子文件，仅本地保存 |
| `test_summary_pilot.py::test_trace_accounting_replay_and_private_grading` | 同上 | 同上 |
| `test_public_target.py::test_task_is_complete_public_input_and_manifest_detects_material_change` | `runs/sprint5-main-public/sphinx-doc__sphinx-8721.json` | `runs/` 不入库；文件不存在时自动跳过，不计入失败 |

只想跑公开部分：

```bash
python -m pytest -q --deselect tests/test_a5_real240_h0_snapshot.py::test_h0_export_and_audit_via_cli \
  --deselect tests/test_repo_grader.py::test_full_archive_and_private_test_resources_are_complete \
  --deselect tests/test_summary_pilot.py::test_prepare_frozen_data_and_window_expectations \
  --deselect tests/test_summary_pilot.py::test_trace_accounting_replay_and_private_grading
# 1249 passed, 44 skipped, 4 deselected
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
